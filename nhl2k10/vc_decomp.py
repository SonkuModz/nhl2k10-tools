#!/usr/bin/env python3
"""
vc_decomp.py -- Visual Concepts 0E4837C3 decompressor (NHL 2K10 / VC IFF).

Transcribed from the game's PPC decompressor (10 variants at
0x84148F80 + k*0x480, k=0..9), confirmed via Ghidra decompilation.

Block header (big-endian, 0x14 bytes):
  u32 magic = 0x0E4837C3
  u32 uncompressed_size
  u32 compressed_size          (from magic to end of block)
  u32 variant                  (k; selects window/offset width: offbits = k + 6)
  u32 field                    (per-window param; not needed to decode)
  ... token stream follows at offset 0x14 ...

Token stream (interleaved LZSS):
  ctrl = next byte
    ctrl == 0        -> 8 literal bytes (fast path)
    else, bit 0..7 (LSB first):
      0 -> 1 literal byte
      1 -> 2-byte big-endian token:
             offset = token & ((1<<offbits)-1)
             length = (token >> offbits) + 3
             copy `length` bytes from (dst - offset)   [byte-wise; offset>=length]
"""
import struct

MAGIC = 0x0E4837C3
HDR = 0x14

def decompress_block(payload, unc, offbits):
    """Decode one token stream.

    This is the hot path for the whole toolkit -- every asset goes through it, so
    a full-archive extraction is dominated by this loop. Two things keep it
    reasonable in pure Python:

    * A match whose offset is >= its length does not overlap its own output, so
      it is a single slice copy rather than a per-byte loop. That is the common
      case by a wide margin.
    * `len(out)` is tracked in a local (`have`) instead of being recomputed on
      every symbol.

    Overlapping matches still need the byte-at-a-time path: they are how runs are
    encoded, and the source bytes are produced as the copy proceeds.
    """
    out = bytearray()
    i = 0
    n = len(payload)
    mask = (1 << offbits) - 1
    have = 0
    while have < unc and i < n:
        ctrl = payload[i]; i += 1
        if ctrl == 0:
            out += payload[i:i + 8]; i += 8; have += 8
            continue
        for b in range(8):
            if have >= unc:
                break
            if (ctrl >> b) & 1:
                tok = (payload[i] << 8) | payload[i + 1]; i += 2
                length = (tok >> offbits) + 3
                offset = tok & mask
                if offset == 0 or offset > have:
                    raise ValueError("bad offset %d at out %d" % (offset, have))
                s = have - offset
                if offset >= length:
                    out += out[s:s + length]          # no overlap: one slice
                else:
                    for k in range(length):           # overlapping run
                        out.append(out[s + k])
                have += length
            else:
                out.append(payload[i]); i += 1; have += 1
    return bytes(out)

def decompress_at(data, pos):
    """Decompress one 0E4837C3 block at `pos`. offbits is stored at +0x10."""
    magic, unc, comp, flags, offbits = struct.unpack_from(">IIIII", data, pos)
    if magic != MAGIC:
        raise ValueError("not a 0E4837C3 block at 0x%X" % pos)
    payload = data[pos+HDR: pos+comp]
    return decompress_block(payload, unc, offbits), unc, flags, offbits

if __name__ == "__main__":
    import os, sys
    sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
    from nhl2k_arc import Archive
    arc = Archive(sys.argv[1])
    idxs = [int(x) for x in sys.argv[2:]] if len(sys.argv) > 2 else [0, 4]
    for idx in idxs:
        e = arc.files[idx]; data = arc.read_file(e)
        p = data.find(b"\x0e\x48\x37\xc3")
        try:
            out, unc, var, ob = decompress_at(data, p)
            ok = "OK" if len(out) == unc else "MISMATCH %d" % len(out)
            print(f"file#{idx}: variant={var} offbits={ob} unc={unc} -> {len(out)} [{ok}]  "
                  f"head={out[:16].hex()}")
        except Exception as ex:
            print(f"file#{idx}: ERROR {ex}")
