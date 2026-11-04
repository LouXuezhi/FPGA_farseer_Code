"""Build the screen assets for the 800x480 panel.

    python -I tools/ui/build_ui.py --panel build/ui_panel [--cam data/cam_frame_640x800.npy]

Inputs : pipe_panel.py renders (panel_a.png, panel_b.png, panel.json)
Outputs:
  src/rtl/video/heat_lut.mem, font12x24.mem     PL ROMs (synthesised)
  board/fz3b/ui/bg.bin, layout.json             loaded by fsf_ui.py on the board
  build/ui/bg_words.mem                         background for the testbench
  build/ui/preview.png                          disp_ref.compose() with sample data:
                                                bit-exact what the PL will output
Text and panels are drawn by ui_text.py under the system Python (Pillow).
"""
import argparse
import json
import os
import subprocess
import sys

import cv2
import numpy as np

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
sys.path[:0] = [os.path.join(ROOT, "src", "host"), os.path.join(ROOT, "src", "rtl", "video")]
import ui_layout as L          # noqa: E402
import disp_ref as R           # noqa: E402

HEX = lambda c: "#%06x" % c                                  # noqa: E731
CROP = (40, 150)               # rows of the 2x panel render that hold the pipe (handles .. rod)


def rgb(c):
    return np.array([(c >> 16) & 255, (c >> 8) & 255, c & 255], np.float32)


def run_text(job, tmp):
    path = os.path.join(tmp, "job_%s.json" % os.path.basename(job["out"]).split(".")[0])
    json.dump(job, open(path, "w", encoding="utf-8"), ensure_ascii=False)
    # -E: ignore PYTHON* env; not -I, which would hide the user site-packages where Pillow lives
    subprocess.check_call(["/usr/bin/python3", "-E", os.path.join(ROOT, "tools", "ui", "ui_text.py"), path],
                          cwd=tmp)
    return cv2.imread(job["out"], cv2.IMREAD_UNCHANGED)


def over(dst, rgba):
    """Alpha-composite a BGRA uint8 layer onto an RGB float image (in place)."""
    a = rgba[..., 3:4].astype(np.float32) / 255
    dst[:] = rgba[..., 2::-1].astype(np.float32) * a + dst * (1 - a)


def pipe_layers(panel_dir):
    """Panel passes -> (rgb over PANEL colour, water strength W) at screen size, sensor x."""
    info = json.load(open(os.path.join(panel_dir, "panel.json")))
    pa = cv2.imread(os.path.join(panel_dir, "panel_a.png"), cv2.IMREAD_UNCHANGED).astype(np.float32) / 65535
    pb = cv2.imread(os.path.join(panel_dir, "panel_b.png"), cv2.IMREAD_UNCHANGED).astype(np.float32) / 65535
    x0, y0, w, h = L.PIPE
    pa, pb = (cv2.resize(p[CROP[0]:CROP[1]], (w, h), interpolation=cv2.INTER_AREA) for p in (pa, pb))
    panel = rgb(L.PANEL)[::-1] / 255                         # BGR
    ca = pa[..., :3] + (1 - pa[..., 3:]) * panel
    cb = pb[..., :3] + (1 - pb[..., 3:]) * panel
    water = np.clip((cb - ca).mean(-1), 0, 1)
    s = w / info["w"]
    seg = [(a + b) / 2 * s + x0 for a, b in info["segments_px"][1:10]]   # inter-valve segments 1..9
    return ca[..., ::-1] * 255, water * 255, seg, [v * s + x0 for v in info["valves_px"]]


def static_ops(sensor_x):
    P, B, D, W, A = HEX(L.PANEL), HEX(L.BORDER), HEX(L.PALETTE[L.DIM]), HEX(L.PALETTE[L.WHITE]), HEX(L.PALETTE[L.ACCENT])
    ops = [dict(op="rect", xy=[0, 0, 799, 23], fill="#0e141c"),
           dict(op="line", xy=[0, 23, 799, 23], fill=B),
           dict(op="rect", xy=[8, 6, 11, 17], fill=A),
           dict(op="text", xy=[18, 3], s="多模光纤散斑测温 · 液冷管路", size=15, font="cjk_bold", fill=W),
           dict(op="text", xy=[240, 6], s="FZ3B  ΔT-QCNN", size=12, font="mono_reg", fill=D)]
    for box in (L.PIPE_PANEL, L.CAM_PANEL):
        x, y, w, h = box
        ops.append(dict(op="rect", xy=[x, y, x + w - 1, y + h - 1], fill=P, outline=B, r=6))
    for c in range(3):
        for r in range(3):
            x, y = L.CARDS_X[c], L.CARDS_Y + r * L.CARD_H
            ops.append(dict(op="rect", xy=[x, y + 2, x + L.CARD_W - 1, y + L.CARD_H - 4], fill=P, outline=B, r=6))
    for k in range(9):                                       # card names: sensor + heater
        c, r = k % 3, k // 3
        x, y = L.CARDS_X[c] + 12, L.CARDS_Y + r * L.CARD_H + 6
        ops.append(dict(op="text", xy=[x, y], s="S%-2d" % L_SENSORS[k][0], size=13, font="mono", fill=A))
        ops.append(dict(op="text", xy=[x + 34, y + 2], s="H%d" % L_SENSORS[k][1], size=11, font="mono_reg", fill=D))
    hx, hy, hw, hh = L.HEAT_STRIP
    ops.append(dict(op="rect", xy=[hx - 1, hy - 1, hx + hw, hy + hh], outline=B))
    for k, sxp in enumerate(sensor_x):                       # sensor marks + names under the strip
        ops.append(dict(op="line", xy=[sxp, hy + hh + 1, sxp, hy + hh + 3], fill=W))
        ops.append(dict(op="text", xy=[sxp, L.SENSOR_NAME_Y + 1], s="S%d" % L_SENSORS[k][0], size=10,
                        font="mono_reg", fill=D, anchor="ma"))
    cx, cy, cw, ch = L.COLORBAR
    ops.append(dict(op="rect", xy=[cx - 1, cy - 1, cx + cw, cy + ch], outline=B))
    for i in range(5):
        t = L.T_LO + i * (L.T_HI - L.T_LO) / 4
        xt = cx + i * (cw - 1) / 4
        ops.append(dict(op="line", xy=[xt, cy + ch, xt, cy + ch + 3], fill=D))
        ops.append(dict(op="text", xy=[xt, cy + ch + 3], s="%d" % t, size=10, font="mono_reg", fill=D, anchor="ma"))
    ops.append(dict(op="text", xy=[cx - 10, cy - 4], s="水温 ℃", size=11, font="cjk", fill=D, anchor="ra"))
    ops.append(dict(op="text", xy=[cx + cw + 10, cy - 4], s="PL 实时着色", size=11, font="cjk", fill=D))
    x, y, w, h = L.CAM_PANEL
    ops.append(dict(op="text", xy=[x + 8, y + 6], s="散斑相机 · OV9281 实时", size=13, font="cjk", fill=W))
    ops.append(dict(op="rect", xy=[L.CAM[0] - 1, L.CAM[1] - 1, L.CAM[0] + L.CAM[2], L.CAM[1] + L.CAM[3]], outline=B))
    ops.append(dict(op="line", xy=[0, 455, 799, 455], fill=B))
    ops.append(dict(op="text", xy=[792, 461], s="Zynq UltraScale+ ZU3EG · PL 合成 800×480", size=11, font="cjk",
                    fill=D, anchor="ra"))
    return ops


L_SENSORS = [(7, 7), (16, 5), (1, 6), (12, 3), (2, 9), (9, 8), (4, 4), (3, 13), (10, 10)]  # (S, H), dT order


def font_rom(atlas):
    """atlas (24, 12 * n) uint8 -> 4096 rows of 12 x 2-bit coverage (col 0 in bits 23:22)."""
    rom = np.zeros(4096, np.int64)
    q = np.clip(np.round(atlas.astype(np.float32) / 255 * 3), 0, 3).astype(np.int64)
    for g in range(atlas.shape[1] // L.CELL_W):
        cell = q[:, g * L.CELL_W:(g + 1) * L.CELL_W]
        for r in range(L.CELL_H):
            word = 0
            for c in range(L.CELL_W):
                word |= int(cell[r, c]) << (22 - 2 * c)
            rom[g * 32 + r] = word
    return rom


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--panel", default=os.path.join(ROOT, "build", "ui_panel"))
    ap.add_argument("--cam", help="(800, 640) RAW8 .npy frame for the preview camera window")
    a = ap.parse_args()
    tmp = os.path.join(ROOT, "build", "ui")
    os.makedirs(tmp, exist_ok=True)
    pipe_rgb, water, sensor_x, valve_x = pipe_layers(a.panel)

    # ---------------- background ----------------
    img = np.empty((L.H, L.W, 3), np.float32)
    img[:] = rgb(L.BG)
    glyphs = u"".join(chr(c) for c in range(0x20, 0x7F)) + L.EXTRA_GLYPHS
    lay = run_text(dict(w=L.W, h=L.H, out=os.path.join(tmp, "layer.png"), ops=static_ops(sensor_x),
                        atlas=os.path.join(tmp, "atlas.png"), cell=[L.CELL_W, L.CELL_H], glyphs=glyphs,
                        glyph_size=17), tmp)
    over(img, lay)
    x0, y0, w, h = L.PIPE
    img[y0:y0 + h, x0:x0 + w] = pipe_rgb                    # pipe on its panel (already over PANEL)
    heat = R.heat_lut()
    cx, cy, cw, ch = L.COLORBAR
    img[cy:cy + ch, cx:cx + cw] = R.rgb_of(heat[np.linspace(0, 255, cw).round().astype(int)])[None]
    W = np.zeros((L.H, L.W), np.float32)
    W[y0:y0 + h, x0:x0 + w] = water
    hx, hy, hw, hh = L.HEAT_STRIP
    img[hy:hy + hh, hx:hx + hw] = 0                          # pure PL colour: black base, full water weight
    W[hy:hy + hh, hx:hx + hw] = 255
    bg = np.concatenate([np.clip(np.round(img), 0, 255), np.round(W)[..., None]], -1).astype(np.uint8)

    # ---------------- outputs ----------------
    vid = os.path.join(ROOT, "src", "rtl", "video")
    open(os.path.join(vid, "heat_lut.mem"), "w").write("\n".join("%06x" % v for v in heat) + "\n")
    atlas = cv2.imread(os.path.join(tmp, "atlas.png"), cv2.IMREAD_GRAYSCALE)
    font = font_rom(atlas)
    open(os.path.join(vid, "font12x24.mem"), "w").write("\n".join("%06x" % v for v in font) + "\n")
    brd = os.path.join(ROOT, "board", "fz3b", "ui")
    os.makedirs(brd, exist_ok=True)
    bg.tofile(os.path.join(brd, "bg.bin"))                   # R, G, B, W per pixel
    words = bg.astype(np.int64)
    words = words[..., 0] | (words[..., 1] << 8) | (words[..., 2] << 16) | (words[..., 3] << 24)
    open(os.path.join(tmp, "bg_words.mem"), "w").write("\n".join("%08x" % v for v in words.ravel()) + "\n")
    cols = L.pipe_field_cols([x - L.PIPE[0] for x in sensor_x])
    layout = dict(w=L.W, h=L.H, sensor_x=[round(x, 1) for x in sensor_x], valve_x=[round(x, 1) for x in valve_x],
                  pipe_temp_cols=cols, sensors=L_SENSORS, cam=L.CAM, t_lo=L.T_LO, t_hi=L.T_HI,
                  note="sensor_x: segment centres between valves 1..10, S order = dT order (not measured)")
    json.dump(layout, open(os.path.join(brd, "layout.json"), "w"), indent=1)

    # ---------------- preview: the board driver on fake registers + the reference model ----------------
    import fsf_ui
    scr = fsf_ui.Screen(fsf_ui.FakeRegs(), layout)
    temps = [36.8, 37.4, 38.9, 41.6, 44.2, 43.1, 40.2, 38.0, 37.1]
    alarms = [False] * 4 + [True] + [False] * 4
    scr.show(temps, [True] * 9, alarms, [0.012, 0.018, 0.031, 0.064, 0.112, 0.081, 0.040, 0.022, 0.015],
             dict(step=1284, dt=1.0, qcnn_ms=0.57, pre_ms=312, mqtt=True, clock="23:59:59",
                  topic="fsf/fz3b/state", sent=1284, queued=0))
    prof = [scr.regs.mem.get(fsf_ui.PROF0 + 4 * i, 0) for i in range(256)]
    if a.cam:
        raw = np.load(a.cam)
        cam = raw[0::2][80:80 + L.CAM[3], 128:128 + L.CAM[2]]   # same crop as dp_live.camera_setup
    else:
        cam = R.cam_pattern(1)[0::2][:L.CAM[3], :L.CAM[2]] if hasattr(R, "cam_pattern") else None
    out = R.compose(bg, prof, heat, font, scr.text_ram(), L.PALETTE, 0x0D, L.W, L.H, cam, L.CAM[0], L.CAM[1])
    cv2.imwrite(os.path.join(tmp, "preview.png"), out[..., ::-1])
    np.savez(os.path.join(tmp, "case_ui.npz"), bg=bg, prof=prof, heat=heat, font=font, text=scr.text_ram(),
             pal=L.PALETTE, ctrl=0x0D, w=L.W, h=L.H, cam=cam if cam is not None else np.zeros(0),
             cam_x0=L.CAM[0], cam_y0=L.CAM[1])
    # testbench inputs (sim/rtl/tb_video_overlay.v): text / profile RAM contents, raw camera frame
    open(os.path.join(tmp, "text.mem"), "w").write("\n".join("%03x" % v for v in scr.text_ram()) + "\n")
    open(os.path.join(tmp, "prof.mem"), "w").write("\n".join("%02x" % v for v in prof) + "\n")
    if a.cam:
        open(os.path.join(tmp, "cam_raw.mem"), "w").write("\n".join("%02x" % v for v in raw.ravel()) + "\n")
    print("water pixels", int((W > 8).sum()), "sensor x", [int(x) for x in sensor_x], "-> build/ui/preview.png")


if __name__ == "__main__":
    main()
