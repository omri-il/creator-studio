"""wistia.py — multipart assembly, streaming, and error mapping.

The network is stubbed throughout: `upload()` takes an `opener`, so nothing
here touches Wistia.

The load-bearing tests are the two about memory and length:
  * the body reports an exact Content-Length, and
  * it never holds more than one CHUNK of the video at a time
— because the whole reason this client exists rather than a `requests.post`
one-liner is that a Resolve master does not fit in RAM.
"""
import io
import json
import os
import sys
import urllib.error

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import wistia  # noqa: E402


class _Resp:
    def __init__(self, payload):
        self._data = json.dumps(payload).encode("utf-8")

    def read(self):
        return self._data

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False


def _video(tmp_path, size=3 * wistia.CHUNK + 17):
    p = tmp_path / "vsl.mp4"
    p.write_bytes(bytes(range(256)) * (size // 256) + b"\x00" * (size % 256))
    return str(p)


def _capture(payload=None, status=None, body=""):
    """An opener that records the request and returns a canned response."""
    seen = {}

    def opener(req, timeout=None):
        seen["req"] = req
        seen["timeout"] = timeout
        # Drain the body exactly as urllib would, so streaming is exercised.
        chunks = []
        while True:
            data = req.data.read(wistia.CHUNK)
            if not data:
                break
            chunks.append(data)
        seen["body"] = b"".join(chunks)
        seen["max_chunk"] = max((len(c) for c in chunks), default=0)
        if status:
            raise urllib.error.HTTPError(
                wistia.UPLOAD_URL, status, "err", {},
                io.BytesIO(body.encode("utf-8")))
        return _Resp(payload or {"hashed_id": "abc12345"})

    return opener, seen


# ------------------------------------------------------------------ multipart

def test_body_length_matches_the_bytes_actually_produced(tmp_path):
    path = _video(tmp_path)
    body = wistia._MultipartBody(path, "BOUND", {"name": "x"})
    declared = len(body)
    produced = b""
    while True:
        chunk = body.read(wistia.CHUNK)
        if not chunk:
            break
        produced += chunk
    body.close()
    assert len(produced) == declared


def test_the_file_is_streamed_never_buffered_whole(tmp_path):
    """No single read returns more than the requested chunk — this is the
    property that lets a multi-GB master upload at all."""
    path = _video(tmp_path)
    body = wistia._MultipartBody(path, "BOUND", {})
    biggest = 0
    while True:
        chunk = body.read(wistia.CHUNK)
        if not chunk:
            break
        biggest = max(biggest, len(chunk))
    body.close()
    assert biggest <= wistia.CHUNK
    assert body.file_size > wistia.CHUNK      # the fixture is genuinely multi-chunk


def test_body_contains_the_fields_the_file_and_the_closing_boundary(tmp_path):
    path = _video(tmp_path, size=100)
    body = wistia._MultipartBody(
        path, "BOUND", {"project_id": "proj1", "name": "כותרת", "description": ""})
    out = b""
    while True:
        chunk = body.read(wistia.CHUNK)
        if not chunk:
            break
        out += chunk
    body.close()

    assert b'name="project_id"' in out and b"proj1" in out
    assert "כותרת".encode("utf-8") in out
    assert b'name="description"' not in out        # empty fields are omitted
    assert b'name="file"; filename="vsl.mp4"' in out
    assert b"Content-Type: video/mp4" in out
    assert out.endswith(b"--BOUND--\r\n")
    assert open(path, "rb").read() in out


def test_progress_is_reported_over_the_video_bytes_only(tmp_path):
    path = _video(tmp_path)
    seen = []
    body = wistia._MultipartBody(path, "B", {}, progress=lambda s, t: seen.append((s, t)))
    while body.read(wistia.CHUNK):
        pass
    body.close()
    assert seen, "progress was never called"
    assert seen[-1] == (body.file_size, body.file_size)
    assert all(t == body.file_size for _, t in seen)
    assert [s for s, _ in seen] == sorted(s for s, _ in seen)


# --------------------------------------------------------------------- upload

def test_upload_sends_bearer_token_and_exact_content_length(tmp_path):
    path = _video(tmp_path, size=5000)
    opener, seen = _capture()
    media = wistia.upload(path, token="tok", project_id="p1", name="ווידאו",
                          opener=opener)
    assert media["hashed_id"] == "abc12345"

    req = seen["req"]
    assert req.get_header("Authorization") == "Bearer tok"
    assert req.get_header("Content-type").startswith("multipart/form-data; boundary=")
    assert int(req.get_header("Content-length")) == len(seen["body"])
    assert seen["max_chunk"] <= wistia.CHUNK


def test_upload_reports_progress_to_the_caller(tmp_path):
    path = _video(tmp_path)
    opener, _ = _capture()
    seen = []
    wistia.upload(path, token="t", progress=lambda s, tot: seen.append(s),
                  opener=opener)
    assert seen and seen[-1] == os.path.getsize(path)


def test_missing_token_and_missing_file_are_refused_before_any_network(tmp_path):
    def opener(*a, **k):                       # pragma: no cover - must not run
        raise AssertionError("the network was touched")

    with pytest.raises(wistia.WistiaError, match="token"):
        wistia.upload(_video(tmp_path, 10), token="", opener=opener)
    with pytest.raises(wistia.WistiaError, match="not found"):
        wistia.upload(str(tmp_path / "nope.mp4"), token="t", opener=opener)


@pytest.mark.parametrize("status,needle", [
    (401, "token"),
    (400, "video limit"),      # the docs name this one; it must not read transient
    (404, "project_id"),
    (500, "HTTP 500"),
])
def test_http_errors_become_actionable_messages(tmp_path, status, needle):
    path = _video(tmp_path, 100)
    opener, _ = _capture(status=status, body='{"error":"nope"}')
    with pytest.raises(wistia.WistiaError) as ei:
        wistia.upload(path, token="t", opener=opener)
    assert needle in str(ei.value)
    assert ei.value.status == status


def test_unreachable_host_is_a_clear_error(tmp_path):
    path = _video(tmp_path, 100)

    def opener(req, timeout=None):
        raise urllib.error.URLError("getaddrinfo failed")

    with pytest.raises(wistia.WistiaError, match="Could not reach Wistia"):
        wistia.upload(path, token="t", opener=opener)


def test_non_json_response_is_an_error_not_a_crash(tmp_path):
    path = _video(tmp_path, 100)

    class Raw:
        def read(self): return b"<html>maintenance</html>"
        def __enter__(self): return self
        def __exit__(self, *a): return False

    with pytest.raises(wistia.WistiaError, match="non-JSON"):
        wistia.upload(path, token="t", opener=lambda r, timeout=None: Raw())


# ------------------------------------------------------------------ url shape

def test_media_url_round_trips_through_event_engines_own_regex():
    """The reason media_url is the canonical form: Event-Engine must accept it.

    This asserts against Event-Engine's REAL vsl.py rather than a copy of the
    pattern — a copy would keep passing after the original changed.
    """
    vsl = _load_event_engine_vsl()
    if vsl is None:
        pytest.skip("Event-Engine checkout not available next to this repo")
    parsed = vsl.parse(wistia.media_url("abc12345"))
    assert parsed and parsed["provider"] == "wistia"
    assert parsed["video_id"] == "abc12345"
    assert parsed["embed_url"] == wistia.media_url("abc12345")


def test_account_url_also_parses_and_falls_back_without_a_subdomain():
    vsl = _load_event_engine_vsl()
    assert wistia.account_url("abc12345", "") == wistia.media_url("abc12345")
    assert wistia.account_url("abc12345", None) == wistia.media_url("abc12345")
    url = wistia.account_url("abc12345", "omri")
    assert url == "https://omri.wistia.com/medias/abc12345"
    if vsl is not None:
        assert vsl.parse(url)["video_id"] == "abc12345"


def _load_event_engine_vsl():
    """Import Event-Engine's vsl.py from the sibling checkout, if present."""
    import importlib.util
    here = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    candidate = os.path.join(os.path.dirname(here), "Event-Engine", "vsl.py")
    if not os.path.isfile(candidate):
        return None
    spec = importlib.util.spec_from_file_location("_ee_vsl", candidate)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod
