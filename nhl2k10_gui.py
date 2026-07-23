#!/usr/bin/env python3
"""
nhl2k10_gui.py -- NHL 2K10 modding toolkit.

Tabbed UI over the `nhl2k10/` library: browse the archive by asset name, extract
and replace textures and audio, and edit team colours in a roster save.

The tab layout mirrors the NHL 2K10 Mod Launcher so the two are navigable side by
side. Tabs needing a capability this toolkit does not have (attaching to a running
game's memory) say so plainly rather than showing controls that do nothing.
"""
import os
import queue
import shutil
import sys
import threading
import traceback

import tkinter as tk
from tkinter import ttk, filedialog, messagebox

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, "nhl2k10"))

from nhl2k_arc import Archive
import vc_texture
import xma_extract

try:
    import names as asset_names
except Exception:
    asset_names = None
try:
    import sound_bank
except Exception:
    sound_bank = None
try:
    import roster as roster_mod
except Exception:
    roster_mod = None
try:
    import vc_write
    HAS_WRITE = True
except Exception:
    HAS_WRITE = False

HAS_FFMPEG = shutil.which("ffmpeg") is not None

IFF_MAGIC = bytes.fromhex("ff3bef94")
BLOCK_MAGIC = bytes.fromhex("0e4837c3")
XMA_MAGIC = bytes.fromhex("08000000")
MANIFEST_MAGIC = bytes.fromhex("e4791207")
UNCOMPRESSED_MAGIC = bytes.fromhex("f0985030")


def find_iso():
    """Locate the user's own disc image. No game data ships with this tool."""
    env = os.environ.get("NHL2K10_ISO")
    if env and os.path.isfile(env):
        return env
    try:
        isos = [os.path.join(HERE, f) for f in os.listdir(HERE)
                if f.lower().endswith(".iso")]
        isos = [p for p in isos if os.path.isfile(p)]
        if isos:
            return max(isos, key=os.path.getsize)
    except OSError:
        pass
    return ""


def classify(head):
    h = head[:4]
    if h == IFF_MAGIC:
        return "IFF (compressed)"
    if h == BLOCK_MAGIC:
        return "IFF block"
    if h == XMA_MAGIC:
        return "XMA2 audio"
    if h == MANIFEST_MAGIC:
        return "manifest"
    if h == UNCOMPRESSED_MAGIC:
        return "IFF (uncompressed)"
    return "other"


def _choose(parent, title, prompt, choices):
    """Modal list picker -> chosen index, or None."""
    win = tk.Toplevel(parent)
    win.title(title)
    win.transient(parent)
    win.grab_set()
    ttk.Label(win, text=prompt, padding=8).pack(anchor="w")
    lb = tk.Listbox(win, width=52, height=min(18, max(3, len(choices))))
    for c in choices:
        lb.insert("end", c)
    lb.selection_set(0)
    lb.pack(fill="both", expand=True, padx=8)
    res = {"i": None}

    def ok(*_a):
        s = lb.curselection()
        res["i"] = s[0] if s else None
        win.destroy()

    bf = ttk.Frame(win, padding=8)
    bf.pack(fill="x")
    ttk.Button(bf, text="OK", command=ok).pack(side="right")
    ttk.Button(bf, text="Cancel", command=win.destroy).pack(side="right", padx=6)
    lb.bind("<Double-Button-1>", ok)
    win.wait_window()
    return res["i"]


class App(object):
    def __init__(self, root):
        self.root = root
        root.title("NHL 2K10 Modding Toolkit")
        root.geometry("1180x780")

        self.arc = None
        self.rows = []
        self.banks = []
        self.roster = None
        self.names = asset_names.build_catalog() if asset_names else {}
        self.q = queue.Queue()
        self.worker = None

        self.iso_var = tk.StringVar(value=find_iso())
        self.out_var = tk.StringVar(value=os.path.join(HERE, "extracted"))
        self.ros_var = tk.StringVar()
        self.xma_enc_var = tk.StringVar(value=shutil.which("xma2encode") or "")

        self._build()
        self.root.after(100, self._poll)
        if self.iso_var.get():
            self._load()

    # ================= layout =================
    def _build(self):
        top = ttk.Frame(self.root, padding=(8, 6))
        top.pack(fill="x")
        ttk.Label(top, text="Disc image:").pack(side="left")
        ttk.Entry(top, textvariable=self.iso_var).pack(side="left", fill="x",
                                                       expand=True, padx=6)
        ttk.Button(top, text="Browse", command=self._browse_iso).pack(side="left")
        ttk.Button(top, text="Load", command=self._load).pack(side="left", padx=4)
        self.status_var = tk.StringVar(value="No disc image loaded")
        ttk.Label(top, textvariable=self.status_var).pack(side="right")

        pane = ttk.PanedWindow(self.root, orient="vertical")
        pane.pack(fill="both", expand=True, padx=8, pady=4)

        nbf = ttk.Frame(pane)
        self.nb = ttk.Notebook(nbf)
        self.nb.pack(fill="both", expand=True)
        pane.add(nbf, weight=4)

        self.tab_audio = ttk.Frame(self.nb, padding=8)
        self.tab_iff = ttk.Frame(self.nb, padding=8)
        self.tab_teams = ttk.Frame(self.nb, padding=8)
        self.tab_goalie = ttk.Frame(self.nb, padding=8)
        self.tab_portrait = ttk.Frame(self.nb, padding=8)
        self.tab_scorebug = ttk.Frame(self.nb, padding=8)
        self.tab_settings = ttk.Frame(self.nb, padding=8)
        for t, label in ((self.tab_audio, "  Audio  "),
                         (self.tab_iff, "  IFF Textures  "),
                         (self.tab_teams, "  Teams  "),
                         (self.tab_goalie, "  Goalie Equipment  "),
                         (self.tab_portrait, "  Portraits  "),
                         (self.tab_scorebug, "  Scoreclock  "),
                         (self.tab_settings, "  Settings  ")):
            self.nb.add(t, text=label)

        logf = ttk.LabelFrame(pane, text="Log", padding=4)
        self.prog = ttk.Progressbar(logf, mode="determinate")
        self.prog.pack(fill="x", pady=(0, 3))
        self.log = tk.Text(logf, height=8, wrap="none")
        self.log.pack(fill="both", expand=True)
        pane.add(logf, weight=1)

        self._build_audio()
        self._build_iff()
        self._build_teams()
        self._build_live_tab(
            self.tab_goalie, "Goalie Equipment",
            "Goalie masks are selected by two bit-fields in the loaded player "
            "struct:\n\n"
            "    shell   = (*(u32*)(player + 0xB4) >> 23) & 0xF\n"
            "    pattern =  *(u32*)(player + 0xB8)        & 0x1F\n\n"
            "In the Roster.ROS file the same value sits inside a per-save "
            "scrambled and checksummed field at +0x118, so it cannot be edited "
            "cleanly on disk. Assigning a mask means writing those two fields in "
            "the running game.")
        self._build_live_tab(
            self.tab_portrait, "Portraits",
            "A player's portrait is chosen by the u16 at player_record + 0x1C. "
            "The game formats an asset name \"%04d_image\" from that key, hashes "
            "it, and matches the result against a portrait blob header.\n\n"
            "On disk the roster stores players in a different, mostly-empty "
            "layout where the key is not plainly at +0x1C, so reassigning a "
            "portrait means writing that u16 in the running game.")
        self._build_scorebug()
        self._build_settings()

    # ---------------- Audio ----------------
    def _build_audio(self):
        t = self.tab_audio
        bar = ttk.Frame(t)
        bar.pack(fill="x", pady=(0, 6))
        ttk.Label(bar, text="Sound bank:").pack(side="left")
        self.bank_var = tk.StringVar()
        self.bank_cb = ttk.Combobox(bar, textvariable=self.bank_var, width=26,
                                    state="readonly")
        self.bank_cb.pack(side="left", padx=6)
        self.bank_cb.bind("<<ComboboxSelected>>", lambda e: self._fill_sounds())
        ttk.Button(bar, text="Extract selected",
                   command=self._extract_sounds).pack(side="left", padx=4)
        ttk.Button(bar, text="Extract whole bank",
                   command=lambda: self._extract_sounds(all_of=True)).pack(side="left")
        if not HAS_FFMPEG:
            ttk.Label(bar, text="  ffmpeg not found — decoding disabled",
                      foreground="#b04").pack(side="left", padx=8)

        cols = ("idx", "rate", "secs", "size", "offset")
        self.sound_tree = ttk.Treeview(t, columns=cols, show="headings",
                                       selectmode="extended")
        for c, h, w in (("idx", "#", 60), ("rate", "Sample rate", 100),
                        ("secs", "Seconds", 90), ("size", "Bytes", 100),
                        ("offset", "Offset in bank", 130)):
            self.sound_tree.heading(c, text=h)
            self.sound_tree.column(c, width=w, anchor="w")
        self.sound_tree.pack(fill="both", expand=True)
        ttk.Label(t, foreground="#777", wraplength=980, justify="left",
                  text="Sample rate comes from the bank record — the game uses "
                       "48000, 44100, 22050 and 16000, so a fixed 48 kHz guess is "
                       "wrong about half the time. Channel count is read from the "
                       "stream itself, not the record."
                  ).pack(anchor="w", pady=(4, 0))

    def _fill_banks(self):
        if not (sound_bank and self.arc):
            return
        try:
            self.banks = sound_bank.find_banks(self.arc)
        except Exception:
            self.banks = []
        self.bank_cb["values"] = [n for n, _e in self.banks]
        if self.banks:
            self.bank_cb.current(0)
            self._fill_sounds()

    def _fill_sounds(self):
        self.sound_tree.delete(*self.sound_tree.get_children())
        entry = dict(self.banks).get(self.bank_var.get())
        if not entry:
            return
        try:
            sounds, _payload = sound_bank.parse_bank(self.arc.read_file(entry))
        except Exception as ex:
            self._logln("bank: %s" % ex)
            return
        for s in sounds:
            self.sound_tree.insert("", "end", iid=str(s.index),
                                   values=(s.index, s.rate, "%.2f" % s.seconds,
                                           s.size, "0x%X" % s.offset))
        self._logln("%s: %d sounds" % (self.bank_var.get(), len(sounds)))

    def _extract_sounds(self, all_of=False):
        if not (self.arc and sound_bank and self.bank_var.get()):
            return
        if not HAS_FFMPEG:
            messagebox.showerror("ffmpeg required",
                                 "Decoding XMA2 needs ffmpeg on PATH.")
            return
        sel = None if all_of else [int(i) for i in self.sound_tree.selection()]
        if not all_of and not sel:
            messagebox.showinfo("Select sounds", "Pick one or more sounds first.")
            return
        self._start(self._do_extract_sounds, self.bank_var.get(), sel)

    def _do_extract_sounds(self, name, sel):
        entry = dict(self.banks)[name]
        raw = self.arc.read_file(entry)
        sounds, payload = sound_bank.parse_bank(raw)
        d = os.path.join(self._outdir(), "audio", name.rsplit(".", 1)[0])
        os.makedirs(d, exist_ok=True)
        want = sounds if sel is None else [s for s in sounds if s.index in sel]
        self.q.put(("progmax", len(want)))
        done = 0
        for n, s in enumerate(want):
            data = sound_bank.read_sound(raw, s, payload)
            if len(data) >= sound_bank.PACKET:
                ch = sound_bank.stream_channels(data)
                wav = os.path.join(d, "%03d_%dHz_%dch_%.2fs.wav"
                                   % (s.index, s.rate, ch, s.seconds))
                try:
                    xma_extract.extract_to_wav(data, wav, rate=s.rate, channels=ch)
                    done += 1
                except Exception:
                    pass
            self.q.put(("prog", n + 1))
        self._logln("decoded %d/%d sounds -> %s" % (done, len(want), d))

    # ---------------- IFF Textures ----------------
    def _build_iff(self):
        t = self.tab_iff
        bar = ttk.Frame(t)
        bar.pack(fill="x", pady=(0, 6))
        ttk.Label(bar, text="Filter:").pack(side="left")
        self.filter_var = tk.StringVar()
        e = ttk.Entry(bar, textvariable=self.filter_var, width=24)
        e.pack(side="left", padx=6)
        e.bind("<KeyRelease>", lambda ev: self._refill())
        ttk.Button(bar, text="Extract textures",
                   command=self._extract_textures).pack(side="left", padx=4)
        ttk.Button(bar, text="Extract raw file",
                   command=self._extract_raw).pack(side="left")
        ttk.Separator(bar, orient="vertical").pack(side="left", fill="y", padx=8)
        ttk.Button(bar, text="Replace texture…",
                   command=self._replace_texture).pack(side="left")
        ttk.Button(bar, text="Revert all mods",
                   command=self._revert).pack(side="left", padx=4)
        self.count_var = tk.StringVar(value="")
        ttk.Label(bar, textvariable=self.count_var).pack(side="right")

        cols = ("idx", "name", "type", "size", "hash")
        self.tree = ttk.Treeview(t, columns=cols, show="headings",
                                 selectmode="extended")
        for c, h, w in (("idx", "#", 60), ("name", "Asset name", 250),
                        ("type", "Type", 140), ("size", "Bytes", 110),
                        ("hash", "Name hash", 110)):
            self.tree.heading(c, text=h)
            self.tree.column(c, width=w, anchor="w")
        self.tree.pack(fill="both", expand=True)
        ttk.Label(t, foreground="#777", wraplength=980, justify="left",
                  text="Textures extract into folders by asset type — jerseys/, "
                       "arenas/, rinks/ — rather than one flat dump. Unnamed "
                       "entries fall back to their archive index."
                  ).pack(anchor="w", pady=(4, 0))

    def _refill(self):
        self.tree.delete(*self.tree.get_children())
        f = self.filter_var.get().strip().lower()
        n = 0
        for e, label in self.rows:
            nm = self.names.get(e.crc, "")
            if f and f not in label.lower() and f not in nm.lower() \
                    and f != str(e.index):
                continue
            self.tree.insert("", "end", iid=str(e.index),
                             values=(e.index, nm, label, e.size, "0x%08X" % e.crc))
            n += 1
        self.count_var.set("%d shown / %d total" % (n, len(self.rows)))

    def _selected(self):
        sel = self.tree.selection()
        if not sel:
            messagebox.showinfo("Select a file", "Pick a file in the list first.")
            return None
        return [int(s) for s in sel]

    def _extract_textures(self):
        idxs = self._selected()
        if idxs:
            self._start(self._do_textures, idxs)

    def _do_textures(self, idxs):
        root = os.path.join(self._outdir(), "textures")
        os.makedirs(root, exist_ok=True)
        self.q.put(("progmax", len(idxs)))
        total = 0
        for n, idx in enumerate(idxs):
            asset = self.names.get(self.arc.files[idx].crc, "")
            try:
                cnt, sub = vc_texture.dump_file(self.arc, idx, root, asset)
                if cnt:
                    total += cnt
                    self._logln("  %s: %d textures -> %s"
                                % (asset or "#%d" % idx, cnt,
                                   os.path.relpath(sub, root)))
            except Exception as ex:
                self._logln("  #%d: %s" % (idx, ex))
            self.q.put(("prog", n + 1))
        self._logln("Done: %d .dds -> %s" % (total, root))

    def _extract_raw(self):
        idxs = self._selected()
        if idxs:
            self._start(self._do_raw, idxs)

    def _do_raw(self, idxs):
        d = os.path.join(self._outdir(), "raw")
        os.makedirs(d, exist_ok=True)
        for idx in idxs:
            e = self.arc.files[idx]
            nm = self.names.get(e.crc, "") or ("%05d.bin" % idx)
            raw = self.arc.read_file(e)
            with open(os.path.join(d, nm), "wb") as f:
                f.write(raw)
            self._logln("  wrote %s (%d bytes)" % (nm, len(raw)))

    def _replace_texture(self):
        if not HAS_WRITE:
            messagebox.showerror("Unavailable",
                                 "Replacing needs numpy and Pillow:\n\n"
                                 "    pip install numpy pillow")
            return
        idxs = self._selected()
        if not idxs:
            return
        idx = idxs[0]
        try:
            raw = self.arc.read_file(self.arc.files[idx])
            dec, bounds = vc_texture.decompress_with_bounds(raw)
            descs = vc_texture.describe_textures(dec, bounds)
        except Exception as ex:
            messagebox.showerror("Cannot read", str(ex))
            return
        if not descs:
            messagebox.showinfo("No textures", "That file holds no textures.")
            return
        choices = ["%d: %dx%d %s" % (i, d["w"], d["h"],
                                     vc_texture.GPU_FMT[d["fmt"]][0])
                   for i, d in enumerate(descs)]
        ti = _choose(self.root, "Replace texture", "Which texture?", choices)
        if ti is None:
            return
        d = descs[ti]
        path = filedialog.askopenfilename(
            title="Replacement image (must be exactly %dx%d)" % (d["w"], d["h"]),
            filetypes=[("Images", "*.png *.dds *.tga *.bmp"), ("All", "*.*")])
        if not path:
            return
        if not messagebox.askyesno(
                "Write to the disc image?",
                "This edits the image in place.\n\n"
                "#%d texture %d — %dx%d %s\n%s\n\n"
                "Every write is journalled, so 'Revert all mods' restores the "
                "image byte-for-byte. Continue?"
                % (idx, ti, d["w"], d["h"], vc_texture.GPU_FMT[d["fmt"]][0],
                   os.path.basename(path))):
            return
        self._start(self._do_replace, idx, ti, path)

    def _do_replace(self, idx, ti, path):
        import numpy as np
        from PIL import Image
        img = np.array(Image.open(path).convert("RGBA"))
        self._logln("Replacing #%d texture %d — recompressing, this can take a "
                    "while for large assets…" % (idx, ti))
        info = vc_write.replace_texture(self.iso_var.get(), idx, ti, img)
        self._logln("  OK  %s %s  %d -> %d bytes (slot %d)"
                    % (info["dims"], info["format"], info["orig_bytes"],
                       info["new_bytes"], info["slot"]))
        self.q.put(("journal", None))

    def _revert(self):
        if not HAS_WRITE:
            return
        try:
            p = vc_write.Patcher(self.iso_var.get())
            n = p.pending()
            p.close()
        except Exception as ex:
            messagebox.showerror("Cannot read journal", str(ex))
            return
        if not n:
            messagebox.showinfo("Nothing to revert", "No journalled changes.")
            return
        if messagebox.askyesno("Revert", "Restore %d modified region(s)?" % n):
            self._start(self._do_revert)

    def _do_revert(self):
        n = vc_write.revert(self.iso_var.get())
        self._logln("Reverted %d region(s) — image restored exactly." % n)
        self.q.put(("journal", None))

    # ---------------- Teams ----------------
    def _build_teams(self):
        t = self.tab_teams
        bar = ttk.Frame(t)
        bar.pack(fill="x", pady=(0, 6))
        ttk.Label(bar, text="Roster.ROS:").pack(side="left")
        ttk.Entry(bar, textvariable=self.ros_var).pack(side="left", fill="x",
                                                       expand=True, padx=6)
        ttk.Button(bar, text="Browse", command=self._browse_ros).pack(side="left")
        ttk.Button(bar, text="Load", command=self._load_ros).pack(side="left", padx=4)
        self.led_var = tk.BooleanVar(value=False)
        ttk.Checkbutton(bar, text="Arena LED colours", variable=self.led_var,
                        command=self._fill_teams).pack(side="left", padx=8)

        cols = ("code", "primary", "secondary")
        self.team_tree = ttk.Treeview(t, columns=cols, show="headings", height=16)
        for c, h, w in (("code", "Team", 90), ("primary", "Primary", 170),
                        ("secondary", "Secondary", 170)):
            self.team_tree.heading(c, text=h)
            self.team_tree.column(c, width=w, anchor="w")
        self.team_tree.pack(fill="both", expand=True)
        self.team_tree.bind("<Double-1>", self._edit_team_color)

        ttk.Label(t, foreground="#777", wraplength=980, justify="left",
                  text="Team colours live in the roster save, not on the disc. "
                       "Double-click a row to change one. Edits are made in place "
                       "and never change the file size, so every offset the game "
                       "holds stays valid; a .colorbak copy is written before the "
                       "first change. The game caches colours at load, so restart "
                       "to see them."
                  ).pack(anchor="w", pady=(6, 0))

    def _browse_ros(self):
        p = filedialog.askopenfilename(title="Roster save",
                                       filetypes=[("Roster", "*.ROS"),
                                                  ("All", "*.*")])
        if p:
            self.ros_var.set(p)
            self._load_ros()

    def _load_ros(self):
        if not roster_mod:
            messagebox.showerror("Unavailable", "The roster module failed to import.")
            return
        p = self.ros_var.get().strip()
        if not p or not os.path.isfile(p):
            messagebox.showinfo("Pick a file", "Choose a Roster.ROS save first.")
            return
        try:
            self.roster = roster_mod.Roster(p)
            base, stride = self.roster._color_table()
            self._logln("Roster: %d chunks, colour table at 0x%X stride %d"
                        % (len(self.roster.chunks), base, stride))
        except Exception as ex:
            self.roster = None
            messagebox.showerror("Cannot read roster", str(ex))
            return
        self._fill_teams()

    def _fill_teams(self):
        self.team_tree.delete(*self.team_tree.get_children())
        if not self.roster:
            return
        try:
            rows = self.roster.team_colors(led=self.led_var.get())
        except Exception as ex:
            self._logln("teams: %s" % ex)
            return
        for code, pri, sec in rows:
            self.team_tree.insert("", "end", iid=code,
                                  values=(code, "#%02X%02X%02X" % pri,
                                          "#%02X%02X%02X" % sec))

    def _edit_team_color(self, _ev=None):
        if not self.roster:
            return
        sel = self.team_tree.selection()
        if not sel:
            return
        code = sel[0]
        which = _choose(self.root, "Edit %s" % code, "Which colour?",
                        ["Primary", "Secondary"])
        if which is None:
            return
        from tkinter import colorchooser
        rgb = colorchooser.askcolor(
            title="%s %s" % (code, "primary" if which == 0 else "secondary"))
        if not rgb or not rgb[0]:
            return
        v = tuple(int(x) for x in rgb[0][:3])
        try:
            if which == 0:
                self.roster.set_team_color(code, primary=v, led=self.led_var.get())
            else:
                self.roster.set_team_color(code, secondary=v, led=self.led_var.get())
            self.roster.save()
        except Exception as ex:
            messagebox.showerror("Cannot save", str(ex))
            return
        self._logln("%s %s -> #%02X%02X%02X (saved; .colorbak kept)"
                    % (code, "primary" if which == 0 else "secondary",
                       v[0], v[1], v[2]))
        self._fill_teams()

    # ---------------- live-only tabs ----------------
    def _build_live_tab(self, tab, title, body):
        ttk.Label(tab, text=title, font=("", 13, "bold")).pack(anchor="w")
        ttk.Label(tab, text="Needs a running game — not supported here",
                  foreground="#b04").pack(anchor="w", pady=(2, 10))
        ttk.Label(tab, text=body, wraplength=940, justify="left").pack(anchor="w")
        ttk.Label(tab, wraplength=940, justify="left", foreground="#777",
                  text="\nThis toolkit edits files on disc and does not attach to "
                       "a running process, so the feature is out of scope here. "
                       "The format notes are recorded so it can be built later; "
                       "the NHL 2K10 Mod Launcher already implements it against "
                       "Xenia."
                  ).pack(anchor="w")

    def _build_scorebug(self):
        t = self.tab_scorebug
        ttk.Label(t, text="Scoreclock", font=("", 13, "bold")).pack(anchor="w")
        ttk.Label(t, text="Not yet implemented",
                  foreground="#b04").pack(anchor="w", pady=(2, 10))
        ttk.Label(t, wraplength=940, justify="left", text=(
            "The scoreclock is a small 3D scene serialised inside "
            "overlay_static.iff: joints plus textured quads, with names stored as "
            "UTF-16BE strings and name references as crc32 of the RAW, "
            "case-sensitive string.\n\n"
            "That differs from asset hashing, which uppercases first — the two "
            "hashes are not interchangeable, and mixing them up is an easy way to "
            "resolve nothing.\n\n"
            "Moving an element means rewriting the baked text-record table, not "
            "the skeleton joints: the joints are only the bind pose and editing "
            "them has no visible effect in game."
        )).pack(anchor="w")
        ttk.Button(t, text="Extract overlay_static.iff textures",
                   command=self._extract_overlay).pack(anchor="w", pady=12)

    def _extract_overlay(self):
        if not (self.arc and asset_names):
            return
        e = asset_names.resolve(self.arc, "overlay_static.iff")
        if not e:
            messagebox.showinfo("Not found",
                                "overlay_static.iff is not in this archive.")
            return
        self._start(self._do_textures, [e.index])

    # ---------------- Settings ----------------
    def _build_settings(self):
        t = self.tab_settings
        g = ttk.LabelFrame(t, text="Paths", padding=8)
        g.pack(fill="x")
        for label, var, browse in (("Output folder", self.out_var, self._browse_out),
                                   ("Roster.ROS", self.ros_var, self._browse_ros),
                                   ("xma2encode.exe", self.xma_enc_var,
                                    self._browse_xma)):
            r = ttk.Frame(g)
            r.pack(fill="x", pady=2)
            ttk.Label(r, text=label, width=16).pack(side="left")
            ttk.Entry(r, textvariable=var).pack(side="left", fill="x",
                                                expand=True, padx=6)
            ttk.Button(r, text="Browse", command=browse).pack(side="left")

        s = ttk.LabelFrame(t, text="Environment", padding=8)
        s.pack(fill="x", pady=8)
        for k, v in (
                ("ffmpeg", "found" if HAS_FFMPEG
                 else "NOT FOUND — audio decoding disabled"),
                ("numpy + Pillow", "found" if HAS_WRITE
                 else "NOT FOUND — replacement disabled"),
                ("asset names", "%d candidate names" % len(self.names)
                 if self.names else "unavailable"),
                ("xma2encode", "set" if self.xma_enc_var.get() else
                 "not set — needed to encode new audio (XDK tool, cannot be "
                 "redistributed)")):
            r = ttk.Frame(s)
            r.pack(fill="x")
            ttk.Label(r, text=k, width=16).pack(side="left")
            ttk.Label(r, text=v).pack(side="left")

        j = ttk.LabelFrame(t, text="Modifications", padding=8)
        j.pack(fill="x")
        self.jrn_var = tk.StringVar(value="—")
        ttk.Label(j, textvariable=self.jrn_var).pack(anchor="w")
        b = ttk.Frame(j)
        b.pack(anchor="w", pady=4)
        ttk.Button(b, text="Refresh", command=self._refresh_journal).pack(side="left")
        ttk.Button(b, text="Revert all", command=self._revert).pack(side="left",
                                                                    padx=6)
        ttk.Label(j, foreground="#777", wraplength=940, justify="left",
                  text="Every write records the bytes it overwrites to "
                       "<iso>.undo.json, so any edit can be undone exactly. Keep "
                       "that file. Work on a copy of the image regardless."
                  ).pack(anchor="w")

    def _refresh_journal(self):
        if not (HAS_WRITE and self.iso_var.get()):
            return
        try:
            p = vc_write.Patcher(self.iso_var.get())
            self.jrn_var.set("%d modified region(s) — journal %s"
                             % (p.pending(), os.path.basename(p.journal_path)))
            p.close()
        except Exception as ex:
            self.jrn_var.set(str(ex))

    def _browse_xma(self):
        p = filedialog.askopenfilename(title="xma2encode.exe",
                                       filetypes=[("Executable", "*.exe"),
                                                  ("All", "*.*")])
        if p:
            self.xma_enc_var.set(p)

    def _browse_out(self):
        p = filedialog.askdirectory(title="Output folder")
        if p:
            self.out_var.set(p)

    def _browse_iso(self):
        p = filedialog.askopenfilename(title="NHL 2K10 disc image",
                                       filetypes=[("Disc image", "*.iso"),
                                                  ("All", "*.*")])
        if p:
            self.iso_var.set(p)
            self._load()

    # ================= plumbing =================
    def _outdir(self):
        d = self.out_var.get().strip()
        os.makedirs(d, exist_ok=True)
        return d

    def _logln(self, s):
        self.q.put(("log", s))

    def _load(self):
        iso = self.iso_var.get().strip()
        if not iso or not os.path.isfile(iso):
            self.status_var.set("No disc image loaded")
            return
        try:
            self.arc = Archive(iso)
        except Exception as ex:
            messagebox.showerror("Cannot open", str(ex))
            return
        self.rows = []
        for e in self.arc.files:
            try:
                head = self.arc.read_file_head(e, 8)
            except Exception:
                head = b""
            self.rows.append((e, classify(head)))
        named = sum(1 for e, _ in self.rows if e.crc in self.names)
        self.status_var.set("%d files · %d named" % (len(self.rows), named))
        self._refill()
        self._fill_banks()
        self._refresh_journal()
        self._logln("Loaded %s — %d entries, %d named"
                    % (os.path.basename(iso), len(self.rows), named))

    def _start(self, target, *args):
        if self.worker and self.worker.is_alive():
            messagebox.showinfo("Busy", "A job is already running.")
            return
        self.prog["value"] = 0
        self.worker = threading.Thread(target=self._wrap, args=(target, args),
                                       daemon=True)
        self.worker.start()

    def _wrap(self, target, args):
        try:
            target(*args)
        except Exception:
            self.q.put(("log", traceback.format_exc()))

    def _poll(self):
        try:
            while True:
                kind, payload = self.q.get_nowait()
                if kind == "log":
                    self.log.insert("end", str(payload) + "\n")
                    self.log.see("end")
                elif kind == "prog":
                    self.prog["value"] = payload
                elif kind == "progmax":
                    self.prog["maximum"] = payload
                elif kind == "journal":
                    self._refresh_journal()
        except queue.Empty:
            pass
        self.root.after(100, self._poll)


def main():
    root = tk.Tk()
    try:
        ttk.Style().theme_use("clam")
    except Exception:
        pass
    App(root)
    root.mainloop()


if __name__ == "__main__":
    main()
