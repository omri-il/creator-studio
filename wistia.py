"""Wistia Upload API client — stdlib only.

Why stdlib and not `requests`: this app is frozen with PyInstaller and its
requirements.txt carries neither `requests` nor `python-dotenv`. Adding a
dependency means touching mic_tracker.spec and re-testing the build, for no
gain — and urllib gives the control this needs anyway, because the one thing
that actually matters here is NOT reading the file into memory.

🚨 The upload body is STREAMED with a real Content-Length. A Resolve master is
routinely several GB; the obvious `requests.post(files={...})` buffers the whole
multipart body in RAM first. `_MultipartBody` below yields the preamble, then
the file in chunks, then the epilogue, and computes its own exact length up
front so no chunked transfer-encoding is needed (the length is known, and
progress reporting comes out exact).

Docs: https://docs.wistia.com/reference/getting-started-with-the-upload-api-1
POST https://upload.wistia.com, Authorization: Bearer <token>, multipart with
`file` plus optional project_id / name / description.
"""
from __future__ import annotations

import json
import mimetypes
import os
import urllib.error
import urllib.request
import uuid

UPLOAD_URL = "https://upload.wistia.com"
CHUNK = 1024 * 1024      # 1 MiB — smooth progress without a syscall per KB


class WistiaError(Exception):
    """A failed upload, carrying the HTTP status and body.

    The status matters to the caller: Wistia's docs call out 400 specifically
    for "account video limit reached", which the user must act on rather than
    retry, so it must not read as a transient failure.
    """

    def __init__(self, message, status=None, body=""):
        super().__init__(message)
        self.status = status
        self.body = body


def _field(boundary, name, value):
    return (
        "--" + boundary + "\r\n"
        'Content-Disposition: form-data; name="' + name + '"\r\n\r\n'
        + value + "\r\n"
    ).encode("utf-8")


class _MultipartBody:
    """A file-like request body that reads the video off disk in chunks.

    urllib calls .read(size) on this; `len()` gives it the exact
    Content-Length. Nothing but one CHUNK of the video is ever in memory.
    """

    def __init__(self, path, boundary, fields, progress=None):
        self.path = path
        self.progress = progress
        filename = os.path.basename(path)
        ctype = mimetypes.guess_type(filename)[0] or "application/octet-stream"

        preamble = b"".join(_field(boundary, k, v)
                            for k, v in fields.items() if v)
        preamble += (
            "--" + boundary + "\r\n"
            'Content-Disposition: form-data; name="file"; filename="'
            + filename + '"\r\n'
            "Content-Type: " + ctype + "\r\n\r\n"
        ).encode("utf-8")
        epilogue = ("\r\n--" + boundary + "--\r\n").encode("utf-8")

        self._preamble = preamble
        self._epilogue = epilogue
        self.file_size = os.path.getsize(path)
        self._total = len(preamble) + self.file_size + len(epilogue)

        self._fh = None
        self._pos = 0          # bytes emitted overall
        self._sent_file = 0    # bytes of the VIDEO emitted (what progress reports)

    def __len__(self):
        return self._total

    def read(self, size=-1):
        if size is None or size < 0:
            size = CHUNK
        out = b""

        pre = len(self._preamble)
        if self._pos < pre:                                  # 1. preamble
            take = min(size, pre - self._pos)
            out += self._preamble[self._pos:self._pos + take]
            self._pos += take
            size -= take
            if size <= 0:
                return out

        file_end = pre + self.file_size
        if self._pos < file_end:                             # 2. the video
            if self._fh is None:
                self._fh = open(self.path, "rb")
            take = min(size, file_end - self._pos)
            data = self._fh.read(take)
            out += data
            self._pos += len(data)
            self._sent_file += len(data)
            size -= len(data)
            if self.progress and self.file_size:
                self.progress(self._sent_file, self.file_size)
            if size <= 0 or not data:
                return out

        if self._pos < self._total:                          # 3. epilogue
            start = self._pos - file_end
            take = min(size, self._total - self._pos)
            out += self._epilogue[start:start + take]
            self._pos += take

        return out

    def close(self):
        if self._fh:
            try:
                self._fh.close()
            finally:
                self._fh = None


def upload(path, *, token, project_id=None, name=None, description=None,
           progress=None, timeout=None, opener=None):
    """Upload one video file. Returns Wistia's media dict (`hashed_id`, ...).

    `progress(sent_bytes, total_bytes)` is called as the video streams.
    `opener` is the urlopen callable, injected by the tests so nothing here
    needs the network. Raises WistiaError on anything that is not a 2xx.
    """
    if not token:
        raise WistiaError("No Wistia API token configured (WISTIA_API_TOKEN).")
    if not os.path.isfile(path):
        raise WistiaError("File not found: " + str(path))

    boundary = "----creatorstudio" + uuid.uuid4().hex
    fields = {
        "project_id": project_id or "",
        "name": name or "",
        "description": description or "",
    }
    body = _MultipartBody(path, boundary, fields, progress=progress)
    req = urllib.request.Request(UPLOAD_URL, data=body, method="POST")
    req.add_header("Authorization", "Bearer " + token)
    req.add_header("Content-Type",
                   "multipart/form-data; boundary=" + boundary)
    req.add_header("Content-Length", str(len(body)))

    urlopen = opener or urllib.request.urlopen
    try:
        with urlopen(req, timeout=timeout) as resp:
            raw = resp.read().decode("utf-8", "replace")
    except urllib.error.HTTPError as e:
        detail = e.read().decode("utf-8", "replace")
        raise WistiaError(_http_message(e.code, detail), status=e.code,
                          body=detail) from e
    except urllib.error.URLError as e:
        raise WistiaError("Could not reach Wistia: " + str(e.reason)) from e
    finally:
        body.close()

    try:
        return json.loads(raw)
    except ValueError as e:
        raise WistiaError("Wistia returned a non-JSON response", body=raw) from e


def _http_message(status, detail):
    if status == 401:
        return ("Wistia rejected the API token (401). Check WISTIA_API_TOKEN "
                "in .env — Wistia account -> Settings -> API Access.")
    if status == 400:
        # Named explicitly in the docs and NOT retryable, so it must not read
        # like a transient failure.
        return ("Wistia refused the upload (400). This is what it returns when "
                "the account's video limit is reached. Details: " + detail[:300])
    if status == 404:
        return ("Wistia returned 404 — usually a project_id that does not exist "
                "on this account. Clear WISTIA_PROJECT_ID or fix it.")
    return "Wistia upload failed (HTTP " + str(status) + "): " + detail[:300]


def media_url(hashed_id):
    """The canonical link to paste into an event's VSL field.

    Deterministic, and chosen because it is GUARANTEED to match Event-Engine's
    `vsl.py` `_WISTIA` regex — no account-subdomain guessing involved, which is
    the failure mode of building `<sub>.wistia.com/medias/<id>` blind.
    """
    return "https://fast.wistia.net/embed/iframe/" + hashed_id


def account_url(hashed_id, subdomain):
    """The prettier account link, when a subdomain is configured. Also parses
    cleanly on the Event-Engine side; falls back to media_url without one."""
    sub = (subdomain or "").strip().strip(".")
    if not sub:
        return media_url(hashed_id)
    return "https://" + sub + ".wistia.com/medias/" + hashed_id
