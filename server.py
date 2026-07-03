"""Flask backend for Creator Studio — serves the web UI and a JSON API that the
pywebview window (and, if wanted, a browser) talk to over fetch.

The heavy work (import, merge, transcribe, normalize) runs as background jobs;
the UI polls GET /api/job/<id>. State that must be shared with the desktop shell
(the MicMonitor, the native folder-picker) is injected by tracker.py via the
setters below.
"""
from __future__ import annotations

import os
import subprocess

from flask import Flask, jsonify, request, send_from_directory

import audio_tools
import davinci
import jobs
import mediatools
import osmo_import
from settings_store import APP_NAME, APP_VERSION, WEB_DIR

app = Flask(__name__, static_folder=None)

# Injected by tracker.py (the desktop shell owns these).
_MIC = None
_PICK_FOLDER = None


def set_mic_monitor(monitor):
    global _MIC
    _MIC = monitor


def set_folder_picker(fn):
    global _PICK_FOLDER
    _PICK_FOLDER = fn


# ── static / UI ───────────────────────────────────────────────────────────────
@app.route("/")
def index():
    return send_from_directory(WEB_DIR, "index.html")


@app.route("/<path:filename>")
def static_files(filename):
    return send_from_directory(WEB_DIR, filename)


# ── home summary ──────────────────────────────────────────────────────────────
@app.route("/api/status")
def api_status():
    return jsonify({
        "app": APP_NAME, "version": APP_VERSION,
        "features": {
            "davinci": davinci.HAS_DAVINCI,
            "dashboard": davinci.HAS_DASHBOARD,
            "transcribe": osmo_import.HAS_TRANSCRIBE,
            "map_drive": os.path.isfile(davinci.MAP_DRIVE_SCRIPT),
        },
        "mic": _MIC.status() if _MIC else None,
        "osmo_backup_root": osmo_import.get_backup_root(),
    })


# ── mic ───────────────────────────────────────────────────────────────────────
@app.route("/api/mic")
def api_mic():
    return jsonify(_MIC.status() if _MIC else {"volume": None, "locked": False, "target": 90})


@app.route("/api/mic/lock", methods=["POST"])
def api_mic_lock():
    if not _MIC:
        return jsonify({"ok": False, "error": "mic monitor unavailable"}), 503
    data = request.get_json(force=True, silent=True) or {}
    if "target" in data:
        _MIC.set_target(int(data["target"]))
    if data.get("locked"):
        _MIC.enable_lock(data.get("target"))
    else:
        _MIC.disable_lock()
    return jsonify({"ok": True, **_MIC.status()})


# ── osmo import ───────────────────────────────────────────────────────────────
@app.route("/api/osmo/detect")
def api_osmo_detect():
    return jsonify({"drives": osmo_import.detect_camera_drives()})


@app.route("/api/osmo/scan", methods=["POST"])
def api_osmo_scan():
    data = request.get_json(force=True, silent=True) or {}
    source = data.get("source")
    if not source or not os.path.isdir(source):
        return jsonify({"ok": False, "error": "תיקיית מקור לא קיימת"}), 400
    try:
        scan = osmo_import.scan_source(source, data.get("backup_root"))
    except Exception as e:  # noqa: BLE001
        return jsonify({"ok": False, "error": str(e)}), 500
    # Trim clip dicts to what the UI needs (avoid dumping every field).
    for s in scan["sessions"]:
        s["clips"] = [{"name": c["name"], "duration": c.get("duration"),
                       "size": c.get("size"), "already": c.get("already"),
                       "width": c.get("width"), "height": c.get("height"),
                       "fps": c.get("fps")} for c in s["clips"]]
    return jsonify({"ok": True, **scan})


@app.route("/api/osmo/import", methods=["POST"])
def api_osmo_import():
    data = request.get_json(force=True, silent=True) or {}
    source = data.get("source")
    if not source or not os.path.isdir(source):
        return jsonify({"ok": False, "error": "תיקיית מקור לא קיימת"}), 400
    options = {
        "merge": bool(data.get("merge", True)),
        "transcribe": bool(data.get("transcribe", True)),
        "keep_originals": bool(data.get("keep_originals", True)),
        "backup_root": data.get("backup_root"),
    }
    jid = jobs.run(lambda update: osmo_import.run_import(
        source, options, progress=lambda pct, msg: update(pct, msg)))
    return jsonify({"ok": True, "id": jid})


@app.route("/api/osmo/transcribe", methods=["POST"])
def api_osmo_transcribe():
    """(Re)transcribe already-imported files — used when the import's transcription
    phase was skipped or failed (e.g. a transient model lock). The idempotent
    import won't re-reach transcription on its own, so this targets files directly."""
    data = request.get_json(force=True, silent=True) or {}
    paths = [p for p in (data.get("paths") or []) if isinstance(p, str) and p]
    if not paths:
        return jsonify({"ok": False, "error": "לא צוינו קבצים לתמלול"}), 400
    if not osmo_import.HAS_TRANSCRIBE:
        return jsonify({"ok": False, "error": "סקריפט התמלול לא נמצא"}), 503
    dest_dir = data.get("dest_dir") or os.path.dirname(paths[0])
    jid = jobs.run(lambda update: osmo_import.transcribe_files(
        paths, dest_dir, progress=lambda pct, msg: update(pct, msg)))
    return jsonify({"ok": True, "id": jid})


@app.route("/api/osmo/config", methods=["GET", "POST"])
def api_osmo_config():
    if request.method == "POST":
        data = request.get_json(force=True, silent=True) or {}
        if data.get("backup_root"):
            osmo_import.set_backup_root(data["backup_root"])
    return jsonify({"backup_root": osmo_import.get_backup_root()})


# ── davinci + tools ───────────────────────────────────────────────────────────
@app.route("/api/davinci/launch", methods=["POST"])
def api_davinci_launch():
    return jsonify(davinci.launch_resolve(wait=False))


@app.route("/api/davinci/dashboard", methods=["POST"])
def api_davinci_dashboard():
    return jsonify(davinci.open_dashboard())


@app.route("/api/davinci/bot", methods=["POST"])
def api_davinci_bot():
    return jsonify(davinci.start_bot())


@app.route("/api/davinci/watch", methods=["POST"])
def api_davinci_watch():
    return jsonify(davinci.start_watch())


@app.route("/api/davinci/map-drive", methods=["POST"])
def api_davinci_map_drive():
    return jsonify(davinci.map_drive())


@app.route("/api/davinci/categories")
def api_davinci_categories():
    return jsonify({"categories": davinci.CATEGORIES, "base_dir": davinci.get_base_dir()})


@app.route("/api/audio/analyze", methods=["POST"])
def api_audio_analyze():
    data = request.get_json(force=True, silent=True) or {}
    path = data.get("path")
    if not path or not os.path.isfile(path):
        return jsonify({"ok": False, "error": "קובץ לא קיים"}), 400

    def worker(update):
        res = audio_tools.analyze_audio_file(path, progress_cb=lambda p, t: update(p, t))
        if res is None:
            raise RuntimeError("הניתוח נכשל — בדוק ש-ffmpeg זמין והקובץ קריא")
        verdict, color, obs, rec = audio_tools.youtube_verdict(
            res["integrated_lufs"], res["true_peak_dbfs"])
        return {**res, "verdict": verdict, "color": color,
                "observations": obs, "recommendation": rec, "path": path}

    return jsonify({"ok": True, "id": jobs.run(worker)})


@app.route("/api/audio/normalize", methods=["POST"])
def api_audio_normalize():
    data = request.get_json(force=True, silent=True) or {}
    path = data.get("path")
    if not path or not os.path.isfile(path):
        return jsonify({"ok": False, "error": "קובץ לא קיים"}), 400
    stem, ext = os.path.splitext(path)
    out = f"{stem}_normalized{ext}"

    def worker(update):
        res = audio_tools.normalize_audio_file(
            path, out, progress_cb=lambda stage, t: update(None, t))
        if res is None:
            raise RuntimeError("הנרמול נכשל")
        return res

    return jsonify({"ok": True, "id": jobs.run(worker)})


# ── jobs + utils ──────────────────────────────────────────────────────────────
@app.route("/api/job/<jid>")
def api_job(jid):
    j = jobs.get(jid)
    if not j:
        return jsonify({"error": "not found"}), 404
    return jsonify(j)


@app.route("/api/reveal", methods=["POST"])
def api_reveal():
    data = request.get_json(force=True, silent=True) or {}
    path = data.get("path")
    if not path or not os.path.exists(path):
        return jsonify({"ok": False}), 400
    try:
        if os.path.isdir(path):
            os.startfile(path)  # noqa: S606
        else:
            subprocess.Popen(["explorer", "/select,", os.path.normpath(path)])
        return jsonify({"ok": True})
    except Exception as e:  # noqa: BLE001
        return jsonify({"ok": False, "error": str(e)}), 500


@app.route("/api/pick", methods=["POST"])
def api_pick():
    if not _PICK_FOLDER:
        return jsonify({"ok": False, "error": "picker unavailable"}), 503
    data = request.get_json(force=True, silent=True) or {}
    kind = data.get("kind", "folder")
    try:
        path = _PICK_FOLDER(kind)
        return jsonify({"ok": bool(path), "path": path})
    except Exception as e:  # noqa: BLE001
        return jsonify({"ok": False, "error": str(e)}), 500


def create_app():
    return app
