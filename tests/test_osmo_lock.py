"""One Osmo import at a time (2026-09-23: an API-started import and one started
from the window ~85 min later ran concurrently into the same folder).

The worker is stubbed with one that blocks on an Event, so "still running" is
real, not simulated.
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
def client():
    server.app.config["TESTING"] = True
    return server.app.test_client()


@pytest.fixture
def gate(monkeypatch):
    """Stub both Osmo workers so each runs until `gate.set()`."""
    ev = threading.Event()

    def _block(*a, **k):
        assert ev.wait(10)
        return {"ok": True}
    monkeypatch.setattr(osmo_import, "run_import", _block)
    monkeypatch.setattr(osmo_import, "transcribe_files", _block)
    monkeypatch.setattr(osmo_import, "HAS_TRANSCRIBE", True)
    yield ev
    ev.set()


def _wait_done(jid):
    for _ in range(200):
        if jobs.get(jid)["state"] != "running":
            return
        time.sleep(0.02)
    raise AssertionError("job never finished")


def test_jobs_run_refuses_a_second_exclusive_job():
    ev = threading.Event()
    first = jobs.run(lambda u: ev.wait(10), kind="k", exclusive=("k",))
    with pytest.raises(jobs.JobBusy) as e:
        jobs.run(lambda u: None, kind="k", exclusive=("k",))
    assert e.value.job["id"] == first
    jobs.run(lambda u: None, kind="other")        # other kinds are unaffected
    ev.set()
    _wait_done(first)
    jobs.run(lambda u: None, kind="k", exclusive=("k",))   # free again


def test_second_import_is_refused_while_one_runs(client, gate, tmp_path):
    r1 = client.post("/api/osmo/import", json={"source": str(tmp_path)})
    assert r1.status_code == 200
    jid = r1.get_json()["id"]

    r2 = client.post("/api/osmo/import", json={"source": str(tmp_path)})
    body = r2.get_json()
    assert r2.status_code == 409 and body["busy"] is True
    assert body["job"]["id"] == jid
    assert "ייבוא כבר רץ" in body["error"]

    assert client.get("/api/osmo/active").get_json()["job"]["id"] == jid
    gate.set()
    _wait_done(jid)
    assert client.get("/api/osmo/active").get_json()["job"] is None


def test_transcribe_is_refused_during_an_import(client, gate, tmp_path):
    jid = client.post("/api/osmo/import", json={"source": str(tmp_path)}).get_json()["id"]
    r = client.post("/api/osmo/transcribe", json={"paths": [str(tmp_path / "a.mp4")]})
    assert r.status_code == 409 and r.get_json()["job"]["id"] == jid
    gate.set()
    _wait_done(jid)


def test_import_is_allowed_during_a_transcribe(client, gate, tmp_path):
    t = client.post("/api/osmo/transcribe", json={"paths": [str(tmp_path / "a.mp4")]})
    assert t.status_code == 200
    # A running transcription is not an import: no "busy" for the window…
    assert client.get("/api/osmo/active").get_json()["job"] is None
    # …and a new import may start.
    i = client.post("/api/osmo/import", json={"source": str(tmp_path)})
    assert i.status_code == 200
    # But a second transcribe is refused.
    t2 = client.post("/api/osmo/transcribe", json={"paths": [str(tmp_path / "a.mp4")]})
    assert t2.status_code == 409
    gate.set()
    _wait_done(t.get_json()["id"])
    _wait_done(i.get_json()["id"])
