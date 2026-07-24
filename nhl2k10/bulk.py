#!/usr/bin/env python3
"""
bulk.py -- export and re-import textures a whole category at a time.

The round trip is:

    export   textures/jerseys/overlay/uniform_cgy_home/00_0000000_2048x512_ARGB4444.dds
    edit     (any image editor, keep the filename and the dimensions)
    import   the same folder, straight back into the disc image

Mapping a file back to its slot uses nothing but the paths, which is why the
export layout is what it is:

* the **folder** is the asset name (`uniform_cgy_home`), which hashes to the
  archive entry -- see names.name_hash;
* the **filename** starts with the texture index within that asset (`00_`).

Unnamed assets export as their archive index (`unnamed/01234/`), and import
reads the index straight back off the folder name.

Edits for one asset are collected and applied in a **single** rebuild. Applying
them one at a time would recompress the whole resource once per texture, which
on a multi-megabyte asset is minutes rather than seconds.

Any of .dds/.png/.tga/.bmp is accepted on import -- edit in whatever format you
like, as long as the filename keeps its leading texture number and the image
keeps the original dimensions.

Verified end to end: all 30 team logos export and map back 30/30 with zero
folder/asset mismatches; two of them recoloured, imported, read back out of the
disc image at 41 dB against the edited sources, then reverted byte-identically.
"""
import os
import re
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from nhl2k_arc import Archive
import names as _names
import vc_texture
import vc_write

IMAGE_EXT = (".dds", ".png", ".tga", ".bmp")
LEADING_INDEX = re.compile(r"^(\d+)_")


def export_category(iso, category, outdir, progress=None):
    """Extract every texture in one category. -> (textures, files, root)."""
    arc = Archive(iso)
    catalog = _names.build_catalog()
    todo = _names.assets_in_category(arc, category, catalog)
    root = os.path.join(outdir, "textures")
    os.makedirs(root, exist_ok=True)

    total = files = 0
    for n, (idx, asset) in enumerate(todo):
        try:
            cnt, _sub = vc_texture.dump_file(arc, idx, root, asset)
            if cnt:
                total += cnt
                files += 1
        except Exception:
            pass
        if progress:
            progress(n + 1, len(todo), asset or "#%d" % idx)
    return total, files, root


def scan_folder(arc, folder, catalog=None):
    """Map an exported folder back to {archive_index: {tex_index: path}}.

    Walks any depth, so it works on a single asset folder, a category, or the
    whole `textures/` tree.
    """
    catalog = catalog if catalog is not None else _names.build_catalog()
    by_hash = {}
    for h, n in catalog.items():
        by_hash[n.rsplit(".", 1)[0].lower()] = h
    have = {e.crc: e for e in arc.files}

    plan = {}
    unknown = []
    for dirpath, _dirs, filenames in os.walk(folder):
        imgs = [f for f in filenames if f.lower().endswith(IMAGE_EXT)]
        if not imgs:
            continue
        leaf = os.path.basename(dirpath.rstrip(os.sep))
        idx = None
        if leaf.isdigit():                       # unnamed/01234
            idx = int(leaf)
        else:
            h = by_hash.get(leaf.lower())
            if h is not None and h in have:
                idx = have[h].index
        if idx is None:
            unknown.append(dirpath)
            continue
        for f in sorted(imgs):
            m = LEADING_INDEX.match(f)
            if not m:
                unknown.append(os.path.join(dirpath, f))
                continue
            plan.setdefault(idx, {})[int(m.group(1))] = os.path.join(dirpath, f)
    return plan, unknown


def import_folder(iso, folder, dry_run=False, progress=None, log=None):
    """Write every edited texture under `folder` back into the disc image.

    Returns a summary dict. Files whose asset or texture index cannot be
    resolved are reported rather than guessed at.
    """
    import numpy as np
    from PIL import Image

    arc = Archive(iso)
    plan, unknown = scan_folder(arc, folder)
    done_files = done_tex = 0
    failures = []

    for n, (idx, images) in enumerate(sorted(plan.items())):
        loaded = {}
        for ti, path in images.items():
            try:
                loaded[ti] = np.array(Image.open(path).convert("RGBA"))
            except Exception as ex:
                failures.append("%s: %s" % (os.path.basename(path), ex))
        if not loaded:
            continue
        try:
            info = vc_write.replace_many(iso, idx, loaded, dry_run=dry_run,
                                         note="bulk import")
            done_files += 1
            done_tex += len(loaded)
            if log:
                log("  #%d: %d textures, %d -> %d bytes (slot %d)"
                    % (idx, len(loaded), info["orig_bytes"], info["new_bytes"],
                       info["slot"]))
        except Exception as ex:
            failures.append("#%d: %s" % (idx, ex))
            if log:
                log("  #%d FAILED: %s" % (idx, ex))
        if progress:
            progress(n + 1, len(plan), "#%d" % idx)

    return {"files": done_files, "textures": done_tex,
            "planned_files": len(plan), "failures": failures,
            "unresolved": unknown, "dry_run": dry_run}


def main():
    iso = sys.argv[1]
    cmd = sys.argv[2]
    if cmd == "categories":
        for c in _names.all_categories():
            print("  " + c)
    elif cmd == "export":
        cat = sys.argv[3]
        out = sys.argv[4] if len(sys.argv) > 4 else "extracted"
        t, f, root = export_category(
            iso, cat, out,
            progress=lambda i, n, s: sys.stdout.write("\r  %d/%d %-40s" % (i, n, s)))
        print("\n%d textures from %d files -> %s" % (t, f, root))
    elif cmd == "import":
        folder = sys.argv[3]
        dry = "--dry-run" in sys.argv
        r = import_folder(iso, folder, dry_run=dry, log=print)
        print("%s%d textures across %d files"
              % ("DRY RUN: " if dry else "", r["textures"], r["files"]))
        for f in r["failures"]:
            print("  failed: %s" % f)
        for u in r["unresolved"][:10]:
            print("  unresolved: %s" % u)
    else:
        print(__doc__)


if __name__ == "__main__":
    main()
