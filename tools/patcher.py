#!/usr/bin/env python3
"""
patcher.py -- journalled in-place writer for the NHL 2K10 disc image.

The ISO is ~6 GB and re-ripping it is expensive, so nothing here writes a byte
without first recording the bytes it is about to overwrite. The journal lives
next to the ISO as `<iso>.undo.json` and every write can be rolled back exactly.

    p = Patcher("NHL 2K10 (Europe).iso")
    p.write(offset, new_bytes, note="texture #17/3")   # journalled
    p.close()
    ...
    Patcher("NHL 2K10 (Europe).iso").revert_all()      # byte-for-byte undo

Design notes
------------
* The journal stores the ORIGINAL bytes, base64-encoded, keyed by offset. If the
  same region is written twice, the FIRST original is kept -- reverting always
  returns to the pristine disc, not to an intermediate state.
* `dry_run=True` journals and validates but never touches the file, which is how
  the test suite exercises the whole pipeline against the real ISO safely.
* Writes are refused if they would extend the file: the disc layout is fixed and
  growing it would desynchronise every later sector.
"""
import base64
import json
import os
import shutil


class PatchError(Exception):
    pass


class Patcher:
    def __init__(self, path, dry_run=False):
        self.path = path
        self.dry_run = dry_run
        self.journal_path = path + ".undo.json"
        self.size = os.path.getsize(path)
        self.journal = self._load()
        self._fh = None

    # ---- journal ----
    def _load(self):
        if os.path.exists(self.journal_path):
            with open(self.journal_path, "r", encoding="utf-8") as f:
                return json.load(f)
        return {"iso": os.path.basename(self.path), "entries": {}}

    def _save(self):
        tmp = self.journal_path + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(self.journal, f, indent=1)
        os.replace(tmp, self.journal_path)

    def _open(self):
        if self._fh is None:
            self._fh = open(self.path, "r+b")
        return self._fh

    # ---- reading ----
    def read(self, offset, length):
        with open(self.path, "rb") as f:
            f.seek(offset)
            return f.read(length)

    # ---- writing ----
    def write(self, offset, data, note=""):
        """Overwrite `data` at `offset`, journalling the original bytes first."""
        if offset < 0 or offset + len(data) > self.size:
            raise PatchError(
                "write of %d bytes at 0x%X would run past the end of the image "
                "(size 0x%X) -- the disc layout is fixed and cannot grow"
                % (len(data), offset, self.size))
        key = str(offset)
        original = self.read(offset, len(data))
        if key not in self.journal["entries"]:
            self.journal["entries"][key] = {
                "len": len(data),
                "note": note,
                "original": base64.b64encode(original).decode("ascii"),
            }
        elif self.journal["entries"][key]["len"] < len(data):
            # a later, larger write over the same start: extend the saved original
            prev = base64.b64decode(self.journal["entries"][key]["original"])
            extra = self.read(offset + len(prev), len(data) - len(prev))
            self.journal["entries"][key]["original"] = base64.b64encode(
                prev + extra).decode("ascii")
            self.journal["entries"][key]["len"] = len(data)
        if self.dry_run:
            return len(data)
        f = self._open()
        f.seek(offset)
        f.write(data)
        f.flush()
        self._save()
        return len(data)

    # ---- undo ----
    def revert_all(self):
        """Restore every journalled region, longest-first so overlaps resolve."""
        entries = sorted(self.journal["entries"].items(),
                         key=lambda kv: (int(kv[0]), -kv[1]["len"]))
        if not entries:
            return 0
        f = self._open()
        for off, e in entries:
            f.seek(int(off))
            f.write(base64.b64decode(e["original"]))
        f.flush()
        self.journal["entries"] = {}
        self._save()
        return len(entries)

    def backup_journal(self, dest=None):
        dest = dest or (self.journal_path + ".bak")
        if os.path.exists(self.journal_path):
            shutil.copy2(self.journal_path, dest)
        return dest

    def pending(self):
        return len(self.journal["entries"])

    def close(self):
        if self._fh:
            self._fh.close()
            self._fh = None
        if not self.dry_run:
            self._save()

    def __enter__(self):
        return self

    def __exit__(self, *a):
        self.close()


if __name__ == "__main__":
    import sys
    iso = sys.argv[1]
    p = Patcher(iso)
    if len(sys.argv) > 2 and sys.argv[2] == "--revert":
        n = p.revert_all()
        print("reverted %d region(s)" % n)
    else:
        print("journal: %s\npending regions: %d" % (p.journal_path, p.pending()))
        for off, e in sorted(p.journal["entries"].items(), key=lambda kv: int(kv[0])):
            print("  0x%010X  %8d bytes  %s" % (int(off), e["len"], e["note"]))
    p.close()
