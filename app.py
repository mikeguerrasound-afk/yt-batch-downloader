#!/usr/bin/env python3
"""Batch video downloader: a quiet, dark GUI around yt-dlp.

Run from source:
    pip install -r requirements.txt
    python app.py
"""
import os
import queue
import subprocess
import sys
import threading
import tkinter as tk
from tkinter import filedialog

try:
    import customtkinter as ctk
except ImportError:
    raise SystemExit("customtkinter is not installed. Run: pip install -r requirements.txt")

try:
    import yt_dlp
    from yt_dlp.utils import DownloadCancelled
except ImportError:
    raise SystemExit("yt-dlp is not installed. Run: pip install -U yt-dlp")

# Use the ffmpeg bundled via imageio-ffmpeg when available (packaged builds);
# otherwise fall back to whatever ffmpeg is on PATH.
try:
    import imageio_ffmpeg
    FFMPEG_PATH = imageio_ffmpeg.get_ffmpeg_exe()
except Exception:  # noqa: BLE001
    FFMPEG_PATH = None

# ---- design tokens -------------------------------------------------------
BG = "#14171C"          # window
PANEL = "#1C2027"       # inputs
PANEL_HI = "#252A33"    # controls, hover
LINE = "#2C323C"        # hairlines
INK = "#101318"         # log well
TEXT = "#E6E8EB"
MUTED = "#8A93A3"
ACCENT = "#3F74E0"
ACCENT_HOVER = "#5586EA"
# Level-meter colours (functional, like a console meter)
METER_OFF = "#232831"
GREEN = "#3DDC97"
AMBER = "#F2B544"
RED = "#EF5B5B"

if sys.platform.startswith("win"):
    UI_FONT, MONO = "Segoe UI", "Consolas"
elif sys.platform == "darwin":
    UI_FONT, MONO = "Helvetica Neue", "Menlo"
else:
    UI_FONT, MONO = "DejaVu Sans", "DejaVu Sans Mono"

QUALITIES = ["Best", "4K", "1440p", "1080p", "720p", "480p"]
HEIGHTS = {"Best": None, "4K": 2160, "1440p": 1440, "1080p": 1080, "720p": 720, "480p": 480}
VIDEO_CONTAINERS = ["mp4", "mkv"]
AUDIO_FORMATS = ["wav", "flac", "mp3", "m4a"]
PLACEHOLDER = "Paste links, one per line.\nVideos, playlists and channels all work."
PADX = 32


def font(size, weight="normal", family=None):
    return ctk.CTkFont(family=family or UI_FONT, size=size, weight=weight)


class Meter(tk.Canvas):
    """Segmented level meter used as the progress indicator."""
    SEGMENTS = 48

    def __init__(self, master, **kw):
        super().__init__(master, height=12, highlightthickness=0, bd=0, bg=BG, **kw)
        self.value = 0.0
        self.bind("<Configure>", lambda _e: self._redraw())

    def set(self, v):
        v = max(0.0, min(1.0, v))
        if v == self.value:
            return
        self.value = v
        self._redraw()

    def _redraw(self):
        self.delete("all")
        w, h = self.winfo_width(), self.winfo_height()
        if w < 20:
            return
        n, gap = self.SEGMENTS, 3
        seg = (w - gap * (n - 1)) / n
        lit = round(self.value * n)
        for i in range(n):
            x0 = i * (seg + gap)
            if i < lit:
                frac = i / (n - 1)
                col = GREEN if frac < 0.65 else AMBER if frac < 0.88 else RED
            else:
                col = METER_OFF
            self.create_rectangle(x0, 0, x0 + seg, h, fill=col, outline="")


class App(ctk.CTk):
    def __init__(self):
        super().__init__(fg_color=BG)
        ctk.set_appearance_mode("dark")
        self.title("Batch video downloader")
        self.geometry("860x780")
        self.minsize(720, 660)

        self.log_q = queue.Queue()
        self.cancel_flag = threading.Event()
        self.worker = None
        self._placeholder_on = False

        self.out_dir = tk.StringVar(value=os.path.join(os.path.expanduser("~"), "Downloads"))
        self.quality = tk.StringVar(value="Best")
        self.fmt = tk.StringVar(value="mp4")
        self.no_playlist = tk.BooleanVar(value=False)

        self._build()
        self._show_placeholder()
        self.after(100, self._drain_log)

    # ---- layout ----------------------------------------------------------
    def _build(self):
        self.grid_columnconfigure(0, weight=1)
        self.grid_rowconfigure(5, weight=1)

        ctk.CTkLabel(self, text="Batch video downloader", font=font(26, "bold"),
                     text_color=TEXT, anchor="w").grid(row=0, column=0, sticky="ew", padx=PADX, pady=(28, 18))

        # links
        head = ctk.CTkFrame(self, fg_color="transparent")
        head.grid(row=1, column=0, sticky="ew", padx=PADX)
        head.grid_columnconfigure(0, weight=1)
        ctk.CTkLabel(head, text="Links", font=font(13, "bold"), text_color=TEXT,
                     anchor="w").grid(row=0, column=0, sticky="w")
        self.count = ctk.CTkLabel(head, text="0 links", font=font(12), text_color=MUTED)
        self.count.grid(row=0, column=1, padx=(0, 12))
        ctk.CTkButton(head, text="Paste", width=60, height=26, corner_radius=8, font=font(12),
                      fg_color=PANEL_HI, hover_color=LINE, text_color=TEXT,
                      command=self._paste).grid(row=0, column=2)

        self.urls = ctk.CTkTextbox(self, height=150, corner_radius=14, fg_color=PANEL,
                                   border_width=1, border_color=LINE, text_color=TEXT,
                                   font=font(12, family=MONO), wrap="none")
        self.urls.grid(row=2, column=0, sticky="ew", padx=PADX, pady=(8, 20))
        self.urls.tag_config("ph", foreground=MUTED)
        self.urls.bind("<FocusIn>", lambda _e: self._hide_placeholder(), add=True)
        self.urls.bind("<FocusOut>", lambda _e: self._show_placeholder(), add=True)
        self.urls.bind("<KeyRelease>", lambda _e: self._update_count(), add=True)

        # settings
        opts = ctk.CTkFrame(self, fg_color="transparent")
        opts.grid(row=3, column=0, sticky="ew", padx=PADX, pady=(0, 18))
        opts.grid_columnconfigure(1, weight=1)

        def label(row, text):
            ctk.CTkLabel(opts, text=text, font=font(13), text_color=MUTED, width=80,
                         anchor="w").grid(row=row, column=0, sticky="w", pady=7)

        seg = dict(height=32, corner_radius=8, border_width=0, font=font(13),
                   fg_color=PANEL, selected_color=ACCENT, selected_hover_color=ACCENT_HOVER,
                   unselected_color=PANEL, unselected_hover_color=PANEL_HI,
                   text_color=TEXT, text_color_disabled=MUTED)

        label(0, "Format")
        fmt_row = ctk.CTkFrame(opts, fg_color="transparent")
        fmt_row.grid(row=0, column=1, sticky="w")
        self.mode_seg = ctk.CTkSegmentedButton(fmt_row, values=["Video", "Audio"],
                                               command=self._mode_changed, **seg)
        self.mode_seg.set("Video")
        self.mode_seg.pack(side="left")
        self.fmt_menu = ctk.CTkOptionMenu(fmt_row, values=VIDEO_CONTAINERS, variable=self.fmt,
                                          width=96, height=32, corner_radius=8, font=font(13),
                                          dropdown_font=font(13), fg_color=PANEL_HI,
                                          button_color=PANEL_HI, button_hover_color=LINE,
                                          dropdown_fg_color=PANEL, dropdown_hover_color=PANEL_HI,
                                          text_color=TEXT, dropdown_text_color=TEXT)
        self.fmt_menu.pack(side="left", padx=(10, 0))

        label(1, "Quality")
        self.quality_seg = ctk.CTkSegmentedButton(opts, values=QUALITIES, variable=self.quality, **seg)
        self.quality_seg.grid(row=1, column=1, sticky="w")

        label(2, "Save to")
        save_row = ctk.CTkFrame(opts, fg_color="transparent")
        save_row.grid(row=2, column=1, sticky="ew")
        save_row.grid_columnconfigure(0, weight=1)
        ctk.CTkEntry(save_row, textvariable=self.out_dir, height=34, corner_radius=10, font=font(12),
                     fg_color=PANEL, border_color=LINE, border_width=1,
                     text_color=TEXT).grid(row=0, column=0, sticky="ew")
        ctk.CTkButton(save_row, text="Browse", width=76, height=34, corner_radius=10, font=font(13),
                      fg_color=PANEL_HI, hover_color=LINE, text_color=TEXT,
                      command=self._browse).grid(row=0, column=1, padx=(10, 0))

        ctk.CTkSwitch(opts, text="Ignore the playlist when a link has one", variable=self.no_playlist,
                      font=font(13), text_color=MUTED, progress_color=ACCENT, fg_color=PANEL_HI,
                      button_color=TEXT, button_hover_color=TEXT, switch_width=40,
                      switch_height=20).grid(row=3, column=1, sticky="w", pady=(10, 0))

        # actions + meter
        act = ctk.CTkFrame(self, fg_color="transparent")
        act.grid(row=4, column=0, sticky="ew", padx=PADX, pady=(0, 18))
        act.grid_columnconfigure(0, weight=1)

        self.start_btn = ctk.CTkButton(act, text="Download", height=46, corner_radius=10,
                                       font=font(15, "bold"), fg_color=ACCENT, hover_color=ACCENT_HOVER,
                                       text_color="#FFFFFF", command=self._start)
        self.start_btn.grid(row=0, column=0, sticky="ew")
        ghost = dict(height=46, corner_radius=10, font=font(14), fg_color="transparent",
                     border_width=1, border_color=LINE, hover_color=PANEL,
                     text_color=TEXT, text_color_disabled=MUTED)
        self.cancel_btn = ctk.CTkButton(act, text="Cancel", width=92, state="disabled",
                                        command=self._cancel, **ghost)
        self.cancel_btn.grid(row=0, column=1, padx=(10, 0))
        self.open_btn = ctk.CTkButton(act, text="Open folder", width=116, command=self._open_folder, **ghost)
        self.open_btn.grid(row=0, column=2, padx=(10, 0))

        self.meter = Meter(act)
        self.meter.grid(row=1, column=0, columnspan=3, sticky="ew", pady=(18, 8))
        info = ctk.CTkFrame(act, fg_color="transparent")
        info.grid(row=2, column=0, columnspan=3, sticky="ew")
        info.grid_columnconfigure(0, weight=1)
        self.status = ctk.CTkLabel(info, text="Ready", font=font(13), text_color=MUTED, anchor="w")
        self.status.grid(row=0, column=0, sticky="ew")
        self.percent = ctk.CTkLabel(info, text="", font=font(12, family=MONO), text_color=MUTED)
        self.percent.grid(row=0, column=1)

        # log
        self.log = ctk.CTkTextbox(self, corner_radius=14, fg_color=INK, border_width=1, border_color=LINE,
                                  text_color=MUTED, font=font(11, family=MONO), state="disabled")
        self.log.grid(row=5, column=0, sticky="nsew", padx=PADX, pady=(0, 28))

    # ---- placeholder / input helpers --------------------------------------
    def _show_placeholder(self):
        if not self._placeholder_on and not self.urls.get("1.0", "end").strip():
            self.urls.insert("1.0", PLACEHOLDER, "ph")
            self._placeholder_on = True
            self._update_count()

    def _hide_placeholder(self):
        if self._placeholder_on:
            self.urls.delete("1.0", "end")
            self._placeholder_on = False

    def _get_urls(self):
        if self._placeholder_on:
            return []
        return [u.strip() for u in self.urls.get("1.0", "end").splitlines() if u.strip()]

    def _update_count(self):
        n = len(self._get_urls())
        self.count.configure(text=f"{n} link" + ("" if n == 1 else "s"))

    def _paste(self):
        try:
            text = self.clipboard_get().strip()
        except tk.TclError:
            return
        if not text:
            return
        self._hide_placeholder()
        current = self.urls.get("1.0", "end").rstrip("\n")
        self.urls.insert("end", ("\n" if current else "") + text)
        self._update_count()

    def _mode_changed(self, value):
        if value == "Video":
            self.fmt_menu.configure(values=VIDEO_CONTAINERS)
            self.fmt.set("mp4")
            self.quality_seg.configure(state="normal")
        else:
            self.fmt_menu.configure(values=AUDIO_FORMATS)
            self.fmt.set("wav")
            self.quality_seg.configure(state="disabled")

    def _browse(self):
        d = filedialog.askdirectory(initialdir=self.out_dir.get())
        if d:
            self.out_dir.set(d)

    def _open_folder(self):
        path = self.out_dir.get()
        if not os.path.isdir(path):
            return
        if sys.platform.startswith("win"):
            os.startfile(path)  # noqa: S606
        elif sys.platform == "darwin":
            subprocess.Popen(["open", path])
        else:
            subprocess.Popen(["xdg-open", path])

    # ---- thread-safe UI updates -------------------------------------------
    def _log(self, msg):
        self.log_q.put(("log", msg))

    def _drain_log(self):
        try:
            while True:
                kind, val = self.log_q.get_nowait()
                if kind == "log":
                    self.log.configure(state="normal")
                    self.log.insert("end", val + "\n")
                    self.log.see("end")
                    self.log.configure(state="disabled")
                elif kind == "progress":
                    self.meter.set(val / 100)
                    self.percent.configure(text=f"{val:.0f}%" if val else "")
                elif kind == "status":
                    text, color = val
                    self.status.configure(text=text, text_color=color)
                elif kind == "done":
                    self.start_btn.configure(state="normal")
                    self.cancel_btn.configure(state="disabled")
        except queue.Empty:
            pass
        self.after(100, self._drain_log)

    # ---- download logic ---------------------------------------------------
    def _start(self):
        urls = self._get_urls()
        if not urls:
            self.status.configure(text="Paste at least one link to start.", text_color=AMBER)
            return
        os.makedirs(self.out_dir.get(), exist_ok=True)
        self.cancel_flag.clear()
        self.start_btn.configure(state="disabled")
        self.cancel_btn.configure(state="normal")
        audio = self.mode_seg.get() == "Audio"
        opts = dict(
            out=self.out_dir.get(),
            quality=self.quality.get(),
            container=self.fmt.get(),
            audio_only=audio,
            audio_fmt=self.fmt.get(),
            no_playlist=self.no_playlist.get(),
        )
        self.status.configure(text=f"Starting {len(urls)} link" + ("" if len(urls) == 1 else "s") + "…",
                              text_color=MUTED)
        self.worker = threading.Thread(target=self._run, args=(urls, opts), daemon=True)
        self.worker.start()

    def _cancel(self):
        self.cancel_flag.set()
        self._log("Cancelling after the current chunk…")

    def _hook(self, d):
        if self.cancel_flag.is_set():
            raise DownloadCancelled()
        if d["status"] == "downloading":
            total = d.get("total_bytes") or d.get("total_bytes_estimate")
            if total:
                self.log_q.put(("progress", d["downloaded_bytes"] * 100 / total))
            speed = d.get("_speed_str", "").strip()
            eta = d.get("_eta_str", "").strip()
            self.log_q.put(("status", (f"Downloading   {speed}   ETA {eta}", TEXT)))
        elif d["status"] == "finished":
            self.log_q.put(("progress", 100))
            self.log_q.put(("status", ("Merging and converting…", MUTED)))

    def _ydl_opts(self, url, o):
        is_playlist = "list=" in url and not o["no_playlist"]
        name = "%(title)s [%(id)s].%(ext)s"
        if is_playlist:
            name = "%(playlist_title)s/%(playlist_index)03d - " + name
        elif "/@" in url or "/channel/" in url or "/c/" in url or "/user/" in url:
            name = "%(uploader)s/" + name

        opts = {
            "outtmpl": os.path.join(o["out"], name),
            "noplaylist": o["no_playlist"],
            "progress_hooks": [self._hook],
            "ignoreerrors": True,          # keep going if one item fails
            "retries": 10,
            "fragment_retries": 10,
            "concurrent_fragment_downloads": 4,
            "windowsfilenames": True,
            "logger": _Logger(self._log),
        }
        if FFMPEG_PATH:
            opts["ffmpeg_location"] = FFMPEG_PATH
        if o["audio_only"]:
            opts["format"] = "bestaudio/best"
            opts["postprocessors"] = [{
                "key": "FFmpegExtractAudio",
                "preferredcodec": o["audio_fmt"],
                "preferredquality": "0",
            }]
        else:
            h = HEIGHTS[o["quality"]]
            opts["format"] = f"bv*[height<={h}]+ba/b[height<={h}]/b" if h else "bv*+ba/b"
            opts["merge_output_format"] = o["container"]
        return opts

    def _run(self, urls, o):
        ok = 0
        for i, url in enumerate(urls, 1):
            if self.cancel_flag.is_set():
                break
            self._log(f"\n[{i}/{len(urls)}] {url}")
            self.log_q.put(("progress", 0))
            try:
                with yt_dlp.YoutubeDL(self._ydl_opts(url, o)) as ydl:
                    rc = ydl.download([url])
                if rc == 0:
                    ok += 1
            except DownloadCancelled:
                self._log("Cancelled.")
                break
            except Exception as e:  # noqa: BLE001
                self._log(f"Error: {e}")
        if self.cancel_flag.is_set():
            msg, color = "Cancelled.", AMBER
        elif ok == len(urls):
            msg, color = f"Done. {ok} of {len(urls)} downloaded.", GREEN
        else:
            msg, color = f"Finished with errors. {ok} of {len(urls)} downloaded; see the log.", AMBER
        self.log_q.put(("status", (msg, color)))
        self.log_q.put(("progress", 0))
        self.log_q.put(("done", None))


class _Logger:
    def __init__(self, fn):
        self.fn = fn

    def debug(self, msg):
        if not msg.startswith("[download]"):
            self.fn(msg)

    def info(self, msg):
        self.fn(msg)

    def warning(self, msg):
        self.fn(f"Warning: {msg}")

    def error(self, msg):
        self.fn(f"Error: {msg}")


if __name__ == "__main__":
    App().mainloop()
