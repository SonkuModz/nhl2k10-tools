#!/usr/bin/env python3
"""
names.py -- resolve human-readable asset names to archive entries.

The archive TOC is keyed by **CRC-32 of the UPPERCASED asset name, extension
included**:

    key = zlib.crc32(name.upper().encode("ascii")) & 0xFFFFFFFF

The runtime uppercases the VFS path before hashing, which is why lowercase never
matched. Credit: this came from the NHL 2K10 Mod Launcher project's findings; it
is reproduced here because it is verified against this archive (36/38 names
harvested from the in-game manifests resolve, and all 30 team codes resolve
across the asset templates below).

The table is sorted ascending by key so the game can binary-search it. Since the
hash is one-way, names are recovered by *generating candidates and testing* --
hence the templates here.
"""
import itertools
import os
import string
import sys
import zlib

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from nhl2k_arc import Archive


def name_hash(name):
    """The archive TOC key for an asset name (extension included)."""
    return zlib.crc32(name.upper().encode("ascii")) & 0xFFFFFFFF


# Verified by brute-forcing all 17,576 three-letter codes against the templates
# below: every one of these resolves 4+ assets. 30 NHL clubs plus three extras
# (`int` international, `als` all-stars, `pnd` — unidentified).
TEAM_CODES = [
    "ana", "atl", "bos", "buf", "car", "cbj", "cgy", "chi", "col", "dal",
    "det", "edm", "fla", "lak", "min", "mtl", "njd", "nsh", "nyi", "nyr",
    "ott", "phi", "pho", "pit", "sjs", "stl", "tbl", "tor", "van", "wsh",
    "int", "als", "pnd",
]

# {template: description}. `{c}` is a team code.
TEAM_ASSETS = {
    "logo_{c}.iff": "team logo (uncompressed IFF)",
    "uniform_{c}_home.iff": "jersey overlay, home",
    "uniform_{c}_away.iff": "jersey overlay, away",
    "uniform_{c}_alt.iff": "jersey overlay, alternate",
    "uniform_base_{c}_home.iff": "jersey base, home",
    "uniform_base_{c}_away.iff": "jersey base, away",
    "uniform_base_{c}_alt.iff": "jersey base, alternate",
    "rink_{c}.iff": "rink ice, regular season",
    "ice_{c}_playoffs.iff": "rink ice, playoffs",
    "ice_{c}_finals.iff": "rink ice, finals",
    "led_{c}.iff": "arena LED board",
    "zamboni_{c}.iff": "zamboni",
    "zamboni_team_{c}.iff": "zamboni, team livery",
    "arena_{c}.iff": "arena sound bank (audio, not a texture)",
}

# Assets with no team code.
GLOBAL_ASSETS = [
    "global.iff", "overlay_static.iff", "frontend.iff", "default.xex",
    "english.iff", "french.iff", "german.iff", "swedish.iff", "finnish.iff",
    "englishbootup.iff", "frenchbootup.iff", "germanbootup.iff",
    "swedishbootup.iff", "finnishbootup.iff",
]


def build_catalog(codes=None, templates=None):
    """-> {hash: name} for every candidate name we know how to generate."""
    codes = codes or TEAM_CODES
    templates = templates or TEAM_ASSETS
    out = {}
    for name in GLOBAL_ASSETS:
        out[name_hash(name)] = name
    for code, tpl in itertools.product(codes, templates):
        n = tpl.format(c=code)
        out[name_hash(n)] = n
    return out


# Asset name prefix -> output folder. First match wins, so order matters
# (`uniform_base_` must be tested before `uniform_`).
CATEGORIES = [
    ("uniform_base_", "jerseys/base"),
    ("uniform_", "jerseys/overlay"),
    ("logo_", "logos"),
    ("rink_", "rinks"),
    ("ice_", "rinks/postseason"),
    ("arena_", "arenas"),
    ("led_", "arenas/led"),
    ("zamboni_team_", "zamboni"),
    ("zamboni_", "zamboni"),
]


def category(name):
    """Folder an asset's textures belong in."""
    if not name:
        return "unnamed"
    for prefix, folder in CATEGORIES:
        if name.startswith(prefix):
            return folder
    return "other"


def output_dir(root, index, name):
    """Where one archive file's textures should be written.

    Dumping thousands of .dds into a single folder is unusable, so lay them out
    by what the asset actually is:

        textures/jerseys/overlay/uniform_cgy_home/...
        textures/arenas/arena_nyr/...
        textures/unnamed/01234/...

    Unnamed entries fall back to their archive index, which is still stable and
    still groups a file's textures together.
    """
    stem = (name[:-4] if name.lower().endswith(".iff") else name) if name else ""
    leaf = stem or "%05d" % index
    return os.path.join(root, category(name), leaf)


def resolve(arc, name):
    """Find the archive entry for `name`, or None."""
    h = name_hash(name)
    for e in arc.files:
        if e.crc == h:
            return e
    return None


def discover_codes(arc, templates=None, min_hits=4):
    """Brute-force every 3-letter code against the templates.

    This is how TEAM_CODES was derived; re-run it to check another region or to
    pick up codes a different release uses.
    """
    templates = list(templates or TEAM_ASSETS)
    have = {e.crc for e in arc.files}
    found = {}
    for a, b, c in itertools.product(string.ascii_lowercase, repeat=3):
        code = a + b + c
        n = sum(1 for t in templates if name_hash(t.format(c=code)) in have)
        if n >= min_hits:
            found[code] = n
    return found


def main():
    iso = sys.argv[1]
    arc = Archive(iso)
    have = {e.crc: e for e in arc.files}
    catalog = build_catalog()
    named = {h: n for h, n in catalog.items() if h in have}

    print("archive entries : %d" % len(arc.files))
    print("candidate names : %d" % len(catalog))
    print("resolved        : %d (%.1f%% of the archive)"
          % (len(named), 100.0 * len(named) / len(arc.files)))

    if len(sys.argv) > 2 and sys.argv[2] == "--list":
        for h, n in sorted(named.items(), key=lambda kv: kv[1]):
            e = have[h]
            print("  #%-5d %10d bytes  %08X  %s" % (e.index, e.size, h, n))


if __name__ == "__main__":
    main()


def all_categories():
    """Every folder name output_dir() can produce, for a category picker."""
    cats = sorted({folder for _prefix, folder in CATEGORIES})
    return cats + ["other", "unnamed"]


def assets_in_category(arc, cat, catalog=None):
    """-> [(archive_index, asset_name)] for one category.

    'unnamed' collects every entry we cannot name; those still extract, grouped
    by archive index.
    """
    catalog = catalog if catalog is not None else build_catalog()
    out = []
    for e in arc.files:
        name = catalog.get(e.crc, "")
        if category(name) == cat:
            out.append((e.index, name))
    return out
