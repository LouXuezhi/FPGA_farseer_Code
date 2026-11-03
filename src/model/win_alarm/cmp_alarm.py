#!/usr/bin/env python3
"""Same windows, same folds, same alarm rule: packaged FSF alarm (nested.npz) vs other methods.
usage: python cmp_alarm.py <cmp_own cache> <nested.npz> <out.json>"""
import glob, json, os, sys, time
import numpy as np
import torch, torchvision
from torch.nn import functional as Fn
C, NEST, OUTF = sys.argv[1:4]
CT = ["20261103-013344", "20261103-032059", "20261103-074028", "20261103-104936"]
PL = ["20261103-015900", "20261103-034616", "20261103-053036", "20261103-080544", "20261103-092803"]
FOLDS = [(p, CT[k % 4]) for k, p in enumerate(PL)]
W, G, STEP = 60, 120, 5
dev = torch.device("cuda")


def L(s): return np.load(glob.glob(os.path.join(C, s + "*.npz"))[0], allow_pickle=True)
def z(X): X = X - X.mean(-1, keepdims=True); return X / (X.std(-1, keepdims=True) + 1e-6)
def wmean(X, ts, w):
    cs = np.cumsum(np.r_[np.zeros((1,) + X.shape[1:]), X], 0); return ((cs[ts] - cs[ts - w]) / w).astype(np.float32)
def auc(p, n):
    r = np.argsort(np.argsort(np.r_[p, n])) + 1
    return float((r[:len(p)].sum() - len(p) * (len(p) + 1) / 2) / (len(p) * len(n)))


NETS = {}
@torch.no_grad()
def feat(name, imgs):
    if name not in NETS:
        m = torchvision.models.resnet18(weights="DEFAULT") if name == "resnet18" else torchvision.models.vit_b_16(weights="DEFAULT")
        if name == "resnet18": m.fc = torch.nn.Identity()
        else: m.heads = torch.nn.Identity()
        NETS[name] = m.eval().to(dev)
    mean = torch.tensor([0.485, 0.456, 0.406], device=dev).view(1, 3, 1, 1); std = torch.tensor([0.229, 0.224, 0.225], device=dev).view(1, 3, 1, 1)
    out = []
    for i in range(0, len(imgs), 128):
        x = torch.from_numpy(imgs[i:i + 128]).float().to(dev)[:, None]
        x = (x - x.amin((2, 3), keepdim=True)) / (x.amax((2, 3), keepdim=True) - x.amin((2, 3), keepdim=True) + 1e-6)
        x = Fn.interpolate(x, size=(224, 224), mode="bilinear", align_corners=False).expand(-1, 3, -1, -1)
        out.append(NETS[name]((x - mean) / std).float().cpu())
    return torch.cat(out).numpy()


def build(sid):
    S = L(sid); X = S["X"].reshape(-1, 50, 80).astype(np.float32); n = len(X)
    ts = np.arange(W + G, n + 1, STEP); T = np.nan_to_num(S["temps"][:, :16])
    dT = wmean(T, ts, W) - wmean(T, ts - G, W)
    if sid in CT: dT = np.zeros_like(dT)
    full = z(X.reshape(n, -1)); sc = {}
    sc["逐帧相关 ZNCC"] = 1 - (full[ts - 1] * full[ts - 11]).mean(1)
    A, B = wmean(X, ts, W), wmean(X, ts - G, W)
    sc["窗口均方差"] = ((A - B) ** 2).reshape(len(ts), -1).mean(1)
    for nm, lab in (("resnet18", "ResNet-18 特征距离"), ("vit_b16", "ViT-B/16 特征距离")):
        fa, fb = feat(nm, A), feat(nm, B)
        sc[lab] = 1 - (fa * fb).sum(1) / (np.linalg.norm(fa, axis=1) * np.linalg.norm(fb, axis=1) + 1e-9)
    pair = np.stack([z(A.reshape(len(ts), -1)).reshape(-1, 50, 80), z(B.reshape(len(ts), -1)).reshape(-1, 50, 80)], 1)
    return dict(sc=sc, m=np.abs(dT).max(1), pair=pair.astype(np.float16), n=len(ts))


def finetune(arch, tr, te, seed=0, ep=12):
    torch.manual_seed(seed)
    x = torch.from_numpy(np.concatenate([D[s]["pair"] for s in tr])).float()
    y = torch.from_numpy(np.clip(np.concatenate([D[s]["m"] for s in tr]) / 4, 0, 1)).float()
    if arch == "resnet18":
        m = torchvision.models.resnet18(weights="DEFAULT"); m.fc = torch.nn.Linear(512, 1)
    else:
        m = torchvision.models.mobilenet_v3_small(weights="DEFAULT"); m.classifier[-1] = torch.nn.Linear(1024, 1)
    m = m.to(dev); opt = torch.optim.AdamW(m.parameters(), 3e-4, weight_decay=1e-2)
    sch = torch.optim.lr_scheduler.CosineAnnealingLR(opt, ep)
    def inp(xb):
        xb = Fn.interpolate(xb.to(dev), size=(112, 176), mode="bilinear", align_corners=False)
        return torch.cat([xb, xb[:, 1:] - xb[:, :1]], 1)
    for e in range(ep):
        m.train()
        for b in torch.randperm(len(x)).split(64):
            xb = x[b]; sw = torch.rand(len(b)) < 0.5; xb = torch.where(sw[:, None, None, None], xb.flip(1), xb)
            loss = Fn.smooth_l1_loss(m(inp(xb)).squeeze(1), y[b].to(dev), beta=0.05)
            opt.zero_grad(); loss.backward(); opt.step()
        sch.step()
    m.eval(); out = {}
    with torch.no_grad():
        for s in te:
            xs = torch.from_numpy(D[s]["pair"]).float()
            out[s] = torch.cat([m(inp(xs[i:i + 256])).squeeze(1).cpu() for i in range(0, len(xs), 256)]).numpy()
    return out


t0 = time.time()
D = {s: build(s) for s in CT + PL}
print("built %.0fs" % (time.time() - t0), flush=True)
N = np.load(NEST)
for s in CT + PL:
    D[s]["sc"]["本设计"] = N["%s|score" % [k for k in N.files if k.startswith(s)][0].split("|")[0]]
    assert len(D[s]["sc"]["本设计"]) == D[s]["n"], (s, len(D[s]["sc"]["本设计"]), D[s]["n"])
# supervised baselines, same folds as ours
for arch, lab in (("resnet18", "ResNet-18 微调"), ("mnv3", "MobileNetV3-S 微调")):
    for k, (p, c) in enumerate(FOLDS):
        tr = [s for s in CT + PL if s not in (p, c)]
        o = finetune(arch, tr, (p, c))
        for s, v in o.items():
            if s == c and lab in D[s]["sc"]: D[s]["sc"][lab] = (D[s]["sc"][lab] + v) / 2
            else: D[s]["sc"][lab] = v
        print(lab, "fold", k, "%.0fs" % (time.time() - t0), flush=True)


def alarms(v, thr, N_=3, cool=300):
    a, run, last = [], 0, -1e9
    for i, x in enumerate(v):
        run = run + 1 if x > thr else 0
        if run >= N_ and i * STEP - last >= cool: a.append(i); last = i * STEP
    return a


rows = {}; roc = {}
for meth in D[CT[0]]["sc"]:
    neg = np.concatenate([D[c]["sc"][meth] for c in CT])
    big = np.concatenate([D[p]["sc"][meth][D[p]["m"] >= 1] for p in PL])
    small = np.concatenate([D[p]["sc"][meth][(D[p]["m"] >= .5) & (D[p]["m"] < 1)] for p in PL])
    thr99 = np.percentile(neg, 99)
    fa = hours = ev = hit = 0; lat = []
    for k, (p, c) in enumerate(FOLDS):           # threshold from the other folds' controls (no peeking)
        oc = sorted({x for j, (_, x) in enumerate(FOLDS) if j != k} - {c})
        thr = np.percentile(np.concatenate([D[x]["sc"][meth] for x in oc]), 99.5)
        for s in ((p, c) if k < 4 else (p,)):
            a = alarms(D[s]["sc"][meth], thr)
            if s in CT: fa += len(a); hours += D[s]["n"] * STEP / 3600; continue
            m = D[s]["m"]
            for i in range(1, len(m)):
                if m[i] >= 2 and m[i - 1] < 2 and (m[max(0, i - 24):i] < 1).any():
                    ev += 1; t0_ = max(j for j in range(max(0, i - 24), i + 1) if m[j] < 1)
                    h = [x for x in a if t0_ <= x <= t0_ + 120]
                    if h: hit += 1; lat.append((h[0] - t0_) * STEP)
    rows[meth] = dict(auc_big=auc(big, neg), auc_small=auc(small, neg), det_big=float((big > thr99).mean()),
                      det_small=float((small > thr99).mean()), fa=fa, ctrl_hours=hours, fa_per_h=fa / hours,
                      events=ev, detected=hit, lat_med=float(np.median(lat)) if lat else None)
    roc[meth] = (neg, big)
    print("%-20s AUC %.3f/%.3f det %.0f%%/%.0f%% FA %d/%.1fh events %d/%d lat %s" % (
        meth, rows[meth]["auc_big"], rows[meth]["auc_small"], 100 * rows[meth]["det_big"], 100 * rows[meth]["det_small"],
        fa, hours, hit, ev, rows[meth]["lat_med"]), flush=True)
json.dump(rows, open(OUTF, "w"), ensure_ascii=False, indent=1)
np.savez(OUTF.replace(".json", "_scores.npz"), **{"%s|%s" % (s, m): D[s]["sc"][m] for s in CT + PL for m in D[s]["sc"]}, **{"%s|m" % s: D[s]["m"] for s in CT + PL})
np.savez(OUTF.replace(".json", "_roc.npz"), **{"%s|%s" % (m, k): v for m, (n, b) in roc.items() for k, v in (("neg", n), ("big", b))})
