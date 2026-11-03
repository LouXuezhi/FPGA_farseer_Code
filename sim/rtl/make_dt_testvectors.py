"""Synthetic dT-shaped QCNN for RTL checks: 2 input planes (reference, current) -> 9 codes.

The weights are NOT a trained model. conv1..conv3 / FC1 come from the T1 qmodel;
conv0 sees both planes (w_ref = -w_cur + small noise, a frame-difference start),
FC2 is random with 9 outputs. Only the shapes and the integer arithmetic matter:
the RTL must match qcnn_int.forward_int bit for bit.

    python -I sim/rtl/make_dt_testvectors.py data/qmodel_t1.npz <t1 tv_input.mem> src/rtl/gen
"""
import sys
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "src" / "model"))
from qcnn_int import export_rtl, forward_int, load_qmodel  # noqa: E402

N_OUT = 9


def make_dt_qmodel(t1, seed=0):
    rng = np.random.default_rng(seed)
    q = [dict(layer) for layer in t1]
    w0 = q[0]["w"]                                                    # (C0, 1, 3, 3)
    w_ref = np.clip(-w0 + rng.integers(-4, 5, w0.shape), -127, 127)
    q[0]["w"] = np.concatenate([w_ref, w0], axis=1)                   # (C0, 2, 3, 3)
    hidden = q[-1]["w"].shape[1]
    q[-1]["w"] = rng.integers(-64, 65, (N_OUT, hidden, 1, 1))
    q[-1]["b"] = rng.integers(-20000, 20000, N_OUT)
    return q


def main(qmodel_path, frames_mem, out_dir, n_frame=16):
    t1 = load_qmodel(qmodel_path)
    x = np.array([int(v, 16) for v in Path(frames_mem).read_text().split()], np.uint8).reshape(-1, 56, 56)
    # pair (frame k, frame k+1): plane 0 reference, plane 1 current
    pairs = np.stack([x[:n_frame], np.roll(x, -1, axis=0)[:n_frame]], axis=1)
    q = make_dt_qmodel(t1)
    export_rtl(q, pairs, out_dir)
    out = forward_int(q, pairs)
    print("dT test model:", pairs.shape, "->", out.shape, "codes min/max", out.min(), out.max(),
          "distinct", len(np.unique(out)))


if __name__ == "__main__":
    main(*sys.argv[1:4])
