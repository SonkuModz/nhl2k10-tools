#!/usr/bin/env python3
"""
make_verify_manifest.py -- build (and check against) a reproducible texture
verification manifest.

The manifest records, for every texture we extract, its dimensions, format,
location, and the MD5 of its decoded payload. It contains **no game data** --
only hashes and metadata -- so it can live in the repository while the textures
themselves cannot.

That makes the headline claim independently checkable. Anyone with their own
disc image can run

    python tools/make_verify_manifest.py <iso> --check docs/verify_manifest.json

and confirm they get byte-identical output from their own copy. With Xenia GPU
dumps on hand, `--dumps <dir>` additionally re-derives the byte-exact score
against emulator ground truth.

Build a fresh manifest with:

    python tools/make_verify_manifest.py <iso> --out docs/verify_manifest.json
"""
import argparse
import glob
import hashlib
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from nhl2k_arc import Archive
import vc_texture as V

# Xenia's format names -> ours
XENIA_FMT = {
    "DXT1": "DXT1", "DXT2_3": "DXT3", "DXT4_5": "DXT5", "DXN": "ATI2",
    "8_8_8_8": "ARGB", "5_6_5": "RGB565", "4_4_4_4": "ARGB4444",
    "8": "L8", "8_8": "RG8", "DXT5A": "DXT5A", "DXT3A": "DXT3A",
}
BYTES_PER_BLOCK = {
    "DXT1": 8, "DXT2_3": 16, "DXT4_5": 16, "DXN": 16, "8_8_8_8": 4,
    "5_6_5": 2, "4_4_4_4": 2, "8": 1, "8_8": 2, "DXT5A": 8, "DXT3A": 8,
}

# Files sampled for the published figure. Kept explicit so the number is
# reproducible rather than dependent on a random seed.
SAMPLE = [0, 1, 2, 3, 5, 6, 7, 10, 11, 13, 17, 18, 87, 203, 261,
          290, 348, 507, 580, 1569]


def scan(iso, indices):
    """Extract every texture in `indices` -> list of manifest records."""
    arc = Archive(iso)
    out = []
    for fi in indices:
        try:
            raw = arc.read_file(arc.files[fi])
            dec, bounds = V.decompress_with_bounds(raw)
            res, _ = V.extract_textures(dec, bounds)
        except Exception as e:
            out.append({"file": fi, "error": str(e)})
            continue
        res_start = bounds[-1][0]
        for ti, (d, fourcc, blob) in enumerate(res):
            payload = blob[128:]
            mc = V.mip_consistency(dec, d["base"] + d["off"],
                                   d["w"], d["h"], d["fmt"])
            out.append({
                "file": fi,
                "tex": ti,
                "w": d["w"],
                "h": d["h"],
                "format": fourcc,
                "offset": d["base"] + d["off"] - res_start,
                "bytes": len(payload),
                "md5": hashlib.md5(payload).hexdigest(),
                "mip": None if mc is None else round(mc, 3),
            })
    return out


def index_dumps(dump_dir):
    """MD5 set of every Xenia dump payload we can parse."""
    seen = set()
    for jf in glob.glob(os.path.join(dump_dir, "*.json")):
        try:
            j = json.load(open(jf))
        except Exception:
            continue
        fmt = j.get("xenos_format")
        w, h = j.get("width"), j.get("height")
        bpb = BYTES_PER_BLOCK.get(fmt)
        if not bpb or not w or not h:
            continue
        bdim = 4 if (fmt.startswith("DXT") or fmt == "DXN") else 1
        n = max(1, w // bdim) * max(1, h // bdim) * bpb
        dd = jf[:-5] + ".dds"
        if not os.path.exists(dd):
            continue
        raw = open(dd, "rb").read()
        # DX10-fourcc dumps carry an extra 20-byte header
        off = 148 if raw[84:88] == b"DX10" else 128
        payload = raw[off:off + n]
        if len(payload) == n:
            seen.add(hashlib.md5(payload).hexdigest())
    return seen


def main():
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("iso")
    ap.add_argument("--out", help="write a fresh manifest here")
    ap.add_argument("--check", help="compare this ISO against an existing manifest")
    ap.add_argument("--dumps", help="Xenia texture-dump folder, for the byte-exact score")
    ap.add_argument("--files", help="comma-separated archive indices (default: the sample set)")
    ns = ap.parse_args()

    indices = ([int(x) for x in ns.files.split(",")] if ns.files else SAMPLE)
    records = scan(ns.iso, indices)
    textures = [r for r in records if "md5" in r]
    print("extracted %d textures from %d files" % (len(textures), len(indices)))

    if ns.dumps:
        seen = index_dumps(ns.dumps)
        hit = sum(1 for r in textures if r["md5"] in seen)
        print("byte-exact vs Xenia dumps: %d/%d (%.1f%%)  [%d dump payloads indexed]"
              % (hit, len(textures), 100.0 * hit / max(1, len(textures)), len(seen)))

    checkable = [r for r in textures if r.get("mip") is not None]
    good = sum(1 for r in checkable if r["mip"] > 0.5)
    if checkable:
        print("mip self-consistency: %d/%d (%.1f%%) -- needs no reference data"
              % (good, len(checkable), 100.0 * good / len(checkable)))

    if ns.check:
        ref = json.load(open(ns.check))
        old = {(r["file"], r["tex"]): r for r in ref["textures"]}
        new = {(r["file"], r["tex"]): r for r in textures}
        same = sum(1 for k in old if k in new and old[k]["md5"] == new[k]["md5"])
        print("\nagainst %s:" % ns.check)
        print("  %d/%d textures byte-identical" % (same, len(old)))
        missing = [k for k in old if k not in new]
        differing = [k for k in old if k in new and old[k]["md5"] != new[k]["md5"]]
        if missing:
            print("  %d missing here: %s" % (len(missing), missing[:8]))
        if differing:
            print("  %d differ: %s" % (len(differing), differing[:8]))
        if not missing and not differing:
            print("  PASS -- this build reproduces the published manifest exactly.")
        else:
            print("  MISMATCH -- a different game region, or a code change.")
            return 1

    if ns.out:
        doc = {
            "note": "Hashes and metadata only -- no game data. Reproduce with "
                    "tools/make_verify_manifest.py.",
            "iso_size": os.path.getsize(ns.iso),
            "files_sampled": indices,
            "texture_count": len(textures),
            "textures": textures,
        }
        with open(ns.out, "w", encoding="utf-8") as f:
            json.dump(doc, f, indent=1)
        print("\nwrote %s (%d records)" % (ns.out, len(textures)))
    return 0


if __name__ == "__main__":
    sys.exit(main())
