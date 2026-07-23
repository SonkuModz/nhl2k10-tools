# NHL 2K10 (Xbox 360) — format documentation and tools

How **NHL 2K10** stores its data — disc filesystem, archive container,
compression, textures, audio — documented as a specification, with a working
Python implementation alongside it.

The documentation is the point; the code is proof it is correct. Every finding
was derived from binary analysis and checked against ground truth, and where
something is unproven or wrong, it says so.

> **You supply your own game.** No game data is included or distributed here.
> These tools read a disc image you dump from a copy you own.

## Layout

```
docs/          the findings, written to be implementable in any language
nhl2k10/       the library — read the disc, decode, encode, write back
research/      one-off scripts from the investigation, including the dead ends
ghidra/        headless decompilation of the codec routines
nhl2k10_gui.py browse, extract and replace with a UI
```

## Quick start

```bash
pip install -r requirements.txt
```

Point it at your own disc image — drop the `.iso` in this folder (it is
git-ignored) or set `NHL2K10_ISO`. Then:

```bash
python nhl2k10_gui.py
```

or from the command line:

```bash
python nhl2k10/vc_write.py "your.iso" list-textures 18
```
```bash
python nhl2k10/vc_write.py "your.iso" texture 18 3 my_art.png
```
```bash
python nhl2k10/vc_write.py "your.iso" revert
```

Writes are journalled: `revert` restores the image byte-for-byte, verified by
SHA-256 in both directions. Work on a copy anyway.

## The documentation

| | |
|---|---|
| [`01_FILESYSTEM_AND_ARCHIVE.md`](docs/01_FILESYSTEM_AND_ARCHIVE.md) | XDVDFS disc layout, the `AA00B3BF` archive, locating a file |
| [`02_COMPRESSION.md`](docs/02_COMPRESSION.md) | the `0E4837C3` codec — decoding **and** encoding |
| [`03_TEXTURES.md`](docs/03_TEXTURES.md) | descriptors, GPU formats, tiling, mip layout |
| [`04_AUDIO.md`](docs/04_AUDIO.md) | raw XMA2 streams and how to make them playable |
| [`05_REPLACEMENT.md`](docs/05_REPLACEMENT.md) | writing assets back, and the constraints that bite |
| [`06_EXTERNAL_NOTES.md`](docs/06_EXTERNAL_NOTES.md) | an independent project's findings, cross-checked |
| [`file_census.csv`](docs/file_census.csv) | all 2,407 archive entries: index, size, hash, offset, magic |

Module-level notes live in [`nhl2k10/README.md`](nhl2k10/README.md) and
[`research/README.md`](research/README.md).

## The short version

**Disc.** XDVDFS/XGD2, partition base `0x0FD90000`. Nearly everything lives in
four split files that concatenate into one 6.1 GB `AA00B3BF` archive holding 2,407
entries, indexed by `CRC-32(NAME.UPPER())` and sorted for binary search. Names are
recoverable by generating candidates and testing — 443 so far.

**Files.** Most entries are Visual Concepts IFF packages (`FF3BEF94`) containing
one or more `0E4837C3` compressed blocks. That codec is an interleaved LZSS with
ten window variants — fully documented here, both directions. The exception is
audio: `08000000` files are raw XMA2 and skip compression entirely.

**Textures.** Each 0xE0-byte descriptor embeds an Xbox 360 GPU texture fetch
constant giving format, dimensions and offset. Pixel data is tiled and 16-bit
byte-swapped. Formats seen: DXT1, DXT3, DXT5, DXN, DXT3A, DXT5A, A8R8G8B8,
R5G6B5, A4R4G4B4, L8, R8G8.

**Replacement** works in place and is reversible, with real limits — no resolution
changes, no relocation, and no way to author new audio (see below).

## Things that cost real time — read before you start

These are the mistakes that produced convincing but wrong results:

* **`pixel_base` must be the exact start of the last sub-resource, not rounded up
  to 4 KB.** That start is usually unaligned while descriptor offsets are aligned.
  Rounding shifts every texture by ~0xFA8 bytes and makes every correct untiler
  look broken.
* **The stored base level is tile-padded** to whole 32-block macro tiles. A 512×64
  DXT1 occupies twice its nominal size. Read and untile at padded dimensions, then
  crop.
* **Dimensions are not always powers of two** (3968×256 exists). A power-of-two
  filter drops those descriptors — and because an invalid entry ends the table
  walk, one dropped entry silently truncates the rest of the table.
* **An unmapped GPU format does the same thing.** One unknown `0x3B` (DXT5A) in
  the middle of a run discarded every descriptor after it.
* **Verify your tiling function is bijective.** A hand-rolled
  `XGAddress2DTiledOffset` produced 576 distinct addresses out of 4096 and
  repeated the image ~4×4 — subtle enough to survive eyeballing.
* **Import the XEX as `PowerPC:BE:64`, not BE:32.** As 32-bit the `ldx`/`std`
  instructions decode as bad data and the decompiler truncates mid-function.
* **The archive TOC key is CRC-32 of the *uppercased* name, extension included** —
  while the `E4791207` manifests use CRC-32 of the *lowercase stem*. Two tables,
  two rules. An earlier version of these notes claimed the archive key was not a
  CRC-32 at all; that was wrong, and the sweep that "proved" it had a helper which
  force-lowercased its own input. If a parameter sweep returns zero across every
  variant, suspect the harness.

## How the claims here were checked

Two independent methods, both reproducible:

1. **Byte-exact against Xenia GPU dumps.** Run the game in Xenia with texture
   dumping enabled; each `.dds` + `.json` it writes is ground truth. 275 of 345
   sampled textures matched byte-for-byte, including files where every texture
   matched. A block-*multiset* comparison — order-independent — distinguishes
   "wrong bytes" from "wrong tiling", and finds zero wrong-tiling cases.
2. **Mip self-consistency**, which needs no reference at all: a texture's mip
   level 1 must be a 2× downscale of its own base, so correlating them scores
   ~1.0 when the decode is right and ~0.0 when it is not. 163/172 (94.8%) across
   40 randomly sampled files.

The second is the more useful one, since it works on textures no emulator dump
covers. It also caught a mistake the first could not: mip levels written at the
same offsets a checker assumes will always agree with it. Validate a writer
against the *game's* data, never against your own reader.

## What is still unknown

* **Most archive filenames.** The hash is one-way, so names are recovered by
  generating candidates and testing; 443 of 2,407 entries are named so far. The
  method works, the template list is just incomplete.
* **The Xenos packed mip tail** — the layout below 32px, which is why replacement
  can only regenerate mips down to that size.
* **Resolution changes**, which need that same packed layout.
* **Meshes and models.** Barely touched.
* **Sample-rate metadata** for audio; it is not in the bitstream.
* A handful of textures in shared-region packages still decode wrong (7 of 96 in
  one file).

## Credits

* The **QuickBMS** community `nhl_2k10.bms` / `nhl_2k11.bms` scripts
  ([QuickBMS](https://aluigi.altervista.org/quickbms.htm), Luigi Auriemma) worked
  out the `AA00B3BF` header fields first.
* [**Xenia**](https://xenia.jp/) made all of this falsifiable — its texture dumps
  are the ground truth every claim above is measured against.
* An independent Mod Launcher project's notes, cross-checked in
  [`06_EXTERNAL_NOTES.md`](docs/06_EXTERNAL_NOTES.md) — including where the two
  accounts disagree.

## Legal

Reverse engineering for interoperability. No game code or content is reproduced
here, and the repository ships nothing usable without your own copy of the game.

Not affiliated with, endorsed by, or connected to 2K Sports, Visual Concepts,
Take-Two Interactive or the NHL. All trademarks belong to their respective owners.
