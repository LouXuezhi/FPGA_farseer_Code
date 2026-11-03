// axis_frame_link: AXI-Stream wrapper of qcnn_core for AXI DMA (PS <-> PL).
//
//   s_axis : pixels, 8 bit, frames back to back (IN_CH planes per frame);
//            tlast ends a DMA batch
//   m_axis : N_OUT 32-bit results per frame (sign-extended int16);
//            tlast is set on the last result of the frame whose last pixel
//            carried tlast, so one DMA receive returns exactly one batch.
`timescale 1ns / 1ps

module axis_frame_link (
    (* X_INTERFACE_PARAMETER = "ASSOCIATED_BUSIF s_axis:m_axis, ASSOCIATED_RESET rst" *)
    input  wire        clk,
    // active high: without this Vivado infers ACTIVE_LOW for a port named rst and
    // the block design wires peripheral_aresetn here, holding the core in reset
    (* X_INTERFACE_PARAMETER = "POLARITY ACTIVE_HIGH" *)
    input  wire        rst,
    input  wire [7:0]  s_axis_tdata,
    input  wire        s_axis_tvalid,
    output wire        s_axis_tready,
    input  wire        s_axis_tlast,
    output wire [31:0] m_axis_tdata,
    output wire        m_axis_tvalid,
    input  wire        m_axis_tready,
    output wire        m_axis_tlast
);

    `include "qcnn_params.vh"
    localparam N_PIXEL = IN_CH * IN_H * IN_H;

    reg  [$clog2(N_PIXEL)-1:0] count_q, count_d;
    wire        pixel_fire = s_axis_tvalid && s_axis_tready;
    wire        frame_end  = pixel_fire && (count_q == N_PIXEL - 1);
    wire signed [15:0] result_tdata;
    wire        result_tvalid, result_tlast, batch_last;

    always @* begin
        count_d = count_q;
        if (pixel_fire) count_d = (count_q == N_PIXEL - 1) ? 0 : count_q + 1;
    end

    always @(posedge clk) begin
        if (rst) count_q <= 0;
        else     count_q <= count_d;
    end

    qcnn_core u_core (
        .clk(clk), .rst(rst),
        .s_axis_pixel_tdata(s_axis_tdata), .s_axis_pixel_tvalid(s_axis_tvalid), .s_axis_pixel_tready(s_axis_tready),
        .m_axis_result_tdata(result_tdata), .m_axis_result_tvalid(result_tvalid), .m_axis_result_tready(m_axis_tready),
        .m_axis_result_tlast(result_tlast)
    );

    // per-frame tlast flag, popped with the last result of the frame (verilog-axis)
    axis_fifo #(.DEPTH(8), .DATA_WIDTH(1), .KEEP_ENABLE(0), .LAST_ENABLE(0), .USER_ENABLE(0)) u_last_fifo (
        .clk(clk), .rst(rst),
        .s_axis_tdata(s_axis_tlast), .s_axis_tkeep(1'b1), .s_axis_tvalid(frame_end), .s_axis_tready(),
        .s_axis_tlast(1'b0), .s_axis_tid(8'd0), .s_axis_tdest(8'd0), .s_axis_tuser(1'b0),
        .m_axis_tdata(batch_last), .m_axis_tkeep(), .m_axis_tvalid(),
        .m_axis_tready(result_tvalid && m_axis_tready && result_tlast), .m_axis_tlast(), .m_axis_tid(), .m_axis_tdest(), .m_axis_tuser(),
        .pause_req(1'b0), .pause_ack(), .status_depth(), .status_depth_commit(), .status_overflow(),
        .status_bad_frame(), .status_good_frame()
    );

    assign m_axis_tdata  = {{16{result_tdata[15]}}, result_tdata};
    assign m_axis_tvalid = result_tvalid;
    assign m_axis_tlast  = batch_last && result_tlast;

endmodule
