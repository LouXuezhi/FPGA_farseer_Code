"""Recursively download an OSF project's osfstorage into a local directory."""
import json
import os
import sys
import time
import urllib.request

API = "https://api.osf.io/v2/nodes/{}/files/osfstorage/"


def retry(fn, *args, tries=6):
    for i in range(tries):
        try:
            return fn(*args)
        except Exception as exc:  # network hiccups on OSF are common
            if i == tries - 1:
                raise
            print(f"retry {i + 1}: {exc}", flush=True)
            time.sleep(10 * (i + 1))


def _get(url):
    with urllib.request.urlopen(url, timeout=120) as r:
        return json.load(r)


def get(url):
    return retry(_get, url)


def walk(url, dest, listing):
    while url:
        d = get(url)
        for f in d["data"]:
            a = f["attributes"]
            path = os.path.join(dest, a["name"])
            if a["kind"] == "folder":
                os.makedirs(path, exist_ok=True)
                walk(f["relationships"]["files"]["links"]["related"]["href"], path, listing)
            else:
                listing.append((path, a["size"], f["links"]["download"]))
        url = d["links"].get("next")


def main(node, dest, dry=False):
    os.makedirs(dest, exist_ok=True)
    listing = []
    walk(API.format(node), dest, listing)
    total = sum(s or 0 for _, s, _ in listing)
    print(f"{node}: {len(listing)} files, {total/1e6:.1f} MB")
    with open(os.path.join(dest, "_listing.tsv"), "w") as fh:
        for p, s, u in listing:
            fh.write(f"{os.path.relpath(p, dest)}\t{s}\t{u}\n")
    if dry:
        return
    for p, s, u in listing:
        if os.path.exists(p) and os.path.getsize(p) == s:
            continue
        print("get", os.path.relpath(p, dest), s, flush=True)
        retry(urllib.request.urlretrieve, u, p)
        if os.path.getsize(p) != s:
            print("SIZE MISMATCH", p, os.path.getsize(p), s, flush=True)


if __name__ == "__main__":
    main(sys.argv[1], sys.argv[2], dry="--dry" in sys.argv)
