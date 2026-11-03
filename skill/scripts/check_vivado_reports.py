"""Check a routed Vivado design from its text reports and print a metrics table.

    python -I skill/scripts/check_vivado_reports.py <reports dir> [--max-lut 80] [--max-bram 80]
                                                    [--max-dsp 80] [--json out.json]

Reads timing_summary.rpt, utilization.rpt and (optional) power.rpt as written by
report_timing_summary / report_utilization / report_power. Fails (exit 1) when
any timing constraint is violated, the timing report says constraints are not
all met, or a resource is above its limit (percent of the device).

Standard library only; works for any AMD device whose reports use the usual
table layout (UltraScale+, 7-series, Versal).
"""
import argparse
import json
import os
import re
import sys


def read(path):
    with open(path, encoding="utf-8", errors="replace") as f:
        return f.read()


def timing(txt):
    """-> dict WNS/TNS/WHS/THS/WPWS (ns), all_met, clocks {name: MHz}."""
    m = re.search(r"Design Timing Summary.*?\n\s*-+\s+-+.*?\n\s*([-\d.\s]+)\n", txt, re.S)
    if not m:
        raise ValueError("no Design Timing Summary table")
    v = m.group(1).split()
    out = {"WNS": float(v[0]), "TNS": float(v[1]), "WHS": float(v[4]), "THS": float(v[5]),
           "WPWS": float(v[8])}
    out["all_met"] = "All user specified timing constraints are met." in txt
    clocks = {}
    cs = re.search(r"Clock Summary.*?\nClock\s+Waveform.*?\n-+.*?\n(.*?)\n\n", txt, re.S)
    if cs:
        for line in cs.group(1).splitlines():
            p = line.split()
            if len(p) >= 4:
                try:
                    clocks[p[0]] = float(p[-1])
                except ValueError:
                    pass
    out["clocks"] = clocks
    return out


RES = [("LUT", r"(?:CLB|Slice) LUTs\*?"), ("FF", r"(?:CLB|Slice) Registers"),
       ("BRAM", r"Block RAM Tile"), ("URAM", r"URAM"), ("DSP", r"DSPs")]


def utilization(txt):
    """-> {name: (used, available, percent)} for the resources found."""
    out = {}
    for name, pat in RES:
        m = re.search(r"^\|\s*" + pat + r"\s*\|\s*([\d.]+)\s*\|.*?\|\s*([\d.]+)\s*\|\s*([<\d.]+)\s*\|\s*$",
                      txt, re.M)
        if m:
            pct = m.group(3).lstrip("<")
            out[name] = (float(m.group(1)), float(m.group(2)), float(pct))
    return out


def power(txt):
    m = re.search(r"Total On-Chip Power \(W\)\s*\|\s*([\d.]+)", txt)
    return float(m.group(1)) if m else None


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("dir")
    for r in ("lut", "ff", "bram", "uram", "dsp"):
        ap.add_argument("--max-" + r, type=float, default=100.0, help="percent of device")
    ap.add_argument("--json")
    a = ap.parse_args()

    t = timing(read(os.path.join(a.dir, "timing_summary.rpt")))
    u = utilization(read(os.path.join(a.dir, "utilization.rpt")))
    pw_path = os.path.join(a.dir, "power.rpt")
    p = power(read(pw_path)) if os.path.exists(pw_path) else None

    fails = []
    if t["WNS"] < 0 or t["TNS"] < 0:
        fails.append("setup violated (WNS %.3f ns)" % t["WNS"])
    if t["WHS"] < 0 or t["THS"] < 0:
        fails.append("hold violated (WHS %.3f ns)" % t["WHS"])
    if t["WPWS"] < 0:
        fails.append("pulse width violated")
    if not t["all_met"]:
        fails.append("report does not say all constraints are met")
    for name, (used, avail, pct) in u.items():
        if pct > getattr(a, "max_" + name.lower()):
            fails.append("%s %.1f%% > %.1f%%" % (name, pct, getattr(a, "max_" + name.lower())))

    print("| metric | value |\n|---|---|")
    print("| WNS / TNS | %.3f / %.3f ns |" % (t["WNS"], t["TNS"]))
    print("| WHS / THS | %.3f / %.3f ns |" % (t["WHS"], t["THS"]))
    for c, f in sorted(t["clocks"].items()):
        print("| clock %s | %.3f MHz |" % (c, f))
    for name, (used, avail, pct) in u.items():
        print("| %s | %g / %g (%.2f%%) |" % (name, used, avail, pct))
    if p is not None:
        print("| on-chip power | %.3f W |" % p)
    print("RESULT", "PASS" if not fails else "FAIL: " + "; ".join(fails))

    if a.json:
        with open(a.json, "w") as f:
            json.dump({"timing": t, "utilization": {k: list(v) for k, v in u.items()},
                       "power_w": p, "fails": fails}, f, indent=1)
    return 1 if fails else 0


if __name__ == "__main__":
    sys.exit(main())
