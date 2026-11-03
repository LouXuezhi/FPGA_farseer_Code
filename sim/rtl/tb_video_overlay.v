// tb_video_overlay: the screen (video_overlay_top) end to end.
//   AXI-Lite (100 MHz): ID, palette, CTRL, commit (+ SLVERR while pending), the
//   water profile and every text cell (prof.mem / text.mem from build_ui.py);
//   AXI4 read slaves: background (bg_words.mem, RGBW8888) and four VDMA-like
//   camera frame stores (store 1 = cam_raw.mem, others 0xEE so a wrong store shows).
//   Captures one 800x480 frame (reset timing) and one 1280x720 frame (run-time
//   timing, camera off) as raw RGB for disp_ref.py check; checks raster size,
//   frame counter, store choice and that no line fetch was late.
`timescale 1ns / 1ps

module tb_video_overlay;

    reg aclk = 0, vclk = 0, aresetn = 0;
    always #5 aclk = ~aclk;                  // 100 MHz
    always #15 vclk = ~vclk;                 // 33.3 MHz, the 800x480 panel's pixel clock

    reg  [14:0] awaddr = 0, araddr = 0;
    reg  [31:0] wdata = 0;
    reg         awvalid = 0, wvalid = 0, bready = 0, arvalid = 0, rready = 0;
    wire        awready, wready, bvalid, arready, rvalid;
    wire [1:0]  bresp, rresp;
    wire [31:0] rdata;
    wire        de, hs, vs;
    wire [35:0] pix;
    reg  [5:0]  frame_ptr = 6'd2;            // VDMA writing store 2 -> the screen reads store 1

    // ---------------- DDR: background and camera, read-only AXI4 slaves ----------------
    localparam BG_BASE = 32'h7200_0000, CAM_BASE = 32'h7000_0000, CAM_SPAN = 32'h0040_0000;
    reg [31:0] bg_mem  [0:800*480-1];
    reg [7:0]  cam_mem [0:640*800-1];
    function [63:0] ddr_word(input [31:0] a);                // 8 bytes at a (8-aligned)
        integer k; reg [31:0] off;
        begin
            ddr_word = 64'd0;
            if (a >= BG_BASE && a < BG_BASE + 800*480*4) begin
                off = (a - BG_BASE) >> 2;
                ddr_word = {bg_mem[off + 1], bg_mem[off]};
            end else for (k = 0; k < 8; k = k + 1) begin
                off = a + k - CAM_BASE;
                ddr_word[8*k +: 8] = (off >= CAM_SPAN && off < CAM_SPAN + 640*800) ? cam_mem[off - CAM_SPAN] : 8'hEE;
            end
        end
    endfunction

    wire [31:0] bg_araddr, cam_araddr;
    wire [7:0]  bg_arlen, cam_arlen;
    wire [2:0]  bg_arsize, cam_arsize;
    wire [1:0]  bg_arburst, cam_arburst;
    wire        bg_arvalid, cam_arvalid, bg_rready, cam_rready;
    wire        bg_arready, cam_arready, bg_rvalid, cam_rvalid, bg_rlast, cam_rlast;
    wire [63:0] bg_rdata, cam_rdata;
    integer n_error = 0;
    task fail(input [8*64-1:0] msg); begin n_error = n_error + 1; $display("ERROR %0s", msg); end endtask

    tb_axi_rd_slave #(.SEED(1)) u_bg_mem (.clk(aclk), .araddr(bg_araddr), .arlen(bg_arlen), .arsize(bg_arsize),
        .arburst(bg_arburst), .arvalid(bg_arvalid), .arready(bg_arready),
        .rdata(bg_rdata), .rlast(bg_rlast), .rvalid(bg_rvalid), .rready(bg_rready));
    tb_axi_rd_slave #(.SEED(2)) u_cam_mem (.clk(aclk), .araddr(cam_araddr), .arlen(cam_arlen), .arsize(cam_arsize),
        .arburst(cam_arburst), .arvalid(cam_arvalid), .arready(cam_arready),
        .rdata(cam_rdata), .rlast(cam_rlast), .rvalid(cam_rvalid), .rready(cam_rready));

    video_overlay_top dut (
        .aclk(aclk), .aresetn(aresetn),
        .s_axi_awaddr(awaddr), .s_axi_awvalid(awvalid), .s_axi_awready(awready),
        .s_axi_wdata(wdata), .s_axi_wstrb(4'hF), .s_axi_wvalid(wvalid), .s_axi_wready(wready),
        .s_axi_bresp(bresp), .s_axi_bvalid(bvalid), .s_axi_bready(bready),
        .s_axi_araddr(araddr), .s_axi_arvalid(arvalid), .s_axi_arready(arready),
        .s_axi_rdata(rdata), .s_axi_rresp(rresp), .s_axi_rvalid(rvalid), .s_axi_rready(rready),
        .m_axi_araddr(bg_araddr), .m_axi_arlen(bg_arlen), .m_axi_arsize(bg_arsize), .m_axi_arburst(bg_arburst),
        .m_axi_arcache(), .m_axi_arprot(), .m_axi_arvalid(bg_arvalid), .m_axi_arready(bg_arready),
        .m_axi_rdata(bg_rdata), .m_axi_rresp(2'b00), .m_axi_rlast(bg_rlast), .m_axi_rvalid(bg_rvalid),
        .m_axi_rready(bg_rready),
        .m_axi_cam_araddr(cam_araddr), .m_axi_cam_arlen(cam_arlen), .m_axi_cam_arsize(cam_arsize),
        .m_axi_cam_arburst(cam_arburst), .m_axi_cam_arcache(), .m_axi_cam_arprot(),
        .m_axi_cam_arvalid(cam_arvalid), .m_axi_cam_arready(cam_arready),
        .m_axi_cam_rdata(cam_rdata), .m_axi_cam_rresp(2'b00), .m_axi_cam_rlast(cam_rlast),
        .m_axi_cam_rvalid(cam_rvalid), .m_axi_cam_rready(cam_rready), .cam_frame_ptr(frame_ptr),
        .vclk(vclk), .vclk_locked(1'b1), .dp_video_in_clk(),
        .dp_live_video_in_vsync(vs), .dp_live_video_in_hsync(hs), .dp_live_video_in_de(de),
        .dp_live_video_in_pixel1(pix)
    );

    task axi_write(input [14:0] a, input [31:0] dd, output [1:0] resp);
        begin
            @(posedge aclk); awaddr <= a; wdata <= dd; awvalid <= 1; wvalid <= 1; bready <= 1;
            @(posedge aclk); while (!(awready && wready)) @(posedge aclk);
            awvalid <= 0; wvalid <= 0;
            while (!bvalid) @(posedge aclk);
            resp = bresp;
            @(posedge aclk); bready <= 0;
        end
    endtask
    task axi_read(input [14:0] a, output [31:0] dd);
        begin
            @(posedge aclk); araddr <= a; arvalid <= 1; rready <= 1;
            @(posedge aclk); while (!arready) @(posedge aclk);
            arvalid <= 0;
            while (!rvalid) @(posedge aclk);
            dd = rdata;
            @(posedge aclk); rready <= 0;
        end
    endtask

    reg [1:0]  resp;
    reg [31:0] d;
    task commit; begin
        axi_write(15'h0008, 32'h1, resp);
        d = 1; while (d[0]) axi_read(15'h0008, d);
    end endtask

    // ---------------- frame capture (COMP_ORDER 0: R, G, B) ----------------
    integer fd, n_de = 0, n_line = 0, de_q = 0;
    reg     capturing = 0;
    always @(posedge vclk) begin
        if (capturing && de) begin
            $fwrite(fd, "%c%c%c", pix[35:28], pix[23:16], pix[11:4]);
            n_de = n_de + 1;
        end
        if (capturing && de && !de_q) n_line = n_line + 1;
        de_q = de;
    end
    task capture_frame(input [8*32-1:0] name, input integer w, input integer h);
        begin
            @(negedge vs); @(posedge vs);
            while (de !== 1'b0) @(posedge vclk);
            fd = $fopen(name, "wb"); n_de = 0; n_line = 0; capturing = 1;
            @(posedge vs);
            capturing = 0; $fclose(fd);
            if (n_de != w * h || n_line != h) fail("raster size");
            $display("captured %0s: %0d pixels, %0d lines", name, n_de, n_line);
        end
    endtask

    reg [11:0] text_mem [0:4095];
    reg [7:0]  prof_mem [0:255];
    reg [31:0] s_bg, s_cam;
    integer t;
    initial begin
        $readmemh("bg_words.mem", bg_mem);
        $readmemh("cam_raw.mem", cam_mem);
        $readmemh("text.mem", text_mem);
        $readmemh("prof.mem", prof_mem);
        repeat (20) @(posedge aclk); aresetn <= 1; repeat (20) @(posedge aclk);
        axi_read(15'h0000, d); if (d !== 32'h4F564C33) fail("ID");
        // palette = ui_layout.PALETTE
        axi_write(15'h0080, 32'h000000, resp); axi_write(15'h0084, 32'hE6EDF3, resp);
        axi_write(15'h0088, 32'h8B949E, resp); axi_write(15'h008C, 32'h39C5CF, resp);
        axi_write(15'h0090, 32'h3FB950, resp); axi_write(15'h0094, 32'hF0B429, resp);
        axi_write(15'h0098, 32'hF85149, resp); axi_write(15'h009C, 32'h58A6FF, resp);
        axi_write(15'h0020, BG_BASE, resp);   axi_write(15'h0024, 32'd3200, resp);
        // camera: 4 stores, 384 x 240 crop at raw row 160, column 128 (dp_live.camera_setup)
        for (t = 0; t < 4; t = t + 1) axi_write(15'h0040 + 4 * t, CAM_BASE + CAM_SPAN * t + 160 * 640 + 128, resp);
        axi_write(15'h0030, 32'd1280, resp);  axi_write(15'h0034, 32'd4, resp);
        axi_write(15'h0028, (204 << 16) | 16, resp);  axi_write(15'h002C, 240 << 16, resp);
        for (t = 0; t < 256; t = t + 1) axi_write(15'h0400 + 4 * t, prof_mem[t], resp);
        for (t = 0; t < 4096; t = t + 1) if (text_mem[t] != 0) axi_write(15'h4000 + 4 * t, text_mem[t], resp);
        axi_write(15'h0004, 32'h000D, resp);                     // UI + blink + camera
        axi_write(15'h0008, 32'h1, resp);
        axi_read(15'h0008, d); if (d[0] !== 1'b1) fail("pending not set");
        axi_write(15'h0004, 32'h0, resp); if (resp != 2'b10) fail("no SLVERR while pending");
        d = 1; while (d[0]) axi_read(15'h0008, d);
        capture_frame("frame_ui.rgb", 800, 480);
        axi_read(15'h0050, s_bg); axi_read(15'h0054, s_cam);
        $display("800x480: bg %0d lines (%0d late), camera %0d lines (%0d late)",
                 s_bg[15:0], s_bg[31:16], s_cam[15:0], s_cam[31:16]);
        if (s_bg[31:16] != 0 || s_cam[31:16] != 0) fail("late line fetch");
        // run-time 1280x720p60, camera off, background still 800 px wide (shows top left)
        axi_write(15'h0010, (1650 << 16) | 1280, resp);
        axi_write(15'h0014, 32'h80000000 | (1430 << 16) | 1390, resp);
        axi_write(15'h0018, (750 << 16) | 720, resp);
        axi_write(15'h001C, 32'h80000000 | (730 << 16) | 725, resp);
        axi_write(15'h0004, 32'h0001, resp);
        commit;                                                   // BGSIZE stays 800x480: black beyond
        capture_frame("frame_720.rgb", 1280, 720);
        axi_read(15'h000C, d);
        $display("%0s: %0d errors, frames counted %0d", n_error ? "FAIL" : "PASS", n_error, d);
        $finish;
    end

    initial begin #900_000_000; $display("TIMEOUT"); $finish; end

endmodule

// AXI4 read slave over tb_video_overlay.ddr_word(), random ready/valid gaps,
// in-order bursts (16 x 64 bit, INCR) with the shape checked, up to 64 outstanding.
module tb_axi_rd_slave #(parameter SEED = 1) (
    input  wire        clk,
    input  wire [31:0] araddr,
    input  wire [7:0]  arlen,
    input  wire [2:0]  arsize,
    input  wire [1:0]  arburst,
    input  wire        arvalid,
    output reg         arready = 0,
    output reg  [63:0] rdata = 0,
    output reg         rlast = 0,
    output reg         rvalid = 0,
    input  wire        rready
);
    reg [31:0] q [0:63];                       // a background row issues 25 bursts at once
    integer wr = 0, rd = 0, beat = 0, seed = SEED;
    always @(posedge clk) begin
        arready <= ($random(seed) & 3) != 0;
        if (arvalid && arready) begin
            if (arlen != 15 || arsize != 3 || arburst != 1 || araddr[6:0] != 0)
                tb_video_overlay.fail("AR burst shape");
            q[wr % 64] = araddr;
            wr = wr + 1;
        end
        if (rvalid && rready) begin
            beat = beat + 1;
            if (rlast) begin beat = 0; rd = rd + 1; end
        end
        rvalid <= 0;
        if (rd < wr && (($random(seed) & 7) != 0) && !(rvalid && rlast)) begin
            rdata  <= tb_video_overlay.ddr_word(q[rd % 64] + 8 * beat);
            rvalid <= 1;
            rlast  <= (beat == 15);
        end
    end
endmodule
