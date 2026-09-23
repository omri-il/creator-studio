"""Tiny in-memory job registry with live progress — same pattern as video-prep.

A job runs a worker function on a daemon thread. The worker receives an
`update(pct=None, message=None)` callback and may return a result dict that is
merged into the job. The web UI polls GET /api/job/<id> once a second.

A job may carry a `kind`. `run(..., exclusive=(kinds…))` refuses to start while
a job of any of those kinds is still running — checked and registered under one
lock, so two requests racing in cannot both get through. That is what stops a
second Osmo import from running over the first one (2026-09-23).
"""
from __future__ import annotations

import threading
import uuid

_JOBS: dict[str, dict] = {}
_LOCK = threading.Lock()


class JobBusy(Exception):
    """Raised by run() when an exclusive job is already running. `.job` is a
    snapshot of that job, so the caller can show its progress instead."""

    def __init__(self, job: dict):
        super().__init__(f"job {job.get('id')} ({job.get('kind')}) is running")
        self.job = job


def _running_unlocked(kinds) -> dict | None:
    for j in _JOBS.values():
        if j["state"] == "running" and j.get("kind") in kinds:
            return dict(j)
    return None


def running(*kinds: str) -> dict | None:
    """The running job of any of `kinds`, or None."""
    with _LOCK:
        return _running_unlocked(kinds)


def _new(kind: str | None = None, exclusive=()) -> str:
    jid = uuid.uuid4().hex[:12]
    with _LOCK:
        if exclusive:
            busy = _running_unlocked(exclusive)
            if busy:
                raise JobBusy(busy)
        _JOBS[jid] = {"id": jid, "kind": kind, "state": "running", "progress": 0.0,
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


def run(worker, kind: str | None = None, exclusive=()) -> str:
    """Start `worker(update)` on a thread. `update(pct=None, message=None)`
    reports progress. Worker's return value (dict) is stored as `result`.
    Raises JobBusy (and starts nothing) if a job of a kind in `exclusive` runs."""
    jid = _new(kind, tuple(exclusive))

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
