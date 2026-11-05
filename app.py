#!/usr/bin/env python3
"""
Simple batch video downloader (GUI wrapper around yt-dlp).

Requirements:
    pip install -U yt-dlp
    ffmpeg installed and on PATH (needed to merge best video + best audio)

Run:
    python yt_batch_downloader.py
"""
import os
import queue
import threading
import tkinter as tk
from tkinter import filedialog, ttk
from tkinter.scrolledtext import ScrolledText

try:
    import yt_dlp
    from yt_dlp.utils import DownloadCancelled
except ImportError:
    raise SystemExit("yt-dlp is not installed. Run: pip install -U yt-dlp")

# Use the ffmpeg binary bundled via imageio-ffmpeg when available (packaged
# builds); otherwise fall back to whatever ffmpeg is on PATH.
try:
    import imageio_ffmpeg
    FFMPEG_PATH = imageio_ffmpeg.get_ffmpeg_exe()
except Exception:  # noqa: BLE001
    FFMPEG_PATH = None

QUALITIES = ["Best available", "2160p (4K)", "1440p", "1080p", "720p", "480p"]
HEIGHTS = {"Best available": None, "2160p (4K)": 2160, "1440p": 1440,
           "1080p": 1080, "720p": 720, "480p": 480}
VIDEO_CONTAINERS = ["mkv", "mp4"]
AUDIO_FORMATS = ["wav", "flac", "mp3", "m4a"]


class App(tk.Tk):
    def __init__(self):
        super().__init__()
        self.title("Batch Video Downloader")
        self.geometry("760x640")
        self.log_q = queue.Queue()
        self.cancel_flag = threading.Event()
        self.worker = None

        self.out_dir = tk.StringVar(value=os.path.join(os.path.expanduser("~"), "Downloads"))
        self.quality = tk.StringVar(value=QUALITIES[0])
        self.container = tk.StringVar(value="mp4")
        self.audio_only = tk.BooleanVar(value=False)
        self.audio_fmt = tk.StringVar(value="wav")
        self.no_playlist = tk.BooleanVar(value=False)

        self._build()
        self.after(100, self._drain_log)

    def _build(self):
        pad = {"padx": 10, "pady": 4}

        ttk.Label(self, text="Paste URLs (one per line — videos, playlists or channels):").pack(anchor="w", **pad)
        self.urls = ScrolledText(self, height=10)
        self.urls.pack(fill="x", **pad)

        opts = ttk.Frame(self)
        opts.pack(fill="x", **pad)

        ttk.Label(opts, text="Quality:").grid(row=0, column=0, sticky="w")
        ttk.Combobox(opts, textvariable=self.quality, values=QUALITIES,
                     state="readonly", width=16).grid(row=0, column=1, padx=6)

        ttk.Label(opts, text="Container:").grid(row=0, column=2, sticky="w", padx=(12, 0))
        ttk.Combobox(opts, textvariable=self.container, values=VIDEO_CONTAINERS,
                     state="readonly", width=6).grid(row=0, column=3, padx=6)

        ttk.Checkbutton(opts, text="Audio only:", variable=self.audio_only).grid(row=1, column=0, sticky="w", pady=6)
        ttk.Combobox(opts, textvariable=self.audio_fmt, values=AUDIO_FORMATS,
                     state="readonly", width=16).grid(row=1, column=1, padx=6)

        ttk.Checkbutton(opts, text="Single video only (ignore playlist in URL)",
                        variable=self.no_playlist).grid(row=1, column=2, columnspan=2, sticky="w", padx=(12, 0))

        folder = ttk.Frame(self)
        folder.pack(fill="x", **pad)
        ttk.Label(folder, text="Save to:").pack(side="left")
        ttk.Entry(folder, textvariable=self.out_dir).pack(side="left", fill="x", expand=True, padx=6)
        ttk.Button(folder, text="Browse…", command=self._browse).pack(side="left")

        btns = ttk.Frame(self)
        btns.pack(fill="x", **pad)
        self.start_btn = ttk.Button(btns, text="Download", command=self._start)
        self.start_btn.pack(side="left")
        self.cancel_btn = ttk.Button(btns, text="Cancel", command=self._cancel, state="disabled")
        self.cancel_btn.pack(side="left", padx=6)

        self.progress = ttk.Progressbar(self, maximum=100)
        self.progress.pack(fill="x", **pad)
        self.status = ttk.Label(self, text="Idle")
        self.status.pack(anchor="w", padx=10)

        self.log = ScrolledText(self, height=12, state="disabled")
        self.log.pack(fill="both", expand=True, **pad)

    # ---- UI helpers ----
    def _browse(self):
        d = filedialog.askdirectory(initialdir=self.out_dir.get())
        if d:
            self.out_dir.set(d)

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
                    self.progress["value"] = val
                elif kind == "status":
                    self.status.configure(text=val)
                elif kind == "done":
                    self.start_btn.configure(state="normal")
                    self.cancel_btn.configure(state="disabled")
        except queue.Empty:
            pass
        self.after(100, self._drain_log)

    # ---- download logic ----
    def _start(self):
        urls = [u.strip() for u in self.urls.get("1.0", "end").splitlines() if u.strip()]
        if not urls:
            self._log("No URLs provided.")
            return
        os.makedirs(self.out_dir.get(), exist_ok=True)
        self.cancel_flag.clear()
        self.start_btn.configure(state="disabled")
        self.cancel_btn.configure(state="normal")
        opts = dict(
            out=self.out_dir.get(),
            quality=self.quality.get(),
            container=self.container.get(),
            audio_only=self.audio_only.get(),
            audio_fmt=self.audio_fmt.get(),
            no_playlist=self.no_playlist.get(),
        )
        self.worker = threading.Thread(target=self._run, args=(urls, opts), daemon=True)
        self.worker.start()

    def _cancel(self):
        self.cancel_flag.set()
        self._log("Cancelling after current chunk…")

    def _hook(self, d):
        if self.cancel_flag.is_set():
            raise DownloadCancelled()
        if d["status"] == "downloading":
            total = d.get("total_bytes") or d.get("total_bytes_estimate")
            if total:
                self.log_q.put(("progress", d["downloaded_bytes"] * 100 / total))
            speed = d.get("_speed_str", "").strip()
            eta = d.get("_eta_str", "").strip()
            self.log_q.put(("status", f"Downloading… {d.get('_percent_str', '').strip()}  {speed}  ETA {eta}"))
        elif d["status"] == "finished":
            self.log_q.put(("progress", 100))
            self.log_q.put(("status", "Processing (merging / converting)…"))

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
            if h:
                opts["format"] = f"bv*[height<={h}]+ba/b[height<={h}]/b"
            else:
                opts["format"] = "bv*+ba/b"
            opts["merge_output_format"] = o["container"]
        return opts

    def _run(self, urls, o):
        ok = 0
        for i, url in enumerate(urls, 1):
            if self.cancel_flag.is_set():
                break
            self._log(f"\n[{i}/{len(urls)}] {url}")
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
        self.log_q.put(("status", f"Finished — {ok}/{len(urls)} URLs completed"))
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
        self.fn(f"WARN: {msg}")

    def error(self, msg):
        self.fn(f"ERROR: {msg}")


if __name__ == "__main__":
    App().mainloop()
