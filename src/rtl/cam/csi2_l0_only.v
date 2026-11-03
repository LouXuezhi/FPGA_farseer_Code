// Lane-0-only CSI-2 parser for diagnosing a 2-lane link with a dead lane 1.
// In 2-lane mode lane 0 carries bytes 0,2,4,... of every packet: DI, WC_MSB, payload
// even bytes, CRC LSB. The header ECC and WC LSB are on lane 1, so they cannot be checked.
// The per-lane payload length (WC/2) comes from a register. Output beats pair two
// consecutive lane-0 bytes, i.e. for RAW8 they are pixels 2k and 2k+2.
`timescale 1ns/1ps
module csi2_l0_only (
    input  wire        clk, rst,
    input  wire [15:0] lane_bytes,   // expected payload bytes per line on lane 0 (= WC/2), even
    input  wire [7:0]  d, input wire v, input wire a,
    output reg  [15:0] pl_data, output reg pl_valid, output reg pl_last, output reg pl_first,
    output reg  [5:0]  pl_dt,
    output reg         fs_pulse, fe_pulse, pkt_pulse, bad_pulse, short_pulse
);
    localparam S_DI = 2'd0, S_WCM = 2'd1, S_DATA = 2'd2, S_SKIP = 2'd3;
    reg [1:0] st; reg [7:0] di, lo; reg half; reg [15:0] left; reg first;
    reg a_d; always @(posedge clk) a_d <= a;
    always @(posedge clk) begin
        pl_valid <= 0; pl_last <= 0; pl_first <= 0; fs_pulse <= 0; fe_pulse <= 0;
        pkt_pulse <= 0; bad_pulse <= 0; short_pulse <= 0;
        if (rst || (!a && !a_d)) st <= S_DI;
        else if (v) case (st)
            S_DI:  begin di <= d; st <= S_WCM; end
            S_WCM: begin
                if (di[7:6] == 2'b00 && di[5:0] <= 6'h07) begin
                    short_pulse <= 1; pkt_pulse <= 1;
                    if (di[5:0] == 6'h00) fs_pulse <= 1;
                    if (di[5:0] == 6'h01) fe_pulse <= 1;
                    st <= S_SKIP;
                end else if (di == 8'h2a || di == 8'h2b) begin
                    pkt_pulse <= 1; pl_dt <= di[5:0]; left <= lane_bytes; half <= 0; first <= 1; st <= S_DATA;
                end else begin bad_pulse <= 1; st <= S_SKIP; end
            end
            S_DATA: begin
                half <= ~half;
                if (!half) lo <= d;
                else begin
                    pl_data <= {d, lo}; pl_valid <= 1; pl_first <= first; first <= 0;
                    if (left <= 16'd2) begin pl_last <= 1; st <= S_SKIP; end
                end
                left <= left - 1'b1;
            end
            default: ;
        endcase
    end
endmodule
