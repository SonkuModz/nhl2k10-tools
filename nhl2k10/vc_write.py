#!/usr/bin/env python3
"""
vc_write.py -- write replacement assets back into the NHL 2K10 disc image.

Texture replacement (image -> game) and audio replacement (XMA2 -> game), both
strictly in-place and fully reversible via tools/patcher.py.

Why in-place only
-----------------
The engine sizes its decompression / VRAM buffer from the ORIGINAL resource, so a
larger *decompressed* payload overflows and takes the game down (see
docs/04_MODDING_INTEL.md). We never change decompressed size -- only content --
so that constraint is satisfied by construction. The *compressed* result still has
to fit the archive slot; when it does not we refuse rather than relocate, because
relocation needs the TOC rewritten and is easy to get wrong.

Everything is journalled: `Patcher` records the original bytes of every region it
touches, so `revert_all()` restores the disc exactly.
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import numpy as np

import bcenc
import dds
import vc_compress
import vc_extract
import vc_texture as V
from nhl2k_arc import Archive
from patcher import Patcher


class ReplaceError(Exception):
    pass


# --------------------------------------------------------------------------
# resource rebuild
# --------------------------------------------------------------------------
def rebuild_raw(raw, new_dec, bounds, blocks, old_dec=None):
    """Re-emit an archive file whose decompressed payload is `new_dec`.

    Each 0E4837C3 block is recompressed with the SAME window variant (offbits)
    and flags the game chose for it, and every byte outside the blocks (the
    FF3BEF94 wrapper, padding, trailing data) is copied through untouched.

    When `old_dec` is supplied, a block whose payload is unchanged is copied
    verbatim, and a changed block re-uses the original token stream up to the
    first differing byte. Recompressing an 8 MB blob costs minutes in pure
    Python, so this is the difference between usable and not.
    """
    out = bytearray(raw[:blocks[0][0]])
    for i, (p, unc, comp, offbits) in enumerate(blocks):
        start, length = bounds[i]
        seg = new_dec[start:start + length]
        if len(seg) != unc:
            raise ReplaceError("block %d: payload is %d bytes, must stay %d"
                               % (i, len(seg), unc))
        flags = int.from_bytes(raw[p + 0x0C:p + 0x10], "big")
        reuse = None
        if old_dec is not None:
            old_seg = old_dec[start:start + length]
            if old_seg == seg:
                out += raw[p:p + comp]          # untouched block: copy as-is
                nxt = blocks[i + 1][0] if i + 1 < len(blocks) else len(raw)
                out += raw[p + comp:nxt]
                continue
            diff = next((k for k in range(len(seg)) if seg[k] != old_seg[k]), 0)
            if diff:
                reuse = vc_compress.reusable_prefix(raw[p:p + comp], diff)
        out += vc_compress.compress_verified(bytes(seg), offbits, flags, reuse=reuse)
        nxt = blocks[i + 1][0] if i + 1 < len(blocks) else len(raw)
        out += raw[p + comp:nxt]          # inter-block bytes, verbatim
    return bytes(out)


def _iso_writes(arc, stream_off, data):
    """Split a virtual-stream write into (iso_offset, chunk) pairs."""
    pieces = []
    pos, rest = stream_off, data
    while rest:
        _a, iso_off, avail = arc._stream_to_iso(pos)
        take = min(len(rest), avail)
        pieces.append((iso_off, rest[:take]))
        pos += take
        rest = rest[take:]
    return pieces


# --------------------------------------------------------------------------
# textures
# --------------------------------------------------------------------------
def encode_for_descriptor(img, d):
    """RGBA image -> the exact tiled, byte-swapped bytes the resource expects."""
    kind, bpb, bdim = V.GPU_FMT[d["fmt"]]
    w, h = d["w"], d["h"]
    if img.shape[0] != h or img.shape[1] != w:
        raise ReplaceError(
            "image is %dx%d but this texture is %dx%d -- resolution changes need "
            "the packed mip-tail layout and are not supported"
            % (img.shape[1], img.shape[0], w, h))
    pw, ph = V.padded_dims(w, h, bdim)
    if (pw, ph) != (w, h):
        # the stored base level is padded out to whole 32-block macro tiles;
        # replicate edge pixels into the pad so the tiler sees defined data
        pad = np.zeros((ph, pw, 4), np.uint8)
        pad[:h, :w] = img
        if ph > h:
            pad[h:, :w] = img[h - 1:h, :, :]
        if pw > w:
            pad[:, w:] = pad[:, w - 1:w, :]
        img = pad
    if bdim == 4:
        linear = bcenc.encode(img, pw, ph, kind)
    else:
        linear = bcenc.encode_uncompressed(img, pw, ph, kind)
    tiled = dds.tile(linear, pw, ph, bpb, block_dim=bdim)
    return dds.endian_swap16(tiled)


def encode_mip_chain(img, d):
    """Encode the base level AND regenerate the whole mip chain.

    Replacing only mip 0 leaves the original chain in place, which shows the OLD
    art at distance and also breaks `mip_consistency()` (level 1 no longer matches
    level 0). Levels sit back to back starting at `padded_size(w, h)`, each one
    tile-padded in its own right.

    That layout is verified against the game's own data: for original textures,
    every level down to 32px decodes at exactly these offsets and correlates
    0.96-0.999 with a downscale of the base.

    STOPS at 32px. Below that the Xenos **packed mip tail** squeezes the
    remaining levels into the last 1-4 pages under a layout we have not cracked
    (see docs/04_MODDING_INTEL.md). Writing our own level there would produce
    structurally invalid data, so we return short and let the caller leave the
    original tail bytes in place -- old art at extreme minification, but a valid
    surface.
    """
    _kind, bpb, bdim = V.GPU_FMT[d["fmt"]]
    w, h = d["w"], d["h"]
    budget = d["stored"]
    out = bytearray(encode_for_descriptor(img, d))
    level = img
    lw, lh = w, h
    while len(out) < budget:
        if lw <= bdim and lh <= bdim:
            break
        nw, nh = max(bdim, lw // 2), max(bdim, lh // 2)
        if nw < 32 or nh < 32:
            break                      # entering the packed tail -- leave it alone
        # box filter; float32 so the average does not wrap
        cw, ch = (lw // nw), (lh // nh)
        a = level[:nh * ch, :nw * cw].astype(np.float32)
        level = a.reshape(nh, ch, nw, cw, 4).mean(axis=(1, 3)).round() \
                 .clip(0, 255).astype(np.uint8)
        lw, lh = nw, nh
        sub = dict(d, w=lw, h=lh)
        try:
            chunk = encode_for_descriptor(level, sub)
        except Exception:
            break
        if len(out) + len(chunk) > budget:
            break
        out += chunk
    return bytes(out)


def replace_texture(iso, file_index, tex_index, img, dry_run=False, note=None):
    """Replace one texture inside one archive file. `img` is (h,w,4) uint8 RGBA.

    Returns a dict describing what was done (or would be done, if dry_run).
    """
    arc = Archive(iso)
    entry = arc.files[file_index]
    raw = arc.read_file(entry)
    dec, bounds = V.decompress_with_bounds(raw)
    blocks = vc_extract.find_blocks(raw)
    if not blocks:
        raise ReplaceError("file #%d has no compressed blocks" % file_index)

    descs = V.describe_textures(dec, bounds)
    if not 0 <= tex_index < len(descs):
        raise ReplaceError("file #%d has %d textures, no index %d"
                           % (file_index, len(descs), tex_index))
    d = descs[tex_index]

    payload = encode_mip_chain(img, d)
    at = d["base"] + d["off"]
    if at + len(payload) > len(dec):
        raise ReplaceError("texture runs past the end of the decompressed data")

    new_dec = bytearray(dec)
    new_dec[at:at + len(payload)] = payload
    new_raw = rebuild_raw(raw, bytes(new_dec), bounds, blocks, old_dec=dec)

    info = {
        "file": file_index, "texture": tex_index,
        "dims": "%dx%d" % (d["w"], d["h"]),
        "format": V.GPU_FMT[d["fmt"]][0],
        "orig_bytes": len(raw), "new_bytes": len(new_raw),
        "slot": entry.size, "fits": len(new_raw) <= entry.size,
    }
    if not info["fits"]:
        raise ReplaceError(
            "recompressed resource is %d bytes but the slot holds %d. The new "
            "image compresses worse than the original; try flatter//less noisy "
            "art. (Growing a packed resource overflows the engine's buffer.)"
            % (len(new_raw), entry.size))

    # pad to the original length so the TOC size field stays valid untouched
    new_raw = new_raw + b"\x00" * (entry.size - len(new_raw))

    p = Patcher(iso, dry_run=dry_run)
    try:
        written = 0
        for iso_off, chunk in _iso_writes(arc, entry.offset, new_raw):
            written += p.write(iso_off, chunk,
                               note=note or "tex f%d/#%d" % (file_index, tex_index))
        info["written"] = written
        info["journal"] = p.journal_path
    finally:
        p.close()
    return info


# --------------------------------------------------------------------------
# audio
# --------------------------------------------------------------------------
def replace_audio(iso, file_index, xma_bytes, dry_run=False, note=None):
    """Replace a raw XMA2 stream (`08000000` files) with another XMA2 stream.

    IMPORTANT: this takes XMA2 *bitstream* bytes, not WAV/MP3. There is no XMA
    encoder outside the Xbox 360 XDK -- ffmpeg decodes XMA but cannot produce it
    -- so the replacement must already be XMA2. Sources that work: another clip
    from this game, or a .xma produced by an XDK-based tool. See replace_audio_from_wav.
    """
    arc = Archive(iso)
    entry = arc.files[file_index]
    if len(xma_bytes) % 2048:
        raise ReplaceError("XMA2 streams are whole 2048-byte packets; got %d bytes"
                           % len(xma_bytes))
    if len(xma_bytes) > entry.size:
        raise ReplaceError("replacement is %d bytes, slot holds %d -- trim the clip"
                           % (len(xma_bytes), entry.size))

    head = arc.read_file_head(entry, 4)
    if head[:4] != b"\x08\x00\x00\x00":
        raise ReplaceError("file #%d is not an 08000000 XMA2 stream" % file_index)

    # keep the slot length: pad with silence-safe zero packets
    data = xma_bytes + b"\x00" * (entry.size - len(xma_bytes))

    p = Patcher(iso, dry_run=dry_run)
    try:
        written = 0
        for iso_off, chunk in _iso_writes(arc, entry.offset, data):
            written += p.write(iso_off, chunk, note=note or "audio f%d" % file_index)
    finally:
        p.close()
    return {"file": file_index, "packets": len(xma_bytes) // 2048,
            "slot": entry.size, "written": written, "journal": p.journal_path}


def xma_from_file(path):
    """Load XMA2 packet bytes from a .xma file or a RIFF wrapper around one.

    Accepts: raw packet streams, and RIFF/WAVE files whose `data` chunk holds an
    XMA2 bitstream (format tag 0x0166) -- i.e. exactly what tools/xma_extract.py
    writes. A normal PCM .wav is rejected: converting PCM to XMA2 needs an
    encoder that only ships in the Xbox 360 XDK.
    """
    import struct as _s
    blob = open(path, "rb").read()
    if blob[:4] != b"RIFF":
        return blob
    pos, end = 12, len(blob)
    fmt_tag = None
    data = None
    while pos + 8 <= end:
        cid = blob[pos:pos + 4]
        sz = _s.unpack_from("<I", blob, pos + 4)[0]
        body = blob[pos + 8: pos + 8 + sz]
        if cid == b"fmt ":
            fmt_tag = _s.unpack_from("<H", body, 0)[0]
        elif cid == b"data":
            data = body
        pos += 8 + sz + (sz & 1)
    if data is None:
        raise ReplaceError("%s has no RIFF data chunk" % path)
    if fmt_tag not in (0x0166, 0x0165):
        raise ReplaceError(
            "%s is WAVE format 0x%04X (PCM or similar), not XMA2 (0x0166). "
            "There is no XMA2 encoder outside the Xbox 360 XDK -- ffmpeg can "
            "decode XMA but cannot create it -- so the replacement clip must "
            "already be XMA2." % (path, fmt_tag or 0))
    return data


def audio_slots(iso, min_size=0):
    """List the 08000000 raw-XMA2 streams and their slot sizes, so a clip can be
    matched to somewhere it will fit."""
    arc = Archive(iso)
    out = []
    for e in arc.files:
        if e.size <= min_size:
            continue
        try:
            if arc.read_file_head(e, 4)[:4] == b"\x08\x00\x00\x00":
                out.append((e.index, e.size))
        except Exception:
            pass
    return out


def revert(iso):
    p = Patcher(iso)
    try:
        return p.revert_all()
    finally:
        p.close()


def _main(argv):
    import argparse
    ap = argparse.ArgumentParser(
        prog="vc_write",
        description="Write replacement textures and audio into an NHL 2K10 ISO. "
                    "Every write is journalled and can be undone exactly.")
    ap.add_argument("iso")
    sub = ap.add_subparsers(dest="cmd", required=True)

    t = sub.add_parser("texture", help="replace one texture")
    t.add_argument("file_index", type=int)
    t.add_argument("tex_index", type=int)
    t.add_argument("image", help="PNG/DDS/TGA at the texture's exact size")
    t.add_argument("--dry-run", action="store_true")

    a = sub.add_parser("audio", help="replace one 08000000 XMA2 stream")
    a.add_argument("file_index", type=int)
    a.add_argument("clip", help=".xma, or a RIFF wrapper around XMA2 (not PCM)")
    a.add_argument("--dry-run", action="store_true")

    sub.add_parser("list-audio", help="show the XMA2 streams and their slot sizes")
    nm = sub.add_parser("find", help="resolve an asset name to its archive index")
    nm.add_argument("name", help='e.g. "led_pit.iff"')
    sub.add_parser("list-names", help="every archive entry we can name")
    ls = sub.add_parser("list-textures", help="show the textures in one file")
    ls.add_argument("file_index", type=int)
    sub.add_parser("status", help="show journalled (modified) regions")
    sub.add_parser("revert", help="restore every modified region")

    ns = ap.parse_args(argv)
    if ns.cmd == "texture":
        from PIL import Image
        img = np.array(Image.open(ns.image).convert("RGBA"))
        info = replace_texture(ns.iso, ns.file_index, ns.tex_index, img,
                               dry_run=ns.dry_run)
        print(("DRY RUN " if ns.dry_run else "") + str(info))
    elif ns.cmd == "audio":
        data = xma_from_file(ns.clip)
        print(replace_audio(ns.iso, ns.file_index, data, dry_run=ns.dry_run))
    elif ns.cmd == "find":
        import names as NM
        arc = Archive(ns.iso)
        e = NM.resolve(arc, ns.name)
        if e is None:
            print("%s -> no entry (hash %08X)" % (ns.name, NM.name_hash(ns.name)))
            return 1
        print("%s -> #%d, %d bytes, hash %08X" % (ns.name, e.index, e.size, e.crc))
    elif ns.cmd == "list-names":
        import names as NM
        arc = Archive(ns.iso)
        cat = NM.build_catalog()
        rows = [(e.index, e.size, cat[e.crc]) for e in arc.files if e.crc in cat]
        for idx, size, n in sorted(rows, key=lambda r: r[2]):
            print("  #%-5d %10d  %s" % (idx, size, n))
        print("%d of %d entries named" % (len(rows), len(arc.files)))
    elif ns.cmd == "list-audio":
        for idx, sz in audio_slots(ns.iso):
            print("  #%-5d %12d bytes" % (idx, sz))
    elif ns.cmd == "list-textures":
        arc = Archive(ns.iso)
        raw = arc.read_file(arc.files[ns.file_index])
        dec, bounds = V.decompress_with_bounds(raw)
        for i, d in enumerate(V.describe_textures(dec, bounds)):
            print("  %-3d %5dx%-5d %-9s at +0x%07X"
                  % (i, d["w"], d["h"], V.GPU_FMT[d["fmt"]][0],
                     d["base"] + d["off"] - bounds[-1][0]))
    elif ns.cmd == "status":
        p = Patcher(ns.iso)
        print("%d modified region(s); journal %s" % (p.pending(), p.journal_path))
        for off, e in sorted(p.journal["entries"].items(), key=lambda kv: int(kv[0])):
            print("  0x%010X  %9d bytes  %s" % (int(off), e["len"], e["note"]))
        p.close()
    elif ns.cmd == "revert":
        print("reverted %d region(s)" % revert(ns.iso))


if __name__ == "__main__":
    _main(sys.argv[1:])
