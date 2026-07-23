#!/usr/bin/env python3
"""Robust PPC disassembler: emit .long for undecodable words, never desync."""
import sys, struct
from capstone import Cs, CS_ARCH_PPC, CS_MODE_BIG_ENDIAN, CS_MODE_32
BASE = 0x82000000

def main():
    path, va = sys.argv[1], int(sys.argv[2], 16)
    length = int(sys.argv[3], 16) if len(sys.argv) > 3 else 0x400
    data = open(path, "rb").read()
    off = va - BASE
    md = Cs(CS_ARCH_PPC, CS_MODE_BIG_ENDIAN | CS_MODE_32)
    a = va
    end = va + length
    while a < end:
        o = a - BASE
        word = data[o:o+4]
        if len(word) < 4:
            break
        ins = next(md.disasm(word, a, 1), None)
        raw = struct.unpack(">I", word)[0]
        if ins is None:
            print(f"0x{a:08X}  {raw:08X}  .long    0x{raw:08X}")
        else:
            print(f"0x{a:08X}  {raw:08X}  {ins.mnemonic:<8} {ins.op_str}")
        a += 4

if __name__ == "__main__":
    main()
