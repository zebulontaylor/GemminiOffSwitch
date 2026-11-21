  localparam int SHORT_DEADLINE = 1767;
  int lock_bit, max_cycles, masked_active_cycles = 0;
  logic [SWITCH_COUNT-1:0] expected_grants;

  task automatic report_lock_result;
    int stock_tiles, returned_tiles, missing_tiles, tile_bytes;
    stock_tiles = gold.tiles_checked;
    returned_tiles = fault.tiles_checked;
    tile_bytes = gold.DIM * gold.DIM;
    // An early extra result has no reference tile yet at this checkpoint.
    missing_tiles = stock_tiles > returned_tiles ? stock_tiles - returned_tiles : 0;
    if (stock_tiles < 2 || gold.tiles_wrong != 0)
      $fatal(1, "Stock Gemmini failed the arithmetic or progress check at cycle %0d", cycles);
    if (lock_bit == -1 &&
        (returned_tiles != stock_tiles || fault.cmd_count != gold.cmd_count ||
         fault.gets != gold.gets || fault.puts != gold.puts))
      $fatal(1, "All-granted Gemmini changed workload progress at cycle %0d", cycles);
    $display("LOCK_RESULT workload=%0d lock_bit=%0d cycles=%0d stock_tiles=%0d returned_tiles=%0d correct_tiles=%0d wrong_tiles=%0d wrong_bytes=%0d unknown_bytes=%0d missing_tiles=%0d missing_bytes=%0d stock_bytes=%0d failed_bytes=%0d invalid_request_cycle=%0d masked_active_cycles=%0d",
             gold.workload, lock_bit, cycles, stock_tiles, returned_tiles,
             returned_tiles - fault.tiles_wrong, fault.tiles_wrong, fault.wrong_elements,
             fault.unknown_elements, missing_tiles, missing_tiles * tile_bytes,
             stock_tiles * tile_bytes, fault.wrong_elements + missing_tiles * tile_bytes,
             fault.invalid_request_cycle, masked_active_cycles);
  endtask

  initial begin : lock_scenario
    if (!$value$plusargs("LOCK_BIT=%d", lock_bit) || lock_bit < -1 || lock_bit >= SWITCH_COUNT)
      $fatal(1, "LOCK_BIT must be -1 or a grant bit in 0..%0d", SWITCH_COUNT - 1);
    if (!$value$plusargs("CYCLES=%d", max_cycles)) max_cycles = 14001;
    if (max_cycles < SHORT_DEADLINE)
      $fatal(1, "CYCLES must be at least %0d", SHORT_DEADLINE);
    expected_grants = '1;
    if (lock_bit >= 0) expected_grants[lock_bit] = 0;
    direct_grant = expected_grants;
    $display("OFF_SWITCH_LOCK START");
    $fflush();

    repeat (8) @(negedge clock);
    #1 reset = 0;
    while (cycles < max_cycles) begin
      @(posedge workload_clock);
      #1;
      cycles++;
      if (grant !== expected_grants)
        $fatal(1, "Grant vector changed at cycle %0d, expected=%h actual=%h",
               cycles, expected_grants, grant);
      check_gate_values();
      if (lock_bit == -1) begin
        check_switched_signals();
        compare_lanes();
      end else if (raw_active[lock_bit]) begin
        masked_active_cycles++;
      end
      #1;
      if (cycles == SHORT_DEADLINE || cycles == max_cycles) report_lock_result();
    end

    // Check that the opened switch follows its current input before another edge.
    direct_grant = '1;
    #0.001;
    if (grant !== '1) $fatal(1, "The grant vector did not reopen");
    check_gate_values();
    $display("OFF_SWITCH_LOCK PASS");
    $finish;
  end
