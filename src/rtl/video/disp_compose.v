// disp_compose: per-pixel screen pipeline (vclk). Stage sK = K cycles after x/y
// (s0); every path is aligned at s6, composed there, output registered (s7).
//
//   background : RGBW8888 from DDR (line_fetch). W = how much coolant light the
//                pixel shows (pipe_panel.py renders the water black and white).
//   water      : out = bg.rgb + W * heat[prof[x/4]] / 256  (saturating)
//                prof[] = colormap index along the screen x, written by the PS
//                from the sensor temperatures; heat[] = HEAT_FILE (ParaView-like)
//   camera     : RAW8 gray inside the camera window, replaces the background
//   text       : 12x24 cells, code + colour + blink from the text RAM, 2-bit
//                anti-aliased glyphs (FONT_FILE), blended over the result
//   test bars  : CTRL[1], 8 bars of 100 px, for the pixel packing check
// ctrl: [0] UI (background, water, text) [1] test bars [2] blink [3] camera
`timescale 1ns / 1ps

module disp_compose #(
    parameter CAM_W     = 384,
    parameter HEAT_FILE = "heat_lut.mem",
    parameter FONT_FILE = "font12x24.mem"
) (
    input  wire        clk,
    input  wire        rst,
    input  wire        de, hs, vs, frame_start,
    input  wire [11:0] x, y,
    input  wire [15:0] ctrl,
    input  wire [11:0] cam_x0, cam_y0, cam_rows,
    input  wire [8*24-1:0] pal,
    input  wire [11:0] bg_w, bg_h,                   // background size: outside it is black
    // background line buffer (2 px per word)
    output wire        bg_bank,
    output wire [9:0]  bg_word,
    input  wire [63:0] bg_data,
    // camera line buffer (8 px per word)
    output wire        cam_bank,
    output wire [5:0]  cam_word,
    input  wire [63:0] cam_data,
    // profile and text RAM (registered reads, in disp_regs)
    output wire [7:0]  prof_addr,
    input  wire [7:0]  prof_data,
    output wire [11:0] text_addr,
    input  wire [11:0] text_data,
    output reg         out_de, out_hs, out_vs,
    output reg  [23:0] out_rgb
);

    (* rom_style = "block" *) reg [23:0] heat [0:255];
    (* rom_style = "block" *) reg [23:0] font [0:4095];      // {glyph[6:0], row[4:0]}: 12 x 2 bit, col 0 in [23:22]
    initial begin
        $readmemh(HEAT_FILE, heat);
        $readmemh(FONT_FILE, font);
    end

    // ---------------- raster pipeline ----------------
    reg [11:0] x1, x2, x3, x4;
    reg [7:0]  de_d, hs_d, vs_d;
    reg [5:0]  frame_cnt;
    always @(posedge clk) begin
        {x1, x2, x3, x4} <= {x, x1, x2, x3};
        de_d <= {de_d[6:0], de};
        hs_d <= {hs_d[6:0], hs};
        vs_d <= {vs_d[6:0], vs};
        if (rst) frame_cnt <= 0; else if (frame_start) frame_cnt <= frame_cnt + 1;
    end

    // ---------------- background + water ----------------
    assign bg_bank = y[0];
    assign bg_word = x[10:1];                         // data at s2
    assign prof_addr = x2[9:2];                       // profile at s3
    reg        bsel3;
    reg [31:0] bgp3, bgp4, bgp5;
    reg [4:0]  bin_d;                                 // inside the background, s1..s5
    reg [23:0] heat4, heat5;
    reg [23:0] base6;
    wire [31:0] bgp = bsel3 ? bg_data[63:32] : bg_data[31:0];
    always @(posedge clk) begin
        bsel3 <= x1[0];                               // pixel select, aligned with bg_data (s2 -> s3)
        bin_d <= {bin_d[3:0], (x < bg_w) && (y < bg_h)};
        bgp3  <= bin_d[1] ? bgp : 32'd0;
        bgp4  <= bgp3;
        bgp5  <= bgp4;
        heat4 <= heat[prof_data];                     // prof_data at s3 -> s4 (ROM read)
        heat5 <= heat4;                               // s5 (ROM output register)
    end
    // per channel (plain wires, no functions: xsim and Verilator agree bit for bit)
    // bg word bytes: [7:0] R [15:8] G [23:16] B [31:24] W; heat / base6: {R, G, B}
    wire [7:0] w5 = bgp5[31:24];
    genvar ch;
    generate for (ch = 0; ch < 3; ch = ch + 1) begin : g_water
        wire [15:0] m   = heat5[16 - 8 * ch +: 8] * w5;
        wire [8:0]  sum = bgp5[8 * ch +: 8] + m[15:8];
        always @(posedge clk) base6[16 - 8 * ch +: 8] <= sum[8] ? 8'hFF : sum[7:0];
    end endgenerate

    // ---------------- camera ----------------
    wire [11:0] cx = x - cam_x0, cy = y - cam_y0;
    wire        in_cam = (x >= cam_x0) && (x < cam_x0 + CAM_W) && (y >= cam_y0) && (y < cam_y0 + cam_rows);
    assign cam_bank = cy[0];
    assign cam_word = cx[8:3];                        // data at s2
    reg  [2:0] cbyte1, cbyte2;
    reg  [6:0] chit_d;                                // in_cam s1..s7
    reg  [7:0] gray3, gray4, gray5, gray6;
    always @(posedge clk) begin
        {cbyte1, cbyte2} <= {cx[2:0], cbyte1};
        chit_d <= {chit_d[5:0], in_cam};
        gray3  <= cam_data[8 * cbyte2 +: 8];
        {gray4, gray5, gray6} <= {gray3, gray4, gray5};
    end

    // ---------------- text ----------------
    // col = x / 12, row = y / 24 by multiply-shift (exact for x < 4096 / y < 2048)
    wire [23:0] qx = x * 12'd2731;                    // 2731 / 32768 ~ 1/12
    wire [23:0] qy = y * 12'd2731;                    // 2731 / 65536 ~ 1/24
    reg  [6:0]  col1;  reg [5:0] row1;  reg [11:0] xs1, ys1;
    reg  [3:0]  subx2, subx3, subx4;
    reg  [4:0]  suby2;
    always @(posedge clk) begin
        col1 <= qx[21:15];  row1 <= qy[21:16];  xs1 <= x;  ys1 <= y;           // s1
        subx2 <= xs1 - col1 * 12;  suby2 <= ys1 - row1 * 24;                   // s2
        {subx3, subx4} <= {subx2, subx3};
    end
    assign text_addr = {row1[4:0], col1};             // text_data at s2 (registered in disp_regs)
    wire [7:0]  code2 = text_data[7:0];
    wire [6:0]  gidx2 = (code2 < 8'h7F) ? code2[6:0] - 7'h20 : 7'd95 + (code2[6:0] & 7'h0F);
    wire        gok2  = (code2 >= 8'h21) && (code2 < 8'h7F || (code2 >= 8'h80 && code2 < 8'h87));
    reg  [23:0] glyph3, glyph4;
    reg  [3:0]  attr3, attr4;                         // {blink, colour}
    reg         gvalid3, gvalid4;
    always @(posedge clk) begin
        glyph3 <= font[{gidx2, suby2}];               // s3 (ROM read)
        glyph4 <= glyph3;                             // s4 (ROM output register)
        {attr3, attr4} <= {text_data[11:8], attr3};
        {gvalid3, gvalid4} <= {gok2, gvalid3};
    end
    wire [1:0] cov4  = gvalid4 ? glyph4[22 - 2 * subx4 +: 2] : 2'd0;
    wire       hide4 = attr4[3] && ctrl[2] && frame_cnt[5];
    reg  [1:0] cov5, cov6;
    reg  [23:0] fg5, fg6;
    always @(posedge clk) begin
        cov5 <= hide4 ? 2'd0 : cov4;
        fg5  <= pal[24 * attr4[2:0] +: 24];
        {cov6, fg6} <= {cov5, fg5};
    end

    // ---------------- test bars ----------------
    reg [2:0] bar2, bar3, bar4, bar5, bar6;
    always @(posedge clk) begin
        bar2 <= (x1 >= 700) ? 3'd7 : x1 / 100;
        {bar3, bar4, bar5, bar6} <= {bar2, bar3, bar4, bar5};
    end
    reg [23:0] bar_rgb;
    always @* case (bar6)
        3'd0: bar_rgb = 24'hFFFFFF;  3'd1: bar_rgb = 24'hFFFF00;
        3'd2: bar_rgb = 24'h00FFFF;  3'd3: bar_rgb = 24'h00FF00;
        3'd4: bar_rgb = 24'hFF00FF;  3'd5: bar_rgb = 24'hFF0000;
        3'd6: bar_rgb = 24'h0000FF;  default: bar_rgb = 24'h000000;
    endcase

    // ---------------- s6 -> s7: compose ----------------
    wire [23:0] under = (ctrl[3] && chit_d[5]) ? {3{gray6}} : ctrl[0] ? base6 : 24'd0;
    wire [8:0]  a = (cov6 == 0) ? 9'd0 : (cov6 == 1) ? 9'd85 : (cov6 == 2) ? 9'd171 : 9'd256;
    wire [23:0] txt;
    generate for (ch = 0; ch < 3; ch = ch + 1) begin : g_text
        wire [16:0] acc = a * fg6[8 * ch +: 8] + (9'd256 - a) * under[8 * ch +: 8];
        assign txt[8 * ch +: 8] = acc[15:8];
    end endgenerate
    always @(posedge clk) begin
        if (rst) begin
            {out_de, out_hs, out_vs} <= 3'b000;
            out_rgb <= 0;
        end else begin
            {out_de, out_hs, out_vs} <= {de_d[5], hs_d[5], vs_d[5]};          // s6 -> s7
            out_rgb <= !de_d[5] ? 24'd0 : ctrl[1] ? bar_rgb : ctrl[0] ? txt : under;
        end
    end

endmodule
