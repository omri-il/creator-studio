"""Tiny in-memory job registry with live progress — same pattern as video-prep.

A job runs a worker function on a daemon thread. The worker receives an
`update(pct=None, message=None)` callback and may return a result dict that is
merged into the job. The web UI polls GET /api/job/<id> once a second.
"""
from __future__ import annotations

import threading
import uuid

_JOBS: dict[str, dict] = {}
_LOCK = threading.Lock()


def _new() -> str:
    jid = uuid.uuid4().hex[:12]
    with _LOCK:
        _JOBS[jid] = {"id": jid, "state": "running", "progress": 0.0,
                      "message": "מתחיל…", "result": None, "error": ""}
    return jid


def set_fields(jid: str, **fields) -> None:
    with _LOCK:
        if jid in _JOBS:
            _JOBS[jid].update(fields)


def get(jid: str) -> dict | None:
    with _LOCK:
        j = _JOBS.get(jid)
        return dict(j) if j else None


def run(worker) -> str:
    """Start `worker(update)` on a thread. `update(pct=None, message=None)`
    reports progress. Worker's return value (dict) is stored as `result`."""
    jid = _new()

    def _update(pct=None, message=None):
        fields = {}
        if pct is not None:
            fields["progress"] = float(pct)
        if message is not None:
            fields["message"] = message
        if fields:
            set_fields(jid, **fields)

    def _work():
        try:
            result = worker(_update)
            set_fields(jid, state="done", progress=100.0, result=result,
                       message="הושלם")
        except Exception as e:  # noqa: BLE001
            set_fields(jid, state="error", error=str(e), message=f"שגיאה: {e}")

    threading.Thread(target=_work, daemon=True).start()
    return jid
