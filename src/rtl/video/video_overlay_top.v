// video_overlay_top: the whole screen, composed in the PL, on the ZynqMP
// DisplayPort live video input (PS DPSUB, PG201 "live video").
//
//   aclk (100 MHz)                                   vclk (DP pixel clock)
//   disp_regs (AXI-Lite) ── commit/ack toggles ──►  vid_timing (run-time mode)
//   line_fetch bg  (m_axi,     RGBW8888 background) ─┐
//   line_fetch cam (m_axi_cam, VDMA frame stores)  ──┼► disp_compose ─► dp_live_video_*
//   profile / text / palette (disp_regs)           ──┘
//
// Why the video layer, not the graphics layer: Linux fbdev / DRM uses the
// graphics layer as its primary plane and rewrites its source on every
// commit, and the 4.14 (xilinx-v2018.3) driver cannot select live graphics
// (VID2 MEM|LIVE = NONE). The video layer is left alone by the driver, so
// src/host/dp_live.py switches it to live, the clock to the PL and the
// timing to external, and hides the graphics layer with global alpha.
// Pixel packing: 36-bit pixel1 = 3 x 12-bit components, 8-bit data MSB-aligned.
// COMP_ORDER: 0 = R,G,B  1 = B,G,R  2 = G,B,R in [35:24]/[23:12]/[11:0] (check with CTRL[1]).
`timescale 1ns / 1ps

module video_overlay_top #(
    parameter COMP_ORDER = 0,
    parameter CAM_W      = 384,                  // camera window width, multiple of 128
    parameter HEAT_FILE  = "heat_lut.mem",
    parameter FONT_FILE  = "font12x24.mem"
) (
    (* X_INTERFACE_PARAMETER = "ASSOCIATED_BUSIF s_axi:m_axi:m_axi_cam, ASSOCIATED_RESET aresetn" *)
    input  wire        aclk,
    input  wire        aresetn,
    input  wire [14:0] s_axi_awaddr,
    input  wire        s_axi_awvalid,
    output wire        s_axi_awready,
    input  wire [31:0] s_axi_wdata,
    input  wire [3:0]  s_axi_wstrb,                       // full-word writes only
    input  wire        s_axi_wvalid,
    output wire        s_axi_wready,
    output wire [1:0]  s_axi_bresp,
    output wire        s_axi_bvalid,
    input  wire        s_axi_bready,
    input  wire [14:0] s_axi_araddr,
    input  wire        s_axi_arvalid,
    output wire        s_axi_arready,
    output wire [31:0] s_axi_rdata,
    output wire [1:0]  s_axi_rresp,
    output wire        s_axi_rvalid,
    input  wire        s_axi_rready,
    // background image in DDR (read only)
    output wire [31:0] m_axi_araddr,
    output wire [7:0]  m_axi_arlen,
    output wire [2:0]  m_axi_arsize,
    output wire [1:0]  m_axi_arburst,
    output wire [3:0]  m_axi_arcache,
    output wire [2:0]  m_axi_arprot,
    output wire        m_axi_arvalid,
    input  wire        m_axi_arready,
    input  wire [63:0] m_axi_rdata,
    input  wire [1:0]  m_axi_rresp,
    input  wire        m_axi_rlast,
    input  wire        m_axi_rvalid,
    output wire        m_axi_rready,
    // camera frame stores in DDR (read only)
    output wire [31:0] m_axi_cam_araddr,
    output wire [7:0]  m_axi_cam_arlen,
    output wire [2:0]  m_axi_cam_arsize,
    output wire [1:0]  m_axi_cam_arburst,
    output wire [3:0]  m_axi_cam_arcache,
    output wire [2:0]  m_axi_cam_arprot,
    output wire        m_axi_cam_arvalid,
    input  wire        m_axi_cam_arready,
    input  wire [63:0] m_axi_cam_rdata,
    input  wire [1:0]  m_axi_cam_rresp,
    input  wire        m_axi_cam_rlast,
    input  wire        m_axi_cam_rvalid,
    output wire        m_axi_cam_rready,
    input  wire [5:0]  cam_frame_ptr,                     // VDMA s2mm_frame_ptr_out (aclk)

    input  wire        vclk,
    input  wire        vclk_locked,                       // 1 if vclk has no lock signal
    (* X_INTERFACE_PARAMETER = "FREQ_HZ 74250000" *)
    output wire        dp_video_in_clk,
    output wire        dp_live_video_in_vsync,
    output wire        dp_live_video_in_hsync,
    output wire        dp_live_video_in_de,
    output wire [35:0] dp_live_video_in_pixel1
);

    // ---------------- video reset: aresetn and lock, synchronized to vclk ----------------
    (* ASYNC_REG = "TRUE" *) reg [2:0] vrst_s = 3'b111;
    always @(posedge vclk or negedge aresetn)
        if (!aresetn) vrst_s <= 3'b111;
        else          vrst_s <= {vrst_s[1:0], ~vclk_locked};
    wire vrst = vrst_s[2];

    wire [15:0] ctrl;
    wire [31:0] htim0, htim1, vtim0, vtim1, campos, camsize, bgsize, bg_stat, cam_stat;
    wire [8*24-1:0] pal;
    wire [31:0] bg_addr, cam_addr0, cam_addr1, cam_addr2, cam_addr3;
    wire [15:0] bg_pitch, cam_pitch;
    wire [2:0]  cam_nfs;
    wire        hs, vs, de, frame_start, vblank_start;
    wire [11:0] x, y;
    wire [7:0]  prof_addr, prof_data;
    wire [11:0] text_addr, text_data;

    disp_regs u_regs (
        .aclk(aclk), .aresetn(aresetn),
        .s_axi_awaddr(s_axi_awaddr), .s_axi_awvalid(s_axi_awvalid), .s_axi_awready(s_axi_awready),
        .s_axi_wdata(s_axi_wdata), .s_axi_wvalid(s_axi_wvalid), .s_axi_wready(s_axi_wready),
        .s_axi_bresp(s_axi_bresp), .s_axi_bvalid(s_axi_bvalid), .s_axi_bready(s_axi_bready),
        .s_axi_araddr(s_axi_araddr), .s_axi_arvalid(s_axi_arvalid), .s_axi_arready(s_axi_arready),
        .s_axi_rdata(s_axi_rdata), .s_axi_rresp(s_axi_rresp), .s_axi_rvalid(s_axi_rvalid), .s_axi_rready(s_axi_rready),
        .bg_addr(bg_addr), .bg_pitch(bg_pitch),
        .cam_addr0(cam_addr0), .cam_addr1(cam_addr1), .cam_addr2(cam_addr2), .cam_addr3(cam_addr3),
        .cam_pitch(cam_pitch), .cam_nfs(cam_nfs), .bg_stat(bg_stat), .cam_stat(cam_stat),
        .vclk(vclk), .vrst(vrst), .vblank_start(vblank_start),
        .ap_ctrl(ctrl), .ap_htim0(htim0), .ap_htim1(htim1), .ap_vtim0(vtim0), .ap_vtim1(vtim1),
        .ap_campos(campos), .ap_camsize(camsize), .ap_bgsize(bgsize), .ap_pal(pal),
        .prof_raddr(prof_addr), .prof_rdata(prof_data), .text_raddr(text_addr), .text_rdata(text_data)
    );

    vid_timing u_timing (
        .clk(vclk), .rst(vrst),
        .h_active(htim0[11:0]), .h_total(htim0[27:16]), .hs_start(htim1[11:0]), .hs_end(htim1[27:16]),
        .v_active(vtim0[11:0]), .v_total(vtim0[27:16]), .vs_start(vtim1[11:0]), .vs_end(vtim1[27:16]),
        .hs_pos(htim1[31]), .vs_pos(vtim1[31]),
        .hsync(hs), .vsync(vs), .de(de), .x(x), .y(y), .frame_start(frame_start), .vblank_start(vblank_start)
    );

    wire        bg_bank, cam_bank;
    wire [9:0]  bg_word;
    wire [5:0]  cam_word;
    wire [63:0] bg_data, cam_data;
    wire [4:0]  bg_bursts = (bg_pitch[15:7] > 31) ? 5'd31 : bg_pitch[11:7];   // whole row, pitch / 128

    line_fetch #(.WORDS(1024)) u_bg (
        .aclk(aclk), .aresetn(aresetn),
        .m_axi_araddr(m_axi_araddr), .m_axi_arlen(m_axi_arlen), .m_axi_arsize(m_axi_arsize),
        .m_axi_arburst(m_axi_arburst), .m_axi_arcache(m_axi_arcache), .m_axi_arprot(m_axi_arprot),
        .m_axi_arvalid(m_axi_arvalid), .m_axi_arready(m_axi_arready),
        .m_axi_rdata(m_axi_rdata), .m_axi_rvalid(m_axi_rvalid), .m_axi_rready(m_axi_rready),
        .addr0(bg_addr), .addr1(bg_addr), .addr2(bg_addr), .addr3(bg_addr),
        .pitch(bg_pitch), .nfs(3'd1), .n_burst(bg_bursts), .frame_ptr(6'd0), .stat(bg_stat),
        .vclk(vclk), .vrst(vrst), .en(ctrl[0]), .x(x), .y(y),
        .h_active(htim0[11:0]), .v_total(vtim0[27:16]), .y0(12'd0), .rows(bgsize[27:16]),
        .rd_bank(bg_bank), .rd_word(bg_word), .rd_data(bg_data)
    );

    line_fetch #(.WORDS(64)) u_cam (
        .aclk(aclk), .aresetn(aresetn),
        .m_axi_araddr(m_axi_cam_araddr), .m_axi_arlen(m_axi_cam_arlen), .m_axi_arsize(m_axi_cam_arsize),
        .m_axi_arburst(m_axi_cam_arburst), .m_axi_arcache(m_axi_cam_arcache), .m_axi_arprot(m_axi_cam_arprot),
        .m_axi_arvalid(m_axi_cam_arvalid), .m_axi_arready(m_axi_cam_arready),
        .m_axi_rdata(m_axi_cam_rdata), .m_axi_rvalid(m_axi_cam_rvalid), .m_axi_rready(m_axi_cam_rready),
        .addr0(cam_addr0), .addr1(cam_addr1), .addr2(cam_addr2), .addr3(cam_addr3),
        .pitch(cam_pitch), .nfs(cam_nfs), .n_burst(CAM_W / 128), .frame_ptr(cam_frame_ptr), .stat(cam_stat),
        .vclk(vclk), .vrst(vrst), .en(ctrl[3]), .x(x), .y(y),
        .h_active(htim0[11:0]), .v_total(vtim0[27:16]), .y0(campos[27:16]), .rows(camsize[27:16]),
        .rd_bank(cam_bank), .rd_word(cam_word), .rd_data(cam_data)
    );

    wire [23:0] rgb;
    disp_compose #(.CAM_W(CAM_W), .HEAT_FILE(HEAT_FILE), .FONT_FILE(FONT_FILE)) u_compose (
        .clk(vclk), .rst(vrst), .de(de), .hs(hs), .vs(vs), .frame_start(frame_start), .x(x), .y(y),
        .ctrl(ctrl), .cam_x0(campos[11:0]), .cam_y0(campos[27:16]), .cam_rows(camsize[27:16]), .pal(pal),
        .bg_w(bgsize[11:0]), .bg_h(bgsize[27:16]),
        .bg_bank(bg_bank), .bg_word(bg_word), .bg_data(bg_data),
        .cam_bank(cam_bank), .cam_word(cam_word), .cam_data(cam_data),
        .prof_addr(prof_addr), .prof_data(prof_data), .text_addr(text_addr), .text_data(text_data),
        .out_de(dp_live_video_in_de), .out_hs(dp_live_video_in_hsync), .out_vs(dp_live_video_in_vsync),
        .out_rgb(rgb)
    );

    wire [7:0]  r = rgb[23:16], g = rgb[15:8], b = rgb[7:0];
    wire [23:0] ordered = (COMP_ORDER == 1) ? {b, g, r} : (COMP_ORDER == 2) ? {g, b, r} : {r, g, b};
    assign dp_live_video_in_pixel1 = {ordered[23:16], 4'd0, ordered[15:8], 4'd0, ordered[7:0], 4'd0};
    assign dp_video_in_clk         = vclk;

endmodule
