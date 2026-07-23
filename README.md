# NHL 2K10 (Xbox 360) — format research and modding toolkit

Reverse-engineering notes and working tools for **NHL 2K10** on Xbox 360: read the
disc, extract textures and audio, and write replacements back — reversibly.

Everything here was derived from binary analysis and verified against ground
truth. Where something is unproven, the docs say so.

> **You supply your own game.** No game data, executable or extracted asset is
> included or distributed here, and none ever will be. These tools operate on a
> disc image you dump yourself from a copy you own.

## What works

| | status |
|---|---|
| XDVDFS / XGD2 disc filesystem | read |
| `AA00B3BF` archive (2,407 files across four splits, 6.1 GB stream) | read |
| `0E4837C3` compression | **decoder and encoder**, both verified |
| Textures → DDS/PNG | **275/345 byte-identical** to Xenia GPU dumps; 94.8% self-verify archive-wide |
| Audio → WAV | XMA2 via ffmpeg |
| Texture replacement | **working**, in-place, journalled and reversible |
| Audio replacement | working (XMA2 in, XMA2 out — see limits) |

Formats decoded: DXT1, DXT3, DXT5, DXN/ATI2, DXT3A, DXT5A, A8R8G8B8, R5G6B5,
A4R4G4B4, L8, R8G8.

## Setup

```bash
git clone <your-fork-url> nhl2k10-tools && cd nhl2k10-tools
```
```bash
pip install -r requirements.txt
```

Then point the tools at your own disc image — either drop the `.iso` in the repo
folder (it is git-ignored), or set an environment variable:

```bash
export NHL2K10_ISO="/path/to/your/NHL 2K10.iso"
```

**Work on a copy.** The write commands modify the image in place. They journal
every byte they overwrite so `revert` restores it exactly, but a spare copy is
still the sensible precaution.

## Use

Graphical:

```bash
python nhl2k10_extractor_gui.py
```

Browse all 2,407 files, filter by type, extract raw or decompressed, export
textures to DDS and audio to WAV, and replace assets from the **Mod** row.

Command line:

```bash
python tools/vc_write.py "your.iso" list-textures 18
```
```bash
python tools/vc_write.py "your.iso" texture 18 3 my_art.png
```
```bash
python tools/vc_write.py "your.iso" status
```
```bash
python tools/vc_write.py "your.iso" revert
```

`revert` restores every modified region byte-for-byte (SHA-256 verified in both
directions). The journal lives beside the image as `<iso>.undo.json` — keep it.

## How to trust this

Two independent checks, both reproducible:

1. **Byte-exact against Xenia GPU dumps.** Run the game in Xenia with texture
   dumping on; each `.dds` + `.json` it writes is ground truth. 275 of 345 sampled
   textures match byte-for-byte — including files where *every* texture matches
   (#1569 91/91, #261 74/74). The misses are textures that never appear in the
   dumps, not decode failures: a block-multiset comparison finds **zero**
   wrong-tiling cases.
2. **Mip self-consistency**, which needs no reference at all. A texture's mip
   level 1 must be a 2× downscale of its own base, so correlating them scores
   ~1.0 when the decode is right and ~0.0 when it is not. 163/172 (94.8%) across
   40 randomly sampled files.

Both are worth understanding before trusting any output — see
[`docs/02_AUDIO_AND_TEXTURES.md`](docs/02_AUDIO_AND_TEXTURES.md).

## Known limits

* **No resolution changes.** Replacements must match the original dimensions; the
  Xenos *packed mip tail* layout is not reversed yet.
* **Mips regenerate down to 32px only.** Below that the packed tail takes over, so
  the original texture's smallest levels are left in place — visible only at
  extreme minification.
* **No relocation.** If a replacement compresses worse than the original it is
  refused rather than moved, because relocating needs the TOC rewritten and that
  is the path that historically corrupts saves.
* **You cannot convert WAV to XMA here, or with any free tool.** No XMA2 encoder
  exists outside the Xbox 360 XDK — ffmpeg decodes XMA but cannot produce it. So
  audio replacement takes clips that are *already* XMA2 (another in-game clip
  works fine). PCM input is rejected with an explicit message rather than writing
  something the console cannot play.
* Archive filenames are **not recovered.** The TOC key is a 32-bit hash that is
  *not* CRC-32 of any name form tested — see the correction in
  [`docs/00_RESEARCH_REPORT.md`](docs/00_RESEARCH_REPORT.md) §7.
* Cubemaps write face 0 only.
* Compression is pure Python: 0.5 s to ~80 s per edit depending on where in the
  resource it lands.

## Documentation

| | |
|---|---|
| [`docs/00_RESEARCH_REPORT.md`](docs/00_RESEARCH_REPORT.md) | filesystem, archive format, file census, hashing |
| [`docs/01_DECOMPRESSOR_RE.md`](docs/01_DECOMPRESSOR_RE.md) | how the `0E4837C3` codec was reversed |
| [`docs/02_AUDIO_AND_TEXTURES.md`](docs/02_AUDIO_AND_TEXTURES.md) | descriptor layout, tiling, mip rules, verification |
| [`docs/04_MODDING_INTEL.md`](docs/04_MODDING_INTEL.md) | write-back constraints, cross-checked against external work |
| [`docs/05_WRITE_BACK.md`](docs/05_WRITE_BACK.md) | replacement pipeline and safety model |

`docs/file_census.csv` lists all 2,407 archive entries (index, size, hash, offset,
magic).

Failed approaches are kept deliberately — `tools/lzss_bruteforce.py`,
`tools/vc_bruteforce2.py` and friends are the record of what did *not* work while
attacking the codec, which is often the more useful half.

## Layout

```
tools/          extraction, decode/encode, write-back (stdlib + numpy/Pillow)
  nhl2k_arc.py    archive TOC + direct-from-ISO reads
  vc_decomp.py    0E4837C3 decoder      vc_compress.py  encoder
  vc_texture.py   descriptor parsing    vc_write.py     replacement
  bcdec.py        BC1-BC5 decode        bcenc.py        BC1-BC5 encode
  patcher.py      journalled writes + exact undo
docs/           findings, including what remains unsolved
```

## Legal

Reverse engineering for interoperability. No copyrighted game content is
included, and the repository ships nothing you could use without your own copy of
the game.

Verbatim decompiler output from the retail executable is **not** committed
(`docs/ghidra_decompiled.c` is git-ignored); the documentation describes the
*format* — facts about data layout — and the implementations here are written
from those findings.

Not affiliated with, endorsed by, or connected to 2K Sports, Visual Concepts,
Take-Two Interactive or the NHL. All trademarks belong to their respective owners.

## Licence

MIT — see [LICENSE](LICENSE). Applies to the code and documentation in this
repository only, not to anything you extract from your own game disc.
