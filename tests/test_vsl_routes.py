"""The Flask routes behind the VSL panel, plus the Event-Engine client.

Network stubbed throughout. The property worth pinning is that the two tokens
never leave the machine: the browser gets `has_*_token` booleans, and the event
listing is proxied so the page never needs the Event-Engine key at all.
"""
import io
import json
import os
import sys
import urllib.error

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import eventengine  # noqa: E402
import server  # noqa: E402
import settings_store  # noqa: E402
import vsl_publish  # noqa: E402
import wistia  # noqa: E402

CFG = {
    "wistia_token": "wtok", "event_engine_token": "eetok",
    "wistia_project_id": "", "wistia_subdomain": "",
    "event_engine_url": "https://events.example.com", "exports_dir": "",
}


@pytest.fixture
def client():
    server.app.config["TESTING"] = True
    return server.app.test_client()


# --------------------------------------------------- eventengine.py (client)

def _opener(payload=None, status=None, body="{}"):
    seen = {}

    def opener(req, timeout=None):
        seen["url"] = req.full_url
        seen["method"] = req.get_method()
        seen["key"] = req.get_header("X-api-key")
        seen["body"] = req.data
        if status:
            raise urllib.error.HTTPError(req.full_url, status, "e", {},
                                         io.BytesIO(body.encode()))

        class R:
            def read(self): return json.dumps(payload or {"ok": True}).encode()
            def __enter__(self): return self
            def __exit__(self, *a): return False
        return R()
    return opener, seen


def test_list_events_sends_the_key_and_returns_the_list():
    opener, seen = _opener({"ok": True, "events": [{"id": 1, "name": "x"}]})
    evs = eventengine.list_events("https://e.example.com/", "k", opener=opener)
    assert evs == [{"id": 1, "name": "x"}]
    assert seen["url"] == "https://e.example.com/api/media/events"
    assert seen["key"] == "k"
    assert seen["method"] == "GET"


def test_set_vsl_posts_json_to_the_event():
    opener, seen = _opener({"ok": True})
    eventengine.set_vsl("https://e.example.com", "k", 18, "https://v/1",
                        opener=opener)
    assert seen["url"] == "https://e.example.com/api/media/event/18/vsl"
    assert seen["method"] == "POST"
    assert json.loads(seen["body"]) == {"vsl_url": "https://v/1"}


def test_missing_url_or_token_is_refused_before_the_network():
    def explode(*a, **k):                     # pragma: no cover - must not run
        raise AssertionError("network touched")
    with pytest.raises(eventengine.EventEngineError, match="URL"):
        eventengine.list_events("", "k", opener=explode)
    with pytest.raises(eventengine.EventEngineError, match="token"):
        eventengine.list_events("https://e", "", opener=explode)


@pytest.mark.parametrize("status,needle", [
    (401, "rejected the API key"),
    (503, "MEDIA_API_TOKEN"),          # the GEG-safety answer, named as such
    (404, "does not have that event"),
])
def test_http_errors_map_to_messages_that_say_what_to_fix(status, needle):
    opener, _ = _opener(status=status)
    with pytest.raises(eventengine.EventEngineError, match=needle):
        eventengine.list_events("https://e", "k", opener=opener)


def test_a_400_surfaces_event_engines_own_message():
    opener, _ = _opener(status=400,
                        body='{"error":"unparseable_url","message":"Paste a normal link"}')
    with pytest.raises(eventengine.EventEngineError, match="Paste a normal link"):
        eventengine.set_vsl("https://e", "k", 1, "junk", opener=opener)


# ------------------------------------------------------------------- routes

def test_config_route_never_returns_the_tokens(client, monkeypatch):
    monkeypatch.setattr(vsl_publish, "config", lambda: dict(CFG))
    body = client.get("/api/wistia/config").get_json()
    assert body["has_wistia_token"] is True
    assert body["has_event_engine_token"] is True
    assert "wtok" not in json.dumps(body)
    assert "eetok" not in json.dumps(body)


def test_config_post_saves_only_preferences(client, monkeypatch):
    saved = {}
    monkeypatch.setattr(settings_store, "set_setting",
                        lambda k, v: saved.__setitem__(k, v))
    monkeypatch.setattr(vsl_publish, "config", lambda: dict(CFG))
    client.post("/api/wistia/config", json={
        "wistia_project_id": " p1 ", "event_engine_url": "https://x",
        "exports_dir": "E:\\out", "wistia_token": "should-be-ignored"})
    assert saved == {"wistia_project_id": "p1",
                     "event_engine_url": "https://x",
                     "vsl_exports_dir": "E:\\out"}
    assert not any("token" in k for k in saved)


def test_events_route_proxies_and_reports_failure_as_502(client, monkeypatch):
    monkeypatch.setattr(vsl_publish, "config", lambda: dict(CFG))
    monkeypatch.setattr(eventengine, "list_events",
                        lambda base, tok, opener=None: [{"id": 3, "name": "ev"}])
    body = client.get("/api/wistia/events").get_json()
    assert body["events"][0]["id"] == 3

    def boom(*a, **k):
        raise eventengine.EventEngineError("Could not reach Event-Engine: down")
    monkeypatch.setattr(eventengine, "list_events", boom)
    r = client.get("/api/wistia/events")
    assert r.status_code == 502 and "down" in r.get_json()["error"]


def test_inspect_and_latest_routes(client, tmp_path, monkeypatch):
    monkeypatch.setattr(vsl_publish, "config", lambda: dict(CFG))
    p = tmp_path / "a.mp4"
    p.write_bytes(b"\0" * 2048)
    body = client.post("/api/wistia/inspect", json={"path": str(p)}).get_json()
    assert body["ok"] is True and body["size"] == 2048 and body["warn"] is False

    assert client.post("/api/wistia/inspect",
                       json={"path": str(tmp_path / "no.mp4")}).status_code == 400

    body = client.post("/api/wistia/latest",
                       json={"folder": str(tmp_path)}).get_json()
    assert body["name"] == "a.mp4"
    assert client.post("/api/wistia/latest",
                       json={"folder": str(tmp_path / "empty")}).status_code == 404


def test_upload_route_refuses_without_a_token_or_a_file(client, tmp_path, monkeypatch):
    monkeypatch.setattr(vsl_publish, "config",
                        lambda: dict(CFG, wistia_token=""))
    p = tmp_path / "a.mp4"
    p.write_bytes(b"\0" * 10)
    r = client.post("/api/wistia/upload", json={"path": str(p)})
    assert r.status_code == 503 and "WISTIA_API_TOKEN" in r.get_json()["error"]

    monkeypatch.setattr(vsl_publish, "config", lambda: dict(CFG))
    assert client.post("/api/wistia/upload",
                       json={"path": str(tmp_path / "no.mp4")}).status_code == 400


def test_upload_route_starts_a_job_whose_result_carries_the_link(
        client, tmp_path, monkeypatch):
    import time
    monkeypatch.setattr(vsl_publish, "config", lambda: dict(CFG))
    monkeypatch.setattr(wistia, "upload", lambda path, **k: {"hashed_id": "zz11yy22"})
    monkeypatch.setattr(eventengine, "set_vsl",
                        lambda b, t, e, u, opener=None: {"ok": True, "event_id": e,
                                                         "public_url": "https://p/x"})
    p = tmp_path / "a.mp4"
    p.write_bytes(b"\0" * 10)
    jid = client.post("/api/wistia/upload",
                      json={"path": str(p), "event_id": 7}).get_json()["id"]

    for _ in range(100):
        job = client.get("/api/job/%s" % jid).get_json()
        if job["state"] != "running":
            break
        time.sleep(0.05)
    assert job["state"] == "done", job
    assert job["result"]["video_url"].endswith("zz11yy22")
    assert job["result"]["event"]["event_id"] == 7
