// =============================================================================
// ntt_butterfly: pipelined Cooley-Tukey (decimation-in-time) NTT butterfly
// =============================================================================
//
//     t = w * b mod q          (Barrett, mod_mul_barrett, 4 cycles)
//     x = a + t mod q
//     y = a - t mod q
//
// a and b are < q; w is a twiddle factor (a power of a root of unity mod q).
// `a` and a user tag travel alongside the multiplier in a shift register,
// so the butterfly accepts one input per cycle and has latency LAT = 5.
// The tag carries the write-back addresses when the butterfly sits in an NTT
// core; the testbench uses it to match results to stimulus.
// =============================================================================

module ntt_butterfly #(
    parameter int W    = 50,
    parameter int TAGW = 8
) (
    input  logic            clk,
    input  logic            rst_n,
    input  logic            in_valid,
    input  logic [W-1:0]    a,
    input  logic [W-1:0]    b,
    input  logic [W-1:0]    w,
    input  logic [TAGW-1:0] in_tag,
    input  logic [W-1:0]    q,
    input  logic [W:0]      mu,
    output logic            out_valid,
    output logic [W-1:0]    x,
    output logic [W-1:0]    y,
    output logic [TAGW-1:0] out_tag,
    output logic [1:0]      corr
);
    localparam int MUL_LAT = 4;

    logic            m_valid;
    logic [W-1:0]    t;
    logic [1:0]      m_corr;

    mod_mul_barrett #(.W(W)) u_mul (
        .clk, .rst_n, .in_valid,
        .a(w), .b(b), .q, .mu,
        .out_valid(m_valid), .r(t), .corr(m_corr)
    );

    // a and the tag wait for the product
    logic [W-1:0]    a_d   [MUL_LAT];
    logic [TAGW-1:0] tag_d [MUL_LAT];

    always_ff @(posedge clk) begin
        a_d[0]   <= a;
        tag_d[0] <= in_tag;
        for (int i = 1; i < MUL_LAT; i++) begin
            a_d[i]   <= a_d[i-1];
            tag_d[i] <= tag_d[i-1];
        end
    end

    // modular add and subtract, W + 1 bits wide
    logic [W:0] sum;
    /* verilator lint_off UNUSEDSIGNAL */
    logic [W:0] sum_red, dif;     // only the low W bits are used
    /* verilator lint_on UNUSEDSIGNAL */
    logic [W-1:0] ad;
    assign ad      = a_d[MUL_LAT-1];
    assign sum     = {1'b0, ad} + {1'b0, t};
    assign sum_red = sum - {1'b0, q};
    assign dif     = {1'b0, ad} - {1'b0, t};

    always_ff @(posedge clk or negedge rst_n) begin
        if (!rst_n) begin
            out_valid <= 1'b0;
            x         <= '0;
            y         <= '0;
            out_tag   <= '0;
            corr      <= '0;
        end else begin
            out_valid <= m_valid;
            x         <= (sum >= {1'b0, q}) ? sum_red[W-1:0] : sum[W-1:0];
            y         <= (ad >= t) ? dif[W-1:0] : W'(dif[W-1:0] + q);
            out_tag   <= tag_d[MUL_LAT-1];
            corr      <= m_corr;
        end
    end

endmodule
