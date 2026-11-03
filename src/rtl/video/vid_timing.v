// vid_timing: raster timing generator, timing set at run time.
//
// The DP controller trains the link and sets the mode (Linux DRM); with a live
// input in "external timing" mode it follows these syncs, so the PS software
// copies the mode into the overlay registers (from the DP main stream
// attributes, see src/host/dp_live.py) and they are applied here at frame end.
// Line layout: active, front porch, sync, back porch (CEA-861 order).
//   h_active/h_total, hs_start = h_active + front porch, hs_end = hs_start + sync
// Outputs are registered; x/y belong to the pixel whose de is high in the same
// cycle. frame_start pulses on the first active pixel, vblank_start on the
// first line after the active area (the safe point to swap overlay registers).
`timescale 1ns / 1ps

module vid_timing (
    input  wire        clk,
    input  wire        rst,
    input  wire [11:0] h_active, h_total, hs_start, hs_end,
    input  wire [11:0] v_active, v_total, vs_start, vs_end,
    input  wire        hs_pos, vs_pos,                // sync polarity: 1 = active high
    output reg         hsync,
    output reg         vsync,
    output reg         de,
    output reg  [11:0] x,
    output reg  [11:0] y,
    output reg         frame_start,
    output reg         vblank_start
);

    reg  [11:0] h_q, v_q;
    // >= rather than ==: a smaller total applied mid-frame still wraps
    wire        h_end = (h_q >= h_total - 1);
    wire        v_end = (v_q >= v_total - 1);

    always @(posedge clk) begin
        if (rst) begin
            h_q <= 0;
            v_q <= 0;
        end else begin
            h_q <= h_end ? 12'd0 : h_q + 1;
            if (h_end) v_q <= v_end ? 12'd0 : v_q + 1;
        end
    end

    wire hs_raw = (h_q >= hs_start) && (h_q < hs_end);
    wire vs_raw = (v_q >= vs_start) && (v_q < vs_end);

    always @(posedge clk) begin
        if (rst) begin
            {hsync, vsync} <= 2'b00;
            {de, frame_start, vblank_start} <= 3'b000;
        end else begin
            hsync        <= hs_pos ? hs_raw : ~hs_raw;
            vsync        <= vs_pos ? vs_raw : ~vs_raw;
            de           <= (h_q < h_active) && (v_q < v_active);
            frame_start  <= (h_q == 0) && (v_q == 0);
            vblank_start <= (h_q == 0) && (v_q == v_active);
        end
        x <= h_q;
        y <= v_q;
    end

endmodule
