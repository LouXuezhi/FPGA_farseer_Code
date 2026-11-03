// AXI4-Lite register block: 0x00 ctrl (RW), 0x04 version, 0x40-0x7C status 0-15, 0x80/0x84 status 16-17
`timescale 1ns/1ps
module cam_regs (
    input  wire        aclk, aresetn,
    input  wire [7:0]  awaddr, input wire awvalid, output reg awready,
    input  wire [31:0] wdata, input wire [3:0] wstrb, input wire wvalid, output reg wready,
    output wire [1:0]  bresp, output reg bvalid, input wire bready,
    input  wire [7:0]  araddr, input wire arvalid, output reg arready,
    output reg  [31:0] rdata, output wire [1:0] rresp, output reg rvalid, input wire rready,
    output reg  [31:0] ctrl,
    output reg  [31:0] ctrl2,
    input  wire [31:0] s0, s1, s2, s3, s4, s5, s6, s7, s8, s9, s10, s11, s12, s13, s14, s15, s16, s17, s18, s19, s20, s21, s22, s23, s24, s25
);
    localparam CTRL_RESET = 32'h0000_003E;   // dphy out of reset, cam RST#/PWDN# high, MCLK on, ECC check on, stream on
    assign bresp = 2'b00;
    assign rresp = 2'b00;
    reg [7:0] aw; reg aw_ok, w_ok; reg [31:0] wd;
    always @(posedge aclk) begin
        if (!aresetn) begin
            awready <= 0; wready <= 0; bvalid <= 0; aw_ok <= 0; w_ok <= 0; ctrl <= CTRL_RESET; ctrl2 <= 32'h0000_0000;
        end else begin
            awready <= !aw_ok && awvalid && !awready;
            wready  <= !w_ok && wvalid && !wready;
            if (awvalid && awready) begin aw <= awaddr; aw_ok <= 1; end
            if (wvalid && wready)   begin wd <= wdata;  w_ok  <= 1; end
            if (aw_ok && w_ok && !bvalid) begin
                if (aw[7:2] == 6'd0) ctrl <= wd;
                if (aw[7:2] == 6'd2) ctrl2 <= wd;
                bvalid <= 1; aw_ok <= 0; w_ok <= 0;
            end
            if (bvalid && bready) bvalid <= 0;
        end
    end
    always @(posedge aclk) begin
        if (!aresetn) begin arready <= 0; rvalid <= 0; end
        else begin
            arready <= arvalid && !arready && !rvalid;
            if (arvalid && arready) begin
                rvalid <= 1;
                case (araddr[7:2])
                    6'h00: rdata <= ctrl;
                    6'h01: rdata <= 32'hCA92_8103;
                    6'h02: rdata <= ctrl2;
                    6'h10: rdata <= s0;  6'h11: rdata <= s1;  6'h12: rdata <= s2;  6'h13: rdata <= s3;
                    6'h14: rdata <= s4;  6'h15: rdata <= s5;  6'h16: rdata <= s6;  6'h17: rdata <= s7;
                    6'h18: rdata <= s8;  6'h19: rdata <= s9;  6'h1a: rdata <= s10; 6'h1b: rdata <= s11;
                    6'h1c: rdata <= s12; 6'h1d: rdata <= s13; 6'h1e: rdata <= s14; 6'h1f: rdata <= s15;
                    6'h20: rdata <= s16; 6'h21: rdata <= s17; 6'h22: rdata <= s18; 6'h23: rdata <= s19; 6'h24: rdata <= s20; 6'h25: rdata <= s21; 6'h26: rdata <= s22; 6'h27: rdata <= s23; 6'h28: rdata <= s24; 6'h29: rdata <= s25;
                    default: rdata <= 32'hDEAD_BEEF;
                endcase
            end
            if (rvalid && rready) rvalid <= 0;
        end
    end
endmodule
