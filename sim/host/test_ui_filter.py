"""Display stabilisation (src/host/ui_filter.py, ps_gfx.Speckle gate / smoothing).

    python -I sim/host/test_ui_filter.py
"""
import sys
from pathlib import Path

import numpy as np

ROOT = Path(__file__).resolve().parents[2]
sys.path[:0] = [str(ROOT / "src" / "host")]
import ui_filter as F  # noqa: E402
import ps_gfx as G  # noqa: E402


def check(name, ok):
    print(("PASS " if ok else "FAIL ") + name)
    if not ok:
        raise SystemExit(1)


def main():
    # spike: one-sample outlier dropped, steady ramp and a confirmed step pass
    f = F.SpikeFilter(1, jump=0.4)
    x = [36.0] * 4 + [36.6, 36.0, 36.0]
    y = [f([v])[0] for v in x]
    check("spike: lone +0.6 outlier never shown", max(y) < 36.05 and f.dropped == 1)
    f = F.SpikeFilter(1, jump=0.4)
    ramp = [36 + 0.3 * i for i in range(12)]
    check("spike: 0.3/step ramp passes undelayed", [f([v])[0] for v in ramp] == ramp)
    f = F.SpikeFilter(1, jump=0.4)
    y = [f([v])[0] for v in [36.0] * 4 + [40.0] * 3]
    check("spike: real step shown one sample late", y[4] == 36.0 and y[5] == 40.0)
    f = F.SpikeFilter(1, jump=0.4)
    y = [f([v], bypass=[b])[0] for v, b in [(36.0, 0)] * 4 + [(40.0, 1)]]
    check("spike: alarm channel bypasses the hold", y[-1] == 40.0)

    # quantiser: 36.95 +- 0.02 noise does not toggle the last digit
    q = F.Quantiser(1)
    rng = np.random.RandomState(0)
    vals = [36.95 + 0.02 * rng.randn() for _ in range(200)]
    shown = [q.fmt(v) for v in vals]
    plain = ["%.1f" % v for v in vals]
    flips = lambda a: sum(x != y for x, y in zip(a, a[1:]))  # noqa: E731
    check("quantiser: noisy 36.95 toggles %d times (plain rounding %d)" % (flips(shown), flips(plain)),
          flips(shown) <= 4 and flips(plain) > 50)
    check("quantiser: real change follows", q.fmt(37.2) == "37.2")

    # status: alarm at once, held hold_s; rising needs on_s, clears after off_s below off
    s = F.Status(on=1.0, off=0.6, on_s=4, off_s=8, hold_s=15)
    check("status: alarm shown on the first flagged step", s(True, True, 5.0, 0.0) == "a")
    check("status: alarm held 15 s after the flag clears", s(True, False, 0.0, 14.0) == "a")
    check("status: alarm released after hold", s(True, False, 0.0, 16.0) == "n")
    s = F.Status(on=1.0, off=0.6, on_s=4, off_s=8, hold_s=15)
    seq = [s(True, False, r, 2.0 * i) for i, r in enumerate([1.1, 0.9, 1.1, 0.9, 1.1, 0.9])]
    check("status: rate flickering around 1.0 stays normal (%s)" % "".join(seq), set(seq) == {"n"})
    seq = [s(True, False, r, 20 + 2.0 * i) for i, r in enumerate([1.3] * 4 + [0.8] * 3 + [0.4] * 6)]
    check("status: sustained rise shown, 0.8 keeps it, clears after 8 s below 0.6 (%s)" % "".join(seq),
          "".join(seq) == "nnwwwwwwwwwnn")

    # axis: grows at once, shrinks only after 30 s
    r = F.StickyRange(step=1.0, span=4.0, pad=0.4, shrink_s=30)
    a = r(36.2, 36.8, 0)
    b = r(36.2, 41.0, 1)
    c = r(36.2, 36.8, 2)
    d = r(36.2, 36.8, 40)
    check("axis: %s -> grow %s -> hold %s -> shrink %s" % (a, b, c, d), b[1] >= 41.4 and c == b and d == a)

    # eased: glides, snaps on a large jump (re-calibration)
    e = F.Eased(tau=0.8, snap=5.0)
    e(36.0, 0.0)
    v1 = e(37.0, 0.2)
    v2 = e(37.0, 3.0)
    check("eased: 36 -> 37 glides (%.2f at 0.2 s, %.3f at 3 s)" % (v1, v2), 36.1 < v1 < 36.4 and v2 > 36.97)
    check("eased: re-calibration jump applied at once", e(45.0, 3.1) == 45.0)

    # speckle: brightness glitch gated, persistent change accepted, views smoothed
    sp = G.Speckle(ref_age=1e9)
    rng = np.random.RandomState(1)
    base = (60 + 40 * rng.rand(800, 640)).astype(np.uint8)
    for i in range(5):
        sp.push(float(i), base)
    v0 = sp.view("pseudo", (240, 150)).astype(np.float32)
    ok_black = sp.push(5.0, np.zeros_like(base))
    ok_bright = sp.push(6.0, np.clip(base.astype(np.int32) * 2, 0, 255).astype(np.uint8))
    v1 = sp.view("pseudo", (240, 150)).astype(np.float32)
    check("speckle: black frame and 2x exposure step gated", not ok_black and not ok_bright)
    check("speckle: view unchanged across gated frames", np.abs(v1 - v0).max() == 0)
    bright = np.clip(base.astype(np.int32) * 2, 0, 255).astype(np.uint8)
    acc = [sp.push(7.0 + i, bright) for i in range(F_GATE_N + 1)]
    check("speckle: persistent change accepted after %d frames" % F_GATE_N, acc[-1])
    noisy = [np.clip(base.astype(np.float32) + 25 * rng.randn(800, 640), 0, 255).astype(np.uint8) for _ in range(12)]
    sp2 = G.Speckle(ref_age=1e9)
    flick_raw, flick_ema, prev_r, prev_e = [], [], None, None
    for i, fr in enumerate(noisy):
        sp2.push(float(i), fr)
        r_ = G.INFERNO[np.clip(sp2._raw_view("pseudo", (240, 150)), 0, 255).astype(np.uint8)].astype(np.float32)
        e_ = sp2.view("pseudo", (240, 150)).astype(np.float32)
        if prev_r is not None and i > 4:
            flick_raw.append(np.abs(r_ - prev_r).mean())
            flick_ema.append(np.abs(e_ - prev_e).mean())
        prev_r, prev_e = r_, e_
    fr_, fe_ = np.mean(flick_raw), np.mean(flick_ema)
    check("speckle: frame-to-frame flicker %.1f -> %.1f (EMA)" % (fr_, fe_), fe_ < 0.7 * fr_)
    print("ALL PASS")


F_GATE_N = G.Speckle.GATE_N

if __name__ == "__main__":
    main()
