#!/usr/bin/env python3
"""Scan candidate compressed-stream start offsets for a clean VC-LZSS decode."""
import os, sys, struct
sys.path.insert(0, os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "nhl2k10"))
from nhl2k_arc import Archive

def decode(buf, start, unc):
    out = bytearray(); i = start; n = len(buf)
    while len(out) < unc and i < n:
        ctrl = buf[i]; i += 1
        for b in range(8):
            if len(out) >= unc: break
            if (ctrl >> b) & 1:
                if i + 1 >= n: return out, "eof"
                tok = (buf[i] << 8) | buf[i+1]; i += 2
                length = (tok >> 15) + 3
                offset = tok & 0x7FFF
                if offset == 0: return out, f"zerooff@{len(out)}"
                s = len(out) - offset
                if s < 0: return out, f"badoff({offset})@{len(out)}"
                for k in range(length): out.append(out[s+k])
            else:
                out.append(buf[i]); i += 1
    return out, ("ok" if len(out) == unc else f"short({len(out)})")

def main():
    iso = sys.argv[1]; idx = int(sys.argv[2]) if len(sys.argv)>2 else 4
    arc = Archive(iso); e = arc.files[idx]; data = arc.read_file(e)
    pos = data.find(b"\x0e\x48\x37\xc3")
    unc, comp = struct.unpack_from(">II", data, pos+4)
    print(f"file#{idx} block@0x{pos:X} unc={unc} comp={comp}")
    # also try MSB-first variant
    for order in ("lsb",):
        for hdr in range(0x08, 0x40, 1):
            start = pos + hdr
            out, status = decode(data, start, unc)
            if status == "ok":
                print(f"  [{order}] hdr=0x{hdr:X} start=0x{start:X}: {status}  "
                      f"head={bytes(out[:32]).hex()}")
    # verbose for a few header sizes
    print("\nverbose (all header guesses):")
    for hdr in (0x0C, 0x10, 0x14, 0x18, 0x1C, 0x20):
        out, status = decode(data, pos+hdr, unc)
        print(f"  hdr=0x{hdr:X}: {status} produced {len(out)}")

if __name__ == "__main__":
    main()
