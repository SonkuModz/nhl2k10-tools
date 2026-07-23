# research/ — the working record, including the dead ends

One-off scripts written while figuring the formats out. They are kept
deliberately: the failures are often more useful than the successes, because they
show which plausible-looking theories were tested and ruled out.

**These are not the library.** They are unpolished, some take positional
arguments with no help text, and several are superseded. For anything
production-shaped use [`../nhl2k10/`](../nhl2k10/).

Most take the disc image as the first argument:

```bash
python research/census.py "your.iso"
```

## Cracking the compression codec

Before the codec was reversed from the executable, it was attacked from the data
side. All of these failed, which is the point:

| script | what it tried |
|---|---|
| `lzss_bruteforce.py` | brute-force the token split (length/offset bit widths) against a known block |
| `vc_bruteforce2.py` | wider parameter sweep, including match-length bases |
| `vc_scan_start.py` | find where the token stream actually begins |
| `vc_startwide.py` | same, over a wider range of header sizes |
| `decompress_probe.py`, `probe2.py` | early structural probes of block payloads |
| `vc_decompress.py` | an earlier decoder attempt, superseded by `nhl2k10/vc_decomp.py` |

The parameters were eventually read straight out of the disassembly instead —
see [`../docs/02_COMPRESSION.md`](../docs/02_COMPRESSION.md). The lesson: the
header field at `+0x10` *selects* one of ten decompressor variants, so no single
fixed parameter set could ever have worked.

## Executable analysis

| script | |
|---|---|
| `disasm.py`, `disasm2.py` | PowerPC disassembly around the codec routines (capstone) |
| `find_func.py`, `find_const.py` | locate functions and constants in the decrypted XEX |
| `xex_strings.py` | mine engine and format strings |

## Archive and format survey

| script | |
|---|---|
| `census.py` | magic-byte census of all 2,407 entries → `docs/file_census.csv` |
| `scan_blocks.py` | size ranking, non-IFF census, compression-ratio statistics |
| `analyze_ff3b.py` | structural field analysis of `FF3BEF94` / `0E4837C3` |
| `examine_meta.py` | dump uncompressed manifests, inspect the giant audio files |
| `peek_headers.py`, `dump_file.py` | quick inspection helpers |
| `id_hash.py` | identified the manifest hash as CRC-32 of the lowercase stem |
| `xma_analyze.py` | XMA2 packet-header validation |

> `id_hash.py` proves the hash **for the `E4791207` manifests**. It does *not*
> extend to the archive TOC — that key is still unidentified, and an earlier
> claim that it was CRC-32 was wrong. See
> [`../docs/01_FILESYSTEM_AND_ARCHIVE.md`](../docs/01_FILESYSTEM_AND_ARCHIVE.md) §7.
