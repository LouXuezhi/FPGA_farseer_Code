// Block-RAM primitives shared by all stages.
// keep_hierarchy stops synthesis from pulling the read register into the
// downstream DSP, which would turn the memory into LUT logic.
`timescale 1ns / 1ps

// Synchronous ROM, $readmemh initialised; small ones in LUTs, large in BRAM.
(* keep_hierarchy = "yes" *)
module qcnn_sync_rom #(
    parameter DEPTH     = 72,
    parameter WIDTH     = 8,
    parameter INIT_FILE = "l0_weight_lane00.mem"
) (
    input  wire                     clk,
    input  wire [$clog2(DEPTH)-1:0] rd_addr,
    output reg  [WIDTH-1:0]         rd_data
);
    generate if (DEPTH * WIDTH >= 8192) begin : g_block
        (* rom_style = "block" *) reg [WIDTH-1:0] mem [0:DEPTH-1];
        initial $readmemh(INIT_FILE, mem);
        always @(posedge clk) rd_data <= mem[rd_addr];
    end else begin : g_lut
        (* rom_style = "distributed" *) reg [WIDTH-1:0] mem [0:DEPTH-1];
        initial $readmemh(INIT_FILE, mem);
        always @(posedge clk) rd_data <= mem[rd_addr];
    end endgenerate
endmodule

// Simple dual-port RAM: one write port, one registered read port.
(* keep_hierarchy = "yes" *)
module qcnn_sdp_ram #(
    parameter DEPTH = 3136,
    parameter WIDTH = 8
) (
    input  wire                     clk,
    input  wire                     wr_en,
    input  wire [$clog2(DEPTH)-1:0] wr_addr,
    input  wire [WIDTH-1:0]         wr_data,
    input  wire [$clog2(DEPTH)-1:0] rd_addr,
    output reg  [WIDTH-1:0]         rd_data
);
    (* ram_style = "block" *) reg [WIDTH-1:0] mem [0:DEPTH-1];
    always @(posedge clk) begin
        if (wr_en) mem[wr_addr] <= wr_data;
        rd_data <= mem[rd_addr];
    end
endmodule
