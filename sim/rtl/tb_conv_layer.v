// tb_conv_layer: drives one conv layer from a golden input map, then reads the
// output map back through the module ports and compares it to the golden map.
// Port-only, so the same testbench runs on RTL and on the synthesized netlist.
`timescale 1ns / 1ps

module tb_conv_layer #(parameter LAYER = 0);

    `include "qcnn_params.vh"

    localparam C_IN  = (LAYER == 0) ? 1  : (LAYER == 1) ? C0 : (LAYER == 2) ? C1 : C2;
    localparam C_OUT = (LAYER == 0) ? C0 : (LAYER == 1) ? C1 : (LAYER == 2) ? C2 : C3;
    localparam H_IN  = (LAYER == 0) ? 56 : (LAYER == 1) ? 28 : (LAYER == 2) ? 14 : 7;
    localparam H_OUT = (H_IN + 1) / 2;
    localparam N_IN  = C_IN * H_IN * H_IN;
    localparam N_OUT = C_OUT * H_OUT * H_OUT;

    reg clk = 0, rst = 1;
    always #5 clk = ~clk;

    // golden maps: layer 0 reads the input frame, deeper layers the previous map
    reg [15:0] src_mem [0:N_IN-1];
    reg [15:0] dst_mem [0:N_OUT-1];
    initial begin
        if (LAYER == 0)      $readmemh("tv_input.mem", src_mem);
        else if (LAYER == 1) $readmemh("tv_frame0_l0.mem", src_mem);
        else if (LAYER == 2) $readmemh("tv_frame0_l1.mem", src_mem);
        else                 $readmemh("tv_frame0_l2.mem", src_mem);
        if (LAYER == 0)      $readmemh("tv_frame0_l0.mem", dst_mem);
        else if (LAYER == 1) $readmemh("tv_frame0_l1.mem", dst_mem);
        else if (LAYER == 2) $readmemh("tv_frame0_l2.mem", dst_mem);
        else                 $readmemh("tv_frame0_l3.mem", dst_mem);
    end

    wire [15:0] src_rd_addr, dst_rd_data;
    wire        src_taken, dst_valid;
    reg  [15:0] src_rd_data;
    reg  [15:0] dst_rd_addr = 0;

    always @(posedge clk) src_rd_data <= src_mem[src_rd_addr];   // source RAM model

`ifdef POSTSYNTH
    conv_layer_dut dut (
`else
    conv_layer_dut #(.LAYER(LAYER)) dut (
`endif
        .clk(clk), .rst(rst), .src_valid(1'b1), .src_taken(src_taken),
        .src_rd_addr(src_rd_addr), .src_rd_data(src_rd_data),
        .dst_valid(dst_valid), .dst_rd_addr(dst_rd_addr), .dst_rd_data(dst_rd_data)
    );

    integer i, n_bad = 0;
    initial begin
        repeat (30) @(posedge clk);   // past the gate-sim global reset (GSR, 100 ns)
        rst <= 0;
        @(posedge dst_valid);
        for (i = 0; i < N_OUT; i = i + 1) begin   // read the map back, one address per cycle
            dst_rd_addr <= i;
            @(posedge clk);
            @(posedge clk);                        // RAM read latency
            if (dst_rd_data !== dst_mem[i]) begin
                if (n_bad < 5) $display("LAYER %0d addr %0d rtl=%0d golden=%0d", LAYER, i, dst_rd_data, dst_mem[i]);
                n_bad = n_bad + 1;
            end
        end
        $display("RESULT layer %0d: %0d/%0d outputs match", LAYER, N_OUT - n_bad, N_OUT);
        $finish;
    end

    initial begin
        #500_000_000;
        $display("TIMEOUT layer %0d", LAYER);
        $finish;
    end

endmodule
