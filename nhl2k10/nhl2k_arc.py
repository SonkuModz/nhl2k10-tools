#!/usr/bin/env python3
"""
nhl2k_arc.py -- Parser / direct-from-ISO extractor for the 2K Sports
                'AA00B3BF' archive container used by NHL 2K10 (Xbox 360).

Container layout (big-endian):
  Header 0x18 bytes:
    u32 magic        = 0xAA00B3BF
    u32 align                       (0x800 = 2048; multiplies sector counts)
    u32 num_archives
    u32 zero
    u32 num_files
    u32 zero
  Archive table  (num_archives * 16):
    u32 size_sectors  (* align = byte size of that split file)
    u32 zero
    u16[4] name       (UTF-16 BE, e.g. "0A")
  File table     (num_files * 16):
    u32 zero          (compression flag - always 0 in 2K10)
    u32 size          (bytes)
    u32 crc           (hash / checksum, unverified)
    u32 offset_sectors(* align = absolute offset into the CONCATENATED
                        archive stream 0A|0B|1A|1B)

The index lives at the start of the first archive ("0A").  File offsets address
a virtual stream that is the concatenation of all split files in table order.
Files may span a split boundary; extraction stitches across.

This module can read straight out of the .iso (via xdvdfs) so no 6 GB
intermediate extraction of 0A/0B/1A/1B is required.
"""
import os
import struct
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from xdvdfs import list_files, file_offset

MAGIC = 0xAA00B3BF


class ArchiveFile:
    """One split file (0A/0B/1A/1B): its logical stream range + ISO location."""
    def __init__(self, name, size, stream_start, iso_offset):
        self.name = name
        self.size = size
        self.stream_start = stream_start      # start offset within virtual stream
        self.iso_offset = iso_offset          # absolute byte offset in the .iso


class FileEntry:
    __slots__ = ("index", "flag", "size", "crc", "offset")
    def __init__(self, index, flag, size, crc, offset):
        self.index = index
        self.flag = flag
        self.size = size
        self.crc = crc
        self.offset = offset                  # absolute offset within virtual stream


class Archive:
    def __init__(self, iso_path):
        self.iso_path = iso_path
        self.align = 0
        self.archives = []                    # list[ArchiveFile]
        self.files = []                       # list[FileEntry]
        self._iso_map = {}                    # split name -> iso byte offset
        self._parse()

    def _parse(self):
        base, entries = list_files(self.iso_path)
        byname = {e.path: e for e in entries}
        # The index is at the head of "0A".
        head = byname["0A"]
        with open(self.iso_path, "rb") as f:
            f.seek(file_offset(base, head))
            hdr = f.read(0x18)
            magic, align, narch, z0, nfiles, z1 = struct.unpack(">IIIIII", hdr)
            if magic != MAGIC:
                raise RuntimeError(f"bad magic 0x{magic:08X}")
            self.align = align
            # Archive table
            atbl = f.read(narch * 16)
        stream_pos = 0
        for i in range(narch):
            size_sectors, _z, n0, n1, n2, n3 = struct.unpack_from(">IIHHHH", atbl, i * 16)
            name = "".join(chr(c) for c in (n0, n1, n2, n3) if c).strip()
            byte_size = size_sectors * align
            iso_ent = byname[name]
            self.archives.append(
                ArchiveFile(name, byte_size, stream_pos, file_offset(base, iso_ent)))
            self._iso_map[name] = file_offset(base, iso_ent)
            stream_pos += byte_size
        self.stream_size = stream_pos
        # File table follows the archive table.
        with open(self.iso_path, "rb") as f:
            f.seek(file_offset(base, head) + 0x18 + narch * 16)
            ftbl = f.read(nfiles * 16)
        for i in range(nfiles):
            flag, size, crc, off_sectors = struct.unpack_from(">IIII", ftbl, i * 16)
            self.files.append(FileEntry(i, flag, size, crc, off_sectors * align))

    def _stream_to_iso(self, stream_off):
        """Map a virtual-stream offset to (ArchiveFile, iso_offset, bytes_left_in_split)."""
        for a in self.archives:
            if a.stream_start <= stream_off < a.stream_start + a.size:
                delta = stream_off - a.stream_start
                return a, a.iso_offset + delta, a.size - delta
        raise ValueError(f"stream offset {stream_off} out of range")

    def read_file(self, entry):
        """Return the raw bytes of one FileEntry, stitching across split boundaries."""
        remaining = entry.size
        pos = entry.offset
        chunks = []
        with open(self.iso_path, "rb") as f:
            while remaining > 0:
                a, iso_off, avail = self._stream_to_iso(pos)
                take = min(remaining, avail)
                f.seek(iso_off)
                chunks.append(f.read(take))
                remaining -= take
                pos += take
        return b"".join(chunks)

    def read_file_head(self, entry, n):
        """Read up to n bytes from the start of a file (no full read)."""
        a, iso_off, avail = self._stream_to_iso(entry.offset)
        n = min(n, entry.size)
        if n <= avail:
            with open(self.iso_path, "rb") as f:
                f.seek(iso_off)
                return f.read(n)
        return self.read_file(entry)[:n]


if __name__ == "__main__":
    arc = Archive(sys.argv[1])
    print(f"align={arc.align} archives={len(arc.archives)} files={len(arc.files)} "
          f"stream_size={arc.stream_size} ({arc.stream_size/1e9:.2f} GB)")
    print("\nArchive splits:")
    for a in arc.archives:
        print(f"  {a.name}: size={a.size} stream_start={a.stream_start} "
              f"iso_off=0x{a.iso_offset:X}")
    print("\nFirst 8 file entries:")
    for e in arc.files[:8]:
        print(f"  #{e.index:<5} flag={e.flag} size={e.size:<10} "
              f"crc=0x{e.crc:08X} offset={e.offset}")
    sizes = [e.size for e in arc.files]
    print(f"\nfile sizes: min={min(sizes)} max={max(sizes)} "
          f"total={sum(sizes)} ({sum(sizes)/1e9:.2f} GB)")
