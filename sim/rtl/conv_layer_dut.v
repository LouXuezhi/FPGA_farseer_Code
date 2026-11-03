// conv_layer_dut: one conv layer with the geometry of LAYER, plain ports only.
// Synthesizable on its own, so the same testbench drives RTL and netlist.
`timescale 1ns / 1ps

module conv_layer_dut #(parameter LAYER = 0) (
    input  wire        clk,
    input  wire        rst,
    input  wire        src_valid,
    output wire        src_taken,
    output wire [15:0] src_rd_addr,
    input  wire [15:0] src_rd_data,
    output wire        dst_valid,
    input  wire [15:0] dst_rd_addr,
    output wire [15:0] dst_rd_data
);

    `include "qcnn_params.vh"

    localparam C_IN    = (LAYER == 0) ? 1  : (LAYER == 1) ? C0 : (LAYER == 2) ? C1 : C2;
    localparam C_OUT   = (LAYER == 0) ? C0 : (LAYER == 1) ? C1 : (LAYER == 2) ? C2 : C3;
    localparam PAR     = (LAYER == 0) ? PAR0 : (LAYER == 1) ? PAR1 : (LAYER == 2) ? PAR2 : PAR3;
    localparam H_IN    = (LAYER == 0) ? 56 : (LAYER == 1) ? 28 : (LAYER == 2) ? 14 : 7;
    localparam H_OUT   = (H_IN + 1) / 2;
    localparam IN_BITS = (LAYER == 0) ? 8 : ACT_BITS;
    localparam SHIFT   = (LAYER == 0) ? L0_SHIFT : (LAYER == 1) ? L1_SHIFT : (LAYER == 2) ? L2_SHIFT : L3_SHIFT;

    wire [$clog2(C_IN*H_IN*H_IN)-1:0]    layer_src_addr;
    wire [ACT_BITS-1:0]                  layer_dst_data;

    assign src_rd_addr = {{(16 - $clog2(C_IN*H_IN*H_IN)){1'b0}}, layer_src_addr};
    assign dst_rd_data = {{(16 - ACT_BITS){1'b0}}, layer_dst_data};

    qcnn_conv_layer #(.C_IN(C_IN), .C_OUT(C_OUT), .PAR(PAR), .H_IN(H_IN), .H_OUT(H_OUT),
                      .IN_BITS(IN_BITS), .ACT_BITS(ACT_BITS), .SHIFT(SHIFT),
                      .WEIGHT_PREFIX((LAYER == 0) ? "l0" : (LAYER == 1) ? "l1" : (LAYER == 2) ? "l2" : "l3"),
                      .BIAS_FILE((LAYER == 0) ? "l0_bias.mem" : (LAYER == 1) ? "l1_bias.mem" :
                                 (LAYER == 2) ? "l2_bias.mem" : "l3_bias.mem")) u_layer (
        .clk(clk), .rst(rst),
        .src_valid(src_valid), .src_taken(src_taken),
        .src_rd_addr(layer_src_addr), .src_rd_data(src_rd_data[IN_BITS-1:0]),
        .dst_valid(dst_valid), .dst_taken(1'b0),
        .dst_rd_addr(dst_rd_addr[$clog2(C_OUT*H_OUT*H_OUT)-1:0]), .dst_rd_data(layer_dst_data)
    );

endmodule
