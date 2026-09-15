// qcnn_core: 56x56 uint8 frame stream -> int16 temperature code stream.
//
//   loader -> conv0 -> conv1 -> conv2 -> conv3 -> head
//   56x56x1  28x28xC0 14x14xC1 7x7xC2   4x4xC3   GAP, FC C3->HIDDEN->1
//
// Channel counts, activation width and shifts come from qcnn_params.vh.
// Each stage owns its output RAM; frames advance stage by stage, so several
// frames are in flight. T[degC] = 25 + 175 * result / 4096.
`timescale 1ns / 1ps

module qcnn_core (
    input  wire               clk,
    input  wire               rst,
    input  wire [7:0]         s_axis_pixel_tdata,
    input  wire               s_axis_pixel_tvalid,
    output wire               s_axis_pixel_tready,
    output wire signed [15:0] m_axis_result_tdata,
    output wire               m_axis_result_tvalid,
    input  wire               m_axis_result_tready
);

    `include "qcnn_params.vh"

    // lanes per conv layer (hardware choice, not model): PAR <= C_IN*9, divides C_OUT
    localparam PAR0 = 8, PAR1 = 32, PAR2 = 32, PAR3 = 32;

    // stage i -> stage i+1: frame handshake + read port
    wire [4:0] valid, taken;
    wire [$clog2(56*56)-1:0]    rd_addr_0;  wire [7:0]          rd_data_0;
    wire [$clog2(C0*28*28)-1:0] rd_addr_1;  wire [ACT_BITS-1:0] rd_data_1;
    wire [$clog2(C1*14*14)-1:0] rd_addr_2;  wire [ACT_BITS-1:0] rd_data_2;
    wire [$clog2(C2*7*7)-1:0]   rd_addr_3;  wire [ACT_BITS-1:0] rd_data_3;
    wire [$clog2(C3*4*4)-1:0]   rd_addr_4;  wire [ACT_BITS-1:0] rd_data_4;

    qcnn_frame_loader #(.N_PIXEL(56 * 56)) u_loader (
        .clk(clk), .rst(rst),
        .s_axis_pixel_tdata(s_axis_pixel_tdata), .s_axis_pixel_tvalid(s_axis_pixel_tvalid),
        .s_axis_pixel_tready(s_axis_pixel_tready),
        .dst_valid(valid[0]), .dst_taken(taken[0]), .dst_rd_addr(rd_addr_0), .dst_rd_data(rd_data_0)
    );

    qcnn_conv_layer #(.C_IN(1), .C_OUT(C0), .PAR(PAR0), .H_IN(56), .H_OUT(28), .IN_BITS(8), .ACT_BITS(ACT_BITS),
                      .SHIFT(L0_SHIFT), .WEIGHT_FILE("l0_weight.mem"), .BIAS_FILE("l0_bias.mem")) u_conv0 (
        .clk(clk), .rst(rst),
        .src_valid(valid[0]), .src_taken(taken[0]), .src_rd_addr(rd_addr_0), .src_rd_data(rd_data_0),
        .dst_valid(valid[1]), .dst_taken(taken[1]), .dst_rd_addr(rd_addr_1), .dst_rd_data(rd_data_1)
    );

    qcnn_conv_layer #(.C_IN(C0), .C_OUT(C1), .PAR(PAR1), .H_IN(28), .H_OUT(14), .IN_BITS(ACT_BITS), .ACT_BITS(ACT_BITS),
                      .SHIFT(L1_SHIFT), .WEIGHT_FILE("l1_weight.mem"), .BIAS_FILE("l1_bias.mem")) u_conv1 (
        .clk(clk), .rst(rst),
        .src_valid(valid[1]), .src_taken(taken[1]), .src_rd_addr(rd_addr_1), .src_rd_data(rd_data_1),
        .dst_valid(valid[2]), .dst_taken(taken[2]), .dst_rd_addr(rd_addr_2), .dst_rd_data(rd_data_2)
    );

    qcnn_conv_layer #(.C_IN(C1), .C_OUT(C2), .PAR(PAR2), .H_IN(14), .H_OUT(7), .IN_BITS(ACT_BITS), .ACT_BITS(ACT_BITS),
                      .SHIFT(L2_SHIFT), .WEIGHT_FILE("l2_weight.mem"), .BIAS_FILE("l2_bias.mem")) u_conv2 (
        .clk(clk), .rst(rst),
        .src_valid(valid[2]), .src_taken(taken[2]), .src_rd_addr(rd_addr_2), .src_rd_data(rd_data_2),
        .dst_valid(valid[3]), .dst_taken(taken[3]), .dst_rd_addr(rd_addr_3), .dst_rd_data(rd_data_3)
    );

    qcnn_conv_layer #(.C_IN(C2), .C_OUT(C3), .PAR(PAR3), .H_IN(7), .H_OUT(4), .IN_BITS(ACT_BITS), .ACT_BITS(ACT_BITS),
                      .SHIFT(L3_SHIFT), .WEIGHT_FILE("l3_weight.mem"), .BIAS_FILE("l3_bias.mem")) u_conv3 (
        .clk(clk), .rst(rst),
        .src_valid(valid[3]), .src_taken(taken[3]), .src_rd_addr(rd_addr_3), .src_rd_data(rd_data_3),
        .dst_valid(valid[4]), .dst_taken(taken[4]), .dst_rd_addr(rd_addr_4), .dst_rd_data(rd_data_4)
    );

    qcnn_head #(.C_IN(C3), .HIDDEN(HIDDEN), .ACT_BITS(ACT_BITS),
                .GAP_SHIFT(L4_SHIFT), .FC1_SHIFT(L5_SHIFT), .FC2_SHIFT(L6_SHIFT)) u_head (
        .clk(clk), .rst(rst),
        .src_valid(valid[4]), .src_taken(taken[4]), .src_rd_addr(rd_addr_4), .src_rd_data(rd_data_4),
        .m_axis_result_tdata(m_axis_result_tdata), .m_axis_result_tvalid(m_axis_result_tvalid),
        .m_axis_result_tready(m_axis_result_tready)
    );

endmodule
