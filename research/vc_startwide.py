#!/usr/bin/env python3
"""Wide start-offset scan with the CONFIRMED grammar; report furthest progress."""
import os, sys, struct
sys.path.insert(0, os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "nhl2k10"))
from nhl2k_arc import Archive

def decode(buf, start, unc):
    out = bytearray(); i = start; n = len(buf)
    while len(out) < unc and i < n:
        ctrl = buf[i]; i += 1
        for b in range(8):
            if len(out) >= unc: break
            if (ctrl >> b) & 1:
                if i+1 >= n: return len(out), "eof"
                tok = (buf[i] << 8) | buf[i+1]; i += 2
                length = (tok >> 15) + 3
                offset = tok & 0x7FFF
                if offset == 0: return len(out), "zerooff"
                s = len(out) - offset
                if s < 0: return len(out), f"badoff{offset}"
                for k in range(length): out.append(out[s+k])
            else:
                if i >= n: return len(out), "eof"
                out.append(buf[i]); i += 1
    return len(out), ("OK" if len(out)==unc else "short")

def main():
    iso=sys.argv[1]; idx=int(sys.argv[2]) if len(sys.argv)>2 else 4
    arc=Archive(iso); e=arc.files[idx]; data=arc.read_file(e)
    pos=data.find(b"\x0e\x48\x37\xc3")
    unc,comp=struct.unpack_from(">II",data,pos+4)
    print(f"file#{idx} block@0x{pos:X} unc={unc} comp={comp} blockend=0x{pos+comp:X}")
    best=[]
    for hdr in range(0x08, min(0x120, comp)):
        reached,status=decode(data,pos+hdr,unc)
        best.append((reached,hdr,status))
    best.sort(reverse=True)
    print("top 12 starts by bytes-reached:")
    for reached,hdr,status in best[:12]:
        print(f"  hdr=0x{hdr:X} (start=0x{pos+hdr:X}): reached {reached}/{unc} [{status}]")

if __name__=="__main__":
    main()
