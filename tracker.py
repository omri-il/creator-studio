"""Creator Studio — entry point.

The rebuilt, windowed successor to the old "Studio Flow" tray app. Instead of a
tray-first UI with a naggy floating mic bar, it opens a real desktop window
(pywebview + WebView2) served by an embedded Flask backend. Background threads
run silently: the mic lock and the Osmo camera watcher. Closing the window hides
it to a small tray icon so detection keeps working; quit from the tray.
"""
from __future__ import annotations

import os
import socket
import sys
import threading
import time

import webview

import mic as mic_mod
import server as server_mod
from settings_store import APP_NAME, SERVER_HOST, SERVER_PORT, _RES_DIR

# Frozen builds have a cp1252 (or absent) stdout; make logging never crash on
# unicode. Without this, a single emoji print killed startup before the window.
for _stream in (sys.stdout, sys.stderr):
    try:
        _stream.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass


def _log(msg: str):
    try:
        print(msg)
    except Exception:
        pass

URL = f"http://{SERVER_HOST}:{SERVER_PORT}/"

WINDOW = None
TRAY = None
MIC = None
_quitting = False


# ── Flask server (background thread) ──────────────────────────────────────────
def _start_server():
    server_mod.app.run(host=SERVER_HOST, port=SERVER_PORT,
                       threaded=True, use_reloader=False, debug=False)


def _wait_server(timeout=8.0):
    end = time.time() + timeout
    while time.time() < end:
        try:
            with socket.create_connection((SERVER_HOST, SERVER_PORT), 0.3):
                return True
        except OSError:
            time.sleep(0.1)
    return False


# ── native folder / file picker (called by the Flask API) ─────────────────────
def _pick(kind: str):
    if WINDOW is None:
        return None
    if kind == "file":
        res = WINDOW.create_file_dialog(
            webview.OPEN_DIALOG, allow_multiple=False,
            file_types=("קבצי מדיה (*.mp4;*.mov;*.mkv;*.webm;*.wav;*.mp3;*.m4a)",
                        "כל הקבצים (*.*)"))
    else:
        res = WINDOW.create_file_dialog(webview.FOLDER_DIALOG)
    if not res:
        return None
    return res[0] if isinstance(res, (list, tuple)) else res


# ── camera watcher (background thread) ────────────────────────────────────────
def _camera_watcher():
    import osmo_import
    last = None
    time.sleep(4)
    while not _quitting:
        try:
            drives = osmo_import.detect_camera_drives()
        except Exception:
            drives = []
        root = drives[0]["root"] if drives else None
        if root and root != last:
            # New camera plugged in → notify + surface the window (review-then-confirm).
            name = "DJI Osmo" if drives[0].get("is_dji") else "מצלמה"
            try:
                if TRAY is not None:
                    TRAY.notify(f"{name} מחוברת — פתח את Creator Studio לייבוא", "📷 זוהתה מצלמה")
            except Exception:
                pass
            _show_window()
        last = root
        time.sleep(4)


# ── tray (background thread) ──────────────────────────────────────────────────
def _load_icon():
    from PIL import Image, ImageDraw
    ico = os.path.join(_RES_DIR, "assets", "icon.ico")
    if os.path.isfile(ico):
        try:
            return Image.open(ico)
        except Exception:
            pass
    img = Image.new("RGBA", (64, 64), (0, 0, 0, 0))
    d = ImageDraw.Draw(img)
    d.ellipse([4, 4, 60, 60], fill=(255, 154, 46, 255))
    return img


def _show_window(*_):
    if WINDOW is not None:
        try:
            WINDOW.show()
            WINDOW.restore()
        except Exception:
            pass


def _quit(*_):
    global _quitting
    _quitting = True
    try:
        if MIC:
            MIC.stop()
    except Exception:
        pass
    try:
        if TRAY:
            TRAY.stop()
    except Exception:
        pass
    try:
        if WINDOW:
            WINDOW.destroy()
    except Exception:
        pass


def _start_tray():
    global TRAY
    import pystray
    TRAY = pystray.Icon(
        "creator_studio", _load_icon(), APP_NAME,
        menu=pystray.Menu(
            pystray.MenuItem(f"פתח את {APP_NAME}", _show_window, default=True),
            pystray.Menu.SEPARATOR,
            pystray.MenuItem("יציאה", _quit),
        ),
    )
    TRAY.run()   # blocks this (daemon) thread; creates its own message loop


# ── window close → hide to tray (keep background threads alive) ────────────────
def _on_closing():
    if _quitting:
        return True          # allow real close
    try:
        WINDOW.hide()
    except Exception:
        pass
    return False             # cancel the close → stays running in tray


def main():
    global WINDOW, MIC

    # 1) backend + mic monitor
    MIC = mic_mod.MicMonitor()
    MIC.start()
    server_mod.set_mic_monitor(MIC)
    server_mod.set_folder_picker(_pick)
    threading.Thread(target=_start_server, daemon=True).start()
    if not _wait_server():
        print("⚠ שרת ה-Flask לא עלה בזמן", file=sys.stderr)

    # 2) tray + camera watcher (background threads)
    threading.Thread(target=_start_tray, daemon=True).start()
    threading.Thread(target=_camera_watcher, daemon=True).start()

    # 3) the window (main thread owns the GUI loop)
    WINDOW = webview.create_window(
        APP_NAME, URL, width=1040, height=760, min_size=(840, 600),
        background_color="#0c0e12")
    WINDOW.events.closing += _on_closing
    _log(f"{APP_NAME} - {URL}")
    webview.start()   # blocks until the window is destroyed (Quit)


if __name__ == "__main__":
    main()
