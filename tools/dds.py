#!/usr/bin/env python3
"""dds.py -- minimal DDS container writer for DXT1/3/5 + Xbox 360 helpers."""
import struct

def dds_header(width, height, fourcc, linear_size):
    # DDSURFACEDESC2
    flags = 0x1 | 0x2 | 0x4 | 0x1000 | 0x80000   # CAPS|HEIGHT|WIDTH|PIXELFORMAT|LINEARSIZE
    h = b"DDS " + struct.pack("<I", 124)
    h += struct.pack("<IIIII", flags, height, width, linear_size, 0)
    h += struct.pack("<I", 0)                     # mipcount
    h += b"\x00" * 44                             # reserved
    # pixel format (32 bytes)
    h += struct.pack("<II", 32, 0x4)              # size, FOURCC flag
    h += fourcc
    h += struct.pack("<IIIII", 0, 0, 0, 0, 0)
    # caps
    h += struct.pack("<IIIII", 0x1000, 0, 0, 0, 0)
    return h

def dds_header_argb(width, height):
    """DDS header for uncompressed A8R8G8B8 (32bpp)."""
    flags = 0x1 | 0x2 | 0x4 | 0x1000 | 0x8    # CAPS|HEIGHT|WIDTH|PIXELFORMAT|PITCH
    h = b"DDS " + struct.pack("<I", 124)
    h += struct.pack("<IIIII", flags, height, width, width * 4, 0)
    h += struct.pack("<I", 0) + b"\x00" * 44
    # pixel format: RGB|ALPHAPIXELS, 32bpp, ARGB masks
    h += struct.pack("<II", 32, 0x41)
    h += struct.pack("<I", 0)                 # no fourcc
    h += struct.pack("<IIIII", 32, 0x00FF0000, 0x0000FF00, 0x000000FF, 0xFF000000)
    h += struct.pack("<IIIII", 0x1000, 0, 0, 0, 0)
    return h

def dds_header_uncompressed(width, height, bpp, masks):
    """DDS header for an uncompressed format. masks = (R,G,B,A) bit masks.
    bpp = bits per pixel (8/16/32)."""
    a_mask = masks[3]
    pf_flags = 0x40 | (0x1 if a_mask else 0)     # RGB | ALPHAPIXELS
    flags = 0x1 | 0x2 | 0x4 | 0x1000 | 0x8       # CAPS|HEIGHT|WIDTH|PIXELFORMAT|PITCH
    h = b"DDS " + struct.pack("<I", 124)
    h += struct.pack("<IIIII", flags, height, width, width * bpp // 8, 0)
    h += struct.pack("<I", 0) + b"\x00" * 44
    h += struct.pack("<II", 32, pf_flags)
    h += struct.pack("<I", 0)                    # no fourcc
    h += struct.pack("<IIIII", bpp, masks[0], masks[1], masks[2], a_mask)
    h += struct.pack("<IIIII", 0x1000, 0, 0, 0, 0)
    return h

def write_dds(path, width, height, fmt, data):
    fourccs = {"DXT1": b"DXT1", "DXT3": b"DXT3", "DXT5": b"DXT5"}
    fc = fourccs[fmt]
    block = 8 if fmt == "DXT1" else 16
    linear = max(1, width // 4) * max(1, height // 4) * block
    with open(path, "wb") as f:
        f.write(dds_header(width, height, fc, linear))
        f.write(data[:linear])

def endian_swap16(data):
    b = bytearray(data)
    b[0::2], b[1::2] = data[1::2], data[0::2]
    return bytes(b)

# ---- Xbox 360 texture untiling ----
def _tiled_offset(x, y, width, log2_bpb):
    """Xbox 360 2D tiled address (per block), texelBytePitch = 1<<log2_bpb."""
    aligned_w = (width + 31) & ~31
    macro = ((x >> 5) + (y >> 5) * (aligned_w >> 5)) << (log2_bpb + 7)
    micro = (((x & 7) + ((y & 6) << 2)) << log2_bpb)
    offset = macro + ((micro & ~0xF) << 1) + (micro & 0xF) + ((y & 1) << 4)
    return (((offset & ~0x1FF) << 3) + ((offset & 0x1C0) << 2) +
            (offset & 0x3F) + ((y & 8) << 6) + ((y & 16) << 7)) >> log2_bpb

def tile(data, width, height, block_bytes, block_dim=4):
    """Linear -> Xbox 360 tiled. Exact inverse of untile(); used when writing a
    replacement texture back into a resource."""
    from reversebox.image.swizzling.swizzle_x360 import swizzle_x360
    return swizzle_x360(data, width, height, block_dim, block_bytes)


def untile(data, width, height, block_bytes, block_dim=4):
    """Convert Xbox 360 tiled data to linear via the verified reversebox
    unswizzle (the correct XGAddress2DTiled implementation; a hand-rolled copy
    proved non-bijective). block_dim = texels per block edge (4 DXT, 1 uncompressed),
    block_bytes = bytes per block/texel."""
    from reversebox.image.swizzling.swizzle_x360 import unswizzle_x360
    return unswizzle_x360(data, width, height, block_dim, block_bytes)

if __name__ == "__main__":
    import sys
    # test harness: raw file -> DDS with given w h fmt [swap] [untile]
    raw = open(sys.argv[1], "rb").read()
    w = int(sys.argv[2]); h = int(sys.argv[3]); fmt = sys.argv[4]
    opts = sys.argv[5:]
    block = 8 if fmt == "DXT1" else 16
    d = raw
    if "swap" in opts:
        d = endian_swap16(d)
    if "untile" in opts:
        d = untile(d, w, h, block)
    write_dds(sys.argv[1] + ".dds", w, h, fmt, d)
    print("wrote", sys.argv[1] + ".dds", w, h, fmt, opts)
