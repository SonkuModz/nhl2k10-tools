#!/usr/bin/env python3
"""
id_hash.py -- Identify the filename hash used by the IFF manifest (e4791207).

Pulls UTF-16BE names and candidate 4-byte hashes from a manifest file, then
tests many hash functions / string normalizations to find which reproduces the
stored hashes.
"""
import os, sys, struct, zlib, binascii
sys.path.insert(0, os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "nhl2k10"))
from nhl2k_arc import Archive

def get_utf16_names(data):
    names = []
    i = 0
    while i + 2 <= len(data):
        # look for ascii-ish utf16be run: 00 XX 00 XX ... 00 00
        if data[i] == 0 and 0x20 <= data[i+1] < 0x7f:
            j = i; chars = []
            while j + 2 <= len(data) and data[j] == 0 and data[j+1] != 0:
                chars.append(chr(data[j+1])); j += 2
            if len(chars) >= 4:
                names.append("".join(chars))
                i = j
                continue
        i += 2
    return names

def candidate_hashes(data):
    hs = set()
    for i in range(0, len(data) - 4, 1):
        v = struct.unpack_from(">I", data, i)[0]
        if 0x01000000 <= v <= 0xFFFFFFFE:
            hs.add(v)
    return hs

# ---- hash functions ----
def fnv1_32(b, seed=0x811C9DC5):
    h = seed
    for c in b:
        h = ((h * 0x01000193) ^ c) & 0xFFFFFFFF
    return h
def fnv1a_32(b, seed=0x811C9DC5):
    h = seed
    for c in b:
        h = ((h ^ c) * 0x01000193) & 0xFFFFFFFF
    return h
def djb2(b):
    h = 5381
    for c in b:
        h = ((h * 33) + c) & 0xFFFFFFFF
    return h
def djb2x(b):
    h = 5381
    for c in b:
        h = ((h * 33) ^ c) & 0xFFFFFFFF
    return h
def sdbm(b):
    h = 0
    for c in b:
        h = (c + (h << 6) + (h << 16) - h) & 0xFFFFFFFF
    return h
def crc32(b): return zlib.crc32(b) & 0xFFFFFFFF
def crc32_neg(b): return (~zlib.crc32(b)) & 0xFFFFFFFF
def adler(b): return zlib.adler32(b) & 0xFFFFFFFF

HASHES = {"fnv1": fnv1_32, "fnv1a": fnv1a_32, "djb2": djb2, "djb2x": djb2x,
          "sdbm": sdbm, "crc32": crc32, "crc32_neg": crc32_neg, "adler": adler}

def norm_variants(name):
    base = name
    yield ("asis", base.encode("latin-1"))
    yield ("lower", base.lower().encode("latin-1"))
    yield ("upper", base.upper().encode("latin-1"))
    yield ("lower_bs", base.lower().replace("/", "\\").encode("latin-1"))
    yield ("utf16le", base.lower().encode("utf-16-le"))
    yield ("utf16be", base.lower().encode("utf-16-be"))
    # without extension
    stem = base.rsplit(".", 1)[0]
    yield ("stem_lower", stem.lower().encode("latin-1"))

def main():
    iso = sys.argv[1]; arc = Archive(iso)
    idxs = [int(x) for x in sys.argv[2:]] if len(sys.argv) > 2 else [2353, 612]
    names = []
    cands = set()
    for idx in idxs:
        d = arc.read_file(arc.files[idx])
        names += get_utf16_names(d)
        cands |= candidate_hashes(d)
    names = sorted(set(names))
    print(f"names ({len(names)}): {names}")
    print(f"candidate hash pool: {len(cands)} values\n")
    for hname, hfn in HASHES.items():
        for vname, _ in list(norm_variants(names[0])):
            matches = 0
            for nm in names:
                enc = dict(norm_variants(nm))[vname]
                if hfn(enc) in cands:
                    matches += 1
            if matches >= max(2, len(names)//2):
                print(f"  >>> {hname} / {vname}: {matches}/{len(names)} names matched a stored hash")
    # brute report: for first name, show all hash values
    print(f"\nAll hash values for '{names[0]}':")
    for hname, hfn in HASHES.items():
        for vname, enc in norm_variants(names[0]):
            print(f"  {hname:10} {vname:10} = 0x{hfn(enc):08X}")

if __name__ == "__main__":
    main()
