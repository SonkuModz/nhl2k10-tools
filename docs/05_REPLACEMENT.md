# 05 — Replacing assets

Everything here was implemented and verified against a real disc image: a texture
was encoded, compressed, written, read back from disc, decoded, and confirmed to
be the new art (40.6 dB) — then reverted to a byte-identical file. Audio likewise.

This document is the specification, not a manual for any particular tool.

## Safety model — read this first

Nothing is written without first recording what was there. Journal the original bytes of every region you touch to a side file before
writing, so any edit can be undone exactly:


`revert` restores the disc **byte-for-byte** (verified by SHA-256 both ways). If
the same region is written twice the *first* original is kept, so revert always
returns to the pristine disc rather than to an intermediate edit. Writes that
would extend the image are refused outright — the disc layout is fixed.

Keep the journal. Deleting it does not corrupt anything, but it does mean the
edits can no longer be undone automatically.

## Why in-place only

The engine sizes its decompression / VRAM buffer from the **original** resource, so
a larger *decompressed* payload overflows it and crashes (see [`06_EXTERNAL_NOTES.md`](06_EXTERNAL_NOTES.md)). We only ever
change content, never decompressed size, so that constraint holds by construction.

The *compressed* result still has to fit the archive slot. When it doesn't, the
replace is **refused** with the measured sizes rather than relocated — relocation
needs the TOC rewritten and record offsets redirected, which is exactly the path
that historically corrupted saves (again, see [`06_EXTERNAL_NOTES.md`](06_EXTERNAL_NOTES.md)).

## The compressor

The compressor must be the exact inverse of the decoder and should use a
**least-cost parse**: a literal costs 1 control bit + 8, a match 1 + 16, so the cheapest
encoding is a shortest path over positions. Because the token packs length into
`16 - offbits` bits the longest match is short (18 bytes at offbits=12), which
makes the DP cheap.

This matters. Greedy parsing came out **~1.8% larger** than the original cooker,
which was enough to fail the slot-fit check on a real edit. The optimal parse
beats the original on every block tested:

| file | offbits | original | ours | |
|---|---|---|---|---|
| #0 | 12 | 102,024 | 101,741 | −0.3% |
| #6 | 13 | 232,581 | 230,550 | −0.9% |
| #17 | 10 | 4,918 | 4,669 | −5.1% |
| #203 | 9 | 324 | 292 | −9.9% |

Round-trip every produced block through the real decoder before it goes anywhere
near the disc, and keep an all-literal fallback for when the matcher misbehaves.

**Speed.** The first version ran at 11 KB/s — 12.6 minutes for one texture in
file #17, which is not usable. Two fixes:

* The match search no longer allocates a `bytes` slice per position (the 3-byte
  hash key is a rolling integer) and stops as soon as a candidate hits the length
  cap: **11 -> 55 KB/s**.
* Re-use work. A block whose payload did not change is copied
  verbatim, and a changed block re-uses the ORIGINAL token stream up to the first
  differing byte . LZ decoder state is a pure function of the
  bytes decoded so far, so replaying those tokens reproduces the identical state —
  and the round-trip check still has to pass regardless.

| edit | before | now |
|---|---|---|
| #0 tex13 (95% into the blob) | ~40 s | **0.5 s** |
| #203 tex1 (66% in, 4.2 MB blob) | ~6.5 min | **78 s** |
| #17 tex5 (83% in, 8.4 MB blob) | ~12.6 min | **65 s** |

Only `depth=192` beats the original cooker (depth 16/32/64 give +2.6/+1.4/+0.8%),
and slot-fit needs that, so depth stays high and the savings come from doing less
work rather than worse work.

## Textures


* The image must be **exactly** the texture's size. Resolution changes need the
  Xenos packed mip-tail layout and are not supported.
* Encoders must be the exact inverse of the decoders. Fitting BC endpoints along
  the block's principal colour axis (rather than per-channel min/max) measures
  36–37 dB on BC formats; ARGB/L8/RG8 are lossless.
* **Alpha is straight, never premultiplied** (see [`06_EXTERNAL_NOTES.md`](06_EXTERNAL_NOTES.md)) — premultiplying is what
  makes replacements look washed out.
* **The mip chain is regenerated down to 32px**, not just mip 0. Replacing only
  the base level leaves the old art showing at distance *and* breaks
  the mip-consistency check that resolves texture order — a
  replaced texture with a stale chain made the extractor mis-order the file on
  re-read.

### Mip layout — what is verified, and the one part that is not

The layout (levels back to back from `padded_size(w,h)`, each tile-padded) is
confirmed **against the game's own data**, not just against our own writer — for
original textures every level down to 32px decodes at exactly these offsets:

| texture | 1/2 | 1/4 | 1/8 | 1/16 |
|---|---|---|---|---|
| #18 1024x512 DXT1 | 0.998 | 0.998 | 0.997 | 0.997 |
| #6 512x512 DXT5 | 0.999 | 0.999 | 0.998 | 0.996 |
| #203 2048x512 ARGB4444 | 0.998 | 0.993 | 0.983 | 0.973 |
| #1569 256x256 DXT1 | 0.994 | 0.994 | 0.995 | — |

(ATI2 scores lower, 0.27–0.90, because a box filter is not the game's normal-map
mip — the levels are in the right place, the content just does not downscale
linearly.)

**Below 32px the Xenos packed mip tail takes over** and squeezes the remaining
levels into the last 1–4 pages under a layout we have not cracked. Every original
texture leaves exactly that much over: 0x1000 (ARGB4444 2048x512), 0x2000 (DXT1),
0x4000 (DXT5/ATI2). An earlier version of this code wrote one of its own levels
into that space, which is structurally wrong. It now **stops at 32px and leaves
the original tail bytes untouched** — old art at extreme minification, but a valid
surface. Verified: every regenerated level correlates 1.000 with the new base, the
tail compares byte-identical to the original, and the mip check reads 1.000.

Cracking the packed tail (RexGlue `rex/graphics/xenos.h`) is the remaining work.

Verified end to end (dry runs, all fitting their slots):

```
f#203  tex0  2048x512  ARGB4444  rebuild=OK  32.64 dB  mip=1.00
f#203  tex1  2048x512  ATI2      rebuild=OK  54.54 dB  mip=1.00
f#17   tex5 1024x1024  DXT5      rebuild=OK  40.05 dB  mip=1.00
f#6    tex2   512x512  DXT1      rebuild=OK  39.32 dB  mip=1.00
```

## Audio


> **You cannot convert a WAV to XMA here, and neither can any other free tool.**
> There is no XMA2 encoder outside the Xbox 360 XDK — ffmpeg *decodes* XMA but
> cannot produce it. Reject PCM input explicitly rather than writing
> something the console cannot play.

What does work: any source that is *already* XMA2 — another clip from this game,
or a `.xma` from an XDK-based encoder. The stream must be whole 2048-byte packets
and fit the slot; the remainder of the slot is zero-filled.

Verified: 797 packets grafted from file #145 into file #56, decoded back with
ffmpeg, then reverted byte-identically.

## Known limits

* No relocation, so an edit that compresses worse than the original is refused.
* No resolution changes.
* Cubemaps write face 0 only.
* `global.iff`-class packs whose offsets the loader fills in at runtime are not
  addressable statically (see [`06_EXTERNAL_NOTES.md`](06_EXTERNAL_NOTES.md)).
* Pure-Python compression is slow on multi-MB resources.
