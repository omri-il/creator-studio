"""DaVinci Resolve + helpers (launch, project creation, dashboard/bot/watch,
Tailscale drive mapping). Headless: functions return status dicts; the web
screen renders them. Ported from the old Studio Flow tray handlers.
"""
from __future__ import annotations

import os
import subprocess
import time

from settings_store import get_setting, set_setting, _RES_DIR

_NO_WINDOW = subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0

# Paths only present on the home PC — features light up when they exist.
NEW_PROJECT_SCRIPT = r"E:\DaVinci Automation\scripts\utils\new_project.py"
IMPORT_PROXY_SCRIPT = r"E:\DaVinci Automation\scripts\utils\import_and_proxy.py"
DEFAULT_BASE = r"E:\Video Projects"
HAS_DAVINCI = os.path.isfile(NEW_PROJECT_SCRIPT)

DASHBOARD_RUNBAT = os.path.join(
    os.path.expanduser("~"), "Projects", "davinci-automation", "control", "run.bat")
DASHBOARD_URL = "http://127.0.0.1:5007/"
HAS_DASHBOARD = os.path.isfile(DASHBOARD_RUNBAT)
_CONTROL_DIR = os.path.dirname(DASHBOARD_RUNBAT)
BOT_BAT = os.path.join(_CONTROL_DIR, "bot.bat")
WATCH_BAT = os.path.join(_CONTROL_DIR, "watch.bat")

EXE_CANDIDATES = [
    r"E:\Program Files\Blackmagic Design\DaVinci Resolve\Resolve.exe",
    r"C:\Program Files\Blackmagic Design\DaVinci Resolve\Resolve.exe",
]
CATEGORIES = ["YouTube - Being a Teacher", "YouTube - Biology Is Life", "GEG",
              "Playback Theater", "Workshops", "School", "Personal"]

# Tailscale drive mapping (laptop-side helper script bundled in laptop-setup/)
MAP_DRIVE_SCRIPT = os.path.join(_RES_DIR, "laptop-setup", "map-video-drive.ps1")


def is_resolve_running() -> bool:
    import psutil
    try:
        for p in psutil.process_iter(attrs=["name"]):
            if (p.info.get("name") or "").lower() in ("resolve.exe", "resolve"):
                return True
    except Exception:
        pass
    return False


def find_resolve_exe() -> str | None:
    for path in EXE_CANDIDATES:
        if os.path.isfile(path):
            return path
    return None


def launch_resolve(wait: bool = False) -> dict:
    if is_resolve_running():
        return {"ok": True, "already": True}
    exe = find_resolve_exe()
    if not exe:
        return {"ok": False, "error": "לא נמצא Resolve.exe"}
    try:
        subprocess.Popen([exe], creationflags=(
            subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0))
    except Exception as e:
        return {"ok": False, "error": str(e)}
    if wait:
        deadline = time.time() + 90
        while time.time() < deadline:
            if is_resolve_running():
                time.sleep(5)
                return {"ok": True, "launched": True}
            time.sleep(1)
        return {"ok": False, "error": "Resolve לא סיים להיטען תוך 90 שניות"}
    return {"ok": True, "launched": True}


def get_base_dir() -> str:
    return get_setting("davinci_base_dir") or DEFAULT_BASE


def set_base_dir(path: str) -> None:
    set_setting("davinci_base_dir", path)


def open_dashboard() -> dict:
    """Start the DaVinci Control Center server if down, then return its URL."""
    import urllib.request

    def _up():
        try:
            urllib.request.urlopen(DASHBOARD_URL, timeout=1)
            return True
        except Exception:
            return False

    if not _up() and os.path.isfile(DASHBOARD_RUNBAT):
        subprocess.Popen([DASHBOARD_RUNBAT], cwd=_CONTROL_DIR,
                         creationflags=subprocess.CREATE_NEW_CONSOLE)
        for _ in range(24):
            if _up():
                break
            time.sleep(0.5)
    return {"ok": True, "url": DASHBOARD_URL}


def start_bot() -> dict:
    if os.path.isfile(BOT_BAT):
        subprocess.Popen([BOT_BAT], cwd=_CONTROL_DIR,
                         creationflags=subprocess.CREATE_NEW_CONSOLE)
        return {"ok": True}
    return {"ok": False, "error": "bot.bat not found"}


def start_watch() -> dict:
    if os.path.isfile(WATCH_BAT):
        subprocess.Popen([WATCH_BAT], cwd=_CONTROL_DIR,
                         creationflags=subprocess.CREATE_NEW_CONSOLE)
        return {"ok": True}
    return {"ok": False, "error": "watch.bat not found"}


def map_drive() -> dict:
    """Run the Tailscale E: drive mapper (laptop-side)."""
    if not os.path.isfile(MAP_DRIVE_SCRIPT):
        return {"ok": False, "error": "map-video-drive.ps1 not found"}
    try:
        r = subprocess.run(
            ["powershell", "-NoProfile", "-ExecutionPolicy", "Bypass",
             "-File", MAP_DRIVE_SCRIPT],
            capture_output=True, text=True, timeout=30, creationflags=_NO_WINDOW)
        return {"ok": r.returncode == 0, "output": (r.stdout or r.stderr or "").strip()}
    except Exception as e:
        return {"ok": False, "error": str(e)}


def create_project(title: str, category: str, base_dir: str,
                   files_to_copy: list[str], gen_proxy: bool, progress=None) -> dict:
    """Create the folder tree + Resolve project, copy files into Raw Footage,
    optionally queue proxies. `progress(pct, msg)` optional. Returns a summary.
    """
    import shutil

    def _p(msg):
        if progress:
            try:
                progress(None, msg)
            except Exception:
                pass

    if not is_resolve_running():
        _p("מפעיל את DaVinci Resolve…")
        res = launch_resolve(wait=True)
        if not res.get("ok"):
            return {"ok": False, "error": res.get("error", "Resolve did not start")}

    _p("יוצר תיקיות ופרויקט Resolve…")
    try:
        result = subprocess.run(
            ["py", "-3.10", NEW_PROJECT_SCRIPT, title,
             "--category", category, "--base-dir", base_dir],
            capture_output=True, text=True, timeout=120, creationflags=_NO_WINDOW)
    except subprocess.TimeoutExpired:
        return {"ok": False, "error": "הסקריפט חרג מזמן (2 דקות)"}
    except FileNotFoundError:
        return {"ok": False, "error": "לא נמצא py launcher — האם Python 3.10 מותקן?"}
    if result.returncode != 0:
        return {"ok": False, "error": (result.stderr or result.stdout or "unknown").strip()}

    project_path = project_name = resolve_name = None
    for line in (result.stdout or "").splitlines():
        if line.startswith("RESULT_PATH="):
            project_path = line[len("RESULT_PATH="):].strip()
        elif line.startswith("RESULT_NAME="):
            project_name = line[len("RESULT_NAME="):].strip()
        elif line.startswith("RESULT_RESOLVE_NAME="):
            resolve_name = line[len("RESULT_RESOLVE_NAME="):].strip()
    if not project_path or not os.path.isdir(project_path):
        return {"ok": False, "error": "הסקריפט רץ אך לא נמצאה תיקיית פרויקט"}

    raw_dir = os.path.join(project_path, "Raw Footage")
    copied, copy_errors = [], []
    for src in files_to_copy:
        _p(f"מעתיק {os.path.basename(src)}…")
        try:
            dst = os.path.join(raw_dir, os.path.basename(src))
            shutil.copyfile(src, dst)
            copied.append(dst)
        except Exception as e:
            copy_errors.append(f"{os.path.basename(src)}: {e}")

    proxies_queued = 0
    if gen_proxy and copied and resolve_name and os.path.isfile(IMPORT_PROXY_SCRIPT):
        _p("מייבא ל-media pool ומכין פרוקסי…")
        try:
            px = subprocess.run(
                ["py", "-3.10", IMPORT_PROXY_SCRIPT, "--project", resolve_name,
                 "--files", *copied],
                capture_output=True, text=True, timeout=120, creationflags=_NO_WINDOW)
            for line in (px.stdout or "").splitlines():
                if line.startswith("PROXIES_QUEUED="):
                    try:
                        proxies_queued = int(line[len("PROXIES_QUEUED="):].strip())
                    except ValueError:
                        proxies_queued = 0
        except Exception:
            proxies_queued = 0

    return {"ok": True, "project_path": project_path, "project_name": project_name,
            "copied": copied, "copy_errors": copy_errors,
            "resolve_created": bool(resolve_name), "proxies_queued": proxies_queued}
