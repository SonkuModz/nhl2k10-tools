#!/usr/bin/env python3
"""
decompress_probe.py -- Try to identify the 0E4837C3 payload compression.

Strategy: extract a target file, then at every offset in the first 0x100 bytes,
attempt zlib (with and without header / various wbits), gzip, lzma, bz2.
Report any offset that decompresses to a plausible size (>= expected).
"""
import os, sys, zlib, lzma, bz2, struct
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from nhl2k_arc import Archive

def try_all(data, off):
    results = []
    chunk = data[off:]
    # raw deflate
    for wbits in (-15, -14, -13, -12, -11, -10, -9, 15, 31, 47):
        try:
            d = zlib.decompressobj(wbits)
            out = d.decompress(chunk)
            out += d.flush()
            if len(out) > 16:
                results.append((f"zlib wbits={wbits}", len(out), out[:16]))
        except Exception:
            pass
    try:
        out = lzma.decompress(chunk)
        results.append(("lzma", len(out), out[:16]))
    except Exception:
        pass
    try:
        out = bz2.decompress(chunk)
        results.append(("bz2", len(out), out[:16]))
    except Exception:
        pass
    return results

def main():
    iso = sys.argv[1]
    idx = int(sys.argv[2]) if len(sys.argv) > 2 else 4
    arc = Archive(iso)
    e = arc.files[idx]
    data = arc.read_file(e)
    print(f"file #{idx} size={e.size}")
    # locate 0E4837C3
    magic = b"\x0e\x48\x37\xc3"
    positions = []
    start = 0
    while True:
        p = data.find(magic, start)
        if p < 0: break
        positions.append(p); start = p + 4
    print(f"0E4837C3 found at offsets: {[hex(p) for p in positions]}")
    for p in positions[:3]:
        unc, comp = struct.unpack_from(">II", data, p+4)
        print(f"  @0x{p:X}: unc_size=0x{unc:X}({unc}) comp_size=0x{comp:X}({comp}) "
              f"next8={data[p+12:p+28].hex()}")
    # brute force decompression at many offsets
    print("\nBrute-force decompression probe (offsets 0..0x120):")
    hits = 0
    for off in range(0, min(0x120, e.size)):
        for name, ln, head in try_all(data, off):
            print(f"  off=0x{off:X}: {name} -> {ln} bytes, head={head.hex()}")
            hits += 1
    if not hits:
        print("  (no standard codec matched -- likely custom LZ)")

if __name__ == "__main__":
    main()
