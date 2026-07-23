# 04 — Audio: format and dumping

The 23 `08000000` archive files — including the 1.07 GB commentary bank — are
**raw Xbox 360 XMA2 packet streams**. They are *not* LZ-compressed, so they are
read straight out of the archive without touching the `0E4837C3` codec.

## Container: there isn't one

The file is nothing but XMA2 packets, back to back:

* Exactly **2048 bytes** per packet, no RIFF header, no chunk table, no
  terminator. File size is always a whole multiple of 2048.
* Packet header is one big-endian dword:

```
bits 31..26   frame_count          (6)
bits 25..11   frame_offset_bits    (15)  bit offset of the first frame boundary
bits 10..8    metadata             (3)
bits  7..0    packet_skip          (8)   packets to skip to the next in sequence
```

Every packet in every file parses cleanly against this, which is what confirmed
the identification.

## Dumping to a playable file

XMA2 needs a container before a decoder will touch it. Wrap the packet stream in
a RIFF `WAVE` with a `fmt ` chunk of tag **0x0166** (`WAVE_FORMAT_XMA2`) followed
by a `data` chunk holding the packets verbatim:

```
"RIFF"  <u32 size>  "WAVE"
"fmt "  <u32 chunk_size>
        u16 wFormatTag        = 0x0166
        u16 nChannels         = 1 or 2      (see below)
        u32 nSamplesPerSec    = 48000       (see below)
        u32 nAvgBytesPerSec
        u16 nBlockAlign
        u16 wBitsPerSample    = 16
        ... XMA2WAVEFORMATEX tail ...
"data"  <u32 size>  <packets>
```

All multi-byte fields in the RIFF wrapper are **little-endian** even though the
packets themselves are big-endian — the wrapper is a PC-side container, the
payload is console data. Decode the result with ffmpeg's `xma2` decoder.

### Two fields the bitstream does not carry — both now located

**Sample rate** is absent from XMA2 itself. It lives in the **sound banks** (see
below), at `+0x10` of each record. This game uses four different rates, so
guessing 48 kHz is wrong about as often as it is right.

**Channel count** is not in the bank record either — that field reads 2 for every
entry, including mono ones. Read it from the stream: **byte 7 of the first packet
is `0x03` for mono**. Decoding a mono stream as stereo yields ~0.009 s of noise,
which is a useful failure signature.

## Sound banks — `sfx_arena###.bnk`

Four banks, 288 sounds each, ~44 minutes apiece. A bank is one archive entry laid
out as:

```
[ FF3BEF94 package -> 44-byte record table ][ raw XMA2 payload ]
```

The table decompresses to exactly `44 * n` bytes with no header, and the payload
begins immediately after the compressed package. Sounds are stored **contiguously
in record order**, so a sound's offset is the running sum of the preceding sizes —
there is no offset field. Confirmed arithmetically: for `sfx_arena000.bnk` the
sizes sum to 16,068,608 and the payload region is exactly that long.

```
+0x00  u32  version/type      always 1
+0x04  u32  codec id          always 5 (XMA2)
+0x08  u32  unknown           always 2 -- NOT the channel count
+0x0C  u32  SAMPLE COUNT      duration = this / sample_rate
+0x10  u32  SAMPLE RATE       48000 / 44100 / 22050 / 16000
+0x14  u32  reserved (0)
+0x18  u32  SIZE in bytes     0x800-aligned
+0x1C  u32  reserved (0)
+0x20  u32  flags             0x20
+0x24  u32  unknown           ~2.9x the sample count; not a duration
+0x28  u32  loop/flags
```

Verified end to end: record 0 declares 31,856 samples at 48 kHz = 0.664 s, and
ffmpeg decodes exactly 0.664 s. All six sounds tested match to the millisecond.

> Two fields are easy to misread. The sample count is at **`+0x0C`**, not `+0x24`
> (`+0x24` predicts 1.93 s for that same sound and matches nothing), and `+0x08`
> is not the channel count. Both were got wrong on the first pass here.

Validated on file #2277: 2 minutes 28 seconds of clean mono audio, mean level
−11.8 dB.

## Replacing audio — read this before trying

**No free software encodes XMA2.** ffmpeg *decodes* XMA but cannot produce it.
The only encoder is **`xma2encode.exe`** from the Xbox 360 XDK, which cannot be
redistributed — so a PCM-to-XMA2 workflow requires the user to supply that binary
themselves. With it in hand the pipeline is: source audio → ffmpeg → WAV →
`xma2encode.exe` → XMA2 → check it fits → patch.

Without it, you can still substitute a stream that is *already* XMA2 — another
clip from the game works. Either way the constraints are:

* The replacement must be whole 2048-byte packets.
* It must fit the original file's slot in the archive; pad the remainder rather
  than shortening the file, so no later offset moves.
* Sample rate and channel count are properties of the *source* clip, and nothing
  in the file records what the game expected — a substituted clip with a
  different rate will play at the wrong speed.

See [`05_REPLACEMENT.md`](05_REPLACEMENT.md) for the general write-back rules,
which apply here too.

## Where the audio lives

`08000000` files are listed in [`file_census.csv`](file_census.csv) alongside
every other archive entry. They are conspicuous by size — the largest single file
in the game is a 1.07 GB commentary bank.
