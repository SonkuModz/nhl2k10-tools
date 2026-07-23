#!/usr/bin/env python3
"""
find_func.py -- Locate code that references a given string in the flat PPC image.

The XEX basefile (default_base.bin) is the memory image loaded at 0x82000000.
VA(offset) = 0x82000000 + offset. We find the string, compute its VA, then scan
for the PPC lis/addi(or ori) immediate-load pair that materialises that address,
which marks code near the referencing function (e.g. the error path of the
decompressor).
"""
import sys, struct, re

BASE = 0x82000000

def find_strings(data, needle):
    out = []
    start = 0
    nb = needle.encode()
    while True:
        p = data.find(nb, start)
        if p < 0: break
        out.append(p); start = p + 1
    return out

def scan_refs(data, va):
    """Find lis rX,hi ; addi/ori rX,rX,lo pairs that build `va`."""
    hi = (va >> 16) & 0xFFFF
    lo = va & 0xFFFF
    hi_a = (hi + 1) & 0xFFFF  # for addi (sign-extended lo), @ha form
    refs = []
    # PPC big-endian, 4-byte instructions
    n = len(data)
    # Precompute: lis rD,imm  => opcode 15 (addis with rA=0): 001111 DDDDD 00000 IIIIIIIIIIIIIIII
    for i in range(0, n - 8, 4):
        w = struct.unpack_from(">I", data, i)[0]
        op = (w >> 26) & 0x3F
        if op != 15:  # addis
            continue
        rA = (w >> 16) & 0x1F
        if rA != 0:   # lis has rA=0
            continue
        rD = (w >> 21) & 0x1F
        imm = w & 0xFFFF
        if imm not in (hi, hi_a):
            continue
        # look ahead a few instructions for addi/ori rX,rD,lo
        for j in range(i + 4, min(i + 4 * 8, n - 4), 4):
            w2 = struct.unpack_from(">I", data, j)[0]
            op2 = (w2 >> 26) & 0x3F
            rA2 = (w2 >> 16) & 0x1F
            rD2 = (w2 >> 21) & 0x1F
            imm2 = w2 & 0xFFFF
            # addi (op 14) uses signed lo; ori (op 24) uses raw lo
            if op2 == 14 and rA2 == rD and imm == hi_a and imm2 == lo:
                refs.append((BASE + i, "lis+addi", rD)); break
            if op2 == 24 and rA2 == rD and imm == hi and imm2 == lo:
                refs.append((BASE + i, "lis+ori", rD)); break
            # stop if register overwritten by another lis
            if op2 == 15 and rD2 == rD:
                break
    return refs

def main():
    path = sys.argv[1]
    data = open(path, "rb").read()
    needles = sys.argv[2:] or ["VCFILEDEVICE::ReadAndDecompress"]
    for needle in needles:
        locs = find_strings(data, needle)
        print(f"\n=== '{needle}' : {len(locs)} occurrence(s) ===")
        for off in locs:
            va = BASE + off
            print(f"  string @ file 0x{off:X}  VA 0x{va:08X}")
            refs = scan_refs(data, va)
            if not refs:
                print("    (no direct lis/addi refs found)")
            for rva, kind, reg in refs:
                print(f"    ref {kind} r{reg} @ VA 0x{rva:08X} (file 0x{rva-BASE:X})")

if __name__ == "__main__":
    main()
