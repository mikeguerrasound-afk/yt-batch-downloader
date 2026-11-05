# Batch Video Downloader

A small desktop app for downloading videos in high quality, in batch. Paste a list of URLs (videos, playlists or channels), pick a quality, and go. Built on [yt-dlp](https://github.com/yt-dlp/yt-dlp); ffmpeg is bundled, so there is nothing else to install.

## Download

Grab the latest build from the [Releases](../../releases/latest) page:

- **Windows:** `BatchVideoDownloader-windows.zip` → unzip and run `BatchVideoDownloader.exe`
- **macOS:** `BatchVideoDownloader-macos.zip` → unzip and open `BatchVideoDownloader.app`

The builds are unsigned. Windows SmartScreen may show "Unknown publisher" (More info → Run anyway). On macOS, right-click the app → Open the first time, or run `xattr -cr BatchVideoDownloader.app`.

## Features

- Batch input, one URL per line; a failed item doesn't stop the queue
- Quality: best available, 4K, 1440p, 1080p, 720p, 480p
- Video as MP4 or MKV, or audio-only as WAV, FLAC, MP3 or M4A
- Playlists saved into their own folders with numbered files
- Progress bar, log and cancel

## Run from source

```
pip install -r requirements.txt
python app.py
```

Requires Python 3.9+ with Tkinter.

## Releasing

Pushing a tag builds both apps and attaches them to a GitHub release:

```
git tag v1.0.0
git push origin v1.0.0
```

YouTube changes often, so if downloads start failing, rebuild with the latest yt-dlp (re-run the workflow or push a new tag).

## Notice

Only download content you own or have permission to download. Respect the terms of the sites you use.
