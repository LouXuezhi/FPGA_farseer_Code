// FZ3B + OV9281 capture top.
// D-PHY RX (2 lanes) -> csi2_rx_lite -> 16->64 packer -> async FIFO -> AXIS -> VDMA S2MM -> DDR (HP0)
// AXI-lite register block on HPM0 for control and diagnostics.
`timescale 1ns/1ps
module fz3b_cam_top (
    input  wire       pl_ref_clk,     // 25 MHz, D4
    output wire       cam_mclk,       // L7
    output wire       cam_rst_n,      // U9
    output wire       cam_pwdn_n,     // Y8
    input  wire       clk_rxp,
    input  wire       clk_rxn,
    input  wire [1:0] data_rxp,
    input  wire [1:0] data_rxn
);
    // ---------------- block design (PS + VDMA + interconnect)
    wire        aclk, aresetn;
    wire [31:0] r_awaddr, r_araddr, r_wdata, r_rdata;
    wire [3:0]  r_wstrb;
    wire [1:0]  r_bresp, r_rresp;
    wire        r_awvalid, r_awready, r_wvalid, r_wready, r_bvalid, r_bready;
    wire        r_arvalid, r_arready, r_rvalid, r_rready;
    wire [63:0] vs_tdata;
    wire        vs_tvalid, vs_tready, vs_tlast;
    wire [0:0]  vs_tuser;

    cam_bd_wrapper bd (
        .aclk(aclk), .aresetn(aresetn),
        .M_REGS_awaddr(r_awaddr), .M_REGS_awprot(), .M_REGS_awvalid(r_awvalid), .M_REGS_awready(r_awready),
        .M_REGS_wdata(r_wdata), .M_REGS_wstrb(r_wstrb), .M_REGS_wvalid(r_wvalid), .M_REGS_wready(r_wready),
        .M_REGS_bresp(r_bresp), .M_REGS_bvalid(r_bvalid), .M_REGS_bready(r_bready),
        .M_REGS_araddr(r_araddr), .M_REGS_arprot(), .M_REGS_arvalid(r_arvalid), .M_REGS_arready(r_arready),
        .M_REGS_rdata(r_rdata), .M_REGS_rresp(r_rresp), .M_REGS_rvalid(r_rvalid), .M_REGS_rready(r_rready),
        .S_VIDEO_tdata(vs_tdata), .S_VIDEO_tkeep(8'hff), .S_VIDEO_tlast(vs_tlast), .S_VIDEO_tuser(vs_tuser),
        .S_VIDEO_tvalid(vs_tvalid), .S_VIDEO_tready(vs_tready)
    );

    // ---------------- clocks from the 25 MHz board oscillator
    wire refclk_ibuf, mmcm_fb, clk200_u, clk24_u, clk200, clk24, mmcm_locked;
    IBUF ib_ref (.I(pl_ref_clk), .O(refclk_ibuf));
    MMCME4_ADV #(
        .CLKIN1_PERIOD(40.0), .DIVCLK_DIVIDE(1), .CLKFBOUT_MULT_F(48.0),   // VCO 1200 MHz
        .CLKOUT0_DIVIDE_F(6.0),                                             // 200 MHz
        .CLKOUT1_DIVIDE(50)                                                 // 24 MHz
    ) mmcm (
        .CLKIN1(refclk_ibuf), .CLKIN2(1'b0), .CLKINSEL(1'b1), .CLKFBIN(mmcm_fb), .CLKFBOUT(mmcm_fb),
        .CLKOUT0(clk200_u), .CLKOUT1(clk24_u), .LOCKED(mmcm_locked), .RST(1'b0), .PWRDWN(1'b0),
        .DADDR(7'd0), .DCLK(1'b0), .DEN(1'b0), .DI(16'd0), .DWE(1'b0), .DO(), .DRDY(),
        .PSCLK(1'b0), .PSEN(1'b0), .PSINCDEC(1'b0), .PSDONE(),
        .CDDCREQ(1'b0), .CDDCDONE(), .CLKFBOUTB(), .CLKOUT0B(), .CLKOUT1B(), .CLKOUT2(), .CLKOUT2B(),
        .CLKOUT3(), .CLKOUT3B(), .CLKOUT4(), .CLKOUT5(), .CLKOUT6(), .CLKFBSTOPPED(), .CLKINSTOPPED()
    );
    BUFG bg200 (.I(clk200_u), .O(clk200));
    BUFG bg24  (.I(clk24_u),  .O(clk24));

    // ---------------- control registers (aclk domain)
    wire [31:0] ctrl;          // [0] dphy_rst [1] cam_rst_n [2] cam_pwdn_n [3] mclk_en [4] ecc_check_en [5] stream_en [6] lane_swap [7] one_lane [8] lane0_only [31:16] lane0 bytes/line
    wire        mclk_en_a = ctrl[3];
    assign cam_rst_n  = ctrl[1];
    assign cam_pwdn_n = ctrl[2];

    // MCLK forwarding through ODDR, gated by a synchronized enable
    reg [1:0] mclk_en_s;
    always @(posedge clk24) mclk_en_s <= {mclk_en_s[0], mclk_en_a};
    ODDRE1 #(.SRVAL(1'b0)) oddr_mclk (.C(clk24), .D1(mclk_en_s[1]), .D2(1'b0), .SR(1'b0), .Q(cam_mclk));

    // ---------------- D-PHY RX (component mode, polarity/bit-order auto-detect per lane)
    wire [31:0] ctrl2;         // [8:0] tap lane0  [24:16] tap lane1  [31] load toggle  [30] term_force
    wire dphy_rst = ctrl[0] | ~mmcm_locked;
    wire byteclk, brst;
    wire [7:0] d0, d1;
    wire v0, v1, a0, a1;
    wire [1:0] lp0, lp1; wire [3:0] info0, info1; wire clk_lpp, clk_lpn;
    dphy_comp_rx dphy (
        .clk_rxp(clk_rxp), .clk_rxn(clk_rxn), .data_rxp(data_rxp), .data_rxn(data_rxn),
        .rst(dphy_rst), .tap0(ctrl2[8:0]), .tap1(ctrl2[24:16]), .tap_load_tgl(ctrl2[31]), .term_force(ctrl2[30]),
        .byteclk(byteclk), .brst(brst), .d0(d0), .d1(d1), .v0(v0), .v1(v1), .a0(a0), .a1(a1),
        .lp0(lp0), .lp1(lp1), .info0(info0), .info1(info1), .clk_lp_p(clk_lpp), .clk_lp_n(clk_lpn));
    wire esot0 = 1'b0, esot1 = 1'b0, esync0 = info0[2], esync1 = info1[2];
    wire init_done = 1'b1, cl_active = 1'b1;
    wire cl_stop = 1'b0;
    wire dl0_stop = (lp0 == 2'b11), dl1_stop = (lp1 == 2'b11);
    reg [15:0] c_ok0, c_ok1, c_ns0, c_ns1; reg [3:0] pr0, pr1;
    always @(posedge byteclk) begin
        if (brst) begin c_ok0 <= 0; c_ok1 <= 0; c_ns0 <= 0; c_ns1 <= 0; end
        else begin
            if (info0[3]) begin c_ok0 <= c_ok0 + 1'b1; pr0 <= {2'b00, info0[1:0]}; end
            if (info1[3]) begin c_ok1 <= c_ok1 + 1'b1; pr1 <= {2'b00, info1[1:0]}; end
            if (info0[2]) c_ns0 <= c_ns0 + 1'b1;
            if (info1[2]) c_ns1 <= c_ns1 + 1'b1;
        end
    end
    // ---------------- byte clock domain
    reg [1:0] ecc_en_s, stream_en_s;
    always @(posedge byteclk) begin ecc_en_s <= {ecc_en_s[0], ctrl[4]}; stream_en_s <= {stream_en_s[0], ctrl[5]}; end

    // full 2-lane parser
    wire [15:0] f_data; wire f_valid, f_last, f_first, f_fs, f_fe, f_pkt, ecc_p, f_bad, crc_ok_p, crc_bad_p;
    wire [5:0] f_dt; wire [15:0] pl_wc;
    reg [1:0] swap_s; always @(posedge byteclk) swap_s <= {swap_s[0], ctrl[6]};
    reg [1:0] onel_s; always @(posedge byteclk) onel_s <= {onel_s[0], ctrl[7]};
    reg [1:0] l0o_s;  always @(posedge byteclk) l0o_s  <= {l0o_s[0],  ctrl[8]};
    reg [15:0] lane_bytes; always @(posedge byteclk) lane_bytes <= ctrl[31:16];
    wire sw = swap_s[1];
    csi2_rx_lite csi (
        .clk(byteclk), .rst(brst), .ecc_check_en(ecc_en_s[1]), .one_lane(onel_s[1]),
        .l0_data(sw ? d1 : d0), .l0_valid(sw ? v1 : v0), .l0_active(sw ? a1 : a0),
        .l1_data(sw ? d0 : d1), .l1_valid(sw ? v0 : v1), .l1_active(sw ? a0 : a1),
        .pl_data(f_data), .pl_valid(f_valid), .pl_last(f_last), .pl_first(f_first),
        .pl_dt(f_dt), .pl_wc(pl_wc),
        .fs_pulse(f_fs), .fe_pulse(f_fe), .pkt_pulse(f_pkt), .ecc_pulse(ecc_p), .bad_pulse(f_bad),
        .crc_ok_pulse(crc_ok_p), .crc_bad_pulse(crc_bad_p)
    );
    // lane-0-only parser (degraded mode: half the bytes of each line)
    wire [15:0] o_data; wire o_valid, o_last, o_first, o_fs, o_fe, o_pkt, o_bad, o_short; wire [5:0] o_dt;
    csi2_l0_only csi0 (
        .clk(byteclk), .rst(brst), .lane_bytes(lane_bytes), .d(sw ? d1 : d0), .v(sw ? v1 : v0), .a(sw ? a1 : a0),
        .pl_data(o_data), .pl_valid(o_valid), .pl_last(o_last), .pl_first(o_first), .pl_dt(o_dt),
        .fs_pulse(o_fs), .fe_pulse(o_fe), .pkt_pulse(o_pkt), .bad_pulse(o_bad), .short_pulse(o_short)
    );
    wire l0o = l0o_s[1];
    wire [15:0] pl_data  = l0o ? o_data  : f_data;
    wire        pl_valid = l0o ? o_valid : f_valid;
    wire        pl_last  = l0o ? o_last  : f_last;
    wire        pl_first = l0o ? o_first : f_first;
    wire [5:0]  pl_dt    = l0o ? o_dt    : f_dt;
    wire        fs_p     = l0o ? o_fs    : f_fs;
    wire        fe_p     = l0o ? o_fe    : f_fe;
    wire        pkt_p    = l0o ? o_pkt   : f_pkt;
    wire        bad_p    = l0o ? o_bad   : f_bad;

    // 16 -> 64 packer, SOF flag on first word after FS, EOL on last word of a line
    // Line payload must be a multiple of 8 bytes (1280 px RAW10 = 1600 B, RAW8 = 1280 B).
    reg [63:0] pk; reg [1:0] pk_n; reg sof_pend, word_sof;
    reg [65:0] fifo_din; reg fifo_wr;
    wire [1:0] beat_n = pl_first ? 2'd0 : pk_n;
    wire       cur_sof = pl_first ? sof_pend : word_sof;
    always @(posedge byteclk) begin
        fifo_wr <= 1'b0;
        if (brst) begin pk_n <= 0; sof_pend <= 1'b0; word_sof <= 1'b0; end
        else begin
            if (fs_p) sof_pend <= 1'b1;
            if (pl_valid && stream_en_s[1]) begin
                pk   <= {pl_data, pk[63:16]};
                pk_n <= beat_n + 1'b1;
                if (pl_first) sof_pend <= 1'b0;
                word_sof <= cur_sof;
                if (beat_n == 2'd3 || pl_last) begin
                    fifo_din <= {pl_last, cur_sof, pl_data, pk[63:16]};
                    fifo_wr  <= 1'b1;
                    word_sof <= 1'b0;
                end
            end
        end
    end

    wire fifo_full, fifo_empty;
    wire [65:0] fifo_dout;
    xpm_fifo_async #(
        .FIFO_MEMORY_TYPE("block"), .FIFO_WRITE_DEPTH(2048), .WRITE_DATA_WIDTH(66), .READ_DATA_WIDTH(66),
        .READ_MODE("fwft"), .FIFO_READ_LATENCY(0), .CDC_SYNC_STAGES(2), .ECC_MODE("no_ecc"),
        .RELATED_CLOCKS(0), .USE_ADV_FEATURES("0000"), .WAKEUP_TIME(0), .FULL_RESET_VALUE(0),
        .PROG_FULL_THRESH(10), .PROG_EMPTY_THRESH(10), .RD_DATA_COUNT_WIDTH(1), .WR_DATA_COUNT_WIDTH(1)
    ) vfifo (
        .rst(brst), .wr_clk(byteclk), .wr_en(fifo_wr & ~fifo_full), .din(fifo_din), .full(fifo_full),
        .rd_clk(aclk), .rd_en(vs_tready & ~fifo_empty), .dout(fifo_dout), .empty(fifo_empty),
        .sleep(1'b0), .injectsbiterr(1'b0), .injectdbiterr(1'b0),
        .overflow(), .wr_rst_busy(), .almost_full(), .wr_ack(), .prog_full(), .wr_data_count(),
        .underflow(), .rd_rst_busy(), .almost_empty(), .data_valid(), .prog_empty(), .rd_data_count(),
        .sbiterr(), .dbiterr()
    );
    assign vs_tdata  = fifo_dout[63:0];
    assign vs_tuser  = fifo_dout[64];
    assign vs_tlast  = fifo_dout[65];
    assign vs_tvalid = ~fifo_empty;

    // ---------------- diagnostics counters (byte clock), snapshotted into aclk domain
    reg [31:0] c_crc_ok, c_crc_bad, c_fs, c_fe, c_lines, c_pkt, c_ecc, c_bad, c_ovf, c_esot, c_burst0, c_burst1, c_bclk, c_lpf_last, c_lpf_cnt;
    reg [15:0] last_wc; reg [5:0] last_dt; reg a0_d, a1_d;
    reg [7:0] snif0 [0:3]; reg [7:0] snif1 [0:3]; reg [2:0] sn0, sn1;
    reg [15:0] bl0, bl1, bl0_last, bl1_last, bl0_max, bl1_max;
    always @(posedge byteclk) begin
        if (brst) begin
            c_crc_ok <= 0; c_crc_bad <= 0; c_fs <= 0; c_fe <= 0; c_lines <= 0; c_pkt <= 0; c_ecc <= 0; c_bad <= 0; c_ovf <= 0; c_esot <= 0;
            c_burst0 <= 0; c_burst1 <= 0; c_bclk <= 0; bl0_max <= 0; bl1_max <= 0; c_lpf_last <= 0; c_lpf_cnt <= 0; sn0 <= 0; sn1 <= 0;
        end else begin
            c_bclk <= c_bclk + 1;
            a0_d <= a0; a1_d <= a1;
            if (a0 & ~a0_d) begin c_burst0 <= c_burst0 + 1; sn0 <= 0; bl0 <= 0; end
            else if (v0) bl0 <= bl0 + 1'b1;
            if (a1 & ~a1_d) begin c_burst1 <= c_burst1 + 1; sn1 <= 0; bl1 <= 0; end
            else if (v1) bl1 <= bl1 + 1'b1;
            if (~a0 & a0_d) begin bl0_last <= bl0; if (bl0 > bl0_max) bl0_max <= bl0; end
            if (~a1 & a1_d) begin bl1_last <= bl1; if (bl1 > bl1_max) bl1_max <= bl1; end
            if (v0 && sn0 < 4) begin snif0[sn0[1:0]] <= d0; sn0 <= sn0 + 1'b1; end
            if (v1 && sn1 < 4) begin snif1[sn1[1:0]] <= d1; sn1 <= sn1 + 1'b1; end
            if (fs_p) begin c_fs <= c_fs + 1; c_lpf_cnt <= 0; end
            if (fe_p) begin c_fe <= c_fe + 1; c_lpf_last <= c_lpf_cnt; end
            if (pl_valid && pl_last) begin c_lines <= c_lines + 1; c_lpf_cnt <= c_lpf_cnt + 1; last_wc <= l0o ? lane_bytes : pl_wc; last_dt <= pl_dt; end
            if (pkt_p) c_pkt <= c_pkt + 1;
            if (ecc_p) c_ecc <= c_ecc + 1;
            if (crc_ok_p) c_crc_ok <= c_crc_ok + 1;
            if (crc_bad_p) c_crc_bad <= c_crc_bad + 1;
            if (bad_p) c_bad <= c_bad + 1;
            if (fifo_wr && fifo_full) c_ovf <= c_ovf + 1;
            if (esot0 | esot1 | esync0 | esync1) c_esot <= c_esot + 1;
        end
    end
    // snapshot every 256 byte clocks
    reg [7:0] snap_div; reg snap_tgl;
    reg [31:0] sh [0:23];
    always @(posedge byteclk) begin
        snap_div <= snap_div + 1'b1;
        if (snap_div == 8'd0) begin
            snap_tgl <= ~snap_tgl;
            sh[0] <= c_fs; sh[1] <= c_fe; sh[2] <= c_lines; sh[3] <= c_pkt;
            sh[4] <= c_ecc; sh[5] <= c_bad; sh[6] <= c_ovf; sh[7] <= c_esot;
            sh[8] <= c_burst0; sh[9] <= c_burst1; sh[10] <= c_bclk; sh[11] <= c_lpf_last;
            sh[12] <= {10'd0, last_dt, last_wc};
            sh[13] <= {snif0[3], snif0[2], snif0[1], snif0[0]};
            sh[14] <= {snif1[3], snif1[2], snif1[1], snif1[0]};
            sh[15] <= {24'd0, a1, a0, dl1_stop, dl0_stop, cl_stop, cl_active, init_done, 1'b1};
            sh[16] <= {bl0_max, bl0_last}; sh[17] <= {bl1_max, bl1_last};
            sh[18] <= {c_ns0, c_ok0}; sh[19] <= {c_ns1, c_ok1}; sh[20] <= c_crc_ok; sh[21] <= c_crc_bad;
        end
    end
    reg [2:0] tgl_s; reg [31:0] st [0:23]; integer k;
    always @(posedge aclk) begin
        tgl_s <= {tgl_s[1:0], snap_tgl};
        if (tgl_s[2] ^ tgl_s[1]) for (k = 0; k < 24; k = k + 1) st[k] <= sh[k];
    end
    // aclk-side frame/heartbeat counter for the byte clock rate
    reg [31:0] aclk_cnt; always @(posedge aclk) aclk_cnt <= aclk_cnt + 1;
    reg [1:0] lock_s; always @(posedge aclk) lock_s <= {lock_s[0], mmcm_locked};

    cam_regs regs (
        .aclk(aclk), .aresetn(aresetn),
        .awaddr(r_awaddr[7:0]), .awvalid(r_awvalid), .awready(r_awready),
        .wdata(r_wdata), .wstrb(r_wstrb), .wvalid(r_wvalid), .wready(r_wready),
        .bresp(r_bresp), .bvalid(r_bvalid), .bready(r_bready),
        .araddr(r_araddr[7:0]), .arvalid(r_arvalid), .arready(r_arready),
        .rdata(r_rdata), .rresp(r_rresp), .rvalid(r_rvalid), .rready(r_rready),
        .ctrl(ctrl),
        .s0(st[0]), .s1(st[1]), .s2(st[2]), .s3(st[3]), .s4(st[4]), .s5(st[5]), .s6(st[6]), .s7(st[7]),
        .s8(st[8]), .s9(st[9]), .s10(st[10]), .s11(st[11]), .s12(st[12]), .s13(st[13]), .s14(st[14]), .s15(st[15]),
        .s16(aclk_cnt), .s17({30'd0, lock_s[1], 1'b1}), .s18(st[16]), .s19(st[17]), .s20(st[18]), .s21(st[19]), .s22({16'd0, 4'd0, pr1, 4'd0, pr0}), .s23({26'd0, lp1, lp0, clk_lpp, clk_lpn}), .s24(st[20]), .s25(st[21]), .ctrl2(ctrl2)
    );
endmodule
