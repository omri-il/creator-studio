"""DJI Osmo Pocket auto-import: detect the camera, scan its clips, group split
recordings into sessions, copy idempotently, merge losslessly, optionally
transcribe.

Split into PURE logic (timeline + grouping + manifest keys — unit-tested in
tests/test_osmo.py) and IO (drive detection, scan, copy, orchestration).

Key facts this encodes:
- DJI splits one long recording into ~4 GB chunks. Those chunks are *contiguous*
  in time; separate takes leave a real gap. We group by time contiguity +
  matching video params, so it works regardless of the exact P4 file naming.
- The camera writes a low-res proxy (`.LRF`) and sometimes a telemetry sidecar
  (`.SRT`) next to each clip. We never transfer those — only the full `.MP4`.
- Never re-copy a clip already imported (manifest keyed by name + size).
"""
from __future__ import annotations

import ctypes
import os
import re
import shutil
from datetime import datetime
from pathlib import Path

import mediatools
from settings_store import get_setting, set_setting

# Sidecars the camera writes next to each real clip — never transferred.
SKIP_EXTS = {".lrf", ".srt", ".thm", ".gif"}

DEFAULT_BACKUP_ROOT = r"D:\DJI Pocket Archive"
MANIFEST_NAME = "imported.json"          # lives in the backup root
SESSION_MAX_GAP = 5.0                    # seconds between clips → same recording

# DJI filenames often embed a start timestamp, e.g. DJI_20240115143022_0001_D.MP4
_DJI_TS = re.compile(r"(20\d{2})(\d{2})(\d{2})(\d{2})(\d{2})(\d{2})")


# ══════════════════════════════════════════════════════════════════════════════
# PURE LOGIC (no IO — unit-tested)
# ══════════════════════════════════════════════════════════════════════════════

def parse_dji_timestamp(name: str) -> float | None:
    """Extract a recording start time from a DJI filename → epoch secs (local).
    Cross-check signal only; the primary anchor is metadata/mtime. None if absent."""
    m = _DJI_TS.search(name)
    if not m:
        return None
    try:
        y, mo, d, h, mi, s = (int(x) for x in m.groups())
        return datetime(y, mo, d, h, mi, s).timestamp()
    except (ValueError, OverflowError):
        return None


def assign_timeline(clips: list[dict]) -> list[dict]:
    """Give each clip a consistent `start`/`end` (epoch secs) so grouping can
    compare them. Picks ONE source for the whole set to avoid mixing clocks:

    - if every clip has embedded `creation_time` (UTC, written by the camera),
      use it as the start (start = creation_time, end = start + duration);
    - otherwise fall back to filesystem `mtime` as the end-of-write (end = mtime,
      start = mtime - duration).

    Each input clip must have: duration, creation_time (or None), mtime.
    Returns the same dicts with `start`, `end`, `time_source` added.
    """
    use_creation = bool(clips) and all(c.get("creation_time") for c in clips)
    for c in clips:
        dur = float(c.get("duration") or 0.0)
        if use_creation:
            start = float(c["creation_time"])
            c["start"], c["end"], c["time_source"] = start, start + dur, "creation_time"
        else:
            end = float(c.get("mtime") or 0.0)
            c["start"], c["end"], c["time_source"] = end - dur, end, "mtime"
    return clips


def _params(c: dict) -> tuple:
    return (c.get("width"), c.get("height"), round(float(c.get("fps") or 0), 2),
            c.get("vcodec"))


def group_sessions(clips: list[dict], max_gap: float = SESSION_MAX_GAP) -> list[list[dict]]:
    """Group time-adjacent clips with matching video params into sessions.

    `clips` must already have `start`/`end` (see assign_timeline). A new session
    starts when the gap between the previous clip's end and this clip's start
    exceeds `max_gap`, or the video params differ. Returns a list of sessions,
    each a chronological list of clips.
    """
    ordered = sorted(clips, key=lambda c: (c.get("start", 0.0), c.get("name", "")))
    sessions: list[list[dict]] = []
    for c in ordered:
        if not sessions:
            sessions.append([c])
            continue
        prev = sessions[-1][-1]
        gap = float(c.get("start", 0.0)) - float(prev.get("end", 0.0))
        if _params(c) == _params(prev) and -1.0 <= gap <= max_gap:
            sessions[-1].append(c)
        else:
            sessions.append([c])
    return sessions


def manifest_key(name: str, size: int) -> str:
    """Stable identity for a source clip — filename + byte size."""
    return f"{name}|{int(size)}"


def session_label(session: list[dict]) -> str:
    """Human-facing base name for a session's merged output."""
    first = session[0]
    start = first.get("start")
    if start:
        try:
            return "Osmo_" + datetime.fromtimestamp(start).strftime("%Y-%m-%d_%H%M%S")
        except (ValueError, OSError, OverflowError):
            pass
    return Path(first.get("name", "session")).stem


# ══════════════════════════════════════════════════════════════════════════════
# IO — drive detection
# ══════════════════════════════════════════════════════════════════════════════

_DRIVE_REMOVABLE, _DRIVE_FIXED, _DRIVE_REMOTE, _DRIVE_CDROM = 2, 3, 4, 5


def _drive_type(root: str) -> int:
    try:
        return int(ctypes.windll.kernel32.GetDriveTypeW(ctypes.c_wchar_p(root)))
    except Exception:
        return 0


def _volume_label(root: str) -> str:
    try:
        buf = ctypes.create_unicode_buffer(1024)
        ctypes.windll.kernel32.GetVolumeInformationW(
            ctypes.c_wchar_p(root), buf, ctypes.sizeof(buf),
            None, None, None, None, 0)
        return buf.value or ""
    except Exception:
        return ""


def _has_camera_signature(root: str) -> tuple[bool, str]:
    """A drive looks like a DJI camera if it has a DCIM folder with video files.
    Returns (is_camera, dcim_path)."""
    dcim = os.path.join(root, "DCIM")
    if not os.path.isdir(dcim):
        return False, ""
    try:
        for r, _dirs, files in os.walk(dcim):
            for fn in files:
                if os.path.splitext(fn)[1].lower() in mediatools.VIDEO_EXTS:
                    return True, dcim
    except Exception:
        pass
    return False, ""


def detect_camera_drives() -> list[dict]:
    """Return removable/fixed drives that look like a DJI camera.

    Deliberately excludes REMOTE drives so the Tailscale `E:` network mapping is
    never mistaken for a camera. Each entry: {root, label, dcim, is_dji}.
    """
    import psutil
    found = []
    try:
        parts = psutil.disk_partitions(all=False)
    except Exception:
        parts = []
    for part in parts:
        root = part.mountpoint
        if not root:
            continue
        if not root.endswith("\\"):
            root = root + "\\"
        dtype = _drive_type(root)
        if dtype in (_DRIVE_REMOTE, _DRIVE_CDROM):   # skip network (Tailscale E:) + optical
            continue
        if dtype not in (_DRIVE_REMOVABLE, _DRIVE_FIXED):
            continue
        is_cam, dcim = _has_camera_signature(root)
        if not is_cam:
            continue
        label = _volume_label(root)
        is_dji = "dji" in label.lower() or "osmo" in label.lower() or \
            any(Path(dcim).rglob("DJI_*"))
        found.append({"root": root, "label": label, "dcim": dcim, "is_dji": bool(is_dji)})
    return found


# ══════════════════════════════════════════════════════════════════════════════
# IO — scanning
# ══════════════════════════════════════════════════════════════════════════════

def _load_manifest(backup_root: str) -> dict:
    import json
    p = os.path.join(backup_root, MANIFEST_NAME)
    try:
        with open(p, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return {}


def _save_manifest(backup_root: str, manifest: dict) -> None:
    import json
    os.makedirs(backup_root, exist_ok=True)
    p = os.path.join(backup_root, MANIFEST_NAME)
    try:
        with open(p, "w", encoding="utf-8") as f:
            json.dump(manifest, f, indent=2, ensure_ascii=False)
    except Exception:
        pass


def get_backup_root() -> str:
    return get_setting("osmo_backup_root", DEFAULT_BACKUP_ROOT)


def set_backup_root(path: str) -> None:
    set_setting("osmo_backup_root", path)


def scan_source(source_dir: str, backup_root: str | None = None) -> dict:
    """Scan a camera/DCIM folder and return sessions + import plan.

    Skips the `.LRF`/`.SRT` sidecars, probes each real clip, groups them into
    sessions, and flags which clips were already imported (via the manifest).

    Returns: {source, backup_root, dest_dir, sessions:[{label, clips:[...],
    already: bool}], counts:{clips, new, sessions}}.
    """
    backup_root = backup_root or get_backup_root()
    manifest = _load_manifest(backup_root)

    files = [f for f in mediatools.find_videos(source_dir, recursive=True)
             if f.suffix.lower() not in SKIP_EXTS]

    clips = []
    for f in files:
        meta = mediatools.probe(f)
        try:
            mtime = os.path.getmtime(f)
        except OSError:
            mtime = 0.0
        meta["mtime"] = mtime
        key = manifest_key(meta["name"], meta["size"])
        meta["key"] = key
        meta["already"] = key in manifest
        clips.append(meta)

    assign_timeline(clips)
    sessions_raw = group_sessions(clips)

    dest_dir = os.path.join(backup_root, datetime.now().strftime("%Y-%m-%d"))
    sessions = []
    for s in sessions_raw:
        sessions.append({
            "label": session_label(s),
            "clips": s,
            "already": all(c["already"] for c in s),
            "total_duration": round(sum(float(c.get("duration") or 0) for c in s), 1),
            "total_size": sum(int(c.get("size") or 0) for c in s),
        })
    new_clips = sum(1 for c in clips if not c["already"])
    return {
        "source": source_dir,
        "backup_root": backup_root,
        "dest_dir": dest_dir,
        "sessions": sessions,
        "counts": {"clips": len(clips), "new": new_clips, "sessions": len(sessions)},
    }


# ══════════════════════════════════════════════════════════════════════════════
# IO — import orchestration (copy → merge → transcribe)
# ══════════════════════════════════════════════════════════════════════════════

# Two transcription backends, both shelled out to (one truth: the scripts live in
# davinci-automation). Default is the VPS whisper-agent (Omri's preference,
# 2026-07-03) — it uses OpenAI Whisper on the VPS (credits), needs no GPU here, and
# handles the huge Osmo files by uploading a tiny 32 kbps MP3. 'local' is the
# on-PC GPU path (ivrit-ai model) — offline, no credits, but ties up the GPU.
_TRANSCRIBE_DIR = r"E:\DaVinci Automation\scripts\transcription"
TRANSCRIBE_SCRIPT_LOCAL = os.path.join(_TRANSCRIBE_DIR, "transcribe-hebrew.py")
TRANSCRIBE_SCRIPT_VPS = os.path.join(_TRANSCRIBE_DIR, "transcribe_via_vps.py")
HAS_TRANSCRIBE_LOCAL = os.path.isfile(TRANSCRIBE_SCRIPT_LOCAL)
HAS_TRANSCRIBE_VPS = os.path.isfile(TRANSCRIBE_SCRIPT_VPS)
HAS_TRANSCRIBE = HAS_TRANSCRIBE_LOCAL or HAS_TRANSCRIBE_VPS
DEFAULT_TRANSCRIBE_BACKEND = "vps"

# Retry knobs. A fast transient failure is retried; a genuine timeout is NOT (a
# long recording — retrying would burn hours). Transients differ by backend: the
# local model file (~3 GB) can be momentarily unopenable during heavy import I/O
# (ctranslate2 "Unable to open file 'model.bin'"); the VPS path can blip on the
# network. Both clear on a retry.
TRANSCRIBE_ATTEMPTS = 3
TRANSCRIBE_BACKOFF = 15          # seconds, multiplied by the attempt number
TRANSCRIBE_TIMEOUT = 21600       # 6 h ceiling — comfortably above any one clip


def get_transcribe_backend() -> str:
    b = get_setting("transcribe_backend", DEFAULT_TRANSCRIBE_BACKEND)
    return b if b in ("vps", "local") else DEFAULT_TRANSCRIBE_BACKEND


def set_transcribe_backend(backend: str) -> None:
    if backend in ("vps", "local"):
        set_setting("transcribe_backend", backend)


def backend_available(backend: str) -> bool:
    return HAS_TRANSCRIBE_VPS if backend == "vps" else HAS_TRANSCRIBE_LOCAL


def _run_transcribe(vid: str, dest_dir: str, backend: str) -> tuple[str, str]:
    """Run one transcription via the chosen backend ('vps' | 'local'). Both write a
    `<stem>.srt` into dest_dir. Returns (status, err_tail), status ∈ 'ok'|'fail'|'timeout'."""
    import subprocess
    stem = os.path.splitext(os.path.basename(vid))[0]
    if backend == "vps":
        cmd = ["py", "-3.10", TRANSCRIBE_SCRIPT_VPS, vid,
               "--output", os.path.join(dest_dir, stem + ".srt")]
    else:
        cmd = ["py", "-3.10", TRANSCRIBE_SCRIPT_LOCAL, vid, "--output-dir", dest_dir]
    try:
        r = subprocess.run(
            cmd, capture_output=True, text=True, timeout=TRANSCRIBE_TIMEOUT,
            creationflags=(subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0),
        )
    except subprocess.TimeoutExpired:
        return "timeout", "התמלול חרג מזמן מרבי"
    except Exception as e:  # noqa: BLE001
        return "fail", str(e)
    if r.returncode == 0:
        return "ok", ""
    return "fail", (r.stderr or r.stdout or "").strip()


def transcribe_one(vid: str, dest_dir: str, backend: str | None = None,
                   attempts: int = TRANSCRIBE_ATTEMPTS, on_msg=None) -> tuple[bool, str]:
    """Transcribe one file, retrying a *transient* failure with backoff. Returns
    (ok, err_tail). Does not retry a timeout (a genuinely long/hung run)."""
    import time
    backend = backend or get_transcribe_backend()
    last = ""
    for n in range(1, attempts + 1):
        status, err = _run_transcribe(vid, dest_dir, backend)
        if status == "ok":
            return True, ""
        last = err
        if status == "timeout" or n >= attempts:
            break
        wait = TRANSCRIBE_BACKOFF * n
        if on_msg:
            try:
                on_msg(f"התמלול נכשל זמנית, מנסה שוב בעוד {wait}ש׳ ({n}/{attempts})…")
            except Exception:  # noqa: BLE001
                pass
        time.sleep(wait)
    return False, last


def transcribe_files(paths, dest_dir: str, backend: str | None = None,
                     progress=None) -> dict:
    """(Re)transcribe an explicit list of already-imported files. Used by the
    re-transcribe endpoint when the import's transcription phase was skipped or
    failed — the idempotent import skips those files and never reaches
    transcription on its own. Retries each. Returns a summary dict."""
    def _p(pct, msg):
        if progress:
            try:
                progress(round(pct, 1) if pct is not None else None, msg)
            except Exception:  # noqa: BLE001
                pass

    backend = backend or get_transcribe_backend()
    out = {"dest_dir": dest_dir, "transcribed": [], "errors": [],
           "transcribe_backend": backend}
    if not backend_available(backend):
        out["errors"].append("סקריפט התמלול לא נמצא")
        return out
    paths = list(paths or [])
    n = len(paths)
    for i, vid in enumerate(paths):
        base = os.path.basename(vid)
        pct = i / max(1, n) * 100
        _p(pct, f"מתמלל: {base}…")
        if not os.path.isfile(vid):
            out["errors"].append(f"transcribe {base}: הקובץ לא נמצא")
            continue
        ok, err = transcribe_one(vid, dest_dir, backend=backend,
                                 on_msg=lambda m, _pct=pct: _p(_pct, m))
        if ok:
            out["transcribed"].append(vid)
        else:
            out["errors"].append(f"transcribe {base}: {err[-200:]}")
    _p(100, "התמלול הושלם")
    return out


def _copy_with_progress(src: str, dst: str, cb=None):
    """shutil.copyfile with a progress callback (0..100 of this file)."""
    total = os.path.getsize(src) or 1
    done = 0
    with open(src, "rb") as fsrc, open(dst, "wb") as fdst:
        while True:
            chunk = fsrc.read(4 * 1024 * 1024)
            if not chunk:
                break
            fdst.write(chunk)
            done += len(chunk)
            if cb:
                cb(min(100.0, done / total * 100))
    shutil.copystat(src, dst, follow_symlinks=True)


def run_import(source_dir: str, options: dict, progress=None) -> dict:
    """Execute an import. `options`: {merge, transcribe, keep_originals,
    backup_root}. `progress(pct, message)` reports 0..100 overall.

    Returns a summary dict: {dest_dir, copied:[...], merged:[...],
    transcribed:[...], skipped:int, errors:[...]}.
    """
    def _p(pct, msg):
        if progress:
            try:
                progress(round(pct, 1) if pct is not None else None, msg)
            except Exception:
                pass

    backup_root = options.get("backup_root") or get_backup_root()
    merge = options.get("merge", True)
    backend = options.get("transcribe_backend") or get_transcribe_backend()
    transcribe = options.get("transcribe", True) and backend_available(backend)
    keep_originals = options.get("keep_originals", True)

    scan = scan_source(source_dir, backup_root)
    dest_dir = scan["dest_dir"]
    os.makedirs(dest_dir, exist_ok=True)
    manifest = _load_manifest(backup_root)

    summary = {"dest_dir": dest_dir, "copied": [], "merged": [],
               "transcribe_targets": [], "transcribed": [], "skipped": 0,
               "errors": [], "transcribe_backend": backend}

    # Only work on clips not already imported.
    pending_sessions = []
    for s in scan["sessions"]:
        new_clips = [c for c in s["clips"] if not c["already"]]
        summary["skipped"] += len(s["clips"]) - len(new_clips)
        if new_clips:
            pending_sessions.append({"label": s["label"], "clips": new_clips})

    total_new = sum(len(s["clips"]) for s in pending_sessions)
    if total_new == 0:
        _p(100, "אין קבצים חדשים לייבוא")
        return summary

    # ── Phase 1: copy (0–60%) ────────────────────────────────────────────────
    copied_index = 0
    for s in pending_sessions:
        s["dest_clips"] = []
        for c in s["clips"]:
            dst = os.path.join(dest_dir, c["name"])
            # avoid clobbering a differently-sized file of the same name
            if os.path.exists(dst) and os.path.getsize(dst) != int(c.get("size") or -1):
                stem, ext = os.path.splitext(c["name"])
                dst = os.path.join(dest_dir, f"{stem}_{int(c.get('size',0))}{ext}")
            base = copied_index / total_new * 60.0

            def _cb(fp, _base=base):
                _p(_base + (fp / 100.0) * (60.0 / total_new),
                   f"מעתיק {c['name']}…")
            try:
                _copy_with_progress(c["path"], dst, _cb)
                s["dest_clips"].append(dst)
                summary["copied"].append(dst)
                manifest[c["key"]] = {
                    "name": c["name"], "size": int(c.get("size") or 0),
                    "imported_at": datetime.now().isoformat(timespec="seconds"),
                    "dest": dst,
                }
            except Exception as e:
                summary["errors"].append(f"copy {c['name']}: {e}")
            copied_index += 1
    _save_manifest(backup_root, manifest)

    # ── Phase 2: merge sessions (60–85%) ─────────────────────────────────────
    outputs_for_transcribe = []
    merge_sessions = [s for s in pending_sessions if len(s["dest_clips"]) > 1]
    for i, s in enumerate(pending_sessions):
        clips = s["dest_clips"]
        if not clips:
            continue
        if merge and len(clips) > 1:
            _p(60 + (i / max(1, len(pending_sessions))) * 25,
               f"ממזג אירוע: {s['label']}…")
            out_path = os.path.join(dest_dir, f"{s['label']}_merged.mp4")
            total_dur = sum(mediatools.probe(c)["duration"] for c in clips)
            rc, log = mediatools.join(clips, out_path, total_dur)
            if rc == 0 and os.path.isfile(out_path):
                summary["merged"].append(out_path)
                outputs_for_transcribe.append(out_path)
                if not keep_originals:
                    for c in clips:
                        try:
                            os.remove(c)
                        except OSError:
                            pass
            else:
                summary["errors"].append(f"merge {s['label']}: {log[-200:]}")
                outputs_for_transcribe.extend(clips)   # fall back to parts
        else:
            outputs_for_transcribe.extend(clips)
    _ = merge_sessions

    # Record what transcription targets (merged sessions + un-merged singles) so
    # the UI can offer a re-transcribe if the phase below is off or fails.
    summary["transcribe_targets"] = list(outputs_for_transcribe)

    # ── Phase 3: transcribe (85–100%) ────────────────────────────────────────
    if transcribe and outputs_for_transcribe:
        n = len(outputs_for_transcribe)
        for i, vid in enumerate(outputs_for_transcribe):
            cur = 85 + (i / max(1, n)) * 15
            _p(cur, f"מתמלל: {os.path.basename(vid)}…")
            ok, err = transcribe_one(vid, dest_dir, backend=backend,
                                     on_msg=lambda m, _c=cur: _p(_c, m))
            if ok:
                summary["transcribed"].append(vid)
            else:
                summary["errors"].append(
                    f"transcribe {os.path.basename(vid)}: {err[-200:]}")

    _p(100, "הייבוא הושלם")
    return summary
