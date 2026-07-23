#!/usr/bin/env python3
"""
NHL 2K10 (Xbox 360) Asset Extractor -- GUI

A tkinter front-end over the reverse-engineered pipeline:
  ISO (XDVDFS) -> AA00B3BF archive -> FF3BEF94/0E4837C3 (VC LZSS) blocks.

Features:
  * Open a NHL 2K10 .iso, list all 2407 archived files (type, sizes, hash).
  * Extract the top-level ISO "base files" (0A/0B/1A/1B/default.xex/nxeart).
  * Extract archived files RAW (as stored) or DECOMPRESSED (VC LZSS decoded).
  * Multi-select + filter, background worker thread, progress + log.

Pure standard library (tkinter). Reuses tools/: xdvdfs, nhl2k_arc, vc_decomp,
vc_extract.
"""
import os
import sys
import struct
import threading
import queue
import traceback
import tkinter as tk
from tkinter import ttk, filedialog, messagebox

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, "nhl2k10"))

import xdvdfs
from nhl2k_arc import Archive
import vc_decomp
import vc_extract
import vc_texture
import xma_extract
import shutil
try:
    import names as asset_names          # CRC-32(NAME.UPPER()) -> readable name
except Exception:
    asset_names = None
try:
    import vc_write                     # write-back; needs numpy + Pillow
    HAS_WRITE = True
except Exception:
    HAS_WRITE = False

HAS_FFMPEG = shutil.which("ffmpeg") is not None

def _find_iso():
    """Locate the user's own disc image.

    No game data ships with this tool — you supply your own legally obtained
    copy. Checked in order: the NHL2K10_ISO environment variable, then any .iso
    sitting next to this script (largest first, since the game image is big).
    """
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


DEFAULT_ISO = _find_iso()


def _choose(parent, title, prompt, choices):
    """Modal list picker. Returns the chosen index, or None if cancelled."""
    win = tk.Toplevel(parent)
    win.title(title)
    win.transient(parent)
    win.grab_set()
    ttk.Label(win, text=prompt, padding=8).pack(anchor="w")
    lb = tk.Listbox(win, width=52, height=min(18, max(4, len(choices))))
    for c in choices:
        lb.insert("end", c)
    lb.selection_set(0)
    lb.pack(fill="both", expand=True, padx=8)
    result = {"i": None}

    def ok(*_a):
        sel = lb.curselection()
        result["i"] = sel[0] if sel else None
        win.destroy()

    btns = ttk.Frame(win, padding=8)
    btns.pack(fill="x")
    ttk.Button(btns, text="OK", command=ok).pack(side="right")
    ttk.Button(btns, text="Cancel", command=win.destroy).pack(side="right", padx=6)
    lb.bind("<Double-Button-1>", ok)
    win.wait_window()
    return result["i"]


def classify(head):
    """Short type label + suggested extension from the first bytes."""
    m = head[:4]
    if m == b"\xff\x3b\xef\x94":
        return "IFF (compressed)", "iff"
    if m == b"\x0e\x48\x37\xc3":
        return "0E4837C3 block", "iff"
    if m == b"\x08\x00\x00\x00":
        return "XMA audio", "xma"
    if m == b"\x02\x00\x01\x00":
        return "data table (LE)", "bin"
    if m == b"\xe4\x79\x12\x07":
        return "IFF manifest", "iff"
    if m == b"\xf0\x98\x50\x30":
        return "IFF (uncompressed)", "iff"
    if m == b"\x00\x06\xf0\x00":
        return "font/loc package", "bin"
    if m[:3] == b"\x00\x00\x00" or m == b"\x00\x00\x00\x01":
        return "data", "bin"
    return "unknown", "bin"


class ExtractorGUI:
    def __init__(self, root):
        self.root = root
        root.title("NHL 2K10 (Xbox 360) Asset Extractor")
        root.geometry("1000x680")
        self.arc = None
        self.rows = []            # (entry, type_label, ext)
        # name_hash -> readable asset name, for entries we can identify
        self.names = asset_names.build_catalog() if asset_names else {}
        self.q = queue.Queue()
        self.worker = None
        self._build()
        self.root.after(100, self._poll)
        if os.path.exists(DEFAULT_ISO):
            self.iso_var.set(DEFAULT_ISO)

    # ---------- UI ----------
    def _build(self):
        top = ttk.Frame(self.root, padding=6)
        top.pack(fill="x")
        ttk.Label(top, text="ISO:").pack(side="left")
        self.iso_var = tk.StringVar()
        ttk.Entry(top, textvariable=self.iso_var).pack(side="left", fill="x",
                                                       expand=True, padx=4)
        ttk.Button(top, text="Browse", command=self._browse_iso).pack(side="left")
        ttk.Button(top, text="Load", command=self._load).pack(side="left", padx=4)

        out = ttk.Frame(self.root, padding=(6, 0))
        out.pack(fill="x")
        ttk.Label(out, text="Output folder:").pack(side="left")
        self.out_var = tk.StringVar(value=os.path.join(HERE, "extracted"))
        ttk.Entry(out, textvariable=self.out_var).pack(side="left", fill="x",
                                                       expand=True, padx=4)
        ttk.Button(out, text="Browse", command=self._browse_out).pack(side="left")

        # base files row
        base = ttk.LabelFrame(self.root, text="ISO base files", padding=6)
        base.pack(fill="x", padx=6, pady=4)
        ttk.Button(base, text="Extract base files (0A/0B/1A/1B/default.xex/nxeart)",
                   command=self._extract_base).pack(side="left")
        ttk.Label(base, text="  (raw split archives + executable)").pack(side="left")

        # filter
        filt = ttk.Frame(self.root, padding=(6, 0))
        filt.pack(fill="x")
        ttk.Label(filt, text="Filter type:").pack(side="left")
        self.filter_var = tk.StringVar()
        fe = ttk.Entry(filt, textvariable=self.filter_var, width=24)
        fe.pack(side="left", padx=4)
        fe.bind("<KeyRelease>", lambda e: self._refill_tree())
        self.mode_var = tk.StringVar(value="decompressed")
        ttk.Radiobutton(filt, text="Decompressed", value="decompressed",
                        variable=self.mode_var).pack(side="left", padx=(16, 2))
        ttk.Radiobutton(filt, text="Raw", value="raw",
                        variable=self.mode_var).pack(side="left")

        # tree
        cols = ("idx", "name", "type", "csize", "dsize", "hash", "offset")
        self.tree = ttk.Treeview(self.root, columns=cols, show="headings",
                                 selectmode="extended")
        headers = {"idx": ("#", 60), "name": ("Name", 190),
                   "type": ("Type", 150), "csize": ("Stored", 100),
                   "dsize": ("Decompressed", 110), "hash": ("Name hash", 110),
                   "offset": ("Stream offset", 120)}
        for c in cols:
            t, w = headers[c]
            self.tree.heading(c, text=t)
            self.tree.column(c, width=w, anchor="w")
        vsb = ttk.Scrollbar(self.root, orient="vertical", command=self.tree.yview)
        self.tree.configure(yscrollcommand=vsb.set)
        self.tree.pack(side="top", fill="both", expand=True, padx=6)
        vsb.place(in_=self.tree, relx=1.0, rely=0, relheight=1.0, anchor="ne")

        # action buttons
        act = ttk.Frame(self.root, padding=6)
        act.pack(fill="x")
        ttk.Button(act, text="Extract selected", command=self._extract_selected).pack(side="left")
        ttk.Button(act, text="Extract ALL", command=self._extract_all).pack(side="left", padx=6)
        ttk.Separator(act, orient="vertical").pack(side="left", fill="y", padx=8)
        ttk.Button(act, text="Textures → DDS",
                   command=self._extract_textures).pack(side="left")
        wavbtn = ttk.Button(act, text="Audio → WAV", command=self._extract_wav)
        wavbtn.pack(side="left", padx=6)
        if not HAS_FFMPEG:
            wavbtn.state(["disabled"])
            ttk.Label(act, text="(ffmpeg not found)").pack(side="left")
        self.count_var = tk.StringVar(value="No ISO loaded")
        ttk.Label(act, textvariable=self.count_var).pack(side="right")

        # ---- write-back (modding) ----
        mod = ttk.Frame(self.root, padding=(6, 0, 6, 6))
        mod.pack(fill="x")
        ttk.Label(mod, text="Mod:").pack(side="left")
        ttk.Button(mod, text="Replace texture…",
                   command=self._replace_texture).pack(side="left", padx=4)
        ttk.Button(mod, text="Replace audio (.xma)…",
                   command=self._replace_audio).pack(side="left", padx=4)
        ttk.Separator(mod, orient="vertical").pack(side="left", fill="y", padx=8)
        ttk.Button(mod, text="Revert ALL mods",
                   command=self._revert_mods).pack(side="left")
        self.mod_var = tk.StringVar(value="")
        ttk.Label(mod, textvariable=self.mod_var).pack(side="right")

        # progress + log
        self.prog = ttk.Progressbar(self.root, mode="determinate")
        self.prog.pack(fill="x", padx=6)
        self.log = tk.Text(self.root, height=8, wrap="none")
        self.log.pack(fill="both", padx=6, pady=(2, 6))

    # ---------- helpers ----------
    def _browse_iso(self):
        p = filedialog.askopenfilename(title="Select NHL 2K10 ISO",
                                       filetypes=[("ISO", "*.iso"), ("All", "*.*")])
        if p:
            self.iso_var.set(p)

    def _browse_out(self):
        p = filedialog.askdirectory(title="Select output folder")
        if p:
            self.out_var.set(p)

    def _logln(self, s):
        self.log.insert("end", s + "\n")
        self.log.see("end")

    def _load(self):
        iso = self.iso_var.get().strip()
        if not os.path.exists(iso):
            messagebox.showerror("Error", "ISO not found:\n%s" % iso)
            return
        self._logln("Loading %s ..." % iso)
        try:
            self.arc = Archive(iso)
        except Exception as e:
            messagebox.showerror("Parse error", str(e))
            self._logln("ERROR: %s" % e)
            return
        # read type of every file (first bytes) via shared handle
        self.rows = []
        with open(iso, "rb") as f:
            for e in self.arc.files:
                a, iso_off, avail = self.arc._stream_to_iso(e.offset)
                f.seek(iso_off)
                head = f.read(min(8, e.size, avail))
                label, ext = classify(head)
                self.rows.append((e, label, ext))
        self._logln("Loaded %d files (align=%d, %d archives)." %
                    (len(self.arc.files), self.arc.align, len(self.arc.archives)))
        self._refill_tree()

    def _refill_tree(self):
        self.tree.delete(*self.tree.get_children())
        filt = self.filter_var.get().lower().strip()
        shown = 0
        for e, label, ext in self.rows:
            nm = self.names.get(e.crc, "")
            if filt and filt not in label.lower() and filt not in nm.lower():
                continue
            self.tree.insert("", "end", iid=str(e.index),
                             values=(e.index, self.names.get(e.crc, ""), label,
                                     e.size, "-", "0x%08X" % e.crc, e.offset))
            shown += 1
        self.count_var.set("%d shown / %d total" % (shown, len(self.rows)))

    # ---------- extraction (threaded) ----------
    def _start_worker(self, target, *args):
        if self.worker and self.worker.is_alive():
            messagebox.showinfo("Busy", "An extraction is already running.")
            return
        self.prog["value"] = 0
        self.worker = threading.Thread(target=self._wrap, args=(target, args),
                                       daemon=True)
        self.worker.start()

    def _wrap(self, target, args):
        try:
            target(*args)
        except Exception:
            self.q.put(("log", "ERROR:\n" + traceback.format_exc()))
        self.q.put(("done", None))

    def _poll(self):
        try:
            while True:
                kind, payload = self.q.get_nowait()
                if kind == "log":
                    self._logln(payload)
                elif kind == "prog":
                    self.prog["value"] = payload
                elif kind == "progmax":
                    self.prog["maximum"] = payload
                elif kind == "mod":
                    self.mod_var.set(payload)
                elif kind == "done":
                    pass
        except queue.Empty:
            pass
        self.root.after(100, self._poll)

    def _outdir(self):
        d = self.out_var.get().strip()
        os.makedirs(d, exist_ok=True)
        return d

    def _extract_base(self):
        if not self.arc:
            messagebox.showinfo("Load first", "Load an ISO first.")
            return
        self._start_worker(self._do_base)

    def _do_base(self):
        iso = self.arc.iso_path
        base, entries = xdvdfs.list_files(iso)
        targets = ["0A", "0B", "1A", "1B", "default.xex", "nxeart"]
        byname = {e.path: e for e in entries}
        d = self._outdir()
        self.q.put(("progmax", len(targets)))
        with open(iso, "rb") as f:
            for i, name in enumerate(targets):
                e = byname.get(name)
                if not e:
                    continue
                op = os.path.join(d, name.replace("/", "_"))
                self.q.put(("log", "Extracting %s (%d bytes)..." % (name, e.size)))
                f.seek(xdvdfs.file_offset(base, e))
                remaining = e.size
                with open(op, "wb") as o:
                    while remaining:
                        chunk = f.read(min(remaining, 8 << 20))
                        if not chunk:
                            break
                        o.write(chunk)
                        remaining -= len(chunk)
                self.q.put(("prog", i + 1))
        self.q.put(("log", "Base files written to %s" % d))

    def _extract_selected(self):
        sel = self.tree.selection()
        if not sel:
            messagebox.showinfo("No selection", "Select one or more rows.")
            return
        idxs = [int(s) for s in sel]
        self._start_worker(self._do_files, idxs)

    def _extract_all(self):
        if not self.arc:
            return
        idxs = [e.index for e, _, _ in self.rows]
        self._start_worker(self._do_files, idxs)

    # ---------------- write-back (modding) ----------------
    def _selected_index(self):
        sel = self.tree.selection()
        if not sel:
            messagebox.showinfo("Select a file", "Pick a file in the list first.")
            return None
        return int(sel[0])

    def _require_write(self):
        if not HAS_WRITE:
            messagebox.showerror(
                "Write-back unavailable",
                "Replacing assets needs numpy and Pillow:\n\n    pip install numpy pillow")
            return False
        if not self.arc:
            messagebox.showinfo("Load an ISO", "Load an ISO first.")
            return False
        return True

    def _replace_texture(self):
        if not self._require_write():
            return
        idx = self._selected_index()
        if idx is None:
            return
        try:
            raw = self.arc.read_file(self.arc.files[idx])
            dec, bounds = vc_texture.decompress_with_bounds(raw)
            descs = vc_texture.describe_textures(dec, bounds)
        except Exception as ex:
            messagebox.showerror("Cannot read", "File #%d: %s" % (idx, ex))
            return
        if not descs:
            messagebox.showinfo("No textures", "File #%d holds no textures." % idx)
            return
        choices = ["%d: %dx%d %s" % (i, d["w"], d["h"],
                                     vc_texture.GPU_FMT[d["fmt"]][0])
                   for i, d in enumerate(descs)]
        ti = _choose(self.root, "Replace texture in file #%d" % idx,
                     "Which texture?", choices)
        if ti is None:
            return
        d = descs[ti]
        path = filedialog.askopenfilename(
            title="Replacement image for %dx%d %s"
                  % (d["w"], d["h"], vc_texture.GPU_FMT[d["fmt"]][0]),
            filetypes=[("Images", "*.png *.dds *.tga *.bmp"), ("All", "*.*")])
        if not path:
            return
        if not messagebox.askyesno(
                "Write to the game?",
                "This edits the ISO in place.\n\n"
                "File #%d, texture %d (%dx%d %s)\n%s\n\n"
                "Every write is journalled — 'Revert ALL mods' restores the disc "
                "byte-for-byte. Continue?"
                % (idx, ti, d["w"], d["h"], vc_texture.GPU_FMT[d["fmt"]][0],
                   os.path.basename(path))):
            return
        self._start_worker(self._do_replace_texture, idx, ti, path)

    def _do_replace_texture(self, idx, ti, path):
        import numpy as np
        from PIL import Image
        img = np.array(Image.open(path).convert("RGBA"))
        self._logln("Replacing file #%d texture %d from %s …"
                    % (idx, ti, os.path.basename(path)))
        self._logln("  (recompressing the whole resource — large assets take a while)")
        info = vc_write.replace_texture(self.iso_var.get(), idx, ti, img)
        self._logln("  OK  %s %s   %d -> %d bytes (slot %d)"
                    % (info["dims"], info["format"], info["orig_bytes"],
                       info["new_bytes"], info["slot"]))
        self._logln("  journal: %s" % info["journal"])
        self.q.put(("mod", "modified: file #%d tex %d" % (idx, ti)))

    def _replace_audio(self):
        if not self._require_write():
            return
        idx = self._selected_index()
        if idx is None:
            return
        e = self.arc.files[idx]
        if self.arc.read_file_head(e, 4)[:4] != b"\x08\x00\x00\x00":
            messagebox.showerror(
                "Not an audio stream",
                "File #%d is not an 08000000 XMA2 stream." % idx)
            return
        path = filedialog.askopenfilename(
            title="Replacement XMA2 clip (slot holds %d bytes)" % e.size,
            filetypes=[("XMA2 / RIFF-XMA", "*.xma *.wav"), ("All", "*.*")])
        if not path:
            return
        if not messagebox.askyesno(
                "Write to the game?",
                "This edits the ISO in place.\n\nFile #%d  (slot %d bytes)\n%s\n\n"
                "The clip must already be XMA2 — there is no XMA encoder outside "
                "the Xbox 360 XDK, so PCM .wav files are rejected.\n\n"
                "'Revert ALL mods' restores the disc byte-for-byte. Continue?"
                % (idx, e.size, os.path.basename(path))):
            return
        self._start_worker(self._do_replace_audio, idx, path)

    def _do_replace_audio(self, idx, path):
        data = vc_write.xma_from_file(path)
        self._logln("Replacing audio file #%d with %s (%d bytes) …"
                    % (idx, os.path.basename(path), len(data)))
        info = vc_write.replace_audio(self.iso_var.get(), idx, data)
        self._logln("  OK  %d packets into a %d byte slot"
                    % (info["packets"], info["slot"]))
        self.q.put(("mod", "modified: audio #%d" % idx))

    def _revert_mods(self):
        if not self._require_write():
            return
        p = vc_write.Patcher(self.iso_var.get())
        n = p.pending()
        p.close()
        if not n:
            messagebox.showinfo("Nothing to revert", "No journalled changes.")
            return
        if not messagebox.askyesno(
                "Revert everything?",
                "Restore %d modified region(s) to their original bytes?" % n):
            return
        self._start_worker(self._do_revert)

    def _do_revert(self):
        n = vc_write.revert(self.iso_var.get())
        self._logln("Reverted %d region(s) — the disc is back to its original bytes." % n)
        self.q.put(("mod", ""))

    def _extract_textures(self):
        sel = self.tree.selection()
        idxs = [int(s) for s in sel] if sel else [e.index for e, _, _ in self.rows]
        self._start_worker(self._do_textures, idxs)

    def _do_textures(self, idxs):
        d = os.path.join(self._outdir(), "textures")
        os.makedirs(d, exist_ok=True)
        total = 0
        for idx in idxs:
            e = self.arc.files[idx]
            data = self.arc.read_file(e)
            if data[:4] not in (b"\xff\x3b\xef\x94", b"\x0e\x48\x37\xc3"):
                continue
            try:
                dec, bounds = vc_texture.decompress_with_bounds(data)
                res, pbase = vc_texture.extract_textures(dec, bounds)
            except Exception:
                res = []
            for desc, fourcc, dds_bytes in res:
                # Name by the offset within the pixel resource, not the
                # group-relative one -- offsets restart per group, so the
                # relative value collides between textures in the same file.
                pos = desc["base"] + desc["off"] - bounds[-1][0]
                name = "%05d_%07X_%dx%d_%s.dds" % (idx, pos, desc["w"],
                                                   desc["h"], fourcc)
                with open(os.path.join(d, name), "wb") as f:
                    f.write(dds_bytes)
                total += 1
            if res:
                self.q.put(("log", "  #%d: %d textures" % (idx, len(res))))
        self.q.put(("log", "Textures done: %d .dds files -> %s" % (total, d)))

    def _extract_wav(self):
        sel = self.tree.selection()
        idxs = [int(s) for s in sel] if sel else [e.index for e, _, _ in self.rows]
        self._start_worker(self._do_wav, idxs)

    def _do_wav(self, idxs):
        d = os.path.join(self._outdir(), "audio")
        os.makedirs(d, exist_ok=True)
        n = 0
        for idx in idxs:
            e = self.arc.files[idx]
            head = self.arc.read_file_head(e, 4)
            if head[:4] != b"\x08\x00\x00\x00":
                continue
            data = self.arc.read_file(e)
            wav = os.path.join(d, "%05d.wav" % idx)
            self.q.put(("log", "  #%d: decoding XMA (%.1f MB)..." % (idx, e.size/1e6)))
            ok, ch, msg = xma_extract.extract_to_wav(data, wav)
            if ok:
                n += 1
                self.q.put(("log", "    -> %d-channel WAV" % ch))
            else:
                self.q.put(("log", "    FAIL: %s" % msg))
        self.q.put(("log", "Audio done: %d WAV files -> %s" % (n, d)))

    def _do_files(self, idxs):
        d = self._outdir()
        mode = self.mode_var.get()
        by_index = {e.index: (e, lbl, ext) for e, lbl, ext in self.rows}
        self.q.put(("progmax", len(idxs)))
        ok = fail = 0
        for i, idx in enumerate(idxs):
            e, label, ext = by_index[idx]
            try:
                data = self.arc.read_file(e)
                name = "%05d_0x%08X" % (idx, e.crc)
                out = None
                if mode == "decompressed" and (data[:4] == b"\xff\x3b\xef\x94"
                                               or data[:4] == b"\x0e\x48\x37\xc3"):
                    out, blocks = vc_extract.extract_file(data)
                if out:                       # decompressed successfully with content
                    op = os.path.join(d, name + ".dec.iff")
                    with open(op, "wb") as o:
                        o.write(out)
                else:                         # raw (or block-less stub)
                    op = os.path.join(d, name + "." + ext)
                    with open(op, "wb") as o:
                        o.write(data)
                ok += 1
            except Exception as ex:
                fail += 1
                self.q.put(("log", "  #%d FAIL: %s" % (idx, ex)))
            if (i + 1) % 25 == 0 or i + 1 == len(idxs):
                self.q.put(("prog", i + 1))
                self.q.put(("log", "  ...%d/%d (ok=%d fail=%d)" %
                            (i + 1, len(idxs), ok, fail)))
        self.q.put(("log", "Done. %d extracted, %d failed -> %s" % (ok, fail, d)))


def main():
    root = tk.Tk()
    try:
        ttk.Style().theme_use("vista")
    except Exception:
        pass
    ExtractorGUI(root)
    root.mainloop()


if __name__ == "__main__":
    main()
