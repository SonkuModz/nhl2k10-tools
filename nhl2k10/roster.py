#!/usr/bin/env python3
"""
roster.py -- read and edit a `Roster.ROS` save: team colours.

Team data lives in the save file, not on the disc, so this is the one part of the
toolkit that operates on a file the player owns rather than on the ISO.

File layout
-----------
```
+0x08  u32  chunk count
+0x0C  chunk directory, 12 bytes each:  u32 hash, u32 count, u32 data offset
0xB28  data base -- chunk file offset = 0xB28 + directory offset
```

Team colours are in chunk **`0x8489FAF3`** (stride 412) as two blocks of 30:
records 0..29 are the teams, records 30..59 the same teams' arena-LED colours.
Within a record:

```
+0x12B  u8   this record's own index
+0x12C  u8   this record's team id
+0x14C  RGB  PRIMARY colour
+0x14F  RGB  SECONDARY colour
```

The table is **self-describing**: for the 30 team records `+0x12B == +0x12C == k`.
The chunk's directory offset does not point at record 0 (it lands several records
in), so the base is found by probing record-sized shifts for that run of 30 --
never by assuming, because a wrong base writes colours into neighbouring records.

Edits are strictly **in place**: the file size never changes, so every fixed
offset the game holds stays valid. A `.colorbak` copy is written before the first
change. The game caches colours at load, so a change only shows after a restart.

Format credit: the NHL 2K10 Mod Launcher project (`launcher/team_colors.py`,
`launcher/ros_file.py`), used with permission.
"""
import os
import shutil
import struct
import sys

DATA_BASE = 0xB28
TEAM_COLOR_CHUNK = 0x8489FAF3
REC_IDX_OFF = 0x12B
TEAM_ID_OFF = 0x12C
PRIMARY_OFF = 0x14C
SECONDARY_OFF = 0x14F
LED_OFFSET = 30
NTEAMS = 30

# Team id order == plain NHL-alphabetical. Note CGY precedes CAR here.
NHL_CODES = ["ANA", "ATL", "BOS", "BUF", "CGY", "CAR", "CHI", "COL", "CBJ",
             "DAL", "DET", "EDM", "FLA", "LAK", "MIN", "MTL", "NSH", "NJD",
             "NYI", "NYR", "OTT", "PHI", "PHO", "PIT", "SJS", "STL", "TBL",
             "TOR", "VAN", "WSH"]


class RosterError(Exception):
    pass


class Roster(object):
    def __init__(self, path):
        self.path = path
        with open(path, "rb") as f:
            self.data = bytearray(f.read())
        self.chunks = self._parse()
        self._base = None
        self._stride = None

    def _parse(self):
        d = self.data
        if len(d) < 0x0C:
            raise RosterError("file is too small to be a roster save")
        n = struct.unpack_from(">I", d, 8)[0]
        if not 0 < n < 4096:
            raise RosterError("implausible chunk count %d -- not a Roster.ROS?" % n)
        ents = []
        for i in range(n):
            h, cnt, off = struct.unpack_from(">III", d, 0x0C + i * 12)
            ents.append({"index": i, "hash": h, "count": cnt, "off": off})
        order = sorted(range(n), key=lambda i: ents[i]["off"])
        for k, i in enumerate(order):
            nxt = (ents[order[k + 1]]["off"] if k + 1 < len(order)
                   else len(d) - 4 - (DATA_BASE - 4))
            ents[i]["foff"] = DATA_BASE + ents[i]["off"]
            ents[i]["size"] = nxt - ents[i]["off"]
        return ents

    def _color_table(self):
        """-> (base file offset of team record 0, stride)."""
        if self._base is not None:
            return self._base, self._stride
        cands = [c for c in self.chunks if c["hash"] == TEAM_COLOR_CHUNK]
        if not cands:
            raise RosterError("no team-colour chunk (0x%08X) in this save"
                              % TEAM_COLOR_CHUNK)
        d = self.data
        for c in cands:
            stride = c["size"] // c["count"] if c["count"] else 0
            for S in ({stride, 412} if stride else {412}):
                if S < 200:
                    continue
                for shift in range(-16, 17):
                    b = c["foff"] + shift * S
                    if b < 0 or b + (NTEAMS - 1) * S + TEAM_ID_OFF >= len(d):
                        continue
                    if all(d[b + k * S + REC_IDX_OFF] == k and
                           d[b + k * S + TEAM_ID_OFF] == k for k in range(NTEAMS)):
                        self._base, self._stride = b, S
                        return b, S
        raise RosterError(
            "team-colour table not found: no run of 30 records with "
            "+0x12B == +0x12C == k. Refusing to guess a base -- writing to the "
            "wrong one would corrupt neighbouring records.")

    # ---- colours ----
    def team_colors(self, led=False):
        """-> [(code, (r,g,b) primary, (r,g,b) secondary)] for all 30 teams."""
        base, S = self._color_table()
        out = []
        for k, code in enumerate(NHL_CODES):
            rec = base + (k + (LED_OFFSET if led else 0)) * S
            pri = tuple(self.data[rec + PRIMARY_OFF: rec + PRIMARY_OFF + 3])
            sec = tuple(self.data[rec + SECONDARY_OFF: rec + SECONDARY_OFF + 3])
            out.append((code, pri, sec))
        return out

    def set_team_color(self, code, primary=None, secondary=None, led=False):
        """Set one team's colours in place. `primary`/`secondary` are (r,g,b)."""
        code = code.upper()
        if code not in NHL_CODES:
            raise RosterError("unknown team code %r" % code)
        base, S = self._color_table()
        k = NHL_CODES.index(code) + (LED_OFFSET if led else 0)
        rec = base + k * S
        if primary is not None:
            self.data[rec + PRIMARY_OFF: rec + PRIMARY_OFF + 3] = bytes(primary[:3])
        if secondary is not None:
            self.data[rec + SECONDARY_OFF: rec + SECONDARY_OFF + 3] = bytes(secondary[:3])

    def save(self, backup=True):
        """Write back in place. The file size never changes."""
        if backup:
            bak = self.path + ".colorbak"
            if not os.path.exists(bak):
                shutil.copy2(self.path, bak)
        with open(self.path, "r+b") as f:
            f.write(self.data)
        return self.path


def main():
    r = Roster(sys.argv[1])
    led = len(sys.argv) > 2 and sys.argv[2] == "--led"
    base, S = r._color_table()
    print("%d chunks; colour table at 0x%X, stride %d%s"
          % (len(r.chunks), base, S, "  (arena LED)" if led else ""))
    for code, pri, sec in r.team_colors(led=led):
        print("  %-4s  primary #%02X%02X%02X   secondary #%02X%02X%02X"
              % (code, pri[0], pri[1], pri[2], sec[0], sec[1], sec[2]))


if __name__ == "__main__":
    main()
