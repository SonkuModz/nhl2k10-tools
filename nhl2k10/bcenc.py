#!/usr/bin/env python3
"""
bcenc.py -- RGBA8 -> BC1/BC2/BC3/BC4/BC5 and the uncompressed Xbox formats.

The exact inverse of bcdec.py, so an extract -> encode -> decode round trip is
lossless for anything the format can represent. Endpoints come from a principal
axis fit (min/max projection onto the dominant colour direction), which is what
"PCA + least squares" style encoders do and is well above naive min/max-per-
channel quality.

Everything is vectorised over blocks with numpy; a 1024x1024 texture encodes in
well under a second.

Alpha note (see docs/04_MODDING_INTEL.md): NHL 2K10's DXT4_5 textures store
STRAIGHT alpha, not premultiplied. These encoders do not premultiply -- doing so
darkens every partial-alpha pixel and is the classic "replacement looks washed
out" bug.
"""
import numpy as np


def _to_blocks(img, w, h):
    """(h,w,4) uint8 -> (bh,bw,16,4) float32 blocks of 4x4 texels."""
    bh, bw = max(1, h // 4), max(1, w // 4)
    a = img[:bh * 4, :bw * 4, :].astype(np.float32)
    a = a.reshape(bh, 4, bw, 4, 4).transpose(0, 2, 1, 3, 4)
    return a.reshape(bh, bw, 16, 4), bw, bh


def _pack565(rgb):
    r = np.clip(rgb[..., 0], 0, 255).astype(np.uint16) >> 3
    g = np.clip(rgb[..., 1], 0, 255).astype(np.uint16) >> 2
    b = np.clip(rgb[..., 2], 0, 255).astype(np.uint16) >> 3
    return (r << 11) | (g << 5) | b


def _unpack565(v):
    r = ((v >> 11) & 0x1F).astype(np.float32)
    g = ((v >> 5) & 0x3F).astype(np.float32)
    b = (v & 0x1F).astype(np.float32)
    return np.stack([(r * 8 + r // 4), (g * 4 + g // 16), (b * 8 + b // 4)], -1)


def _fit_axis(px, weights=None):
    """px: (...,16,C) -> (lo, hi) endpoints along the principal axis."""
    mean = px.mean(axis=-2, keepdims=True)
    d = px - mean
    # power iteration for the dominant eigenvector of d^T d
    cov = np.einsum('...ij,...ik->...jk', d, d)
    v = np.ones(px.shape[:-2] + (px.shape[-1],), np.float32)
    for _ in range(8):
        v = np.einsum('...jk,...k->...j', cov, v)
        nrm = np.linalg.norm(v, axis=-1, keepdims=True)
        v = np.where(nrm > 1e-6, v / np.maximum(nrm, 1e-6), 1.0)
    t = np.einsum('...ij,...j->...i', d, v)
    tmin = t.min(axis=-1)[..., None, None]
    tmax = t.max(axis=-1)[..., None, None]
    lo = (mean + tmin * v[..., None, :]).squeeze(-2)
    hi = (mean + tmax * v[..., None, :]).squeeze(-2)
    return np.clip(lo, 0, 255), np.clip(hi, 0, 255)


def encode_bc1_colour(blocks):
    """blocks: (bh,bw,16,4) -> (bh,bw,8) uint8 BC1 blocks (4-colour mode)."""
    rgb = blocks[..., :3]
    lo, hi = _fit_axis(rgb)
    c0 = _pack565(hi)
    c1 = _pack565(lo)
    # 4-colour mode needs c0 > c1; if equal the block is flat and indices are 0
    swap = c0 < c1
    c0n = np.where(swap, c1, c0)
    c1n = np.where(swap, c0, c1)
    eq = c0n == c1n
    # rebuild the actual quantised palette and pick nearest index per texel
    e0 = _unpack565(c0n)
    e1 = _unpack565(c1n)
    pal = np.stack([e0, e1, (2 * e0 + e1) / 3.0, (e0 + 2 * e1) / 3.0], axis=-2)
    d = rgb[..., None, :] - pal[..., None, :, :]
    idx = (d * d).sum(-1).argmin(-1).astype(np.uint32)
    idx = np.where(eq[..., None], 0, idx)
    bits = np.zeros(blocks.shape[:2], np.uint32)
    for k in range(16):
        bits |= (idx[..., k] & 3) << (2 * k)
    out = np.empty(blocks.shape[:2] + (8,), np.uint8)
    out[..., 0] = c0n & 0xFF
    out[..., 1] = c0n >> 8
    out[..., 2] = c1n & 0xFF
    out[..., 3] = c1n >> 8
    for k in range(4):
        out[..., 4 + k] = (bits >> (8 * k)) & 0xFF
    return out


def encode_bc4_block(vals):
    """vals: (bh,bw,16) float -> (bh,bw,8) uint8 BC4/alpha blocks (8-value mode)."""
    a0 = vals.max(-1)
    a1 = vals.min(-1)
    a0i = np.clip(np.rint(a0), 0, 255).astype(np.uint8)
    a1i = np.clip(np.rint(a1), 0, 255).astype(np.uint8)
    eq = a0i == a1i
    f0 = a0i.astype(np.float32)
    f1 = a1i.astype(np.float32)
    pal = [f0, f1] + [((6 - i) * f0 + i * f1) / 7.0 for i in range(1, 7)]
    pal = np.stack(pal, -1)                                  # (bh,bw,8)
    d = np.abs(vals[..., None] - pal[..., None, :])
    idx = d.argmin(-1).astype(np.uint64)
    idx = np.where(eq[..., None], 0, idx)
    bits = np.zeros(vals.shape[:2], np.uint64)
    for k in range(16):
        bits |= (idx[..., k] & np.uint64(7)) << np.uint64(3 * k)
    out = np.empty(vals.shape[:2] + (8,), np.uint8)
    out[..., 0] = a0i
    out[..., 1] = a1i
    for k in range(6):
        out[..., 2 + k] = (bits >> np.uint64(8 * k)) & np.uint64(0xFF)
    return out


def _stitch(blocks):
    bh, bw, nb = blocks.shape
    return blocks.reshape(bh * bw * nb).tobytes()


def encode(img, w, h, fmt):
    """(h,w,4) uint8 RGBA -> linear block bytes for `fmt`."""
    blocks, bw, bh = _to_blocks(img, w, h)
    if fmt == "DXT1":
        return _stitch(encode_bc1_colour(blocks))
    if fmt == "DXT5":
        col = encode_bc1_colour(blocks)
        alp = encode_bc4_block(blocks[..., 3])
        return _stitch(np.concatenate([alp, col], axis=-1))
    if fmt == "DXT3":
        col = encode_bc1_colour(blocks)
        a = np.clip(np.rint(blocks[..., 3] / 17.0), 0, 15).astype(np.uint16)
        alp = np.zeros(blocks.shape[:2] + (8,), np.uint8)
        for row in range(4):
            v = np.zeros(blocks.shape[:2], np.uint16)
            for col_i in range(4):
                v |= a[..., row * 4 + col_i] << (4 * col_i)
            alp[..., row * 2] = v & 0xFF
            alp[..., row * 2 + 1] = v >> 8
        return _stitch(np.concatenate([alp, col], axis=-1))
    if fmt in ("ATI2", "DXN", "BC5"):
        # Xbox DXN stores [green half][red half] -- matches bcdec.decode()
        g = encode_bc4_block(blocks[..., 1])
        r = encode_bc4_block(blocks[..., 0])
        return _stitch(np.concatenate([g, r], axis=-1))
    if fmt in ("DXT5A", "DXT3A", "BC4"):
        return _stitch(encode_bc4_block(blocks[..., 0]))
    raise ValueError("unsupported block format " + fmt)


def encode_uncompressed(img, w, h, fmt):
    """(h,w,4) uint8 RGBA -> linear bytes, inverse of bcdec.decode_uncompressed."""
    a = img[:h, :w]
    if fmt == "ARGB":                       # stored BGRA in the DDS payload
        return a[..., [2, 1, 0, 3]].astype(np.uint8).tobytes()
    if fmt == "RGB565":
        v = ((a[..., 0].astype(np.uint16) >> 3) << 11) | \
            ((a[..., 1].astype(np.uint16) >> 2) << 5) | \
            (a[..., 2].astype(np.uint16) >> 3)
        return v.astype("<u2").tobytes()
    if fmt == "ARGB4444":
        v = ((a[..., 3].astype(np.uint16) >> 4) << 12) | \
            ((a[..., 0].astype(np.uint16) >> 4) << 8) | \
            ((a[..., 1].astype(np.uint16) >> 4) << 4) | \
            (a[..., 2].astype(np.uint16) >> 4)
        return v.astype("<u2").tobytes()
    if fmt == "L8":
        return a[..., 0].astype(np.uint8).tobytes()
    if fmt == "RG8":
        return a[..., :2].astype(np.uint8).tobytes()
    raise ValueError("unsupported uncompressed format " + fmt)
