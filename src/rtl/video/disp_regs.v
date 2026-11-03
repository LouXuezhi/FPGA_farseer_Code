// disp_regs: AXI4-Lite registers of the screen (aclk) -> display state (vclk).
//
//   0x0000 ID       RO  "OVL3"
//   0x0004 CTRL     RW* [0] UI on (background/water/text) [1] test bars [2] blink [3] camera on
//   0x0008 COMMIT   W   apply the staged (*) registers at the next vblank;  R [0] pending
//   0x000C FRAMES   RO  vblanks counted
//   0x0010..0x001C  RW* HTIM0/HTIM1/VTIM0/VTIM1 (as before: active/total, sync start/end, polarity)
//   0x0020 BGADDR   RW  background frame in DDR (RGBW8888 little endian: R, G, B, water)
//   0x0024 BGPITCH  RW  bytes per background row (multiple of 128, <= 8192)
//   0x0028 CAMPOS   RW* [11:0] x0 [27:16] y0     0x002C CAMSIZE RW* [27:16] rows (width is CAM_W)
//   0x0030 CAMPITCH RW  bytes between displayed camera rows   0x0034 CAMNFS RW [2:0] stores
//   0x0038 BGSIZE   RW* [11:0] width [27:16] height of the background; outside it is black
//   0x0040+4k CAMADDR RW k = 0..3
//   0x0050 BGSTAT   RO  {late, lines}   0x0054 CAMSTAT RO {late, lines}
//   0x0080+4k PAL   RW* k = 0..7: text colours, RGB888
//   0x0400+4i PROF  RW  i = 0..255: colormap index of screen x = 4i..4i+3 (water colour), 8 bit
//   0x4000+4a TEXT  RW  a = row * 128 + col: [7:0] glyph code (0 = blank) [10:8] colour [11] blink
//   * staged: visible after COMMIT; while pending, writes to them get SLVERR and are dropped.
//   PROF and TEXT are written directly (dual-clock RAMs): at ~1 Hz updates a value
//   changing mid-frame shows for one frame at most.
// Reset: 800x480@60 timing (the 7" panel), everything off.
`timescale 1ns / 1ps

module disp_regs (
    input  wire        aclk,
    input  wire        aresetn,
    input  wire [14:0] s_axi_awaddr,
    input  wire        s_axi_awvalid,
    output reg         s_axi_awready,
    input  wire [31:0] s_axi_wdata,
    input  wire        s_axi_wvalid,
    output reg         s_axi_wready,
    output reg  [1:0]  s_axi_bresp,
    output reg         s_axi_bvalid,
    input  wire        s_axi_bready,
    input  wire [14:0] s_axi_araddr,
    input  wire        s_axi_arvalid,
    output reg         s_axi_arready,
    output reg  [31:0] s_axi_rdata,
    output wire [1:0]  s_axi_rresp,
    output reg         s_axi_rvalid,
    input  wire        s_axi_rready,
    // fetch settings + status (aclk)
    output reg  [31:0] bg_addr,
    output reg  [15:0] bg_pitch,
    output reg  [31:0] cam_addr0, cam_addr1, cam_addr2, cam_addr3,
    output reg  [15:0] cam_pitch,
    output reg  [2:0]  cam_nfs,
    input  wire [31:0] bg_stat, cam_stat,
    // video domain
    input  wire        vclk,
    input  wire        vrst,
    input  wire        vblank_start,
    output reg  [15:0] ap_ctrl,
    output reg  [31:0] ap_htim0, ap_htim1, ap_vtim0, ap_vtim1, ap_campos, ap_camsize, ap_bgsize,
    output reg  [8*24-1:0] ap_pal,
    input  wire [7:0]  prof_raddr,          // vclk read ports (registered data next cycle)
    output reg  [7:0]  prof_rdata,
    input  wire [11:0] text_raddr,
    output reg  [11:0] text_rdata
);

    localparam [31:0] HTIM0_R = (1056 << 16) | 800, HTIM1_R = (968 << 16) | 840,
                      VTIM0_R = (525 << 16) | 480,  VTIM1_R = (496 << 16) | 493,
                      CAMPOS_R = (204 << 16) | 16,  CAMSIZE_R = 240 << 16, BGSIZE_R = (480 << 16) | 800;

    // ---------------- dual-clock RAMs ----------------
    (* ram_style = "block" *)       reg [7:0] prof_ram [0:255];   // BRAM: dual clock, no LUT CDC paths
    (* ram_style = "block" *)       reg [11:0] text_ram [0:4095];
    integer i;
    initial for (i = 0; i < 4096; i = i + 1) text_ram[i] = 12'd0;
    always @(posedge vclk) begin
        prof_rdata <= prof_ram[prof_raddr];
        text_rdata <= text_ram[text_raddr];
    end

    // ---------------- aclk: registers ----------------
    reg [15:0]   st_ctrl_q;
    reg [31:0]   st_htim0_q, st_htim1_q, st_vtim0_q, st_vtim1_q, st_campos_q, st_camsize_q, st_bgsize_q;
    reg [8*24-1:0] st_pal_q;
    reg          commit_tgl_q;
    (* ASYNC_REG = "TRUE" *) reg [1:0] ack_s, frame_s;
    (* ASYNC_REG = "TRUE" *) reg [1:0] commit_s;
    reg          v_ack_tgl_q, v_frame_tgl_q, frame_d_q;
    reg [31:0]   frames_q;
    wire         pending = commit_tgl_q ^ ack_s[1];

    wire        wr_go   = s_axi_awvalid && s_axi_wvalid && !s_axi_bvalid && !s_axi_awready;
    wire [14:0] wa      = s_axi_awaddr;
    wire        w_text  = wa[14];
    wire        w_prof  = !wa[14] && (wa[13:10] == 4'd1);
    wire        w_reg   = !wa[14] && (wa[13:10] == 4'd0);
    wire [7:0]  w_word  = wa[9:2];
    wire        staged  = w_reg && ((w_word == 1) || (w_word >= 4 && w_word <= 7) || w_word == 10 ||
                                    w_word == 11 || w_word == 14 || (w_word >= 32 && w_word < 40));
    wire [31:0] d = s_axi_wdata;

    always @(posedge aclk) begin
        if (wr_go && w_prof) prof_ram[wa[9:2]] <= d[7:0];
        if (wr_go && w_text) text_ram[wa[13:2]] <= d[11:0];
    end

    always @(posedge aclk) begin
        if (!aresetn) begin
            {s_axi_awready, s_axi_wready, s_axi_bvalid} <= 3'b000;
            s_axi_bresp  <= 2'b00;
            st_ctrl_q    <= 16'h0000;
            {st_htim0_q, st_htim1_q, st_vtim0_q, st_vtim1_q} <= {HTIM0_R, HTIM1_R, VTIM0_R, VTIM1_R};
            {st_campos_q, st_camsize_q, st_bgsize_q} <= {CAMPOS_R, CAMSIZE_R, BGSIZE_R};
            st_pal_q     <= 0;
            commit_tgl_q <= 1'b0;
            bg_addr      <= 32'd0;
            bg_pitch     <= 16'd3200;
            {cam_addr0, cam_addr1, cam_addr2, cam_addr3} <= 128'd0;
            cam_pitch    <= 16'd1280;
            cam_nfs      <= 3'd4;
        end else begin
            {s_axi_awready, s_axi_wready} <= {wr_go, wr_go};
            if (s_axi_bvalid && s_axi_bready) s_axi_bvalid <= 1'b0;
            if (wr_go) begin
                s_axi_bvalid <= 1'b1;
                s_axi_bresp  <= (staged && pending) ? 2'b10 : 2'b00;
                if (w_reg && !(staged && pending)) case (w_word)
                    8'd1:  st_ctrl_q    <= d[15:0];
                    8'd2:  commit_tgl_q <= ~commit_tgl_q;
                    8'd4:  st_htim0_q   <= d;
                    8'd5:  st_htim1_q   <= d;
                    8'd6:  st_vtim0_q   <= d;
                    8'd7:  st_vtim1_q   <= d;
                    8'd8:  bg_addr      <= d;
                    8'd9:  bg_pitch     <= d[15:0];
                    8'd10: st_campos_q  <= d;
                    8'd11: st_camsize_q <= d;
                    8'd12: cam_pitch    <= d[15:0];
                    8'd13: cam_nfs      <= d[2:0];
                    8'd14: st_bgsize_q  <= d;
                    8'd16: cam_addr0    <= d;
                    8'd17: cam_addr1    <= d;
                    8'd18: cam_addr2    <= d;
                    8'd19: cam_addr3    <= d;
                    default: if (w_word >= 32 && w_word < 40) st_pal_q[24*(w_word-32) +: 24] <= d[23:0];
                endcase
            end
        end
    end

    // read: registers only (PROF / TEXT are write-only from the bus)
    assign s_axi_rresp = 2'b00;
    wire [7:0] r_word = s_axi_araddr[9:2];
    reg  [31:0] rd_mux;
    always @* begin
        rd_mux = 32'd0;
        if (s_axi_araddr[14:10] == 0) case (r_word)
            8'd0:  rd_mux = 32'h4F564C33;                       // "OVL3"
            8'd1:  rd_mux = {16'd0, st_ctrl_q};
            8'd2:  rd_mux = {31'd0, pending};
            8'd3:  rd_mux = frames_q;
            8'd4:  rd_mux = st_htim0_q;   8'd5: rd_mux = st_htim1_q;
            8'd6:  rd_mux = st_vtim0_q;   8'd7: rd_mux = st_vtim1_q;
            8'd8:  rd_mux = bg_addr;      8'd9: rd_mux = {16'd0, bg_pitch};
            8'd10: rd_mux = st_campos_q;  8'd11: rd_mux = st_camsize_q;
            8'd12: rd_mux = {16'd0, cam_pitch};  8'd13: rd_mux = {29'd0, cam_nfs};
            8'd14: rd_mux = st_bgsize_q;
            8'd16: rd_mux = cam_addr0;    8'd17: rd_mux = cam_addr1;
            8'd18: rd_mux = cam_addr2;    8'd19: rd_mux = cam_addr3;
            8'd20: rd_mux = bg_stat;      8'd21: rd_mux = cam_stat;
            default: if (r_word >= 32 && r_word < 40) rd_mux = {8'd0, st_pal_q[24*(r_word-32) +: 24]};
        endcase
    end
    always @(posedge aclk) begin
        if (!aresetn) {s_axi_arready, s_axi_rvalid} <= 2'b00;
        else begin
            s_axi_arready <= s_axi_arvalid && !s_axi_rvalid && !s_axi_arready;
            if (s_axi_arvalid && !s_axi_rvalid && !s_axi_arready) begin
                s_axi_rvalid <= 1'b1;
                s_axi_rdata  <= rd_mux;
            end else if (s_axi_rvalid && s_axi_rready) s_axi_rvalid <= 1'b0;
        end
    end

    always @(posedge aclk) begin
        ack_s     <= {ack_s[0], v_ack_tgl_q};
        frame_s   <= {frame_s[0], v_frame_tgl_q};
        frame_d_q <= frame_s[1];
        if (!aresetn)                     frames_q <= 0;
        else if (frame_s[1] != frame_d_q) frames_q <= frames_q + 1;
    end

    // ---------------- vclk: apply at vblank ----------------
    always @(posedge vclk) begin
        commit_s <= {commit_s[0], commit_tgl_q};
        if (vrst) begin
            {v_ack_tgl_q, v_frame_tgl_q} <= 2'b00;
            ap_ctrl <= 16'h0000;
            {ap_htim0, ap_htim1, ap_vtim0, ap_vtim1} <= {HTIM0_R, HTIM1_R, VTIM0_R, VTIM1_R};
            {ap_campos, ap_camsize, ap_bgsize} <= {CAMPOS_R, CAMSIZE_R, BGSIZE_R};
            ap_pal  <= 0;
        end else if (vblank_start) begin
            v_frame_tgl_q <= ~v_frame_tgl_q;
            if (commit_s[1] != v_ack_tgl_q) begin
                ap_ctrl <= st_ctrl_q;
                {ap_htim0, ap_htim1, ap_vtim0, ap_vtim1} <= {st_htim0_q, st_htim1_q, st_vtim0_q, st_vtim1_q};
                {ap_campos, ap_camsize, ap_bgsize} <= {st_campos_q, st_camsize_q, st_bgsize_q};
                ap_pal      <= st_pal_q;
                v_ack_tgl_q <= commit_s[1];
            end
        end
    end

endmodule
