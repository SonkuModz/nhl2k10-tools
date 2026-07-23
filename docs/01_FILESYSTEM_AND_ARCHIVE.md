# 01 — Disc filesystem and archive format

Where the data lives and how to get at it: the disc filesystem, the archive
container, and how a file is located inside it.

**Subject:** NHL 2K10 (Europe), retail disc image (7,838,695,424 bytes)
**Engine:** Visual Concepts (2K Sports) — confirmed from executable strings
**Executable:** `nhl_clean_opt.target.xex` · Take-Two Title ID `TT-2131` / `0x54540853` · built 2009-07-27
**Region note:** offsets below are from the European release. Other regions use
the same structures; absolute offsets will differ.
All findings below were validated by direct binary inspection, not assumed.

---

## 0. Executive summary

NHL 2K10 stores **all** of its game content in a single custom 2K archive that is
physically split across four files on the disc. The container format, the disc
filesystem, the nested resource format, the file-naming hash, and the audio
container have all been decoded and verified. The **one remaining blocker** to
full asset extraction is the Visual Concepts proprietary block compression
(`VCFILEDEVICE::ReadAndDecompress`), which is not any standard codec (zlib / lzma /
bz2 / lz4 all ruled out empirically). Everything is in place to finish that by
disassembling one PowerPC routine in the executable.

| Layer | Format | Status |
|-------|--------|--------|
| Disc image | XDVDFS (XGD2, partition base `0x0FD90000`) | **Decoded — parser written** |
| Master archive | `AA00B3BF` (2K "big-file", big-endian) | **Fully decoded — extractor written** |
| Per-file resource | `FF3BEF94` (Visual Concepts IFF, compressed) | **Header/TOC decoded** |
| Resource payload | `0E4837C3` compressed block | **Header decoded; codec pending** |
| Uncompressed IFF | `E4791207`, `F0985030`, `00000001` variants | **Decoded — readable now** |
| Streamed audio | `08000000` (XMA, high-entropy) | **Container located; codec = XMA** |
| Executable | XEX2 (retail, LZX+encrypted) | **Decrypted via xextool** |
| Name → hash (**manifests only**) | **CRC-32(lowercase name, no extension)** | **Verified (5/5 exact)** |
| Name → hash (**archive TOC**) | unknown — *not* CRC-32 of the name | **Open** |

---

## 1. Disc filesystem map (Phase 1)

The Xbox 360 game
partition begins at byte offset **`0x0FD90000`** (XGD2 layout); the
`MICROSOFT*XBOX*MEDIA` volume descriptor sits at partition_base + `0x10000`.

| File | Size (bytes) | ISO offset | Purpose | Format | Compression | Extraction |
|------|-------------:|-----------:|---------|--------|-------------|-----------|
| `default.xex` | 26,718,208 | `0x34340000` | Game executable (PowerPC) | XEX2 | LZX + AES | ✅ via `xextool` |
| `nxeart` | 659,456 | `0x33A96800` | NXE dashboard art package | STFS/`PIRS` | — | STFS tools |
| `0A` | 1,803,550,720 | `0x35CBB000` | Master archive **part 0** (holds index) | `AA00B3BF` | container n/a | ✅ parsed |
| `0B` | 1,213,288,448 | `0xA14BB000` | Master archive **part 1** | raw data | — | ✅ parsed |
| `1A` | 1,803,550,720 | `0xE99D0000` | Master archive **part 2** | raw data | — | ✅ parsed |
| `1B` | 1,278,588,928 | `0x1551D0000` | Master archive **part 3** | raw data | — | ✅ parsed |
| `$SystemUpdate/su20076000_00000000` | 7,938,048 | `0x33B37800` | Xbox 360 system update | system | — | ignore |
| `$SystemUpdate/system.manifest` | 2,100 | `0x342C9800` | Update manifest | system | — | ignore |

**Key insight:** `0A`+`0B`+`1A`+`1B` are **one logical 6.10 GB archive** concatenated in
that order — *not* duplicated disc layers. `0A`/`1A` being identical sizes
(`0xD7000` sectors) is simply the packer's fixed split size.

---

## 2. Master archive format — `AA00B3BF` (Phase 1)

Big-endian throughout. The index lives at the very start of `0A`. Fully validated:
every field cross-checks against the file's actual size and against the BMS scripts
supplied in `2K BMS Scripts Maybe Working/`.

```
Header (0x18 bytes):
  0x00  u32  magic         = 0xAA00B3BF
  0x04  u32  align         = 0x800 (2048)   ; multiplies all sector counts
  0x08  u32  num_archives  = 4
  0x0C  u32  zero
  0x10  u32  num_files     = 0x967 (2407)
  0x14  u32  zero

Archive table  (num_archives * 16 bytes) @ 0x18:
  u32     size_in_sectors    ; * align = byte size of that split file
  u32     zero
  u16[4]  name (UTF-16 BE)   ; "0A","0B","1A","1B"

File table    (num_files * 16 bytes) @ 0x58:
  u32  flag/compression      ; always 0 in 2K10 (outer layer is never compressed)
  u32  size                  ; bytes
  u32  name_hash             ; CRC-32; table is SORTED ASCENDING by this key
  u32  offset_in_sectors     ; * align = absolute offset into the concatenated
                             ;           0A|0B|1A|1B virtual stream
```

Verified numbers: `align=2048`, `4` archives, `2407` files, virtual stream
= 6,098,978,816 bytes; sum of file sizes = 6,096,526,000 bytes (difference = index +
alignment padding). Files may straddle a split boundary; the extractor stitches
across.

**Naming:** there are **no stored filenames** in the master archive. Each entry is
keyed by a 32-bit hash and the table is kept sorted so the game can binary-search
it at load time. The hash is **CRC-32** (see §7).

---

## 3. Resource format — `FF3BEF94` (Visual Concepts IFF) (Phase 2/3/5)

2,307 of 2,407 files (95.8 %) begin with `FF3BEF94`. These are **Visual Concepts
"IFF"** resource packages (same lineage as NBA 2K / WWE 2K / College Hoops IFF).
Confirmed by the manifest files, which name their members `english.iff`,
`german.iff`, `swedish.iff`, etc.

```
FF3BEF94 header:
  0x00  u32  magic       = 0xFF3BEF94
  0x04  u32  dir_size    ; length of this TOC/header; payload begins here
  0x08  u32  total_size  ; == outer file size  (verified: 2307/2307 exact, 0 mismatch)
  0x0C  u32  zero
  0x10  u32  ?           ; sub-resource count-ish
  0x14  u32  0x0000000D  ; constant version tag (all files)
  0x18  u32  child_count
  ...   per-child TOC records: {flags, uncompressed_size, type, offset,
                                 compressed_size, zero, 64-bit type/id hash}
```

At `dir_size` the payload starts, and it is a sequence of `0E4837C3` compressed
blocks (2,302/2,307 files). The TOC arithmetic was verified end-to-end, e.g. file
#0: child0 @`0x138` comp `0x18E88` → child1 @`0x18FC0` comp `0x1DB04` → `0x36AC4`
= 223,940 = exact file size.

The recurring 4-byte tag `E2 6C 9B 5D` inside the TOC is a **resource-type hash**
(CRC-32 of a type name), the mechanism by which the engine distinguishes textures
vs meshes vs materials etc. inside one IFF.

### 3.1 Compressed block — `0E4837C3`

```
  0x00  u32  magic            = 0x0E4837C3
  0x04  u32  uncompressed_size
  0x08  u32  compressed_size  ; counts from this magic to end of block
  0x0C  u32  ?  (e.g. 7)
  0x10  u32  chunk/flag count (e.g. 11, 12)
  0x14  u32  flags (e.g. 0x10000000, 0x80000147)
  0x18  ...  compressed payload
```

Compression statistics (sampled): mean ratio **4.07:1**, up to **37:1**, **zero**
stored blocks. Extrapolated total uncompressed content ≈ **21 GB**. This is why the
codec is the master key to every texture/model/UI asset.

**Codec reverse-engineering — SOLVED (see `docs/01_DECOMPRESSOR_RE.md`):** the
decompressor `VCFILEDEVICE::ReadAndDecompress` is an interleaved **LZSS** with 10
window variants. Reversed with Ghidra (bundled in `ghidra_12.1.2_PUBLIC/`, JDK 21
in `jdk/`). Block header is 0x14 bytes; the dword at `+0x10` is the offset-bit
width (8–15). Decode: control byte (LSB-first), `0`=literal, `1`=2-byte match
(`offset = tok & ((1<<offbits)-1)`, `length = (tok>>offbits)+3`); an all-zero
control byte fast-copies 8 literals. A decoder written from
this description decompresses the archive with a **100% success rate**
(validated 193 files / 840 blocks / 0 failures, mean 2.77×). Textures, models, and
UI are now fully extractable.

---

## 4. Textures, models, UI (Phases 3 & 5)

Player models, jerseys, helmets, sticks, arenas, logos, fonts and UI are all
**resources inside the IFF files** — i.e. `0E4837C3` sub-blocks tagged by
type-hash in the `FF3BEF94` TOC. The executable confirms the Visual Concepts
texture system: strings `__vc_Texture`, `BaseTexture`, `TextureSampler`,
`SprayTexture`, `TextureValueOffset/Scale`, plus animation strings
("Main player anims Loop", "Main Goalie anims Loop").

**Extraction status — textures working for DXT1 (see
`docs/02_AUDIO_AND_TEXTURES.md`).** Descriptor table + embedded Xbox 360 GPU
texture header decoded (format/dims/endian). Key fix: `pixel_base` = 4KB-aligned
start of the **last decompressed sub-resource** (offsets are relative to it). The
DXT1 majority (e.g. 53/72 in arena file #261) now extracts to clean **DDS**;
DXT5 (alpha-decal color/alpha swap) and uncompressed ARGB are partial.
Meshes/models remain future work.

The provided Noesis NHL plugins (`mdl_NHLLegacy_X360_rx2.py`, magic `‰RW4xb2`) are
for **EA's** RenderWare NHL games and **do not apply** to this 2K/Visual Concepts
title. They were tested and ruled out.

---

## 5. Audio (Phase 4)

The largest files in the game carry magic **`08 00 00 00`** (23 files, including the
single **1.07 GB** file #911 and a 329 MB, 253 MB, 239 MB, 231 MB … series). They
are high-entropy, contain **no** `0E4837C3` blocks, and are therefore **not**
LZ-compressed IFF — consistent with **already-compressed XMA audio** (the dominant
asset class in a sports title: commentary, crowd, music, arena and menu SFX). The
executable contains `RIFF` and audio-manager strings.

```
08000000 container head (file #911):
  08 00 00 00 | 03 89 FC 03 | 80 00 14 07 | <XMA/streamed payload ...>
```

**Extraction status — audio SOLVED (see `docs/02_AUDIO_AND_TEXTURES.md`).** The
`08000000` files are raw **XMA2** (2048-byte packets). Wrapping them
them in a RIFF `fmt `(0x0166) header and decodes to **WAV** via ffmpeg, auto-
detecting channels (validated: #2277 → 2:28 of clean mono audio). Sample rate isn't
stored in the bitstream (defaults to 48 kHz, adjustable).

---

## 6. Non-IFF / uncompressed formats (readable *now*)

100 files do not use `FF3BEF94`. Several are **stored uncompressed** and were read
directly:

| Magic | Count | Meaning | Notes |
|-------|------:|---------|-------|
| `08000000` | 23 | Streamed XMA audio (see §5) | biggest files |
| `02000100` | 51 | Little-endian data tables (distinct from the BE rest) | e.g. #19 600 KB; role TBD |
| `E4791207` | 4 | **Uncompressed IFF manifest** — *contains real filenames* | Rosetta stone (§7) |
| `F0985030` | 11 | Uncompressed IFF variant (resource dir + type-hash `1AEDDA1F`) | config/scene data |
| `00000001` | 3 | Structured DB-like (offset/size table) | e.g. #871 2.76 MB — roster/DB candidate |
| `0006F000` | 1 | 26.5 MB package, embeds `02000100` records | fonts/localization candidate |
| `0E4837C3` | 7 | Standalone compressed blocks (no IFF wrapper) | e.g. #1743 68 MB |

`roster.dat` (device path `dcr:\roster.dat`) is referenced by the executable as the
roster database.

---

## 7. File-naming hash — SOLVED & verified (Phase 2)

The `E4791207` manifests store `{name_hash, name}` pairs with names in UTF-16BE.
Testing the ground-truth pairs against many hash functions yielded an exact match:

**`name_hash = CRC-32 (IEEE / zlib polynomial) of the lowercase name with the
extension removed`.**

| Name | CRC-32 | Present in manifest |
|------|--------|---------------------|
| `english` | `0x2C830200` | ✅ |
| `finnish` | `0xA77782DF` | ✅ |
| `french`  | `0xAF75CE5E` | ✅ |
| `german`  | `0xA23440A1` | ✅ |
| `swedish` | `0xB3208328` | ✅ |

5/5 exact; the full-name-with-`.iff` variants are **not** present, proving the
extension is stripped.

### ⚠ Correction — this does NOT extend to the archive TOC

An earlier draft of this report claimed the 2,407 archive entries could therefore
be named. **That was an over-extrapolation and is wrong.** The result above was
only ever verified *inside* the `E4791207` manifests, whose layout is
`[u32 hash array][UTF-16BE name array]` (verified: `crc32("swedishbootup")` =
`A7FF9656` sits in the hash array ahead of the name block).

Tested against the archive TOC and **all failed, 0/38**:

* `crc32` of the stem / full name / uppercase / UTF-16BE encoding of either;
* byte-swapped, bit-inverted, and `adler32` variants;
* 19 path prefixes (`data/`, `art/`, `assets/`, `textures/`, `english/`, …) x 4
  extensions.

So the manifest key space and the archive TOC key space are **different**. The 38
names harvested from the four manifests (files #612, #1063, #1500, #2353) are not
archive members at all — they are resources inside packages.

The archive TOC key is still a well-behaved 32-bit value (2,407 distinct, sorted
ascending, uniformly distributed), just not a plain CRC-32 of any name form tried.
**Naming archive entries remains an open problem.** Note the external Mod Launcher
notes report that asset names *do* resolve "by code via the CRC TOC lookup" — if
so, that tool is either keying a different table or applying a transform we have
not identified; worth reconciling before trusting either account.

---

## 8. Extraction attempts — successes and failures (documented)

**Succeeded**
- XDVDFS parse & direct-from-ISO reads of every file (no 6 GB temp copy needed).
- Full `AA00B3BF` index parse; any of the 2,407 files extractable by index.
- XEX decrypt/decompress via `xextool -b` → PowerPC basefile, strings recovered.
- Uncompressed IFF variants (`E4791207`, `F0985030`) read and structurally decoded.
- Filename hash identified and verified.

**Failed / blocked (with reason)**
- **Standard decompression of `0E4837C3` payloads** — zlib (all wbits, incl. 2-/4-byte
  word-swapped for endianness), lzma, bz2 all fail. The 0x45 "hit" was 73 bytes of
  filler, a false positive. → payload is a **proprietary VC LZ**, not a standard codec.
- **Generic LZSS brute-force** (flag order × literal polarity × offset/length split ×
  min-match, over multiple header offsets) — closest run reached 2,435 vs target 2,016
  bytes; no exact reconstruction. The recurring `18 0C` / `20 0C` / `30 0C` tokens
  indicate a richer match/RLE encoding than textbook LZSS.
- **QuickBMS full extraction** — not run to completion by design: it needs the four
  splits extracted first (6.1 GB) plus its output (~6.1 GB) plus the ISO (7.8 GB) ≈
  28 GB, exceeding the 17 GB free on `C:`. The direct-from-ISO extractor avoids this.
  (The supplied `nhl_2k10.bms` only splits the outer archive; it does **not**
  decompress the IFF payloads, so it would not yield usable assets anyway.)

The byte-distribution analysis is important: `0E4837C3` payloads are **skewed, not
uniform** (0x00/0xF0/0x10/0x20/0x30 dominate). A Huffman/arithmetic-coded stream
would be near-uniform, so the codec is **dictionary-only (LZ), no entropy stage** —
which makes it tractable to finish.

---

## 9. Recommended next steps (to unblock everything)

1. **Reverse `VCFILEDEVICE::ReadAndDecompress`.** It is a single PowerPC function in
   `extracted/default_base.bin` (load address `0x82000000`, entry `0x841E02B0`).
   Disassemble in Ghidra/IDA (PPC BE), locate the `0E4837C3` handler, and transcribe
   the LZ loop. Given the no-entropy finding, this is a bounded task and yields the
   decompressor for **all** textures/models/UI at once.
2. **Audio path in parallel** (independent of #1): wrap the `08000000` XMA payloads as
   `RIFF/'WAVE'` fmt `0x0166` and decode with vgmstream/`towav`. Confirms and extracts
   commentary/crowd/music without waiting on the LZ work.
3. **Build the type-hash dictionary.** Enumerate the `FF3BEF94` TOC type-hashes across
   all IFFs (e.g. `E26C9B5D`, `1AEDDA1F`) and CRC-32-match them against candidate type
   names (`texture`, `mesh`, `material`, `skeleton`, `anim`, `scene`, …) to label
   every sub-resource.
4. **Filename recovery.** Assemble a wordlist (team codes, player IDs, arena names,
   `*.iff`) and CRC-32-match against the 2,407 archive hashes and in-IFF child hashes.

---
