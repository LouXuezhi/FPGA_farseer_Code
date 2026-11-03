// qcnn_core: IN_CH x IN_H x IN_H uint8 frame stream -> N_OUT int16 codes per frame.
//
//   loader -> conv0 -> conv1 -> conv2 -> conv3 -> head
//   IN_H^2xIN_CH  H1^2xC0  H2^2xC1  H3^2xC2  4x4xC3   GAP, FC C3->HIDDEN->N_OUT
//
// Shapes, lanes (PARi), activation width and shifts come from qcnn_params.vh.
// Pixels arrive plane by plane ([c][y][x]); for the dT model plane 0 is the
// reference frame, plane 1 the current frame. Results leave in order, tlast on
// the last of the N_OUT codes of a frame. Each stage owns its output RAM;
// frames advance stage by stage, so several frames are in flight.
// The node service scales the codes: dT = code * dt_lsb (speckle_node.py --dt-lsb).
`timescale 1ns / 1ps

module qcnn_core (
    input  wire               clk,
    input  wire               rst,
    input  wire [7:0]         s_axis_pixel_tdata,
    input  wire               s_axis_pixel_tvalid,
    output wire               s_axis_pixel_tready,
    output wire signed [15:0] m_axis_result_tdata,
    output wire               m_axis_result_tvalid,
    input  wire               m_axis_result_tready,
    output wire               m_axis_result_tlast     // last code of a frame
);

    `include "qcnn_params.vh"
    // stride-2 pad-1 convs: H -> ceil(H/2); the head expects a 4x4 map (IN_H 50..64)
    localparam H1 = (IN_H + 1) / 2, H2 = (H1 + 1) / 2, H3 = (H2 + 1) / 2, H4 = (H3 + 1) / 2;
    localparam N_PIXEL = IN_CH * IN_H * IN_H;

    // stage i -> stage i+1: frame handshake + read port
    wire [4:0] valid, taken;
    wire [$clog2(N_PIXEL)-1:0]    rd_addr_0;  wire [7:0]          rd_data_0;
    wire [$clog2(C0*H1*H1)-1:0] rd_addr_1;  wire [ACT_BITS-1:0] rd_data_1;
    wire [$clog2(C1*H2*H2)-1:0] rd_addr_2;  wire [ACT_BITS-1:0] rd_data_2;
    wire [$clog2(C2*H3*H3)-1:0]   rd_addr_3;  wire [ACT_BITS-1:0] rd_data_3;
    wire [$clog2(C3*4*4)-1:0]   rd_addr_4;  wire [ACT_BITS-1:0] rd_data_4;

    qcnn_frame_loader #(.N_PIXEL(N_PIXEL)) u_loader (
        .clk(clk), .rst(rst),
        .s_axis_pixel_tdata(s_axis_pixel_tdata), .s_axis_pixel_tvalid(s_axis_pixel_tvalid),
        .s_axis_pixel_tready(s_axis_pixel_tready),
        .dst_valid(valid[0]), .dst_taken(taken[0]), .dst_rd_addr(rd_addr_0), .dst_rd_data(rd_data_0)
    );

    qcnn_conv_layer #(.C_IN(IN_CH), .C_OUT(C0), .PAR(PAR0), .H_IN(IN_H), .H_OUT(H1), .IN_BITS(8), .ACT_BITS(ACT_BITS),
                      .SHIFT(L0_SHIFT), .WEIGHT_PREFIX("l0"), .BIAS_FILE("l0_bias.mem")) u_conv0 (
        .clk(clk), .rst(rst),
        .src_valid(valid[0]), .src_taken(taken[0]), .src_rd_addr(rd_addr_0), .src_rd_data(rd_data_0),
        .dst_valid(valid[1]), .dst_taken(taken[1]), .dst_rd_addr(rd_addr_1), .dst_rd_data(rd_data_1)
    );

    qcnn_conv_layer #(.C_IN(C0), .C_OUT(C1), .PAR(PAR1), .H_IN(H1), .H_OUT(H2), .IN_BITS(ACT_BITS), .ACT_BITS(ACT_BITS),
                      .SHIFT(L1_SHIFT), .WEIGHT_PREFIX("l1"), .BIAS_FILE("l1_bias.mem")) u_conv1 (
        .clk(clk), .rst(rst),
        .src_valid(valid[1]), .src_taken(taken[1]), .src_rd_addr(rd_addr_1), .src_rd_data(rd_data_1),
        .dst_valid(valid[2]), .dst_taken(taken[2]), .dst_rd_addr(rd_addr_2), .dst_rd_data(rd_data_2)
    );

    qcnn_conv_layer #(.C_IN(C1), .C_OUT(C2), .PAR(PAR2), .H_IN(H2), .H_OUT(H3), .IN_BITS(ACT_BITS), .ACT_BITS(ACT_BITS),
                      .SHIFT(L2_SHIFT), .WEIGHT_PREFIX("l2"), .BIAS_FILE("l2_bias.mem")) u_conv2 (
        .clk(clk), .rst(rst),
        .src_valid(valid[2]), .src_taken(taken[2]), .src_rd_addr(rd_addr_2), .src_rd_data(rd_data_2),
        .dst_valid(valid[3]), .dst_taken(taken[3]), .dst_rd_addr(rd_addr_3), .dst_rd_data(rd_data_3)
    );

    qcnn_conv_layer #(.C_IN(C2), .C_OUT(C3), .PAR(PAR3), .H_IN(H3), .H_OUT(H4), .IN_BITS(ACT_BITS), .ACT_BITS(ACT_BITS),
                      .SHIFT(L3_SHIFT), .WEIGHT_PREFIX("l3"), .BIAS_FILE("l3_bias.mem")) u_conv3 (
        .clk(clk), .rst(rst),
        .src_valid(valid[3]), .src_taken(taken[3]), .src_rd_addr(rd_addr_3), .src_rd_data(rd_data_3),
        .dst_valid(valid[4]), .dst_taken(taken[4]), .dst_rd_addr(rd_addr_4), .dst_rd_data(rd_data_4)
    );

    qcnn_head #(.C_IN(C3), .HIDDEN(HIDDEN), .N_OUT(N_OUT), .ACT_BITS(ACT_BITS),
                .GAP_SHIFT(L4_SHIFT), .FC1_SHIFT(L5_SHIFT), .FC2_SHIFT(L6_SHIFT)) u_head (
        .clk(clk), .rst(rst),
        .src_valid(valid[4]), .src_taken(taken[4]), .src_rd_addr(rd_addr_4), .src_rd_data(rd_data_4),
        .m_axis_result_tdata(m_axis_result_tdata), .m_axis_result_tvalid(m_axis_result_tvalid),
        .m_axis_result_tready(m_axis_result_tready), .m_axis_result_tlast(m_axis_result_tlast)
    );

    // elaboration-time shape check (no hardware)
    generate if (H4 != 4) begin : g_bad_in_h
        initial begin $display("qcnn_core: IN_H=%0d gives a %0dx%0d head map, need 4x4", IN_H, H4, H4); $finish; end
    end endgenerate

endmodule
