# 03 — Textures: format and dumping

How texture data is stored, located and decoded. Everything below is verified
against Xenia GPU dumps or against the game's own data; where something is
unproven it says so.

Audio is covered separately in [`04_AUDIO.md`](04_AUDIO.md).

## Overview

Texture IFFs decompress to sub-resources: one or more **descriptor tables**, and
the **last** sub-resource holding the tiled pixel data. Each descriptor is
0xE0 bytes and embeds an Xbox 360 GPU texture fetch constant at +0x3C.

### Descriptor layout (all big-endian) — every field below is verified

```
+0x08  u16 width                 +0x0A  u16 height
+0x14  u32 offset                & 0xFFFFF000 = offset into the pixel resource
                                 (may be a constant sentinel of 1 — see below)
+0x18  u32 base_size             tile-PADDED bytes of mip level 0
+0x1C  u32 mip_size              tile-padded bytes of the whole mip chain
+0x3C  GPU dword0                bit31 = tiled, bits22..30 = pitch/32
+0x40  GPU dword1                bits0..5 = format, bits6..7 = endian (1 = 8in16)
+0x44  GPU dword2                (width-1) | (height-1)<<13   -- validation
+0x4C  GPU dword4                mip count = ((v>>6) & 0xF) + 1
+0x50  GPU dword5                bits12..31 = mip address (always == base_size)
```

GPU formats seen: `0x02` 8(L8), `0x04` 5_6_5, `0x06` 8_8_8_8, `0x0A` 8_8,
`0x0F` 4_4_4_4, `0x12` DXT1, `0x13` DXT2_3, `0x14` DXT4_5, `0x31` DXN,
`0x3A` DXT3A, `0x3B` DXT5A (both single-channel BC4, 8 bytes/block).

**Do not leave a format unmapped.** An unknown format fails validation, and a
failed entry *terminates the table walk* — so one unmapped `0x3B` in the middle
of a run silently discarded every descriptor after it. Mapping DXT5A alone took
file #0 from 4 textures to 14 and made its extents sum exactly to the region.

Note the DDS FOURCC field is exactly 4 bytes: `"DXT5A"` is five characters and
overruns it, shifting the whole 128-byte header. DXT3A/DXT5A are written as
`ATI1` (BC4).

### The four rules that make it work

1. **`pixel_base` = the EXACT start of the last sub-resource** — do *not* round
   it up to 4KB. That start is usually unaligned (e.g. `0x6DB058`) while the
   descriptor offsets are 4KB-aligned; rounding shifted every texture by ~0xFA8
   bytes and made every correct untiler look broken.

2. **Stored size = `+0x18` + `+0x1C`, read from the file.** No prediction, no
   learned table, no solver. Verified: consecutive `+0x14` offsets differ by
   exactly this sum (file #18: 11/11 gaps match), and for file #17 the six
   textures' sizes sum to the sub-resource size to the byte.

3. **The base level is tile-padded**: both dimensions are rounded up to whole
   32-block macro tiles (minimum one 4KB page). So a 512x64 DXT1 is *stored* as
   128x32 blocks — twice its nominal size. Read and untile at the **padded**
   dimensions, then crop. Ignoring this was worth ~100 textures.

4. **Dimensions are not always powers of two** (e.g. the 3968x256 jersey
   atlases). A power-of-two filter silently dropped them — and because an
   invalid entry terminates the table walk, one dropped entry truncated whole
   tables. Validate an entry by cross-checking three independent fields instead:
   w/h, the GPU size dword at +0x44, and `+0x18` against the format-implied
   padded size (allowing 6x for cubemaps — `0x0A` entries are cubemaps).

5. **Descriptors are often isolated, not tabulated.** Many IFFs embed a single
   descriptor inside a larger per-object record (file #0 has 14 descriptors at
   irregular strides like 0x140C0 / 0x78A0 / 0x7940). `find_descriptor_tables`
   therefore uses `min_run=1`; requiring two consecutive entries threw most of
   them away. The three-field validity test is strict enough to carry this.

Decode pipeline:

```
linear = crop( unswizzle_x360( endian_swap16(blob), pw, ph, block_dim, block_bytes ) )
DDS    = header + linear
```

The tiling function is the stock Xbox 360 `XGAddress2DTiledOffset`. Implement it
carefully or borrow a known-good one — a hand-rolled version proved *non-bijective*
(576 distinct addresses out of 4096, dropping x bits 3 and 4), which silently
repeats the image ~4x4 and is easy to mistake for a different bug. Verify any
implementation is a true permutation before trusting it.

### The full record is 0xE0 bytes starting 0x58 earlier

External Mod Launcher notes describe the same structure with a different anchor,
which independently confirms the field mapping above:

| their field | their offset | ours |
|---|---|---|
| width | `+0x60` | `+0x08` |
| height | `+0x62` | `+0x0A` |
| VRAM offset | `+0x6C` | `+0x14` |
| mip0 size | `+0x70` | `+0x18` |
| mip tail size | `+0x74` | `+0x1C` |

Every one is ours + 0x58, so a record really begins **0x58 before** the part we
parse — which is why our tables kept starting at 0x58. Those leading 0x58 bytes
are unexamined and are the obvious place to look for a per-texture name or hash.

The resource header also names its first record array outright:
`u32 count @0x20`, `u32 ptr @0x24`, with the table at **`ptr + 0x7B`**. Verified
exactly on #0 (3), #18 (65), #261 (86), #507 (96) — it agrees with the scanner in
all four, so `record_array_header()` is wired in as a cross-check rather than as
the primary source (it only describes the *first* table; #18 has four).

Two further notes from the same source, not yet independently checked here:
type bits `(dword0 & 3) == 2` marks a 2D texture (a cheap extra validity test),
and a few assets carry a single fetch constant at `+0x94` instead of an array.

### Multi-group IFFs and out-of-order storage

An IFF may hold several descriptor tables. Compare the sum of the group extents
against the sub-resource size to tell which of three layouts is in play:

* **sum == region** — groups are consecutive regions, laid end to end from
  `pixel_base`. (File #18: group 0 ends exactly where group 1 begins.)
* **sum > region** — the tables are *overlapping views of one shared region*,
  indexing it with absolute offsets, so every group uses `pixel_base`. File #507
  has three tables totalling 0x111B000 inside a 0x7F4000 region; sharing the base
  moves it from 30 ok / 45 bad to 84 ok / 7 bad.
* **sum < region** — groups are spaced by something not yet modelled; locate each
  one by sliding it over the region and keeping the offset where a texture's own
  mip chain confirms it. (Files #352, #479 — still unsolved.)

Some IFFs leave `+0x14` at a constant sentinel (`1`) for every entry. There the
textures are stored back-to-back — but **not necessarily in table order** (file
#17 stores the jersey sheet before its normal map, while file #203 stores the
same pair the other way round). A **mip-consistency check** resolves this without any
ground truth: a texture's mip level 1 must be a 2x downscale of its own base, so
correlating the two scores ~0.99 at the right offset and ~0.00 elsewhere.
Greedily fill slots using that score.

## Verification

Two independent methods, both reusable:

* **Byte-exact vs Xenia GPU dumps.** Run the game in Xenia with texture dumping;
  each `.dds` + `.json` is ground truth. Compare our DDS payload byte-for-byte.
  Note DX10-fourcc dumps put the payload at offset **148**, not 128.
  Current: **275 / 345 byte-exact** over 19 sample IFFs — including
  **#1569 91/91, #261 74/74, #203 2/2, #18 73/86**. A block-**multiset**
  comparison (order-independent) separates *wrong bytes* from *wrong tiling*;
  there are currently **zero** wrong-tiling cases, and the remaining misses are
  textures that simply never appear in the dumps.
* **Mip self-consistency** (no ground truth needed) — see above. This is the
  metric to use on textures Xenia never rendered.

To view the result: `ffmpeg -i x.dds x.png` handles the common formats, but note
Pillow cannot decode BC4/BC5 and ffmpeg does not recognise the `ATI2` fourcc, so
single- and dual-channel formats need your own decoder.

Only the base mip level is emitted as DDS today; the mip chain is located and
sized correctly, so writing full mip pyramids is a small change.

## Known remaining gaps (do not assume these are solved)

* A random 40-IFF sweep self-verifies **172 / 183 = 94%** of mip-checkable
  textures. **Trust this metric — do not explain away a low score.** Every time a
  texture scored below 0.5 it turned out to be genuinely misplaced, including the
  file #17 letter atlas that "looked fine" and was in fact the wrong texture in
  that slot.
* Files #352, #479 and #0 are **fixed** — they were the `sum < region` class, and
  the cause was the unmapped DXT5A plus `min_run=2`, not an unknown layout. With
  both fixed their extents sum to the region exactly and every position that the
  Xenia dumps can confirm matches.
* **File #507 still has 7 bad of 96** — the shared-region case is right in bulk
  but not complete.
* `ARGB4444` sits at 2/11 dump-verified, but every ARGB4444 we can inspect
  visually (files #17, #203) is pixel-clean and self-verifies at 0.99 — the
  misses look like absence from the dumps rather than defects.
* 42 of 225 textures in the sweep have no mip chain at all, so nothing can
  confirm them; they are placed by elimination (a slot no mip-bearing texture
  claims must belong to a mip-less one).
* Cubemaps (`0x0A`, and any `+0x18 == 6 * padded_size`) are detected and sized
  but only the first face is emitted.
