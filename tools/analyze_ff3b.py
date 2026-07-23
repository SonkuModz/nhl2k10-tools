#!/usr/bin/env python3
"""
analyze_ff3b.py -- Structural analysis of the FF3BEF94 resource format.

For every file in the archive, read the header and correlate fields:
  off 0x00 u32 magic
  off 0x04 u32 dir_size / header_size   (census 'type-ish' discriminator)
  off 0x08 u32 total_size               (hypothesis: == outer file size)
  off 0x14 u32 field14
  off 0x18 u32 field18
Also tally the first-dword ('magic') distribution and, for FF3BEF94 files,
tally the (field14, field18) pairs and the sub-magic found at off 0x04's target.
"""
import os, struct, sys
from collections import Counter
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from nhl2k_arc import Archive

def main():
    iso = sys.argv[1]
    arc = Archive(iso)
    magics = Counter()
    size_match = 0
    size_mismatch = 0
    dirsizes = Counter()
    f14 = Counter()
    f18 = Counter()
    submagic = Counter()
    submagic_by_dirsize = {}
    mismatch_examples = []
    with open(iso, "rb") as f:
        for e in arc.files:
            a, iso_off, avail = arc._stream_to_iso(e.offset)
            n = min(64, e.size, avail)
            f.seek(iso_off); head = f.read(n)
            if len(head) < 4:
                magics["<4bytes>"] += 1; continue
            m = struct.unpack_from(">I", head, 0)[0]
            magics[f"0x{m:08X}"] += 1
            if m == 0xFF3BEF94 and len(head) >= 0x1C:
                dirsize, total = struct.unpack_from(">II", head, 4)
                f14v, f18v = struct.unpack_from(">II", head, 0x14)
                dirsizes[dirsize] += 1
                f14[f14v] += 1
                f18[f18v] += 1
                if total == e.size: size_match += 1
                else:
                    size_mismatch += 1
                    if len(mismatch_examples) < 8:
                        mismatch_examples.append((e.index, e.size, total))
                # sub-magic at the dir_size offset (if within our read window we can't
                # always see it; read a few bytes there directly)
                if dirsize + 4 <= e.size and dirsize < 0x100000:
                    sub = arc_read_at(arc, e, dirsize, 4)
                    sm = struct.unpack_from(">I", sub, 0)[0] if len(sub) >= 4 else 0
                    submagic[f"0x{sm:08X}"] += 1
                    submagic_by_dirsize.setdefault(dirsize, Counter())[f"0x{sm:08X}"] += 1

    print(f"Total files: {len(arc.files)}\n")
    print("First-dword ('magic') distribution:")
    for k, v in magics.most_common():
        print(f"  {v:>6}  {k}")
    print(f"\nFF3BEF94 total_size == outer size:  match={size_match}  mismatch={size_mismatch}")
    for ix, sz, tot in mismatch_examples:
        print(f"    mismatch #{ix}: outer={sz} header_total={tot}")
    print(f"\ndir_size (@0x04) distribution -- candidate TYPE key (top 25):")
    for k, v in dirsizes.most_common(25):
        print(f"  {v:>6}  0x{k:X} ({k})")
    print(f"\nfield@0x14 distribution:")
    for k, v in f14.most_common(15):
        print(f"  {v:>6}  0x{k:X} ({k})")
    print(f"\nfield@0x18 distribution:")
    for k, v in f18.most_common(15):
        print(f"  {v:>6}  0x{k:X} ({k})")
    print(f"\nsub-magic @ dir_size offset distribution:")
    for k, v in submagic.most_common(20):
        print(f"  {v:>6}  {k}")
    print(f"\nsub-magic grouped by dir_size (which header-size implies which sub-format):")
    for ds in sorted(submagic_by_dirsize, key=lambda d: -sum(submagic_by_dirsize[d].values()))[:12]:
        inner = submagic_by_dirsize[ds]
        top = ", ".join(f"{k}:{v}" for k, v in inner.most_common(4))
        print(f"  dir_size 0x{ds:X}: {top}")

def arc_read_at(arc, entry, rel_off, n):
    """Read n bytes at rel_off within a file entry, direct from ISO."""
    a, iso_off, avail = arc._stream_to_iso(entry.offset + rel_off)
    if n <= avail:
        with open(arc.iso_path, "rb") as f:
            f.seek(iso_off); return f.read(n)
    return arc.read_file(entry)[rel_off:rel_off+n]

if __name__ == "__main__":
    main()
