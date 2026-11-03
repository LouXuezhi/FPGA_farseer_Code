"""Windowed alarm: QCNN on window-mean patterns + window decorrelation + N-in-a-row rule.

Every `every` s: window B = mean of the frames in (t-W, t], window A = mean of (t-G-W, t-G].
  cnn   = max of the QCNN's 9 route rises for the pair [A, B] (PL, one DMA call), degC
  corr  = 1 - corr(A, B) on 10x16 block means
  score = (corr - mu0) / sd0 + w1 * (cnn - mu1) / sd1     (mu/sd from no-heating controls)
Alarm after N evaluations in a row above `threshold`, then quiet for `cool` s.
Training and the offline evaluation: experiments/win_alarm (win_qcnn.py final writes fusion.json).

Python 3.6, numpy 1.13 (board).
"""
import collections
import json

import numpy as np

DEFAULTS = {"W": 60.0, "G": 120.0, "every": 5.0, "N": 3, "cool": 300.0, "in_h": 56, "grid": [10, 16],
            "min_cover": 0.5}


def plane(img, in_h):
    """(400, 640) standardised -> (in_h, in_h) float: centre crop + block mean (speckle_node.to_plane before quantization)."""
    k = img.shape[0] // in_h
    side = k * in_h
    y0, x0 = (img.shape[0] - side) // 2, (img.shape[1] - side) // 2
    return img[y0:y0 + side, x0:x0 + side].reshape(in_h, k, in_h, k).mean(axis=(1, 3)).astype(np.float32)


def blocks(img, grid):
    gh, gw = grid
    kh, kw = img.shape[0] // gh, img.shape[1] // gw
    return img[:kh * gh, :kw * gw].reshape(gh, kh, gw, kw).mean(axis=(1, 3)).ravel().astype(np.float64)


def _z(v):
    v = v - v.mean()
    return v / (v.std() + 1e-6)


class WinFusion:
    def __init__(self, cfg, backend):
        c = dict(DEFAULTS)
        c.update(cfg)
        self.c, self.backend = c, backend
        self.mu, self.sd, self.w = (np.asarray(c[k], np.float64) for k in ("mu", "sd", "w"))
        self.buf = collections.deque()          # (t, plane, blocks)
        self.t_eval = None
        self.run = 0
        self.t_alarm = None
        self.state = {"ready": False, "score": None, "threshold": c["threshold"], "alarm": False, "event": False,
                      "t_alarm": None, "cnn": None, "corr": None}

    @classmethod
    def load(cls, path, backend):
        return cls(json.load(open(path)), backend)

    def _mean(self, t0, t1):
        sel = [(p, b) for t, p, b in self.buf if t0 < t <= t1]
        if not sel:
            return None, None, 0
        return np.mean([p for p, _ in sel], axis=0), np.mean([b for _, b in sel], axis=0), len(sel)

    def quant(self, p):
        f = _z(p.ravel().astype(np.float64)).reshape(p.shape)
        return np.clip(np.round(f * self.c["q_scale"] + self.c["q_zero"]), 0, 255).astype(np.uint8)

    def push(self, t, img):
        c, st = self.c, self.state
        self.buf.append((t, plane(img, c["in_h"]), blocks(img, c["grid"])))
        while self.buf and self.buf[0][0] < t - c["G"] - c["W"] - 1.0:
            self.buf.popleft()
        st["event"] = False
        if self.t_eval is not None and t - self.t_eval < c["every"]:
            return st
        self.t_eval = t
        pb, bb, nb = self._mean(t - c["W"], t)
        pa, ba, na = self._mean(t - c["G"] - c["W"], t - c["G"])
        period = (t - self.buf[0][0]) / max(len(self.buf) - 1, 1)
        if pa is None or min(na, nb) < max(1.0, c["min_cover"] * c["W"] / max(period, 1e-3)):
            st["ready"], st["score"] = False, None
            self.run = 0
            return st
        corr = 1.0 - float(np.mean(_z(ba) * _z(bb)))
        codes = np.asarray(self.backend.run(np.stack([self.quant(pa), self.quant(pb)])[None])).reshape(-1)
        cnn = float(codes.max()) / 2 ** c["out_frac"] * c["y_span"]
        score = float(np.dot((np.array([corr, cnn]) - self.mu) / self.sd, self.w))
        self.run = self.run + 1 if score > c["threshold"] else 0
        if self.run >= c["N"] and (self.t_alarm is None or t - self.t_alarm >= c["cool"]):
            self.t_alarm, st["event"] = t, True
        st.update(ready=True, score=round(score, 4), alarm=self.run >= c["N"], t_alarm=self.t_alarm,
                  cnn=round(cnn, 4), corr=round(corr, 4), threshold=c["threshold"])
        return st
