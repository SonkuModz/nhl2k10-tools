#!/usr/bin/env python3
"""
xma_analyze.py -- Inspect the 08000000 audio files.

Hypothesis: raw XMA2 = sequence of 2048-byte packets. Each packet's first
big-endian dword encodes:
  bits 31..26 (6)  frame_count
  bits 25..11 (15) frame_offset_in_bits
  bits 10..8  (3)  packet_metadata
  bits 7..0   (8)  packet_skip_count
We parse packet headers at 2048 intervals and check plausibility; also dump the
file head and tail to look for any format header / seek table.
"""
import os, sys, struct
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from nhl2k_arc import Archive

def parse_pkt(d):
    v = struct.unpack_from(">I", d, 0)[0]
    return (v >> 26) & 0x3F, (v >> 11) & 0x7FFF, (v >> 8) & 7, v & 0xFF

def read_span(arc, e, rel, n):
    out = bytearray(); pos = e.offset + rel; rem = min(n, e.size - rel)
    with open(arc.iso_path, "rb") as f:
        while rem > 0:
            a, io, avail = arc._stream_to_iso(pos)
            take = min(rem, avail); f.seek(io); out += f.read(take)
            rem -= take; pos += take
    return bytes(out)

def main():
    iso = sys.argv[1]; arc = Archive(iso)
    idxs = [int(x) for x in sys.argv[2:]] if len(sys.argv) > 2 else [911, 2315, 961]
    for idx in idxs:
        e = arc.files[idx]
        print(f"\n===== file #{idx} size={e.size} ({e.size/1e6:.1f}MB) "
              f"2048-aligned={e.size % 2048 == 0} packets={e.size//2048} =====")
        head = read_span(arc, e, 0, 4096)
        print("head 32B:", head[:32].hex())
        # parse first 12 packet headers
        print("packet headers (first 12):")
        for i in range(12):
            off = i * 2048
            if off + 4 > len(head):
                break
            fc, fo, md, skip = parse_pkt(head[off:off+4])
            print(f"  pkt {i:2} @0x{off:04X}: frames={fc:2} offset_bits={fo:5} "
                  f"meta={md} skip={skip}")
        # sample deeper packets
        deep = read_span(arc, e, (e.size // 2048 // 2) * 2048, 8)
        fc, fo, md, skip = parse_pkt(deep)
        print(f"  mid packet: frames={fc} offset_bits={fo} meta={md} skip={skip}")
        # tail
        tail = read_span(arc, e, max(0, e.size - 64), 64)
        print("tail 64B:", tail.hex())

if __name__ == "__main__":
    main()
