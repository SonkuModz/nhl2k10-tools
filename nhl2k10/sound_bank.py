#!/usr/bin/env python3
"""
sound_bank.py -- parse `*.bnk` sound banks into individually addressable sounds.

A bank is a single archive entry laid out as:

    [ FF3BEF94 package -> a table of 44-byte records ][ raw XMA2 payload ]

The record table decompresses to exactly `44 * n` bytes with no header, and the
XMA2 payload begins immediately after the compressed package. Sounds are stored
**contiguously in record order**, so a sound's offset is the running sum of the
preceding sizes -- there is no offset field.

Record layout (big-endian u32):

```
+0x00  u32  version/type      always 1 here
+0x04  u32  codec id          always 5 (XMA2)
+0x08  u32  unknown           always 2 -- NOT the channel count (see below)
+0x0C  u32  SAMPLE COUNT      duration = this / sample_rate   [verified]
+0x10  u32  SAMPLE RATE       48000 / 44100 / 22050 / 16000   [verified]
+0x14  u32  reserved (0)
+0x18  u32  SIZE in bytes     0x800-aligned; offset = sum of preceding sizes
+0x1C  u32  reserved (0)
+0x20  u32  flags             0x20
+0x24  u32  unknown           ~2.9x the sample count; not a duration
+0x28  u32  loop/flags
```

This is what supplies the **sample rate**, which XMA2 does not carry in its own
bitstream -- without the bank a stream can only be guessed at 48 kHz. Banks in
this game use four different rates, so guessing is wrong about as often as not.

Two fields are easy to get wrong, and both were, initially:

* **The sample count is at `+0x0C`, not `+0x24`.** Verified by decoding: record 0
  gives 31,856 / 48,000 = 0.664 s and ffmpeg produces exactly 0.664 s. Using
  `+0x24` predicts 1.93 s and matches nothing.
* **`+0x08` is not the channel count.** It reads 2 for every record, but the
  streams are mono and decoding them as stereo yields 0.009 s of noise. Take the
  channel count from the stream instead: byte 7 of the first packet is `0x03` for
  mono.

Record structure credit: the NHL 2K10 Mod Launcher project's
`findings/04_audio_system.md`. The contiguous-layout rule and the field meanings
below were derived and verified here against the retail archive.
"""
import os
import struct
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from nhl2k_arc import Archive
import names as _names
import vc_extract
import vc_texture as _iff
import xma_extract

RECORD = 44
PACKET = 2048


class Sound(object):
    __slots__ = ("index", "channels", "rate", "size", "offset", "samples", "flags")

    def __init__(self, index, channels, rate, size, offset, samples, flags):
        self.index = index
        self.channels = channels
        self.rate = rate
        self.size = size
        self.offset = offset          # into the bank's XMA2 payload
        self.samples = samples
        self.flags = flags

    @property
    def seconds(self):
        return self.samples / float(self.rate) if self.rate else 0.0

    def __repr__(self):
        return ("<Sound %d %dch %dHz %.2fs %d bytes @0x%X>"
                % (self.index, self.channels, self.rate, self.seconds,
                   self.size, self.offset))


def bank_names(limit=64):
    """Candidate bank filenames (the hash is one-way, so we generate and test)."""
    out = []
    for pat in ("sfx_arena%03d.bnk", "ksfx_arena%03d.bnk"):
        out += [pat % i for i in range(limit)]
    return out


def find_banks(arc):
    """-> [(name, entry)] for every bank present in this archive."""
    have = {e.crc: e for e in arc.files}
    found = []
    for n in bank_names():
        h = _names.name_hash(n)
        if h in have:
            found.append((n, have[h]))
    return found


def parse_bank(raw):
    """-> (list[Sound], xma_payload_offset). `raw` is the whole archive entry."""
    blocks = vc_extract.find_blocks(raw)
    if not blocks:
        raise ValueError("not a bank: no compressed record table")
    table, _bounds = _iff.decompress_with_bounds(raw)
    if len(table) % RECORD:
        raise ValueError("record table is %d bytes, not a multiple of %d"
                         % (len(table), RECORD))
    # the XMA2 payload starts after the last compressed block
    last = blocks[-1]
    payload = last[0] + last[2]

    sounds = []
    off = 0
    for i in range(len(table) // RECORD):
        f = struct.unpack_from(">11I", table, i * RECORD)
        s = Sound(index=i, channels=0, rate=f[4], size=f[6],
                  offset=off, samples=f[3], flags=f[8])
        sounds.append(s)
        off += s.size
    return sounds, payload


def read_sound(raw, sound, payload_off):
    """Raw XMA2 packet bytes for one sound."""
    a = payload_off + sound.offset
    return raw[a:a + sound.size]


def stream_channels(data):
    """Channel count read from the stream itself: byte 7 == 0x03 means mono.

    The bank record does not carry this -- its `+0x08` is 2 for every entry,
    including mono ones.
    """
    return 1 if len(data) > 7 and data[7] == 0x03 else 2


def extract_bank(iso, bank_name, outdir, limit=None, rate_override=0):
    """Decode every sound in a bank to WAV, named by index, at its own rate."""
    arc = Archive(iso)
    entry = _names.resolve(arc, bank_name)
    if entry is None:
        raise ValueError("no bank named %s" % bank_name)
    raw = arc.read_file(entry)
    sounds, payload = parse_bank(raw)
    stem = bank_name.rsplit(".", 1)[0]
    d = os.path.join(outdir, "audio", stem)
    os.makedirs(d, exist_ok=True)

    done = 0
    for s in sounds[:limit] if limit else sounds:
        data = read_sound(raw, s, payload)
        if len(data) < PACKET:
            continue
        ch = stream_channels(data)
        wav = os.path.join(d, "%03d_%dHz_%dch_%.2fs.wav"
                           % (s.index, s.rate, ch, s.seconds))
        try:
            xma_extract.extract_to_wav(data, wav,
                                       rate=rate_override or s.rate,
                                       channels=ch)
            done += 1
        except Exception:
            pass
    return done, len(sounds), d


def main():
    iso = sys.argv[1]
    arc = Archive(iso)
    banks = find_banks(arc)

    if len(sys.argv) == 2:
        print("%d sound banks" % len(banks))
        for n, e in banks:
            try:
                sounds, _p = parse_bank(arc.read_file(e))
                rates = sorted({s.rate for s in sounds})
                secs = sum(s.seconds for s in sounds)
                print("  %-22s #%-5d %10d bytes  %4d sounds  %5.1f min  %s Hz"
                      % (n, e.index, e.size, len(sounds), secs / 60.0,
                         "/".join(str(r) for r in rates)))
            except Exception as ex:
                print("  %-22s #%-5d  %s" % (n, e.index, ex))
        return

    name = sys.argv[2]
    if len(sys.argv) > 3 and sys.argv[3] == "--extract":
        out = sys.argv[4] if len(sys.argv) > 4 else "extracted"
        lim = int(sys.argv[5]) if len(sys.argv) > 5 else None
        done, total, d = extract_bank(iso, name, out, limit=lim)
        print("decoded %d/%d sounds -> %s" % (done, total, d))
        return

    entry = _names.resolve(arc, name)
    sounds, payload = parse_bank(arc.read_file(entry))
    print("%s: %d sounds, XMA2 payload at 0x%X" % (name, len(sounds), payload))
    print("  %-4s %-4s %-7s %-8s %-10s %s"
          % ("#", "ch", "rate", "secs", "size", "offset"))
    for s in sounds[:40]:
        print("  %-4d %-4d %-7d %-8.2f %-10d 0x%X"
              % (s.index, s.channels, s.rate, s.seconds, s.size, s.offset))


if __name__ == "__main__":
    main()
