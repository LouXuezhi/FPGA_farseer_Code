"""List the AXI-Lite address map and IP parameters of a Vivado block design
from its .hwh file, without PYNQ.

    python3 -I skill/scripts/hwh_map.py design.hwh                 # address table
    python3 -I skill/scripts/hwh_map.py design.hwh --ip temp_dma   # + parameters of one IP
    python3 -I skill/scripts/hwh_map.py design.hwh --json map.json

PYNQ's Overlay reads the same .hwh to build ip_dict; this script gives the
same table on boards whose image has no PYNQ (or an old Python), so drivers
can take base addresses from the build instead of hard-coding them.

Standard library only, Python >= 3.5. Works for Zynq-7000, Zynq UltraScale+
and Versal .hwh files written by Vivado 2018.x - 2026.x.
"""
import argparse
import json
import sys
import xml.etree.ElementTree as ET


def parse(path):
    root = ET.parse(path).getroot()
    sysinfo = root.find(".//SYSTEMINFO")
    modules = {m.get("INSTANCE"): m for m in root.iter("MODULE")}
    regs, seen = [], set()
    for mod in modules.values():
        for mr in mod.iter("MEMRANGE"):
            if mr.get("MEMTYPE") != "REGISTER":
                continue
            inst = mr.get("INSTANCE")
            base, high = int(mr.get("BASEVALUE"), 16), int(mr.get("HIGHVALUE"), 16)
            key = (inst, base)
            if key in seen:
                continue
            seen.add(key)
            ip = modules.get(inst)               # None: an external AXI port of the block design
            if ip is not None and (ip.get("IS_PL") == "FALSE" or ip.get("MODTYPE", "").startswith("zynq")):
                continue                         # PS-internal windows (OCM, LPS) are not IP
            regs.append({"instance": inst, "type": ip.get("MODTYPE") if ip is not None else "(external port)",
                         "vlnv": ip.get("VLNV") if ip is not None else None,
                         "base": base, "size": high - base + 1, "master": mr.get("MASTERBUSINTERFACE"),
                         "block": mr.get("ADDRESSBLOCK")})
    regs.sort(key=lambda r: r["base"])
    info = dict(sysinfo.attrib) if sysinfo is not None else {}
    return info, regs, modules


def params(module):
    out = {}
    for p in module.iter("PARAMETER"):
        out[p.get("NAME")] = p.get("VALUE")
    return out


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("hwh")
    ap.add_argument("--ip", help="also print the parameters of this instance")
    ap.add_argument("--grep", default="", help="with --ip: only parameters containing this text")
    ap.add_argument("--json")
    a = ap.parse_args()

    info, regs, modules = parse(a.hwh)
    print("# %s %s%s%s" % (info.get("NAME", "?"), info.get("DEVICE", "?"), info.get("PACKAGE", ""),
                            info.get("SPEEDGRADE", "")))
    print("%-24s %-28s %-12s %-10s" % ("instance", "type", "base", "size"))
    for r in regs:
        print("%-24s %-28s 0x%08X  0x%X" % (r["instance"], r["type"], r["base"], r["size"]))
    if a.ip:
        if a.ip not in modules:
            print("no instance %r" % a.ip, file=sys.stderr)
            return 1
        for k, v in sorted(params(modules[a.ip]).items()):
            if a.grep.lower() in k.lower():
                print("  %s = %s" % (k, v))
    if a.json:
        with open(a.json, "w") as f:
            json.dump({"system": info, "ip": regs}, f, indent=1)
    return 0


if __name__ == "__main__":
    sys.exit(main())
