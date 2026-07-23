#!/usr/bin/env python3
"""
vc_decompress.py -- Visual Concepts 0E4837C3 block decompressor.

Algorithm transcribed from VCFILEDEVICE::ReadAndDecompress (PPC, VA 0x8414B8A0)
in NHL 2K10's executable:

  Interleaved LZSS. Repeatedly:
    ctrl = next byte
    for bit in 0..7 (LSB first):
      0 -> emit one literal byte
      1 -> read 2-byte big-endian token:
             offset = token & 0x7FFF
             length = (token >> 15) + 3         # 3 or 4
             copy `length` bytes from (dst - offset)
  (ctrl==0 is a fast path in asm = 8 literals; handled naturally here.)

Block header (0x18 bytes, big-endian):
  u32 magic=0x0E4837C3, u32 unc_size, u32 comp_size, u32 ?, u32 ?, u32 flags
  payload follows immediately.
"""
import struct

MAGIC = 0x0E4837C3
HDR = 0x18

def decompress_block(buf, unc_size):
    """buf = payload bytes (after the 0x18 header). Returns unc_size bytes."""
    out = bytearray()
    i = 0
    n = len(buf)
    while len(out) < unc_size and i < n:
        ctrl = buf[i]; i += 1
        for b in range(8):
            if len(out) >= unc_size:
                break
            if (ctrl >> b) & 1:
                # match
                if i + 1 >= n + 1:  # need 2 bytes
                    return bytes(out)
                tok = (buf[i] << 8) | buf[i + 1]; i += 2
                length = (tok >> 15) + 3
                offset = tok & 0x7FFF
                start = len(out) - offset
                if start < 0:
                    raise ValueError(f"bad offset {offset} at out {len(out)}")
                for k in range(length):
                    out.append(out[start + k])
            else:
                out.append(buf[i]); i += 1
    return bytes(out)

def decompress_at(data, pos):
    """Decompress a 0E4837C3 block located at `pos` in `data`."""
    magic, unc, comp = struct.unpack_from(">III", data, pos)
    if magic != MAGIC:
        raise ValueError(f"not a 0E4837C3 block at 0x{pos:X} (got 0x{magic:08X})")
    payload = data[pos + HDR: pos + comp]
    out = decompress_block(payload, unc)
    return out, unc, comp

if __name__ == "__main__":
    import sys
    sys.path.insert(0, __file__.rsplit("/", 1)[0] if "/" in __file__ else ".")
    from nhl2k_arc import Archive
    iso = sys.argv[1]; idx = int(sys.argv[2]) if len(sys.argv) > 2 else 4
    arc = Archive(iso); e = arc.files[idx]; data = arc.read_file(e)
    pos = data.find(b"\x0e\x48\x37\xc3")
    out, unc, comp = decompress_at(data, pos)
    ok = "OK" if len(out) == unc else f"MISMATCH got {len(out)}"
    print(f"file#{idx} block@0x{pos:X} unc={unc} comp={comp} -> produced {len(out)} [{ok}]")
    print("first 64 decompressed:", out[:64].hex())
    print("ascii:", "".join(chr(b) if 32<=b<127 else "." for b in out[:64]))
