  `include "off_switch_count.svh"

  wire [SWITCH_COUNT-1:0] grant;
  `ifdef OFF_SWITCH_DIRECT
  bit [SWITCH_COUNT-1:0] direct_grant;
  assign grant = direct_grant;
  `else
  bit license_reset = 1;
  bit request_valid;
  wire request_ready, response_valid;
  bit [2:0] request_opcode;
  bit [28:0] request_address;
  bit [15:0] request_mask;
  bit [127:0] request_data;
  wire [2:0] response_opcode, response_size;
  wire [8:0] response_source;
  wire [127:0] response_data;

  OffSwitchLicenseMMIO license_register (
    .clock(clock), .reset(license_reset), .auto_grant_out(grant),
    .auto_in_a_ready(request_ready), .auto_in_a_valid(request_valid),
    .auto_in_a_bits_opcode(request_opcode), .auto_in_a_bits_param('0),
    .auto_in_a_bits_size(3'd2), .auto_in_a_bits_source(9'd1),
    .auto_in_a_bits_address(request_address), .auto_in_a_bits_mask(request_mask),
    .auto_in_a_bits_data(request_data), .auto_in_a_bits_corrupt(1'b0),
    .auto_in_d_ready(1'b1), .auto_in_d_valid(response_valid),
    .auto_in_d_bits_opcode(response_opcode), .auto_in_d_bits_size(response_size),
    .auto_in_d_bits_source(response_source), .auto_in_d_bits_data(response_data)
  );

  // Status bits match the signed-license mailbox CSR definitions.
  localparam int STATUS_READY = 0, STATUS_SIGNER = 2, STATUS_GRANT = 3;
  localparam int STATUS_DONE = 4, STATUS_PASS = 5;
  localparam int STATUS = 'h00, SUBMIT = 'h08, NONCE = 'h20, SIGNATURE = 'h40;
  localparam int LICENSE_POLL_CYCLES = 256;
  localparam int LICENSE_TIMEOUT_CYCLES = 15_000_000;
  logic [255:0] nonces [SWITCH_COUNT];
  logic [511:0] signatures [2 * SWITCH_COUNT];

  task automatic transfer(input bit read, input int engine, input int offset, input bit [31:0] data,
                          output logic [31:0] result);
    int lane_byte;
    lane_byte = offset % 16;
    @(negedge clock);
    request_opcode = read ? 3'd4 : 3'd0;
    request_address = 29'(32'h1001_0000 + engine * 'h80 + offset);
    request_mask = 16'hf << lane_byte;
    request_data = {96'b0, data} << (8 * lane_byte);
    request_valid = 1;
    do @(posedge clock); while (request_ready !== 1'b1);
    if (response_valid !== 1'b1 || response_opcode !== (read ? 3'd1 : 3'd0) ||
        response_size !== 3'd2 || response_source !== 9'd1)
      $fatal(1, "Invalid TileLink response at offset %02h", offset);
    result = response_data[8 * lane_byte +: 32];
    @(negedge clock);
    request_valid = 0;
  endtask

  task automatic read32(input int engine, input int offset, output logic [31:0] data);
    transfer(1, engine, offset, 0, data);
  endtask

  task automatic write32(input int engine, input int offset, input bit [31:0] data);
    logic [31:0] ignored;
    transfer(0, engine, offset, data, ignored);
  endtask

  task automatic wait_status(input int engine, input int flag, output logic [31:0] status);
    repeat (LICENSE_TIMEOUT_CYCLES / LICENSE_POLL_CYCLES) begin
      read32(engine, STATUS, status);
      if (status[flag] === 1'b1) return;
      repeat (LICENSE_POLL_CYCLES) @(posedge clock);
    end
    $fatal(1, "License status bit %0d timed out, last status=%08h", flag, status);
  endtask

  task automatic authorize(input int engine, input logic [511:0] signature,
                           output logic [31:0] status);
    for (int word_index = 0; word_index < 16; word_index++)
      write32(engine, SIGNATURE + word_index * 4, signature[word_index * 32 +: 32]);
    write32(engine, SUBMIT, 1);
    wait_status(engine, STATUS_DONE, status);
  endtask

  task automatic read_nonce(input int engine, output logic [255:0] value);
    for (int word_index = 0; word_index < 8; word_index++)
      read32(engine, NONCE + word_index * 4, value[word_index * 32 +: 32]);
  endtask

  task automatic reset_license;
    @(negedge clock);
    #1 license_reset = 1;
    repeat (8) @(negedge clock);
    #1 license_reset = 0;
  endtask

  `endif

  `include "off_switch_checks.svh"

  task automatic compare_lanes;
    if ({gold.io_cmd_ready, gold.io_busy, gold.io_interrupt, gold.a_valid, gold.d_ready} !==
        {fault.io_cmd_ready, fault.io_busy, fault.io_interrupt, fault.a_valid, fault.d_ready})
      $fatal(1, "Granted Gemmini changed interface control at cycle %0d", cycles);
    if (gold.a_valid && ({gold.a_opcode, gold.a_size, gold.a_source, gold.a_addr, gold.a_mask} !==
                         {fault.a_opcode, fault.a_size, fault.a_source, fault.a_addr, fault.a_mask} ||
                         (gold.a_opcode != 4 && gold.a_data !== fault.a_data)))
      $fatal(1, "Granted Gemmini changed a memory request at cycle %0d", cycles);
    if (fault.tiles_wrong || fault.invalid_request_cycle)
      $fatal(1, "Granted Gemmini failed the matrix-product workload, tiles_wrong=%0d invalid_request_cycle=%0d",
             fault.tiles_wrong, fault.invalid_request_cycle);
  endtask

  `ifdef OFF_SWITCH_LOCK
    `include "off_switch_lock.svh"
  `else
  always @(posedge workload_clock) begin
    #1;
    if (!reset) cycles++;
    check_switched_signals();
    if (!reset && (&grant)) begin
      compare_lanes();
      `ifdef OFF_SWITCH_DIRECT
      pulse_active_switches();
      `endif
    end
  end

  initial begin : scenario
    `ifdef OFF_SWITCH_DIRECT
    repeat (8) @(negedge clock);
    direct_grant = '1;
    `else
    logic [31:0] status;
    logic [255:0] nonce;
    $readmemh("license-nonces.hex", nonces);
    $readmemh("license-signatures.hex", signatures);

    repeat (8) @(negedge clock);
    workload_clock_enabled = 0;
    reset_license();
    for (int engine = 0; engine < SWITCH_COUNT; engine++) begin
      wait_status(engine, STATUS_READY, status);
      if ({status[STATUS_SIGNER], status[STATUS_GRANT], grant[engine]} !== 3'b000)
        $fatal(1, "Engine %0d must start denied, status=%08h", engine, status);
      read_nonce(engine, nonce);
      if ($isunknown(nonces[engine]) || nonce !== nonces[engine])
        $fatal(1, "Engine %0d challenge mismatch, expected=%h actual=%h", engine, nonces[engine], nonce);
      for (int other = 0; other < engine; other++)
        if (nonce === nonces[other])
          $fatal(1, "Engines %0d and %0d have the same challenge", engine, other);
    end

    // Writes to the read-only status CSR cannot grant a switch.
    write32(0, STATUS, '1);
    read32(0, STATUS, status);
    if (status[STATUS_GRANT] !== 1'b0 || grant !== '0)
      $fatal(1, "A write to status granted a switch");
    authorize(0, '0, status);
    if ({status[STATUS_PASS], status[STATUS_SIGNER], status[STATUS_GRANT]} !== 3'b000 || grant !== '0)
      $fatal(1, "Malformed signature changed authorization, status=%08h", status);
    $display("LICENSE malformed_signature_rejected status=%08h", status);

    if (SWITCH_COUNT > 1) begin
      authorize(1, signatures[0], status);
      if ({status[STATUS_PASS], status[STATUS_SIGNER], status[STATUS_GRANT]} !== 3'b000 || grant !== '0)
        $fatal(1, "Engine 1 accepted a signature for engine 0, status=%08h", status);
      $display("LICENSE cross_engine_signature_rejected status=%08h", status);
    end

    for (int engine = 0; engine < SWITCH_COUNT; engine++) begin
      for (int signer = 0; signer < 2; signer++) begin
        logic [SWITCH_COUNT-1:0] expected_grants;
        wait_status(engine, STATUS_READY, status);
        if (status[STATUS_SIGNER] !== 1'(signer))
          $fatal(1, "Engine %0d has unexpected signer challenge, status=%08h", engine, status);
        read_nonce(engine, nonce);
        if (nonce !== nonces[engine])
          $fatal(1, "Engine %0d challenge changed before signer %0d", engine, signer);
        if ($isunknown(signatures[engine * 2 + signer]))
          $fatal(1, "Missing signature fixture for engine %0d signer %0d", engine, signer);
        authorize(engine, signatures[engine * 2 + signer], status);
        // Every lower-index engine has passed both checks. This engine grants
        // its switch only after its second acceptance.
        expected_grants = (SWITCH_COUNT'(1) << (engine + signer)) - 1'b1;
        if (status[STATUS_PASS] !== 1'b1 ||
            status[STATUS_GRANT] !== 1'(signer) || grant !== expected_grants)
          $fatal(1, "Engine %0d signer %0d gave grants %h, expected %h, status=%08h",
                 engine, signer, grant, expected_grants, status);
        $display("LICENSE engine=%0d signer=%0d signature_accepted status=%08h grants=%h",
                 engine, signer, status, grant);
      end
    end

    `endif
    @(negedge clock);
    #1 workload_clock_enabled = 1;
    reset = 0;
    repeat (1800) @(negedge clock);
    if (gold.tiles_checked < 8 || fault.tiles_checked != gold.tiles_checked ||
        fault.cmd_count != gold.cmd_count || fault.gets != gold.gets || fault.puts != gold.puts ||
        saw_active !== '1)
      $fatal(1, "Granted workload mismatch, reference_tiles=%0d switched_tiles=%0d active_sites=%0d/%0d",
             gold.tiles_checked, fault.tiles_checked, $countones(saw_active), SWITCH_COUNT);
    $display("ENABLED cycles=%0d tiles=%0d commands=%0d reads=%0d writes=%0d active_sites=%0d",
             cycles, fault.tiles_checked, fault.cmd_count, fault.gets, fault.puts, $countones(saw_active));

    `ifdef OFF_SWITCH_DIRECT
    if (pulsed_active !== '1)
      $fatal(1, "Only %0d/%0d switches pulsed while active", $countones(pulsed_active), SWITCH_COUNT);
    $display("DIRECT_GRANTS active_independent_switches=%0d", $countones(pulsed_active));
    direct_grant = '0;
    `else
    reset_license();
    `endif
    repeat (512) @(negedge clock);
    if (grant !== '0)
      $fatal(1, "The switch grants did not clear");
    if (!fault.tiles_wrong && fault.tiles_checked == gold.tiles_checked)
      $fatal(1, "Denied Gemmini completed the same correct workload");
    $display("DENIED sites=%0d reference_tiles=%0d switched_tiles=%0d wrong_tiles=%0d",
             SWITCH_COUNT, gold.tiles_checked, fault.tiles_checked, fault.tiles_wrong);
    $display("OFF_SWITCH_REGRESSION PASS");
    $finish;
  end

  initial begin
    `ifdef OFF_SWITCH_DIRECT
    repeat (3000) @(posedge clock);
    `else
    repeat ((2 * SWITCH_COUNT + 3) * LICENSE_TIMEOUT_CYCLES) @(posedge clock);
    `endif
    $fatal(1, "Off-switch regression timed out");
  end
  `endif
