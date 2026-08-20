"""vsl_publish.py + publish_vsl.py — file selection, the size warning, the
partial-success rule, and --dry-run.

Nothing here touches the network: `publish()` is driven with a cfg dict and the
two clients are monkeypatched.

The load-bearing test is `test_a_failed_event_update_is_a_partial_success`: an
upload that worked must never be reported as a failure just because the event
could not be updated afterwards, or the user re-uploads a file that is already
on Wistia.
"""
import os
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import eventengine  # noqa: E402
import vsl_publish  # noqa: E402
import wistia  # noqa: E402

CFG = {
    "wistia_token": "tok",
    "event_engine_token": "eetok",
    "wistia_project_id": "",
    "wistia_subdomain": "",
    "event_engine_url": "https://events.example.com",
    "exports_dir": "",
}


def _mk(tmp_path, name, size=1024, mtime=None):
    p = tmp_path / name
    p.write_bytes(b"\0" * size)
    if mtime:
        os.utime(p, (mtime, mtime))
    return str(p)


# ------------------------------------------------------------ file selection

def test_latest_video_picks_the_newest_and_ignores_non_video(tmp_path):
    _mk(tmp_path, "old.mp4", mtime=1_000_000)
    newest = _mk(tmp_path, "new.mov", mtime=2_000_000)
    _mk(tmp_path, "notes.txt", mtime=3_000_000)      # newer, but not a video
    _mk(tmp_path, "art.png", mtime=3_000_000)
    assert vsl_publish.latest_video(str(tmp_path)) == newest


def test_latest_video_on_an_empty_or_missing_folder_is_none(tmp_path):
    assert vsl_publish.latest_video(str(tmp_path)) is None
    assert vsl_publish.latest_video(str(tmp_path / "nope")) is None
    assert vsl_publish.latest_video("") is None


# ------------------------------------------------------------- size warning

def test_a_small_file_does_not_warn(tmp_path):
    info = vsl_publish.inspect(_mk(tmp_path, "small.mp4", 5 * 1024 * 1024))
    assert info["warn"] is False and info["warn_text"] == ""
    assert info["size_human"].endswith("MB")


def test_a_file_over_the_threshold_warns_with_a_concrete_suggestion(
        tmp_path, monkeypatch):
    # Patch the threshold rather than writing 2GB to disk.
    monkeypatch.setattr(vsl_publish, "WARN_BYTES", 1024)
    info = vsl_publish.inspect(_mk(tmp_path, "big.mp4", 4096))
    assert info["warn"] is True
    assert "delivery export" in info["warn_text"]
    assert info["minutes"] >= 0


def test_the_estimate_scales_with_size():
    small = vsl_publish.estimate_minutes(100 * 1024 ** 2)
    big = vsl_publish.estimate_minutes(10 * 1024 ** 3)
    assert 0 < small < big
    assert vsl_publish.estimate_minutes(0) == 0.0


# ------------------------------------------------------------------- publish

def _fake_upload(monkeypatch, hashed_id="abc12345", boom=None):
    calls = {}

    def fake(path, *, token, project_id=None, name=None, description=None,
             progress=None, timeout=None):
        calls.update(path=path, token=token, project_id=project_id, name=name)
        if boom:
            raise boom
        if progress:
            progress(512, 1024)
            progress(1024, 1024)
        return {"hashed_id": hashed_id}

    monkeypatch.setattr(wistia, "upload", fake)
    return calls


def test_publish_without_an_event_uploads_and_returns_the_link(tmp_path, monkeypatch):
    calls = _fake_upload(monkeypatch)
    path = _mk(tmp_path, "vsl.mp4")
    seen = []
    out = vsl_publish.publish(path, progress=lambda p, m: seen.append((p, m)),
                              cfg=CFG)
    assert out["hashed_id"] == "abc12345"
    assert out["video_url"] == "https://fast.wistia.net/embed/iframe/abc12345"
    assert "event" not in out and "event_error" not in out
    assert calls["name"] == "vsl"            # the filename, minus extension
    assert seen[-1][0] == 100


def test_publish_sets_the_event_when_one_is_given(tmp_path, monkeypatch):
    _fake_upload(monkeypatch)
    seen = {}

    def fake_set(base, token, event_id, url, opener=None):
        seen.update(base=base, token=token, event_id=event_id, url=url)
        return {"ok": True, "event_id": event_id, "vsl_url": url,
                "public_url": "https://events.example.com/register/x"}

    monkeypatch.setattr(eventengine, "set_vsl", fake_set)
    out = vsl_publish.publish(_mk(tmp_path, "v.mp4"), event_id=18, cfg=CFG)
    assert seen["event_id"] == 18
    assert seen["url"] == out["video_url"]
    assert seen["token"] == "eetok"
    assert out["event"]["public_url"].endswith("/register/x")


def test_a_failed_event_update_is_a_partial_success_not_a_failure(
        tmp_path, monkeypatch):
    """The video IS on Wistia. Raising here would send the user to re-upload a
    file that already exists."""
    _fake_upload(monkeypatch)

    def boom(*a, **k):
        raise eventengine.EventEngineError("Event-Engine rejected the API key (401).")

    monkeypatch.setattr(eventengine, "set_vsl", boom)
    out = vsl_publish.publish(_mk(tmp_path, "v.mp4"), event_id=18, cfg=CFG)
    assert out["video_url"].endswith("abc12345")     # still handed back
    assert "401" in out["event_error"]
    assert "event" not in out


def test_a_failed_upload_does_raise(tmp_path, monkeypatch):
    _fake_upload(monkeypatch, boom=wistia.WistiaError("nope", status=400))
    with pytest.raises(wistia.WistiaError):
        vsl_publish.publish(_mk(tmp_path, "v.mp4"), cfg=CFG)


def test_a_response_without_a_hashed_id_is_an_error(tmp_path, monkeypatch):
    monkeypatch.setattr(wistia, "upload",
                        lambda path, **k: {"unexpected": True})
    with pytest.raises(wistia.WistiaError, match="hashed_id"):
        vsl_publish.publish(_mk(tmp_path, "v.mp4"), cfg=CFG)


def test_a_configured_subdomain_produces_the_account_link(tmp_path, monkeypatch):
    _fake_upload(monkeypatch)
    cfg = dict(CFG, wistia_subdomain="omri")
    out = vsl_publish.publish(_mk(tmp_path, "v.mp4"), cfg=cfg)
    assert out["video_url"] == "https://omri.wistia.com/medias/abc12345"
    assert out["media_url"] == "https://fast.wistia.net/embed/iframe/abc12345"


def test_dry_run_writes_nothing_anywhere(tmp_path, monkeypatch):
    def explode(*a, **k):                      # pragma: no cover - must not run
        raise AssertionError("dry run touched the network")

    monkeypatch.setattr(wistia, "upload", explode)
    monkeypatch.setattr(eventengine, "set_vsl", explode)
    out = vsl_publish.publish(_mk(tmp_path, "v.mp4"), event_id=18,
                              dry_run=True, cfg=CFG)
    assert out["dry_run"] is True
    assert out["event"]["dry_run"] is True


# -------------------------------------------------------------------- config

def test_public_config_reports_token_presence_never_the_token(monkeypatch):
    import settings_store
    monkeypatch.setattr(settings_store, "get_secret",
                        lambda k, d="": "s3cret" if k == "WISTIA_API_TOKEN" else "")
    monkeypatch.setattr(settings_store, "get_setting", lambda k, d=None: d)
    pub = vsl_publish.public_config()
    assert pub["has_wistia_token"] is True
    assert pub["has_event_engine_token"] is False
    assert "s3cret" not in repr(pub)
    assert not any("token" == k for k in pub if k.endswith("token"))


# ----------------------------------------------------------------------- CLI

def test_cli_dry_run_end_to_end(tmp_path, monkeypatch, capsys):
    import publish_vsl

    def explode(*a, **k):                      # pragma: no cover - must not run
        raise AssertionError("dry run touched the network")

    monkeypatch.setattr(wistia, "upload", explode)
    monkeypatch.setattr(eventengine, "set_vsl", explode)
    monkeypatch.setattr(eventengine, "list_events", explode)
    monkeypatch.setattr(vsl_publish, "config", lambda: dict(CFG))
    monkeypatch.setattr(publish_vsl, "_copy_to_clipboard", lambda t: False)

    path = _mk(tmp_path, "vsl.mp4")
    assert publish_vsl.main([path, "--dry-run", "--event", "18"]) == 0
    out = capsys.readouterr().out
    assert "vsl.mp4" in out
    assert "fast.wistia.net/embed/iframe/" in out
    assert "event 18 not touched" in out


def test_cli_latest_resolves_from_the_configured_exports_folder(
        tmp_path, monkeypatch, capsys):
    import publish_vsl
    newest = _mk(tmp_path, "b.mp4", mtime=2_000_000)
    _mk(tmp_path, "a.mp4", mtime=1_000_000)
    monkeypatch.setattr(vsl_publish, "config",
                        lambda: dict(CFG, exports_dir=str(tmp_path)))
    monkeypatch.setattr(publish_vsl, "_copy_to_clipboard", lambda t: False)
    assert publish_vsl.main(["--latest", "--dry-run"]) == 0
    assert os.path.basename(newest) in capsys.readouterr().out


def test_cli_refuses_a_missing_file(tmp_path, monkeypatch):
    import publish_vsl
    monkeypatch.setattr(vsl_publish, "config", lambda: dict(CFG))
    with pytest.raises(SystemExit):
        publish_vsl.main([str(tmp_path / "nope.mp4"), "--dry-run"])


def test_cli_requires_a_token_unless_dry_running(tmp_path, monkeypatch):
    import publish_vsl
    monkeypatch.setattr(vsl_publish, "config",
                        lambda: dict(CFG, wistia_token=""))
    with pytest.raises(SystemExit) as ei:
        publish_vsl.main([_mk(tmp_path, "v.mp4")])
    assert "WISTIA_API_TOKEN" in str(ei.value)
