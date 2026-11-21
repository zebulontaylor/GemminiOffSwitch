// One host issues Gemmini commands to each lane. The ISA encodings match the
// pinned gemmini-rocc-tests/include/gemmini.h macros for the default 16x16 tile.
localparam int DIM = 16;
localparam int A_MEM = 'h1000, B_MEM = 'h2000, C_MEM = 'h3000;
localparam logic [63:0] DRAM_BASE = 64'h8000_0000;
localparam logic [31:0] A_SP = 0, B_SP = DIM, C_ACC = 32'h8000_0000;
localparam logic [31:0] GARBAGE = 32'hffff_ffff;
localparam logic [31:0] SCALE_ONE = 32'h3f80_0000;  // IEEE binary32

int tiles_checked = 0, tiles_wrong = 0;
int late_flush_after = 0;
bit late_flush_issued = 0;
logic [31:0] host_rng = 32'h8123_4567;
byte signed tile_a [DIM][DIM], tile_b [DIM][DIM], expected_tile [DIM][DIM];
`ifdef MESH_PROFILE
int workload = 0, wrong_elements = 0, unknown_elements = 0;

function automatic byte signed nonzero_operand(input logic [31:0] sample);
    case (sample[1:0])
        2'd0: return -2;
        2'd1: return -1;
        2'd2: return 1;
        2'd3: return 2;
    endcase
endfunction
`endif

function automatic logic [63:0] descriptor(input logic [31:0] addr);
    return {16'(DIM), 16'(DIM), addr};
endfunction

function automatic logic [31:0] xorshift(input logic [31:0] x);
    x ^= x << 13;
    x ^= x >> 17;
    return x ^ (x << 5);
endfunction

task automatic initialize_tile();
    int i, j, k, sum;
    for (i = 0; i < DIM; i++) begin
        for (j = 0; j < DIM; j++) begin
            `ifdef MESH_PROFILE
            logic [31:0] a_sample;
            a_sample = xorshift(host_rng);
            host_rng = xorshift(a_sample);
            case (workload)
                0: begin
                    tile_a[i][j] = byte'(int'(a_sample % 5) - 2);
                    tile_b[i][j] = byte'(int'(host_rng % 5) - 2);
                end
                1: begin
                    tile_a[i][j] = nonzero_operand(a_sample);
                    tile_b[i][j] = nonzero_operand(host_rng);
                end
                2: begin
                    tile_a[i][j] = j == (i + tiles_checked) % DIM
                                  ? (a_sample[0] ? -8'sd1 : 8'sd1) : 8'sd0;
                    tile_b[i][j] = byte'(host_rng);
                end
                3: begin
                    tile_a[i][j] = byte'(int'(a_sample[3:0]) - 8);
                    tile_b[i][j] = byte'(int'(host_rng[3:0]) - 8);
                end
            endcase
            `else
            host_rng = xorshift(host_rng);
            tile_a[i][j] = byte'(int'(host_rng % 5) - 2);
            host_rng = xorshift(host_rng);
            tile_b[i][j] = byte'(int'(host_rng % 5) - 2);
            `endif
            memory[A_MEM + i * DIM + j] = tile_a[i][j];
            memory[B_MEM + i * DIM + j] = tile_b[i][j];
            memory[C_MEM + i * DIM + j] = 8'h80;  // outside the possible result range
        end
    end
    for (i = 0; i < DIM; i++) begin
        for (j = 0; j < DIM; j++) begin
            sum = 0;
            for (k = 0; k < DIM; k++)
                sum += int'(tile_a[i][k]) * int'(tile_b[k][j]);
            `ifdef MESH_PROFILE
            // The identity store scale clips the accumulator to signed bytes.
            if (sum > 127) sum = 127;
            if (sum < -128) sum = -128;
            `endif
            expected_tile[i][j] = byte'(sum);
            `ifdef MESH_PROFILE
            // A missing write must fail even when the expected result is -128.
            if (workload != 0)
                memory[C_MEM + i * DIM + j] = ~expected_tile[i][j];
            `endif
        end
    end
endtask

task automatic issue_rocc(input logic [6:0] funct,
                          input logic [63:0] rs1, rs2);
    @(negedge clock);
    io_cmd_bits_inst_funct = funct;
    io_cmd_bits_rs1 = rs1;
    io_cmd_bits_rs2 = rs2;
    io_cmd_valid = 1'b1;
    do @(posedge clock); while (io_cmd_ready !== 1'b1);
    if (funct == 7)
        $display("FLUSH_ACCEPTED lane_golden=%0d cycle=%0d", GOLDEN, cycle_tb.cycles + 1);
    @(negedge clock);
    io_cmd_valid = 1'b0;
endtask

task automatic check_output();
    bit mismatch;
    mismatch = 0;
    for (int i = 0; i < DIM; i++)
        for (int j = 0; j < DIM; j++)
            if (memory[C_MEM + i * DIM + j] !== expected_tile[i][j]) begin
                `ifdef MESH_PROFILE
                wrong_elements++;
                if ($isunknown(memory[C_MEM + i * DIM + j])) unknown_elements++;
                `endif
                if (!mismatch && (GOLDEN || tiles_wrong == 0))
                    $display("TILE_MISMATCH lane_golden=%0d tile=%0d row=%0d col=%0d expected=%0d actual=%h",
                             GOLDEN, tiles_checked, i, j, expected_tile[i][j], memory[C_MEM + i * DIM + j]);
                mismatch = 1;
            end
    tiles_checked++;
    if (mismatch) begin
        tiles_wrong++;
        if (GOLDEN) $fatal(1, "Unclamped Gemmini failed the signed matmul oracle");
    end
endtask

initial begin : run_host
    void'($value$plusargs("LATE_FLUSH=%d", late_flush_after));
    `ifdef MESH_PROFILE
    void'($value$plusargs("WORKLOAD=%d", workload));
    if (workload < 0 || workload > 3) $fatal(1, "WORKLOAD must be in 0..3");
    `endif
    io_cmd_valid = 0;
    io_cmd_bits_inst_funct = 0;
    io_cmd_bits_rs1 = 0;
    io_cmd_bits_rs2 = 0;
    wait (reset === 1'b1);
    @(negedge reset);
    // Identity scaling, byte stride DIM, unit row strides, weight stationary.
    issue_rocc(0, {SCALE_ONE, 16'(DIM), 8'd1, 8'd1}, 64'(DIM)); // config_ld
    issue_rocc(0, {SCALE_ONE, 16'd1, 16'd4}, 64'h0001_0000_0000_0000); // config_ex
    issue_rocc(0, 2, {SCALE_ONE, 32'(DIM)}); // config_st
    forever begin
        initialize_tile();
        issue_rocc(2, DRAM_BASE + 64'(A_MEM), descriptor(A_SP));
        issue_rocc(2, DRAM_BASE + 64'(B_MEM), descriptor(B_SP));
        issue_rocc(6, descriptor(B_SP), descriptor(C_ACC));
        issue_rocc(4, descriptor(A_SP), descriptor(GARBAGE));
        issue_rocc(3, DRAM_BASE + 64'(C_MEM), descriptor(C_ACC));
        do @(negedge clock); while (io_busy !== 1'b0);
        check_output();
        if (late_flush_after > 0 && !late_flush_issued && cycle_tb.cycles >= late_flush_after) begin
            issue_rocc(7, 0, 0);  // rs1[0]=0 requests retry on Gemmini's flush command
            late_flush_issued = 1;
        end
    end
end
