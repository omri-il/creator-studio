"""Unit tests for the pure Osmo import logic (no camera / ffmpeg needed)."""
import os
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import osmo_import as oi  # noqa: E402


def _clip(name, duration, creation_time=None, mtime=None,
          width=1920, height=1080, fps=30.0, vcodec="hevc", size=1000):
    return {"name": name, "duration": duration, "creation_time": creation_time,
            "mtime": mtime, "width": width, "height": height, "fps": fps,
            "vcodec": vcodec, "size": size}


# ── parse_dji_timestamp ───────────────────────────────────────────────────────

def test_parse_dji_timestamp_ok():
    ts = oi.parse_dji_timestamp("DJI_20240115143022_0001_D.MP4")
    assert ts is not None
    from datetime import datetime
    assert datetime.fromtimestamp(ts).year == 2024


def test_parse_dji_timestamp_absent():
    assert oi.parse_dji_timestamp("DJI_0001.MP4") is None
    assert oi.parse_dji_timestamp("random.mov") is None


# ── assign_timeline: source selection ─────────────────────────────────────────

def test_timeline_uses_creation_time_when_all_present():
    clips = [_clip("a.mp4", 10, creation_time=1000.0),
             _clip("b.mp4", 10, creation_time=1010.0)]
    oi.assign_timeline(clips)
    assert clips[0]["time_source"] == "creation_time"
    assert clips[0]["start"] == 1000.0 and clips[0]["end"] == 1010.0
    assert clips[1]["start"] == 1010.0


def test_timeline_falls_back_to_mtime_when_any_missing():
    clips = [_clip("a.mp4", 10, creation_time=1000.0, mtime=2000.0),
             _clip("b.mp4", 10, creation_time=None, mtime=2010.0)]
    oi.assign_timeline(clips)
    # end == mtime, start == mtime - duration
    assert clips[0]["time_source"] == "mtime"
    assert clips[0]["end"] == 2000.0 and clips[0]["start"] == 1990.0
    assert clips[1]["start"] == 2000.0 and clips[1]["end"] == 2010.0


# ── group_sessions ────────────────────────────────────────────────────────────

def test_contiguous_split_is_one_session():
    # Two ~4GB chunks of one recording: clip B starts right where A ends.
    clips = [_clip("DJI_0001.MP4", 600, creation_time=1000.0),
             _clip("DJI_0002.MP4", 600, creation_time=1600.0)]
    oi.assign_timeline(clips)
    sessions = oi.group_sessions(clips)
    assert len(sessions) == 1
    assert [c["name"] for c in sessions[0]] == ["DJI_0001.MP4", "DJI_0002.MP4"]


def test_gap_splits_into_two_sessions():
    # Second recording starts 5 minutes after the first ended → separate takes.
    clips = [_clip("DJI_0001.MP4", 600, creation_time=1000.0),
             _clip("DJI_0002.MP4", 600, creation_time=1600.0 + 300)]
    oi.assign_timeline(clips)
    sessions = oi.group_sessions(clips)
    assert len(sessions) == 2


def test_param_change_splits_even_when_contiguous():
    # Same timing but different resolution → cannot be the same continuous clip.
    clips = [_clip("DJI_0001.MP4", 600, creation_time=1000.0, height=1080),
             _clip("DJI_0002.MP4", 600, creation_time=1600.0, height=2160)]
    oi.assign_timeline(clips)
    sessions = oi.group_sessions(clips)
    assert len(sessions) == 2


def test_three_way_split_groups_all():
    clips = [_clip("DJI_0001.MP4", 300, creation_time=1000.0),
             _clip("DJI_0002.MP4", 300, creation_time=1300.0),
             _clip("DJI_0003.MP4", 300, creation_time=1600.0)]
    oi.assign_timeline(clips)
    sessions = oi.group_sessions(clips)
    assert len(sessions) == 1 and len(sessions[0]) == 3


def test_out_of_order_input_is_sorted():
    clips = [_clip("DJI_0002.MP4", 300, creation_time=1300.0),
             _clip("DJI_0001.MP4", 300, creation_time=1000.0)]
    oi.assign_timeline(clips)
    sessions = oi.group_sessions(clips)
    assert len(sessions) == 1
    assert [c["name"] for c in sessions[0]] == ["DJI_0001.MP4", "DJI_0002.MP4"]


def test_small_gap_within_threshold_stays_together():
    # 3-second gap (< SESSION_MAX_GAP) — still one recording.
    clips = [_clip("DJI_0001.MP4", 600, creation_time=1000.0),
             _clip("DJI_0002.MP4", 600, creation_time=1603.0)]
    oi.assign_timeline(clips)
    assert len(oi.group_sessions(clips)) == 1


# ── manifest + labels ─────────────────────────────────────────────────────────

def test_manifest_key_is_name_and_size():
    assert oi.manifest_key("DJI_0001.MP4", 12345) == "DJI_0001.MP4|12345"


def test_session_label_from_start_time():
    clips = [_clip("DJI_0001.MP4", 600, creation_time=1705323022.0)]
    oi.assign_timeline(clips)
    label = oi.session_label(clips)
    assert label.startswith("Osmo_")


def test_session_label_fallback_to_stem():
    clips = [_clip("clipX.mov", 600, creation_time=None, mtime=0.0)]
    oi.assign_timeline(clips)
    # start = 0 - 600 = -600 -> may still format; ensure it returns a string
    assert isinstance(oi.session_label(clips), str)


# ── destination planning + .part copies (the 2026-09-23 double-import) ───────

def _write(path, nbytes):
    with open(path, "wb") as f:
        f.write(b"x" * nbytes)


def test_plan_dest_free_name(tmp_path):
    dst, there = oi.plan_dest(str(tmp_path), "DJI_0001.MP4", 100)
    assert dst == str(tmp_path / "DJI_0001.MP4") and there is False


def test_plan_dest_same_size_is_a_finished_copy(tmp_path):
    _write(tmp_path / "DJI_0001.MP4", 100)
    dst, there = oi.plan_dest(str(tmp_path), "DJI_0001.MP4", 100)
    assert dst == str(tmp_path / "DJI_0001.MP4") and there is True


def test_plan_dest_never_targets_a_different_sized_file(tmp_path):
    _write(tmp_path / "DJI_0001.MP4", 40)          # someone else's / legacy junk
    dst, there = oi.plan_dest(str(tmp_path), "DJI_0001.MP4", 100)
    assert dst == str(tmp_path / "DJI_0001_100.MP4") and there is False
    _write(tmp_path / "DJI_0001_100.MP4", 100)     # …and that one, once complete
    assert oi.plan_dest(str(tmp_path), "DJI_0001.MP4", 100) == (dst, True)


def test_copy_goes_through_part_and_leaves_no_part(tmp_path):
    src, dst = tmp_path / "src.mp4", tmp_path / "out" / "dst.mp4"
    dst.parent.mkdir()
    _write(src, 5 * 1024 * 1024 + 7)               # > one 4 MiB chunk
    oi._copy_with_progress(str(src), str(dst))
    assert dst.read_bytes() == src.read_bytes()
    assert not (dst.parent / "dst.mp4.part").exists()


def test_copy_replaces_a_stale_part(tmp_path):
    src, dst = tmp_path / "src.mp4", tmp_path / "dst.mp4"
    _write(src, 1000)
    _write(tmp_path / "dst.mp4.part", 5000)        # left by a crash, bigger even
    oi._copy_with_progress(str(src), str(dst))
    assert dst.stat().st_size == 1000
    assert not (tmp_path / "dst.mp4.part").exists()


def test_failed_copy_never_touches_the_target(tmp_path, monkeypatch):
    src, dst = tmp_path / "src.mp4", tmp_path / "dst.mp4"
    _write(src, 1000)
    dst.write_bytes(b"good finished copy")

    def boom(*a, **k):
        raise OSError("share went away")
    monkeypatch.setattr(oi.shutil, "copystat", boom)
    with pytest.raises(OSError):
        oi._copy_with_progress(str(src), str(dst))
    assert dst.read_bytes() == b"good finished copy"   # the old "wb" truncated it
    assert not (tmp_path / "dst.mp4.part").exists()


# ── manifest: written per clip, merged with what is already on disk ──────────

def test_record_imported_keeps_entries_written_meanwhile(tmp_path):
    root = str(tmp_path)
    oi.record_imported(root, "a|1", {"name": "a"})
    # Another writer (the other machine) adds b; our next record must not drop it.
    m = oi._load_manifest(root)
    m["b|2"] = {"name": "b"}
    oi._save_manifest(root, m)
    oi.record_imported(root, "c|3", {"name": "c"})
    assert set(oi._load_manifest(root)) == {"a|1", "b|2", "c|3"}
    assert not os.path.exists(os.path.join(root, oi.MANIFEST_NAME + ".tmp"))


def _fake_scan(tmp_path, names):
    cam = tmp_path / "cam"
    cam.mkdir()
    clips = []
    for n in names:
        _write(cam / n, 100)
        clips.append({"name": n, "path": str(cam / n), "size": 100,
                      "key": oi.manifest_key(n, 100), "already": False})
    backup = tmp_path / "backup"
    return str(cam), str(backup), {
        "dest_dir": str(backup / "2026-09-23"),
        "sessions": [{"label": "S", "clips": [c]} for c in clips]}


def test_run_import_records_each_clip_before_copying_the_next(tmp_path, monkeypatch):
    cam, backup, scan = _fake_scan(tmp_path, ["A.MP4", "B.MP4", "C.MP4"])
    monkeypatch.setattr(oi, "scan_source", lambda *a, **k: scan)
    seen = []
    real_copy = oi._copy_with_progress

    def spy(src, dst, cb=None):
        # What a second import started right now would read as "already done".
        seen.append(sorted(oi._load_manifest(backup)))
        real_copy(src, dst, cb)
    monkeypatch.setattr(oi, "_copy_with_progress", spy)

    out = oi.run_import(cam, {"backup_root": backup, "merge": False,
                              "transcribe": False})
    assert seen == [[], ["A.MP4|100"], ["A.MP4|100", "B.MP4|100"]]
    assert len(out["copied"]) == 3 and not out["errors"]


def test_run_import_reuses_a_finished_copy_missing_from_the_manifest(tmp_path, monkeypatch):
    # Exactly what the old build left behind: file copied, manifest never saved.
    cam, backup, scan = _fake_scan(tmp_path, ["A.MP4"])
    os.makedirs(scan["dest_dir"])
    _write(os.path.join(scan["dest_dir"], "A.MP4"), 100)
    monkeypatch.setattr(oi, "scan_source", lambda *a, **k: scan)
    monkeypatch.setattr(oi, "_copy_with_progress",
                        lambda *a, **k: pytest.fail("must not copy again"))
    out = oi.run_import(cam, {"backup_root": backup, "merge": False,
                              "transcribe": False})
    assert out["reused"] == [os.path.join(scan["dest_dir"], "A.MP4")]
    assert "A.MP4|100" in oi._load_manifest(backup)
