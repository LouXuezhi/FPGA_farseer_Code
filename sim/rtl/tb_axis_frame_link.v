// tb_axis_frame_link: two DMA-like batches (3 + 5 frames) with random
// valid/ready stalls; checks every result and the batch tlast positions.
`timescale 1ns / 1ps

module tb_axis_frame_link;

    localparam N_FRAME = 8;
    localparam N_PIXEL = 56 * 56;
    localparam BATCH0  = 3;           // tlast after frame 2 and frame 7

    reg clk = 0, rst = 1;
    always #5 clk = ~clk;

    reg [7:0]  pixel_mem  [0:16*N_PIXEL-1];
    reg [15:0] result_mem [0:15];

    reg  [7:0]  s_tdata;
    reg         s_tvalid = 0, s_tlast = 0, m_tready = 0;
    wire        s_tready, m_tvalid, m_tlast;
    wire [31:0] m_tdata;

    axis_frame_link dut (
        .clk(clk), .rst(rst),
        .s_axis_tdata(s_tdata), .s_axis_tvalid(s_tvalid), .s_axis_tready(s_tready), .s_axis_tlast(s_tlast),
        .m_axis_tdata(m_tdata), .m_axis_tvalid(m_tvalid), .m_axis_tready(m_tready), .m_axis_tlast(m_tlast)
    );

    // source with random gaps
    integer pix = 0;
    initial begin
        $readmemh("tv_input.mem", pixel_mem);
        $readmemh("tv_output.mem", result_mem);
        repeat (30) @(posedge clk);   // past the gate-sim global reset (GSR, 100 ns)
        rst <= 0;
        while (pix < N_FRAME * N_PIXEL) begin
            s_tvalid <= ($random & 3) != 0;
            s_tdata  <= pixel_mem[pix];
            s_tlast  <= (pix == BATCH0 * N_PIXEL - 1) || (pix == N_FRAME * N_PIXEL - 1);
            @(posedge clk);
            if (s_tvalid && s_tready) pix = pix + 1;
        end
        s_tvalid <= 0;
    end

    // sink with random stalls
    integer n_result = 0, n_error = 0;
    always @(posedge clk) begin
        m_tready <= ($random & 1);
        if (m_tvalid && m_tready) begin
            if (m_tdata !== {{16{result_mem[n_result][15]}}, result_mem[n_result]} ||
                m_tlast !== (n_result == BATCH0 - 1 || n_result == N_FRAME - 1)) begin
                n_error = n_error + 1;
                $display("FRAME %0d MISMATCH data=%h last=%b golden=%h", n_result, m_tdata, m_tlast, result_mem[n_result]);
            end
            n_result = n_result + 1;
            if (n_result == N_FRAME) begin
                $display("RESULT %0d/%0d match (data + tlast)", N_FRAME - n_error, N_FRAME);
                $finish;
            end
        end
    end

    initial begin
        #2_000_000_000;
        $display("TIMEOUT after %0d results", n_result);
        $finish;
    end

endmodule
