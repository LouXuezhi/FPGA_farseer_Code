"""散斑帧预处理 v1（板端与训练共用，逐位一致）。Python 3.6 + numpy 1.13，无其他依赖。

    from speckle_preproc import preprocess, load_bad_pixels
    load_bad_pixels("bad_pixels.npy")        # 可选：固定坏点表，(k, 2) int，坐标在 (400, 640) 上
    img = preprocess(raw)                    # raw: (800, 640) uint8 RAW8 -> (400, 640) float32

步骤：行两两平均 -> 坏点 3x3 中值替换 -> 除以 sigma=40 的高斯模糊（平场）-> 零均值单位方差。
"""
import os

import numpy as np

FLAT_SIGMA = 40.0
_BLUR = {}
_BAD_PIXELS = None          # 默认坏点表 (k, 2) int，坐标在 (400, 640) 上；load_bad_pixels() 设置


def _blur_matrix(n, sigma=FLAT_SIGMA):
    """(n, n) 高斯模糊矩阵，边界按 reflect（scipy 'reflect' 同义：边缘像素重复）。"""
    key = (n, sigma)
    if key not in _BLUR:
        r = int(4 * sigma)
        k = np.exp(-0.5 * (np.arange(-r, r + 1) / sigma) ** 2); k /= k.sum()
        M = np.zeros((n, n))
        for i in range(n):
            for o, w in zip(range(-r, r + 1), k):
                j = i + o
                while j < 0 or j >= n: j = -j - 1 if j < 0 else 2 * n - j - 1
                M[i, j] += w
        _BLUR[key] = M.astype(np.float32)
    return _BLUR[key]


def gaussian_blur(img, sigma=FLAT_SIGMA):
    h, w = img.shape
    return _blur_matrix(h, sigma) @ img @ _blur_matrix(w, sigma).T


def square(raw):
    """(800, 640) uint8 → (400, 640) float32，行两两平均。"""
    a = np.asarray(raw, dtype=np.float32)
    return 0.5 * (a[0::2] + a[1::2])


def _median3x3(img, pts=None):
    """3×3 中值；pts=None 时全图，否则只算这些 (r, c) 点（坏点替换用）。"""
    p = np.pad(img, 1, mode="edge")
    h, w = img.shape
    if pts is None:
        st = np.stack([p[dy:dy + h, dx:dx + w] for dy in range(3) for dx in range(3)])
        return np.median(st, axis=0)
    r, c = pts[:, 0], pts[:, 1]
    st = np.stack([p[r + dy, c + dx] for dy in range(3) for dx in range(3)])
    return np.median(st, axis=0)


def preprocess(raw, bad_pixels=None):
    """v1：行平均 → 坏点 3×3 中值替换 → 除以 σ=40 高斯模糊（平场）→ 标准化。返回 (400, 640) float32。"""
    img = square(raw)
    bp = _BAD_PIXELS if bad_pixels is None else bad_pixels
    if bp is not None and len(bp):
        img[bp[:, 0], bp[:, 1]] = _median3x3(img, bp)
    flat = gaussian_blur(img)
    img = img / np.maximum(flat, max(float(flat.mean()) * 0.05, 1e-3))
    sd = float(img.std())
    return ((img - img.mean()) / (sd if sd > 0 else 1.0)).astype(np.float32)


def load_bad_pixels(path):
    """设置默认坏点表；文件不存在时不替换坏点。"""
    global _BAD_PIXELS
    _BAD_PIXELS = np.load(path) if path and os.path.exists(path) else None
    return _BAD_PIXELS
