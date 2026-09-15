// uart_frame_link: UART framing around qcnn_core, all in PL.
//
//   host -> FPGA : A5 5A id  pixel[0..3135]
//   FPGA -> host : 5A A5 id  result[7:0] result[15:8]
//
// Frame ids ride in a FIFO next to the core (results leave in input order).
// The pixel FIFO absorbs back-pressure while the pipeline is busy, since
// uart_rx cannot be paused.
`timescale 1ns / 1ps

module uart_frame_link #(
    parameter N_PIXEL  = 56 * 56,
    parameter PRESCALE = 4               // clk / (baud * 8)
) (
    input  wire clk,
    input  wire rst,
    input  wire uart_rxd,
    output wire uart_txd,
    output wire rx_error                 // sticky overrun / framing / FIFO drop
);

    localparam REQ_SYNC0 = 8'hA5, REQ_SYNC1 = 8'h5A, REPLY_SYNC0 = 8'h5A, REPLY_SYNC1 = 8'hA5;
    localparam RX_SYNC0 = 2'd0, RX_SYNC1 = 2'd1, RX_ID = 2'd2, RX_PIXEL = 2'd3;
    localparam TX_IDLE = 3'd0, TX_SYNC0 = 3'd1, TX_SYNC1 = 3'd2, TX_ID = 3'd3, TX_LSB = 3'd4, TX_MSB = 3'd5;

    // ---------------- UART (verilog-uart) ----------------
    wire [7:0] rx_tdata;
    wire       rx_tvalid;
    reg  [7:0] tx_tdata;
    reg        tx_tvalid;
    wire       tx_tready, rx_overrun, rx_frame_err;

    uart #(.DATA_WIDTH(8)) u_uart (
        .clk(clk), .rst(rst),
        .s_axis_tdata(tx_tdata), .s_axis_tvalid(tx_tvalid), .s_axis_tready(tx_tready),
        .m_axis_tdata(rx_tdata), .m_axis_tvalid(rx_tvalid), .m_axis_tready(1'b1),
        .rxd(uart_rxd), .txd(uart_txd),
        .tx_busy(), .rx_busy(), .rx_overrun_error(rx_overrun), .rx_frame_error(rx_frame_err),
        .prescale(PRESCALE[15:0])
    );

    // ---------------- RX parser ----------------
    reg [1:0]  rx_state_q, rx_state_d;
    reg [11:0] rx_count_q, rx_count_d;
    reg        pixel_push, id_push;
    wire       pixel_fifo_ready, id_fifo_ready;

    always @* begin
        rx_state_d = rx_state_q;
        rx_count_d = rx_count_q;
        pixel_push = 1'b0;
        id_push    = 1'b0;
        if (rx_tvalid) case (rx_state_q)
            RX_SYNC0: if (rx_tdata == REQ_SYNC0) rx_state_d = RX_SYNC1;
            RX_SYNC1: rx_state_d = (rx_tdata == REQ_SYNC1) ? RX_ID : (rx_tdata == REQ_SYNC0) ? RX_SYNC1 : RX_SYNC0;
            RX_ID: begin
                id_push    = 1'b1;
                rx_count_d = 0;
                rx_state_d = RX_PIXEL;
            end
            RX_PIXEL: begin
                pixel_push = 1'b1;
                rx_count_d = rx_count_q + 1;
                if (rx_count_q == N_PIXEL - 1) rx_state_d = RX_SYNC0;
            end
        endcase
    end

    // ---------------- FIFOs (verilog-axis) + core ----------------
    wire [7:0]  core_pixel_tdata, core_id_tdata;
    wire        core_pixel_tvalid, core_pixel_tready, core_id_tvalid;
    wire signed [15:0] result_tdata;
    wire        result_tvalid;
    reg         result_tready;

    axis_fifo #(.DEPTH(4096), .DATA_WIDTH(8), .KEEP_ENABLE(0), .LAST_ENABLE(0), .USER_ENABLE(0)) u_pixel_fifo (
        .clk(clk), .rst(rst),
        .s_axis_tdata(rx_tdata), .s_axis_tkeep(1'b1), .s_axis_tvalid(pixel_push), .s_axis_tready(pixel_fifo_ready),
        .s_axis_tlast(1'b0), .s_axis_tid(8'd0), .s_axis_tdest(8'd0), .s_axis_tuser(1'b0),
        .m_axis_tdata(core_pixel_tdata), .m_axis_tkeep(), .m_axis_tvalid(core_pixel_tvalid),
        .m_axis_tready(core_pixel_tready), .m_axis_tlast(), .m_axis_tid(), .m_axis_tdest(), .m_axis_tuser(),
        .pause_req(1'b0), .pause_ack(), .status_depth(), .status_depth_commit(), .status_overflow(),
        .status_bad_frame(), .status_good_frame()
    );

    axis_fifo #(.DEPTH(8), .DATA_WIDTH(8), .KEEP_ENABLE(0), .LAST_ENABLE(0), .USER_ENABLE(0)) u_id_fifo (
        .clk(clk), .rst(rst),
        .s_axis_tdata(rx_tdata), .s_axis_tkeep(1'b1), .s_axis_tvalid(id_push), .s_axis_tready(id_fifo_ready),
        .s_axis_tlast(1'b0), .s_axis_tid(8'd0), .s_axis_tdest(8'd0), .s_axis_tuser(1'b0),
        .m_axis_tdata(core_id_tdata), .m_axis_tkeep(), .m_axis_tvalid(core_id_tvalid),
        .m_axis_tready(result_tvalid && result_tready), .m_axis_tlast(), .m_axis_tid(), .m_axis_tdest(), .m_axis_tuser(),
        .pause_req(1'b0), .pause_ack(), .status_depth(), .status_depth_commit(), .status_overflow(),
        .status_bad_frame(), .status_good_frame()
    );

    qcnn_core u_core (
        .clk(clk), .rst(rst),
        .s_axis_pixel_tdata(core_pixel_tdata), .s_axis_pixel_tvalid(core_pixel_tvalid),
        .s_axis_pixel_tready(core_pixel_tready),
        .m_axis_result_tdata(result_tdata), .m_axis_result_tvalid(result_tvalid),
        .m_axis_result_tready(result_tready)
    );

    // ---------------- TX sequencer ----------------
    reg [2:0]  tx_state_q, tx_state_d;
    reg [23:0] tx_word_q, tx_word_d;     // {id, result}
    always @* begin
        tx_state_d    = tx_state_q;
        tx_word_d     = tx_word_q;
        result_tready = 1'b0;
        tx_tvalid     = (tx_state_q != TX_IDLE);
        case (tx_state_q)
            TX_SYNC0: tx_tdata = REPLY_SYNC0;
            TX_SYNC1: tx_tdata = REPLY_SYNC1;
            TX_ID:    tx_tdata = tx_word_q[23:16];
            TX_LSB:   tx_tdata = tx_word_q[7:0];
            default:  tx_tdata = tx_word_q[15:8];
        endcase
        if (tx_state_q == TX_IDLE) begin
            if (result_tvalid) begin
                result_tready = 1'b1;                 // also pops the id FIFO
                tx_word_d     = {core_id_tdata, result_tdata};
                tx_state_d    = TX_SYNC0;
            end
        end else if (tx_tready) begin
            tx_state_d = (tx_state_q == TX_MSB) ? TX_IDLE : tx_state_q + 1;
        end
    end

    // ---------------- sequential ----------------
    reg rx_error_q;
    assign rx_error = rx_error_q;
    always @(posedge clk) begin
        if (rst) begin
            rx_state_q <= RX_SYNC0;
            tx_state_q <= TX_IDLE;
            rx_error_q <= 1'b0;
        end else begin
            rx_state_q <= rx_state_d;
            tx_state_q <= tx_state_d;
            rx_error_q <= rx_error_q | rx_overrun | rx_frame_err |
                          (pixel_push & ~pixel_fifo_ready) | (id_push & ~id_fifo_ready);
        end
        rx_count_q <= rx_count_d;
        tx_word_q  <= tx_word_d;
    end

endmodule
