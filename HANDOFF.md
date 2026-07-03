# Handoff — Creator Studio

Paste the block below into a fresh Claude Code session opened at
`C:\Users\omrii\Projects\creator-studio` to continue this work. Full architecture
and file map live in [CLAUDE.md](CLAUDE.md) — this is only the state + next steps.

---

## Prompt to paste

> I'm continuing work on **Creator Studio** (this folder, GitHub repo
> `omri-il/creator-studio`). It's a windowed desktop app for my home PC (pywebview
> + WebView2 window + embedded Flask on `127.0.0.1:5015`), rebuilt on 2026-07-02
> from the old tray-only "Studio Flow". Read `CLAUDE.md` first for the architecture
> and file map. **Don't re-explain or rebuild what's already done** — it's all
> committed and verified.
>
> **What's already done and verified (do not redo):**
> - Full rebuild: real window, no floating mic overlay, silent mic lock, tray for
>   background persistence. Frozen `.exe` builds and serves the UI (200).
> - **DJI Osmo Pocket auto-import** (`osmo_import.py`): camera detect → recursive
>   DCIM scan (skips `.LRF`/`.SRT`) → group split clips into sessions by
>   time-contiguity + matching params → idempotent copy to
>   `E:\Video Projects\Osmo Imports\<date>` → lossless per-session merge → optional
>   Hebrew transcription. Unit tests (`tests/test_osmo.py`, 13) pass; a full
>   simulated-card import verified 2 sessions, exact-sum merge duration, and
>   idempotency.
> - Installer built: `dist\installer\CreatorStudio-Setup-2.0.0.exe`.
> - Renamed from studio-flow → creator-studio (repo, remote, app, folder, docs).
> - ✅ **Real Osmo Pocket 4 card verified (2026-07-03):** mounts as a drive letter,
>   8 clips grouped into sessions correctly, lossless merges exact. `SESSION_MAX_GAP`
>   left at 5 s.
> - ✅ **Transcription fixed (2026-07-03):** the first real import failed transcription
>   two ways — (a) a *transient* `model.bin` file-lock during the heavy copy, and (b) a
>   *persistent* `cublas64_12.dll cannot be loaded` (missing `nvidia-cuda-runtime-cu12`
>   **and** `add_nvidia_dll_dirs()` only added dirs; ctranslate2 needs the CUDA DLLs
>   **preloaded**). Both fixed: installed the CUDA runtime pkg, `transcribe-hebrew.py`
>   now preloads, plus retry-with-backoff and a re-transcribe button/endpoint. GPU
>   transcription verified end-to-end on a real clip.
>
> **Open items, in priority order:**
> 1. **Install + smoke test the real app**: run the installer, tick autostart,
>    confirm the window opens, the tray persists on close, and the mic lock is
>    silent (no overlay). Then uninstall the old "Studio Flow" from Apps.
> 2. **Optional — DaVinci "create project from file" UI**: the backend
>    (`davinci.create_project`) is ready and wired-capable; only the UI form wasn't
>    ported from the old app. Add it as a screen if wanted.
> 3. **Minor**: `GET /favicon.ico` returns 404 (harmless) — add a favicon route or
>    asset if you care.
>
> **How to run/build/test:** `run.bat` (dev window) · `py -3.10 -m pytest tests/ -q`
> · `build.bat` (PyInstaller + Inno → installer). ffmpeg is vendored; UPX must stay
> OFF (it corrupts WebView2/.NET DLLs). Reuse points: lossless merge mirrors
> `video-prep/fftools.py`; transcription shells out to
> `E:\DaVinci Automation\scripts\transcription\transcribe-hebrew.py`.
>
> Tell me which of the open items you want to tackle first.

---

_Last updated: 2026-07-02, end of the rebuild session._
