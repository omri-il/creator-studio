import threading
import time
import os
import re
import subprocess
from datetime import datetime
import tkinter as tk
from tkinter import filedialog, ttk
import ctypes
import ctypes.wintypes as wintypes

import psutil
import pystray
from PIL import Image, ImageDraw, ImageFont
from pycaw.pycaw import (
    AudioUtilities,
    IAudioEndpointVolume,
    IAudioSessionManager2,
    IAudioSessionControl2,
    AudioSession,
)
from comtypes import CLSCTX_ALL

# ── Version ───────────────────────────────────────────────────────────────────
APP_VERSION = "1.0.0"
APP_NAME = "Studio Flow"

# ── Config ────────────────────────────────────────────────────────────────────
POLL_INTERVAL = 0.5          # seconds between volume checks
CHANGE_THRESHOLD = 1         # minimum % change to trigger a log entry

# When running as a PyInstaller bundle, __file__ is inside a temp dir.
# sys.executable points to the actual .exe — use that for stable paths.
import sys as _sys
_IS_FROZEN = getattr(_sys, "frozen", False)
_APP_DIR = (
    os.path.dirname(_sys.executable)
    if _IS_FROZEN
    else os.path.dirname(os.path.abspath(__file__))
)

# User data (log file) goes in LOCALAPPDATA when installed — Program Files is read-only
# for non-admin users. In dev, keep it in the project folder.
if _IS_FROZEN:
    _USER_DIR = os.path.join(
        os.environ.get("LOCALAPPDATA", os.path.expanduser("~")),
        "StudioFlow",
    )
    os.makedirs(_USER_DIR, exist_ok=True)
else:
    _USER_DIR = _APP_DIR
LOG_FILE = os.path.join(_USER_DIR, "mic_log.txt")

# ffmpeg: prefer a copy bundled next to the executable, fall back to PATH
_BUNDLED_FFMPEG = os.path.join(_APP_DIR, "ffmpeg.exe")
FFMPEG = _BUNDLED_FFMPEG if os.path.isfile(_BUNDLED_FFMPEG) else "ffmpeg"

# DaVinci Resolve integration (only active on the home PC where these paths exist)
DAVINCI_NEW_PROJECT_SCRIPT = r"E:\DaVinci Automation\scripts\utils\new_project.py"
DAVINCI_IMPORT_PROXY_SCRIPT = r"E:\DaVinci Automation\scripts\utils\import_and_proxy.py"
DAVINCI_DEFAULT_BASE = r"E:\Video Projects"
HAS_DAVINCI = os.path.isfile(DAVINCI_NEW_PROJECT_SCRIPT)

# Possible install locations for DaVinci Resolve on this user's machines
DAVINCI_EXE_CANDIDATES = [
    r"E:\Program Files\Blackmagic Design\DaVinci Resolve\Resolve.exe",
    r"C:\Program Files\Blackmagic Design\DaVinci Resolve\Resolve.exe",
]

def is_resolve_running() -> bool:
    try:
        for p in psutil.process_iter(attrs=["name"]):
            n = (p.info.get("name") or "").lower()
            if n in ("resolve.exe", "resolve"):
                return True
    except Exception:
        pass
    return False

def find_resolve_exe() -> str | None:
    for path in DAVINCI_EXE_CANDIDATES:
        if os.path.isfile(path):
            return path
    return None
DAVINCI_CATEGORIES = [
    "YouTube - Being a Teacher",
    "YouTube - Biology Is Life",
    "GEG",
    "Playback Theater",
    "Workshops",
    "School",
    "Personal",
]
SETTINGS_FILE = os.path.join(_USER_DIR, "settings.json")

def load_settings() -> dict:
    try:
        import json
        with open(SETTINGS_FILE, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return {}

def save_settings(settings: dict) -> None:
    try:
        import json
        with open(SETTINGS_FILE, "w", encoding="utf-8") as f:
            json.dump(settings, f, indent=2)
    except Exception:
        pass

def get_davinci_base_dir() -> str:
    return load_settings().get("davinci_base_dir") or DAVINCI_DEFAULT_BASE

def set_davinci_base_dir(path: str) -> None:
    settings = load_settings()
    settings["davinci_base_dir"] = path
    save_settings(settings)

KNOWN_AUDIO_APPS = {
    "discord.exe", "zoom.exe", "teams.exe", "obs64.exe", "obs32.exe",
    "skype.exe", "slack.exe", "loom.exe", "webex.exe", "chrome.exe",
    "firefox.exe", "streamlabs obs.exe", "streamlabsobs.exe", "msedge.exe",
    "audiodg.exe", "sndvol.exe", "realtek hd audio manager.exe",
    "rtkaudioservice.exe", "nahimicservice.exe", "soundswitch.exe",
    "nvidia broadcast.exe", "nvidiabroadcast.exe",
}

# ── Monitor enumeration ───────────────────────────────────────────────────────

def get_monitors():
    """Return list of (left, top, right, bottom) for each monitor."""
    monitors = []
    MONITORENUMPROC = ctypes.WINFUNCTYPE(
        ctypes.c_bool,
        ctypes.c_ulong, ctypes.c_ulong,
        ctypes.POINTER(wintypes.RECT),
        ctypes.c_double,
    )
    def callback(_hmonitor, _hdc, lprect, _lparam):
        r = lprect.contents
        monitors.append((r.left, r.top, r.right, r.bottom))
        return True
    ctypes.windll.user32.EnumDisplayMonitors(None, None, MONITORENUMPROC(callback), 0)
    return monitors if monitors else [(0, 0, 1920, 1080)]


# ── Floating overlay ─────────────────────────────────────────────────────────

class VolumeOverlay:
    """Small always-on-top floating window showing current mic %."""

    def __init__(self):
        self.root = tk.Tk()
        self.root.overrideredirect(True)       # no title bar / borders
        self.root.attributes("-topmost", True)
        self.root.attributes("-alpha", 0.75)
        self.root.configure(bg="#1a1a1a")

        self.label = tk.Label(
            self.root,
            text="🎤 --",
            font=("Segoe UI", 14, "bold"),
            fg="white",
            bg="#1a1a1a",
            padx=10,
            pady=6,
        )
        self.label.pack()

        self._monitors = get_monitors()
        self._monitor_idx = 0
        self.monitor = None  # set after VolumeMonitor is constructed

        # Position: bottom-right corner of first monitor
        self.root.update_idletasks()
        self._snap_to_monitor(self._monitor_idx)

        # Left-click drag, right-click menu
        self.label.bind("<ButtonPress-1>", self._drag_start)
        self.label.bind("<B1-Motion>", self._drag_move)
        self.label.bind("<ButtonPress-3>", self._show_menu)
        self._drag_x = 0
        self._drag_y = 0

    def _snap_to_monitor(self, idx):
        left, top, right, bottom = self._monitors[idx]
        w = self.root.winfo_reqwidth()
        h = self.root.winfo_reqheight()
        x = right - w - 20
        y = bottom - h - 60
        self.root.geometry(f"+{x}+{y}")

    def _show_menu(self, event):
        menu = tk.Menu(self.root, tearoff=0)
        if self.monitor is not None:
            if self.monitor.locked:
                menu.add_command(
                    label=f"🔓 Unlock (currently locked at {self.monitor.target_vol}%)",
                    command=self.monitor.disable_lock,
                )
            else:
                cur = self.monitor.last_vol if self.monitor.last_vol is not None else 80
                menu.add_command(
                    label=f"🔒 Lock at current volume ({cur}%)",
                    command=lambda: self.monitor.enable_lock(cur),
                )
            menu.add_separator()
        menu.add_command(label="🎚️ Analyze file for YouTube…", command=self._analyze_file)
        menu.add_separator()
        if len(self._monitors) > 1:
            menu.add_command(label="Move to next screen", command=self._move_to_next_screen)
        else:
            menu.add_command(label="Only one screen detected", state="disabled")
        try:
            menu.tk_popup(event.x_root, event.y_root)
        finally:
            menu.grab_release()

    def _analyze_file(self):
        path = filedialog.askopenfilename(
            title="Select audio/video file to analyze",
            filetypes=[
                ("Audio/Video files", "*.mp4 *.mkv *.mov *.webm *.wav *.mp3 *.m4a *.flac *.aac *.ogg"),
                ("All files", "*.*"),
            ],
        )
        if not path:
            return

        progress = tk.Toplevel(self.root)
        progress.title("Analyzing…")
        progress.configure(bg="#1a1a1a")
        progress.attributes("-topmost", True)

        tk.Frame(progress, bg="#1a1a1a", height=15).pack()
        tk.Label(
            progress, text=os.path.basename(path),
            fg="#cccccc", bg="#1a1a1a", font=("Segoe UI", 10),
            padx=30, pady=5,
        ).pack()
        status = tk.Label(
            progress, text="Starting…",
            fg="white", bg="#1a1a1a", font=("Segoe UI", 11),
            padx=30, pady=5,
        )
        status.pack()
        bar = ttk.Progressbar(progress, mode="determinate",
                              maximum=100, length=340)
        bar.pack(padx=30, pady=(10, 25))

        progress.update_idletasks()
        sw, sh = progress.winfo_screenwidth(), progress.winfo_screenheight()
        w, h = progress.winfo_reqwidth(), progress.winfo_reqheight()
        progress.geometry(f"+{(sw - w) // 2}+{(sh - h) // 2}")

        def on_progress(pct, text):
            def update():
                try:
                    bar["value"] = pct
                    status.config(text=text)
                except Exception:
                    pass
            self.root.after(0, update)

        def worker():
            result = analyze_audio_file(path, on_progress)
            self.root.after(0, lambda: self._on_analysis_done(progress, path, result))

        threading.Thread(target=worker, daemon=True).start()

    def _on_analysis_done(self, progress_window, path: str, result: dict | None):
        try:
            progress_window.destroy()
        except Exception:
            pass
        if result is None:
            self._show_error(f"Analysis failed for:\n{os.path.basename(path)}\n\n"
                             "Check that ffmpeg is installed and the file is readable.")
            return
        self._show_analysis_result(path, result)

    def _show_error(self, msg: str):
        win = tk.Toplevel(self.root)
        win.title("Error")
        win.configure(bg="#1a1a1a")
        win.attributes("-topmost", True)
        tk.Label(win, text=msg, fg="#dc3545", bg="#1a1a1a",
                 font=("Segoe UI", 11), padx=25, pady=20, justify="left").pack()
        tk.Button(win, text="Close", command=win.destroy).pack(pady=(0, 15))

    def _show_analysis_result(self, path: str, result: dict):
        lufs = result["integrated_lufs"]
        peak = result["true_peak_dbfs"]
        lra = result["loudness_range"]
        duration = result["duration_sec"]

        verdict, color, observations, recommendation = youtube_verdict(lufs, peak)

        win = tk.Toplevel(self.root)
        win.title("YouTube Audio Analysis")
        win.configure(bg="#1a1a1a")
        win.attributes("-topmost", True)

        # Filename
        tk.Frame(win, bg="#1a1a1a", height=15).pack()
        tk.Label(
            win, text=os.path.basename(path), fg="#cccccc", bg="#1a1a1a",
            font=("Segoe UI", 10), padx=20, pady=5,
        ).pack(anchor="w")

        # Verdict (big, colored)
        tk.Label(
            win, text=verdict, fg="white", bg=color,
            font=("Segoe UI", 16, "bold"), padx=20, pady=12,
        ).pack(fill="x", padx=15)

        # Measurements
        def row(label: str, value: str):
            frame = tk.Frame(win, bg="#1a1a1a")
            frame.pack(fill="x", padx=20, pady=2)
            tk.Label(frame, text=label, fg="#999999", bg="#1a1a1a",
                     font=("Segoe UI", 10), width=22, anchor="w").pack(side="left")
            tk.Label(frame, text=value, fg="white", bg="#1a1a1a",
                     font=("Consolas", 11, "bold"), anchor="w").pack(side="left")

        tk.Frame(win, bg="#1a1a1a", height=10).pack()
        row("Integrated Loudness:", f"{lufs:.1f} LUFS   (target: -14)")
        row("True Peak:", f"{peak:.1f} dBTP   (target: ≤ -1)")
        if lra is not None:
            row("Loudness Range:", f"{lra:.1f} LU")
        if duration is not None:
            m, s = divmod(int(duration), 60)
            row("Duration:", f"{m}:{s:02d}")

        # Observations
        tk.Frame(win, bg="#1a1a1a", height=12).pack()
        obs_frame = tk.Frame(win, bg="#1a1a1a")
        obs_frame.pack(fill="x", padx=20)
        for obs in observations:
            tk.Label(obs_frame, text=obs, fg="white", bg="#1a1a1a",
                     font=("Segoe UI", 10), anchor="w", justify="left").pack(anchor="w", pady=1)

        # Recommendation
        if recommendation:
            tk.Frame(win, bg="#1a1a1a", height=12).pack()
            tk.Label(
                win, text=f"💡 {recommendation}", fg="#ffa500", bg="#1a1a1a",
                font=("Segoe UI", 11, "bold"), padx=20, wraplength=460, justify="left",
            ).pack(anchor="w", padx=15)

        tk.Frame(win, bg="#1a1a1a", height=15).pack()
        button_row = tk.Frame(win, bg="#1a1a1a")
        button_row.pack(pady=(0, 15))
        if not verdict.startswith("✅"):
            tk.Button(
                button_row, text="🎛️ Normalize to -14 LUFS",
                command=lambda: self._start_normalize(path, win),
                font=("Segoe UI", 10, "bold"), padx=20, pady=6,
                bg="#28a745", fg="white", activebackground="#218838",
            ).pack(side="left", padx=6)
        if HAS_DAVINCI:
            tk.Button(
                button_row, text="🎬 Create DaVinci project…",
                command=lambda: self._create_davinci_project(path, win),
                font=("Segoe UI", 10, "bold"), padx=20, pady=6,
                bg="#6f42c1", fg="white", activebackground="#5a349a",
            ).pack(side="left", padx=6)
        tk.Button(button_row, text="Close", command=win.destroy,
                  font=("Segoe UI", 10), padx=20, pady=6).pack(side="left", padx=6)

        # Center window
        win.update_idletasks()
        sw, sh = win.winfo_screenwidth(), win.winfo_screenheight()
        w, h = win.winfo_reqwidth(), win.winfo_reqheight()
        win.geometry(f"+{(sw - w) // 2}+{(sh - h) // 2}")

    def _create_davinci_project(self, input_path: str, parent_win):
        """Dialog: pick category + title, then create folder tree + Resolve project
        and copy the analyzed file(s) into Raw Footage/."""
        try:
            parent_win.destroy()
        except Exception:
            pass

        win = tk.Toplevel(self.root)
        win.title("Create DaVinci project")
        win.configure(bg="#1a1a1a")
        win.attributes("-topmost", True)

        tk.Frame(win, bg="#1a1a1a", height=15).pack()
        tk.Label(
            win, text="Create DaVinci project from this file",
            fg="white", bg="#1a1a1a", font=("Segoe UI", 13, "bold"),
            padx=25, pady=5,
        ).pack(anchor="w")
        tk.Label(
            win, text=os.path.basename(input_path),
            fg="#999999", bg="#1a1a1a", font=("Segoe UI", 9),
            padx=25, pady=2,
        ).pack(anchor="w")

        tk.Frame(win, bg="#1a1a1a", height=15).pack()

        # Location (base folder) — persisted across runs
        tk.Label(win, text="Location:", fg="#cccccc", bg="#1a1a1a",
                 font=("Segoe UI", 10), padx=25).pack(anchor="w")
        base_dir_var = tk.StringVar(value=get_davinci_base_dir())
        loc_row = tk.Frame(win, bg="#1a1a1a")
        loc_row.pack(padx=25, pady=(2, 10), anchor="w", fill="x")
        loc_entry = tk.Entry(
            loc_row, textvariable=base_dir_var, width=35,
            font=("Segoe UI", 10), bg="#2a2a2a", fg="white",
            insertbackground="white", relief="flat",
        )
        loc_entry.pack(side="left", ipady=4)

        def browse_base_dir():
            picked = filedialog.askdirectory(
                title="Pick base folder for video projects",
                initialdir=base_dir_var.get() or DAVINCI_DEFAULT_BASE,
                parent=win,
            )
            if picked:
                base_dir_var.set(picked)

        def reset_base_dir():
            base_dir_var.set(DAVINCI_DEFAULT_BASE)

        tk.Button(loc_row, text="Browse…", command=browse_base_dir,
                  font=("Segoe UI", 9), padx=8, pady=2).pack(side="left", padx=(6, 0))
        tk.Button(loc_row, text="Default", command=reset_base_dir,
                  font=("Segoe UI", 9), padx=8, pady=2).pack(side="left", padx=(4, 0))

        # Category
        tk.Label(win, text="Category:", fg="#cccccc", bg="#1a1a1a",
                 font=("Segoe UI", 10), padx=25).pack(anchor="w")
        category_var = tk.StringVar(value=DAVINCI_CATEGORIES[0])
        cat_combo = ttk.Combobox(
            win, textvariable=category_var, values=DAVINCI_CATEGORIES,
            state="readonly", width=40, font=("Segoe UI", 10),
        )
        cat_combo.pack(padx=25, pady=(2, 10), anchor="w")

        # Title
        tk.Label(win, text="Project title:", fg="#cccccc", bg="#1a1a1a",
                 font=("Segoe UI", 10), padx=25).pack(anchor="w")
        default_title = os.path.splitext(os.path.basename(input_path))[0]
        # Strip "_normalized" suffix so re-analyzed files don't carry it through
        if default_title.endswith("_normalized"):
            default_title = default_title[: -len("_normalized")]
        title_var = tk.StringVar(value=default_title)
        title_entry = tk.Entry(
            win, textvariable=title_var, width=42,
            font=("Segoe UI", 10), bg="#2a2a2a", fg="white",
            insertbackground="white", relief="flat",
        )
        title_entry.pack(padx=25, pady=(2, 10), anchor="w", ipady=4)

        # Options
        copy_var = tk.BooleanVar(value=True)
        tk.Checkbutton(
            win, text="Copy this file into Raw Footage\\",
            variable=copy_var, bg="#1a1a1a", fg="#cccccc",
            activebackground="#1a1a1a", activeforeground="white",
            selectcolor="#1a1a1a", font=("Segoe UI", 10),
        ).pack(padx=25, anchor="w")

        # Only show "also copy normalized" if there's a sibling _normalized file
        stem, ext = os.path.splitext(input_path)
        normalized_sibling = None
        if stem.endswith("_normalized"):
            candidate = stem[: -len("_normalized")] + ext
            if os.path.isfile(candidate):
                normalized_sibling = ("original", candidate)
        else:
            candidate = f"{stem}_normalized{ext}"
            if os.path.isfile(candidate):
                normalized_sibling = ("normalized", candidate)

        copy_sibling_var = tk.BooleanVar(value=bool(normalized_sibling))
        if normalized_sibling:
            label = ("Also copy the _normalized version"
                     if normalized_sibling[0] == "normalized"
                     else "Also copy the original (non-normalized) version")
            tk.Checkbutton(
                win, text=label,
                variable=copy_sibling_var, bg="#1a1a1a", fg="#cccccc",
                activebackground="#1a1a1a", activeforeground="white",
                selectcolor="#1a1a1a", font=("Segoe UI", 10),
            ).pack(padx=25, anchor="w")

        gen_proxy_var = tk.BooleanVar(value=True)
        tk.Checkbutton(
            win, text="Generate proxies in Resolve (half-res, background)",
            variable=gen_proxy_var, bg="#1a1a1a", fg="#cccccc",
            activebackground="#1a1a1a", activeforeground="white",
            selectcolor="#1a1a1a", font=("Segoe UI", 10),
        ).pack(padx=25, anchor="w")

        tk.Frame(win, bg="#1a1a1a", height=15).pack()
        button_row = tk.Frame(win, bg="#1a1a1a")
        button_row.pack(pady=(0, 15))

        def on_create():
            category = category_var.get()
            title = title_var.get().strip()
            base_dir = base_dir_var.get().strip() or DAVINCI_DEFAULT_BASE
            if not title:
                title_entry.focus_set()
                return
            # Persist base folder for next time
            set_davinci_base_dir(base_dir)
            files_to_copy = []
            if copy_var.get():
                files_to_copy.append(input_path)
            if copy_sibling_var.get() and normalized_sibling:
                files_to_copy.append(normalized_sibling[1])
            gen_proxy = gen_proxy_var.get()
            win.destroy()
            self._run_davinci_project_create(title, category, base_dir,
                                             files_to_copy, gen_proxy)

        tk.Button(
            button_row, text="Create",
            command=on_create,
            font=("Segoe UI", 10, "bold"), padx=24, pady=6,
            bg="#6f42c1", fg="white", activebackground="#5a349a",
        ).pack(side="left", padx=6)
        tk.Button(
            button_row, text="Cancel", command=win.destroy,
            font=("Segoe UI", 10), padx=20, pady=6,
        ).pack(side="left", padx=6)

        title_entry.focus_set()
        win.update_idletasks()
        sw, sh = win.winfo_screenwidth(), win.winfo_screenheight()
        w, h = win.winfo_reqwidth(), win.winfo_reqheight()
        win.geometry(f"+{(sw - w) // 2}+{(sh - h) // 2}")

    def _run_davinci_project_create(self, title: str, category: str,
                                    base_dir: str, files_to_copy: list,
                                    gen_proxy: bool = False):
        """Run new_project.py in a background thread with a progress dialog,
        then copy the requested files into Raw Footage/ and show a done dialog."""
        progress = tk.Toplevel(self.root)
        progress.title("Creating project…")
        progress.configure(bg="#1a1a1a")
        progress.attributes("-topmost", True)
        tk.Frame(progress, bg="#1a1a1a", height=25).pack()
        status = tk.Label(
            progress, text="Starting DaVinci Resolve…",
            fg="white", bg="#1a1a1a", padx=30, pady=10,
            font=("Segoe UI", 11),
        )
        status.pack()
        bar = ttk.Progressbar(progress, mode="indeterminate", length=340)
        bar.pack(padx=30, pady=(10, 25))
        bar.start(10)
        progress.update_idletasks()
        sw, sh = progress.winfo_screenwidth(), progress.winfo_screenheight()
        w, h = progress.winfo_reqwidth(), progress.winfo_reqheight()
        progress.geometry(f"+{(sw - w) // 2}+{(sh - h) // 2}")

        def set_status(text):
            self.root.after(0, lambda: status.config(text=text))

        def worker():
            try:
                # Ensure Resolve is running before asking new_project.py to
                # create the Resolve project — otherwise the script silently
                # skips that step and the user sees only folders, no project.
                if not is_resolve_running():
                    exe = find_resolve_exe()
                    if exe:
                        set_status("DaVinci Resolve not running — launching…")
                        try:
                            subprocess.Popen([exe], creationflags=(
                                subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0))
                        except Exception as e:
                            self.root.after(0, lambda: self._on_davinci_done(
                                progress, None, None, None,
                                f"Could not launch DaVinci Resolve:\n{e}", 0))
                            return
                        # Wait up to 90 s for Resolve's scripting server to accept us.
                        set_status("Waiting for DaVinci Resolve to finish loading…")
                        deadline = time.time() + 90
                        ready = False
                        while time.time() < deadline:
                            if is_resolve_running():
                                # Process is up — scripting may still need a few more seconds.
                                time.sleep(5)
                                ready = True
                                break
                            time.sleep(1)
                        if not ready:
                            self.root.after(0, lambda: self._on_davinci_done(
                                progress, None, None, None,
                                "DaVinci Resolve did not finish launching within 90 s.\n"
                                "Open it manually and try again.", 0))
                            return
                    else:
                        self.root.after(0, lambda: self._on_davinci_done(
                            progress, None, None, None,
                            "DaVinci Resolve is not running and I could not find Resolve.exe.\n"
                            "Please open DaVinci Resolve and try again.", 0))
                        return

                set_status("Creating folders and Resolve project…")
                result = subprocess.run(
                    ["py", "-3.10", DAVINCI_NEW_PROJECT_SCRIPT,
                     title, "--category", category,
                     "--base-dir", base_dir],
                    capture_output=True, text=True, timeout=120,
                    creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0,
                )
                if result.returncode != 0:
                    err = (result.stderr or result.stdout or "unknown error").strip()
                    self.root.after(0, lambda: self._on_davinci_done(
                        progress, None, None, None, f"Script failed:\n{err}", 0
                    ))
                    return

                # Parse the machine-readable markers
                out = result.stdout or ""
                project_path = None
                project_name = None
                resolve_name = None
                for line in out.splitlines():
                    if line.startswith("RESULT_PATH="):
                        project_path = line[len("RESULT_PATH="):].strip()
                    elif line.startswith("RESULT_NAME="):
                        project_name = line[len("RESULT_NAME="):].strip()
                    elif line.startswith("RESULT_RESOLVE_NAME="):
                        resolve_name = line[len("RESULT_RESOLVE_NAME="):].strip()
                if not project_path or not os.path.isdir(project_path):
                    self.root.after(0, lambda: self._on_davinci_done(
                        progress, None, None, None,
                        "Script ran but no project folder was found in its output.",
                        0,
                    ))
                    return

                raw_footage_dir = os.path.join(project_path, "Raw Footage")
                copied = []
                copy_errors = []
                for src in files_to_copy:
                    set_status(f"Copying {os.path.basename(src)}…")
                    try:
                        dst = os.path.join(raw_footage_dir, os.path.basename(src))
                        # Stream-copy (file can be large) — use shutil.copyfile
                        import shutil
                        shutil.copyfile(src, dst)
                        copied.append(dst)
                    except Exception as e:
                        copy_errors.append(f"{os.path.basename(src)}: {e}")

                # Optionally queue proxies in Resolve for the copied files
                proxies_queued = 0
                if gen_proxy and copied and resolve_name and \
                        os.path.isfile(DAVINCI_IMPORT_PROXY_SCRIPT):
                    set_status("Importing to media pool and queuing proxies…")
                    try:
                        px = subprocess.run(
                            ["py", "-3.10", DAVINCI_IMPORT_PROXY_SCRIPT,
                             "--project", resolve_name,
                             "--files", *copied],
                            capture_output=True, text=True, timeout=120,
                            creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0,
                        )
                        for line in (px.stdout or "").splitlines():
                            if line.startswith("PROXIES_QUEUED="):
                                try:
                                    proxies_queued = int(
                                        line[len("PROXIES_QUEUED="):].strip()
                                    )
                                except ValueError:
                                    proxies_queued = 0
                    except Exception:
                        # Proxy is nice-to-have — never fail the whole flow
                        proxies_queued = 0

                # If Resolve project creation was silently skipped, surface it.
                warning_bits = []
                if copy_errors:
                    warning_bits.append("\n".join(copy_errors))
                if not resolve_name:
                    warning_bits.append(
                        "⚠ Folders + files created, but the DaVinci Resolve project "
                        "was NOT created (Resolve didn't respond). Open Resolve "
                        "manually and try again to create the project + proxies."
                    )
                elif gen_proxy and copied and proxies_queued == 0:
                    warning_bits.append(
                        "⚠ Resolve project created, but proxy generation did not "
                        "queue any jobs. Media may not have imported."
                    )
                warn = "\n\n".join(warning_bits) if warning_bits else None
                self.root.after(0, lambda: self._on_davinci_done(
                    progress, project_path, project_name, copied,
                    warn,
                    proxies_queued,
                ))
            except subprocess.TimeoutExpired:
                self.root.after(0, lambda: self._on_davinci_done(
                    progress, None, None, None,
                    "Script timed out after 2 minutes.", 0
                ))
            except FileNotFoundError:
                self.root.after(0, lambda: self._on_davinci_done(
                    progress, None, None, None,
                    "Could not find 'py' launcher. Is Python 3.10 installed?", 0
                ))
            except Exception as e:
                self.root.after(0, lambda: self._on_davinci_done(
                    progress, None, None, None, f"Unexpected error: {e}", 0
                ))

        threading.Thread(target=worker, daemon=True).start()

    def _on_davinci_done(self, progress_window, project_path, project_name,
                         copied_files, error_msg, proxies_queued: int = 0):
        try:
            progress_window.destroy()
        except Exception:
            pass

        if project_path is None:
            self._show_error(f"Creating the DaVinci project failed.\n\n{error_msg or ''}")
            return

        win = tk.Toplevel(self.root)
        win.title("DaVinci Project Created")
        win.configure(bg="#1a1a1a")
        win.attributes("-topmost", True)

        tk.Frame(win, bg="#1a1a1a", height=15).pack()
        tk.Label(
            win, text="✅ Project created", fg="white", bg="#6f42c1",
            font=("Segoe UI", 16, "bold"), padx=20, pady=12,
        ).pack(fill="x", padx=15)

        tk.Frame(win, bg="#1a1a1a", height=12).pack()
        if project_name:
            tk.Label(win, text=project_name, fg="white", bg="#1a1a1a",
                     font=("Segoe UI", 11, "bold"), padx=20).pack(anchor="w")
        tk.Label(win, text=project_path, fg="#cccccc", bg="#1a1a1a",
                 font=("Consolas", 9), padx=20, wraplength=500,
                 justify="left").pack(anchor="w")

        if copied_files:
            tk.Frame(win, bg="#1a1a1a", height=10).pack()
            tk.Label(win, text=f"Copied {len(copied_files)} file(s) to Raw Footage\\",
                     fg="#2eb85c", bg="#1a1a1a", font=("Segoe UI", 10),
                     padx=20).pack(anchor="w")
        if proxies_queued > 0:
            tk.Label(win,
                     text=f"🎞️ {proxies_queued} proxy job(s) queued — check Playback → Background Tasks in Resolve.",
                     fg="#2eb85c", bg="#1a1a1a", font=("Segoe UI", 10),
                     padx=20, wraplength=500, justify="left").pack(anchor="w")
        if error_msg:
            tk.Frame(win, bg="#1a1a1a", height=8).pack()
            tk.Label(win, text=f"⚠ Some files did not copy:\n{error_msg}",
                     fg="#ffa500", bg="#1a1a1a", font=("Segoe UI", 9),
                     padx=20, wraplength=500, justify="left").pack(anchor="w")

        tk.Frame(win, bg="#1a1a1a", height=15).pack()
        row = tk.Frame(win, bg="#1a1a1a")
        row.pack(pady=(0, 15))

        def open_folder():
            try:
                os.startfile(project_path)
            except Exception:
                pass

        tk.Button(row, text="📁 Open project folder", command=open_folder,
                  font=("Segoe UI", 10, "bold"), padx=16, pady=6,
                  bg="#6f42c1", fg="white", activebackground="#5a349a",
                  ).pack(side="left", padx=6)
        tk.Button(row, text="Close", command=win.destroy,
                  font=("Segoe UI", 10), padx=16, pady=6).pack(side="left", padx=6)

        win.update_idletasks()
        sw, sh = win.winfo_screenwidth(), win.winfo_screenheight()
        w, h = win.winfo_reqwidth(), win.winfo_reqheight()
        win.geometry(f"+{(sw - w) // 2}+{(sh - h) // 2}")

    def _start_normalize(self, input_path: str, parent_win):
        """Run two-pass loudnorm in a background thread; show progress dialog."""
        stem, ext = os.path.splitext(input_path)
        output_path = f"{stem}_normalized{ext}"

        try:
            parent_win.destroy()
        except Exception:
            pass

        progress = tk.Toplevel(self.root)
        progress.title("Normalizing…")
        progress.configure(bg="#1a1a1a")
        progress.attributes("-topmost", True)

        tk.Frame(progress, bg="#1a1a1a", height=25).pack()
        status = tk.Label(
            progress, text="Starting…",
            fg="white", bg="#1a1a1a", padx=30, pady=10,
            font=("Segoe UI", 11),
        )
        status.pack()

        tk.Label(
            progress, text=os.path.basename(input_path),
            fg="#999999", bg="#1a1a1a", font=("Segoe UI", 9), padx=30,
        ).pack()

        bar = ttk.Progressbar(progress, mode="indeterminate", length=320)
        bar.pack(padx=30, pady=(15, 25))
        bar.start(10)

        progress.update_idletasks()
        sw, sh = progress.winfo_screenwidth(), progress.winfo_screenheight()
        w, h = progress.winfo_reqwidth(), progress.winfo_reqheight()
        progress.geometry(f"+{(sw - w) // 2}+{(sh - h) // 2}")

        def on_progress(_stage, text):
            self.root.after(0, lambda: status.config(text=text))

        def worker():
            result = normalize_audio_file(input_path, output_path, on_progress)
            self.root.after(0, lambda: self._on_normalize_done(
                progress, input_path, output_path, result
            ))

        threading.Thread(target=worker, daemon=True).start()

    def _on_normalize_done(self, progress_window, input_path: str,
                           output_path: str, result: dict | None):
        try:
            progress_window.destroy()
        except Exception:
            pass

        if result is None or not os.path.exists(output_path):
            self._show_error(
                f"Normalization failed for:\n{os.path.basename(input_path)}\n\n"
                "Check that ffmpeg is installed and the file is readable."
            )
            return

        win = tk.Toplevel(self.root)
        win.title("Normalization Complete")
        win.configure(bg="#1a1a1a")
        win.attributes("-topmost", True)

        tk.Frame(win, bg="#1a1a1a", height=15).pack()
        tk.Label(
            win, text="✅ Done", fg="white", bg="#2eb85c",
            font=("Segoe UI", 16, "bold"), padx=20, pady=12,
        ).pack(fill="x", padx=15)

        tk.Frame(win, bg="#1a1a1a", height=12).pack()
        tk.Label(
            win, text="Saved to:", fg="#999999", bg="#1a1a1a",
            font=("Segoe UI", 10), padx=20,
        ).pack(anchor="w")
        tk.Label(
            win, text=output_path, fg="white", bg="#1a1a1a",
            font=("Consolas", 10), padx=20, wraplength=500, justify="left",
        ).pack(anchor="w")

        tk.Frame(win, bg="#1a1a1a", height=8).pack()
        tk.Label(
            win,
            text=f"Measured input: {result['input_i']:.1f} LUFS, "
                 f"peak {result['input_tp']:.1f} dBTP\n"
                 f"Applied offset: {result['target_offset']:+.1f} dB",
            fg="#cccccc", bg="#1a1a1a", font=("Segoe UI", 10),
            padx=20, justify="left",
        ).pack(anchor="w")

        tk.Frame(win, bg="#1a1a1a", height=15).pack()
        row = tk.Frame(win, bg="#1a1a1a")
        row.pack(pady=(0, 15))

        def reanalyze():
            win.destroy()
            progress = tk.Toplevel(self.root)
            progress.title("Analyzing…")
            progress.configure(bg="#1a1a1a")
            progress.attributes("-topmost", True)

            tk.Frame(progress, bg="#1a1a1a", height=15).pack()
            tk.Label(progress, text=os.path.basename(output_path),
                     fg="#cccccc", bg="#1a1a1a", font=("Segoe UI", 10),
                     padx=30, pady=5).pack()
            status2 = tk.Label(progress, text="Starting…",
                               fg="white", bg="#1a1a1a",
                               font=("Segoe UI", 11), padx=30, pady=5)
            status2.pack()
            bar2 = ttk.Progressbar(progress, mode="determinate",
                                   maximum=100, length=340)
            bar2.pack(padx=30, pady=(10, 25))

            progress.update_idletasks()
            sw, sh = progress.winfo_screenwidth(), progress.winfo_screenheight()
            w, h = progress.winfo_reqwidth(), progress.winfo_reqheight()
            progress.geometry(f"+{(sw - w) // 2}+{(sh - h) // 2}")

            def on_progress2(pct, text):
                def update():
                    try:
                        bar2["value"] = pct
                        status2.config(text=text)
                    except Exception:
                        pass
                self.root.after(0, update)

            def w2():
                r = analyze_audio_file(output_path, on_progress2)
                self.root.after(0, lambda: self._on_analysis_done(progress, output_path, r))
            threading.Thread(target=w2, daemon=True).start()

        def open_folder():
            try:
                os.startfile(os.path.dirname(output_path))
            except Exception:
                pass

        if HAS_DAVINCI:
            tk.Button(row, text="🎬 Create DaVinci project…",
                      command=lambda: self._create_davinci_project(output_path, win),
                      font=("Segoe UI", 10, "bold"), padx=16, pady=6,
                      bg="#6f42c1", fg="white", activebackground="#5a349a",
                      ).pack(side="left", padx=6)
        tk.Button(row, text="🔍 Re-analyze output", command=reanalyze,
                  font=("Segoe UI", 10, "bold"), padx=16, pady=6,
                  bg="#28a745", fg="white", activebackground="#218838",
                  ).pack(side="left", padx=6)
        tk.Button(row, text="📁 Open folder", command=open_folder,
                  font=("Segoe UI", 10), padx=16, pady=6).pack(side="left", padx=6)
        tk.Button(row, text="Close", command=win.destroy,
                  font=("Segoe UI", 10), padx=16, pady=6).pack(side="left", padx=6)

        win.update_idletasks()
        sw, sh = win.winfo_screenwidth(), win.winfo_screenheight()
        w, h = win.winfo_reqwidth(), win.winfo_reqheight()
        win.geometry(f"+{(sw - w) // 2}+{(sh - h) // 2}")

    def _move_to_next_screen(self):
        self._monitor_idx = (self._monitor_idx + 1) % len(self._monitors)
        self._snap_to_monitor(self._monitor_idx)

    def _drag_start(self, event):
        self._drag_x = event.x
        self._drag_y = event.y

    def _drag_move(self, event):
        x = self.root.winfo_x() + event.x - self._drag_x
        y = self.root.winfo_y() + event.y - self._drag_y
        self.root.geometry(f"+{x}+{y}")

    def update(self, vol: int, locked: bool = False):
        if vol >= 70:
            color = "#2eb85c"   # green
        elif vol >= 40:
            color = "#ffa500"   # orange
        else:
            color = "#dc3545"   # red
        prefix = "🔒" if locked else "🎤"
        self.label.config(text=f"{prefix} {vol}%", fg=color)

    def run(self):
        self.root.mainloop()


# ── Tray icon drawing ─────────────────────────────────────────────────────────

def make_icon_image(volume_pct: int) -> Image.Image:
    size = 64
    img = Image.new("RGBA", (size, size), (0, 0, 0, 0))
    draw = ImageDraw.Draw(img)

    if volume_pct >= 70:
        color = (46, 184, 92)    # green
    elif volume_pct >= 40:
        color = (255, 165, 0)    # orange
    else:
        color = (220, 53, 69)    # red

    draw.ellipse([2, 2, size - 2, size - 2], fill=color)

    text = f"{volume_pct}%"
    font_size = 18 if volume_pct < 100 else 15
    try:
        font = ImageFont.truetype("arial.ttf", font_size)
    except Exception:
        font = ImageFont.load_default()

    bbox = draw.textbbox((0, 0), text, font=font)
    tw = bbox[2] - bbox[0]
    th = bbox[3] - bbox[1]
    draw.text(((size - tw) / 2, (size - th) / 2), text, fill="white", font=font)

    return img


# ── Volume reading ────────────────────────────────────────────────────────────

def get_mic_volume() -> int | None:
    try:
        mic = AudioUtilities.GetMicrophone()
        if mic is None:
            return None
        interface = mic.Activate(IAudioEndpointVolume._iid_, CLSCTX_ALL, None)
        volume = interface.QueryInterface(IAudioEndpointVolume)
        scalar = volume.GetMasterVolumeLevelScalar()
        return round(scalar * 100)
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


# ── YouTube audio analyzer (ffmpeg ebur128) ───────────────────────────────────

def analyze_audio_file(path: str, progress_cb=None) -> dict | None:
    """Run ffmpeg ebur128 and parse the summary block.
    progress_cb(percent, text) is called as ffmpeg processes the file.
    Returns dict with keys: integrated_lufs, true_peak_dbfs, loudness_range,
    duration_sec. Returns None on failure."""
    try:
        proc = subprocess.Popen(
            [FFMPEG, "-hide_banner", "-i", path,
             "-af", "ebur128=peak=true", "-f", "null", "-"],
            stderr=subprocess.PIPE, stdout=subprocess.DEVNULL,
            text=True, bufsize=1,
            creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0,
        )
    except FileNotFoundError:
        return None

    stderr_lines = []
    duration = None
    last_update = 0.0

    try:
        for line in proc.stderr:
            stderr_lines.append(line)
            # Capture duration once (ffmpeg emits this when opening the file)
            if duration is None:
                m = re.search(r"Duration:\s*(\d+):(\d+):(\d+\.?\d*)", line)
                if m:
                    h, mm, s = m.groups()
                    duration = int(h) * 3600 + int(mm) * 60 + float(s)
                    if progress_cb:
                        progress_cb(0, f"Starting… ({int(duration//60)}:{int(duration%60):02d} total)")
            # Parse progress time from ebur128 lines: "t: 12.3  M: -23.4 ..."
            if duration and progress_cb:
                m = re.search(r"\bt:\s*(\d+\.?\d*)", line)
                if m:
                    now = time.time()
                    if now - last_update >= 0.2:   # throttle to 5 Hz
                        t = float(m.group(1))
                        pct = min(99, int(t / duration * 100))
                        progress_cb(pct, f"Analyzing… {pct}%")
                        last_update = now
    except Exception:
        proc.kill()
        return None

    try:
        proc.wait(timeout=30)
    except subprocess.TimeoutExpired:
        proc.kill()
        return None

    if progress_cb:
        progress_cb(100, "Parsing results…")

    out = "".join(stderr_lines)

    # Extract the summary block (comes after "Summary:" line)
    summary_match = re.search(r"Summary:\s*(.*)", out, re.DOTALL)
    if not summary_match:
        return None
    block = summary_match.group(1)

    def find(pattern: str) -> float | None:
        m = re.search(pattern, block)
        return float(m.group(1)) if m else None

    integrated = find(r"I:\s*(-?\d+\.?\d*)\s*LUFS")
    lra = find(r"LRA:\s*(-?\d+\.?\d*)\s*LU")
    true_peak = find(r"Peak:\s*(-?\d+\.?\d*)\s*dBFS")

    # Duration from ffmpeg's input metadata line
    dur_match = re.search(r"Duration:\s*(\d+):(\d+):(\d+\.?\d*)", out)
    duration = None
    if dur_match:
        h, m, s = dur_match.groups()
        duration = int(h) * 3600 + int(m) * 60 + float(s)

    if integrated is None or true_peak is None:
        return None

    return {
        "integrated_lufs": integrated,
        "true_peak_dbfs": true_peak,
        "loudness_range": lra,
        "duration_sec": duration,
    }


def youtube_verdict(lufs: float, true_peak: float) -> tuple[str, str, list[str], str]:
    """Evaluate against YouTube standards.
    Returns (verdict_label, verdict_color, observations, recommendation)."""
    observations = []
    rec_parts = []

    # Loudness (target -14 LUFS, YouTube only attenuates, never boosts)
    lufs_delta = lufs - (-14.0)
    if -15.0 <= lufs <= -13.0:
        observations.append(f"✓ Loudness on target ({lufs:.1f} LUFS)")
    elif lufs > -13.0:
        observations.append(
            f"⚠ Too loud: {lufs:.1f} LUFS (YouTube will reduce by ~{lufs_delta:.1f} dB)"
        )
        rec_parts.append(f"Reduce overall volume by {lufs_delta:.1f} dB")
    else:
        boost_needed = -14.0 - lufs
        observations.append(
            f"⚠ Too quiet: {lufs:.1f} LUFS (will sound {boost_needed:.1f} dB softer than other videos)"
        )
        rec_parts.append(f"Boost overall volume by {boost_needed:.1f} dB")

    # True peak — only clipping (≥ 0) is a problem; the -1 dBTP is a safety margin
    if true_peak >= 0:
        observations.append(f"✗ CLIPPING at {true_peak:.1f} dBTP — audio is distorted")
        rec_parts.append("Reduce gain until peaks stay below 0 dBFS")
    elif true_peak > -1.0:
        observations.append(
            f"✓ Peak {true_peak:.1f} dBTP (under the 0 dB limit, close to the -1 dB margin)"
        )
    else:
        observations.append(f"✓ Peaks safe ({true_peak:.1f} dBTP)")

    # Overall verdict
    if true_peak >= 0:
        return ("❌ DON'T UPLOAD — clipping", "#dc3545", observations,
                " / ".join(rec_parts))
    if -15.0 <= lufs <= -13.0:
        # Loudness on target + no clipping → safe to upload.
        # The -1 dBTP ceiling is a safety margin, not a hard cutoff.
        return ("✅ UPLOAD AS-IS", "#2eb85c", observations, "")
    return ("⚠️ NEEDS ADJUSTMENT", "#ffa500", observations,
            " / ".join(rec_parts))


def normalize_audio_file(input_path: str, output_path: str,
                         progress_cb=None) -> dict | None:
    """Two-pass EBU R128 normalization to -14 LUFS / -1 dBTP.

    progress_cb(stage, text) is called with stage in {'pass1','pass2','done','error'}.
    Returns a dict with measured input/output stats on success, or None on failure.
    """
    import json

    def _notify(stage, text):
        if progress_cb:
            try:
                progress_cb(stage, text)
            except Exception:
                pass

    creation = subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0

    # ── Pass 1: measure ───────────────────────────────────────────────────────
    _notify("pass1", "Pass 1 of 2: Measuring loudness…")
    try:
        p1 = subprocess.run(
            [FFMPEG, "-hide_banner", "-nostats", "-i", input_path,
             "-af", "loudnorm=I=-14:TP=-1:LRA=11:print_format=json",
             "-f", "null", "-"],
            capture_output=True, text=True, timeout=1800,
            creationflags=creation,
        )
    except (FileNotFoundError, subprocess.TimeoutExpired):
        _notify("error", "ffmpeg failed during pass 1")
        return None

    # loudnorm prints a JSON block near the end of stderr
    err = p1.stderr or ""
    # Find the last {...} block (the JSON output)
    json_match = re.search(r"\{[^{}]*\"input_i\"[^{}]*\}", err, re.DOTALL)
    if not json_match:
        _notify("error", "Could not parse loudnorm measurement")
        return None
    try:
        measured = json.loads(json_match.group(0))
    except json.JSONDecodeError:
        _notify("error", "Invalid JSON from loudnorm")
        return None

    required = ("input_i", "input_tp", "input_lra", "input_thresh", "target_offset")
    if not all(k in measured for k in required):
        _notify("error", "Incomplete loudnorm measurement")
        return None

    # ── Pass 2: apply ─────────────────────────────────────────────────────────
    _notify("pass2", "Pass 2 of 2: Applying gain…")
    af = (
        "loudnorm=I=-14:TP=-1:LRA=11"
        f":measured_I={measured['input_i']}"
        f":measured_TP={measured['input_tp']}"
        f":measured_LRA={measured['input_lra']}"
        f":measured_thresh={measured['input_thresh']}"
        f":offset={measured['target_offset']}"
        ":print_format=summary"
    )

    # Detect if input has a video stream — if so, copy it through
    # (avoids re-encoding video, keeps original quality + saves time)
    ext = os.path.splitext(input_path)[1].lower()
    is_video = ext in {".mp4", ".mov", ".mkv", ".avi", ".m4v", ".webm"}

    cmd = [FFMPEG, "-y", "-hide_banner", "-nostats", "-i", input_path,
           "-af", af]
    if is_video:
        cmd += ["-c:v", "copy", "-c:a", "aac", "-b:a", "192k"]
    # For audio-only, let ffmpeg pick the encoder from the output extension
    cmd += [output_path]

    try:
        p2 = subprocess.run(
            cmd, capture_output=True, text=True, timeout=3600,
            creationflags=creation,
        )
    except (FileNotFoundError, subprocess.TimeoutExpired):
        _notify("error", "ffmpeg failed during pass 2")
        return None

    if p2.returncode != 0:
        _notify("error", f"ffmpeg exited with code {p2.returncode}")
        return None

    _notify("done", "Normalization complete")

    return {
        "input_i": float(measured["input_i"]),
        "input_tp": float(measured["input_tp"]),
        "input_lra": float(measured["input_lra"]),
        "target_offset": float(measured["target_offset"]),
        "output_path": output_path,
    }


# ── Process snapshot ──────────────────────────────────────────────────────────

def snapshot_processes():
    procs = set()
    for p in psutil.process_iter(["name"]):
        try:
            procs.add(p.info["name"].lower())
        except Exception:
            pass
    audio_procs = sorted(p for p in procs if p in KNOWN_AUDIO_APPS)
    all_procs = sorted(procs)
    return audio_procs, all_procs


def snapshot_active_mic_sessions():
    """Return (active, all) process names with audio sessions on the microphone.
    'active' = state 1 (currently streaming). This is the definitive list of
    apps using the mic at the moment of call."""
    active = set()
    all_sessions = set()
    try:
        mic = AudioUtilities.GetMicrophone()
        if mic is None:
            return [], []
        iface = mic.Activate(IAudioSessionManager2._iid_, CLSCTX_ALL, None)
        mgr = iface.QueryInterface(IAudioSessionManager2)
        enumerator = mgr.GetSessionEnumerator()
        count = enumerator.GetCount()
        for i in range(count):
            try:
                ctl = enumerator.GetSession(i)
                ctl2 = ctl.QueryInterface(IAudioSessionControl2)
                session = AudioSession(ctl2)
                proc = session.Process
                name = proc.name().lower() if proc else "system"
                all_sessions.add(name)
                if session.State == 1:
                    active.add(name)
            except Exception:
                pass
    except Exception:
        pass
    return sorted(active), sorted(all_sessions)


# ── Logging ───────────────────────────────────────────────────────────────────

def log_change(old_vol: int, new_vol: int, audio_procs: list, all_procs: list,
               active_mic: list, all_mic: list):
    delta = new_vol - old_vol
    sign = "+" if delta > 0 else ""
    timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    audio_str = ", ".join(audio_procs) if audio_procs else "none detected"
    all_str = ", ".join(all_procs)
    active_mic_str = ", ".join(active_mic) if active_mic else "none"
    all_mic_str = ", ".join(all_mic) if all_mic else "none"

    line = (
        f"[{timestamp}] {old_vol}% → {new_vol}%  ({sign}{delta}%)\n"
        f"  >>> ACTIVE mic sessions: {active_mic_str}\n"
        f"  All mic sessions: {all_mic_str}\n"
        f"  Audio apps running: {audio_str}\n"
        f"  All processes: {all_str}\n"
        f"{'─' * 60}\n"
    )

    with open(LOG_FILE, "a", encoding="utf-8") as f:
        f.write(line)

    print(line, end="")


# ── Monitoring thread ─────────────────────────────────────────────────────────

class VolumeMonitor:
    def __init__(self, overlay: VolumeOverlay):
        self.icon = None
        self.overlay = overlay
        self._stop = threading.Event()

        # Lock state: persisted across runs. First-time default = locked at 90%.
        settings = load_settings()
        self.locked = bool(settings.get("mic_locked", True))
        self.target_vol = int(settings.get("mic_lock_target", 90))

        # If locked at startup, snap the mic to the target before reading it
        # so last_vol reflects the locked value (not whatever Windows had).
        if self.locked:
            set_mic_volume(self.target_vol)
        self.last_vol = get_mic_volume()

    def _persist_lock(self):
        settings = load_settings()
        settings["mic_locked"] = self.locked
        settings["mic_lock_target"] = self.target_vol
        save_settings(settings)

    def start(self):
        t = threading.Thread(target=self._loop, daemon=True)
        t.start()

    def enable_lock(self, target: int | None = None):
        if target is not None:
            self.target_vol = target
        elif self.last_vol is not None:
            self.target_vol = self.last_vol
        self.locked = True
        # Snap to target immediately so the lock takes effect now
        set_mic_volume(self.target_vol)
        self.last_vol = self.target_vol
        self.overlay.root.after(0, self.overlay.update, self.target_vol, True)
        self._persist_lock()
        self._rebuild_menu()

    def disable_lock(self):
        self.locked = False
        current = get_mic_volume()
        if current is not None:
            self.overlay.root.after(0, self.overlay.update, current, False)
        self._persist_lock()
        self._rebuild_menu()

    def _rebuild_menu(self):
        if self.icon is not None:
            try:
                self.icon.menu = build_menu(self)
                self.icon.update_menu()
            except Exception:
                pass

    def _loop(self):
        while not self._stop.is_set():
            time.sleep(POLL_INTERVAL)
            current = get_mic_volume()
            if current is None:
                continue
            if self.last_vol is not None and abs(current - self.last_vol) >= CHANGE_THRESHOLD:
                old = self.last_vol
                audio_procs, all_procs = snapshot_processes()
                active_mic, all_mic = snapshot_active_mic_sessions()
                log_change(old, current, audio_procs, all_procs, active_mic, all_mic)
                self._notify(old, current, active_mic or audio_procs)
                self._update_icon(current)
                # If locked, restore to target on any change that isn't us
                if self.locked and current != self.target_vol:
                    if set_mic_volume(self.target_vol):
                        current = self.target_vol
            if current != self.last_vol:
                self.overlay.root.after(0, self.overlay.update, current, self.locked)
            self.last_vol = current

    def _notify(self, old: int, new: int, audio_procs: list):
        if self.icon is None:
            return
        delta = new - old
        sign = "+" if delta > 0 else ""
        active = ", ".join(audio_procs[:3]) if audio_procs else "unknown"
        try:
            self.icon.notify(
                f"{old}% → {new}%  ({sign}{delta}%)\nActive: {active}",
                "🎤 Mic volume changed"
            )
        except Exception:
            pass

    def _update_icon(self, vol: int):
        if self.icon is None:
            return
        try:
            self.icon.icon = make_icon_image(vol)
            self.icon.title = f"Mic: {vol}%"
        except Exception:
            pass


# ── Tray setup ────────────────────────────────────────────────────────────────

def open_log(icon, item):
    subprocess.Popen(["notepad.exe", LOG_FILE])


def build_menu(monitor: VolumeMonitor):
    def current_vol_label(item):
        vol = monitor.last_vol
        return f"Current volume: {vol}%" if vol is not None else "Current volume: unknown"

    def lock_label(item):
        if monitor.locked:
            return f"🔓 Unlock (locked at {monitor.target_vol}%)"
        cur = monitor.last_vol if monitor.last_vol is not None else 80
        return f"🔒 Lock at current volume ({cur}%)"

    def toggle_lock(_icon, _item):
        if monitor.locked:
            monitor.disable_lock()
        else:
            monitor.enable_lock()

    return pystray.Menu(
        pystray.MenuItem(current_vol_label, None, enabled=False),
        pystray.Menu.SEPARATOR,
        pystray.MenuItem(lock_label, toggle_lock),
        pystray.Menu.SEPARATOR,
        pystray.MenuItem("Open Log File", open_log),
        pystray.MenuItem("Exit", lambda icon, item: icon.stop()),
    )


def main():
    # Write log header
    with open(LOG_FILE, "a", encoding="utf-8") as f:
        f.write(f"\n{'=' * 60}\n")
        f.write(f"Session started: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}\n")
        f.write(f"{'=' * 60}\n")

    # Overlay runs in its own thread (tkinter must stay on its creation thread)
    overlay_ready = threading.Event()
    overlay_ref = [None]

    def run_overlay():
        overlay_ref[0] = VolumeOverlay()
        overlay_ready.set()
        overlay_ref[0].run()

    overlay_thread = threading.Thread(target=run_overlay, daemon=True)
    overlay_thread.start()
    overlay_ready.wait()
    overlay = overlay_ref[0]

    monitor = VolumeMonitor(overlay)
    overlay.monitor = monitor
    initial_vol = monitor.last_vol if monitor.last_vol is not None else 0
    overlay.root.after(0, overlay.update, initial_vol, monitor.locked)

    print(f"🎬 Studio Flow started. Current mic volume: {initial_vol}%")
    print(f"📄 Logging changes to: {LOG_FILE}")
    print("Watching for changes... (right-click tray icon to exit)\n")

    icon_image = make_icon_image(initial_vol)
    icon = pystray.Icon(
        "studio_flow",
        icon_image,
        f"Mic: {initial_vol}%",
        menu=build_menu(monitor),
    )
    monitor.icon = icon
    monitor.start()
    icon.run()  # pystray runs on the main thread (required on Windows)


if __name__ == "__main__":
    main()
