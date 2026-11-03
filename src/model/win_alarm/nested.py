#!/usr/bin/env python3
"""Nested evaluation of the packaged alarm: per fold the CNN weight is picked on the other folds' held-out output,
threshold = 99.5 % of the training controls' fused score (from cv.npz thr source is per-fold; here re-derived on other folds' controls).
Writes per-session fused scores and summary -> nested.json / nested.npz.  usage: python nested.py <cv.npz> <outdir>"""
import json, sys
import numpy as np
CT = ["20261103-013344", "20261103-032059", "20261103-074028", "20261103-104936"]
PL = ["20261103-015900", "20261103-034616", "20261103-053036", "20261103-080544", "20261103-092803"]
FOLDS = [(p, CT[k % 4]) for k, p in enumerate(PL)]
WS = [0, 0.05, 0.1, 0.15, 0.2, 0.3, 0.5, 1]
STEP = 5
FIXED_W = float(sys.argv[3]) if len(sys.argv) > 3 else None
TAG = sys.argv[4] if len(sys.argv) > 4 else 'nested'
D = np.load(sys.argv[1]); R = {}
for k in D.files:
    s, f = k.split("|"); R.setdefault(s[:15], {})[f] = D[k]


def auc(p, n):
    r = np.argsort(np.argsort(np.r_[p, n])) + 1
    return float((r[:len(p)].sum() - len(p) * (len(p) + 1) / 2) / (len(p) * len(n)))


def norm(fs):
    c = np.concatenate([R[x]["corr"] for _, x in fs]); n = np.concatenate([R[x]["cnn"] for _, x in fs])
    return c.mean(), c.std(), n.mean(), n.std()


def fuse(r, nz, w): return (r["corr"] - nz[0]) / nz[1] + w * (r["cnn"] - nz[2]) / nz[3]


def quality(fs, w):
    nz = norm(fs); neg = np.concatenate([fuse(R[c], nz, w) for _, c in fs])
    big = np.concatenate([fuse(R[p], nz, w)[R[p]["m"] >= 1] for p, _ in fs])
    return (big > np.percentile(neg, 99)).mean() + auc(big, neg)


def alarms(s, thr, N=3, cool=300):
    out, run, last = [], 0, -1e9
    for i, v in enumerate(s):
        run = run + 1 if v > thr else 0
        if run >= N and i * STEP - last >= cool: out.append(i); last = i * STEP
    return out


S = {}
for k, (p, c) in enumerate(FOLDS):
    other = [f for j, f in enumerate(FOLDS) if j != k]
    w = FIXED_W if FIXED_W is not None else max(WS, key=lambda w: quality(other, w)); nz = norm(other)
    thr = np.percentile(np.concatenate([fuse(R[x], nz, w) for _, x in other]), 99.5)
    for s in (p, c) if c not in S else (p,):
        S[s] = dict(score=fuse(R[s], nz, w), m=R[s]["m"], ts=R[s]["ts"], thr=thr, w=w, cnn=R[s]["cnn"], corr=R[s]["corr"])
neg = np.concatenate([S[c]["score"] for c in CT]); thr99 = np.percentile(neg, 99)
big = np.concatenate([S[p]["score"][S[p]["m"] >= 1] for p in PL]); small = np.concatenate([S[p]["score"][(S[p]["m"] >= .5) & (S[p]["m"] < 1)] for p in PL])
bins = [(0, .25), (.25, .5), (.5, 1), (1, 2), (2, 99)]
detbin = [float(np.mean(np.concatenate([S[p]["score"][(S[p]["m"] >= a) & (S[p]["m"] < b)] for p in PL]) > thr99)) for a, b in bins]
fa, hours, ev, hit, lat = 0, 0.0, 0, 0, []
for s, r in S.items():
    a = alarms(r["score"], r["thr"])
    if s in CT: fa += len(a); hours += len(r["score"]) * STEP / 3600; continue
    m = r["m"]
    for i in range(1, len(m)):
        if m[i] >= 2 and m[i - 1] < 2 and (m[max(0, i - 24):i] < 1).any():
            ev += 1; t0 = max(j for j in range(max(0, i - 24), i + 1) if m[j] < 1)
            h = [x for x in a if t0 <= x <= t0 + 120]
            if h: hit += 1; lat.append((h[0] - t0) * STEP)
out = dict(auc_big=auc(big, neg), auc_small=auc(small, neg), det_big=float((big > thr99).mean()), det_small=float((small > thr99).mean()),
           det_bins=detbin, bins=bins, fa=fa, ctrl_hours=hours, events=ev, detected=hit, lat_med=float(np.median(lat)),
           lat_p10=float(np.percentile(lat, 10)), lat_p90=float(np.percentile(lat, 90)), weights=[S[p]["w"] for p in PL])
print(json.dumps(out, indent=1))
json.dump(out, open(sys.argv[2] + "/%s.json" % TAG, "w"), indent=1)
np.savez(sys.argv[2] + "/%s.npz" % TAG, **{"%s|%s" % (s, k): np.asarray(v) for s, r in S.items() for k, v in r.items()}, lat=np.array(lat))
