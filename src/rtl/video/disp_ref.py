"""Bit-exact reference of disp_compose.v (the screen), and the PL ROM formats.

    compose(bg, prof, heat, font, text, pal, ctrl, w, h, cam=None, cam_x0, cam_y0, frame_cnt)
        -> (h, w, 3) uint8, what the DP live video input receives
    python3 -I src/rtl/video/disp_ref.py check <dump.rgb> <case.npz>
        compare an RTL frame dump with compose() on the inputs saved in case.npz

Formats (shared with tools/ui/build_ui.py and the board driver):
  bg    (h, w, 4) uint8  R, G, B, W  (W = coolant light, pipe_panel.py)
  prof  (256,) colormap index for screen x = 4i .. 4i+3
  heat  (256,) 0xRRGGBB, the colormap (HEAT_STOPS)
  font  (4096,) 24-bit rows: glyph * 32 + row, 12 x 2-bit coverage, col 0 in bits 23:22
  text  (4096,) 12-bit cells: row * 128 + col: [7:0] code [10:8] colour [11] blink
Only numpy.
"""
import sys

import numpy as np

# ParaView-like colormap sampled from the reference picture by the modelling
# session (pipe_model.py HEAT_STOPS_SRGB): deep navy -> blue -> pale cyan ->
# pale yellow -> orange -> deep red
HEAT_STOPS = [
    (0.00, (31, 36, 138)), (0.08, (37, 59, 160)), (0.16, (38, 110, 194)), (0.25, (33, 162, 228)),
    (0.33, (66, 203, 245)), (0.41, (142, 228, 245)), (0.49, (214, 243, 199)), (0.53, (235, 234, 170)),
    (0.61, (250, 199, 107)), (0.70, (253, 164, 70)), (0.78, (233, 122, 41)), (0.86, (207, 79, 14)),
    (0.94, (186, 43, 29)), (1.00, (176, 19, 30)),
]
BARS = [0xFFFFFF, 0xFFFF00, 0x00FFFF, 0x00FF00, 0xFF00FF, 0xFF0000, 0x0000FF, 0x000000]
CAM_W = 384


def cam_pattern(seed, rows=800, cols=640):
    """VDMA frame store test content (raw 640x800 RAW8), same formula as tb_video_overlay."""
    y, x = np.mgrid[0:rows, 0:cols]
    return ((x * 3 + y * 5 + ((x * y) >> 4) + seed * 37) & 255).astype(np.uint8)


def heat_lut():
    t = np.linspace(0.0, 1.0, 256)
    xs = [s[0] for s in HEAT_STOPS]
    rgb = np.stack([np.interp(t, xs, [s[1][c] for s in HEAT_STOPS]) for c in range(3)], 1)
    rgb = np.clip(np.round(rgb), 0, 255).astype(np.int64)
    return (rgb[:, 0] << 16) | (rgb[:, 1] << 8) | rgb[:, 2]


def glyph_index(code):
    code = np.asarray(code)
    return np.where(code < 0x7F, code - 0x20, 95 + (code & 0x0F))


def glyph_ok(code):
    code = np.asarray(code)
    return (code >= 0x21) & ((code < 0x7F) | ((code >= 0x80) & (code < 0x87)))


def rgb_of(v):
    v = np.asarray(v, np.int64)
    return np.stack([(v >> 16) & 255, (v >> 8) & 255, v & 255], -1)


def compose(bg, prof, heat, font, text, pal, ctrl, w, h, cam=None, cam_x0=16, cam_y0=228, frame_cnt=0):
    y, x = np.mgrid[0:h, 0:w]
    bgp = np.zeros((h, w, 4), np.int64)                      # BGSIZE: outside the background is black
    src = np.asarray(bg, np.int64)
    bgp[:min(h, src.shape[0]), :min(w, src.shape[1])] = src[:h, :w]
    wv = bgp[..., 3]
    hc = rgb_of(np.asarray(heat)[np.asarray(prof)[(x >> 2) & 255]])
    base = np.minimum(bgp[..., :3] + ((hc * wv[..., None]) >> 8), 255)
    under = base if ctrl & 1 else np.zeros_like(base)
    if cam is not None and ctrl & 8:
        rows = cam.shape[0]
        hit = (x >= cam_x0) & (x < cam_x0 + CAM_W) & (y >= cam_y0) & (y < cam_y0 + rows)
        g = np.zeros((h, w), np.int64)
        g[hit] = np.asarray(cam, np.int64)[(y - cam_y0)[hit], (x - cam_x0)[hit]]
        under = np.where(hit[..., None], g[..., None], under)
    col, row = (x * 2731) >> 15, (y * 2731) >> 16
    assert (col == x // 12).all() and (row == y // 24).all()
    sx, sy = x - col * 12, y - row * 24
    cell = np.asarray(text, np.int64)[((row & 31) << 7) | (col & 127)]
    code, attr = cell & 255, cell >> 8
    grow = np.asarray(font, np.int64)[((glyph_index(code) & 127) << 5) | sy]
    cov = np.where(glyph_ok(code), (grow >> (22 - 2 * sx)) & 3, 0)
    if ctrl & 4 and (frame_cnt >> 5) & 1:
        cov = np.where(attr & 8, 0, cov)
    fg = rgb_of(np.asarray(pal, np.int64)[attr & 7])
    a = np.array([0, 85, 171, 256])[cov][..., None]
    txt = ((a * fg + (256 - a) * under) >> 8)
    out = txt if ctrl & 1 else under
    if ctrl & 2:
        out = rgb_of(np.array(BARS)[np.minimum(x // 100, 7)])
    return out.astype(np.uint8)


def check(dump, case):
    c = np.load(case)
    h, w = int(c["h"]), int(c["w"])
    got = np.fromfile(dump, np.uint8).reshape(h, w, 3)
    want = compose(c["bg"], c["prof"], c["heat"], c["font"], c["text"], c["pal"], int(c["ctrl"]), w, h,
                   c["cam"] if c["cam"].size else None, int(c["cam_x0"]), int(c["cam_y0"]))
    bad = np.argwhere((got != want).any(-1))
    print("%s: %d/%d pixels match" % (dump.split("/")[-1], h * w - len(bad), h * w))
    for yy, xx in bad[:5]:
        print("  MISMATCH y=%d x=%d rtl=%s ref=%s" % (yy, xx, got[yy, xx], want[yy, xx]))
    return len(bad) == 0


if __name__ == "__main__":
    sys.exit(0 if check(sys.argv[2], sys.argv[3]) else 1)
