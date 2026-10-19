"""Plugging the Osmo in starts the import by itself (2026-10-19).

The import itself is stubbed; what is tested is the decision around it: switched
off, a backup folder whose drive is missing or is the camera, the one-import lock,
and the end callback.
"""
import os
import sys
import threading
import time

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import jobs  # noqa: E402
import osmo_import  # noqa: E402
import server  # noqa: E402


@pytest.fixture
def settings(monkeypatch, tmp_path):
    store = {"osmo_backup_root": str(tmp_path / "archive")}
    monkeypatch.setattr(osmo_import, "get_setting", lambda k, d=None: store.get(k, d))
    monkeypatch.setattr(osmo_import, "set_setting", lambda k, v: store.__setitem__(k, v))
    return store


def _wait(jid):
    for _ in range(200):
        if jobs.get(jid)["state"] != "running":
            return jobs.get(jid)
        time.sleep(0.02)
    raise AssertionError("job never finished")


def test_on_by_default_and_switched_off_from_the_config_route(settings):
    assert osmo_import.auto_import_enabled() is True
    c = server.app.test_client()
    assert c.get("/api/osmo/config").get_json()["auto_import"] is True
    assert c.post("/api/osmo/config", json={"auto_import": False}).get_json()["auto_import"] is False
    assert server.start_auto_import("X:\\DCIM") == (None, "off")


def test_a_usable_backup_root(tmp_path):
    root = str(tmp_path / "archive")
    drive = os.path.splitdrive(root)[0]
    assert osmo_import.backup_root_usable(root) is True
    # Away from home the Osmo can take the backup drive's letter: never copy onto the camera.
    assert osmo_import.backup_root_usable(root, camera_root=drive + "\\") is False
    assert osmo_import.backup_root_usable("") is False
    missing = next(f"{c}:\\Archive" for c in "QRSTUVWXYZ" if not os.path.isdir(f"{c}:\\"))
    assert osmo_import.backup_root_usable(missing) is False


def test_an_unreachable_backup_root_starts_nothing(settings, monkeypatch):
    monkeypatch.setattr(osmo_import, "run_import", lambda *a, **k: pytest.fail("must not run"))
    settings["osmo_backup_root"] = next(f"{c}:\\Archive" for c in "QRSTUVWXYZ"
                                        if not os.path.isdir(f"{c}:\\"))
    assert server.start_auto_import("X:\\DCIM") == (None, "unreachable")


def test_it_runs_the_import_with_the_defaults_and_reports_the_end(settings, monkeypatch):
    seen, ended = {}, []

    def fake(source, options, progress=None):
        seen.update(source=source, options=options)
        progress(50, "מעתיק")
        return {"copied": ["a.mp4"], "errors": []}
    monkeypatch.setattr(osmo_import, "run_import", fake)
    jid, why = server.start_auto_import("X:\\DCIM", on_done=lambda r, e: ended.append((r, e)))
    assert why is None
    assert _wait(jid)["state"] == "done"
    assert seen["source"] == "X:\\DCIM"
    assert seen["options"] == osmo_import.auto_options()
    assert ended == [({"copied": ["a.mp4"], "errors": []}, None)]


def test_a_failed_import_is_reported_too(settings, monkeypatch):
    def boom(*a, **k):
        raise OSError("disk full")
    monkeypatch.setattr(osmo_import, "run_import", boom)
    ended = []
    jid, _ = server.start_auto_import("X:\\DCIM", on_done=lambda r, e: ended.append((r, e)))
    assert _wait(jid)["state"] == "error"
    assert ended == [(None, "disk full")]


def test_never_a_second_import_beside_a_running_one(settings, monkeypatch):
    gate = threading.Event()
    monkeypatch.setattr(osmo_import, "run_import", lambda *a, **k: gate.wait(10) and {})
    first, why = server.start_auto_import("X:\\DCIM")
    assert why is None
    assert server.start_auto_import("X:\\DCIM") == (None, "busy")
    gate.set()
    _wait(first)
