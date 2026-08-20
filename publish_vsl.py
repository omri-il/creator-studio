"""CLI: render in Resolve, then put the video on Wistia and on the event.

    py -3.10 publish_vsl.py "E:\\Video Projects\\...\\vsl.mp4" --pick
    py -3.10 publish_vsl.py --latest --event 18
    py -3.10 publish_vsl.py --latest --dry-run

Configuration lives in .env (WISTIA_API_TOKEN, EVENT_ENGINE_TOKEN) and
settings.json (wistia_project_id, wistia_subdomain, event_engine_url,
vsl_exports_dir) — see settings_store.py. The publish logic itself is in
vsl_publish.py, shared with the Creator Studio button so the two cannot drift.
"""
from __future__ import annotations

import argparse
import os
import subprocess
import sys

import eventengine
import vsl_publish
import wistia


def _copy_to_clipboard(text):
    try:
        p = subprocess.Popen("clip", stdin=subprocess.PIPE, shell=True)
        p.communicate(text.encode("utf-16-le"))
        return p.returncode == 0
    except Exception:                                        # noqa: BLE001
        return False


def _resolve_path(args, cfg):
    if args.path:
        if not os.path.isfile(args.path):
            sys.exit("No such file: %s" % args.path)
        return args.path
    folder = args.latest if isinstance(args.latest, str) else cfg["exports_dir"]
    if not folder:
        sys.exit("--latest needs a folder: pass one, or set `vsl_exports_dir` "
                 "in settings.json.")
    found = vsl_publish.latest_video(folder)
    if not found:
        sys.exit("No video files found in %s" % folder)
    print("Newest video in %s:\n  %s" % (folder, found))
    return found


def _choose_event(cfg):
    try:
        events = eventengine.list_events(cfg["event_engine_url"],
                                         cfg["event_engine_token"])
    except eventengine.EventEngineError as e:
        print("Could not list events: %s" % e)
        print("Uploading anyway — you can paste the link in by hand.")
        return None
    if not events:
        print("No active events. Uploading without setting one.")
        return None

    print("\nActive events:")
    for i, ev in enumerate(events, 1):
        has = "  [has a video]" if ev.get("vsl_url") else ""
        print("  %2d) %-40s %s%s" % (i, ev["name"][:40],
                                     ev["starts_at"][:16], has))
    print("   0) none — just upload")
    while True:
        raw = input("Which event? ").strip()
        if raw in ("0", ""):
            return None
        if raw.isdigit() and 1 <= int(raw) <= len(events):
            return events[int(raw) - 1]["id"]
        print("Pick a number from the list.")


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("path", nargs="?", help="the video file to publish")
    ap.add_argument("--latest", nargs="?", const=True, default=None,
                    metavar="FOLDER",
                    help="use the newest video in FOLDER (default: the "
                         "configured exports folder)")
    ap.add_argument("--event", type=int, help="set this event's VSL")
    ap.add_argument("--pick", action="store_true",
                    help="choose the event from a menu")
    ap.add_argument("--name", help="title in Wistia (default: the filename)")
    ap.add_argument("--yes", action="store_true",
                    help="skip the size confirmation")
    ap.add_argument("--dry-run", action="store_true",
                    help="do everything except the two network writes")
    args = ap.parse_args(argv)

    if not args.path and args.latest is None:
        ap.error("give a file path, or --latest")

    cfg = vsl_publish.config()
    if not cfg["wistia_token"] and not args.dry_run:
        sys.exit("No WISTIA_API_TOKEN in .env — Wistia account -> Settings -> "
                 "API Access, then add WISTIA_API_TOKEN=... to %s"
                 % __import__("settings_store").ENV_FILE)

    path = _resolve_path(args, cfg)
    info = vsl_publish.inspect(path)
    print("\n%s  —  %s, roughly %s minutes to upload"
          % (info["name"], info["size_human"], info["minutes"]))
    if info["warn"]:
        print("\n  !! " + info["warn_text"] + "\n")
        if not args.yes and not args.dry_run:
            if input("Upload it anyway? [y/N] ").strip().lower() not in ("y", "yes"):
                sys.exit("Stopped.")

    event_id = args.event
    if event_id is None and args.pick and not args.dry_run:
        event_id = _choose_event(cfg)
    elif event_id is None and args.pick:
        print("(dry run: skipping the event menu)")

    last = [""]

    def progress(pct, message):
        if message != last[0]:
            last[0] = message
            sys.stdout.write("\r" + " " * 78 + "\r" + message)
        else:
            sys.stdout.write("\r%-78s" % message)
        sys.stdout.flush()

    try:
        result = vsl_publish.publish(path, event_id=event_id, name=args.name,
                                     progress=progress, dry_run=args.dry_run,
                                     cfg=cfg)
    except (wistia.WistiaError, eventengine.EventEngineError) as e:
        print()
        sys.exit(str(e))

    print("\n\n" + result["video_url"])
    if _copy_to_clipboard(result["video_url"]):
        print("(copied to the clipboard)")
    if result.get("event_error"):
        print("\n!! The video uploaded fine, but the event was NOT updated:")
        print("   %s" % result["event_error"])
        print("   Paste the link above into the event's VSL field by hand.")
    elif result.get("event"):
        ev = result["event"]
        if ev.get("dry_run"):
            print("(dry run: event %s not touched)" % ev["id"])
        else:
            print("Live on: %s" % ev.get("public_url", ""))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
