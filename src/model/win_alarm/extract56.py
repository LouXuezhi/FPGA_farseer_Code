#!/usr/bin/env python3
"""Per-frame 56x56 plane (board to_plane before quantization) and 10x16 blocks -> $FSF_WIN_WORK/cache/<sid>.npz
usage: python extract56.py <session dir> [...]   (session dirs in the dataset handover format)"""
import csv, json, os, sys
from multiprocessing import Pool
import numpy as np
ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..", ".."))
WORK = os.environ.get("FSF_WIN_WORK", os.path.join(ROOT, "build", "win_alarm"))   # caches and outputs
sys.path.insert(0, os.path.join(ROOT, "src", "host"))
from speckle_preproc import preprocess  # noqa: E402  same as the board
CHUNK = 300                                # frames per camera_*.npy
OUT = os.path.join(WORK, "cache"); os.makedirs(OUT, exist_ok=True)


def plane(img, in_h=56):
    k = img.shape[0] // in_h; side = k * in_h
    y0, x0 = (img.shape[0] - side) // 2, (img.shape[1] - side) // 2
    return img[y0:y0 + side, x0:x0 + side].reshape(in_h, k, in_h, k).mean(axis=(1, 3))


def one(d):
    d = d.rstrip("/"); sid = os.path.basename(d); o = os.path.join(OUT, sid + ".npz")
    if os.path.exists(o): return sid + " cached"
    rows = list(csv.DictReader(open(os.path.join(d, "frames.csv"))))
    meta = json.load(open(os.path.join(d, "session.json")))
    P = np.zeros((len(rows), 56, 56), np.float32); Q = np.zeros((len(rows), 160), np.float32); cache = {}
    bp = np.zeros((0, 2), int)
    for i in range(len(rows)):
        c = i // CHUNK * CHUNK
        if c not in cache: cache = {c: np.load(os.path.join(d, "camera_%05d.npy" % c), mmap_mode="r")}
        img = preprocess(cache[c][i % CHUNK], bad_pixels=bp)
        P[i] = plane(img)
        Q[i] = img.reshape(50, 8, 80, 8).mean((1, 3)).reshape(10, 5, 16, 5).mean((1, 3)).ravel()
    temps = np.array([[np.nan if v is None else v for v in json.loads(r["temps_json"])] for r in rows], float)
    t = np.array([float(r["host_time"]) for r in rows])
    np.savez(o, P=P, Q=Q, t=t, temps=temps, kind=meta.get("session_kind", "?"))
    return "%s %d" % (sid, len(rows))


if __name__ == "__main__":
    with Pool(min(12, len(sys.argv) - 1)) as p:
        for r in p.imap_unordered(one, sys.argv[1:]): print(r, flush=True)
