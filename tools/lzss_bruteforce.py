#!/usr/bin/env python3
"""
lzss_bruteforce.py -- Search common LZSS/LZ77 variants for the 0E4837C3 codec.

We know the exact uncompressed size, so a correct parameterization must consume
the compressed payload and emit exactly `unc` bytes. Search over:
  - flag bit order (MSB-first / LSB-first)
  - literal/match bit polarity
  - match encoding (2-byte: split of offset/length bits, min match)
Report any parameterization that reaches the target size cleanly.
"""
import os, sys, struct
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from nhl2k_arc import Archive

def decode_lzss(src, unc, msb_first, lit_is_one,
                off_bits, len_bits, min_match, add_len):
    """Generic 8-flag LZSS. Match = 2 bytes big-endian split into off/len."""
    out = bytearray()
    i = 0
    n = len(src)
    while len(out) < unc and i < n:
        flags = src[i]; i += 1
        for b in range(8):
            if len(out) >= unc: break
            if msb_first:
                bit = (flags >> (7 - b)) & 1
            else:
                bit = (flags >> b) & 1
            is_lit = (bit == 1) if lit_is_one else (bit == 0)
            if is_lit:
                if i >= n: return out
                out.append(src[i]); i += 1
            else:
                if i + 1 >= n: return out
                tok = (src[i] << 8) | src[i+1]; i += 2
                off = (tok >> len_bits) & ((1 << off_bits) - 1)
                ln = (tok & ((1 << len_bits) - 1)) + min_match + add_len
                if off == 0:
                    return out
                start = len(out) - off
                if start < 0: return out
                for k in range(ln):
                    out.append(out[start + k])
    return out

def main():
    iso = sys.argv[1]; idx = int(sys.argv[2]) if len(sys.argv)>2 else 4
    arc = Archive(iso); e = arc.files[idx]; data = arc.read_file(e)
    p = data.find(b"\x0e\x48\x37\xc3")
    unc, comp = struct.unpack_from(">II", data, p+4)
    # try payload starting right after 0x18 header, and a few nearby starts
    best = []
    for hdr in (0x18, 0x14, 0x10, 0x1C, 0x20):
        payload = data[p+hdr : p+comp]
        for msb in (True, False):
            for lit1 in (True, False):
                for off_bits in (11, 12, 13, 10, 4, 8):
                    len_bits = 16 - off_bits
                    for min_match in (1, 2, 3):
                        for add_len in (0,):
                            try:
                                out = decode_lzss(payload, unc, msb, lit1,
                                                  off_bits, len_bits, min_match, add_len)
                            except Exception:
                                continue
                            score = len(out)
                            if score == unc:
                                print(f"EXACT: hdr=0x{hdr:X} msb={msb} lit1={lit1} "
                                      f"off={off_bits} len={len_bits} mm={min_match} "
                                      f"-> {score} head={bytes(out[:16]).hex()}")
                            best.append((abs(unc-score), score, hdr, msb, lit1,
                                         off_bits, min_match))
    best.sort()
    print(f"\ntarget unc={unc}. Closest attempts:")
    for d, score, hdr, msb, lit1, ob, mm in best[:10]:
        print(f"  reached {score} (off by {d})  hdr=0x{hdr:X} msb={msb} lit1={lit1} off_bits={ob} mm={mm}")

if __name__ == "__main__":
    main()
