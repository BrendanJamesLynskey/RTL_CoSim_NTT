// =============================================================================
// mod_mul_barrett: pipelined Barrett modular multiplier, r = a * b mod q
// =============================================================================
//
// The modulus is a run-time input, as in an RNS datapath that switches limbs:
// each limb has its own W-bit prime q (2^(W-1) < q < 2^W) and its own Barrett
// constant mu = floor(2^(2W) / q), which software precomputes. With x = a * b,
//
//     q3 = floor( floor(x / 2^(W-1)) * mu / 2^(W+1) )      (estimate of floor(x / q))
//     r0 = x - q3 * q                                       (0 <= r0 < 3q)
//     r  = r0 - {0, q, 2q}                                  (final correction)
//
// The estimate is never too large and at most 2 too small, so r0 < 3q and fits
// in W + 2 bits; the subtraction is done modulo 2^(W+2).
//
// Pipeline (latency LAT = 4, one result per cycle):
//   S1  x  = a * b
//   S2  q3 = ((x >> (W-1)) * mu) >> (W+1)
//   S3  r0 = x - q3 * q            (low W+2 bits)
//   S4  r  = r0 - {0, q, 2q}
//
// `define INJECT_BUG keeps only one correction step, a seeded bug that only
// operands needing two corrections expose (rare for uniform random stimulus;
// see the functional coverage in tb/ and deck SimEng 05).
// =============================================================================

module mod_mul_barrett #(
    parameter int W = 50
) (
    input  logic           clk,
    input  logic           rst_n,
    input  logic           in_valid,
    input  logic [W-1:0]   a,
    input  logic [W-1:0]   b,
    input  logic [W-1:0]   q,
    input  logic [W:0]     mu,
    output logic           out_valid,
    output logic [W-1:0]   r,
    output logic [1:0]     corr         // corrections applied to this result (for coverage)
);
    localparam int LAT = 4;

    logic [LAT-1:0]   v;
    logic [2*W-1:0]   x1;
    /* verilator lint_off UNUSEDSIGNAL */
    logic [2*W-1:0]   x2;           // only the low W+2 bits reach S3
    /* verilator lint_on UNUSEDSIGNAL */
    logic [W:0]       q3;
    logic [W+1:0]     r0;

    // S2 combinational: the quotient estimate
    logic [W:0]       xh;
    /* verilator lint_off UNUSEDSIGNAL */
    logic [2*W+1:0]   q2;           // only the top W+1 bits are kept
    /* verilator lint_on UNUSEDSIGNAL */
    assign xh = x1[2*W-1:W-1];
    assign q2 = xh * mu;

    // S3 combinational: remainder modulo 2^(W+2)
    /* verilator lint_off UNUSEDSIGNAL */
    logic [2*W+1:0]   q3q;          // only the low W+2 bits matter
    /* verilator lint_on UNUSEDSIGNAL */
    logic [W+1:0]     r0_next;
    assign q3q     = q3 * q;
    assign r0_next = x2[W+1:0] - q3q[W+1:0];

    // S4 combinational: final correction
    logic [W+1:0]     q_ext, q2_ext;
    assign q_ext  = {2'b00, q};
    assign q2_ext = {1'b0, q, 1'b0};

    always_ff @(posedge clk or negedge rst_n) begin
        if (!rst_n) begin
            v    <= '0;
            x1   <= '0;
            x2   <= '0;
            q3   <= '0;
            r0   <= '0;
            r    <= '0;
            corr <= '0;
        end else begin
            v  <= {v[LAT-2:0], in_valid};
            // S1
            x1 <= a * b;
            // S2
            x2 <= x1;
            q3 <= q2[2*W+1:W+1];
            // S3
            r0 <= r0_next;
            // S4
`ifdef INJECT_BUG
            if (r0 >= q_ext) begin
                r    <= W'(r0 - q_ext);
                corr <= (r0 >= q2_ext) ? 2'd2 : 2'd1;
            end else begin
                r    <= W'(r0);
                corr <= 2'd0;
            end
`else
            if (r0 >= q2_ext) begin
                r    <= W'(r0 - q2_ext);
                corr <= 2'd2;
            end else if (r0 >= q_ext) begin
                r    <= W'(r0 - q_ext);
                corr <= 2'd1;
            end else begin
                r    <= W'(r0);
                corr <= 2'd0;
            end
`endif
        end
    end

    assign out_valid = v[LAT-1];

endmodule
