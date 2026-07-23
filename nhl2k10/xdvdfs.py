#!/usr/bin/env python3
"""
xdvdfs.py -- Minimal read-only parser for Xbox / Xbox 360 disc images (XDVDFS).

Locates the "MICROSOFT*XBOX*MEDIA" volume descriptor, walks the root directory
AVL tree, and returns a flat list of (path, lba, size) entries so callers can
read any file directly out of the .iso without a full extraction.

Designed to be a reusable building block for the NHL 2K10 modding toolkit.

Reference: XDVDFS volume descriptor lives at partition_base + 32*2048.
Directory entries are 4-byte-aligned nodes in an AVL tree:
    u16 left_sub_tree   (in 4-byte words, 0 = none, 0xFFFF = end)
    u16 right_sub_tree
    u32 start_sector    (LBA relative to partition base)
    u32 file_size
    u8  attributes
    u8  name_length
    char name[name_length]
All little-endian.
"""
import struct
import sys

SECTOR = 2048
MAGIC = b"MICROSOFT*XBOX*MEDIA"

# Known partition base offsets for various Xbox 360 disc image layouts.
# The parser probes each until it finds the magic string.
KNOWN_BASES = [
    0x00000000,   # GDF / redump game partition at start, or raw XDVDFS
    0x0000FD90,   # rare
    0x02080000,   # XGD3 (video partition trimmed)
    0x0FD90000,   # XGD2 games (0xFDA0000 * ... ) common
    0x0FDA0000,   # XGD2 partition base
    0x18300000,   # XGD3 full redump (video + game)
    0x1FB20000,
]

ATTR_DIRECTORY = 0x10


class Entry:
    __slots__ = ("path", "lba", "size", "attrs", "is_dir")

    def __init__(self, path, lba, size, attrs):
        self.path = path
        self.lba = lba
        self.size = size
        self.attrs = attrs
        self.is_dir = bool(attrs & ATTR_DIRECTORY)


def find_partition_base(f):
    """Return the byte offset of the partition base (where LBA 0 maps to)."""
    for base in KNOWN_BASES:
        f.seek(base + 32 * SECTOR)
        if f.read(len(MAGIC)) == MAGIC:
            return base
    # Fallback: brute-force scan on sector boundaries (slow-ish but bounded).
    f.seek(0, 2)
    size = f.tell()
    step = SECTOR
    pos = 0
    while pos + 32 * SECTOR + len(MAGIC) <= size:
        f.seek(pos + 32 * SECTOR)
        if f.read(len(MAGIC)) == MAGIC:
            return pos
        pos += step
        # Only scan the first 512 MB for the descriptor; bases are near the front.
        if pos > 512 * 1024 * 1024:
            break
    raise RuntimeError("XDVDFS volume descriptor not found")


def read_volume(f, base):
    """Read root directory sector + size from the volume descriptor."""
    f.seek(base + 32 * SECTOR)
    data = f.read(SECTOR)
    if data[:len(MAGIC)] != MAGIC:
        raise RuntimeError("bad volume descriptor")
    root_sector, root_size = struct.unpack_from("<II", data, 0x14)
    return root_sector, root_size


def _walk_dir_table(f, base, dir_sector, dir_size, prefix, out):
    """Read a whole directory table into memory and walk its AVL nodes."""
    f.seek(base + dir_sector * SECTOR)
    table = f.read(dir_size)
    subdirs = []

    def walk(off):
        # Skip padding (0xFF filler between/inside sectors).
        if off + 14 > len(table):
            return
        left, right, start, size, attrs, namelen = struct.unpack_from(
            "<HHIIBB", table, off)
        if left == 0xFFFF:
            return
        if left:
            walk(left * 4)
        name = table[off + 14: off + 14 + namelen].decode("latin-1")
        full = prefix + "/" + name if prefix else name
        e = Entry(full, start, size, attrs)
        out.append(e)
        if e.is_dir and size > 0:
            subdirs.append((start, size, full))
        if right:
            walk(right * 4)

    walk(0)
    for s, sz, pfx in subdirs:
        _walk_dir_table(f, base, s, sz, pfx, out)


def list_files(path):
    with open(path, "rb") as f:
        base = find_partition_base(f)
        root_sector, root_size = read_volume(f, base)
        out = []
        _walk_dir_table(f, base, root_sector, root_size, "", out)
    return base, out


def file_offset(base, entry):
    """Absolute byte offset of a file's data within the image."""
    return base + entry.lba * SECTOR


def read_file_bytes(path, base, entry, length=None, seek=0):
    with open(path, "rb") as f:
        f.seek(file_offset(base, entry) + seek)
        return f.read(entry.size - seek if length is None else length)


if __name__ == "__main__":
    iso = sys.argv[1]
    base, entries = list_files(iso)
    print(f"# partition base: 0x{base:08X}")
    print(f"# {len(entries)} entries")
    for e in sorted(entries, key=lambda x: x.path.lower()):
        kind = "DIR " if e.is_dir else "FILE"
        off = file_offset(base, e)
        print(f"{kind} {e.size:>12} lba={e.lba:<8} off=0x{off:010X}  {e.path}")
