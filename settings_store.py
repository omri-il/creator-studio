"""Shared paths, settings persistence, and ffmpeg resolution for Creator Studio.

Single source of truth for: where user data lives (settings.json, logs), and
which ffmpeg binary to use. Every other module imports from here so paths stay
consistent between the dev run and the packaged .exe.
"""
from __future__ import annotations

import json
import os
import sys

# ── App identity ──────────────────────────────────────────────────────────────
APP_VERSION = "2.0.0"
APP_NAME = "Creator Studio"          # rebuilt, windowed successor to "Studio Flow"
SERVER_HOST = "127.0.0.1"
SERVER_PORT = 5015                   # distinct from video-prep (5005) / dashboard (5007)

# ── Paths ─────────────────────────────────────────────────────────────────────
# When frozen by PyInstaller, __file__ is inside a temp dir; sys.executable is
# the real .exe. Use that for stable, writable paths.
_IS_FROZEN = getattr(sys, "frozen", False)
_APP_DIR = (
    os.path.dirname(sys.executable)
    if _IS_FROZEN
    else os.path.dirname(os.path.abspath(__file__))
)
# Bundled read-only resources (web/, ffmpeg.exe, assets/, laptop-setup/) live in
# PyInstaller's _internal dir (sys._MEIPASS) when frozen; the repo root in dev.
_RES_DIR = getattr(sys, "_MEIPASS", _APP_DIR)

if _IS_FROZEN:
    # Program Files is read-only for non-admin users → keep user data in LOCALAPPDATA.
    _USER_DIR = os.path.join(
        os.environ.get("LOCALAPPDATA", os.path.expanduser("~")), "StudioFlow"
    )
    os.makedirs(_USER_DIR, exist_ok=True)
else:
    _USER_DIR = _APP_DIR

SETTINGS_FILE = os.path.join(_USER_DIR, "settings.json")
LOG_FILE = os.path.join(_USER_DIR, "mic_log.txt")

# Where the web UI assets live (bundled under _internal/web when frozen).
WEB_DIR = os.path.join(_RES_DIR, "web")


def _resolve_ffmpeg() -> str:
    """Pick an ffmpeg binary, preferring one bundled next to the app.

    Order: FFMPEG env override → bundled next to exe → vendored copy in repo →
    the Python 3.10 install dir (where the rest of Omri's tools keep it) → PATH.
    """
    override = os.environ.get("FFMPEG")
    if override and os.path.isfile(override):
        return override
    candidates = [
        os.path.join(_RES_DIR, "ffmpeg.exe"),            # bundled (frozen: _internal)
        os.path.join(_APP_DIR, "ffmpeg.exe"),            # next to the .exe
        os.path.join(_APP_DIR, "vendor", "ffmpeg.exe"),  # repo dev layout
        r"C:\Users\omrii\AppData\Local\Programs\Python\Python310\ffmpeg.exe",
    ]
    for c in candidates:
        if os.path.isfile(c):
            return c
    return "ffmpeg"


FFMPEG = _resolve_ffmpeg()


# ── Settings (flat JSON dict, no schema) ──────────────────────────────────────
def load_settings() -> dict:
    try:
        with open(SETTINGS_FILE, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return {}


def save_settings(settings: dict) -> None:
    try:
        with open(SETTINGS_FILE, "w", encoding="utf-8") as f:
            json.dump(settings, f, indent=2, ensure_ascii=False)
    except Exception:
        pass


def get_setting(key: str, default=None):
    return load_settings().get(key, default)


def set_setting(key: str, value) -> None:
    s = load_settings()
    s[key] = value
    save_settings(s)
