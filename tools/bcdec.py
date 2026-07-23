#!/usr/bin/env python3
"""bcdec.py -- vectorised BC1/BC2/BC3/BC4/BC5 decoders -> RGBA8 numpy arrays.

Used for previewing extracted textures (Pillow cannot decode BC4/BC5, and ffmpeg
does not handle the ATI2/DXN fourcc).  Input is *linear* (already untiled) block
data; output is (h, w, 4) uint8.
"""
import numpy as np


def _blocks(data, w, h, block_bytes):
    bw, bh = max(1, w // 4), max(1, h // 4)
    n = bw * bh * block_bytes
    a = np.frombuffer(data[:n], dtype=np.uint8)
    return a.reshape(bh, bw, block_bytes), bw, bh


def _bc1_colour(b):
    """b: (bh,bw,8) uint8 -> (bh,bw,4,4,4) RGBA (blocks of 4x4 texels)."""
    bh, bw = b.shape[:2]
    c0 = b[..., 0].astype(np.uint16) | (b[..., 1].astype(np.uint16) << 8)
    c1 = b[..., 2].astype(np.uint16) | (b[..., 3].astype(np.uint16) << 8)

    def unpack565(c):
        r = ((c >> 11) & 0x1F).astype(np.uint16)
        g = ((c >> 5) & 0x3F).astype(np.uint16)
        bl = (c & 0x1F).astype(np.uint16)
        return (np.stack([(r << 3) | (r >> 2),
                          (g << 2) | (g >> 4),
                          (bl << 3) | (bl >> 2)], -1)).astype(np.uint16)

    e0, e1 = unpack565(c0), unpack565(c1)
    wide = (c0 > c1)[..., None]
    p2 = np.where(wide, (2 * e0 + e1) // 3, (e0 + e1) // 2)
    p3 = np.where(wide, (e0 + 2 * e1) // 3, np.zeros_like(e0))
    pal = np.stack([e0, e1, p2, p3], axis=2).astype(np.uint8)      # (bh,bw,4,3)

    bits = (b[..., 4].astype(np.uint32) | (b[..., 5].astype(np.uint32) << 8) |
            (b[..., 6].astype(np.uint32) << 16) | (b[..., 7].astype(np.uint32) << 24))
    sh = (np.arange(16, dtype=np.uint32) * 2).reshape(4, 4)
    idx = (bits[..., None, None] >> sh) & 3                        # (bh,bw,4,4)

    out = np.take_along_axis(pal[:, :, :, None, None, :],
                             idx[:, :, None, :, :, None].astype(np.intp), axis=2)[:, :, 0]
    rgba = np.empty(out.shape[:-1] + (4,), np.uint8)
    rgba[..., :3] = out
    # BC1 1-bit alpha: index 3 in the narrow (c0<=c1) mode is transparent black
    alpha = np.where((~wide[..., 0])[..., None, None] & (idx == 3), 0, 255)
    rgba[..., 3] = alpha
    return rgba


def _bc4_block(b):
    """b: (bh,bw,8) -> (bh,bw,4,4) uint8 single channel."""
    a0 = b[..., 0].astype(np.uint16)
    a1 = b[..., 1].astype(np.uint16)
    pal = [a0, a1]
    wide = a0 > a1
    for i in range(1, 7):
        p6 = ((6 - i) * a0 + i * a1) // 7
        if i <= 5:
            p4 = ((5 - i) * a0 + i * a1) // 5
        else:
            p4 = np.zeros_like(a0)
        if i == 6:
            p4 = np.full_like(a0, 255)
        pal.append(np.where(wide, p6, p4))
    pal = np.stack(pal, axis=2).astype(np.uint8)                   # (bh,bw,8)

    bits = np.zeros(b.shape[:2], np.uint64)
    for i in range(6):
        bits |= b[..., 2 + i].astype(np.uint64) << np.uint64(8 * i)
    sh = (np.arange(16, dtype=np.uint64) * 3).reshape(4, 4)
    idx = (bits[..., None, None] >> sh) & np.uint64(7)
    return np.take_along_axis(pal[:, :, :, None, None],
                              idx[:, :, None, :, :].astype(np.intp), axis=2)[:, :, 0]


def _stitch(blocks4x4, bw, bh):
    """(bh,bw,4,4,C) -> (bh*4, bw*4, C)"""
    c = blocks4x4.shape[-1]
    return blocks4x4.transpose(0, 2, 1, 3, 4).reshape(bh * 4, bw * 4, c)


def decode(data, w, h, fmt):
    """fmt in DXT1/DXT3/DXT5/ATI2/BC4 -> (h,w,4) uint8 RGBA."""
    if fmt == "DXT1":
        b, bw, bh = _blocks(data, w, h, 8)
        return _stitch(_bc1_colour(b), bw, bh)[:h, :w]

    if fmt in ("DXT3", "DXT5"):
        b, bw, bh = _blocks(data, w, h, 16)
        rgba = _bc1_colour(b[..., 8:])
        if fmt == "DXT5":
            a = _bc4_block(b[..., :8])
        else:                                    # BC2: 4-bit explicit alpha
            nib = np.zeros(b.shape[:2] + (4, 4), np.uint8)
            for row in range(4):
                lo = b[..., row * 2].astype(np.uint16)
                hi = b[..., row * 2 + 1].astype(np.uint16)
                v = lo | (hi << 8)
                for col in range(4):
                    n = (v >> (col * 4)) & 0xF
                    nib[:, :, row, col] = (n * 17).astype(np.uint8)
            a = nib
        rgba = rgba.copy()
        rgba[..., 3] = a
        return _stitch(rgba, bw, bh)[:h, :w]

    if fmt in ("ATI2", "DXN", "BC5"):
        b, bw, bh = _blocks(data, w, h, 16)
        # Xbox 360 DXN stores the two BC4 halves as [green][red].
        g = _bc4_block(b[..., :8])
        r = _bc4_block(b[..., 8:])
        out = np.empty(r.shape + (4,), np.uint8)
        out[..., 0] = r
        out[..., 1] = g
        # reconstruct Z for a normal map so the preview is meaningful
        rf = r.astype(np.float32) / 127.5 - 1.0
        gf = g.astype(np.float32) / 127.5 - 1.0
        z = np.sqrt(np.clip(1.0 - rf * rf - gf * gf, 0, 1))
        out[..., 2] = ((z + 1.0) * 127.5).astype(np.uint8)
        out[..., 3] = 255
        return _stitch(out, bw, bh)[:h, :w]

    if fmt == "BC4":
        b, bw, bh = _blocks(data, w, h, 8)
        v = _bc4_block(b)
        out = np.repeat(v[..., None], 4, axis=-1)
        out[..., 3] = 255
        return _stitch(out, bw, bh)[:h, :w]

    raise ValueError("unsupported block format " + fmt)


def decode_uncompressed(data, w, h, fmt):
    """Linear uncompressed Xbox formats -> (h,w,4) RGBA."""
    if fmt == "ARGB":                                   # already byte-swapped to BGRA
        a = np.frombuffer(data[:w * h * 4], np.uint8).reshape(h, w, 4)
        return a[..., [2, 1, 0, 3]]
    if fmt == "RGB565":
        v = np.frombuffer(data[:w * h * 2], "<u2").reshape(h, w).astype(np.uint32)
        r = ((v >> 11) & 0x1F); g = ((v >> 5) & 0x3F); b = v & 0x1F
        out = np.empty((h, w, 4), np.uint8)
        out[..., 0] = (r << 3) | (r >> 2)
        out[..., 1] = (g << 2) | (g >> 4)
        out[..., 2] = (b << 3) | (b >> 2)
        out[..., 3] = 255
        return out
    if fmt == "ARGB4444":
        v = np.frombuffer(data[:w * h * 2], "<u2").reshape(h, w).astype(np.uint32)
        out = np.empty((h, w, 4), np.uint8)
        out[..., 3] = ((v >> 12) & 0xF) * 17
        out[..., 0] = ((v >> 8) & 0xF) * 17
        out[..., 1] = ((v >> 4) & 0xF) * 17
        out[..., 2] = (v & 0xF) * 17
        return out
    if fmt == "L8":
        v = np.frombuffer(data[:w * h], np.uint8).reshape(h, w)
        out = np.repeat(v[..., None], 4, -1).copy()
        out[..., 3] = 255
        return out
    if fmt == "RG8":
        a = np.frombuffer(data[:w * h * 2], np.uint8).reshape(h, w, 2)
        out = np.zeros((h, w, 4), np.uint8)
        out[..., 0] = a[..., 0]; out[..., 1] = a[..., 1]; out[..., 3] = 255
        return out
    raise ValueError("unsupported uncompressed format " + fmt)
