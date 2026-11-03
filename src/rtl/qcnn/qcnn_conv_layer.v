// qcnn_conv_layer: 3x3 conv (pad 1) + requant + ReLU/unsigned clamp, one frame at a time.
//
// Output channels are computed PAR at a time ("lanes"):
//   loop  oy -> ox -> group g -> tap j = (ic, ky, kx)
//   the input activation of tap j is shared by all lanes, so one source
//   read feeds PAR multipliers; lane p owns a ROM with the weights of
//   output channels g*PAR + p (file <prefix>_weight_laneNN.mem from qcnn_int.py).
// Pipeline: S0 counters -> S1 address register -> S2 read + PAR multiplies
//           -> S3 PAR accumulators -> S4 requantize -> writer puts the PAR
//           results into the output RAM one per cycle
//           (needs PAR <= C_IN*9, the gap between two group completions).
// Frame handshake: src_valid/src_taken with the previous stage,
//                  dst_valid/dst_taken with the next stage.
// Feature map address = (c * H + y) * H + x.
`timescale 1ns / 1ps

module qcnn_conv_layer #(
    parameter C_IN        = 1,
    parameter C_OUT       = 8,
    parameter PAR         = 8,               // lanes, divides C_OUT, <= C_IN*9
    parameter H_IN        = 56,
    parameter H_OUT       = 28,
    parameter STRIDE      = 2,
    parameter IN_BITS     = 8,               // unsigned input activation width
    parameter ACT_BITS    = 8,               // unsigned output activation width
    parameter SHIFT       = 8,
    parameter WEIGHT_PREFIX = "l0",            // lane files <prefix>_weight_laneNN.mem
    parameter BIAS_FILE   = "l0_bias.mem"
) (
    input  wire                                 clk,
    input  wire                                 rst,
    // source feature map (read port of previous stage)
    input  wire                                 src_valid,
    output reg                                  src_taken,
    output wire [$clog2(C_IN*H_IN*H_IN)-1:0]    src_rd_addr,
    input  wire [IN_BITS-1:0]                   src_rd_data,
    // destination feature map (read by next stage)
    output wire                                 dst_valid,
    input  wire                                 dst_taken,
    input  wire [$clog2(C_OUT*H_OUT*H_OUT)-1:0] dst_rd_addr,
    output wire [ACT_BITS-1:0]                  dst_rd_data
);

    localparam N_TAP   = C_IN * 9;
    localparam N_GROUP = C_OUT / PAR;
    localparam N_OUT   = C_OUT * H_OUT * H_OUT;
    localparam PLANE   = H_OUT * H_OUT;
    localparam ACT_MAX = (1 << ACT_BITS) - 1;
    localparam ST_IDLE = 2'd0, ST_RUN = 2'd1, ST_FLUSH = 2'd2, ST_DONE = 2'd3;

    // ---------------- control registers ----------------
    reg [1:0]  state_q, state_d;
    reg        dst_valid_q, dst_valid_d;
    reg [7:0]  oy_q, oy_d, ox_q, ox_d, g_q, g_d, ic_q, ic_d;       // loop counters
    reg [1:0]  ky_q, ky_d, kx_q, kx_d;
    reg        wr_busy_q, wr_busy_d;                                // output writer
    reg [7:0]  wr_lane_q, wr_lane_d;
    reg [$clog2(N_OUT)-1:0] wr_base_q, wr_base_d;

    // ---------------- pipeline registers ----------------
    reg        s1_valid_q, s1_first_q, s1_last_q, s1_pad_q;
    reg [7:0]  s1_g_q;
    reg [$clog2(N_OUT)-1:0] s1_base_q;
    reg [$clog2(C_IN*H_IN*H_IN)-1:0] s1_src_addr_q;
    reg [$clog2(N_GROUP*N_TAP)-1:0] s1_w_addr_q;
    reg        s2_valid_q, s2_first_q, s2_last_q, s2_pad_q;
    reg [7:0]  s2_g_q;
    reg [$clog2(N_OUT)-1:0] s2_base_q;
    reg        s3_valid_q, s3_first_q, s3_last_q;
    reg [$clog2(N_OUT)-1:0] s3_base_q;
    reg        s4_valid_q;                                       // S4: group sums complete
    reg [$clog2(N_OUT)-1:0] s4_base_q;
    wire [ACT_BITS*PAR-1:0] lane_result;                        // per-lane results, waiting for the writer

    // ---------------- memories ----------------
    reg  [$clog2(C_IN*H_IN*H_IN)-1:0] src_addr;
    reg  [$clog2(N_GROUP*N_TAP)-1:0] w_addr;
    reg  [$clog2(N_OUT)-1:0]         base_addr;                 // address of (g*PAR, oy, ox)
    wire [8*PAR-1:0]                 w_data;
    (* rom_style = "distributed" *) reg signed [31:0] bias_rom [0:C_OUT-1];
    initial $readmemh(BIAS_FILE, bias_rom);

    genvar p;
    generate for (p = 0; p < PAR; p = p + 1) begin : g_lane_rom
        localparam [8*32-1:0] LANE_FILE = {WEIGHT_PREFIX, "_weight_lane", 8'd48 + p / 10, 8'd48 + p % 10, ".mem"};
        qcnn_sync_rom #(.DEPTH(N_GROUP * N_TAP), .WIDTH(8), .INIT_FILE(LANE_FILE)) u_weight_rom (
            .clk(clk), .rd_addr(s1_w_addr_q), .rd_data(w_data[8*p +: 8])
        );
    end endgenerate

    qcnn_sdp_ram #(.DEPTH(N_OUT), .WIDTH(ACT_BITS)) u_out_ram (
        .clk(clk), .wr_en(wr_busy_q), .wr_addr(wr_base_q + wr_lane_q * PLANE), .wr_data(lane_result[ACT_BITS*wr_lane_q +: ACT_BITS]),
        .rd_addr(dst_rd_addr), .rd_data(dst_rd_data)
    );

    assign dst_valid   = dst_valid_q;
    assign src_rd_addr = s1_src_addr_q;

    // ---------------- S0: loop control + writer (combinational next state) ----------------
    reg        last_tap, last_pixel, pad;
    reg signed [9:0] iy, ix;
    always @* begin
        state_d     = state_q;
        dst_valid_d = dst_valid_q & ~dst_taken;
        {oy_d, ox_d, g_d, ic_d, ky_d, kx_d} = {oy_q, ox_q, g_q, ic_q, ky_q, kx_q};
        {wr_busy_d, wr_lane_d, wr_base_d}   = {wr_busy_q, wr_lane_q, wr_base_q};
        src_taken   = 1'b0;

        last_tap    = (kx_q == 2) && (ky_q == 2) && (ic_q == C_IN - 1);
        last_pixel  = (g_q == N_GROUP - 1) && (ox_q == H_OUT - 1) && (oy_q == H_OUT - 1);
        iy          = $signed({2'b0, oy_q}) * STRIDE + $signed({1'b0, ky_q}) - 1;  // keep signed: pad < 0
        ix          = $signed({2'b0, ox_q}) * STRIDE + $signed({1'b0, kx_q}) - 1;
        pad         = (iy < 0) || (iy >= H_IN) || (ix < 0) || (ix >= H_IN);
        src_addr    = (ic_q * H_IN + (pad ? 0 : iy)) * H_IN + (pad ? 0 : ix);
        w_addr      = g_q * N_TAP + (ic_q * 9 + ky_q * 3 + kx_q);
        base_addr   = (g_q * PAR * H_OUT + oy_q) * H_OUT + ox_q;

        case (state_q)
            ST_IDLE: if (src_valid && !dst_valid_q) begin
                state_d = ST_RUN;
                {oy_d, ox_d, g_d, ic_d, ky_d, kx_d} = 0;
            end
            ST_RUN: begin
                // kx -> ky -> ic -> g -> ox -> oy
                kx_d = kx_q + 1;
                if (kx_q == 2) begin
                    kx_d = 0; ky_d = ky_q + 1;
                    if (ky_q == 2) begin
                        ky_d = 0; ic_d = ic_q + 1;
                        if (ic_q == C_IN - 1) begin
                            ic_d = 0; g_d = g_q + 1;
                            if (g_q == N_GROUP - 1) begin
                                g_d = 0; ox_d = ox_q + 1;
                                if (ox_q == H_OUT - 1) begin
                                    ox_d = 0; oy_d = oy_q + 1;
                                end
                            end
                        end
                    end
                end
                if (last_tap && last_pixel) state_d = ST_FLUSH;
            end
            ST_FLUSH: if (!s1_valid_q && !s2_valid_q && !s3_valid_q && !s4_valid_q && !wr_busy_q) state_d = ST_DONE;
            ST_DONE: begin
                src_taken   = 1'b1;
                dst_valid_d = 1'b1;
                state_d     = ST_IDLE;
            end
        endcase

        // writer: start when S4 holds a group, then one lane per cycle
        if (s4_valid_q) begin
            wr_busy_d = 1'b1;
            wr_lane_d = 0;
            wr_base_d = s4_base_q;
        end else if (wr_busy_q) begin
            wr_lane_d = wr_lane_q + 1;
            if (wr_lane_q == PAR - 1) wr_busy_d = 1'b0;
        end
    end

    // ---------------- sequential: control ----------------
    always @(posedge clk) begin
        if (rst) begin
            state_q     <= ST_IDLE;
            dst_valid_q <= 1'b0;
            wr_busy_q   <= 1'b0;
            s1_valid_q  <= 1'b0;
            s2_valid_q  <= 1'b0;
            s3_valid_q  <= 1'b0;
            s4_valid_q  <= 1'b0;
        end else begin
            state_q     <= state_d;
            dst_valid_q <= dst_valid_d;
            wr_busy_q   <= wr_busy_d;
            s1_valid_q  <= (state_q == ST_RUN);
            s2_valid_q  <= s1_valid_q;
            s3_valid_q  <= s2_valid_q;
            s4_valid_q  <= s3_valid_q && s3_last_q;
        end
        {oy_q, ox_q, g_q, ic_q, ky_q, kx_q} <= {oy_d, ox_d, g_d, ic_d, ky_d, kx_d};
        {wr_lane_q, wr_base_q}              <= {wr_lane_d, wr_base_d};

        // S0 -> S1: register addresses (cuts counter -> RAM address path)
        s1_first_q    <= (kx_q == 0) && (ky_q == 0) && (ic_q == 0);
        s1_last_q     <= last_tap;
        s1_pad_q      <= pad;
        s1_g_q        <= g_q;
        s1_base_q     <= base_addr;
        s1_src_addr_q <= src_addr;
        s1_w_addr_q   <= w_addr;
        // S1 -> S2 (memories read on the same edge)
        s2_first_q <= s1_first_q;
        s2_last_q  <= s1_last_q;
        s2_pad_q   <= s1_pad_q;
        s2_g_q     <= s1_g_q;
        s2_base_q  <= s1_base_q;
        // S2 -> S3
        s3_first_q <= s2_first_q;
        s3_last_q  <= s2_last_q;
        s3_base_q  <= s2_base_q;
        s4_base_q  <= s3_base_q;
    end

    // ---------------- lanes: multiply, accumulate, requantize ----------------
    generate for (p = 0; p < PAR; p = p + 1) begin : g_lane
        reg signed [IN_BITS+8:0] prod_q;
        // use_dsp "no": on UltraScale+ (DSP48E2) Vivado 2026.1 folds this accumulator and
        // its "first tap loads the bias" select into a dynamic-OPMODE DSP, and the
        // netlist computes wrong sums (layers 1-3 post-synth on ZU3EG: 1590/6272,
        // 629/3136, 113/1024 match; xc7z020 was fine). The multiplier stays in a DSP.
        (* use_dsp = "no" *) reg signed [31:0] bias_q, acc_q;
        reg [ACT_BITS-1:0]       result_q;
        wire [$clog2(C_OUT)-1:0] bias_idx = s2_g_q * PAR + p;          // exact width: 1-entry-per-channel ROM
        wire signed [31:0] acc_sum   = (s3_first_q ? bias_q : acc_q) + prod_q;
        wire signed [31:0] acc_round = (acc_q + (32'sd1 <<< (SHIFT - 1))) >>> SHIFT;       // from finished sum
        assign lane_result[ACT_BITS*p +: ACT_BITS] = result_q;
        always @(posedge clk) begin
            prod_q <= s2_pad_q ? 0 : $signed({1'b0, src_rd_data}) * $signed(w_data[8*p +: 8]);   // S2
            bias_q <= bias_rom[bias_idx];                                                         // S2
            if (s3_valid_q) acc_q <= acc_sum;                                                     // S3
            if (s4_valid_q)                                                                       // S4
                result_q <= (acc_round < 0) ? 0 : (acc_round > ACT_MAX) ? ACT_MAX : acc_round[ACT_BITS-1:0];
        end
    end endgenerate

endmodule
