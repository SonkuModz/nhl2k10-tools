# 06 — External notes, cross-checked

Source: texture-modding notes from a separate **Mod Launcher** project. This file
records what those notes claim, what was independently confirmed against this
game's own data, and what remains unverified. Where the two accounts disagree,
both positions are stated rather than reconciled by assumption.

## Confirmed against our own data

* **Record layout.** Their `w@+0x60 / h@+0x62 / VRAM offset@+0x6C / mip0@+0x70 /
  mip tail@+0x74` are exactly our `+0x08 / +0x0A / +0x14 / +0x18 / +0x1C` shifted
  by 0x58. Two independent derivations agreeing is strong evidence the layout is
  right. Records are 0xE0 bytes; a record starts 0x58 before what we parse.
* **`count @0x20`, `ptr @0x24`**, table at `ptr + 0x7B` — verified exactly on four
  files (see [`02_COMPRESSION.md`](02_COMPRESSION.md)).
* **Format codes.** Their bytes-per-block table (DXT1 = 8, DXT2_3/DXT4_5 = 16,
  565/1555/4444 = 2, 8888 = 4) matches ours.
* **Byte order.** DXT colour endpoints big-endian, indices little-endian; 16-bit
  texels BE; 8888 stored A R G B — matches our `endian_swap16` + ARGB→BGRA path.
* **DXT4_5 block order**: alpha block bytes 0–7, colour block 8–15. Matches.

## Claimed, NOT yet verified here

* **Asset name templates** keyed by 3-letter team code — **verified** (`logo_{code}.iff`,
  `uniform_base_{code}_{home|away|alt}.iff` = 565 1024x1024,
  `uniform_{code}_{home|away|alt}.iff` = 4444 2048x512, `rink_{code}.iff`,
  `ice_{code}_{playoffs|finals}.iff`, `led_{code}.iff`, `zamboni_{code}.iff`).
  Phoenix is `pho`. `arena_{code}.iff` is an audio bank, not a texture.

  > **CONFIRMED — and they were right where we were wrong.** These templates all
  > resolve, once the hash is `CRC-32(name.upper())` with the extension included.
  > Our earlier "zero hits" result came from a broken test helper that
  > force-lowercased its input, so the uppercase case was never actually tried.
  > All 30 team codes resolve; 443 archive entries are now named. See
  > [`01_FILESYSTEM_AND_ARCHIVE.md`](01_FILESYSTEM_AND_ARCHIVE.md) §7.
  >
  > One correction back: `arena_{code}.iff` is **not** an audio bank. All three
  > checked are `FF3BEF94` texture packages of 5-7 MB — `arena_nyr.iff` is the
  > file where all 91 textures verified byte-exact. The audio banks are
  > `sfx_arena###.bnk` / `ksfx_arena###.bnk`.

* `logo_{code}.iff` is **uncompressed** — a raw IFF with no `0E4837C3` blocks.
  Our extractor should handle that path explicitly.
* A single fetch constant at `+0x94` for small assets (we always find arrays).
* Type bits `(dword0 & 3) == 2` marks a 2D texture — a cheap extra validity test.

## Write-back rules (their hard-won constraints — treat as authoritative)

* **A packed multi-blob resource cannot grow.** The engine sizes the decompress /
  VRAM buffer from the *original* resource; a larger decompressed payload
  overflows and crashes Xenia. Keep decompressed size **==** original.
* **Safe growth = append + redirect.** Append the new mip chain to the end of the
  texture blob, then repoint that record's stored offset (`+0x6C`, i.e. our
  `+0x14`) at it and patch `+0x70`/`+0x74` (our `+0x18`/`+0x1C`). Re-encode both
  blobs, relocate the resource to the end of split `1B`, repoint the TOC. Old
  slots become dead space; other textures are untouched.
  Preconditions: the texture blob is **last**, the record is findable, dims match.
* **Naive relocate corrupts** — moving a resource without redirecting the records
  leaves offsets pointing at the wrong bytes. Do not do it.
* `global.iff` (one ~67 MB blob, 427 sub-textures) and `overlay_static` are
  **in-place only**; `global.iff` does not store its sub-texture offsets in the
  file at all (the loader repacks them), so they must be captured at runtime.
* **Resolution increases are R&D** — the engine derives the VRAM allocation and
  the Xenos *packed* mip-tail layout from the fetch-constant dims
  (`Gpu_CalcSurfaceLayout` @0x841C1000, `Gpu_SetTextureHeader` @0x84212A58).

## Alpha — matters for quality

Most DXT4_5 textures (UI/HUD/overlay) store **straight**, not premultiplied,
alpha; only some cut-out logos are premultiplied. Premultiplying straight art
darkens every partial-alpha pixel — the classic "replacement looks washed out"
symptom (~32–42 dB round-trip when handled correctly vs ~6–24 dB when not).
Detect per texture via the invariant *stored RGB ≤ alpha*.

## Relevance to our open problems

`global.iff`-style assets whose offsets are not stored in the file are a plausible
explanation for the residual failures in **#507** (7 bad of 96): if some records'
offsets are filled in by the loader, no static rule can place them, and runtime
capture (Xenia `.xtr` GPU traces, `trace_gpu_stream=true`) is the way in.
