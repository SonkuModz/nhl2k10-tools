#!/usr/bin/env python3
"""
vc_extract.py -- Full decompression of NHL 2K10 archive files.

For any archive file index, locate every 0E4837C3 compressed sub-block (in
order) and decompress each with vc_decomp, concatenating the results into the
fully-decompressed file. Handles both FF3BEF94-wrapped packages (multiple
sub-blocks) and standalone 0E4837C3 files.

Usage:
  python vc_extract.py <iso> <index> [outfile]      # extract one file
  python vc_extract.py <iso> --batch N              # validate N files
"""
import os, sys, struct
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from nhl2k_arc import Archive
import vc_decomp

def find_blocks(data):
    """Return offsets of all 0E4837C3 block headers (validated by size math)."""
    blocks = []
    pos = 0
    n = len(data)
    while True:
        p = data.find(b"\x0e\x48\x37\xc3", pos)
        if p < 0 or p + 0x14 > n:
            break
        magic, unc, comp, flags, offbits = struct.unpack_from(">IIIII", data, p)
        # sanity: plausible sizes and offbits
        if 0 < comp <= n - p + 0x100 and 0 < unc < 200_000_000 and 6 <= offbits <= 16:
            blocks.append((p, unc, comp, offbits))
            pos = p + comp          # jump past this block
        else:
            pos = p + 4
    return blocks

def extract_file(data):
    """Decompress an entire archive file into its uncompressed bytes."""
    blocks = find_blocks(data)
    out = bytearray()
    for (p, unc, comp, offbits) in blocks:
        dec, u, flags, ob = vc_decomp.decompress_at(data, p)
        if len(dec) != unc:
            raise ValueError("block@0x%X decoded %d != %d" % (p, len(dec), unc))
        out += dec
    return bytes(out), blocks

def main():
    iso = sys.argv[1]
    arc = Archive(iso)
    if sys.argv[2] == "--batch":
        N = int(sys.argv[3])
        ok = blk = 0
        total_in = total_out = 0
        fails = []
        import random
        idxs = list(range(len(arc.files)))
        random.seed(1); random.shuffle(idxs)
        for idx in idxs[:N]:
            e = arc.files[idx]
            head = arc.read_file_head(e, 4)
            if head[:4] != b"\xff\x3b\xef\x94" and head[:4] != b"\x0e\x48\x37\xc3":
                continue
            data = arc.read_file(e)
            try:
                out, blocks = extract_file(data)
                ok += 1; blk += len(blocks)
                total_in += e.size; total_out += len(out)
            except Exception as ex:
                fails.append((idx, str(ex)[:40]))
        print(f"decoded files OK={ok}  blocks={blk}  fails={len(fails)}")
        if total_out:
            print(f"total {total_in} -> {total_out} bytes ({total_out/total_in:.2f}x)")
        for f in fails[:10]:
            print("  FAIL", f)
        return
    idx = int(sys.argv[2])
    e = arc.files[idx]
    data = arc.read_file(e)
    out, blocks = extract_file(data)
    print(f"file#{idx}: {e.size} compressed -> {len(out)} decompressed "
          f"({len(blocks)} block(s), {len(out)/e.size:.2f}x)")
    print("first 64 bytes:", out[:64].hex())
    print("ascii:", "".join(chr(b) if 32<=b<127 else "." for b in out[:64]))
    if len(sys.argv) > 3:
        open(sys.argv[3], "wb").write(out)
        print("wrote", sys.argv[3])

if __name__ == "__main__":
    main()
