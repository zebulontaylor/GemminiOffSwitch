// The two lanes accept the same ordered command stream independently. Each owns
// its memory and TileLink manager so a stalled DUT cannot block the reference.
module lane #(parameter bit GOLDEN = 0)(input logic clock, reset);
  logic [7:0] memory [0:65535];
  logic io_cmd_valid;
  wire io_cmd_ready, io_busy, io_interrupt, a_valid, d_ready;
  logic [6:0] io_cmd_bits_inst_funct;
  logic [63:0] io_cmd_bits_rs1, io_cmd_bits_rs2;
  wire [2:0] a_opcode;
  wire [3:0] a_size;
  wire [4:0] a_source;
  wire [31:0] a_addr;
  wire [15:0] a_mask;
  wire [127:0] a_data;
  logic d_valid, d_is_read;
  logic [3:0] d_size;
  logic [4:0] d_source;
  logic [31:0] d_addr;
  logic [127:0] d_data;
  integer d_index, d_beats, put_index;
  integer gets = 0, puts = 0, cmd_count = 0;
  integer invalid_request_cycle = 0;
  wire a_ready = !d_valid && invalid_request_cycle == 0;

  if (GOLDEN) begin : reference
    `include "reference_ports.svh"
  end else begin : protected_lane
    `include "dut_ports.svh"
  end
  `include "rocc_workload.svh"

  // One transaction at a time; a multibeat Put keeps A available until its last beat.
  // Get response beats stay stable under D-channel backpressure.
  always_comb begin
    d_data = 0;
    if (d_valid && d_is_read)
      for (int byte_i = 0; byte_i < 16; byte_i++)
        d_data[8*byte_i +: 8] = memory[(d_addr[15:0] & 16'hfff0) + d_index*16 + byte_i];
  end
  always @(posedge clock) begin
    if (reset) begin
      d_valid <= 0;
      put_index <= 0;
    end else begin
      if (io_cmd_valid && io_cmd_ready) cmd_count++;
      if (d_valid && d_ready) begin
        if (d_index + 1 == d_beats) d_valid <= 0;
        else d_index <= d_index + 1;
      end
      if (a_valid && a_ready) begin
        int beats;
        beats = a_size > 4 ? 1 << (a_size - 4) : 1;
        if ($isunknown({a_opcode, a_addr, a_size}) || a_addr[31:16] != 16'h8000 ||
            a_size > 6 || !(a_opcode inside {0, 1, 4})) begin
          if (GOLDEN) $fatal(1, "Invalid golden TL request addr=%h size=%d opcode=%d", a_addr, a_size, a_opcode);
          // A corrupted lane can no longer use this memory model. Stop accepting
          // its requests and keep sampling both DUTs until the common window ends.
          invalid_request_cycle <= cycle_tb.cycles + 1;
        end else if (a_opcode == 4) begin
          gets++;
          d_valid <= 1;
          d_is_read <= 1;
          d_beats <= beats;
          d_size <= a_size;
          d_source <= a_source;
          d_addr <= a_addr;
          d_index <= 0;
        end else if (a_opcode == 0 || a_opcode == 1) begin
          for (int byte_i = 0; byte_i < 16; byte_i++)
            if (a_mask[byte_i])
              memory[(a_addr[15:0] & 16'hfff0) + put_index*16 + byte_i] <= a_data[8*byte_i +: 8];
          if (put_index + 1 == beats) begin
            puts++;
            put_index <= 0;
            d_valid <= 1;
            d_is_read <= 0;
            d_size <= a_size;
            d_source <= a_source;
            d_beats <= 1;
            d_index <= 0;
          end else put_index <= put_index + 1;
        end
      end
      if (GOLDEN && io_interrupt) $fatal(1, "Golden Gemmini raised a TLB fault");
    end
  end
endmodule

module cycle_tb;
  bit clock = 0;
  always #5 clock = !clock;
  bit reset = 1;
  int cycles = 0;
  bit workload_clock_enabled = 1;
  wire workload_clock = clock && workload_clock_enabled;
  lane #(.GOLDEN(1)) gold(workload_clock, reset);
  lane #(.GOLDEN(0)) fault(workload_clock, reset);
  `include "off_switch_regression.svh"
endmodule
