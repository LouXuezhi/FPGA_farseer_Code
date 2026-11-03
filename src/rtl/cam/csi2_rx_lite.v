// Minimal MIPI CSI-2 packet parser for 2 data lanes on the Xilinx D-PHY RX PPI.
// Runs in the D-PHY byte clock domain (rxbyteclkhs). Each HS burst is parsed
// from the start; after a long packet's CRC, or on any header that fails the
// checks, the rest of the burst is skipped until both lanes leave HS (EoT), so
// HS-trail bytes are never parsed as headers.
`timescale 1ns/1ps
module csi2_rx_lite (
    input  wire        clk,
    input  wire        rst,
    input  wire        ecc_check_en,
    input  wire        one_lane,     // 1: all bytes arrive on lane 0
    input  wire [7:0]  l0_data,
    input  wire        l0_valid,
    input  wire        l0_active,
    input  wire [7:0]  l1_data,
    input  wire        l1_valid,
    input  wire        l1_active,
    output reg  [15:0] pl_data,      // {lane1 byte, lane0 byte} = {byte 2k+1, byte 2k}
    output reg         pl_valid,
    output reg         pl_last,      // last beat of a long packet
    output reg         pl_first,     // first beat of a long packet
    output reg  [5:0]  pl_dt,
    output reg  [15:0] pl_wc,        // word count of the current long packet
    output reg         fs_pulse,
    output reg         fe_pulse,
    output reg         pkt_pulse,    // any header accepted
    output reg         ecc_pulse,    // header rejected by ECC
    output reg         bad_pulse,    // header rejected by VC/DT filter
    output reg         crc_ok_pulse, // long packet payload CRC-16 matched
    output reg         crc_bad_pulse
);
    // per-lane FIFOs absorb a few byte clocks of lane-to-lane skew
    reg [7:0] f0 [0:7];
    reg [7:0] f1 [0:7];
    reg [2:0] w0, w1, r0, r1;
    wire e0 = (w0 == r0);
    wire e1 = (w1 == r1);
    reg  a_d1, a_d2;
    wire burst = l0_active | (l1_active & ~one_lane);
    always @(posedge clk) begin a_d1 <= burst; a_d2 <= a_d1; end
    wire idle = !burst && !a_d1 && !a_d2;   // a few cycles after EoT

    reg odd;   // one-lane mode: even bytes -> f0, odd bytes -> f1
    always @(posedge clk) begin
        if (rst || idle) begin
            w0 <= 0; w1 <= 0; odd <= 1'b0;
        end else if (one_lane) begin
            if (l0_valid) begin
                if (!odd) begin f0[w0] <= l0_data; w0 <= w0 + 1'b1; end
                else      begin f1[w1] <= l0_data; w1 <= w1 + 1'b1; end
                odd <= ~odd;
            end
        end else begin
            if (l0_valid) begin f0[w0] <= l0_data; w0 <= w0 + 1'b1; end
            if (l1_valid) begin f1[w1] <= l1_data; w1 <= w1 + 1'b1; end
        end
    end
    wire pop = !e0 && !e1;
    wire [7:0] b0 = f0[r0];
    wire [7:0] b1 = f1[r1];
    always @(posedge clk) begin
        if (rst || idle) begin r0 <= 0; r1 <= 0; end
        else if (pop) begin r0 <= r0 + 1'b1; r1 <= r1 + 1'b1; end
    end

    function [5:0] ecc6;
        input [23:0] d;
        begin
            ecc6[0] = d[0]^d[1]^d[2]^d[4]^d[5]^d[7]^d[10]^d[11]^d[13]^d[16]^d[20]^d[21]^d[22]^d[23];
            ecc6[1] = d[0]^d[1]^d[3]^d[4]^d[6]^d[8]^d[10]^d[12]^d[14]^d[17]^d[20]^d[21]^d[22]^d[23];
            ecc6[2] = d[0]^d[2]^d[3]^d[5]^d[6]^d[9]^d[11]^d[12]^d[15]^d[18]^d[20]^d[21]^d[22];
            ecc6[3] = d[1]^d[2]^d[3]^d[7]^d[8]^d[9]^d[13]^d[14]^d[15]^d[19]^d[20]^d[21]^d[23];
            ecc6[4] = d[4]^d[5]^d[6]^d[7]^d[8]^d[9]^d[16]^d[17]^d[18]^d[19]^d[20]^d[22]^d[23];
            ecc6[5] = d[10]^d[11]^d[12]^d[13]^d[14]^d[15]^d[16]^d[17]^d[18]^d[19]^d[21]^d[22]^d[23];
        end
    endfunction

    localparam S_HDR1 = 3'd0, S_HDR2 = 3'd1, S_DATA = 3'd2, S_CRC = 3'd3, S_SKIP = 3'd4;
    reg [2:0]  st;
    reg [7:0]  h0, h1;
    reg [15:0] left;
    reg        first;
    // CSI-2 payload CRC: CRC-16-CCITT, poly x^16+x^12+x^5+1, reflected (0x8408), init 0xFFFF,
    // no final xor; transmitted LSB first after the payload.
    reg [15:0] crc;
    function [15:0] crc8; input [15:0] c; input [7:0] d; integer i; reg [15:0] r; begin
        r = c;
        for (i = 0; i < 8; i = i + 1) r = (r[0] ^ d[i]) ? ((r >> 1) ^ 16'h8408) : (r >> 1);
        crc8 = r; end endfunction
    wire [23:0] hdr = {b0, h1, h0};
    wire ecc_ok = !ecc_check_en || (ecc6(hdr) == b1[5:0] && b1[7:6] == 2'b00);
    wire vc_ok  = (h0[7:6] == 2'b00);
    wire is_short = (h0[5:0] <= 6'h07);
    wire long_ok  = (h0[5:0] == 6'h2a || h0[5:0] == 6'h2b) && hdr[23:8] != 16'd0;

    always @(posedge clk) begin
        pl_valid <= 1'b0; pl_last <= 1'b0; pl_first <= 1'b0;
        fs_pulse <= 1'b0; fe_pulse <= 1'b0;
        pkt_pulse <= 1'b0; ecc_pulse <= 1'b0; bad_pulse <= 1'b0; crc_ok_pulse <= 1'b0; crc_bad_pulse <= 1'b0;
        if (rst || idle) begin
            st <= S_HDR1;
        end else if (pop) begin
            case (st)
            S_HDR1: begin h0 <= b0; h1 <= b1; st <= S_HDR2; end
            S_HDR2: begin
                if (!ecc_ok) begin
                    ecc_pulse <= 1'b1; st <= S_SKIP;
                end else if (!vc_ok || !(is_short || long_ok)) begin
                    bad_pulse <= 1'b1; st <= S_SKIP;
                end else if (is_short) begin
                    pkt_pulse <= 1'b1;
                    if (h0[5:0] == 6'h00) fs_pulse <= 1'b1;
                    if (h0[5:0] == 6'h01) fe_pulse <= 1'b1;
                    st <= S_SKIP;           // one packet per HS burst; skip EoT trail
                end else begin
                    pkt_pulse <= 1'b1;
                    pl_dt <= h0[5:0];
                    pl_wc <= hdr[23:8];
                    left  <= hdr[23:8];
                    first <= 1'b1;
                    crc   <= 16'hFFFF;
                    st <= S_DATA;
                end
            end
            S_DATA: begin
                pl_data  <= {b1, b0};
                pl_valid <= 1'b1;
                pl_first <= first;
                first    <= 1'b0;
                left     <= left - 16'd2;
                crc      <= crc8(crc8(crc, b0), b1);
                if (left <= 16'd2) begin pl_last <= 1'b1; st <= S_CRC; end
            end
            S_CRC: begin                    // 2 CRC bytes (LSB on lane 0), then EoT trail
                if ({b1, b0} == crc) crc_ok_pulse <= 1'b1; else crc_bad_pulse <= 1'b1;
                st <= S_SKIP;
            end
            default: ;                      // S_SKIP until idle
            endcase
        end
    end
endmodule
