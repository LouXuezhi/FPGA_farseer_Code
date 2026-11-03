"""Pipe asset for the PS screen (fsf_ps_ui.py): the two Blender passes of pipe_panel.py
reduced to what the PS needs per frame.

    python -I tools/ui/build_ps_pipe.py --panel build/ui_panel [--w 768]

Writes board/fz3b/ui/pipe_ps.npz:
  a      (h, w, 3) uint8  pass "a" (dark water) over the page background
  water  (h, w)    uint8  b - a: how much coolant light reaches the camera, 0..255
  sensor_x, valve_x       screen x inside the asset (px), sensor order = dT order
The screen shows a + water/255 * colour(T(x)), same rule as the PL overlay (build_ui.py).
"""
import argparse
import json
import os
import sys

import cv2
import numpy as np

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
CROP = (40, 150)                         # rows of the 2x render that hold the pipe (build_ui.CROP)
BG = np.array([10, 12, 15], np.float32)  # ps_gfx.C["bg"], RGB


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--panel", default=os.path.join(ROOT, "build", "ui_panel"))
    ap.add_argument("--w", type=int, default=768)
    a = ap.parse_args()
    info = json.load(open(os.path.join(a.panel, "panel.json")))
    pa = cv2.imread(os.path.join(a.panel, "panel_a.png"), cv2.IMREAD_UNCHANGED).astype(np.float32) / 65535
    pb = cv2.imread(os.path.join(a.panel, "panel_b.png"), cv2.IMREAD_UNCHANGED).astype(np.float32) / 65535
    h = int(round((CROP[1] - CROP[0]) * a.w / float(info["w"])))
    pa, pb = (cv2.resize(p[CROP[0]:CROP[1]], (a.w, h), interpolation=cv2.INTER_AREA) for p in (pa, pb))
    bg = BG[::-1] / 255                                      # BGR
    ca = pa[..., :3] + (1 - pa[..., 3:]) * bg
    cb = pb[..., :3] + (1 - pb[..., 3:]) * bg
    water = np.clip((cb - ca).mean(-1), 0, 1)
    s = a.w / float(info["w"])
    sensor_x = [(x0 + x1) / 2 * s for x0, x1 in info["segments_px"][1:10]]   # inter-valve segments 1..9
    valve_x = [v * s for v in info["valves_px"]]
    out = os.path.join(ROOT, "board", "fz3b", "ui", "pipe_ps.npz")
    np.savez_compressed(out, a=np.clip(np.round(ca[..., ::-1] * 255), 0, 255).astype(np.uint8),
                        water=np.clip(np.round(water * 255), 0, 255).astype(np.uint8),
                        sensor_x=np.array(sensor_x), valve_x=np.array(valve_x))
    print("pipe asset %dx%d, water px %d, sensor x %s -> %s" % (a.w, h, int((water > 0.03).sum()),
                                                               [int(x) for x in sensor_x], out))


if __name__ == "__main__":
    main()
