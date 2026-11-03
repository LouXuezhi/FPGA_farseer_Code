#!/usr/bin/env python3
"""Windowed QCNN alarm (FSF-WinNet): QCNN on (60 s mean pattern 120 s ago, latest 60 s mean pattern)
-> 9 route rise amplitudes, fused with the windows' 1-corr, then the N-in-a-row / cool-down rule.

  python win_qcnn.py cv      # leave-one-plateau-and-one-control-out CV -> out/cv.npz, out/cv.json
  python win_qcnn.py final   # train on all r3 sessions -> out/final/qmodel.npz + fusion params
QCNN shape is the RTL's: 2x56x56 uint8 -> conv 16,32,64,64 (3x3 s2) -> GAP -> fc 16 -> fc 9, 12-bit act.
"""
import glob, json, os, sys, time
import numpy as np
import torch
from torch.nn import functional as Fn
ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..", ".."))
WORK = os.environ.get("FSF_WIN_WORK", os.path.join(ROOT, "build", "win_alarm"))
sys.path.insert(0, os.path.join(ROOT, "src", "model"))
from train_qcnn import QConv, fake_quant, OUT_FRAC
from qcnn_int import predict_int, save_qmodel

C = os.path.join(WORK, "cache"); OUT = os.path.join(WORK, "out"); os.makedirs(OUT, exist_ok=True)
W, G, STEP_EVAL, STEP_TRAIN = 60, 120, 5, 2
ROUTES = [6, 15, 0, 11, 1, 8, 3, 2, 9]            # S7,S16,S1,S12,S2,S9,S4,S3,S10 (0-based)
Q_SCALE, Q_ZERO, Y_SPAN = 40.0, 128.0, 4.0         # input quantization; label y = |dT| / 4 degC
CTRL = ["20261103-013344", "20261103-032059", "20261103-074028", "20261103-104936"]
PLAT = ["20261103-015900", "20261103-034616", "20261103-053036", "20261103-080544", "20261103-092803"]
OTHER = ["20261103-050820", "20261103-065155"]     # iw, std: training only
dev = torch.device("cuda")


def z(x):
    x = x - x.mean(-1, keepdims=True); return x / (x.std(-1, keepdims=True) + 1e-6)


def wmean(X, ts, w):
    cs = np.cumsum(np.r_[np.zeros((1,) + X.shape[1:]), X], 0, dtype=np.float64)
    return ((cs[ts] - cs[ts - w]) / w).astype(np.float32)


def quant(Pm):
    n = len(Pm); f = z(Pm.reshape(n, -1)).reshape(n, 56, 56)
    return np.clip(np.round(f * Q_SCALE + Q_ZERO), 0, 255).astype(np.uint8)


def windows(sid, step):
    S = np.load(glob.glob(os.path.join(C, sid + "*.npz"))[0])
    P, Qb, T = S["P"], S["Q"], np.nan_to_num(S["temps"][:, :16])
    n = len(P); ts = np.arange(W + G, n + 1, step)
    x = np.stack([quant(wmean(P, ts - G, W)), quant(wmean(P, ts, W))], 1)          # [reference, current]
    a, b = z(wmean(Qb, ts, W)), z(wmean(Qb, ts - G, W))
    corr = 1 - (a * b).mean(1)
    dT = wmean(T, ts, W) - wmean(T, ts - G, W)
    if sid in CTRL: dT = np.zeros_like(dT)
    return dict(x=x, corr=corr.astype(np.float32), y9=np.abs(dT[:, ROUTES]).astype(np.float32),
                m=np.abs(dT).max(1), ts=ts)


class WinQCNN(torch.nn.Module):
    def __init__(self, widths=(16, 32, 64, 64), hidden=16, act_bits=12):
        super().__init__()
        w = [2, *widths]
        self.convs = torch.nn.ModuleList(QConv(w[i], w[i + 1], 3, 2, act_bits=act_bits) for i in range(len(w) - 1))
        self.fc1 = QConv(w[-1], hidden, 1, 1, act_bits=act_bits); self.fc2 = QConv(hidden, 9, 1, 1, relu=False)
        self.act_hi = 2 ** act_bits - 1

    def forward(self, x_u8, quant=False):
        x, f = x_u8.float() / 256, 8
        for c in self.convs: x, f = c(x, f, quant), c.f_out
        x = x.mean((2, 3), keepdim=True)
        if quant: x = fake_quant(x, f, 0, self.act_hi)
        x = self.fc1(x, f, quant)
        return self.fc2(x, self.fc1.f_out, quant).flatten(1)



def calibrate(m, x, pct=0.9999):
    for l in [*m.convs, m.fc1]: l.act_samples = []
    m.eval()
    with torch.no_grad():
        for i in range(0, len(x), 512): m(x[i:i + 512])
    for l in [*m.convs, m.fc1]:
        top = torch.quantile(torch.cat(l.act_samples)[:2_000_000].float(), pct).item()
        l.f_out = int(np.floor(np.log2(m.act_hi / top)))


def to_integer(m):
    q, f_in = [], 8
    for l in [*m.convs, "gap", m.fc1, m.fc2]:
        if l == "gap": q.append({"type": "gap", "shift": 4}); continue
        f_w = l.frac_w(); sh = f_in + f_w - l.f_out; assert sh >= 1
        q.append({"type": "conv", "stride": l.stride[0], "pad": l.padding[0], "relu": l.relu, "shift": sh, "act_hi": l.act_hi,
                  "w": np.clip(np.floor(l.weight.detach().cpu().numpy() * 2.0 ** f_w + 0.5), -128, 127).astype(np.int64),
                  "b": np.floor(l.bias.detach().cpu().numpy() * 2.0 ** (f_in + f_w) + 0.5).astype(np.int64)})
        f_in = l.f_out
    return q


def train(tr, seed=0, ep_f=40, ep_q=10, log=""):
    torch.manual_seed(seed); np.random.seed(seed)
    x = torch.from_numpy(np.concatenate([d["x"] for d in tr])).to(dev)
    y = torch.from_numpy(np.clip(np.concatenate([d["y9"] for d in tr]) / Y_SPAN, 0, 1)).to(dev)
    wgt = torch.ones(len(x), device=dev)
    m = WinQCNN().to(dev)

    def phase(ep, lr, q):
        opt = torch.optim.AdamW(m.parameters(), lr=lr, weight_decay=1e-3)
        sch = torch.optim.lr_scheduler.CosineAnnealingLR(opt, ep)
        for e in range(ep):
            m.train()
            for b in torch.randperm(len(x), device=dev).split(128):
                xb = x[b]
                sw = torch.rand(len(b), device=dev) < 0.5                  # |dT| is symmetric: swap the windows
                xb = torch.where(sw[:, None, None, None], xb.flip(1), xb)
                sh = np.random.randint(-3, 4, 2)                            # small fiber-end shift
                xb = torch.roll(xb, (int(sh[0]), int(sh[1])), (2, 3))
                loss = Fn.smooth_l1_loss(m(xb, q), y[b], beta=0.05)
                opt.zero_grad(set_to_none=True); loss.backward(); opt.step()
            sch.step()
        return float(loss)
    l1 = phase(ep_f, 2e-3, False); calibrate(m, x); l2 = phase(ep_q, 2e-4, True)
    print(log, "loss float %.4f qat %.4f n %d" % (l1, l2, len(x)), flush=True)
    return to_integer(m)


def cnn_score(q, d):
    return predict_int(q, d["x"]).max(1) / 2 ** OUT_FRAC * Y_SPAN     # max route rise, degC


def fuse_fit(tr_sc):
    """1-corr and CNN score standardized on training controls, weight by logistic regression (>=1 degC vs control)."""
    C_ = np.concatenate([np.c_[s["corr"], s["cnn"]] for s, isc in tr_sc if isc])
    mu, sd = C_.mean(0), C_.std(0) + 1e-6
    X = np.concatenate([np.c_[s["corr"], s["cnn"]] for s, _ in tr_sc]); X = (X - mu) / sd
    Y = np.concatenate([np.where(isc, 0, np.where(s["m"] >= 1, 1, -1)) for s, isc in tr_sc])
    k = Y >= 0; X, Y = X[k], Y[k].astype(float)
    w = np.zeros(3)
    for _ in range(500):                                                      # plain gradient descent, tiny problem
        p = 1 / (1 + np.exp(-(X @ w[:2] + w[2]))); g = np.r_[X.T @ (p - Y), (p - Y).sum()] / len(Y)
        w -= 1.0 * (g + 1e-3 * np.r_[w[:2], 0])
    return dict(mu=mu.tolist(), sd=sd.tolist(), w=w[:2].tolist())


def fused(fp, s):
    X = (np.c_[s["corr"], s["cnn"]] - fp["mu"]) / fp["sd"]
    return X @ np.array(fp["w"])


def main():
    mode = sys.argv[1]
    sids = CTRL + PLAT + OTHER
    t0 = time.time()
    DT = {s: windows(s, STEP_TRAIN) for s in sids}; DE = {s: windows(s, STEP_EVAL) for s in CTRL + PLAT}
    print("windows %.0fs" % (time.time() - t0), flush=True)
    if mode == "cv":
        res = {}
        for k, p in enumerate(PLAT):
            c = CTRL[k % len(CTRL)]
            tr = [s for s in sids if s not in (p, c)]
            q = train([DT[s] for s in tr], log="fold %d (%s, %s)" % (k, p, c))
            sc = {s: dict(corr=DT[s]["corr"], cnn=cnn_score(q, DT[s]), m=DT[s]["m"]) for s in tr}
            fp = fuse_fit([(sc[s], s in CTRL) for s in tr])
            thr_src = np.concatenate([fused(fp, sc[s]) for s in tr if s in CTRL])
            for s in (p, c):
                e = dict(corr=DE[s]["corr"], cnn=cnn_score(q, DE[s]), m=DE[s]["m"])
                res[s] = dict(corr=e["corr"], cnn=e["cnn"], fused=fused(fp, e), m=e["m"], ts=DE[s]["ts"],
                              thr995=np.percentile(thr_src, 99.5), thr99=np.percentile(thr_src, 99), fp=json.dumps(fp))
            print(" fuse", fp, flush=True)
        np.savez(os.path.join(OUT, "cv.npz"), **{"%s|%s" % (s, k): v for s, r in res.items() for k, v in r.items()})
    else:
        q = train([DT[s] for s in sids], log="final")
        os.makedirs(os.path.join(OUT, "final"), exist_ok=True)
        save_qmodel(q, os.path.join(OUT, "final", "qmodel.npz"))
        sc = {s: dict(corr=DT[s]["corr"], cnn=cnn_score(q, DT[s]), m=DT[s]["m"]) for s in sids}
        cc = np.concatenate([sc[s]["corr"] for s in CTRL]); cn = np.concatenate([sc[s]["cnn"] for s in CTRL])
        fp = dict(mu=[float(cc.mean()), float(cn.mean())], sd=[float(cc.std()), float(cn.std())], w=[1.0, 0.1])
        thr = np.percentile(np.concatenate([fused(fp, sc[s]) for s in CTRL]), 99.5)
        fp.update(threshold=float(thr), q_scale=Q_SCALE, q_zero=Q_ZERO, y_span=Y_SPAN, out_frac=OUT_FRAC, W=W, G=G)
        json.dump(fp, open(os.path.join(OUT, "final", "fusion.json"), "w"), indent=1)
        tv = np.concatenate([DT[s]["x"][::200] for s in (PLAT[0], CTRL[0])])[:16]
        np.save(os.path.join(OUT, "final", "tv_pairs_16x2x56x56.npy"), tv)
        print("final", fp)


if __name__ == "__main__":
    main()
