#!/usr/bin/env python3
"""Disassemble a VA window of the flat PPC image with capstone (PPC BE)."""
import sys
from capstone import Cs, CS_ARCH_PPC, CS_MODE_BIG_ENDIAN, CS_MODE_32
BASE = 0x82000000

def main():
    path = sys.argv[1]
    va = int(sys.argv[2], 16)
    length = int(sys.argv[3], 16) if len(sys.argv) > 3 else 0x400
    data = open(path, "rb").read()
    off = va - BASE
    code = data[off: off + length]
    md = Cs(CS_ARCH_PPC, CS_MODE_BIG_ENDIAN | CS_MODE_32)
    md.detail = False
    for ins in md.disasm(code, va):
        raw = int.from_bytes(ins.bytes, "big")
        print(f"0x{ins.address:08X}  {raw:08X}  {ins.mnemonic:<8} {ins.op_str}")

if __name__ == "__main__":
    main()
