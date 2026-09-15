// Block-RAM primitives shared by all stages.
// keep_hierarchy stops synthesis from pulling the read register into the
// downstream DSP, which would turn the memory into LUT logic.
`timescale 1ns / 1ps

// Synchronous ROM, $readmemh initialised.
// Optional slicing for parallel conv: holds blocks (g*STRIDE + OFFSET) of
// length BLOCK_LEN from INIT_FILE, i.e. the weights of one multiplier lane.
(* keep_hierarchy = "yes" *)
module qcnn_sync_rom #(
    parameter DEPTH      = 72,
    parameter WIDTH      = 8,
    parameter INIT_FILE  = "l0_weight.mem",
    parameter FULL_DEPTH = DEPTH,          // entries in INIT_FILE
    parameter BLOCK_LEN  = 1,
    parameter STRIDE     = 1,
    parameter OFFSET     = 0
) (
    input  wire                     clk,
    input  wire [$clog2(DEPTH)-1:0] rd_addr,
    output reg  [WIDTH-1:0]         rd_data
);
    (* rom_style = "block" *) reg [WIDTH-1:0] mem [0:DEPTH-1];
    reg [WIDTH-1:0] file_mem [0:FULL_DEPTH-1];
    integer i;
    initial begin
        $readmemh(INIT_FILE, file_mem);
        for (i = 0; i < DEPTH; i = i + 1)
            mem[i] = file_mem[((i / BLOCK_LEN) * STRIDE + OFFSET) * BLOCK_LEN + i % BLOCK_LEN];
    end
    always @(posedge clk) rd_data <= mem[rd_addr];
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
