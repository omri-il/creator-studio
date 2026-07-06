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
- Transcription **shells out** to one of two davinci-automation CLIs, chosen by
  the `transcribe_backend` setting (default **`vps`**):
  - **`vps`** (default) → `transcribe_via_vps.py <mp4> --output <dest>\<stem>.srt` —
    extracts a tiny 32 kbps MP3 and uploads it to the **whisper-agent** on the VPS
    (Tailscale `100.94.153.60:8080`, OpenAI Whisper, uses credits, no GPU here).
    Produces the `.srt` only.
  - **`local`** → `transcribe-hebrew.py <mp4> --output-dir <dest>` — GPU on this PC
    (ivrit-ai model; also writes `_transcription.txt`/`.json`/`_fillers.txt`).
  Routing lives in `osmo_import._run_transcribe`; both are picked in the import
  screen (segmented toggle) and via `POST /api/osmo/config`.

## Run / build
- **Dev:** `run.bat` → opens the window (`py -3.10 tracker.py`). Server: `localhost:5015`.
- **Tests:** `py -3.10 -m pytest tests/ -q`.
- **Build installer:** `build.bat` → PyInstaller (`mic_tracker.spec`, UPX off — it
  corrupts the WebView2/.NET DLLs) → Inno Setup (`installer.iss`) →
  `dist\installer\CreatorStudio-Setup-2.0.0.exe`. New app identity
  (GUID `7C1E9A44-…`); its installer kills the old `StudioFlow.exe` and removes the
  old "Studio Flow" startup shortcut so only the new app auto-starts. `build.bat`
  ends with `pause` — run the two steps directly when scripting headless.
- **⚠️ Windows Defender false-positive on every fresh build:** the newly built
  `CreatorStudio.exe` (PyInstaller bootloader) gets flagged `Trojan:Win32/Bearfoos.A!ml`
  — an ML heuristic, **not** real malware — and Defender quarantines it mid-install
  (the installer dies with *"CreateProcess failed; code 225 … contains a virus"*).
  Fix once per rebuilt exe: **Windows Security → Virus & threat protection →
  Protection history → the Bearfoos entry → Actions → Allow** (restores the exe +
  whitelists it). Each rebuild has a new hash and may need Allow again. Permanent
  fixes if this gets old: code-sign the exe, or just run from source (`run.bat`).

## Config / defaults
- Osmo backup root default: `E:\Video Projects\Osmo Imports\` (dated subfolder per
  import). Change in the import screen (persisted as `osmo_backup_root`).
- Default import actions all ON: merge sessions, transcribe Hebrew, keep originals.
- **Transcription engine defaults to the VPS whisper-agent** (`transcribe_backend`
  = `vps`; Omri's preference 2026-07-03). Switch to local GPU in the import screen
  (persisted). See Reuse for the two backends.
- Mic: first-run default **unlocked** (lock is opt-in from the app screen). When
  locked, target defaults to 90% (`mic_locked`/`mic_lock_target` persisted).

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
- **Local (GPU) backend requirement:** only the `local` backend needs the CUDA DLLs
  from the `nvidia-*-cu12` pip packages **including `nvidia-cuda-runtime-cu12`** (the
  `cudart64_12.dll` package — easy to miss). `transcribe-hebrew.py`'s
  `add_nvidia_dll_dirs()` now both adds the dirs *and* **preloads** the DLLs by full
  path — ctranslate2 loads cuBLAS lazily at encode time and ignores
  `add_dll_directory`, so preloading is required or the GPU dies mid-transcribe with
  `Library cublas64_12.dll ... cannot be loaded` *after* the 3 GB model loaded. The
  default `vps` backend needs none of this (VPS does the work). Long footage is slow
  on either (GPU minutes / VPS queue+credits).
- **VPS backend & long files:** OpenAI's Whisper API caps a request at 25 MB, but
  the whisper-agent handles this itself — `transcriber/whisper_client.py` auto-splits
  audio >24 MB into 10-min chunks and stitches the result. So even multi-hour Osmo
  recordings transcribe fine via `vps` (verified: the 2h46m file's full transcript
  came back). No local size handling needed.
- **Local backend teardown crash (cosmetic):** on long footage `transcribe-hebrew.py`
  finishes and writes the `.srt`/`.txt`/`.json` correctly, then the process can crash
  on CUDA/interpreter teardown with exit `0xC0000409` — a *false negative*. The
  outputs are valid. Another reason `vps` is the default. If reviving heavy use of
  `local`, treat "`.srt` exists" as success rather than trusting the exit code.
