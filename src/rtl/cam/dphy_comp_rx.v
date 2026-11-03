// Component-mode MIPI D-PHY receiver for UltraScale+ HP banks (bank 64 on FZ3B).
// Clock lane: IBUFDS_DPHY -> BUFGCE (bit clock) + BUFGCE_DIV /4 (byte clock).
// Data lanes: IBUFDS_DPHY -> IDELAYE3 (COUNT, software tap) -> ISERDESE3 1:8.
// Each lane has its own LP state machine (from the IBUFDS_DPHY LP receivers),
// controls its HS termination, and searches for the HS sync byte in both
// polarities, so a lane whose P/N are swapped on the board still works.
`timescale 1ns/1ps
module dphy_comp_rx #(parameter NL = 2) (
    input  wire          clk_rxp, clk_rxn,
    input  wire [NL-1:0] data_rxp, data_rxn,
    input  wire          rst,            // async, from register domain
    input  wire [8:0]    tap0, tap1,     // IDELAY taps (quasi-static)
    input  wire          tap_load_tgl,   // toggle to load taps
    input  wire          term_force,     // 1: keep data termination always on
    output wire          byteclk,
    output wire          brst,
    output wire [7:0]    d0, d1,
    output wire          v0, v1, a0, a1,
    output wire [1:0]    lp0, lp1,       // synchronized LP state {P,N}
    output wire [3:0]    info0, info1,   // {sync_ok_pulse, nosync_pulse, polarity, bitrev}
    output wire          clk_lp_p, clk_lp_n
);
    // ---------------- clock lane
    wire clk_hs, clk_lpp, clk_lpn;
    IBUFDS_DPHY #(.DIFF_TERM("TRUE"), .IOSTANDARD("MIPI_DPHY_DCI")) ibc (
        .I(clk_rxp), .IB(clk_rxn), .HSRX_DISABLE(1'b0), .LPRX_DISABLE(1'b0),
        .HSRX_O(clk_hs), .LPRX_O_P(clk_lpp), .LPRX_O_N(clk_lpn));
    assign clk_lp_p = clk_lpp; assign clk_lp_n = clk_lpn;
    wire bitclk;
    BUFGCE #(.CE_TYPE("ASYNC")) bgbit (.I(clk_hs), .CE(1'b1), .O(bitclk));
    BUFGCE_DIV #(.BUFGCE_DIVIDE(4)) bgdiv (.I(clk_hs), .CE(1'b1), .CLR(rst), .O(byteclk));

    reg [3:0] rs;
    always @(posedge byteclk or posedge rst) if (rst) rs <= 4'hf; else rs <= {rs[2:0], 1'b0};
    assign brst = rs[3];

    reg [2:0] tl_s; always @(posedge byteclk) tl_s <= {tl_s[1:0], tap_load_tgl};
    wire tap_load = tl_s[2] ^ tl_s[1];

    wire [7:0] q   [0:1];
    wire [1:0] lps [0:1];
    wire [1:0] term_dis;
    genvar i;
    generate for (i = 0; i < NL; i = i + 1) begin : ln
        wire hs, lpp, lpn, dly;
        IBUFDS_DPHY #(.DIFF_TERM("TRUE"), .IOSTANDARD("MIPI_DPHY_DCI")) ibd (
            .I(data_rxp[i]), .IB(data_rxn[i]), .HSRX_DISABLE(term_dis[i]), .LPRX_DISABLE(1'b0),
            .HSRX_O(hs), .LPRX_O_P(lpp), .LPRX_O_N(lpn));
        IDELAYE3 #(.DELAY_SRC("IDATAIN"), .DELAY_TYPE("VAR_LOAD"), .DELAY_FORMAT("COUNT"), .DELAY_VALUE(0),
                   .REFCLK_FREQUENCY(300.0), .UPDATE_MODE("ASYNC"), .SIM_DEVICE("ULTRASCALE_PLUS")) idl (
            .IDATAIN(hs), .DATAIN(1'b0), .DATAOUT(dly), .CLK(byteclk), .CE(1'b0), .INC(1'b0),
            .LOAD(tap_load), .CNTVALUEIN(i == 0 ? tap0 : tap1), .CNTVALUEOUT(), .EN_VTC(1'b0), .RST(brst),
            .CASC_IN(1'b0), .CASC_RETURN(1'b0), .CASC_OUT());
        ISERDESE3 #(.DATA_WIDTH(8), .FIFO_ENABLE("FALSE"), .FIFO_SYNC_MODE("FALSE"), .IS_CLK_B_INVERTED(1'b1),
                    .IS_CLK_INVERTED(1'b0), .IS_RST_INVERTED(1'b0), .SIM_DEVICE("ULTRASCALE_PLUS")) ise (
            .D(dly), .CLK(bitclk), .CLK_B(bitclk), .CLKDIV(byteclk), .RST(brst),
            .FIFO_RD_CLK(1'b0), .FIFO_RD_EN(1'b0), .FIFO_EMPTY(), .INTERNAL_DIVCLK(), .Q(q[i]));
        reg [1:0] s1, s2;
        always @(posedge byteclk) begin s1 <= {lpp, lpn}; s2 <= s1; end
        assign lps[i] = s2;
    end endgenerate

    wire td0, td1;
    dphy_lane_align al0 (.clk(byteclk), .rst(brst), .q(q[0]), .lp(lps[0]), .term_force(term_force),
        .term_dis(td0), .d(d0), .v(v0), .a(a0), .info(info0));
    dphy_lane_align al1 (.clk(byteclk), .rst(brst), .q(q[1]), .lp(lps[1]), .term_force(term_force),
        .term_dis(td1), .d(d1), .v(v1), .a(a1), .info(info1));
    assign term_dis = {td1, td0};
    assign lp0 = lps[0]; assign lp1 = lps[1];
endmodule

// LP state machine + HS sync search for one lane.
module dphy_lane_align (
    input  wire       clk, rst,
    input  wire [7:0] q,
    input  wire [1:0] lp,          // {P, N}
    input  wire       term_force,
    output reg        term_dis,
    output reg  [7:0] d,
    output reg        v,
    output reg        a,
    output wire [3:0] info
);
    // two candidate bit orders of the deserializer word; index 0 = earliest bit
    wire [7:0] qa = q;
    wire [7:0] qb = {q[0], q[1], q[2], q[3], q[4], q[5], q[6], q[7]};
    reg  [7:0] pa1, pa2, pb1, pb2;
    wire [23:0] wa = {qa, pa1, pa2};    // pa2 earliest
    wire [23:0] wb = {qb, pb1, pb2};
    localparam IDLE = 2'd0, ARM = 2'd1, HS = 2'd2;
    reg [1:0] st;
    reg [2:0] off; reg pol, rev;
    reg [7:0] settle;
    reg sync_ok, nosync, found;
    assign info = {sync_ok, nosync, pol, rev};

    // search: sync byte at bits [8+k +: 8], preceded by 8 bits of HS-zero
    reg hit; reg [2:0] hk; reg hp, hr;
    integer k;
    always @(*) begin
        hit = 1'b0; hk = 0; hp = 0; hr = 0;
        for (k = 7; k >= 0; k = k - 1) begin
            if (wa[8 + k +: 8] == 8'hB8 && wa[k +: 8] == 8'h00) begin hit = 1; hk = k; hp = 0; hr = 0; end
            if (wa[8 + k +: 8] == 8'h47 && wa[k +: 8] == 8'hFF) begin hit = 1; hk = k; hp = 1; hr = 0; end
            if (wb[8 + k +: 8] == 8'hB8 && wb[k +: 8] == 8'h00) begin hit = 1; hk = k; hp = 0; hr = 1; end
            if (wb[8 + k +: 8] == 8'h47 && wb[k +: 8] == 8'hFF) begin hit = 1; hk = k; hp = 1; hr = 1; end
        end
    end
    wire [23:0] w = rev ? wb : wa;
    wire [7:0]  byte_now = w[8 + off +: 8] ^ {8{pol}};

    always @(posedge clk) begin
        pa1 <= qa; pa2 <= pa1; pb1 <= qb; pb2 <= pb1;
        v <= 1'b0; sync_ok <= 1'b0; nosync <= 1'b0;
        if (rst) begin
            st <= IDLE; term_dis <= 1'b1; a <= 1'b0; found <= 1'b0;
        end else case (st)
            IDLE: begin
                a <= 1'b0; found <= 1'b0;
                term_dis <= ~term_force;
                if (lp == 2'b00) begin st <= ARM; settle <= 0; term_dis <= 1'b0; end
            end
            ARM: begin           // termination on, wait a few byte clocks, then hunt for sync
                term_dis <= 1'b0;
                if (lp == 2'b11) st <= IDLE;
                else if (settle == 8'd3) begin st <= HS; a <= 1'b1; end
                else settle <= settle + 1'b1;
            end
            HS: begin
                term_dis <= 1'b0;
                if (lp == 2'b11) begin
                    st <= IDLE; a <= 1'b0; term_dis <= ~term_force;
                    if (!found) nosync <= 1'b1;
                end else if (!found) begin
                    if (hit) begin found <= 1'b1; off <= hk; pol <= hp; rev <= hr; sync_ok <= 1'b1; end
                end else begin
                    d <= byte_now; v <= 1'b1;
                end
            end
            default: st <= IDLE;
        endcase
    end
endmodule
