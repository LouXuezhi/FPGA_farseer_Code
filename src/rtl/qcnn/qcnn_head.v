// qcnn_head: GAP over 4x4 -> FC(C->HIDDEN)+ReLU/unsigned -> FC(HIDDEN->1) -> int16 result.
//
// GAP : read source map, sum 16 pixels, round-shift by 4          (2-stage)
// FC  : shared multiply-accumulate for both FC layers, layer_q     (2-stage)
// Releases the source map (src_taken) right after the GAP reads.
`timescale 1ns / 1ps

module qcnn_head #(
    parameter C_IN      = 32,
    parameter HIDDEN    = 16,
    parameter ACT_BITS  = 8,
    parameter GAP_SHIFT = 4,
    parameter FC1_SHIFT = 8,
    parameter FC2_SHIFT = 8,
    parameter FC1_WEIGHT_FILE = "l5_weight.mem",
    parameter FC1_BIAS_FILE   = "l5_bias.mem",
    parameter FC2_WEIGHT_FILE = "l6_weight.mem",
    parameter FC2_BIAS_FILE   = "l6_bias.mem"
) (
    input  wire                              clk,
    input  wire                              rst,
    // source feature map, C_IN x 4 x 4
    input  wire                              src_valid,
    output reg                               src_taken,
    output reg  [$clog2(C_IN*16)-1:0]        src_rd_addr,
    input  wire [ACT_BITS-1:0]               src_rd_data,
    // result stream
    output wire signed [15:0]                m_axis_result_tdata,
    output wire                              m_axis_result_tvalid,
    input  wire                              m_axis_result_tready
);

    localparam ACT_MAX = (1 << ACT_BITS) - 1;
    localparam ST_IDLE = 2'd0, ST_GAP = 2'd1, ST_FC = 2'd2, ST_OUT = 2'd3;

    // ---------------- memories ----------------
    reg signed [7:0]  fc1_weight_rom [0:HIDDEN*C_IN-1];
    reg signed [31:0] fc1_bias_rom   [0:HIDDEN-1];
    reg signed [7:0]  fc2_weight_rom [0:HIDDEN-1];
    reg signed [31:0] fc2_bias_rom   [0:0];
    reg [ACT_BITS-1:0] gap_mem [0:C_IN-1];
    reg [ACT_BITS-1:0] fc1_mem [0:HIDDEN-1];
    initial begin
        $readmemh(FC1_WEIGHT_FILE, fc1_weight_rom);
        $readmemh(FC1_BIAS_FILE, fc1_bias_rom);
        $readmemh(FC2_WEIGHT_FILE, fc2_weight_rom);
        $readmemh(FC2_BIAS_FILE, fc2_bias_rom);
    end

    // ---------------- registers ----------------
    reg [1:0]  state_q, state_d;
    reg [7:0]  ch_q, ch_d;              // GAP channel / FC input index
    reg [3:0]  pix_q, pix_d;            // GAP pixel 0..15
    reg [7:0]  out_q, out_d;            // FC output neuron
    reg        layer_q, layer_d;        // 0: FC1, 1: FC2
    reg signed [15:0] result_q;
    reg        result_valid_q, result_valid_d;
    // GAP pipeline
    reg        gap_valid_q, gap_first_q, gap_last_q;
    reg [7:0]  gap_ch_q;
    reg [ACT_BITS+3:0] gap_sum_q;
    // FC pipeline
    reg        fc_valid_q, fc_first_q, fc_last_q, fc_layer_q;
    reg [7:0]  fc_out_q;
    reg signed [ACT_BITS+8:0] fc_prod_q;
    reg signed [31:0] fc_acc_q;

    assign m_axis_result_tdata  = result_q;
    assign m_axis_result_tvalid = result_valid_q;

    // ---------------- control ----------------
    reg [7:0] fc_n_in, fc_n_out;
    always @* begin
        state_d        = state_q;
        {ch_d, pix_d, out_d, layer_d} = {ch_q, pix_q, out_q, layer_q};
        result_valid_d = result_valid_q;
        src_taken      = 1'b0;
        src_rd_addr    = ch_q * 16 + pix_q;
        fc_n_in        = layer_q ? HIDDEN : C_IN;
        fc_n_out       = layer_q ? 1 : HIDDEN;

        case (state_q)
            ST_IDLE: if (src_valid && !result_valid_q) begin
                state_d = ST_GAP;
                {ch_d, pix_d} = 0;
            end
            ST_GAP: begin
                pix_d = pix_q + 1;
                if (pix_q == 15) begin
                    ch_d = ch_q + 1;
                    if (ch_q == C_IN - 1) begin
                        src_taken = 1'b1;       // last read issued, source may be reused
                        state_d   = ST_FC;
                        {ch_d, out_d, layer_d} = 0;
                    end
                end
            end
            ST_FC: begin
                ch_d = ch_q + 1;
                if (ch_q == fc_n_in - 1) begin
                    ch_d  = 0;
                    out_d = out_q + 1;
                    if (out_q == fc_n_out - 1) begin
                        out_d   = 0;
                        layer_d = 1'b1;
                        if (layer_q) state_d = ST_OUT;
                    end
                end
            end
            ST_OUT: if (result_valid_q && m_axis_result_tready) begin
                result_valid_d = 1'b0;
                state_d        = ST_IDLE;
            end
        endcase
    end

    // ---------------- datapath ----------------
    reg [ACT_BITS+3:0] gap_sum;
    reg signed [31:0] fc_acc_sum, fc1_round, fc2_round;
    always @* begin
        gap_sum    = (gap_first_q ? 0 : gap_sum_q) + src_rd_data;
        fc_acc_sum = (fc_first_q ? (fc_layer_q ? fc2_bias_rom[0] : fc1_bias_rom[fc_out_q]) : fc_acc_q) + fc_prod_q;
        fc1_round  = (fc_acc_sum + (32'sd1 <<< (FC1_SHIFT - 1))) >>> FC1_SHIFT;
        fc2_round  = (fc_acc_sum + (32'sd1 <<< (FC2_SHIFT - 1))) >>> FC2_SHIFT;
    end

    always @(posedge clk) begin
        if (rst) begin
            state_q        <= ST_IDLE;
            result_valid_q <= 1'b0;
            gap_valid_q    <= 1'b0;
            fc_valid_q     <= 1'b0;
        end else begin
            state_q        <= state_d;
            result_valid_q <= result_valid_d | (fc_valid_q & fc_last_q & fc_layer_q);
            gap_valid_q    <= (state_q == ST_GAP);
            fc_valid_q     <= (state_q == ST_FC);
        end
        {ch_q, pix_q, out_q, layer_q} <= {ch_d, pix_d, out_d, layer_d};

        // GAP: read issued in ST_GAP, data one cycle later
        gap_first_q <= (pix_q == 0);
        gap_last_q  <= (pix_q == 15);
        gap_ch_q    <= ch_q;
        gap_sum_q   <= gap_sum;
        if (gap_valid_q && gap_last_q)
            gap_mem[gap_ch_q] <= (gap_sum + (1 << (GAP_SHIFT - 1))) >> GAP_SHIFT;

        // FC stage 1: multiply
        fc_first_q <= (ch_q == 0);
        fc_last_q  <= (ch_q == fc_n_in - 1);
        fc_layer_q <= layer_q;
        fc_out_q   <= out_q;
        fc_prod_q  <= layer_q ? $signed({1'b0, fc1_mem[ch_q]}) * fc2_weight_rom[ch_q]
                              : $signed({1'b0, gap_mem[ch_q]}) * fc1_weight_rom[out_q * C_IN + ch_q];
        // FC stage 2: accumulate, write neuron output
        fc_acc_q <= fc_acc_sum;
        if (fc_valid_q && fc_last_q && !fc_layer_q)
            fc1_mem[fc_out_q] <= (fc1_round < 0) ? 0 : (fc1_round > ACT_MAX) ? ACT_MAX : fc1_round[ACT_BITS-1:0];
        if (fc_valid_q && fc_last_q && fc_layer_q)
            result_q <= (fc2_round < -32768) ? -16'sd32768 : (fc2_round > 32767) ? 16'sd32767 : fc2_round[15:0];
    end

endmodule
