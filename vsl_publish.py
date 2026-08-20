"""The VSL publish pipeline, shared by the CLI and the Creator Studio button.

One home for the logic so the two surfaces cannot drift — the `fftools.py` ->
`mediatools.py` copy-and-drift is the pattern this deliberately avoids.

Deliberately does NOT compress. A Resolve master can be 10GB+, but video-prep
is the documented home for compression (its normalize/size-variant work is
still designed-only), and building a second compressor here would duplicate the
feature that repo exists for. So: report the size, estimate the upload, and
WARN loudly above a threshold with the advice to export a delivery file.
"""
from __future__ import annotations

import os

import eventengine
import settings_store as store
import wistia

VIDEO_EXTS = (".mp4", ".mov", ".mkv", ".webm", ".m4v", ".avi")

# Above this, an upload stops being something to sit and watch. Not a hard
# limit — the upload still runs; the user is told what they are in for.
WARN_BYTES = 2 * 1024 ** 3          # 2 GB
# A conservative home-upload figure. Only ever used to say "roughly this long";
# being wrong by 2x still makes the difference between minutes and hours clear.
ASSUMED_MBPS = 20.0


# --------------------------------------------------------------------- config
def config():
    """Everything the publisher needs, secrets separated from preferences.

    Secrets come from .env (gitignored); preferences from settings.json, which
    is not — so a token can never end up in the settings file by accident.
    """
    return {
        "wistia_token": store.get_secret("WISTIA_API_TOKEN"),
        "event_engine_token": store.get_secret("EVENT_ENGINE_TOKEN"),
        "wistia_project_id": store.get_setting("wistia_project_id", "") or "",
        "wistia_subdomain": store.get_setting("wistia_subdomain", "") or "",
        "event_engine_url": store.get_setting(
            "event_engine_url", "https://events.omri-iram.co.il") or "",
        "exports_dir": store.get_setting("vsl_exports_dir", "") or "",
    }


def public_config():
    """The same, safe to hand to the browser: presence of each secret, never
    its value."""
    c = config()
    return {
        "wistia_project_id": c["wistia_project_id"],
        "wistia_subdomain": c["wistia_subdomain"],
        "event_engine_url": c["event_engine_url"],
        "exports_dir": c["exports_dir"],
        "has_wistia_token": bool(c["wistia_token"]),
        "has_event_engine_token": bool(c["event_engine_token"]),
    }


# ----------------------------------------------------------------- file admin
def latest_video(folder):
    """The newest video file in `folder`, or None. What `--latest` resolves."""
    if not folder or not os.path.isdir(folder):
        return None
    best, best_mtime = None, -1.0
    for name in os.listdir(folder):
        if not name.lower().endswith(VIDEO_EXTS):
            continue
        path = os.path.join(folder, name)
        if not os.path.isfile(path):
            continue
        mtime = os.path.getmtime(path)
        if mtime > best_mtime:
            best, best_mtime = path, mtime
    return best


def human_size(n):
    for unit in ("B", "KB", "MB", "GB", "TB"):
        if n < 1024 or unit == "TB":
            return ("%.0f %s" % (n, unit)) if unit in ("B", "KB") \
                else ("%.1f %s" % (n, unit))
        n /= 1024.0


def estimate_minutes(size_bytes, mbps=ASSUMED_MBPS):
    if size_bytes <= 0 or mbps <= 0:
        return 0.0
    return (size_bytes * 8) / (mbps * 1_000_000) / 60.0


def inspect(path):
    """Size, an upload estimate, and whether it is big enough to warn about."""
    size = os.path.getsize(path)
    return {
        "path": path,
        "name": os.path.basename(path),
        "size": size,
        "size_human": human_size(size),
        "minutes": round(estimate_minutes(size), 1),
        "warn": size >= WARN_BYTES,
        "warn_text": (
            "This file is %s. Uploading it will take roughly %d minutes and "
            "Wistia will re-encode it anyway — a smaller delivery export "
            "(1080p H.264) uploads in a fraction of the time with no visible "
            "difference on a sign-up page."
            % (human_size(size), round(estimate_minutes(size)))
        ) if size >= WARN_BYTES else "",
    }


# -------------------------------------------------------------------- publish
def publish(path, *, event_id=None, name=None, progress=None, dry_run=False,
            cfg=None):
    """Upload `path` to Wistia and optionally point an event at it.

    `progress(pct, message)` mirrors jobs.py's update() signature so the
    Creator Studio job wrapper is a one-liner.

    Returns a dict: always `video_url`/`hashed_id`; `event` when one was set,
    and `event_error` when the upload worked but the event update did not —
    which is a partial success, not a failure, and must be reported as such.
    """
    cfg = cfg or config()
    info = inspect(path)

    def report(pct, message):
        if progress:
            progress(pct, message)

    if dry_run:
        report(100, "Dry run — nothing uploaded")
        out = {"dry_run": True, "file": info,
               "video_url": wistia.media_url("DRYRUN0000"),
               "hashed_id": "DRYRUN0000"}
        if event_id:
            out["event"] = {"id": int(event_id), "vsl_url": out["video_url"],
                            "dry_run": True}
        return out

    report(0, "Uploading %s (%s) to Wistia…" % (info["name"], info["size_human"]))

    def on_bytes(sent, total):
        # 0-90% is the upload; the last stretch is the event update, so the bar
        # never sits at 100% while there is still work to do.
        report(round(90.0 * sent / total, 1),
               "Uploading… %s / %s" % (human_size(sent), human_size(total)))

    media = wistia.upload(
        path,
        token=cfg["wistia_token"],
        project_id=cfg["wistia_project_id"] or None,
        name=name or os.path.splitext(info["name"])[0],
        progress=on_bytes,
    )
    hashed_id = media.get("hashed_id") or media.get("hashedId")
    if not hashed_id:
        raise wistia.WistiaError(
            "Wistia accepted the upload but returned no hashed_id.")

    video_url = (wistia.account_url(hashed_id, cfg["wistia_subdomain"])
                 if cfg["wistia_subdomain"] else wistia.media_url(hashed_id))
    out = {"hashed_id": hashed_id, "video_url": video_url, "file": info,
           "media_url": wistia.media_url(hashed_id)}

    if not event_id:
        report(100, "Uploaded. Paste the link into the event.")
        return out

    report(95, "Pointing the event at the video…")
    try:
        out["event"] = eventengine.set_vsl(
            cfg["event_engine_url"], cfg["event_engine_token"],
            int(event_id), video_url)
        report(100, "Done — the sign-up page is showing the video.")
    except eventengine.EventEngineError as e:
        # The video IS on Wistia. Reporting this as a failure would send the
        # user to re-upload a file that is already there.
        out["event_error"] = str(e)
        report(100, "Uploaded, but the event was not updated: %s" % e)
    return out
