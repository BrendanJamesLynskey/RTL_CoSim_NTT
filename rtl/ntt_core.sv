// =============================================================================
// ntt_core: iterative radix-2 NTT with P butterfly lanes
// =============================================================================
//
// Computes the cyclic number-theoretic transform of n = 2^cfg_logn words,
//
//     X_k = sum_j x_j * w^(j*k) mod q,      w a primitive n-th root of unity,
//
// for any n up to N = 2^LOGN, in four phases:
//
//   LOAD   n/P beats of P words; word i is stored at bitrev(i) (DIT order)
//   ISSUE  for each of the log2(n) stages, n/(2P) cycles issuing P butterflies
//   DRAIN  wait until the stage's last results are written back
//   OUT    n/P beats of P words, natural order
//
// Stage s combines blocks of size 2m (m = 2^s): butterfly t (0 <= t < n/2)
// reads i0 = (t / m) * 2m + (t mod m) and i1 = i0 + m, with twiddle
// w^((t mod m) * n / 2m), taken from a table of w^0 .. w^(N/2 - 1) that the
// host loads (tw_*) for each (q, n) along with q and mu.
//
// Every stage waits for the previous one to drain, so a transform costs
//
//     compute cycles = log2(n) * (n / (2P) + DRAIN)     (DRAIN = butterfly latency + 1)
//
// and n/P cycles each to load and unload, which are not overlapped with
// compute. The testbench measures these counts and model/cycles.py predicts
// them; RTL cycle counts are what calibrate the FHE simulator's NTT throughput.
//
// The data and twiddle memories are register arrays with 2P read ports; a
// silicon design would bank SRAMs instead (conflict-free for this access pattern
// with the usual XOR bank mapping). That changes area, not the cycle counts.
// =============================================================================

module ntt_core #(
    parameter int W    = 50,
    parameter int LOGN = 10,         // largest transform: N = 2^LOGN
    parameter int P    = 1           // butterfly lanes (power of two, P <= N/2)
) (
    input  logic             clk,
    input  logic             rst_n,
    // configuration (hold steady while busy)
    input  logic [W-1:0]     cfg_q,
    input  logic [W:0]       cfg_mu,
    input  logic [4:0]       cfg_logn,      // log2(n), from log2(2P) to LOGN
    // twiddle table: w^k at address k, k < N/2
    input  logic             tw_we,
    input  logic [LOGN-2:0]  tw_addr,
    input  logic [W-1:0]     tw_data,
    // input stream: P words per beat, lane 0 in the low bits
    input  logic             in_valid,
    output logic             in_ready,
    input  logic [P*W-1:0]   in_data,
    // output stream
    output logic             out_valid,
    input  logic             out_ready,
    output logic [P*W-1:0]   out_data,
    // status
    output logic             busy,
    output logic [31:0]      stat_compute_cycles   // ISSUE + DRAIN cycles of the last transform
);
    localparam int N    = 1 << LOGN;
    localparam int LOGP = $clog2(P);
    localparam int TAGW = 2 * LOGN;

    typedef enum logic [1:0] {S_LOAD, S_ISSUE, S_DRAIN, S_OUT} state_t;
    state_t state;

    logic [W-1:0]    mem [N];
    logic [W-1:0]    tw  [N/2];

    logic [LOGN-1:0] cnt;            // beat / issue counter
    logic [4:0]      stage;
    logic [7:0]      outstanding;    // butterfly groups in flight
    logic [31:0]     ccount;

    // beats per phase
    logic [LOGN:0]   n_words;
    logic [LOGN-1:0] last_beat, last_issue;
    assign n_words    = (LOGN+1)'(1) << cfg_logn;
    assign last_beat  = LOGN'((n_words >> LOGP) - (LOGN+1)'(1));
    assign last_issue = LOGN'((n_words >> (LOGP + 1)) - (LOGN+1)'(1));

    function automatic logic [LOGN-1:0] bitrev(input logic [LOGN-1:0] i, input logic [4:0] logn);
        logic [LOGN-1:0] r;
        for (int k = 0; k < LOGN; k++) r[k] = i[LOGN-1-k];
        return r >> (5'(LOGN) - logn);
    endfunction

    // ── butterfly lanes ────────────────────────────────────────────────
    logic            issue;
    logic            bf_v    [P];
    logic [W-1:0]    bf_x    [P];
    logic [W-1:0]    bf_y    [P];
    logic [TAGW-1:0] bf_tag  [P];
    logic [LOGN-1:0] ld_addr [P];

    assign issue = (state == S_ISSUE);

    for (genvar l = 0; l < P; l++) begin : g_lane
        logic [LOGN-1:0] t, m, j, i0, i1;
        logic [LOGN-2:0] twi;
        logic [1:0]      corr_unused;
        assign t   = LOGN'(cnt << LOGP) | LOGN'(l);
        assign m   = LOGN'(1) << stage;
        assign j   = t & (m - 1);
        assign i0  = ((t >> stage) << (stage + 1)) | j;
        assign i1  = i0 | m;
        assign twi = (LOGN-1)'(j << (cfg_logn - 1 - stage));

        ntt_butterfly #(.W(W), .TAGW(TAGW)) u_bf (
            .clk, .rst_n,
            .in_valid(issue),
            .a(mem[i0]), .b(mem[i1]), .w(tw[twi]),
            .in_tag({i0, i1}),
            .q(cfg_q), .mu(cfg_mu),
            .out_valid(bf_v[l]), .x(bf_x[l]), .y(bf_y[l]), .out_tag(bf_tag[l]),
            .corr(corr_unused)
        );

        assign ld_addr[l] = bitrev(LOGN'(cnt << LOGP) | LOGN'(l), cfg_logn);
        assign out_data[l*W +: W] = mem[LOGN'(cnt << LOGP) | LOGN'(l)];
    end

    // ── memories ───────────────────────────────────────────────────────
    always_ff @(posedge clk) begin
        if (tw_we)
            tw[tw_addr] <= tw_data;
        if (state == S_LOAD && in_valid)
            for (int l = 0; l < P; l++)
                mem[ld_addr[l]] <= in_data[l*W +: W];
        for (int l = 0; l < P; l++)
            if (bf_v[l]) begin
                mem[bf_tag[l][TAGW-1:LOGN]] <= bf_x[l];
                mem[bf_tag[l][LOGN-1:0]]    <= bf_y[l];
            end
    end

    // ── control ────────────────────────────────────────────────────────
    assign in_ready  = (state == S_LOAD);
    assign out_valid = (state == S_OUT);
    assign busy      = (state != S_LOAD) || (cnt != '0);

    always_ff @(posedge clk or negedge rst_n) begin
        if (!rst_n) begin
            state               <= S_LOAD;
            cnt                 <= '0;
            stage               <= '0;
            outstanding         <= '0;
            ccount              <= '0;
            stat_compute_cycles <= '0;
        end else begin
            outstanding <= outstanding + {7'd0, issue} - {7'd0, bf_v[0]};
            if (state == S_ISSUE || state == S_DRAIN)
                ccount <= ccount + 1;
            case (state)
                S_LOAD:
                    if (in_valid) begin
                        if (cnt == last_beat) begin
                            cnt    <= '0;
                            stage  <= '0;
                            ccount <= '0;
                            state  <= S_ISSUE;
                        end else
                            cnt <= cnt + 1;
                    end
                S_ISSUE:
                    if (cnt == last_issue) begin
                        cnt   <= '0;
                        state <= S_DRAIN;
                    end else
                        cnt <= cnt + 1;
                S_DRAIN:
                    if (outstanding == 0 && !bf_v[0]) begin
                        if (stage == cfg_logn - 1) begin
                            state               <= S_OUT;
                            stat_compute_cycles <= ccount + 1;
                        end else begin
                            stage <= stage + 1;
                            state <= S_ISSUE;
                        end
                    end
                S_OUT:
                    if (out_ready) begin
                        if (cnt == last_beat) begin
                            cnt   <= '0;
                            state <= S_LOAD;
                        end else
                            cnt <= cnt + 1;
                    end
                default: state <= S_LOAD;
            endcase
        end
    end

endmodule
