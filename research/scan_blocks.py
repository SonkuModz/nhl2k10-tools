#!/usr/bin/env python3
"""
scan_blocks.py -- Broad structural scan without needing the codec:
  * rank files by size (find the giant streamed resource)
  * for a sample of files, enumerate every 0E4837C3 block and record (unc,comp)
    to measure compression ratios and detect any STORED (uncompressed) blocks
  * dump the header of the biggest file and of each non-FF3BEF94 file
"""
import os, sys, struct
from collections import Counter
sys.path.insert(0, os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "nhl2k10"))
from nhl2k_arc import Archive

def head_of(arc, e, n):
    a, iso_off, avail = arc._stream_to_iso(e.offset)
    n = min(n, e.size, avail)
    with open(arc.iso_path, "rb") as f:
        f.seek(iso_off); return f.read(n)

def main():
    iso = sys.argv[1]; arc = Archive(iso)
    # rank by size
    bysize = sorted(arc.files, key=lambda e: -e.size)
    print("Top 15 largest files:")
    with open(iso, "rb") as f:
        for e in bysize[:15]:
            h = head_of(arc, e, 16)
            print(f"  #{e.index:<5} {e.size:>12} ({e.size/1e6:8.2f} MB) "
                  f"hash=0x{e.crc:08X} magic={h[:4].hex()} head={h.hex()}")
    print("\nSmallest 8 files:")
    for e in sorted(arc.files, key=lambda e: e.size)[:8]:
        h = head_of(arc, e, 16)
        print(f"  #{e.index:<5} {e.size:>6}  magic={h[:4].hex()} head={h.hex()}")

    # non-FF3BEF94 files: dump headers
    print("\nNon-FF3BEF94 files (full magic census with 32-byte head):")
    others = []
    for e in arc.files:
        h = head_of(arc, e, 32)
        if h[:4] != b"\xff\x3b\xef\x94":
            others.append((e, h))
    bymagic = Counter(h[:4] for _, h in others)
    for m, c in bymagic.most_common():
        print(f"  magic {m.hex()}: {c} files")
    print("  examples:")
    seen = set()
    for e, h in others:
        m = h[:4]
        if m in seen: continue
        seen.add(m)
        print(f"    #{e.index} size={e.size} magic={m.hex()} head={h.hex()}")

    # compression stats on a sample (every 40th file), scanning 0E4837C3 blocks
    print("\nCompression stats (sample every 40th file, scan 0E4837C3 blocks):")
    total_comp = total_unc = nblocks = stored = 0
    ratios = []
    sample = arc.files[::40]
    for e in sample:
        data = arc.read_file(e)
        start = 0
        while True:
            p = data.find(b"\x0e\x48\x37\xc3", start)
            if p < 0 or p+12 > len(data): break
            unc, comp = struct.unpack_from(">II", data, p+4)
            if 0 < comp < e.size*2 and 0 < unc < 200_000_000:
                nblocks += 1; total_comp += comp; total_unc += unc
                if unc <= comp:  # stored / expanded
                    stored += 1
                ratios.append(unc / comp if comp else 0)
            start = p + 4
    if nblocks:
        print(f"  sampled files={len(sample)} blocks={nblocks} stored(unc<=comp)={stored}")
        print(f"  mean ratio unc/comp = {sum(ratios)/len(ratios):.2f}  "
              f"min={min(ratios):.2f} max={max(ratios):.2f}")
        print(f"  (extrapolated total uncompressed ~ "
              f"{total_unc/total_comp*6.1:.1f} GB if ratio holds)")

if __name__ == "__main__":
    main()
