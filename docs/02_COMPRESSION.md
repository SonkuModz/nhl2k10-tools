# 02 — The `0E4837C3` compression format

**Fully reverse-engineered.** A decoder written from this specification
decompresses every block in the archive: **193 files / 840 blocks / 0 failures**,
mean ratio 2.77x.

Reversed by disassembling the game executable in Ghidra. Two details matter if you
want to repeat that: extract the XEX basefile first (it ships LZX-compressed and
encrypted), and import it as **`PowerPC:BE:64`** at base `0x82000000`. Imported as
BE:32 the 64-bit `ldx`/`std` instructions decode as bad data and the decompiler
silently truncates its output mid-function — which looks like a failed decompile
rather than a wrong setting.

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

The 2,307 `FF3BEF94` packages contain one or more `0E4837C3` sub-blocks: walk them
in order, decompress each, and concatenate. The 7 standalone
`0E4837C3` files decode directly. The large `08000000` files are **XMA audio**,
not this codec.

## Compressing (for replacement)

The format has no entropy stage, so an encoder is straightforward — but a *naive*
one is not good enough. A replaced resource normally has to fit the slot it came
from, and greedy matching produced output about **1.8% larger** than the game's
own cooker, which is enough to fail that check.

Use a **least-cost parse**. Each symbol costs one control bit plus its payload: a
literal is 8 more bits, a match 16. The cheapest encoding is therefore a shortest
path over positions, computed backwards:

```
cost[n] = 0
for i = n-1 down to 0:
    best = cost[i+1] + 9                       # emit a literal
    for L in 3 .. longest_match_at(i):
        best = min(best, cost[i+L] + 17)       # emit a match
    cost[i] = best
```

This is cheap precisely because the token packs length into `16 - offbits` bits,
so the longest possible match is short — 18 bytes at `offbits=12`. With it, the
encoder beats the original cooker on every block tested:

| offbits | original | least-cost parse | |
|--------:|---------:|-----------------:|---|
| 12 | 102,024 | 101,741 | −0.3% |
| 13 | 232,581 | 230,550 | −0.9% |
| 10 | 4,918 | 4,669 | −5.1% |
| 9 | 324 | 292 | −9.9% |

Two practical notes:

* **Match offsets may exceed the match length.** The decoder copies byte by byte,
  so overlapping matches are legal and are how runs are encoded. Do not reject them.
* **Round-trip every block through your own decoder before writing it anywhere.**
  A malformed token stream is not detectable by inspection, and the failure only
  shows up on the console.

## Reproducing the decompilation

Import the XEX basefile as `PowerPC:BE:64` at `0x82000000`, disassemble at each of
the ten addresses in the table above, and decompile. Ghidra's headless analyzer
(`analyzeHeadless`) automates this; note that Ghidra 12 removed Jython, so
post-scripts must be Java rather than Python.
