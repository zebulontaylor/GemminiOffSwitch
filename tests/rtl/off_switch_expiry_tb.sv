module off_switch_expiry_tb;
    localparam logic [63:0] SHORT_ALLOWANCE = 64'd9;
    localparam logic [63:0] LONG_ALLOWANCE = 64'd1_000_000;

    `include "off_switch_expiry_vectors.svh"

    logic clk = 1'b0;
    always #5 clk = !clk;

    logic reset = 1'b1;
    logic [1:0] submit = '0;
    logic [1:0][511:0] signature = '0;
    wire [1:0][255:0] nonce;
    wire [1:0][31:0] status;
    wire [1:0] enabled;
    wire [63:0] short_remaining = short_lease.verifier.allowance;
    wire [63:0] long_remaining = long_lease.verifier.allowance;

    off_switch_license_mailbox #(.INSTANCE_ID(0)) short_lease (
        .clk(clk), .reset(reset), .submit(submit[0]), .signature(signature[0]),
        .nonce(nonce[0]), .status(status[0]), .enabled(enabled[0])
    );
    off_switch_license_mailbox #(.INSTANCE_ID(1)) long_lease (
        .clk(clk), .reset(reset), .submit(submit[1]), .signature(signature[1]),
        .nonce(nonce[1]), .status(status[1]), .enabled(enabled[1])
    );

    // Shorten the two allowances in the testbench while exercising the same RTL
    // and clock-driven decrement as in the SoC.
    defparam short_lease.verifier.ALLOWANCE_INCREMENT = SHORT_ALLOWANCE;
    defparam long_lease.verifier.ALLOWANCE_INCREMENT = LONG_ALLOWANCE;

    task automatic submit_pair(input logic [511:0] short_signature, long_signature);
        signature = {long_signature, short_signature};
        submit = '1;
        @(negedge clk);
        assert (status[0][1] && status[1][1]) else $fatal(1, "Both verifiers must enter busy");
        submit = '0;
    endtask

    initial begin
        logic [63:0] initial_long_remaining;
        logic [1:0][255:0] next_nonce;

        repeat (5) @(negedge clk);
        reset = 1'b0;
        do @(negedge clk); while (!(status[0][0] && status[1][0]));
        assert (nonce[0] === NONCE0 && nonce[1] === NONCE1)
            else $fatal(1, "Unexpected reset challenges");

        submit_pair(SIGNER00, SIGNER01);
        do @(negedge clk); while (!(status[0][0] && status[1][0]));
        assert (status[0][5:2] === 4'b1101 && status[1][5:2] === 4'b1101)
            else $fatal(1, "Both verifiers must accept signer 0 while locked");
        assert (nonce[0] === NONCE0 && nonce[1] === NONCE1)
            else $fatal(1, "Signer 1 must sign the same challenge");

        // With these signatures the long lease verifies first. Measure the
        // short lease from the exact cycle its final signer is accepted.
        submit_pair(SIGNER10, SIGNER11);
        do @(negedge clk); while (enabled[0] !== 1'b1);
        assert (status[0][5:2] === 4'b1110 && status[1][5:2] === 4'b1110)
            else $fatal(1, "Both verifiers must accept signer 1 and enable their gates");
        assert (short_remaining === SHORT_ALLOWANCE && long_remaining > SHORT_ALLOWANCE)
            else $fatal(1, "Unexpected initial allowances: short=%0d long=%0d", short_remaining, long_remaining);
        initial_long_remaining = long_remaining;

        @(negedge clk);
        assert (status[0][0] && status[1][0] && nonce[0] !== NONCE0 && nonce[1] !== NONCE1)
            else $fatal(1, "Both accepted signer pairs must rotate their challenges");
        next_nonce = nonce;
        for (logic [63:0] elapsed = 1; elapsed <= SHORT_ALLOWANCE + 1; elapsed++) begin
            assert (short_remaining === (elapsed < SHORT_ALLOWANCE ? SHORT_ALLOWANCE - elapsed : 64'd0))
                else $fatal(1, "Wrong short countdown after %0d cycles: %0d", elapsed, short_remaining);
            assert (enabled[0] === (elapsed < SHORT_ALLOWANCE) && status[0][3] === enabled[0])
                else $fatal(1, "Wrong short gate state after %0d cycles", elapsed);
            assert (long_remaining === initial_long_remaining - elapsed && enabled[1] === 1'b1)
                else $fatal(1, "Wrong long allowance or gate state after %0d cycles", elapsed);
            assert (nonce === next_nonce) else $fatal(1, "Expiry changed a challenge");
            $display("elapsed=%0d short_allowance=%0d short_enabled=%0b long_allowance=%0d long_enabled=%0b",
                     elapsed, short_remaining, enabled[0], long_remaining, enabled[1]);
            @(negedge clk);
        end
        $display("PASS independent allowance expiry");
        $finish;
    end

    initial begin
        repeat (30_000_000) @(posedge clk);
        $fatal(1, "Timed out waiting for signed licenses");
    end
endmodule
