#!/usr/bin/env python3
"""Examine stored metadata files + the giant 08000000 files for audio structure."""
import os, sys, struct
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from nhl2k_arc import Archive

def hexdump(data, base=0, limit=None):
    if limit: data = data[:limit]
    for i in range(0, len(data), 16):
        c = data[i:i+16]
        h = " ".join(f"{b:02X}" for b in c)
        a = "".join(chr(b) if 32<=b<127 else "." for b in c)
        print(f"  {base+i:06X}  {h:<47}  {a}")

def main():
    iso = sys.argv[1]; arc = Archive(iso)
    # 1) dump small stored metadata files fully
    for idx in (2353, 612, 775):
        e = arc.files[idx]; data = arc.read_file(e)
        print(f"\n### file #{idx} size={e.size} hash=0x{e.crc:08X} magic={data[:4].hex()} ###")
        hexdump(data, limit=min(e.size, 400))

    # 2) inspect giant 08000000 file header + scan for audio/codec markers
    for idx in (911, 2315):
        e = arc.files[idx]
        # read first 4KB + probe interior for markers, direct from ISO
        a, iso_off, avail = arc._stream_to_iso(e.offset)
        with open(iso, "rb") as f:
            f.seek(iso_off); head = f.read(4096)
        print(f"\n### GIANT #{idx} size={e.size} ({e.size/1e6:.1f}MB) hash=0x{e.crc:08X} ###")
        hexdump(head, limit=256)
        # search markers in first 8MB (read in stream order)
        markers = [b"RIFF", b"WAVE", b"XMA2", b"fmt ", b"data", b"seek",
                   b"WBND", b"XWB", b"\x0e\x48\x37\xc3", b"\xff\x3b\xef\x94"]
        span = min(e.size, 8*1024*1024)
        buf = arc_read_span(arc, e, 0, span)
        print(f"  marker scan in first {span/1e6:.1f}MB:")
        for m in markers:
            c = buf.count(m)
            first = buf.find(m)
            if c:
                print(f"    {m!r}: {c} hits, first@0x{first:X}")
        # how many 0E4837C3 blocks & their sizes in this span
        s=0; blks=[]
        while True:
            p = buf.find(b"\x0e\x48\x37\xc3", s)
            if p<0: break
            if p+12<=len(buf):
                unc,comp=struct.unpack_from(">II",buf,p+4); blks.append((p,unc,comp))
            s=p+4
        print(f"    0E4837C3 blocks in span: {len(blks)}; first 5: "
              + ", ".join(f'@0x{p:X}(u{u}/c{c})' for p,u,c in blks[:5]))

def arc_read_span(arc, e, rel, n):
    out=bytearray(); pos=e.offset+rel; rem=min(n, e.size-rel)
    with open(arc.iso_path,"rb") as f:
        while rem>0:
            a,iso_off,avail=arc._stream_to_iso(pos)
            take=min(rem,avail); f.seek(iso_off); out+=f.read(take)
            rem-=take; pos+=take
    return bytes(out)

if __name__ == "__main__":
    main()
