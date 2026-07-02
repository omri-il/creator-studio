"""ffmpeg helpers — probe + lossless concat merge.

Ported from video-prep's fftools.py (the source of truth for Omri's lossless
join). Everything here that touches video is stream-copy (`-c copy`), so a merge
is near-instant regardless of file size. Metadata is read by parsing
`ffmpeg -i` stderr (this box has ffmpeg but no ffprobe).

Extended beyond video-prep with `creation_time` extraction, which the Osmo
session-grouping relies on to tell one long split recording from separate takes.
"""
from __future__ import annotations

import os
import re
import subprocess
import threading
from datetime import datetime, timezone
from pathlib import Path

from settings_store import FFMPEG

VIDEO_EXTS = {".mp4", ".mov", ".mkv", ".avi", ".m4v", ".webm", ".mpg",
              ".mpeg", ".ts", ".m2ts", ".flv", ".wmv"}

_NO_WINDOW = subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0

_DUR = re.compile(r"Duration:\s*(\d+):(\d+):(\d+(?:\.\d+)?)")
_RES = re.compile(r"(\d{2,5})x(\d{2,5})")
_FPS = re.compile(r"([\d.]+)\s*fps")
_CREATION = re.compile(r"creation_time\s*:\s*([0-9T:\-\.]+Z?)")


def _run(cmd):
    return subprocess.run(cmd, capture_output=True, text=True,
                          encoding="utf-8", errors="replace",
                          creationflags=_NO_WINDOW)


def _parse_creation_time(s: str) -> float | None:
    """ISO8601 'creation_time' from ffmpeg metadata → epoch seconds (UTC)."""
    m = _CREATION.search(s)
    if not m:
        return None
    raw = m.group(1).strip()
    for fmt in ("%Y-%m-%dT%H:%M:%S.%fZ", "%Y-%m-%dT%H:%M:%SZ",
                "%Y-%m-%dT%H:%M:%S.%f", "%Y-%m-%dT%H:%M:%S"):
        try:
            dt = datetime.strptime(raw, fmt).replace(tzinfo=timezone.utc)
            return dt.timestamp()
        except ValueError:
            continue
    return None


def probe(path: str | Path) -> dict:
    """Return metadata by parsing `ffmpeg -i` stderr.

    Keys: path, name, duration, width, height, fps, vcodec, acodec, has_audio,
    size, creation_time (epoch secs or None). Missing fields default to 0/''/False.
    """
    p = Path(path)
    out = {"path": str(p), "name": p.name, "duration": 0.0, "width": 0,
           "height": 0, "fps": 0.0, "vcodec": "", "acodec": "",
           "has_audio": False, "size": p.stat().st_size if p.exists() else 0,
           "creation_time": None}
    if not p.exists():
        return out
    s = _run([FFMPEG, "-hide_banner", "-i", str(p)]).stderr or ""

    m = _DUR.search(s)
    if m:
        out["duration"] = round(int(m.group(1)) * 3600 + int(m.group(2)) * 60
                                + float(m.group(3)), 3)

    vline = next((ln for ln in s.splitlines() if " Video:" in ln), "")
    if vline:
        cm = re.search(r"Video:\s*([\w0-9]+)", vline)
        out["vcodec"] = cm.group(1) if cm else ""
        rm = _RES.search(vline)
        if rm:
            out["width"], out["height"] = int(rm.group(1)), int(rm.group(2))
        fm = _FPS.search(vline)
        if fm:
            out["fps"] = float(fm.group(1))

    aline = next((ln for ln in s.splitlines() if " Audio:" in ln), "")
    if aline:
        out["has_audio"] = True
        am = re.search(r"Audio:\s*([\w0-9]+)", aline)
        out["acodec"] = am.group(1) if am else ""

    out["creation_time"] = _parse_creation_time(s)
    return out


def find_videos(folder: str | Path, recursive: bool = True) -> list[Path]:
    """Return video file paths under `folder`. DJI cameras nest clips under
    DCIM\\… so recursive is the default. Sorted for stable ordering."""
    d = Path(folder)
    if not d.is_dir():
        raise NotADirectoryError(str(folder))
    it = d.rglob("*") if recursive else d.iterdir()
    return sorted(f for f in it if f.is_file() and f.suffix.lower() in VIDEO_EXTS)


_OUT_TIME = re.compile(r"out_time=(\d+):(\d+):(\d+(?:\.\d+)?)")


def _run_progress(cmd, total_dur, cb):
    """Run ffmpeg with `-progress pipe:1`; call cb(pct 0..99) as it advances.
    Returns (returncode, last_error_lines)."""
    proc = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                            text=True, encoding="utf-8", errors="replace",
                            bufsize=1, creationflags=_NO_WINDOW)
    errlines: list[str] = []

    def drain_err():
        for ln in proc.stderr:            # type: ignore[union-attr]
            errlines.append(ln.rstrip())
            if len(errlines) > 60:
                errlines.pop(0)

    t = threading.Thread(target=drain_err, daemon=True)
    t.start()
    for line in proc.stdout:              # type: ignore[union-attr]
        m = _OUT_TIME.search(line)
        if m and total_dur and cb:
            sec = int(m.group(1)) * 3600 + int(m.group(2)) * 60 + float(m.group(3))
            cb(min(99.0, sec / total_dur * 100))
    proc.wait()
    t.join(timeout=2)
    return proc.returncode, "\n".join(errlines[-25:])


def join(files: list[str], out_path: str, total_dur: float | None = None, cb=None):
    """Lossless concat (concat demuxer + -c copy). Inputs must share codec/params.

    Returns (returncode, last_error_lines). Same command video-prep uses.
    """
    listfile = out_path + ".concat.txt"
    with open(listfile, "w", encoding="utf-8") as f:
        for fp in files:
            esc = str(fp).replace("'", "'\\''")
            f.write(f"file '{esc}'\n")
    cmd = [FFMPEG, "-hide_banner", "-y", "-f", "concat", "-safe", "0",
           "-i", listfile, "-c", "copy", "-movflags", "+faststart",
           "-progress", "pipe:1", "-nostats", out_path]
    try:
        rc, log = _run_progress(cmd, total_dur, cb)
    finally:
        try:
            os.remove(listfile)
        except OSError:
            pass
    return rc, log
