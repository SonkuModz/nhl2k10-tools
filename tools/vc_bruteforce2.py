#!/usr/bin/env python3
"""
Brute-force the VC-LZSS token parameters + stream start against a known block.
Token (2-byte BE) split into [length_bits | offset_bits]; length = topfield + base.
Control byte: bit set = match, clear = literal; LSB-first (confirmed from asm).
Accept only decodes that reach exactly unc_size with all valid back-references.
"""
import os, sys, struct
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from nhl2k_arc import Archive

def decode(buf, start, unc, off_bits, base, msb):
    out = bytearray(); i = start; n = len(buf)
    off_mask = (1 << off_bits) - 1
    while len(out) < unc and i < n:
        ctrl = buf[i]; i += 1
        for b in range(8):
            if len(out) >= unc: break
            bit = (ctrl >> (7-b)) & 1 if msb else (ctrl >> b) & 1
            if bit:
                if i+1 >= n: return None
                tok = (buf[i] << 8) | buf[i+1]; i += 2
                length = (tok >> off_bits) + base
                offset = tok & off_mask
                if offset == 0: return None
                s = len(out) - offset
                if s < 0: return None
                for k in range(length):
                    out.append(out[s+k])
            else:
                if i >= n: return None
                out.append(buf[i]); i += 1
    return bytes(out) if len(out) == unc else None

def main():
    iso = sys.argv[1]; idx = int(sys.argv[2]) if len(sys.argv)>2 else 4
    arc = Archive(iso); e = arc.files[idx]; data = arc.read_file(e)
    pos = data.find(b"\x0e\x48\x37\xc3")
    unc, comp = struct.unpack_from(">II", data, pos+4)
    print(f"file#{idx} block@0x{pos:X} unc={unc} comp={comp}")
    found = []
    for hdr in range(0x06, 0x2C):
        for off_bits in (12, 13, 14, 15):
            for base in (1, 2, 3, 4):
                for msb in (False, True):
                    out = decode(data, pos+hdr, unc, off_bits, base, msb)
                    if out is not None:
                        # sanity: how many distinct bytes / printable ratio
                        distinct = len(set(out))
                        found.append((hdr, off_bits, base, msb, out))
    print(f"\n{len(found)} parameter combos produced exactly {unc} bytes:")
    for hdr, ob, base, msb, out in found:
        tag = f"hdr=0x{hdr:X} off_bits={ob} len_base={base} {'MSB' if msb else 'LSB'}"
        print(f"  {tag}: head={out[:24].hex()}  distinct={len(set(out))}")
    # If exactly one, dump more
    if len(found) == 1:
        out = found[0][4]
        print("\nFULL first 128 bytes:")
        print(out[:128].hex())
        print("".join(chr(b) if 32<=b<127 else "." for b in out[:128]))

if __name__ == "__main__":
    main()
