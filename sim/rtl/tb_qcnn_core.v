// tb_qcnn_core: stream N_FRAME golden frames, compare every result and the
// frame-0 feature maps of all conv layers against the Python integer model.
`timescale 1ns / 1ps

module tb_qcnn_core;

    `include "qcnn_params.vh"
    localparam N_FRAME = 16;
    localparam N_PIXEL = 56 * 56;

    reg clk = 0, rst = 1;
    always #5 clk = ~clk;  // 100 MHz

    reg  [7:0]  pixel_mem  [0:N_FRAME*N_PIXEL-1];
    reg  [15:0] result_mem [0:N_FRAME-1];
    reg  [15:0] map_mem    [0:65535];

    reg  [7:0]  pixel_tdata;
    reg         pixel_tvalid;
    wire        pixel_tready;
    wire signed [15:0] result_tdata;
    wire        result_tvalid;

    qcnn_core dut (
        .clk(clk), .rst(rst),
        .s_axis_pixel_tdata(pixel_tdata), .s_axis_pixel_tvalid(pixel_tvalid), .s_axis_pixel_tready(pixel_tready),
        .m_axis_result_tdata(result_tdata), .m_axis_result_tvalid(result_tvalid), .m_axis_result_tready(1'b1)
    );

    // ---------------- stimulus: all frames back to back ----------------
    integer pix = 0;
    initial begin
        $readmemh("tv_input.mem", pixel_mem);
        $readmemh("tv_output.mem", result_mem);
        pixel_tvalid = 0;
        repeat (5) @(posedge clk);
        rst <= 0;
        while (pix < N_FRAME * N_PIXEL) begin
            pixel_tdata  <= pixel_mem[pix];
            pixel_tvalid <= 1;
            @(posedge clk);
            if (pixel_tready) pix = pix + 1;
        end
        pixel_tvalid <= 0;
    end

    // ---------------- result checker ----------------
    integer n_result = 0, n_error = 0, t_first = 0;
    always @(posedge clk) if (result_tvalid) begin
        if (result_tdata !== $signed(result_mem[n_result])) begin
            n_error = n_error + 1;
            $display("FRAME %0d MISMATCH rtl=%0d golden=%0d", n_result, result_tdata, $signed(result_mem[n_result]));
        end
        if (n_result == 0) t_first = $time;
        n_result = n_result + 1;
        if (n_result == N_FRAME) begin
            $display("RESULT %0d/%0d match, first frame %0d us, then %0d us/frame",
                     N_FRAME - n_error, N_FRAME, t_first / 1000, ($time - t_first) / (N_FRAME - 1) / 1000);
            $finish;
        end
    end

    // ---------------- frame-0 feature map checker ----------------
    task check_map(input integer layer, input integer n);
        integer i, bad;
        begin
            bad = 0;
            for (i = 0; i < n; i = i + 1) begin
                case (layer)
                    0: if (dut.u_conv0.u_out_ram.mem[i] !== map_mem[i][ACT_BITS-1:0]) bad = bad + 1;
                    1: if (dut.u_conv1.u_out_ram.mem[i] !== map_mem[i][ACT_BITS-1:0]) bad = bad + 1;
                    2: if (dut.u_conv2.u_out_ram.mem[i] !== map_mem[i][ACT_BITS-1:0]) bad = bad + 1;
                    3: if (dut.u_conv3.u_out_ram.mem[i] !== map_mem[i][ACT_BITS-1:0]) bad = bad + 1;
                endcase
            end
            $display("LAYER %0d frame-0 map: %0d/%0d match", layer, n - bad, n);
        end
    endtask

    initial begin
        @(posedge dut.valid[1]); $readmemh("tv_frame0_l0.mem", map_mem); check_map(0, C0 * 28 * 28);
        @(posedge dut.valid[2]); $readmemh("tv_frame0_l1.mem", map_mem); check_map(1, C1 * 14 * 14);
        @(posedge dut.valid[3]); $readmemh("tv_frame0_l2.mem", map_mem); check_map(2, C2 * 7 * 7);
        @(posedge dut.valid[4]); $readmemh("tv_frame0_l3.mem", map_mem); check_map(3, C3 * 4 * 4);
    end

    initial begin
        #2_000_000_000;
        $display("TIMEOUT after %0d results", n_result);
        $finish;
    end

endmodule
