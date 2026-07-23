#!/usr/bin/env python3
"""
xma_extract.py -- Wrap NHL 2K10 raw XMA2 (08000000) data in a RIFF/XMA2 header
and (optionally) decode to WAV with ffmpeg.

The 08000000 archive files are raw XMA2 packet streams (2048-byte packets, no
container). ffmpeg's xma2 decoder reads a WAVEFORMATEX with wFormatTag=0x0166
plus the XMA2WAVEFORMAT tail. We build that here.

Usage:
  python xma_extract.py <iso> <index> [--ch N] [--rate HZ] [--streams N]
                        [--bytes M] [--wav OUT.wav] [--riff OUT.xma]
"""
import os, sys, struct, subprocess, argparse
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from nhl2k_arc import Archive

PACKET = 2048

def build_riff(xma_data, channels, rate, num_streams, bytes_per_block):
    n = len(xma_data)
    block_count = max(1, (n + bytes_per_block - 1) // bytes_per_block)
    # rough sample estimate (not needed for decode; keep plausible)
    samples = n // PACKET * 512
    # WAVEFORMATEX (18) + XMA2 tail (34) = 52-byte fmt
    fmt = struct.pack("<HHIIHHH",
                      0x0166,            # wFormatTag XMA2
                      channels,          # nChannels
                      rate,              # nSamplesPerSec
                      rate * channels * 2,  # nAvgBytesPerSec (approx)
                      PACKET,            # nBlockAlign
                      16,                # wBitsPerSample
                      34)                # cbSize
    fmt += struct.pack("<HIIIIIIIBBH",
                       num_streams,      # NumStreams
                       0,                # ChannelMask
                       samples,          # SamplesEncoded
                       bytes_per_block,  # BytesPerBlock
                       0,                # PlayBegin
                       samples,          # PlayLength
                       0,                # LoopBegin
                       0,                # LoopLength
                       0,                # LoopCount
                       4,                # EncoderVersion
                       block_count)      # BlockCount
    data_chunk = b"data" + struct.pack("<I", n) + xma_data
    fmt_chunk = b"fmt " + struct.pack("<I", len(fmt)) + fmt
    riff_body = b"WAVE" + fmt_chunk + data_chunk
    return b"RIFF" + struct.pack("<I", len(riff_body)) + riff_body

def extract_to_wav(data, out_wav, rate=48000, channels=0):
    """High-level: raw XMA2 bytes -> WAV via ffmpeg. channels=0 auto-detects.
    Returns (ok, channels, message)."""
    import tempfile
    if len(data) < PACKET:
        return False, 0, "too small / empty"
    ch = channels or detect_channels(data, rate)
    riff = build_riff(data, ch, rate, max(1, (ch + 1) // 2), 0x10000)
    with tempfile.NamedTemporaryFile(suffix=".xma", delete=False) as tf:
        tf.write(riff); tmp = tf.name
    try:
        r = subprocess.run(["ffmpeg", "-y", "-hide_banner", "-v", "error",
                            "-i", tmp, out_wav], capture_output=True, text=True)
    finally:
        os.unlink(tmp)
    ok = r.returncode == 0 and os.path.exists(out_wav)
    return ok, ch, (r.stderr.strip()[-200:] if not ok else "ok")

def detect_channels(data, rate=48000):
    """Probe-decode a small slice at ch=2 then ch=1; return the first that works."""
    probe = data[:PACKET * 64]
    import tempfile
    for ch in (2, 1):
        riff = build_riff(probe, ch, rate, max(1, (ch + 1) // 2), 0x10000)
        with tempfile.NamedTemporaryFile(suffix=".xma", delete=False) as tf:
            tf.write(riff); tmp = tf.name
        r = subprocess.run(["ffmpeg", "-y", "-hide_banner", "-v", "error",
                            "-i", tmp, "-f", "null", "-"],
                           capture_output=True, text=True)
        os.unlink(tmp)
        if r.returncode == 0 and "error" not in r.stderr.lower():
            return ch
    return 1

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("iso"); ap.add_argument("index", type=int)
    ap.add_argument("--ch", type=int, default=2)
    ap.add_argument("--rate", type=int, default=48000)
    ap.add_argument("--streams", type=int, default=0, help="0=auto ceil(ch/2)")
    ap.add_argument("--bytes", type=int, default=0x10000)
    ap.add_argument("--limit", type=int, default=0, help="only first N bytes (0=all)")
    ap.add_argument("--wav", default=None)
    ap.add_argument("--riff", default=None)
    a = ap.parse_args()

    arc = Archive(a.iso); e = arc.files[a.index]
    data = arc.read_file(e)
    if a.limit:
        data = data[:a.limit - (a.limit % PACKET)]
    if a.ch == 0:                       # auto-detect channels via a probe decode
        a.ch = detect_channels(data)
        print("auto-detected channels =", a.ch)
    streams = a.streams or max(1, (a.ch + 1) // 2)
    riff = build_riff(data, a.ch, a.rate, streams, a.bytes)
    riff_path = a.riff or f"xma_{a.index}.xma"
    open(riff_path, "wb").write(riff)
    print(f"wrote {riff_path} ({len(riff)} bytes) ch={a.ch} rate={a.rate} "
          f"streams={streams} packets={len(data)//PACKET}")
    if a.wav:
        cmd = ["ffmpeg", "-y", "-hide_banner", "-i", riff_path, a.wav]
        r = subprocess.run(cmd, capture_output=True, text=True)
        print("--- ffmpeg stderr (tail) ---")
        print("\n".join(r.stderr.splitlines()[-15:]))
        print("ffmpeg exit:", r.returncode,
              "->", a.wav, os.path.getsize(a.wav) if os.path.exists(a.wav) else "n/a")

if __name__ == "__main__":
    main()
