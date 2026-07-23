#!/usr/bin/env python3
"""Peek the first N bytes of each in-ISO file and hexdump, to spot magics."""
import sys
from xdvdfs import list_files, file_offset

ISO = sys.argv[1]
N = int(sys.argv[2]) if len(sys.argv) > 2 else 96

base, entries = list_files(ISO)
targets = ["0A", "0B", "1A", "1B", "default.xex", "nxeart"]
byname = {e.path: e for e in entries}

with open(ISO, "rb") as f:
    for name in targets:
        e = byname.get(name)
        if not e:
            continue
        f.seek(file_offset(base, e))
        data = f.read(N)
        print(f"\n=== {name}  size={e.size}  off=0x{file_offset(base, e):X} ===")
        for i in range(0, len(data), 16):
            chunk = data[i:i+16]
            hexs = " ".join(f"{b:02X}" for b in chunk)
            asci = "".join(chr(b) if 32 <= b < 127 else "." for b in chunk)
            print(f"{i:04X}  {hexs:<47}  {asci}")
