#!/usr/bin/env python3
"""Extract specific file indices from the archive (direct from ISO) and hexdump."""
import os, sys
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from nhl2k_arc import Archive

def hexdump(data, limit=None, base=0):
    if limit: data = data[:limit]
    for i in range(0, len(data), 16):
        c = data[i:i+16]
        hexs = " ".join(f"{b:02X}" for b in c)
        asci = "".join(chr(b) if 32 <= b < 127 else "." for b in c)
        print(f"{base+i:06X}  {hexs:<47}  {asci}")

def main():
    iso = sys.argv[1]
    arc = Archive(iso)
    indices = [int(x) for x in sys.argv[2:]] if len(sys.argv) > 2 else [4]
    for idx in indices:
        e = arc.files[idx]
        print(f"\n===== file #{idx} size={e.size} hash=0x{e.crc:08X} "
              f"stream_off={e.offset} =====")
        data = arc.read_file(e)
        print(f"(read {len(data)} bytes)")
        hexdump(data, limit=512)
        if e.size > 512:
            print("  ...")
            print(f"  [tail 64 bytes @ 0x{e.size-64:X}]")
            hexdump(data[-64:], base=e.size-64)

if __name__ == "__main__":
    main()
