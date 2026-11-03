// tb_head: feeds the frame-0 conv3 map into the head and checks the int16 result.
`timescale 1ns / 1ps

module tb_head;

    `include "qcnn_params.vh"

    reg clk = 0, rst = 1;
    always #5 clk = ~clk;

    reg [15:0] src_mem    [0:C3*16-1];
    reg [15:0] result_mem [0:15];
    initial begin
        $readmemh("tv_frame0_l3.mem", src_mem);
        $readmemh("tv_output.mem", result_mem);
    end

    wire [15:0] src_rd_addr;
    wire        src_taken, result_tvalid;
    wire signed [15:0] result_tdata;
    reg  [15:0] src_rd_data;

    always @(posedge clk) src_rd_data <= src_mem[src_rd_addr];

    head_dut dut (
        .clk(clk), .rst(rst), .src_valid(1'b1), .src_taken(src_taken),
        .src_rd_addr(src_rd_addr), .src_rd_data(src_rd_data),
        .result_tdata(result_tdata), .result_tvalid(result_tvalid), .result_tready(1'b1)
    );

    initial begin
        repeat (30) @(posedge clk);   // past the gate-sim global reset (GSR, 100 ns)
        rst <= 0;
        @(posedge result_tvalid);
        @(negedge clk);
        if (result_tdata === $signed(result_mem[0]))
            $display("RESULT head: match (%0d)", result_tdata);
        else
            $display("RESULT head: MISMATCH rtl=%0d golden=%0d", result_tdata, $signed(result_mem[0]));
        $finish;
    end

    initial begin
        #10_000_000;
        $display("TIMEOUT head");
        $finish;
    end

endmodule
