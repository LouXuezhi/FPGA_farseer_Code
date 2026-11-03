"""Drawing helpers for the PS-rendered screen (fsf_ps_ui.py).

Python 3.6, numpy 1.13, PIL >= 5.1 (board) and current PIL / numpy (Mac preview).
OpenCV is used where present (board: 3.4.3); every call has a numpy fallback.
PIL 5.1 has no anchor=, getbbox, rounded_rectangle or outline width: avoided here.
"""
import os
import time

import numpy as np
from PIL import Image, ImageDraw, ImageFont

try:
    import cv2
except ImportError:
    cv2 = None

W, H = 800, 480
C = dict(bg=(10, 12, 15), pn=(17, 20, 25), pn2=(22, 26, 32), ln=(30, 35, 43), ln2=(42, 48, 57),
         tx=(231, 233, 236), dm=(124, 132, 142), dm2=(74, 81, 90), ac=(242, 163, 58), ok=(61, 190, 139),
         al=(255, 74, 74), sel=(27, 23, 18), alpn=(38, 17, 19), grey=(90, 98, 108))

# matplotlib inferno sampled at 17 points (0, 1/16, ..., 1)
_INFERNO_KEYS = [(0, 0, 4), (11, 7, 36), (33, 12, 74), (61, 9, 101), (87, 16, 110), (113, 25, 110),
                 (138, 34, 106), (163, 44, 97), (188, 55, 84), (210, 70, 68), (228, 90, 49), (241, 115, 29),
                 (249, 142, 9), (252, 172, 17), (249, 203, 53), (242, 234, 105), (252, 255, 164)]


def _lut():
    k = np.array(_INFERNO_KEYS, np.float64)
    x = np.linspace(0, 16, 256)
    return np.stack([np.interp(x, np.arange(17), k[:, c]) for c in range(3)], 1).round().astype(np.uint8)


INFERNO = _lut()                                            # (256, 3) RGB


def temp_rgb(t, lo=30.0, hi=50.0):
    """Pipe colour: lo..hi mapped into the upper 82 % of inferno so 36 degC reads as purple, not black."""
    x = 0.18 + 0.82 * min(1.0, max(0.0, (t - lo) / (hi - lo)))
    return tuple(int(v) for v in INFERNO[int(round(x * 255))])


# ---------------- image ops (OpenCV or numpy) ----------------
def box_mean(x, k):
    """k x k mean, edge padded; float32 in, float32 out."""
    if cv2 is not None:
        return cv2.boxFilter(x, -1, (k, k), borderType=cv2.BORDER_REPLICATE)
    p = k // 2
    y = np.pad(x, p, mode="edge").astype(np.float64)
    s = np.pad(y.cumsum(0).cumsum(1), ((1, 0), (1, 0)), mode="constant")
    h, w = x.shape
    return ((s[k:k + h, k:k + w] - s[:h, k:k + w] - s[k:k + h, :w] + s[:h, :w]) / (k * k)).astype(np.float32)


def blur(x, k):
    return box_mean(box_mean(x, k), k)                      # two box passes ~ Gaussian


def resize(a, size, smooth=True):
    """a: (h, w) or (h, w, 3) uint8; size (w, h)."""
    if a.shape[1] == size[0] and a.shape[0] == size[1]:
        return a
    if cv2 is not None:
        return cv2.resize(a, size, interpolation=cv2.INTER_LINEAR if smooth else cv2.INTER_NEAREST)
    return np.asarray(Image.fromarray(a).resize(size, Image.BILINEAR if smooth else Image.NEAREST))


def blend(a, b, alpha):
    """(1 - alpha) * a + alpha * b, uint8."""
    if cv2 is not None:
        return cv2.addWeighted(a, 1.0 - alpha, b, alpha, 0)
    return (a.astype(np.float32) * (1.0 - alpha) + b.astype(np.float32) * alpha).astype(np.uint8)


def to565(rgb):
    """(h, w, 3) RGB uint8 -> (h, w) little-endian RGB565 (DRM_FORMAT_RGB565)."""
    r = rgb[..., 0].astype(np.uint16)
    g = rgb[..., 1].astype(np.uint16)
    b = rgb[..., 2].astype(np.uint16)
    return (((r >> 3) << 11) | ((g >> 2) << 5) | (b >> 3)).astype("<u2")


# ---------------- text ----------------
CJK_FONTS = ["/usr/share/fonts/truetype/wqy/wqy-zenhei.ttc", "/System/Library/Fonts/Hiragino Sans GB.ttc",
             "/System/Library/Fonts/STHeiti Light.ttc", "/Library/Fonts/Arial Unicode.ttf"]
MONO_FONTS = ["/usr/share/fonts/truetype/dejavu/DejaVuSansMono.ttf", "/System/Library/Fonts/Menlo.ttc",
              "/System/Library/Fonts/Monaco.ttf"]


def _first(paths):
    for p in paths:
        if os.path.exists(p):
            return p
    raise RuntimeError("no font found in %s" % paths)


def _has_cjk(s):
    return any(u"\u3000" <= ch <= u"\u9fff" or u"\uff00" <= ch <= u"\uffef" for ch in s)


def pick_font(s, kind):
    """Mono has no Chinese: such strings go to the mixed (cjk) path."""
    if kind == "mono" and _has_cjk(s):
        return "cjk"
    return kind


class Text:
    """Text as cached L masks; a mask is drawn once per (string, font, size), then pasted.
    Chinese strings cost ~2 ms each on the board, so pages reuse them every redraw."""

    def __init__(self):
        self.paths = {"cjk": _first(CJK_FONTS), "mono": _first(MONO_FONTS)}
        self.fonts, self.cache = {}, {}

    def font(self, kind, size):
        key = (kind, size)
        if key not in self.fonts:
            self.fonts[key] = ImageFont.truetype(self.paths[kind], size)
        return self.fonts[key]

    def mask(self, s, kind, size, track=0):
        """L mask of s. kind "cjk": Chinese runs in the CJK font, everything else in mono
        (the CJK fonts space ° Δ › badly or lack them); runs share one baseline."""
        key = (s, kind, size, track)
        m = self.cache.get(key)
        if m is not None:
            return m
        if len(self.cache) > 3000:
            self.cache.clear()
        if kind == "cjk":
            runs, cur = [], None
            for ch in s:
                k = "cjk" if _has_cjk(ch) else "mono"
                if runs and k == cur:
                    runs[-1][1] += ch
                else:
                    runs.append([k, ch])
                    cur = k
        else:
            runs = [[kind, s]]
        parts = []
        for k, txt in runs:
            f = self.font(k, size if k == "cjk" else max(6, int(round(size * 0.94))))
            asc, desc = f.getmetrics()
            if track:
                ws = [self._width(f, ch) for ch in txt]
                w = sum(ws) + track * len(txt)
            else:
                ws, w = None, self._width(f, txt)
            parts.append((f, txt, ws, w, asc, desc))
        asc = max(p[4] for p in parts)
        desc = max(p[5] for p in parts)
        total = sum(p[3] for p in parts) - (track if track else 0)
        m = Image.new("L", (max(1, total), asc + desc), 0)
        d = ImageDraw.Draw(m)
        x = 0
        for f, txt, ws, w, a, _ in parts:
            y = asc - a
            if ws:
                xx = x
                for ch, cw in zip(txt, ws):
                    d.text((xx, y), ch, font=f, fill=255)
                    xx += cw + track
            else:
                d.text((x, y), txt, font=f, fill=255)
            x += w
        self.cache[key] = m
        return m

    @staticmethod
    def _width(f, s):
        if hasattr(f, "getlength"):
            return int(round(f.getlength(s)))
        return f.getsize(s)[0]

    def size(self, s, kind="cjk", size=12, track=0):
        return self.mask(s, pick_font(s, kind), size, track).size

    def draw(self, img, xy, s, kind="cjk", size=12, colour=C["tx"], align="l", track=0, mid=False):
        """xy = left / centre / right of the line box (align l / c / r); top, or vertical centre if mid."""
        if not s:
            return 0
        kind = pick_font(s, kind)
        m = self.mask(s, kind, size, track)
        x, y = int(xy[0]), int(xy[1])
        if align == "c":
            x -= m.size[0] // 2
        elif align == "r":
            x -= m.size[0]
        if mid:
            y -= m.size[1] // 2
        img.paste(colour, (x, y, x + m.size[0], y + m.size[1]), m)
        return m.size[0]


# ---------------- shapes ----------------
def rrect(d, box, r, fill, outline=None):
    """Rounded rectangle with an optional 1 px outline (PIL 5.1 compatible)."""
    x0, y0, x1, y1 = [int(v) for v in box]
    if outline is not None:
        _rr(d, (x0, y0, x1, y1), r, outline)
        _rr(d, (x0 + 1, y0 + 1, x1 - 1, y1 - 1), max(0, r - 1), fill)
    else:
        _rr(d, (x0, y0, x1, y1), r, fill)


def _rr(d, box, r, fill):
    x0, y0, x1, y1 = box
    r = min(r, (x1 - x0) // 2, (y1 - y0) // 2)
    if r <= 0:
        d.rectangle(box, fill=fill)
        return
    d.rectangle((x0 + r, y0, x1 - r, y1), fill=fill)
    d.rectangle((x0, y0 + r, x1, y1 - r), fill=fill)
    for cx, cy in ((x0, y0), (x1 - 2 * r, y0), (x0, y1 - 2 * r), (x1 - 2 * r, y1 - 2 * r)):
        d.ellipse((cx, cy, cx + 2 * r, cy + 2 * r), fill=fill)


def hline(d, x0, x1, y, fill):
    d.rectangle((x0, y, x1, y), fill=fill)


def tri_up(d, cx, cy, s, fill):
    d.polygon([(cx, cy - s), (cx + s, cy + s * 0.8), (cx - s, cy + s * 0.8)], fill=fill)


def tri_down(d, cx, cy, s, fill):
    d.polygon([(cx - s, cy - s * 0.6), (cx + s, cy - s * 0.6), (cx, cy + s * 0.8)], fill=fill)


def dot(d, cx, cy, r, fill):
    d.ellipse((cx - r, cy - r, cx + r, cy + r), fill=fill)


_LOGO = {}


def logo(size, bg=C["bg"]):
    """Node mark: horizon arc, a point of light on it, a baseline. Drawn at 4x, downsampled."""
    key = (size, bg)
    if key in _LOGO:
        return _LOGO[key]
    s = 4 * size
    im = Image.new("RGB", (s, s), bg)
    d = ImageDraw.Draw(im)
    k = s / 32.0
    pts = []
    for i in range(41):                                     # quadratic arc (3,18) -> (16,8) -> (29,18)
        t = i / 40.0
        x = (1 - t) ** 2 * 3 + 2 * (1 - t) * t * 16 + t * t * 29
        y = (1 - t) ** 2 * 18 + 2 * (1 - t) * t * 0 + t * t * 18
        pts.append((x * k, y * k))
    d.line(pts, fill=C["tx"], width=max(2, int(1.7 * k)))
    d.line([(3 * k, 24 * k), (29 * k, 24 * k)], fill=C["dm2"], width=max(2, int(1.2 * k)))
    dot(d, 16 * k, 18 * k, 3.3 * k, C["ac"])
    _LOGO[key] = im.resize((size, size), Image.LANCZOS)
    return _LOGO[key]


def chart_lines(img, box, lines, y_range, fill=None):
    """Lines drawn at 2x inside box and downsampled into img.
    lines: [(xs, ys, colour, width)], xs in 0..1 (None / NaN ys = gap).
    fill: (xs, ys, colour) polygon down to the bottom edge, drawn first."""
    x0, y0, x1, y1 = [int(v) for v in box]
    w, h = x1 - x0, y1 - y0
    sub = img.crop((x0, y0, x1, y1)).resize((2 * w, 2 * h), Image.NEAREST)
    d = ImageDraw.Draw(sub)
    lo, hi = y_range
    P = lambda x, v: (2 * w * x, min(2 * h - 1.0, max(0.0, 2 * h * (1 - (v - lo) / float(hi - lo)))))  # noqa: E731
    if fill is not None:
        xs, ys, c = fill
        pts = [P(x, v) for x, v in zip(xs, ys) if v is not None and v == v]
        if len(pts) > 1:
            d.polygon([(pts[0][0], 2 * h)] + pts + [(pts[-1][0], 2 * h)], fill=c)
    for xs, ys, c, lw in lines:
        seg = []
        for x, v in zip(xs, ys):
            if v is None or v != v:
                if len(seg) > 1:
                    d.line(seg, fill=c, width=int(round(2 * lw)))
                seg = []
                continue
            seg.append(P(x, v))
        if len(seg) > 1:
            d.line(seg, fill=c, width=int(round(2 * lw)))
    img.paste(sub.resize((w, h), Image.BILINEAR), (x0, y0))


# ---------------- speckle views ----------------
MODES = [("raw", "原始", "ROI 裁剪 + 归一化"), ("pseudo", "伪彩", "inferno 色表，亮度 → 颜色"),
         ("diff", "帧差", "|当前 - 参考|，平滑"), ("decorr", "局部去相关", "1 - ZNCC，对参考帧")]


class Speckle:
    """Camera frames -> display views. ROI: 8:5 window around the spot centroid (checked every
    60 s, moved only if the spot drifted more than ROI_MOVE px, and then glided over ~2 s).
    Reference for diff / decorr: the frame about ref_age s old.

    Stabilisation (display only):
      frame gate   a frame whose mean brightness is off by > GATE from the running mean, or
                   that is nearly all black / saturated, is skipped (camera reconnect,
                   exposure step); GATE_N skipped in a row = the image really changed: accept
      levels       auto-levels follow slowly (EMA), and reset only after an accepted jump
      temporal     every view is an EMA over frames (alpha per mode), so single-frame noise
                   and the reference swap do not flash; the reference swaps at most every
                   ref_age s and the decorrelation map cross-fades over ~1 s
      views are cached per (mode, size) and only recomputed when a frame arrived."""

    GATE, GATE_N, ROI_MOVE = 0.25, 6, 24
    ALPHA = {"raw": 0.6, "pseudo": 0.6, "diff": 0.35, "decorr": 0.3}

    ROI_H, ROI_W = 300, 480

    def __init__(self, ref_age=20.0):
        self.ref_age = ref_age
        self.hist = []                                       # (t, roi float32 300x480)
        self.cur = None
        self.roi = None
        self.roi_t = 0.0
        self.t_frames = []
        self.lo = self.hi = None
        self.stats = dict(rho=None, K=None, fps=0.0, ref_s=None)
        self.mean = None
        self.n_bad = 0
        self.skipped = 0
        self.roi_to = None
        self.views = {}                                      # (mode, size) -> (frame id, float32 EMA)
        self.n = 0

    def push(self, t, raw):
        """One camera frame; returns False if the frame was gated out."""
        pair = raw.shape[0] > raw.shape[1]                    # 800x640 RAW8: rows paired as in preprocess
        small = raw[0::8, 0::4] if pair else raw[0::4, 0::4]
        mu = float(small.mean())
        sat = float((small >= 250).mean())
        bad = mu < 3 or sat > 0.6
        if not bad and self.mean is not None and abs(mu - self.mean) > self.GATE * max(self.mean, 8.0):
            bad = True
        if bad:
            self.n_bad += 1
            self.skipped += 1
            if self.n_bad < self.GATE_N or mu < 3:
                return False
            self.lo = self.hi = None                         # persistent change: accept, re-level
            self.mean = None
            self.views.clear()
        self.n_bad = 0
        self.mean = mu if self.mean is None else 0.9 * self.mean + 0.1 * mu
        if self.roi is None or t - self.roi_t > 60:
            y0, x0 = self._find_roi(small.astype(np.float32), 4)
            if self.roi is None:
                self.roi = (y0, x0)
            elif max(abs(y0 - self.roi[0]), abs(x0 - self.roi[1])) > self.ROI_MOVE:
                self.roi_to = (y0, x0)                       # glide there, a few px per frame
            self.roi_t = t
        if self.roi_to is not None:
            y, x = self.roi
            ty, tx = self.roi_to
            step = lambda a, b: a + max(-8, min(8, b - a))   # noqa: E731
            self.roi = (step(y, ty), step(x, tx))
            if self.roi == self.roi_to:
                self.roi_to = None
            self.hist = [self.hist[-1]] if self.hist else []  # old reference is a different crop
        y0, x0 = self.roi
        if pair:
            r = raw[2 * y0:2 * (y0 + self.ROI_H), x0:x0 + self.ROI_W]
            cur = (r[0::2].astype(np.float32) + r[1::2]) * 0.5
        else:
            cur = raw[y0:y0 + self.ROI_H, x0:x0 + self.ROI_W].astype(np.float32)
        self.cur = cur
        self.n += 1
        self.hist.append((t, cur))
        while len(self.hist) > 2 and t - self.hist[1][0] >= self.ref_age:
            self.hist.pop(0)
        self.t_frames = [x for x in self.t_frames if t - x < 3.0] + [t]
        p1, p99 = np.percentile(cur[::6, ::6], (1, 99.5))
        a = 0.1 if self.lo is not None else 1.0              # slow auto-levels: no flicker
        self.lo = p1 if self.lo is None else (1 - a) * self.lo + a * p1
        self.hi = p99 if self.hi is None else (1 - a) * self.hi + a * p99
        ref = self.ref()
        rho = None
        if ref is not None:
            p, q = ref[::4, ::4].ravel(), cur[::4, ::4].ravel()
            p, q = p - p.mean(), q - q.mean()
            rho = float((p * q).sum() / (np.sqrt((p * p).sum() * (q * q).sum()) + 1e-6))
        m = cur.mean()
        self.stats = dict(rho=rho, K=float(cur.std() / (m + 1e-6)),
                          fps=(len(self.t_frames) - 1) / max(1e-3, self.t_frames[-1] - self.t_frames[0]),
                          ref_s=None if ref is None else t - self.hist[0][0], skipped=self.skipped)
        return True

    def _find_roi(self, img, k):
        """img: 1/k scale of the (row-paired) frame -> ROI corner at full scale."""
        sm = blur(img, 5)
        wgt = np.maximum(sm - np.percentile(sm, 60), 0)
        yy, xx = np.mgrid[:img.shape[0], :img.shape[1]]
        tot = wgt.sum() + 1e-6
        cy, cx = k * (yy * wgt).sum() / tot, k * (xx * wgt).sum() / tot
        H, W = k * img.shape[0], k * img.shape[1]
        y0 = int(min(max(cy - self.ROI_H // 2, 0), H - self.ROI_H))
        x0 = int(min(max(cx - self.ROI_W // 2, 0), W - self.ROI_W))
        return y0, x0

    def ref(self):
        if len(self.hist) < 2 or self.hist[-1][0] - self.hist[0][0] < 0.5:
            return None
        return self.hist[0][1]

    def view(self, mode, size):
        """RGB uint8 (h, w, 3) of the given (w, h), temporally smoothed; None until the first frame."""
        if self.cur is None:
            return None
        key = (mode, size)
        got = self.views.get(key)
        if got is not None and got[0] == self.n:
            return got[2]
        g = self._raw_view(mode, size)
        if g is None:
            return np.zeros((size[1], size[0], 3), np.uint8)
        if got is not None and got[1].shape == g.shape:
            a = self.ALPHA[mode]
            acc = got[1] + a * (g - got[1])
        else:
            acc = g
        idx = np.clip(acc, 0, 255).astype(np.uint8)
        rgb = np.stack([idx] * 3, -1) if mode == "raw" else INFERNO[idx]
        rgb = resize(rgb, size)
        self.views[key] = (self.n, acc, rgb)
        return rgb

    def _raw_view(self, mode, size):
        """Single-frame grey level 0..255 (float32) before colouring."""
        small = size[0] <= 240
        cur = self.cur[::2, ::2] if small else self.cur
        if mode in ("raw", "pseudo"):
            return np.clip((cur - self.lo) * (255.0 / max(1.0, self.hi - self.lo)), 0, 255)
        ref = self.ref()
        if ref is None:
            return None
        ref = ref[::2, ::2] if small else ref
        if mode == "diff":
            d = blur(np.abs(cur - ref), 3 if small else 5) / (ref.mean() + 1e-6)
            return np.clip(d * (255.0 / 0.6), 0, 255)
        p, q = (ref[::2, ::2], cur[::2, ::2]) if not small else (ref, cur)   # local 1 - ZNCC
        k = 15
        mp, mq = box_mean(p, k), box_mean(q, k)
        vp, vq = box_mean(p * p, k) - mp * mp, box_mean(q * q, k) - mq * mq
        z = (box_mean(p * q, k) - mp * mq) / np.sqrt(np.maximum(vp * vq, 1e-6))
        lit = blur(p, 3) > 0.15 * np.percentile(p, 99)
        return np.where(lit, np.clip((1 - z) * 255.0, 0, 255), 0).astype(np.float32)


def now_hms():
    return time.strftime("%H:%M:%S")
