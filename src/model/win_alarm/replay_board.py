#!/usr/bin/env python3
"""Replay a recorded session through win_fusion.WinFusion at the board's 2 s step, golden integer QCNN.
usage: python replay_board.py <qmodel.npz> <fusion.json> <session dir> [period]"""
import csv, json, os, sys
import numpy as np
QM, FJ, SD = sys.argv[1:4]; PERIOD = float(sys.argv[4]) if len(sys.argv) > 4 else 2.0
ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..", ".."))
sys.path[:0] = [os.path.join(ROOT, "src", "host"), os.path.join(ROOT, "src", "model")]
from speckle_preproc import preprocess
CHUNK = 300
from qcnn_int import load_qmodel, forward_int
from win_fusion import WinFusion

class Golden:
    def __init__(s, p): s.q = load_qmodel(p)
    def run(s, x): return forward_int(s.q, x)

wf = WinFusion.load(FJ, Golden(QM))
rows = list(csv.DictReader(open(os.path.join(SD, "frames.csv"))))
t0 = float(rows[0]["host_time"]); cache = {}; out = []; nxt = 0.0
for i, r in enumerate(rows):
    t = float(r["host_time"]) - t0
    if t < nxt: continue
    nxt = t + PERIOD
    c = i // CHUNK * CHUNK
    if c not in cache: cache = {c: np.load(os.path.join(SD, "camera_%05d.npy" % c), mmap_mode="r")}
    st = wf.push(t, preprocess(cache[c][i % CHUNK], bad_pixels=np.zeros((0, 2), int)))
    if st["event"]: out.append(round(t))
    if st["ready"] and i % 300 < PERIOD: print("t %5.0f score %.2f cnn %.3f corr %.3f" % (t, st["score"], st["cnn"], st["corr"]), flush=True)
print("ALARMS", os.path.basename(SD), out)
