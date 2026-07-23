#!/usr/bin/env python3
"""
census.py -- Read the first bytes of every file in the AA00B3BF archive
(directly from the ISO) and classify by magic signature. Produces an
asset-type histogram plus a per-file CSV, with zero full-file extraction.
"""
import csv
import os
import struct
import sys
from collections import Counter

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from nhl2k_arc import Archive

# Signature table: (matcher(head)->bool, label)
def sig(head):
    h = head
    def be32(o=0): return struct.unpack_from(">I", h, o)[0] if len(h) >= o+4 else 0
    def le32(o=0): return struct.unpack_from("<I", h, o)[0] if len(h) >= o+4 else 0

    m4 = h[:4]
    if m4 == b"\xaa\x00\xb3\xbf": return "AA00B3BF-arc"
    if m4 == b"XEX2": return "XEX2-executable"
    if m4 == b"PIRS" or m4 == b"LIVE" or m4 == b"CON ": return "STFS-package"
    if m4 == b"DDS ": return "DDS-texture"
    if m4 == b"RIFF": return f"RIFF({h[8:12].decode('latin-1','replace')})"
    if m4 == b"XWB\x00" or h[:2] == b"WB": return "XWB-wavebank"
    if h[:3] == b"XMA" or m4 == b"XMA2": return "XMA-audio"
    if m4 == b"OggS": return "OGG-audio"
    if m4 == b"BIK" or m4 == b"BIKi": return "BINK-video"
    if m4 == b"\x89PNG": return "PNG"
    if m4[:2] == b"BM": return "BMP?"
    if m4 == b"bhd\x00" or m4 == b"bnk\x00": return "bank"
    if h[:2] == b"PK": return "ZIP"
    if h[:2] == b"\x1f\x8b": return "GZIP"
    if h[:2] in (b"\x78\x9c", b"\x78\x01", b"\x78\xda"): return "ZLIB"
    if m4 == b"FSB4" or m4 == b"FSB5": return "FMOD-FSB"
    if m4 == b"\x00\x00\x00\x0c" and h[4:8] == b"jP  ": return "JP2"
    if m4 == b"\x52\x53\x46\x00": return "RSF"
    # Nintendo/other common
    if m4 == b"NUP1" or m4 == b"NUS3": return "NUS3"
    # Heuristic: mostly-printable 4-byte tag
    if all(32 <= b < 127 for b in m4):
        return f"tag:{m4.decode('latin-1')}"
    return None

def main():
    iso = sys.argv[1]
    arc = Archive(iso)
    labels = Counter()
    rows = []
    unknown_heads = Counter()
    N = 32
    with open(iso, "rb") as f:
        for e in arc.files:
            # inline head read using arc's stream mapping but shared handle
            a, iso_off, avail = arc._stream_to_iso(e.offset)
            n = min(N, e.size, avail)
            f.seek(iso_off)
            head = f.read(n)
            label = sig(head) or "UNKNOWN"
            labels[label] += 1
            if label == "UNKNOWN":
                unknown_heads[head[:8].hex()] += 1
            rows.append((e.index, e.size, f"0x{e.crc:08X}", e.offset,
                         label, head[:16].hex()))
    # Output histogram
    print(f"Total files: {len(arc.files)}\n")
    print("Asset-type histogram (by magic of first bytes):")
    for label, cnt in labels.most_common():
        print(f"  {cnt:>6}  {label}")
    print("\nTop unknown 8-byte heads:")
    for hx, cnt in unknown_heads.most_common(25):
        print(f"  {cnt:>6}  {hx}")
    # CSV
    outdir = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "docs")
    csvp = os.path.join(outdir, "file_census.csv")
    with open(csvp, "w", newline="") as cf:
        w = csv.writer(cf)
        w.writerow(["index", "size", "hash", "stream_offset", "label", "head16"])
        w.writerows(rows)
    print(f"\nWrote {csvp}")

if __name__ == "__main__":
    main()
