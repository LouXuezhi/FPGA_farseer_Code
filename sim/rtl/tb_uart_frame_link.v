// tb_uart_frame_link: a host-side UART (verilog-uart) sends N_FRAME framed
// images back to back at 3 Mbps; checks id + result of every reply.
`timescale 1ns / 1ps

module tb_uart_frame_link;

    localparam N_FRAME  = 4;
    localparam N_PIXEL  = 56 * 56;
    localparam PRESCALE = 4;          // 96 MHz / (3 Mbps * 8)

    reg clk = 0, rst = 1;
    always #5.2083 clk = ~clk;        // 96 MHz

    reg [7:0]  pixel_mem  [0:16*N_PIXEL-1];
    reg [15:0] result_mem [0:15];

    wire host_to_fpga, fpga_to_host, rx_error;
    reg  [7:0] host_tx_tdata;
    reg        host_tx_tvalid;
    wire       host_tx_tready;
    wire [7:0] host_rx_tdata;
    wire       host_rx_tvalid;

    uart_frame_link #(.PRESCALE(PRESCALE)) dut (
        .clk(clk), .rst(rst), .uart_rxd(host_to_fpga), .uart_txd(fpga_to_host), .rx_error(rx_error)
    );

    uart #(.DATA_WIDTH(8)) u_host_uart (
        .clk(clk), .rst(rst),
        .s_axis_tdata(host_tx_tdata), .s_axis_tvalid(host_tx_tvalid), .s_axis_tready(host_tx_tready),
        .m_axis_tdata(host_rx_tdata), .m_axis_tvalid(host_rx_tvalid), .m_axis_tready(1'b1),
        .rxd(fpga_to_host), .txd(host_to_fpga),
        .tx_busy(), .rx_busy(), .rx_overrun_error(), .rx_frame_error(), .prescale(PRESCALE[15:0])
    );

    task send_byte(input [7:0] b);
        begin
            host_tx_tdata  <= b;
            host_tx_tvalid <= 1;
            @(posedge clk);
            while (!host_tx_tready) @(posedge clk);
            host_tx_tvalid <= 0;
            @(posedge clk);
        end
    endtask

    integer f, p;
    initial begin
        $readmemh("tv_input.mem", pixel_mem);
        $readmemh("tv_output.mem", result_mem);
        host_tx_tvalid = 0;
        repeat (10) @(posedge clk);
        rst <= 0;
        send_byte(8'h00);                              // junk before sync
        for (f = 0; f < N_FRAME; f = f + 1) begin
            send_byte(8'hA5); send_byte(8'h5A); send_byte(8'h10 + f);
            for (p = 0; p < N_PIXEL; p = p + 1) send_byte(pixel_mem[f * N_PIXEL + p]);
        end
    end

    // reply decoder: 5 bytes per frame
    reg [7:0] reply [0:4];
    integer n_byte = 0, n_frame = 0, n_error = 0;
    always @(posedge clk) if (host_rx_tvalid) begin
        reply[n_byte] = host_rx_tdata;
        n_byte = n_byte + 1;
        if (n_byte == 5) begin
            n_byte = 0;
            if (reply[0] !== 8'h5A || reply[1] !== 8'hA5 || reply[2] !== 8'h10 + n_frame ||
                {reply[4], reply[3]} !== result_mem[n_frame]) begin
                n_error = n_error + 1;
                $display("REPLY %0d MISMATCH %h %h id=%h result=%h golden=%h", n_frame,
                         reply[0], reply[1], reply[2], {reply[4], reply[3]}, result_mem[n_frame]);
            end
            n_frame = n_frame + 1;
            if (n_frame == N_FRAME) begin
                $display("RESULT %0d/%0d replies match, rx_error=%b", N_FRAME - n_error, N_FRAME, rx_error);
                $finish;
            end
        end
    end

    initial begin
        #1_000_000_000;
        $display("TIMEOUT after %0d replies", n_frame);
        $finish;
    end

endmodule
