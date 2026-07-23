#!/usr/bin/env python3
"""probe2 -- word-swapped deflate + structural look at a 0E4837C3 payload."""
import os, sys, zlib, struct
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from nhl2k_arc import Archive

def swap(data, n):
    b = bytearray(data)
    ln = len(b) - (len(b) % n)
    for i in range(0, ln, n):
        b[i:i+n] = b[i:i+n][::-1]
    return bytes(b)

def try_zlib(chunk, tag):
    for wbits in (-15, 15, 47):
        try:
            d = zlib.decompressobj(wbits)
            out = d.decompress(chunk) + d.flush()
            if len(out) > 64:
                print(f"  HIT {tag} wbits={wbits}: {len(out)} bytes head={out[:16].hex()}")
                return True
        except Exception:
            pass
    return False

def main():
    iso = sys.argv[1]; idx = int(sys.argv[2]) if len(sys.argv)>2 else 0
    arc = Archive(iso); e = arc.files[idx]; data = arc.read_file(e)
    p = data.find(b"\x0e\x48\x37\xc3")
    unc, comp = struct.unpack_from(">II", data, p+4)
    f0c, f10, f14 = struct.unpack_from(">III", data, p+12)
    print(f"file#{idx} 0E4837C3@0x{p:X} unc={unc} comp={comp} f0C={f0c} f10={f10} f14=0x{f14:08X}")
    payload = data[p+0x18: p+comp]
    print(f"payload {len(payload)} bytes, first 48: {payload[:48].hex()}")
    # try swaps on payload and on block-minus-header
    for name, blob in [("payload", payload),
                       ("payload.sw2", swap(payload,2)),
                       ("payload.sw4", swap(payload,4)),
                       ("blk", data[p+8:p+comp]),
                       ("blk.sw4", swap(data[p+8:p+comp],4))]:
        for start in range(0, 20):
            if try_zlib(blob[start:], f"{name}+{start}"):
                break
    # Structural: histogram of byte values in payload (entropy check)
    from collections import Counter
    c = Counter(payload)
    top = c.most_common(8)
    print(f"byte histogram top8: {[(hex(k),v) for k,v in top]}")
    print(f"distinct bytes: {len(c)} / 256  (low => not high-entropy compressed)")

if __name__ == "__main__":
    main()
