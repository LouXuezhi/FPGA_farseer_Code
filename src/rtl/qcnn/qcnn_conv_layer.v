// qcnn_conv_layer: 3x3 conv (pad 1) + requant + ReLU/unsigned clamp, one frame at a time.
//
// Datapath: one multiply-accumulate per cycle, 3-stage pipeline
//   S1 index -> S2 BRAM/ROM read + multiply -> S3 accumulate / write
// Frame handshake with neighbour stages:
//   src_valid : previous stage holds a frame      src_taken : pulse, frame consumed
//   dst_valid : this stage holds a frame          dst_taken : pulse from next stage
// Feature map address = (c * H + y) * H + x.
`timescale 1ns / 1ps

module qcnn_conv_layer #(
    parameter C_IN        = 1,
    parameter C_OUT       = 8,
    parameter H_IN        = 56,
    parameter H_OUT       = 28,
    parameter STRIDE      = 2,
    parameter IN_BITS     = 8,               // unsigned input activation width
    parameter ACT_BITS    = 8,               // unsigned output activation width
    parameter SHIFT       = 8,
    parameter WEIGHT_FILE = "l0_weight.mem",
    parameter BIAS_FILE   = "l0_bias.mem"
) (
    input  wire                                 clk,
    input  wire                                 rst,
    // source feature map (read port of previous stage)
    input  wire                                 src_valid,
    output reg                                  src_taken,
    output reg  [$clog2(C_IN*H_IN*H_IN)-1:0]    src_rd_addr,
    input  wire [IN_BITS-1:0]                   src_rd_data,
    // destination feature map (read by next stage)
    output wire                                 dst_valid,
    input  wire                                 dst_taken,
    input  wire [$clog2(C_OUT*H_OUT*H_OUT)-1:0] dst_rd_addr,
    output wire [ACT_BITS-1:0]                  dst_rd_data
);

    localparam N_W     = C_OUT * C_IN * 9;
    localparam N_OUT   = C_OUT * H_OUT * H_OUT;
    localparam ACT_MAX = (1 << ACT_BITS) - 1;
    localparam ST_IDLE = 2'd0, ST_RUN = 2'd1, ST_FLUSH = 2'd2, ST_DONE = 2'd3;

    // ---------------- registers ----------------
    reg [1:0]  state_q, state_d;
    reg        dst_valid_q, dst_valid_d;
    reg [7:0]  oc_q, oc_d, oy_q, oy_d, ox_q, ox_d, ic_q, ic_d;     // loop counters
    reg [1:0]  ky_q, ky_d, kx_q, kx_d;
    // S1 -> S2
    reg        s2_valid_q, s2_first_q, s2_last_q, s2_pad_q;
    reg [7:0]  s2_oc_q;
    reg [$clog2(N_OUT)-1:0] s2_out_addr_q;
    // S2 -> S3
    reg        s3_valid_q, s3_first_q, s3_last_q;
    reg [7:0]  s3_oc_q;
    reg [$clog2(N_OUT)-1:0] s3_out_addr_q;
    reg signed [IN_BITS+8:0] s3_prod_q;
    reg signed [31:0] acc_q, acc_d;

    // ---------------- memories ----------------
    reg  [$clog2(N_W)-1:0]   w_rd_addr;
    reg  [$clog2(N_OUT)-1:0] out_addr;
    wire [7:0]               w_data;
    reg  [ACT_BITS-1:0]      act_out;
    reg  signed [31:0]       bias_rom [0:C_OUT-1];      // small, distributed
    initial $readmemh(BIAS_FILE, bias_rom);

    qcnn_sync_rom #(.DEPTH(N_W), .WIDTH(8), .INIT_FILE(WEIGHT_FILE)) u_weight_rom (
        .clk(clk), .rd_addr(w_rd_addr), .rd_data(w_data)
    );

    qcnn_sdp_ram #(.DEPTH(N_OUT), .WIDTH(ACT_BITS)) u_out_ram (   // S3 writes one value per pixel
        .clk(clk), .wr_en(s3_valid_q && s3_last_q), .wr_addr(s3_out_addr_q), .wr_data(act_out),
        .rd_addr(dst_rd_addr), .rd_data(dst_rd_data)
    );

    assign dst_valid = dst_valid_q;

    // ---------------- S1: loop control (combinational next state) ----------------
    reg        last_term, last_pixel, pad;
    reg signed [9:0] iy, ix;
    always @* begin
        state_d     = state_q;
        dst_valid_d = dst_valid_q & ~dst_taken;
        {oc_d, oy_d, ox_d, ic_d, ky_d, kx_d} = {oc_q, oy_q, ox_q, ic_q, ky_q, kx_q};
        src_taken   = 1'b0;

        last_term   = (kx_q == 2) && (ky_q == 2) && (ic_q == C_IN - 1);
        last_pixel  = (ox_q == H_OUT - 1) && (oy_q == H_OUT - 1) && (oc_q == C_OUT - 1);
        iy          = $signed({2'b0, oy_q}) * STRIDE + $signed({1'b0, ky_q}) - 1;  // keep signed: pad < 0
        ix          = $signed({2'b0, ox_q}) * STRIDE + $signed({1'b0, kx_q}) - 1;
        pad         = (iy < 0) || (iy >= H_IN) || (ix < 0) || (ix >= H_IN);
        src_rd_addr = (ic_q * H_IN + (pad ? 0 : iy)) * H_IN + (pad ? 0 : ix);
        w_rd_addr   = ((oc_q * C_IN + ic_q) * 3 + ky_q) * 3 + kx_q;
        out_addr    = (oc_q * H_OUT + oy_q) * H_OUT + ox_q;

        case (state_q)
            ST_IDLE: if (src_valid && !dst_valid_q) begin
                state_d = ST_RUN;
                {oc_d, oy_d, ox_d, ic_d, ky_d, kx_d} = 0;
            end
            ST_RUN: begin
                // kx -> ky -> ic -> ox -> oy -> oc
                kx_d = kx_q + 1;
                if (kx_q == 2) begin
                    kx_d = 0; ky_d = ky_q + 1;
                    if (ky_q == 2) begin
                        ky_d = 0; ic_d = ic_q + 1;
                        if (ic_q == C_IN - 1) begin
                            ic_d = 0; ox_d = ox_q + 1;
                            if (ox_q == H_OUT - 1) begin
                                ox_d = 0; oy_d = oy_q + 1;
                                if (oy_q == H_OUT - 1) begin
                                    oy_d = 0; oc_d = oc_q + 1;
                                end
                            end
                        end
                    end
                end
                if (last_term && last_pixel) state_d = ST_FLUSH;
            end
            ST_FLUSH: if (!s2_valid_q && !s3_valid_q) state_d = ST_DONE;
            ST_DONE: begin
                src_taken   = 1'b1;
                dst_valid_d = 1'b1;
                state_d     = ST_IDLE;
            end
        endcase
    end

    // ---------------- S3: accumulate, requantize ----------------
    reg signed [31:0] acc_sum, acc_round;
    always @* begin
        acc_sum   = (s3_first_q ? bias_rom[s3_oc_q] : acc_q) + s3_prod_q;
        acc_round = (acc_sum + (32'sd1 <<< (SHIFT - 1))) >>> SHIFT;
        act_out   = (acc_round < 0) ? 0 : (acc_round > ACT_MAX) ? ACT_MAX : acc_round[ACT_BITS-1:0];
        acc_d     = s3_valid_q ? acc_sum : acc_q;
    end

    // ---------------- sequential ----------------
    // FSM/counters take *_d; pipeline registers are written directly
    always @(posedge clk) begin
        if (rst) begin
            state_q     <= ST_IDLE;
            dst_valid_q <= 1'b0;
            s2_valid_q  <= 1'b0;
            s3_valid_q  <= 1'b0;
        end else begin
            state_q     <= state_d;
            dst_valid_q <= dst_valid_d;
            s2_valid_q  <= (state_q == ST_RUN);
            s3_valid_q  <= s2_valid_q;
        end
        {oc_q, oy_q, ox_q, ic_q, ky_q, kx_q} <= {oc_d, oy_d, ox_d, ic_d, ky_d, kx_d};
        acc_q <= acc_d;

        // S1 -> S2 (memories are read on the same edge)
        s2_first_q    <= (kx_q == 0) && (ky_q == 0) && (ic_q == 0);
        s2_last_q     <= last_term;
        s2_pad_q      <= pad;
        s2_oc_q       <= oc_q;
        s2_out_addr_q <= out_addr;

        // S2 -> S3 (src_rd_data and w_data valid here)
        s3_first_q    <= s2_first_q;
        s3_last_q     <= s2_last_q;
        s3_oc_q       <= s2_oc_q;
        s3_out_addr_q <= s2_out_addr_q;
        s3_prod_q     <= s2_pad_q ? 0 : $signed({1'b0, src_rd_data}) * $signed(w_data);
    end

endmodule
