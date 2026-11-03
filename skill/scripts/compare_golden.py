"""Bit-exact compare of hardware output against a golden reference.

    python -I skill/scripts/compare_golden.py <got> <want> [--width 32] [--signed] [--shape 16,9]

<got> / <want>: .mem ($readmemh hex, // comments allowed), .npy, or any text file with
whitespace-separated words (--radix 10 for decimal).
Prints match count, the first mismatches with their index (and multi-dim index
when --shape is given), and exits 1 on any difference.

Typical use: RTL testbench dumps its outputs with $fwrite("%h"), the reference
model writes the expected .mem, this script is the single pass/fail judge.
"""
import argparse
import sys

import numpy as np


def load(path, width, signed, radix):
    if path.endswith(".npy"):
        x = np.load(path).astype(np.int64).reshape(-1)
    else:
        vals = []
        with open(path) as f:
            for line in f:
                s = line.split("//")[0].strip()
                if s and not s.startswith("@"):                 # skip $readmemh address marks
                    vals += [int(t.replace("_", ""), radix) for t in s.split()]
        x = np.array(vals, np.int64)
    x = x & ((1 << width) - 1)                                   # compare as raw bits ...
    if signed:                                                   # ... or as two's complement
        x = np.where(x >= 1 << (width - 1), x - (1 << width), x)
    return x


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("got")
    ap.add_argument("want")
    ap.add_argument("--width", type=int, default=32)
    ap.add_argument("--signed", action="store_true")
    ap.add_argument("--radix", type=int, default=16, help="text files: 16 ($readmemh) or 10")
    ap.add_argument("--shape", help="e.g. 16,9 to report (frame, output) indices")
    ap.add_argument("--show", type=int, default=10)
    a = ap.parse_args()

    got, want = (load(p, a.width, a.signed, a.radix) for p in (a.got, a.want))
    n = min(len(got), len(want))
    bad = np.nonzero(got[:n] != want[:n])[0]
    shape = tuple(int(s) for s in a.shape.split(",")) if a.shape else None
    for i in bad[:a.show]:
        where = tuple(int(k) for k in np.unravel_index(i, shape)) if shape and i < np.prod(shape) else int(i)
        print("MISMATCH %s got %d want %d" % (where, got[i], want[i]))
    ok = len(bad) == 0 and len(got) == len(want)
    if len(got) != len(want):
        print("LENGTH got %d want %d" % (len(got), len(want)))
    print("RESULT %d/%d match%s" % (n - len(bad), len(want), "" if ok else " -> FAIL"))
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
