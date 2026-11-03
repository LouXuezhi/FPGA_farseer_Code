// qcnn_frame_loader: byte stream (raster order) -> input feature map RAM.
// Accepts N_PIXEL bytes, then holds the frame (dst_valid) until the first
// conv layer takes it; tready is low meanwhile. tlast is not required.
`timescale 1ns / 1ps

module qcnn_frame_loader #(
    parameter N_PIXEL = 56 * 56
) (
    input  wire                         clk,
    input  wire                         rst,
    input  wire [7:0]                   s_axis_pixel_tdata,
    input  wire                         s_axis_pixel_tvalid,
    output wire                         s_axis_pixel_tready,
    output wire                         dst_valid,
    input  wire                         dst_taken,
    input  wire [$clog2(N_PIXEL)-1:0]   dst_rd_addr,
    output wire [7:0]                   dst_rd_data
);

    reg [$clog2(N_PIXEL)-1:0] count_q, count_d;
    reg                       dst_valid_q, dst_valid_d;
    wire                      write_en = s_axis_pixel_tvalid && s_axis_pixel_tready;

    qcnn_sdp_ram #(.DEPTH(N_PIXEL), .WIDTH(8)) u_pixel_ram (
        .clk(clk), .wr_en(write_en), .wr_addr(count_q), .wr_data(s_axis_pixel_tdata),
        .rd_addr(dst_rd_addr), .rd_data(dst_rd_data)
    );

    assign s_axis_pixel_tready = !dst_valid_q;
    assign dst_valid           = dst_valid_q;

    always @* begin
        count_d     = count_q;
        dst_valid_d = dst_valid_q & ~dst_taken;
        if (write_en) begin
            count_d = count_q + 1;
            if (count_q == N_PIXEL - 1) begin
                count_d     = 0;
                dst_valid_d = 1'b1;       // frame complete
            end
        end
    end

    always @(posedge clk) begin
        if (rst) begin
            count_q     <= 0;
            dst_valid_q <= 1'b0;
        end else begin
            count_q     <= count_d;
            dst_valid_q <= dst_valid_d;
        end
    end

endmodule
