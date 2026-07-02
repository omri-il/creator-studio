"""YouTube loudness analysis + two-pass normalization (ffmpeg ebur128/loudnorm).

Ported unchanged in behaviour from the old Studio Flow; just uses the shared
FFMPEG path and drops the Tk UI (results now render on the web screen).
"""
from __future__ import annotations

import json
import os
import re
import subprocess
import time

from settings_store import FFMPEG

_NO_WINDOW = subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0


def analyze_audio_file(path: str, progress_cb=None) -> dict | None:
    """Run ffmpeg ebur128 and parse the summary block. Returns dict with
    integrated_lufs, true_peak_dbfs, loudness_range, duration_sec — or None."""
    try:
        proc = subprocess.Popen(
            [FFMPEG, "-hide_banner", "-i", path,
             "-af", "ebur128=peak=true", "-f", "null", "-"],
            stderr=subprocess.PIPE, stdout=subprocess.DEVNULL,
            text=True, bufsize=1, creationflags=_NO_WINDOW,
        )
    except FileNotFoundError:
        return None

    stderr_lines, duration, last_update = [], None, 0.0
    try:
        for line in proc.stderr:
            stderr_lines.append(line)
            if duration is None:
                m = re.search(r"Duration:\s*(\d+):(\d+):(\d+\.?\d*)", line)
                if m:
                    h, mm, s = m.groups()
                    duration = int(h) * 3600 + int(mm) * 60 + float(s)
            if duration and progress_cb:
                m = re.search(r"\bt:\s*(\d+\.?\d*)", line)
                if m:
                    now = time.time()
                    if now - last_update >= 0.2:
                        pct = min(99, int(float(m.group(1)) / duration * 100))
                        progress_cb(pct, f"מנתח… {pct}%")
                        last_update = now
    except Exception:
        proc.kill()
        return None

    try:
        proc.wait(timeout=30)
    except subprocess.TimeoutExpired:
        proc.kill()
        return None

    out = "".join(stderr_lines)
    summary_match = re.search(r"Summary:\s*(.*)", out, re.DOTALL)
    if not summary_match:
        return None
    block = summary_match.group(1)

    def find(pattern):
        m = re.search(pattern, block)
        return float(m.group(1)) if m else None

    integrated = find(r"I:\s*(-?\d+\.?\d*)\s*LUFS")
    lra = find(r"LRA:\s*(-?\d+\.?\d*)\s*LU")
    true_peak = find(r"Peak:\s*(-?\d+\.?\d*)\s*dBFS")

    dur_match = re.search(r"Duration:\s*(\d+):(\d+):(\d+\.?\d*)", out)
    dur = None
    if dur_match:
        h, m, s = dur_match.groups()
        dur = int(h) * 3600 + int(m) * 60 + float(s)

    if integrated is None or true_peak is None:
        return None
    return {"integrated_lufs": integrated, "true_peak_dbfs": true_peak,
            "loudness_range": lra, "duration_sec": dur}


def youtube_verdict(lufs: float, true_peak: float):
    """(verdict_label, color, observations[], recommendation) vs YouTube (-14 LUFS)."""
    observations, rec_parts = [], []
    lufs_delta = lufs - (-14.0)
    if -15.0 <= lufs <= -13.0:
        observations.append(f"✓ עוצמה בול על היעד ({lufs:.1f} LUFS)")
    elif lufs > -13.0:
        observations.append(f"⚠ חזק מדי: {lufs:.1f} LUFS (יוטיוב יחליש בערך {lufs_delta:.1f} dB)")
        rec_parts.append(f"הנמך עוצמה כללית ב-{lufs_delta:.1f} dB")
    else:
        boost = -14.0 - lufs
        observations.append(f"⚠ חלש מדי: {lufs:.1f} LUFS (יישמע {boost:.1f} dB חלש יותר)")
        rec_parts.append(f"הגבר עוצמה כללית ב-{boost:.1f} dB")

    if true_peak >= 0:
        observations.append(f"✗ קליפ ב-{true_peak:.1f} dBTP — האודיו מעוות")
        rec_parts.append("הנמך גיין עד שהשיאים מתחת ל-0 dBFS")
    elif true_peak > -1.0:
        observations.append(f"✓ שיא {true_peak:.1f} dBTP (מתחת ל-0 dB)")
    else:
        observations.append(f"✓ שיאים בטוחים ({true_peak:.1f} dBTP)")

    if true_peak >= 0:
        return ("❌ אל תעלה — קליפינג", "#dc3545", observations, " / ".join(rec_parts))
    if -15.0 <= lufs <= -13.0:
        return ("✅ אפשר להעלות כמו שזה", "#2eb85c", observations, "")
    return ("⚠️ צריך תיקון", "#ffa500", observations, " / ".join(rec_parts))


def normalize_audio_file(input_path: str, output_path: str, progress_cb=None) -> dict | None:
    """Two-pass EBU R128 normalization to -14 LUFS / -1 dBTP. Video is copied
    through (no re-encode). Returns measured stats dict or None on failure."""
    def _notify(stage, text):
        if progress_cb:
            try:
                progress_cb(stage, text)
            except Exception:
                pass

    _notify("pass1", "מעבר 1 מתוך 2: מודד עוצמה…")
    try:
        p1 = subprocess.run(
            [FFMPEG, "-hide_banner", "-nostats", "-i", input_path,
             "-af", "loudnorm=I=-14:TP=-1:LRA=11:print_format=json",
             "-f", "null", "-"],
            capture_output=True, text=True, timeout=1800, creationflags=_NO_WINDOW)
    except (FileNotFoundError, subprocess.TimeoutExpired):
        _notify("error", "ffmpeg נכשל במעבר 1")
        return None

    json_match = re.search(r"\{[^{}]*\"input_i\"[^{}]*\}", p1.stderr or "", re.DOTALL)
    if not json_match:
        _notify("error", "לא ניתן לפענח מדידת loudnorm")
        return None
    try:
        measured = json.loads(json_match.group(0))
    except json.JSONDecodeError:
        _notify("error", "JSON לא תקין מ-loudnorm")
        return None

    required = ("input_i", "input_tp", "input_lra", "input_thresh", "target_offset")
    if not all(k in measured for k in required):
        _notify("error", "מדידת loudnorm חסרה")
        return None

    _notify("pass2", "מעבר 2 מתוך 2: מיישם גיין…")
    af = ("loudnorm=I=-14:TP=-1:LRA=11"
          f":measured_I={measured['input_i']}"
          f":measured_TP={measured['input_tp']}"
          f":measured_LRA={measured['input_lra']}"
          f":measured_thresh={measured['input_thresh']}"
          f":offset={measured['target_offset']}:print_format=summary")

    ext = os.path.splitext(input_path)[1].lower()
    is_video = ext in {".mp4", ".mov", ".mkv", ".avi", ".m4v", ".webm"}
    cmd = [FFMPEG, "-y", "-hide_banner", "-nostats", "-i", input_path, "-af", af]
    if is_video:
        cmd += ["-c:v", "copy", "-c:a", "aac", "-b:a", "192k"]
    cmd += [output_path]

    try:
        p2 = subprocess.run(cmd, capture_output=True, text=True, timeout=3600,
                            creationflags=_NO_WINDOW)
    except (FileNotFoundError, subprocess.TimeoutExpired):
        _notify("error", "ffmpeg נכשל במעבר 2")
        return None
    if p2.returncode != 0:
        _notify("error", f"ffmpeg יצא עם קוד {p2.returncode}")
        return None

    _notify("done", "הנרמול הושלם")
    return {"input_i": float(measured["input_i"]), "input_tp": float(measured["input_tp"]),
            "input_lra": float(measured["input_lra"]),
            "target_offset": float(measured["target_offset"]), "output_path": output_path}
