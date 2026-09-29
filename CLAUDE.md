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
- **VSL publishing:** after Resolve renders a sales video, one action puts it on
  **Wistia** and points an Event-Engine event's sign-up page at it — no browser
  round-trip through the Wistia dashboard and the events admin. Same pipeline from
  the CLI (`publish_vsl.py`) and from the app's 🎯 tile.
- **Tiles for the rest:** launch DaVinci Resolve + its Control Center dashboard,
  Tailscale E: drive mapping, and audio normalization — which **hands off to
  video-prep** rather than doing it here (see below).

## Audio normalization lives in video-prep, not here (2026-08-22)

The 🔊 tile's `פתח נרמול אודיו` button calls `POST /api/audio/open` →
`davinci.open_video_prep()`, which starts video-prep if it is down and returns
`http://127.0.0.1:5005/#normalize` for the browser to open.

**`audio_tools.py` was deleted, along with `/api/audio/analyze` and
`/api/audio/normalize`.** It did a correct but level-only two-pass loudnorm behind
a file-picker click, with no way to hear the result first. video-prep's
`נרמול אודיו` tab does that and more — a voice chain (highpass + compressor, the
ffmpeg counterpart of Fairlight Dynamics + Limiter), optional denoise, and a
level-matched before/after preview. Keeping a second copy here would have meant
two implementations to fix every time one of loudnorm's traps bit (and there are
several — they're documented in video-prep's CLAUDE.md). The proven ebur128 parser
and `youtube_verdict` were **ported**, not rewritten, into `video-prep/loudness.py`.

If the tile ever reports it can't open video-prep, check that
`~/Projects/video-prep/serve.bat` exists (`davinci.HAS_VIDEOPREP`) — video-prep also
autostarts on logon, so normally it is already up and the tile just opens a tab.

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
| `settings_store.py` | Shared paths (incl. `DAVINCI_AUTOMATION_DIR` — see "davinci-automation's scripts"), `settings.json` (in `%LOCALAPPDATA%\StudioFlow`), ffmpeg resolution, app constants. |
| `mediatools.py` | `probe` (+ `creation_time`), recursive `find_videos`, lossless `join` (concat demuxer + `-c copy`) + progress. Ported from video-prep `fftools.py`. |
| `osmo_import.py` | Camera detect, DCIM scan (skip `.LRF`/`.SRT`), **session grouping**, idempotent copy, import orchestration (copy → merge → transcribe). |
| `mic.py` | `get/set_mic_volume`, headless `MicMonitor` (silent lock loop). |
| `davinci.py` | Resolve launch, project create, dashboard/bot/watch launchers, Tailscale drive map. New projects land in the shared network Resolve Project Library (`Resolve Shared Library`) by default since 2026-07 — see davinci-automation's CLAUDE.md "Database Structure" section (no code change here; it's `new_project.py`'s own default). |
| `jobs.py` | In-memory job registry + progress (UI polls `GET /api/job/<id>`). Jobs carry a `kind`; `run(..., exclusive=…)` raises `JobBusy` instead of starting a second one (see "One import at a time"). |
| `web/` | `index.html`, `app.css`, `app.js` — RTL "control-room" UI (Rubik/Heebo, amber+teal on near-black). |
| `wistia.py` | Wistia Upload API client. **Streams** the file with a real `Content-Length` (`_MultipartBody`) — a Resolve master does not fit in RAM, which is the whole reason this is not a `requests.post(files=…)` one-liner. `media_url()` is the canonical link because it is guaranteed to match Event-Engine's `vsl.py` regex, with no account-subdomain guessing. |
| `eventengine.py` | Client for Event-Engine's token-guarded `/api/media/*`. Errors are returned with what to fix, never raw HTTP codes. |
| `vsl_publish.py` | The pipeline both surfaces share: config, `latest_video`, the size warning, and `publish()`. One home so the CLI and the button cannot drift. |
| `publish_vsl.py` | The CLI. `--latest`, `--pick`, `--event`, `--dry-run`; copies the link to the clipboard. |
| `tests/test_osmo.py` | Unit tests for the pure grouping/timeline/manifest logic, `plan_dest`, `.part` copies, and the per-clip manifest. |
| `tests/test_osmo_lock.py` | The one-import-at-a-time rule, through the real Flask routes (workers stubbed to block). |
| `tests/test_transcribe.py` | Scripts come from the davinci-automation checkout; backend → `--engine`; the error tail; a timeout ends the whole process chain (a fake `transcribe_auto.py`, real processes). |
| `tests/test_wistia.py` · `test_publish_vsl.py` · `test_vsl_routes.py` | The VSL pipeline, network stubbed throughout. |

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

## One import at a time — and copies that can't lie (2026-09-23)

**What happened:** on the laptop, an import started via `POST /api/osmo/import`
was still copying ~85 min later when the camera-detect window started a second
one. Both copied the same clips into the same `S:\DJI Pocket Archive\<date>`:
the manifest was only saved after *all* copies, so the second job saw every clip
as new, and it re-opened same-sized finished files with `"wb"`, truncating them.

Three rules now, each load-bearing:
- **One Osmo import per app.** `POST /api/osmo/import` answers **409** +
  `busy: true` + the running job while one runs; `POST /api/osmo/transcribe` is
  refused during an import or another transcribe. A running *transcribe* does
  not block an import (different files; it could hold the camera for hours).
  `GET /api/osmo/active` returns the running import, and the import view calls
  it on open — so the window shows that job's progress with an amber note and
  **no Start button**. The check-and-register happens under one lock in `jobs.py`.
  ⚠️ This is per process. Two *machines* importing into the same share (home PC
  `D:` is the laptop's `S:`) are not locked against each other — the two rules
  below are what keep that case from destroying data.
- **The manifest is written after every clip** (`record_imported`), re-read and
  merged before each write, and saved via temp file + `os.replace` so a crash
  never leaves half-written JSON (which would read as `{}` = re-import all).
- **Copies go to `<name>.part` and are renamed on success.** So a file under
  its final name is always complete: `plan_dest` reuses a same-sized one instead
  of copying again (this also recovers "copied but never recorded"), sends a
  different-sized one's clip to `<stem>_<size><ext>` rather than overwriting it,
  and a stale `.part` is simply started over. A failed copy deletes its `.part`.
  ⚠️ Builds before this fix wrote in place, so **old dated folders can hold
  truncated files under the real name** (e.g. `2026-09-22` has a 0-byte `…0064_D.MP4`).
  The new code never overwrites them (it copies to `_<size>` beside them); clean
  them up by hand after comparing sizes with the card.

## Reuse (one truth per topic)
- Lossless merge command is the same concat-demuxer `-c copy` as
  `video-prep/fftools.py` — `mediatools.join` carries it so the packaged `.exe` is
  self-contained (no second process). If strict de-dup is wanted later, extract a
  shared helper.
- Transcription **shells out** to davinci-automation's ONE entry point,
  `scripts\transcription\transcribe_auto.py <mp4> --output <dest>\<stem>.srt
  --engine vps|local`, in that repo's checkout (next section). Its own rule is
  "never call an engine script directly from a pipeline" — so never call
  `transcribe_via_vps.py` / `transcribe-hebrew.py` from here. The
  `transcribe_backend` setting (default **`vps`**) picks the engine:
  - **`vps`** (default) → `--engine vps` — the VPS **whisper-agent** only: a tiny
    32 kbps MP3 goes up to Tailscale `100.94.153.60:8080` (OpenAI Whisper, uses
    credits, no GPU here).
  - **`local`** → `--engine local` — ivrit-ai on this PC's GPU, then on its CPU if the
    GPU run fails (slow: on a multi-hour Osmo file, hours).
  Either way only the `.srt` is written (ivrit-ai's cues re-cut short, like the VPS's).
  The `_transcription.txt/.json/_fillers.txt` the direct `transcribe-hebrew.py` call
  used to leave beside the video are gone since 2026-09-29 — nothing here read them.
  Routing lives in `osmo_import.transcribe_cmd` / `_run_transcribe`; both are picked
  in the import screen (segmented toggle) and via `POST /api/osmo/config`. An
  "automatic" (VPS, then local) choice would be `--engine auto` — not offered.

## davinci-automation's scripts come from its checkout (2026-09-29)

`settings_store.DAVINCI_AUTOMATION_DIR` = `~\Projects\davinci-automation` — the
same `C:\Users\omrii\Projects\davinci-automation` on the home PC and the laptop.
Transcription (`osmo_import`) and the DaVinci tile (`davinci.NEW_PROJECT_SCRIPT` /
`IMPORT_PROXY_SCRIPT`, which also decide whether the tile shows) take their scripts
from there. **Never `E:\DaVinci Automation\scripts\`**: that mirror was retired
2026-09-06 (davinci-automation's CLAUDE.md) and only kept stale copies — it never
had `transcribe_auto.py`, and its `transcribe_via_vps.py` kept the dead public-IP
fallback the checkout dropped (davinci-automation `cf7d728`). Until 2026-09-29
Creator Studio still ran those copies on both machines (the laptop over SMB).
- **A machine runs whatever its davinci-automation checkout has.** The dashboard's
  "עדכון הדשבורד" button (or `git pull --ff-only` there) updates it; nothing in this
  repo needs rebuilding for a change to those scripts.
- **The installed app runs the code it was BUILT from.** Both machines run
  `C:\Program Files\Creator Studio\CreatorStudio.exe`; PyInstaller compiles every
  path constant into it (read back 2026-09-29: the 2026-09-23 build still said
  `E:\DaVinci Automation\...`). So a change here reaches a machine only through a
  rebuild + reinstall (Run / build, Laptop install) — `git pull` changes nothing
  that runs. The same goes for `dist\CreatorStudio`: stale until the next build.
- **The time limit must end the whole chain:** the app → `transcribe_auto.py` → the
  engine → ffmpeg. `subprocess.run(timeout=…)` kills only the first and then waits
  for the rest, which hold the output pipe (a 3 s limit waited 20 s in a test).
  `_kill_tree` runs `taskkill /F /T`. The chain's output is one UTF-8 pipe
  (`PYTHONIOENCODING`, `PYTHONUNBUFFERED`, stderr folded in), and `_error_tail`
  drops `transcribe_auto.py`'s generic "every transcription engine failed" line so
  the done screen's 200 characters are the engine's own reason.
- **Verified 2026-09-29 on the home PC** (the new code, a 20 s cut of a real Osmo
  clip, the checkout at `cf7d728`): `vps` 22 s and `local` 12 s (GPU), each a Hebrew
  `.srt` and nothing else; a missing file → `ERROR: file not found: …` as the error.
- ⚠️ **`local` cannot run from an SSH session on the home PC** as is:
  `C:\Users\omrii\.cache\huggingface` is a junction to `E:\cache\huggingface`, and
  Windows refuses a junction inside an SSH session ("The path cannot be traversed
  because it contains an untrusted mount point"; Python sees `OSError: [Errno 22]
  Invalid argument: …\huggingface\token`, GPU and CPU alike). The app, in Omri's
  desktop session, is not affected. For an SSH test set `HF_HOME=E:\cache\huggingface`
  (the same files, no junction).

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
- Osmo backup root default: `D:\DJI Pocket Archive` (`osmo_import.DEFAULT_BACKUP_ROOT`,
  dated subfolder per import). Change in the import screen (persisted as
  `osmo_backup_root`). ⚠️ That default is a **home-PC** path — on the laptop `D:` is
  an empty card-reader slot, so the laptop's setting must be overridden (see below).
- Default import actions all ON: merge sessions, transcribe Hebrew, keep originals.
- **Transcription engine defaults to the VPS whisper-agent** (`transcribe_backend`
  = `vps`; Omri's preference 2026-07-03). Switch to local GPU in the import screen
  (persisted). See Reuse for the two backends.
- Mic: first-run default **unlocked** (lock is opt-in from the app screen). When
  locked, target defaults to 90% (`mic_locked`/`mic_lock_target` persisted).

## Laptop install (2026-09-23)

The Omnibook laptop runs the same installed build (`C:\Program Files\Creator Studio`,
startup shortcut on) so plugging the Osmo in there opens the import window too.
Built on the laptop itself: `py -3.10` + Inno Setup were installed with winget, and
Inno Setup went in **per-user**, so `ISCC.exe` is at
`%LOCALAPPDATA%\Programs\Inno Setup 6\`, not the `Program Files (x86)` path
`build.bat` looks for — call it directly. Defender did not flag this build.

- **Home-PC drives on the laptop** come from `laptop-setup\map-video-drive.ps1`
  (scheduled task `MapVideoDrive`, every 2 min, runs from the main checkout's
  `laptop-setup\`): home `E:` → **`E:`** (same letter, so `E:\…` paths resolve
  identically) and home `D:` → **`S:`** (laptop `D:` is taken; `S:` is Remote-HDD's
  own letter for home `D:`). Shares are Remote-HDD's `DriveE`/`DriveD`, `netshare` creds
  stored via `cmdkey`.
- **`osmo_backup_root` on the laptop** is set in `%LOCALAPPDATA%\StudioFlow\settings.json`
  — never leave it on the `D:\…` default there.
- ✅ **Both installs run the 2026-09-23 import-lock build** (home PC 16:33 over
  SSH, laptop 16:37; installer SHA-256 `08A4E00B…80F0`). **Never run an installer
  while an import is copying** — it kills `CreatorStudio.exe` and the copy dies
  mid-file. How to tell: `GET http://127.0.0.1:5015/api/osmo/active` on that
  machine (non-null job = busy), and on the home PC `Get-SmbOpenFile` lists any
  archive file the laptop has open. The silent install does not reopen the app;
  start it afterwards.
  ⚠️ **Do NOT judge "is it copying?" by a file's size or modified time.** While a
  file is open for writing, the folder listing (Explorer, `Get-ChildItem`, even
  from the home PC) keeps showing the old values. On 2026-09-23 the laptop's
  `0064` looked stopped at 16:03 for over half an hour, yet the old build was
  still copying it — and the laptop install at 16:37 killed that copy at 2.9 of
  12.6 GB. CPU usage is no signal either: the copy loop is I/O-bound (~0%).
- **A laptop import is only as fast as the laptop's internet upload** when the
  laptop is away from home. Measured 2026-09-23 (~0.8 MB/s, a 12.6 GB clip ≈ 4 h):
  the camera was 99.8% idle (2.5 ms reads), the home PC's `D:` 99.6% idle
  (0.8 ms writes), and each SMB write to `S:` took 2.4 s. The laptop was on
  another network (`10.0.0.x`, a different ISP than home's `192.168.1.x`), so
  Tailscale's "direct" path still crossed the internet. SMB signing/encryption are
  off, so nothing to gain there. To import big clips fast: plug the camera into
  the **home PC**, or be on the home network.
  **Transcription has the same trap in reverse:** `vps` transcription runs ffmpeg
  on the machine that started it, so a laptop away from home first pulls the
  *whole video* back from `S:` (~0.4 MB/s — 2.9 GB ≈ 2 h) just to make a small MP3.
  Run it on the home PC instead (its app, `POST /api/osmo/transcribe` with the
  `D:\…` path — takes minutes). Done that way for `0063` on 2026-09-23. Measure before blaming the camera:
  `PhysicalDisk(F:)\% Idle Time` vs `SMB Client Shares(...drived)\Avg. sec/Write`.
- **Installing on the home PC over SSH** (how it was done 2026-09-23): `scp` the
  installer over, then run it with `/VERYSILENT /SUPPRESSMSGBOXES /NORESTART /LOG=…`.
  A reinstall keeps the earlier task choices, so the startup shortcut stays. The
  silent install does **not** relaunch the app (`skipifsilent`), and starting the
  exe from the SSH shell would put it in invisible session 0. Relaunch through a
  one-off scheduled task with `-LogonType Interactive` for
  **`$env:COMPUTERNAME\omrii`**, then delete the task. ⚠️ Over SSH,
  `$env:USERDOMAIN` is `WORKGROUP`, and a task registered for that account
  silently never runs. Verify with `GET /api/osmo/active` (only new builds have it).
- **Works there:** detection, copy, merge, and **`vps` transcription** — since
  2026-09-29 from the laptop's own davinci-automation checkout (before: the home
  PC's retired `E:\` copies over SMB), under the laptop's `py -3.10`. ⚠️ That
  interpreter needs **`requests`** (`py -3.10 -m pip install
  requests`): without it every transcription fails with `ModuleNotFoundError`
  after three retries. It was missing until 2026-09-23 17:53, so the earlier
  "verified" note did not cover a real run. Installed and re-run then. `local`
  there has no NVIDIA GPU, so it runs on the CPU (hours for a long clip) — use `vps`.
  **Home-PC only:** the DaVinci tiles' Resolve work.

## VSL publishing (Wistia → Event-Engine)

Render in Resolve, then either:

```
py -3.10 publish_vsl.py "E:\Video Projects\...sl.mp4" --pick
py -3.10 publish_vsl.py --latest --event 18
```

or open the **🎯 פרסום VSL** tile, pick the file, pick the event, Upload.

**Why Wistia and not YouTube:** a YouTube embed can no longer be stripped of its
chrome (`showinfo` removed 2018, `modestbranding` deprecated 2023), so it always
offers a "Watch on YouTube" exit off the sales page. Event-Engine's `vsl.py`
already parses Wistia links, so nothing was needed on that side but a way in.

**Config.** Copy `.env.example` → `.env`. Secrets live there and ONLY there — `WISTIA_API_TOKEN`,
`EVENT_ENGINE_TOKEN` (must equal `MEDIA_API_TOKEN` in that instance's `.env`).
Preferences in `settings.json`: `wistia_project_id`, `wistia_subdomain`,
`event_engine_url`, `vsl_exports_dir`. `settings.json` is **not** gitignored,
which is exactly why the split exists. The panel reports whether each token is
present and never its value.

**Three things that are load-bearing:**
- 🚨 **The upload is streamed, never buffered.** `wistia._MultipartBody` yields
  the preamble, the file in 1 MiB chunks, then the epilogue, and computes its own
  exact `Content-Length`. Swap in `requests.post(files=…)` and a 10 GB master
  takes the machine down.
- **A failed event update is a PARTIAL success, not a failure.** The video is on
  Wistia either way, so `publish()` returns `event_error` alongside the link
  rather than raising — otherwise the user re-uploads a file that already exists.
  Both surfaces show the link with a "paste it in by hand" note.
- **No compression, on purpose.** A big file is *warned about* (size + estimated
  minutes + "export a delivery file") and then uploaded anyway.
  [video-prep](../video-prep) is the documented home for compression; a second
  compressor here would duplicate the feature that repo exists for.

**Stdlib only** (`urllib`, no `requests`/`python-dotenv`): this app is frozen with
PyInstaller, so a new dependency means touching `mic_tracker.spec` and re-testing
the build — and urllib is what gives the streaming control above.

## Notes / follow-ups
- Requires the **DJI in USB mass-storage (drive-letter) mode**, not MTP. Detection
  excludes REMOTE drives so the Tailscale `E:` mapping is never mistaken for a camera.
- ✅ **Verified against a real Osmo Pocket 4 card (2026-07-03):** filenames
  (`DJI_<ts>_NNNN_D.MP4`) group into sessions correctly and the lossless merge is
  exact. `SESSION_MAX_GAP` (5 s) left as-is — no adjustment needed.
- ✅ **Verified against real Wistia (2026-08-20).** A real 6s clip uploaded
  through `wistia.upload` (18 progress callbacks over the streamed body),
  processed to `ready` in seconds, and its `media_url` parsed cleanly through
  Event-Engine's own `vsl.py` — then a real `/register/<slug>` page rendered the
  player at 16:9 with a live browsing context, and the embed URL serves 200
  publicly. **API access is NOT gated on this account** (the open question when
  this was built): `GET /v1/account.json` and `/v1/projects.json` both answer.
  - Account is **`omri-iram.wistia.com`**, so `wistia_subdomain` = `omri-iram`
    if the prettier `…/medias/<id>` link is ever wanted. Left unset by default:
    `fast.wistia.net` needs no subdomain to be right, and both parse.
  - Existing projects: `Hello's first folder`, `Sample live event`,
    `AI Challange` (10899727) — set `wistia_project_id` to file uploads there.
  - ⚠️ One leftover asset: **`ppryk9q0ie` — "[TEST] Creator Studio pipeline
    check — safe to delete"**. Delete it whenever.
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
- **Local backend teardown crash (handled since 2026-09-29):** on long footage
  `transcribe-hebrew.py` finishes and writes its outputs correctly, then the process
  can crash on CUDA/interpreter teardown with exit `0xC0000409` — a *false negative*.
  `transcribe_auto.py` keeps a complete, non-empty SRT whatever the exit code, so
  through it such a run counts as success.
