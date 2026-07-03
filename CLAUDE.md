# Creator Studio (`creator-studio`)

Windowed desktop hub for Omri's creator workflow, on the **home PC**. The rebuilt,
screen-first successor to the old tray-only "Studio Flow" (renamed in-app to
**Creator Studio**, v2.0.0). Its star feature is **DJI Osmo Pocket auto-import**.

> Renamed 2026-07-02 from "Studio Flow": app, folder, GitHub repo, and git remote
> are all **`creator-studio`** now. Pre-rebuild tray build recoverable at git tag
> `studioflow-tray-final`. (If the local folder is somehow still
> `mic-volume-tracker`, a Windows lock blocked the rename — reboot and
> `mv mic-volume-tracker creator-studio`.)

## What it does
- **Osmo import (the reason it exists):** plug in the camera → a notification +
  the window opens → review the auto-detected **sessions** → Start. It copies the
  full-quality `.MP4`s (skips the `.LRF` proxy + `.SRT` sidecar) to a dated folder,
  **losslessly merges** clips the camera auto-split into one recording, and
  optionally **transcribes Hebrew**. Never re-copies an already-imported clip.
- **Silent mic lock:** keeps the mic pinned at a target % in the background — no
  more naggy floating overlay. Status shows on the home screen.
- **Tiles for the rest:** launch DaVinci Resolve + its Control Center dashboard,
  Tailscale E: drive mapping, YouTube loudness check + normalize.

## Architecture
A native window (**pywebview + WebView2**) whose UI is an HTML/CSS/JS app served by
an **embedded Flask** backend on `127.0.0.1:5015`. Background threads run silently:
the mic lock and the Osmo camera-watcher. **Closing the window hides it to a small
tray icon** (background threads keep running); quit from the tray.

Why this shape: the "screen" is a real browser-grade UI (matches Omri's other
tools); the tray persists background detection; heavy work runs as jobs the UI
polls — the exact `jobs.py` pattern from **video-prep**.

## Files
| File | Role |
|---|---|
| `tracker.py` | Entry point: Flask thread + `MicMonitor` + camera watcher + tray + pywebview window. |
| `server.py` | Flask app — serves `web/` and the JSON API. |
| `settings_store.py` | Shared paths, `settings.json` (in `%LOCALAPPDATA%\StudioFlow`), ffmpeg resolution, app constants. |
| `mediatools.py` | `probe` (+ `creation_time`), recursive `find_videos`, lossless `join` (concat demuxer + `-c copy`) + progress. Ported from video-prep `fftools.py`. |
| `osmo_import.py` | Camera detect, DCIM scan (skip `.LRF`/`.SRT`), **session grouping**, idempotent copy, import orchestration (copy → merge → transcribe). |
| `mic.py` | `get/set_mic_volume`, headless `MicMonitor` (silent lock loop). |
| `audio_tools.py` | ffmpeg ebur128 analyze + two-pass loudnorm normalize + YouTube verdict. |
| `davinci.py` | Resolve launch, project create, dashboard/bot/watch launchers, Tailscale drive map. |
| `jobs.py` | In-memory job registry + progress (UI polls `GET /api/job/<id>`). |
| `web/` | `index.html`, `app.css`, `app.js` — RTL "control-room" UI (Rubik/Heebo, amber+teal on near-black). |
| `tests/test_osmo.py` | Unit tests for the pure grouping/timeline/manifest logic. |

## How session grouping works (the "smart merge")
DJI splits one long recording into ~4 GB chunks that are **contiguous in time**;
separate takes leave a real gap. `osmo_import`:
1. gives every clip a consistent `start`/`end` — from the camera's embedded
   `creation_time` if all clips have it, else from filesystem `mtime` − duration
   (`assign_timeline`);
2. groups consecutive clips into one session when the gap is ≤ `SESSION_MAX_GAP`
   (5 s) **and** video params (w×h, fps, codec) match (`group_sessions`).
So it works regardless of the exact P4 filename convention. Threshold is tunable.
**Idempotency:** an `imported.json` manifest in the backup root, keyed by
`filename|size`; already-imported clips are greyed and never re-copied.

## Reuse (one truth per topic)
- Lossless merge command is the same concat-demuxer `-c copy` as
  `video-prep/fftools.py` — `mediatools.join` carries it so the packaged `.exe` is
  self-contained (no second process). If strict de-dup is wanted later, extract a
  shared helper.
- Transcription **shells out** to the davinci-automation CLI (unchanged):
  `py -3.10 "E:\DaVinci Automation\scripts\transcription\transcribe-hebrew.py" <mp4> --output-dir <dest>`.

## Run / build
- **Dev:** `run.bat` → opens the window (`py -3.10 tracker.py`). Server: `localhost:5015`.
- **Tests:** `py -3.10 -m pytest tests/ -q`.
- **Build installer:** `build.bat` → PyInstaller (`mic_tracker.spec`, UPX off — it
  corrupts the WebView2/.NET DLLs) → Inno Setup (`installer.iss`) →
  `dist\installer\CreatorStudio-Setup-2.0.0.exe`. New app identity
  (GUID `7C1E9A44-…`); its installer kills the old `StudioFlow.exe` and removes the
  old "Studio Flow" startup shortcut so only the new app auto-starts.

## Config / defaults
- Osmo backup root default: `E:\Video Projects\Osmo Imports\` (dated subfolder per
  import). Change in the import screen (persisted as `osmo_backup_root`).
- Default import actions all ON: merge sessions, transcribe Hebrew, keep originals.
- Mic: first-run default locked at 90% (`mic_locked`/`mic_lock_target` persisted).

## Notes / follow-ups
- Requires the **DJI in USB mass-storage (drive-letter) mode**, not MTP. Detection
  excludes REMOTE drives so the Tailscale `E:` mapping is never mistaken for a camera.
- ✅ **Verified against a real Osmo Pocket 4 card (2026-07-03):** filenames
  (`DJI_<ts>_NNNN_D.MP4`) group into sessions correctly and the lossless merge is
  exact. `SESSION_MAX_GAP` (5 s) left as-is — no adjustment needed.
- **Transcription is resilient now (2026-07-03):** the import's transcribe phase
  retries transient failures with backoff (`transcribe_one`), and because the
  idempotent import won't re-reach transcription for already-copied clips, the done
  screen shows a **🎙️ תמלל (N)** button that re-transcribes the missing outputs via
  `POST /api/osmo/transcribe` → `osmo_import.transcribe_files`.
- **Transcription GPU requirement:** `transcribe-hebrew.py` needs the CUDA DLLs from
  the `nvidia-*-cu12` pip packages **including `nvidia-cuda-runtime-cu12`** (the
  `cudart64_12.dll` package — easy to miss). Its `add_nvidia_dll_dirs()` now both
  adds the dirs *and* **preloads** the DLLs by full path — ctranslate2 loads cuBLAS
  lazily at encode time and ignores `add_dll_directory`, so preloading is required or
  the GPU dies mid-transcribe with `Library cublas64_12.dll ... cannot be loaded`
  *after* the 3 GB model already loaded. Long footage is still slow (GPU minutes).
