// tb_qcnn_core: stream N_FRAME golden frames, compare every result and the
// frame-0 feature maps of all conv layers against the Python integer model.
`timescale 1ns / 1ps

module tb_qcnn_core #(parameter N_FRAME = 16);   // up to 16 test frames

    `include "qcnn_params.vh"
    localparam N_PIXEL = 56 * 56;

    reg clk = 0, rst = 1;
    always #5 clk = ~clk;  // 100 MHz

    reg  [7:0]  pixel_mem  [0:16*N_PIXEL-1];
    reg  [15:0] result_mem [0:15];

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
    // Watches the write port of every output RAM (ports survive synthesis,
    // so the same check runs on RTL and on the post-synthesis netlist).
    // Each map is written exactly once per frame: the first n writes are frame 0.
    wire        watch0_en   = dut.u_conv0.u_out_ram.wr_en;
    wire [15:0] watch0_addr = dut.u_conv0.u_out_ram.wr_addr;
    wire [15:0] watch0_data = dut.u_conv0.u_out_ram.wr_data;
    tb_map_watch #(.LAYER(0), .N_WRITE(C0 * 28 * 28), .MAP_FILE("tv_frame0_l0.mem")) u_watch0 (
        .clk(clk), .wr_en(watch0_en), .wr_addr(watch0_addr), .wr_data(watch0_data));
    wire        watch1_en   = dut.u_conv1.u_out_ram.wr_en;
    wire [15:0] watch1_addr = dut.u_conv1.u_out_ram.wr_addr;
    wire [15:0] watch1_data = dut.u_conv1.u_out_ram.wr_data;
    tb_map_watch #(.LAYER(1), .N_WRITE(C1 * 14 * 14), .MAP_FILE("tv_frame0_l1.mem")) u_watch1 (
        .clk(clk), .wr_en(watch1_en), .wr_addr(watch1_addr), .wr_data(watch1_data));
    wire        watch2_en   = dut.u_conv2.u_out_ram.wr_en;
    wire [15:0] watch2_addr = dut.u_conv2.u_out_ram.wr_addr;
    wire [15:0] watch2_data = dut.u_conv2.u_out_ram.wr_data;
    tb_map_watch #(.LAYER(2), .N_WRITE(C2 * 7 * 7), .MAP_FILE("tv_frame0_l2.mem")) u_watch2 (
        .clk(clk), .wr_en(watch2_en), .wr_addr(watch2_addr), .wr_data(watch2_data));
    wire        watch3_en   = dut.u_conv3.u_out_ram.wr_en;
    wire [15:0] watch3_addr = dut.u_conv3.u_out_ram.wr_addr;
    wire [15:0] watch3_data = dut.u_conv3.u_out_ram.wr_data;
    tb_map_watch #(.LAYER(3), .N_WRITE(C3 * 4 * 4), .MAP_FILE("tv_frame0_l3.mem")) u_watch3 (
        .clk(clk), .wr_en(watch3_en), .wr_addr(watch3_addr), .wr_data(watch3_data));

endmodule


// compares the first N_WRITE writes of one RAM port against a golden map
module tb_map_watch #(parameter LAYER = 0, parameter N_WRITE = 1, parameter MAP_FILE = "") (
    input wire        clk,
    input wire        wr_en,
    input wire [15:0] wr_addr,
    input wire [15:0] wr_data
);
    reg [15:0] golden [0:65535];
    integer n_write = 0, n_bad = 0;
    initial $readmemh(MAP_FILE, golden);
    always @(posedge clk) if (wr_en === 1'b1 && n_write < N_WRITE) begin
        if (wr_data !== golden[wr_addr]) begin
            if (n_bad < 3) $display("LAYER %0d addr %0d rtl=%0d golden=%0d", LAYER, wr_addr, wr_data, golden[wr_addr]);
            n_bad = n_bad + 1;
        end
        n_write = n_write + 1;
        if (n_write == N_WRITE) $display("LAYER %0d frame-0 map: %0d/%0d match", LAYER, N_WRITE - n_bad, N_WRITE);
    end
endmodule
