"""Microphone read/lock — headless (no floating overlay, no naggy popups).

Preserves the useful part of the old Studio Flow: keep the mic pinned at a
target level so a call app can't yank it around. Status is surfaced on the app's
screen instead of a floating percentage bar. Lock state persists across runs.
"""
from __future__ import annotations

import threading
import time

from pycaw.pycaw import AudioUtilities, IAudioEndpointVolume
from comtypes import CLSCTX_ALL

from settings_store import load_settings, save_settings

POLL_INTERVAL = 0.5


def get_mic_volume() -> int | None:
    try:
        mic = AudioUtilities.GetMicrophone()
        if mic is None:
            return None
        interface = mic.Activate(IAudioEndpointVolume._iid_, CLSCTX_ALL, None)
        volume = interface.QueryInterface(IAudioEndpointVolume)
        return round(volume.GetMasterVolumeLevelScalar() * 100)
    except Exception:
        return None


def set_mic_volume(pct: int) -> bool:
    try:
        mic = AudioUtilities.GetMicrophone()
        if mic is None:
            return False
        interface = mic.Activate(IAudioEndpointVolume._iid_, CLSCTX_ALL, None)
        volume = interface.QueryInterface(IAudioEndpointVolume)
        volume.SetMasterVolumeLevelScalar(max(0, min(100, pct)) / 100.0, None)
        return True
    except Exception:
        return False


class MicMonitor:
    """Silent background thread that keeps the mic at `target_vol` while locked."""

    def __init__(self):
        self._stop = threading.Event()
        settings = load_settings()
        # First-run default: locked at 90% (unchanged behaviour, just silent).
        self.locked = bool(settings.get("mic_locked", True))
        self.target_vol = int(settings.get("mic_lock_target", 90))
        if self.locked:
            set_mic_volume(self.target_vol)
        self.last_vol = get_mic_volume()

    # ── state ────────────────────────────────────────────────────────────────
    def status(self) -> dict:
        return {"volume": self.last_vol, "locked": self.locked,
                "target": self.target_vol}

    def _persist(self):
        s = load_settings()
        s["mic_locked"] = self.locked
        s["mic_lock_target"] = self.target_vol
        save_settings(s)

    def enable_lock(self, target: int | None = None):
        if target is not None:
            self.target_vol = int(target)
        elif self.last_vol is not None:
            self.target_vol = self.last_vol
        self.locked = True
        set_mic_volume(self.target_vol)
        self.last_vol = self.target_vol
        self._persist()

    def disable_lock(self):
        self.locked = False
        cur = get_mic_volume()
        if cur is not None:
            self.last_vol = cur
        self._persist()

    def set_target(self, target: int):
        self.target_vol = max(0, min(100, int(target)))
        if self.locked:
            set_mic_volume(self.target_vol)
            self.last_vol = self.target_vol
        self._persist()

    # ── loop ─────────────────────────────────────────────────────────────────
    def start(self):
        threading.Thread(target=self._loop, daemon=True).start()

    def stop(self):
        self._stop.set()

    def _loop(self):
        while not self._stop.is_set():
            time.sleep(POLL_INTERVAL)
            current = get_mic_volume()
            if current is None:
                continue
            # Silently restore the lock if something else moved the mic.
            if self.locked and current != self.target_vol:
                if set_mic_volume(self.target_vol):
                    current = self.target_vol
            self.last_vol = current
