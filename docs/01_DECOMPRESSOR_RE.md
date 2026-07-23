# Visual Concepts `0E4837C3` Decompressor — SOLVED

Status: **fully reverse-engineered and validated.** A pure-Python decoder
(`tools/vc_decomp.py` + `tools/vc_extract.py`) decompresses every block in the
archive. Batch test: **193 files / 840 blocks / 0 failures**, mean ratio 2.77×.

Reversed by disassembling and decompiling the game executable with Ghidra
(`ghidra_12.1.2_PUBLIC/`, JDK 21 bundled in `jdk/`) — imported `default_base.bin`
as `PowerPC:BE:64` at base `0x82000000`; decompiled output in
`docs/ghidra_decompiled.c`.

## Block format (`0E4837C3`)

Header, 0x14 bytes, big-endian:

```
+0x00  u32  magic  = 0x0E4837C3
+0x04  u32  uncompressed_size
+0x08  u32  compressed_size      (from magic to end of block)
+0x0C  u32  type/flags           (7 or 8; not needed to decode)
+0x10  u32  offbits              (8..15) -- window offset-bit width; selects the
                                  decompressor variant (index = offbits - 6)
+0x14  ...  token stream
```

## Algorithm (interleaved LZSS, no entropy stage)

```
offbits from header @0x10;  mask = (1<<offbits)-1
while output < uncompressed_size:
    ctrl = next_byte
    if ctrl == 0:                      # fast path
        copy 8 literal bytes
    else:
        for bit in 0..7 (LSB first):
            if bit == 0:
                copy 1 literal byte
            else:
                tok    = next 2 bytes, big-endian
                offset = tok & mask
                length = (tok >> offbits) + 3
                copy `length` bytes from (output_end - offset)   # offset >= length
```

## Decompressor variants (in the executable)

Ten routines at `0x84148F80 + k*0x480`, `k = 0..9`. Each is the same LZSS with a
different offset/length split (larger `offbits` = larger match window):

| addr | offbits | offset mask | max len |
|------|--------:|------------:|--------:|
| 0x84148F80 | 6 | 0x003F | 1026 |
| 0x84149400 | 7 | 0x007F | 514 |
| 0x84149880 | 8 | 0x00FF | 258 |
| 0x84149D00 | 9 | 0x01FF | 130 |
| 0x8414A180 | 10 | 0x03FF | 66 |
| 0x8414A600 | 11 | 0x07FF | 34 |
| 0x8414AA80 | 12 | 0x0FFF | 18 |
| 0x8414AF00 | 13 | 0x1FFF | 10 |
| 0x8414B380 | 14 | 0x3FFF | 6 |
| 0x8414B800 | 15 | 0x7FFF | 4 |

(`length = (tok >> offbits) + 3`, so max length = `(2^(16-offbits) - 1) + 3`.)
The runtime is a streaming decoder with a refill callback and a sliding output
window; for standalone extraction the whole block is in memory, so the token
stream is simply read in order and matches reference the growing output — exactly
the loop above.

## Files that use it

The 2,307 `FF3BEF94` packages contain one or more `0E4837C3` sub-blocks (walk them
in order, decompress, concatenate — see `vc_extract.py`). The 7 standalone
`0E4837C3` files decode directly. The large `08000000` files are **XMA audio**,
not this codec.

## Tooling

- `tools/vc_decomp.py` — the block decoder (`decompress_block`, `decompress_at`).
- `tools/vc_extract.py` — file-level extractor (unwrap package, decode all blocks);
  `--batch N` validation mode.
- `tools/GhidraDecompileTargets.java` + `run_ghidra_headless.bat` — reproduce the
  decompilation headlessly.
