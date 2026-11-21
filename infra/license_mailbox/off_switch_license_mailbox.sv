module off_switch_license_mailbox #(
    parameter int unsigned INSTANCE_ID = 0
)(
    input  logic         clk,
    input  logic         reset,
    input  logic         submit,
    input  logic [511:0] signature,
    output logic [255:0] nonce,
    output logic [31:0]  status,
    output logic         enabled
);
    localparam logic [31:0] NONCE_DOMAIN = 32'h4f530000 | INSTANCE_ID;

    logic [511:0] signature_q;
    logic busy_q, done_q, passed_q;
    logic nonce_ready, license_ready, signature_in_range;
    logic signer, verification_passed, ready;

    // Keep the engine's input stable even if software overwrites its staging
    // registers during verification.
    assign signature_in_range =
        (signature[511:256] != '0) && (signature[511:256] < ecdsa_pkg::PRIME_N) &&
        (signature[255:0] != '0)   && (signature[255:0] < ecdsa_pkg::PRIME_N);
    assign ready = nonce_ready && !busy_q;
    assign status = {26'b0, passed_q, done_q, enabled, signer, busy_q, ready};

    always_ff @(posedge clk) begin
        if (reset) begin
            signature_q <= '0;
            busy_q <= 1'b0;
            done_q <= 1'b0;
            passed_q <= 1'b0;
        end else if (busy_q && license_ready) begin
            busy_q <= 1'b0;
            done_q <= 1'b1;
            passed_q <= verification_passed;
        end else if (submit && ready) begin
            signature_q <= signature;
            busy_q <= signature_in_range;
            done_q <= !signature_in_range;
            passed_q <= 1'b0;
        end
    end

    security_block #(
        .CRYPTO_TYPE(0),
        .NUM_SIGNERS(2),
        .NAMESPACE_NONCE(1'b1),
        .NONCE_DOMAIN(NONCE_DOMAIN)
    ) verifier (
        .clk(clk),
        .rst_n(!reset),
        .license_valid(busy_q),
        .license_ready(license_ready),
        .license_data(signature_q),
        .workload_valid(1'b0),
        .workload_a(8'b0),
        .workload_b(8'b0),
        .trng_seed(256'b0),
        .trng_load_seed(1'b0),
        .nonce(nonce),
        .nonce_ready(nonce_ready),
        .workload_result(),
        .result_valid(),
        .allowance(),
        .enabled(enabled),
        .license_signer(signer),
        .license_passed(verification_passed)
    );
endmodule
