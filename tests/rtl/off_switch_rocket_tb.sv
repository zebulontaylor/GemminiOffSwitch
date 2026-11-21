module off_switch_rocket_tb;
    localparam logic [31:0] ROM_BASE = 32'h80000000;
    localparam logic [31:0] DRAM_END = ROM_BASE + 32'h2000;
    localparam logic [30:0] REPORT_ADDRESS = 31'h60000000;
    localparam int ROM_BYTES = 4096;

    logic clock = 0;
    logic reset = 1;
    always #5 clock = ~clock;

    logic [7:0] rom [0:ROM_BYTES-1];
    logic mem_ar_valid;
    logic [3:0] mem_ar_id;
    logic [31:0] mem_ar_addr;
    logic [7:0] mem_ar_len;
    logic [2:0] mem_ar_size;
    logic [1:0] mem_ar_burst;
    logic mem_aw_valid;
    logic mem_r_ready;
    logic mem_r_valid = 0;
    logic [3:0] mem_r_id;
    logic [31:0] mem_r_addr;
    logic [7:0] mem_r_remaining;
    logic [63:0] mem_r_data;

    logic mmio_ar_valid;
    logic mmio_aw_valid;
    logic [30:0] mmio_aw_addr;
    logic [7:0] mmio_aw_len;
    logic [2:0] mmio_aw_size;
    logic mmio_w_valid;
    logic [63:0] mmio_w_data;
    logic [7:0] mmio_w_strb;
    logic mmio_w_last;
    logic report_address_seen = 0;
    string check_mode;
    bit expect_grant;

    assign mem_r_data = mem_r_addr < ROM_BASE + ROM_BYTES ?
        {rom[mem_r_addr-ROM_BASE+7], rom[mem_r_addr-ROM_BASE+6],
         rom[mem_r_addr-ROM_BASE+5], rom[mem_r_addr-ROM_BASE+4],
         rom[mem_r_addr-ROM_BASE+3], rom[mem_r_addr-ROM_BASE+2],
         rom[mem_r_addr-ROM_BASE+1], rom[mem_r_addr-ROM_BASE]} : 64'b0;

    ExampleRocketSystem dut (
        .io_aggregator_5_clock(clock), .io_aggregator_5_reset(reset),
        .io_aggregator_4_clock(clock), .io_aggregator_4_reset(reset),
        .io_aggregator_3_clock(clock), .io_aggregator_3_reset(reset),
        .io_aggregator_2_clock(clock), .io_aggregator_2_reset(reset),
        .io_aggregator_1_clock(clock), .io_aggregator_1_reset(reset),
        .io_aggregator_0_clock(clock), .io_aggregator_0_reset(reset),
        .resetctrl_hartIsInReset_0(reset),
        .debug_clock(clock), .debug_reset(reset),
        .debug_clockeddmi_dmi_req_valid(1'b0),
        .debug_clockeddmi_dmi_req_bits_addr(7'b0),
        .debug_clockeddmi_dmi_req_bits_data(32'b0),
        .debug_clockeddmi_dmi_req_bits_op(2'b0),
        .debug_clockeddmi_dmi_resp_ready(1'b1),
        .debug_clockeddmi_dmiClock(clock), .debug_clockeddmi_dmiReset(reset),
        .debug_dmactiveAck(1'b0),
        .reset_vector_0(ROM_BASE), .interrupts(2'b0),

        .mem_axi4_0_aw_ready(1'b1), .mem_axi4_0_aw_valid(mem_aw_valid),
        .mem_axi4_0_w_ready(1'b1),
        .mem_axi4_0_b_valid(1'b0), .mem_axi4_0_b_bits_id(4'b0),
        .mem_axi4_0_b_bits_resp(2'b0),
        .mem_axi4_0_ar_ready(!mem_r_valid), .mem_axi4_0_ar_valid(mem_ar_valid),
        .mem_axi4_0_ar_bits_id(mem_ar_id), .mem_axi4_0_ar_bits_addr(mem_ar_addr),
        .mem_axi4_0_ar_bits_len(mem_ar_len), .mem_axi4_0_ar_bits_size(mem_ar_size),
        .mem_axi4_0_ar_bits_burst(mem_ar_burst),
        .mem_axi4_0_r_ready(mem_r_ready), .mem_axi4_0_r_valid(mem_r_valid),
        .mem_axi4_0_r_bits_id(mem_r_id), .mem_axi4_0_r_bits_resp(2'b00),
        .mem_axi4_0_r_bits_data(mem_r_data), .mem_axi4_0_r_bits_last(mem_r_remaining == 0),

        .mmio_axi4_0_aw_ready(!report_address_seen), .mmio_axi4_0_aw_valid(mmio_aw_valid),
        .mmio_axi4_0_aw_bits_addr(mmio_aw_addr),
        .mmio_axi4_0_aw_bits_len(mmio_aw_len), .mmio_axi4_0_aw_bits_size(mmio_aw_size),
        .mmio_axi4_0_w_ready(report_address_seen), .mmio_axi4_0_w_valid(mmio_w_valid),
        .mmio_axi4_0_w_bits_data(mmio_w_data), .mmio_axi4_0_w_bits_strb(mmio_w_strb),
        .mmio_axi4_0_w_bits_last(mmio_w_last),
        .mmio_axi4_0_b_valid(1'b0), .mmio_axi4_0_b_bits_id(4'b0),
        .mmio_axi4_0_b_bits_resp(2'b0),
        .mmio_axi4_0_ar_ready(1'b1), .mmio_axi4_0_ar_valid(mmio_ar_valid),
        .mmio_axi4_0_r_valid(1'b0), .mmio_axi4_0_r_bits_id(4'b0),
        .mmio_axi4_0_r_bits_resp(2'b0), .mmio_axi4_0_r_bits_data(64'b0),
        .mmio_axi4_0_r_bits_last(1'b0),

        .l2_frontend_bus_axi4_0_aw_valid(1'b0),
        .l2_frontend_bus_axi4_0_aw_bits_id(8'b0),
        .l2_frontend_bus_axi4_0_aw_bits_addr(32'b0),
        .l2_frontend_bus_axi4_0_aw_bits_len(8'b0),
        .l2_frontend_bus_axi4_0_aw_bits_size(3'b0),
        .l2_frontend_bus_axi4_0_aw_bits_burst(2'b0),
        .l2_frontend_bus_axi4_0_aw_bits_lock(1'b0),
        .l2_frontend_bus_axi4_0_aw_bits_cache(4'b0),
        .l2_frontend_bus_axi4_0_aw_bits_prot(3'b0),
        .l2_frontend_bus_axi4_0_aw_bits_qos(4'b0),
        .l2_frontend_bus_axi4_0_w_valid(1'b0),
        .l2_frontend_bus_axi4_0_w_bits_data(64'b0),
        .l2_frontend_bus_axi4_0_w_bits_strb(8'b0),
        .l2_frontend_bus_axi4_0_w_bits_last(1'b0),
        .l2_frontend_bus_axi4_0_b_ready(1'b1),
        .l2_frontend_bus_axi4_0_ar_valid(1'b0),
        .l2_frontend_bus_axi4_0_ar_bits_id(8'b0),
        .l2_frontend_bus_axi4_0_ar_bits_addr(32'b0),
        .l2_frontend_bus_axi4_0_ar_bits_len(8'b0),
        .l2_frontend_bus_axi4_0_ar_bits_size(3'b0),
        .l2_frontend_bus_axi4_0_ar_bits_burst(2'b0),
        .l2_frontend_bus_axi4_0_ar_bits_lock(1'b0),
        .l2_frontend_bus_axi4_0_ar_bits_cache(4'b0),
        .l2_frontend_bus_axi4_0_ar_bits_prot(3'b0),
        .l2_frontend_bus_axi4_0_ar_bits_qos(4'b0),
        .l2_frontend_bus_axi4_0_r_ready(1'b1)
    );

    initial begin
        if (!$value$plusargs("ROCKET_CHECK=%s", check_mode))
            $fatal(1, "ROCKET_CHECK plusarg is required");
        expect_grant = check_mode == "full";
        $readmemh("firmware.hex", rom);
        repeat (10) @(negedge clock);
        reset = 0;
        repeat (expect_grant ? 30000000 : 20000) @(negedge clock);
        $fatal(1, "Rocket firmware timed out");
    end

    always @(posedge clock) begin
        if (!reset) begin
            if ((dut.off_switch_grant >> 1) !== '0)
                $fatal(1, "A site other than site 0 unlocked");
            if (mem_aw_valid)
                $fatal(1, "Unexpected DRAM write");
            if (mem_ar_valid && !mem_r_valid) begin
                if (mem_ar_size != 3 || mem_ar_burst != 1 || mem_ar_addr[2:0] != 0 ||
                    mem_ar_addr < ROM_BASE ||
                    {1'b0, mem_ar_addr} + (33'(mem_ar_len) + 1) * 8 > DRAM_END)
                    $fatal(1, "Unexpected DRAM read at %h", mem_ar_addr);
                mem_r_valid <= 1;
                mem_r_id <= mem_ar_id;
                mem_r_addr <= mem_ar_addr;
                mem_r_remaining <= mem_ar_len;
            end else if (mem_r_valid && mem_r_ready) begin
                mem_r_valid <= mem_r_remaining != 0;
                mem_r_addr <= mem_r_addr + 8;
                mem_r_remaining <= mem_r_remaining - 1;
            end

            if (mmio_ar_valid)
                $fatal(1, "Unexpected external MMIO read");
            if (mmio_aw_valid && !report_address_seen) begin
                if (mmio_aw_addr != REPORT_ADDRESS || mmio_aw_len != 0 || mmio_aw_size != 2)
                    $fatal(1, "Unexpected external MMIO write at %h", mmio_aw_addr);
                report_address_seen <= 1;
            end
            if (mmio_w_valid && report_address_seen) begin
                if (!mmio_w_last || mmio_w_strb != 8'h0f)
                    $fatal(1, "Invalid firmware report write");
                if (mmio_w_data[31:0] != 0)
                    $fatal(1, "Rocket firmware failed (status 0x%08h)", mmio_w_data[31:0]);
                if (dut.tile_prci_domain.element_reset_domain_rockettile.gemmini.off_switch_grant
                    !== dut.off_switch_grant)
                    $fatal(1, "Gemmini received a different grant vector than the mailbox");
                if (dut.off_switch_grant[0] !== expect_grant)
                    $fatal(1, "Unexpected site 0 grant in %s smoke", check_mode);
                if (expect_grant)
                    $display("PASS Rocket firmware signer sequence, nonce renewal, site isolation, and locked execution");
                else
                    $display("PASS Rocket firmware all nonce domains, malformed rejection, and locked execution during signature verification");
                $finish;
            end
        end
    end
endmodule
