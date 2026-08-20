"""Event-Engine media API client — stdlib only, same reasoning as wistia.py.

Talks to the two token-guarded endpoints on the Event King instance:
  GET  /api/media/events
  POST /api/media/event/<id>/vsl

Errors are RETURNED, not raised, everywhere the UI is the caller: an upload
that succeeded must never look like it failed because the event could not be
updated afterwards. The video is on Wistia either way, and the link can always
be pasted into the admin by hand.
"""
from __future__ import annotations

import json
import urllib.error
import urllib.request

TIMEOUT = 20


class EventEngineError(Exception):
    def __init__(self, message, status=None):
        super().__init__(message)
        self.status = status


def _request(base_url, token, path, payload=None, opener=None):
    base = (base_url or "").rstrip("/")
    if not base:
        raise EventEngineError("No Event-Engine URL configured "
                               "(EVENT_ENGINE_URL).")
    if not token:
        raise EventEngineError("No Event-Engine API token configured "
                               "(EVENT_ENGINE_TOKEN).")

    data = None
    if payload is not None:
        data = json.dumps(payload).encode("utf-8")
    req = urllib.request.Request(base + path, data=data,
                                 method="POST" if data else "GET")
    req.add_header("X-Api-Key", token)
    if data:
        req.add_header("Content-Type", "application/json")

    urlopen = opener or urllib.request.urlopen
    try:
        with urlopen(req, timeout=TIMEOUT) as resp:
            return json.loads(resp.read().decode("utf-8", "replace"))
    except urllib.error.HTTPError as e:
        body = e.read().decode("utf-8", "replace")
        raise EventEngineError(_message(e.code, body), status=e.code) from e
    except urllib.error.URLError as e:
        raise EventEngineError("Could not reach Event-Engine: "
                               + str(e.reason)) from e
    except ValueError as e:
        raise EventEngineError("Event-Engine returned a non-JSON response") from e


def _message(status, body):
    try:
        parsed = json.loads(body)
    except ValueError:
        parsed = {}
    if status == 401:
        return ("Event-Engine rejected the API key (401). Check "
                "EVENT_ENGINE_TOKEN against MEDIA_API_TOKEN in that "
                "instance's .env.")
    if status == 503:
        return ("Event-Engine has no MEDIA_API_TOKEN set (503), so the media "
                "API is switched off on that instance.")
    if status == 404:
        return "Event-Engine does not have that event (404)."
    if status == 400:
        return parsed.get("message") or ("Event-Engine refused the video URL "
                                         "(400).")
    return "Event-Engine request failed (HTTP " + str(status) + ")."


def list_events(base_url, token, opener=None):
    """Active events, upcoming first. Raises EventEngineError."""
    return _request(base_url, token, "/api/media/events",
                    opener=opener).get("events", [])


def set_vsl(base_url, token, event_id, url, opener=None):
    """Point one event at a video. `url=""` clears it. Raises on failure."""
    return _request(base_url, token,
                    "/api/media/event/%d/vsl" % int(event_id),
                    payload={"vsl_url": url or ""}, opener=opener)
