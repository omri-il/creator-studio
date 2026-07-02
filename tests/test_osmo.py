"""Unit tests for the pure Osmo import logic (no camera / ffmpeg needed)."""
import os
import sys

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
