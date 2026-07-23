#!/usr/bin/env python3
"""
vc_texture.py -- Extract textures from NHL 2K10 IFF files to DDS.

Texture-atlas IFFs decompress to (typically) two sub-resources: res0 holds a
descriptor table, the last resource holds the tiled pixel data. Each 0xE0-byte
descriptor embeds an Xbox 360 GPU texture fetch constant at entry+0x3C:

  entry +0x08  u16 width
        +0x0A  u16 height
        +0x14  u32 offset   (& 0xFFFFF000 = 4KB-aligned offset into pixel resource)
        +0x40  GPU dword1: format = bits 0..5, endian = bits 6..7 (=1 -> 16-bit swap)

  GPU format: 0x12 = DXT1, 0x13 = DXT2/3, 0x14 = DXT4/5, 0x06 = A8R8G8B8.

pixel_base = 4KB-aligned start of the last decompressed sub-resource. Each texture
is Xbox-360 tiled + 16-bit endian-swapped; decode = untile(endian_swap16(blob)).
"""
import os, sys, struct
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from nhl2k_arc import Archive
import vc_extract
import dds

# gpu_format -> (kind, bytes_per_block, block_dim)
#   kind: "DXT1" plain, "BC_COLORFIRST" = 16-byte [color8][alpha8] block (VC's
#         0x13/0x14 layout; we emit the DXT1 color plane), "ARGB" uncompressed.
GPU_FMT = {
    0x12: ("DXT1", 8, 4),      # GPUTEXTUREFORMAT_DXT1
    0x13: ("DXT3", 16, 4),     # DXT2_3
    0x14: ("DXT5", 16, 4),     # DXT4_5
    0x06: ("ARGB", 4, 1),      # 8_8_8_8   (32bpp)
    0x04: ("RGB565", 2, 1),    # 5_6_5     (16bpp)
    0x0F: ("ARGB4444", 2, 1),  # 4_4_4_4   (16bpp)
    0x02: ("L8", 1, 1),        # 8         (8bpp)
    0x31: ("ATI2", 16, 4),     # DXN / BC5 (16 bytes per 4x4 block)
    0x0A: ("RG8", 2, 1),       # 8_8       (16bpp two-channel)
    # Single-channel BC-style formats, 8 bytes per 4x4 block. Missing these was
    # costly: they sit INSIDE the 0xE0-strided runs, so rejecting one split its
    # run into isolated fragments that the min_run filter then discarded too.
    0x3A: ("DXT3A", 8, 4),     # k_DXT3A
    0x3B: ("DXT5A", 8, 4),     # k_DXT5A  (BC4-equivalent; masks, AO, gloss)
}

def decompress_with_bounds(raw):
    """Decompress an IFF file and return (data, resource_boundaries)."""
    blocks = vc_extract.find_blocks(raw)
    out = bytearray()
    bounds = []
    for (p, unc, comp, ob) in blocks:
        d, _, _, _ = vc_decompress_at(raw, p)
        bounds.append((len(out), unc))
        out += d
    return bytes(out), bounds

def vc_decompress_at(raw, p):
    import vc_decomp
    return vc_decomp.decompress_at(raw, p)

def _entry_valid(data, i):
    """A descriptor entry is valid if three independent fields agree:
      * the u16 w/h at +0x08/+0x0A,
      * the GPU fetch-constant size dword at +0x44 (packed w-1, h-1),
      * the base-level byte size at +0x18, which must equal the size implied by
        w, h and the GPU format at +0x40.
    That triple check is strong enough that the dimensions need NOT be powers of
    two -- and they often aren't (e.g. the 3968x256 jersey atlases in file #17,
    which the old power-of-two filter silently dropped)."""
    if i + 0x54 > len(data):
        return False
    w = struct.unpack_from(">H", data, i + 8)[0]
    h = struct.unpack_from(">H", data, i + 0xA)[0]
    if not (1 <= w <= 8192 and 1 <= h <= 8192):
        return False
    d2 = struct.unpack_from(">I", data, i + 0x44)[0]      # GPU size dword
    if ((d2 & 0x1FFF) + 1) != w or (((d2 >> 13) & 0x1FFF) + 1) != h:
        return False
    info = GPU_FMT.get(struct.unpack_from(">I", data, i + 0x40)[0] & 0x3F)
    if info is None:
        return False
    _k, bpb, bdim = info
    # +0x18 is the tile-padded base-level size: one 2D face, or six for a cubemap
    # (e.g. the 128x128 "8_8" entries in file #18 store 0x30000 = 6 * 0x8000).
    p = padded_size(w, h, bpb, bdim)
    return struct.unpack_from(">I", data, i + 0x18)[0] in (p, 6 * p)


def record_array_header(data):
    """The resource header names its first texture-record array outright:
        u32 count @0x20,  u32 ptr @0x24  ->  table starts at ptr + 0x7B.
    Returns (table_offset, count) or None. Verified exactly on files #0 (3),
    #18 (65), #261 (86) and #507 (96) -- the scanner finds the same runs, so this
    is used as a cross-check rather than as the primary source.

    Field naming comes from the Mod Launcher notes, where a full record is 0xE0
    bytes starting 0x58 BEFORE the part parsed here (their w@+0x60 == our +0x08,
    their VRAM offset @+0x6C == our +0x14, mip0 @+0x70 == our +0x18,
    mip tail @+0x74 == our +0x1C) -- an independent confirmation of the layout.
    """
    if len(data) < 0x28:
        return None
    count = struct.unpack_from(">I", data, 0x20)[0]
    ptr = struct.unpack_from(">I", data, 0x24)[0]
    if not count or not ptr or count > 0x4000:
        return None
    t = ptr + 0x7B
    return (t, count) if t + 0x54 <= len(data) and _entry_valid(data, t) else None


def find_descriptor_tables(data, stride=0xE0, min_run=1, limit=None):
    """Locate descriptor-table starts. The table is NOT at a fixed offset (seen at
    0x58, 0xF8, 0x15538, 0x61F98...), so scan for a run of consecutive valid
    entries at `stride` spacing.

    min_run is 1 on purpose: many IFFs embed a SINGLE texture descriptor inside a
    larger per-object record (file #0 has 14 descriptors, most of them isolated,
    at irregular strides like 0x140C0/0x78A0/0x7940). Requiring a run of 2 threw
    those away. The three-field validity test is strict enough to carry this."""
    starts = []
    n = len(data) if limit is None else min(len(data), limit)
    i = 0
    while i + 0x50 <= n:
        if _entry_valid(data, i):
            run = 0
            j = i
            while _entry_valid(data, j):
                run += 1
                j += stride
            if run >= min_run:
                starts.append((i, run))
                i = j
                continue
        i += 8
    return starts


def parse_descriptor_tables(data, stride=0xE0, start=None):
    """Parse each descriptor table separately -> list of lists.

    An IFF may contain SEVERAL texture groups, each with its own table AND its own
    pixel-data region; offsets restart at 0 in every table, so they must not share
    one pixel_base.
    """
    tables = [(start, None)] if start is not None else find_descriptor_tables(data)
    out = []
    for tstart, _run in tables:
        group = []
        i = tstart
        run = 0            # running cumulative offset, used when +0x14 is degenerate
        while _entry_valid(data, i):
            w = struct.unpack_from(">H", data, i + 8)[0]
            h = struct.unpack_from(">H", data, i + 0xA)[0]
            off = struct.unpack_from(">I", data, i + 0x14)[0] & 0xFFFFF000
            fmt = struct.unpack_from(">I", data, i + 0x40)[0] & 0x3F
            mips = ((struct.unpack_from(">I", data, i + 0x4C)[0] >> 6) & 0xF) + 1
            # EXACT stored size, straight out of the descriptor:
            #   +0x18 = base-level bytes, +0x1C = whole mip-chain bytes.
            # Verified: consecutive +0x14 offsets differ by exactly this sum.
            base_sz = struct.unpack_from(">I", data, i + 0x18)[0]
            mip_sz = struct.unpack_from(">I", data, i + 0x1C)[0]
            stored = base_sz + mip_sz
            # Some IFFs leave +0x14 at a constant sentinel (1) for every entry --
            # there the textures simply follow one another in table order.
            degenerate = bool(group) and off <= group[-1]["off"]
            if degenerate:
                off = run
            group.append({"w": w, "h": h, "off": off, "fmt": fmt, "mips": mips,
                          "stored": stored, "degenerate": degenerate})
            run = off + stored
            i += stride
        if group:
            out.append(group)
    return out


def parse_descriptors(data, stride=0xE0, start=None):
    """Flat list of all descriptors (kept for compatibility)."""
    return [d for g in parse_descriptor_tables(data, stride, start) for d in g]


def tex_size(w, h, bpb, bdim):
    return max(1, w // bdim) * max(1, h // bdim) * bpb


def padded_dims(w, h, bdim):
    """Xbox 360 stores a tiled base level padded so that BOTH dimensions are a
    whole number of 32-block macro tiles.  Returns the padded (w, h) in texels.
    e.g. 512x64 DXT1 = 128x16 blocks -> 128x32 blocks (twice the bytes)."""
    bw = (max(1, w // bdim) + 31) // 32 * 32
    bh = (max(1, h // bdim) + 31) // 32 * 32
    return bw * bdim, bh * bdim


def padded_size(w, h, bpb, bdim):
    """Bytes the padded base level occupies (at least one 4KB page)."""
    pw, ph = padded_dims(w, h, bdim)
    return max(0x1000, tex_size(pw, ph, bpb, bdim))


def mip_consistency(data, base, w, h, fmt):
    """Self-check needing no ground truth: a texture's mip level 1 must be a 2x
    downscale of its own base level.  Returns a correlation in [-1, 1] -- ~0.9+
    when the texture really starts at `base`, ~0.0 when it does not -- or None if
    it cannot be evaluated (no mip chain, too small, numpy/bcdec unavailable).

    This is what resolves IFFs whose descriptor offsets are all the same sentinel
    and whose textures are therefore NOT in table order (e.g. file #17, where the
    jersey sheet is stored before its normal map)."""
    try:
        import numpy as np
        import bcdec
    except Exception:
        return None
    info = GPU_FMT.get(fmt)
    if info is None or w < 8 or h < 8:
        return None
    kind, bpb, bdim = info

    def grey(b, ww, hh):
        pw, ph = padded_dims(ww, hh, bdim)
        n = tex_size(pw, ph, bpb, bdim)
        if b < 0 or b + n > len(data):
            return None
        lin = _crop(dds.untile(dds.endian_swap16(data[b:b + n]), pw, ph, bpb,
                               block_dim=bdim), pw, ph, ww, hh, bpb, bdim)
        try:
            img = (bcdec.decode(lin, ww, hh, kind) if bdim == 4
                   else bcdec.decode_uncompressed(lin, ww, hh, kind))
        except Exception:
            return None
        return img[..., :3].astype(np.float32).mean(-1)

    b0 = grey(base, w, h)
    if b0 is None:
        return None
    m = grey(base + padded_size(w, h, bpb, bdim),
             max(bdim, w // 2), max(bdim, h // 2))
    if m is None:
        return None
    hh, ww = m.shape
    ds = b0[:hh * 2, :ww * 2].reshape(hh, 2, ww, 2).mean((1, 3))
    a = ds - ds.mean(); c = m - m.mean()
    den = float(np.sqrt((a * a).sum() * (c * c).sum()))
    return float((a * c).sum() / den) if den > 1e-6 else None


def _resolve_order(data, group, base):
    """Order a group whose descriptor offsets are a constant sentinel.

    Greedy: at each slot try every remaining texture and keep the one whose own
    mip chain confirms it starts there.  Textures without a mip chain carry no
    signal, so they fall through to table order."""
    if not any(d.get("degenerate") for d in group) or len(group) < 2:
        return group
    def has_mips(d):
        return d["stored"] > tex_size(d["w"], d["h"], *GPU_FMT[d["fmt"]][1:])

    remaining = list(group)
    out, off = [], 0
    while remaining:
        best, best_s = None, None
        for d in remaining:
            if not has_mips(d):
                continue                                  # no mip chain -> no signal
            s = mip_consistency(data, base + off, d["w"], d["h"], d["fmt"])
            if s is not None and (best_s is None or s > best_s):
                best, best_s = d, s
        if best is None or best_s is None or best_s < 0.5:
            # No mip-bearing texture claims this slot. Any texture that DID belong
            # here would have scored, so the slot must hold one of the mip-less
            # ones -- they are the only candidates that leave no evidence.
            # (File #17: the two 3968x256 jersey atlases precede the letter atlas,
            # which is why the letter atlas alone was landing 0x1F0000 too early.)
            best = next((d for d in remaining if not has_mips(d)), remaining[0])
        remaining.remove(best)
        d = dict(best); d["off"] = off
        out.append(d)
        off += best["stored"]
    return out


def _crop(linear, pw, ph, w, h, bpb, bdim):
    """Crop an untiled padded image (pw x ph) down to w x h, row by row."""
    if pw == w and ph == h:
        return linear
    src_row = max(1, pw // bdim) * bpb          # bytes per block-row of the padded image
    dst_row = max(1, w // bdim) * bpb
    rows = max(1, h // bdim)
    out = bytearray(dst_row * rows)
    for r in range(rows):
        out[r * dst_row:(r + 1) * dst_row] = linear[r * src_row:r * src_row + dst_row]
    return bytes(out)


def describe_textures(data, bounds):
    """Resolve every texture descriptor to an absolute position.

    Returns a flat list of descriptors, each with its group's `base` filled in, in
    storage order. Extraction and replacement both go through this so they can
    never disagree about where a texture lives.
    """
    groups = parse_descriptor_tables(data)
    if not groups or len(bounds) < 2:
        return []
    # Base of the FIRST texture group = EXACT start of the last sub-resource.
    # Do NOT round up to 4KB (that start is typically unaligned while descriptor
    # offsets are 4KB-aligned). Verified byte-exact against Xenia GPU dumps.
    first_base = bounds[-1][0]
    region = bounds[-1][1]
    # Every texture's stored size is read straight from its descriptor, so each
    # group's extent is exact and the groups simply follow one another. (Checked:
    # for file #17 the six extents sum to the sub-resource size to the byte, and
    # for file #18 all 11 consecutive descriptor gaps match exactly.)
    est = [max((d["off"] + d["stored"]) for d in g) for g in groups]
    bases, b = [], first_base
    for e in est:
        bases.append(b)
        b += e
    # ...but only when the extents actually tile the region. If they OVERSHOOT it,
    # the tables are not consecutive regions at all -- they are overlapping views
    # of one shared pixel region, indexing it with absolute offsets (file #507:
    # three tables whose extents total 0x111B000 inside a 0x7F4000 region; sharing
    # the base takes it from 30 ok / 45 bad to 84 ok / 7 bad).
    if len(groups) > 1 and sum(est) > region:
        bases = [first_base] * len(groups)
    # If they UNDERSHOOT, the groups are spaced apart by something we do not model,
    # so locate each one: slide it over the region and keep the offset where a
    # texture's own mip chain confirms it. Group 0 always sits at the resource start.
    elif len(groups) > 1 and sum(est) != region and region // 0x1000 <= 4096:
        for gi in range(1, len(groups)):
            probe = next((d for d in groups[gi]
                          if d["stored"] > tex_size(d["w"], d["h"],
                                                    *GPU_FMT[d["fmt"]][1:])), None)
            if probe is None:
                continue
            best, best_s = None, 0.5
            for k in range(0, (region - est[gi]) // 0x1000 + 1):
                cand = first_base + k * 0x1000
                s = mip_consistency(data, cand + probe["off"],
                                    probe["w"], probe["h"], probe["fmt"])
                if s is not None and s > best_s:
                    best, best_s = cand, s
            if best is not None:
                bases[gi] = best
    descs = []
    for gi, g in enumerate(groups):
        for d in _resolve_order(data, g, bases[gi]):
            d = dict(d); d["base"] = bases[gi]
            descs.append(d)
    return descs


def extract_textures(data, bounds):
    """Return (list of (desc, fourcc, dds_bytes), first_pixel_base)."""
    descs = describe_textures(data, bounds)
    if not descs:
        return [], 0
    pixel_base = bounds[-1][0]
    results = []
    for d in descs:
        info = GPU_FMT.get(d["fmt"])
        if info is None:
            continue
        kind, bpb, bdim = info
        w, h = d["w"], d["h"]
        # The stored base level is padded out to whole 32-block macro tiles, so
        # read/untile at the PADDED size and crop back afterwards.
        pw, ph = padded_dims(w, h, bdim)
        stored_bytes = tex_size(pw, ph, bpb, bdim)
        size = tex_size(w, h, bpb, bdim)
        b0 = d.get("base", pixel_base)
        blob = data[b0 + d["off"]: b0 + d["off"] + stored_bytes]
        if len(blob) < stored_bytes:
            continue
        # Xbox-360 tiled + 8in16 endian. Verified byte-identical to Xenia GPU dumps.
        if kind == "ARGB":
            # Xbox stores A8R8G8B8 big-endian [A][R][G][B]; DDS wants [B][G][R][A].
            b = bytearray(blob)
            b[0::4], b[3::4] = blob[3::4], blob[0::4]   # swap A<->B
            b[1::4], b[2::4] = blob[2::4], blob[1::4]   # swap R<->G
            linear = _crop(dds.untile(bytes(b), pw, ph, 4, block_dim=1),
                           pw, ph, w, h, 4, 1)
            hdr = dds.dds_header_argb(w, h)
            results.append((d, "ARGB", hdr + linear[:size]))
        elif kind in ("RGB565", "ARGB4444", "L8", "RG8"):
            # uncompressed; 16bpp values are big-endian on Xbox -> swap
            src = dds.endian_swap16(blob) if bpb == 2 else blob
            linear = _crop(dds.untile(src, pw, ph, bpb, block_dim=1),
                           pw, ph, w, h, bpb, 1)
            masks = {"RGB565":   (0xF800, 0x07E0, 0x001F, 0),
                     "ARGB4444": (0x0F00, 0x00F0, 0x000F, 0xF000),
                     "L8":       (0xFF, 0xFF, 0xFF, 0),
                     "RG8":      (0x00FF, 0xFF00, 0, 0)}[kind]
            hdr = dds.dds_header_uncompressed(w, h, bpb * 8, masks)
            results.append((d, kind, hdr + linear[:size]))
        else:  # DXT1 / DXT3 / DXT5 / ATI2 / DXT3A / DXT5A
            linear = _crop(dds.untile(dds.endian_swap16(blob), pw, ph, bpb),
                           pw, ph, w, h, bpb, 4)
            # The FOURCC field is exactly 4 bytes -- a 5-character name like
            # "DXT5A" would overrun it and shift the whole 128-byte header.
            # DXT3A/DXT5A are single-channel BC4, whose DDS fourcc is ATI1.
            fourcc = {"DXT3A": b"ATI1", "DXT5A": b"ATI1"}.get(kind, kind.encode())
            hdr = dds.dds_header(w, h, fourcc, size)
            results.append((d, kind, hdr + linear[:size]))
    return results, pixel_base

def main():
    iso = sys.argv[1]; idx = int(sys.argv[2])
    outdir = sys.argv[3] if len(sys.argv) > 3 else "extracted/textures"
    os.makedirs(outdir, exist_ok=True)
    arc = Archive(iso)
    raw = arc.read_file(arc.files[idx])
    data, bounds = decompress_with_bounds(raw)
    res, pbase = extract_textures(data, bounds)
    print(f"file#{idx}: {len(res)} textures, pixel_base=0x{pbase:X}, "
          f"{len(bounds)} sub-resources")
    for d, fourcc, dds_bytes in res:
        pos = d["base"] + d["off"] - bounds[-1][0]     # absolute within the resource
        name = f"{idx}_{pos:07X}_{d['w']}x{d['h']}_{fourcc}.dds"
        with open(os.path.join(outdir, name), "wb") as f:
            f.write(dds_bytes)
    print(f"wrote {len(res)} .dds -> {outdir}")

if __name__ == "__main__":
    main()
