#!/usr/bin/env python3
"""Extract ASCII strings from the XEX basefile and grep for format keywords."""
import re, sys
data = open(sys.argv[1], "rb").read()
strs = [s.decode("latin-1") for s in re.findall(rb"[\x20-\x7e]{5,}", data)]
patterns = [r"\.iff", r"IFF", r"[Cc]ompress", r"zlib", r"\blzo\b", r"LZX",
            r"[Rr]efpack", r"[Dd]ecompress", r"[Vv]isual", r"[Cc]oncept",
            r"arena", r"[Jj]ersey", r"roster", r"XMA", r"WAVE", r"[Bb]ink",
            r"[Tt]exture", r"[Ss]hader", r"\.tga", r"\.dds", r"\.wav",
            r"[Ss]wizzl", r"\.bin", r"scene", r"anim", r"skelet"]
seen = set()
for pat in patterns:
    hits = [s for s in strs if re.search(pat, s)]
    uniq = []
    for h in hits:
        if h not in seen:
            seen.add(h); uniq.append(h)
    print(f"--- /{pat}/ : {len(hits)} total, {len(uniq)} new ---")
    for h in uniq[:15]:
        print("   ", h[:110])
