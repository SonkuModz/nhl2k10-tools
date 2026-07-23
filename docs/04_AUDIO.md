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

### Two fields the bitstream does not carry

**Channel count** is not recoverable from the stream. Probe it: attempt the decode
as stereo, and if the decoder rejects the stream, fall back to mono. Most files in
this game are mono.

**Sample rate** is likewise absent — XMA2 stores no rate in the bitstream. It
lives in sound-bank metadata that has not been located. 48000 Hz is the right
default; a wrong value changes playback speed and pitch but never whether the
stream decodes, so it is safe to experiment.

Validated on file #2277: 2 minutes 28 seconds of clean mono audio, mean level
−11.8 dB.

## Replacing audio — read this before trying

**You cannot convert a WAV to XMA2**, with these findings or any free tool. No
XMA2 encoder exists outside the Xbox 360 XDK; ffmpeg *decodes* XMA but cannot
produce it. Any workflow that starts from PCM is blocked at that step.

What does work is substituting a stream that is *already* XMA2 — another clip
from the game, or output from an XDK-based encoder. The constraints:

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
