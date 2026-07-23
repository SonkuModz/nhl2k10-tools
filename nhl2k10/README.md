# nhl2k10/ — the library

Modules are flat and import each other by name, so run them from anywhere as
long as this directory is on `sys.path`:

```python
import sys; sys.path.insert(0, "nhl2k10")
from nhl2k_arc import Archive
```

Only `numpy`, `Pillow` and `reversebox` are required, and only for the texture
paths — reading the disc and decompressing work on the standard library alone.

## Reading

| module | |
|---|---|
| `xdvdfs.py` | XDVDFS/XGD2 disc image reader → file list and byte offsets |
| `nhl2k_arc.py` | `AA00B3BF` archive: TOC parsing and **direct-from-ISO** reads |
| `vc_decomp.py` | `0E4837C3` block decoder |
| `vc_extract.py` | file-level extraction (unwrap package, decode every block) |

`nhl2k_arc` maps virtual-stream offsets onto the four disc splits and reads
through them, so no intermediate 6 GB extraction is ever needed.

## Textures

| module | |
|---|---|
| `vc_texture.py` | descriptor tables, group layout, mip rules — the hard part |
| `dds.py` | DDS headers, endian swap, tile/untile |
| `bcdec.py` | BC1–BC5 + uncompressed → RGBA (vectorised) |
| `bcenc.py` | the exact inverse of `bcdec` |

`vc_texture.describe_textures()` resolves every descriptor to an absolute
position; both extraction and replacement go through it so they cannot disagree
about where a texture lives. `mip_consistency()` scores a placement without any
reference data.

## Audio

| module | |
|---|---|
| `xma_extract.py` | wrap raw XMA2 in RIFF and decode via ffmpeg |

## Writing

| module | |
|---|---|
| `patcher.py` | journalled writes — records original bytes, exact undo |
| `vc_compress.py` | `0E4837C3` encoder (least-cost parse) |
| `vc_write.py` | texture and audio replacement, plus the CLI |

Nothing writes a byte without journalling it first. `patcher.revert_all()`
restores the image exactly, and re-writing a region keeps the *first* original so
revert always returns to the pristine disc.

## Verification

| module | |
|---|---|
| `make_verify_manifest.py` | build/check a hashes-only manifest of extracted textures |

## CLI

```bash
python nhl2k10/vc_write.py "your.iso" list-textures 18
```
```bash
python nhl2k10/vc_write.py "your.iso" texture 18 3 art.png
```
```bash
python nhl2k10/vc_write.py "your.iso" list-audio
```
```bash
python nhl2k10/vc_write.py "your.iso" status
```
```bash
python nhl2k10/vc_write.py "your.iso" revert
```
