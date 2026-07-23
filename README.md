# NHL 2K10 (Xbox 360) — format documentation

Reverse-engineering notes on how **NHL 2K10** stores its data: the disc
filesystem, the archive container, the compression format, and the texture and
audio formats — plus what it takes to write modified assets back.

This is documentation, not a tool. Everything here is written as a specification
you can implement in whatever language you like. Findings were derived from binary
analysis and checked against ground truth; where something is unproven or wrong,
it says so.

No game data is included or distributed here. You need your own copy.

## Start here

| | |
|---|---|
| [`01_FILESYSTEM_AND_ARCHIVE.md`](docs/01_FILESYSTEM_AND_ARCHIVE.md) | XDVDFS disc layout, the `AA00B3BF` archive, locating a file |
| [`02_COMPRESSION.md`](docs/02_COMPRESSION.md) | the `0E4837C3` codec — decoding **and** encoding |
| [`03_TEXTURES.md`](docs/03_TEXTURES.md) | descriptors, GPU formats, tiling, mip layout |
| [`04_AUDIO.md`](docs/04_AUDIO.md) | raw XMA2 streams and how to make them playable |
| [`05_REPLACEMENT.md`](docs/05_REPLACEMENT.md) | writing assets back, and the constraints that bite |
| [`06_EXTERNAL_NOTES.md`](docs/06_EXTERNAL_NOTES.md) | an independent project's findings, cross-checked |
| [`file_census.csv`](docs/file_census.csv) | all 2,407 archive entries: index, size, hash, offset, magic |

## The short version

**Disc.** XDVDFS/XGD2, partition base `0x0FD90000`. Nearly everything lives in
four split files that concatenate into one 6.1 GB `AA00B3BF` archive holding 2,407
entries, indexed by a sorted 32-bit hash table with no filenames.

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
* **The archive TOC key is *not* CRC-32 of the filename.** An earlier version of
  these notes claimed it was; that was wrong, and is corrected in `01` §7. Naming
  archive entries remains unsolved.

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

* **Archive filenames.** The TOC hash is not CRC-32 of any name form tested.
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

Documentation of file formats, produced by reverse engineering for
interoperability. Format facts are not copyrightable, and no game code or content
is reproduced here.

Not affiliated with, endorsed by, or connected to 2K Sports, Visual Concepts,
Take-Two Interactive or the NHL. All trademarks belong to their respective owners.
