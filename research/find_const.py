#!/usr/bin/env python3
"""Find PPC code that materialises a given 32-bit constant via lis + ori/addi."""
import sys, struct
BASE = 0x82000000

def find_const(data, const):
    hi = (const >> 16) & 0xFFFF
    lo = const & 0xFFFF
    hi_a = (hi + 1) & 0xFFFF
    hits = []
    n = len(data)
    for i in range(0, n - 8, 4):
        w = struct.unpack_from(">I", data, i)[0]
        op = (w >> 26) & 0x3F
        if op != 15:            # addis / lis
            continue
        rA = (w >> 16) & 0x1F
        if rA != 0:
            continue
        rD = (w >> 21) & 0x1F
        imm = w & 0xFFFF
        if imm not in (hi, hi_a):
            continue
        for j in range(i + 4, min(i + 4 * 10, n - 4), 4):
            w2 = struct.unpack_from(">I", data, j)[0]
            op2 = (w2 >> 26) & 0x3F
            rA2 = (w2 >> 16) & 0x1F
            rD2 = (w2 >> 21) & 0x1F
            imm2 = w2 & 0xFFFF
            if op2 == 24 and rA2 == rD and imm == hi and imm2 == lo:
                hits.append((BASE + i, "lis+ori", rD, BASE + j)); break
            if op2 == 14 and rA2 == rD and imm == hi_a and imm2 == lo:
                hits.append((BASE + i, "lis+addi", rD, BASE + j)); break
            if op2 == 15 and rD2 == rD:
                break
    return hits

def main():
    data = open(sys.argv[1], "rb").read()
    for cs in sys.argv[2:]:
        const = int(cs, 16)
        hits = find_const(data, const)
        print(f"\n=== constant 0x{const:08X}: {len(hits)} code site(s) ===")
        for va, kind, reg, va2 in hits:
            print(f"  {kind} r{reg} @ VA 0x{va:08X}..0x{va2:08X} (file 0x{va-BASE:X})")

if __name__ == "__main__":
    main()
