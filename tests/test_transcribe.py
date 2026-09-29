"""Transcription goes through davinci-automation's transcribe_auto.py, from its
checkout — never the retired E:\\DaVinci Automation mirror. The runner tests use a
fake transcribe_auto.py (a real process tree, no network, no GPU)."""
import os
import sys
import textwrap
import time

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import davinci  # noqa: E402
import osmo_import as oi  # noqa: E402

CHECKOUT = os.path.join(os.path.expanduser("~"), "Projects", "davinci-automation")


def test_scripts_come_from_the_checkout_not_the_old_mirror():
    assert oi.TRANSCRIBE_SCRIPT == os.path.join(
        CHECKOUT, "scripts", "transcription", "transcribe_auto.py")
    assert davinci.NEW_PROJECT_SCRIPT == os.path.join(
        CHECKOUT, "scripts", "utils", "new_project.py")
    assert davinci.IMPORT_PROXY_SCRIPT == os.path.join(
        CHECKOUT, "scripts", "utils", "import_and_proxy.py")
    for path in (oi.TRANSCRIBE_SCRIPT, davinci.NEW_PROJECT_SCRIPT,
                 davinci.IMPORT_PROXY_SCRIPT, davinci.DASHBOARD_RUNBAT):
        assert "DaVinci Automation" not in path


@pytest.mark.parametrize("backend,engine", [("vps", "vps"), ("local", "local")])
def test_backend_maps_onto_the_engine_flag(backend, engine):
    cmd = oi.transcribe_cmd(r"D:\Arch\2026-09-29\DJI_0063_D.MP4", r"D:\Arch\2026-09-29", backend)
    assert cmd[:3] == [*oi._PY, oi.TRANSCRIBE_SCRIPT]
    assert cmd[3:] == [r"D:\Arch\2026-09-29\DJI_0063_D.MP4",
                       "--output", os.path.join(r"D:\Arch\2026-09-29", "DJI_0063_D.srt"),
                       "--engine", engine]


def test_error_tail_keeps_the_engines_reason_not_the_generic_line():
    out = ("Transcribing: a.mp4\n  order: VPS whisper-agent (OpenAI)\n"
           "ERROR: whisper-agent unreachable — Is Tailscale up?\n"
           "  VPS transcription failed (rc=1).\n"
           "ERROR: every transcription engine failed — see the messages above.\n")
    tail = oi._error_tail(out)
    assert tail.endswith("  VPS transcription failed (rc=1).".strip())
    assert "unreachable" in tail[-200:]
    assert "every transcription engine failed" not in tail


def _fake_auto(tmp_path, body: str) -> str:
    """A stand-in transcribe_auto.py; gets the real command line."""
    script = tmp_path / "transcribe_auto.py"
    script.write_text(textwrap.dedent(body), encoding="utf-8")
    return str(script)


@pytest.fixture
def fake(tmp_path, monkeypatch):
    monkeypatch.setattr(oi, "_PY", [sys.executable])

    def install(body):
        monkeypatch.setattr(oi, "TRANSCRIBE_SCRIPT", _fake_auto(tmp_path, body))
    return install


def test_run_ok_writes_where_asked(fake, tmp_path):
    fake("""
        import sys
        out = sys.argv[sys.argv.index("--output") + 1]
        assert sys.argv[sys.argv.index("--engine") + 1] == "vps"
        open(out, "w", encoding="utf-8").write("1\\n00:00:00,000 --> 00:00:01,000\\nשלום\\n")
        print("✅ done")
    """)
    status, err = oi._run_transcribe(str(tmp_path / "clip.mp4"), str(tmp_path), "vps")
    assert (status, err) == ("ok", "")
    assert (tmp_path / "clip.srt").read_text(encoding="utf-8").endswith("שלום\n")


def test_run_fail_reports_the_engines_words_from_either_stream(fake, tmp_path):
    fake("""
        import subprocess, sys
        # the engine is a child writing to the inherited pipes, like the real chain
        subprocess.run([sys.executable, "-c",
                        "import sys; print('Transcribing: קובץ.mp4'); "
                        "sys.stderr.write('RuntimeError: ffmpeg failed: moov atom not found\\\\n'); "
                        "sys.exit(1)"])
        print("  VPS transcription failed (rc=1).")
        print("ERROR: every transcription engine failed — see the messages above.")
        sys.exit(1)
    """)
    status, err = oi._run_transcribe(str(tmp_path / "clip.mp4"), str(tmp_path), "vps")
    assert status == "fail"
    assert "moov atom not found" in err[-200:]
    assert "קובץ" in err
    assert "every transcription engine failed" not in err


@pytest.mark.skipif(os.name != "nt", reason="taskkill /T is the Windows tree kill")
def test_timeout_ends_the_whole_chain(fake, tmp_path, monkeypatch):
    psutil = pytest.importorskip("psutil")
    pidfile = tmp_path / "engine.pid"
    fake(f"""
        import subprocess, sys
        eng = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(120)"])
        open(r"{pidfile}", "w").write(str(eng.pid))
        eng.wait()
    """)
    monkeypatch.setattr(oi, "TRANSCRIBE_TIMEOUT", 3)
    t0 = time.time()
    status, _ = oi._run_transcribe(str(tmp_path / "clip.mp4"), str(tmp_path), "vps")
    assert status == "timeout"
    assert time.time() - t0 < 60          # did not wait for the 120 s engine
    engine_pid = int(pidfile.read_text())
    for _ in range(50):                   # taskkill returns before the exit lands
        if not psutil.pid_exists(engine_pid):
            break
        time.sleep(0.1)
    assert not psutil.pid_exists(engine_pid)
