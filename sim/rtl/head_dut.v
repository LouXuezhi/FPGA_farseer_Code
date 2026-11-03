// head_dut: qcnn_head with the parameters from qcnn_params.vh, plain ports.
// Synthesizable on its own so RTL and netlist share one testbench.
`timescale 1ns / 1ps

module head_dut (
    input  wire        clk,
    input  wire        rst,
    input  wire        src_valid,
    output wire        src_taken,
    output wire [15:0] src_rd_addr,
    input  wire [15:0] src_rd_data,
    output wire signed [15:0] result_tdata,
    output wire        result_tvalid,
    input  wire        result_tready
);

    `include "qcnn_params.vh"

    wire [$clog2(C3*16)-1:0] head_src_addr;
    assign src_rd_addr = {{(16 - $clog2(C3*16)){1'b0}}, head_src_addr};

    qcnn_head #(.C_IN(C3), .HIDDEN(HIDDEN), .ACT_BITS(ACT_BITS),
                .GAP_SHIFT(L4_SHIFT), .FC1_SHIFT(L5_SHIFT), .FC2_SHIFT(L6_SHIFT)) u_head (
        .clk(clk), .rst(rst),
        .src_valid(src_valid), .src_taken(src_taken),
        .src_rd_addr(head_src_addr), .src_rd_data(src_rd_data[ACT_BITS-1:0]),
        .m_axis_result_tdata(result_tdata), .m_axis_result_tvalid(result_tvalid),
        .m_axis_result_tready(result_tready)
    );

endmodule
