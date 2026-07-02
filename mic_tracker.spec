# -*- mode: python ; coding: utf-8 -*-
# PyInstaller spec for Creator Studio (windowed pywebview app).

from pathlib import Path
from PyInstaller.utils.hooks import collect_all

ROOT = Path(SPECPATH)

# pywebview (EdgeChromium/WebView2 via pythonnet) needs its lib DLLs + the
# .NET bridge collected explicitly, or the window fails to open when frozen.
web_datas, web_bins, web_hidden = collect_all('webview')
clr_datas, clr_bins, clr_hidden = collect_all('clr_loader')
pn_datas, pn_bins, pn_hidden = collect_all('pythonnet')

a = Analysis(
    [str(ROOT / 'tracker.py')],
    pathex=[str(ROOT)],
    binaries=[*web_bins, *clr_bins, *pn_bins],
    datas=[
        (str(ROOT / 'vendor' / 'ffmpeg.exe'), '.'),   # ffmpeg → _internal/
        (str(ROOT / 'assets' / 'icon.ico'), 'assets'),
        (str(ROOT / 'web'), 'web'),                    # the UI
        (str(ROOT / 'laptop-setup'), 'laptop-setup'),  # Tailscale drive mapper
        *web_datas, *clr_datas, *pn_datas,
    ],
    hiddenimports=[
        # our backend modules (some imported lazily inside functions)
        'settings_store', 'mediatools', 'osmo_import', 'jobs',
        'mic', 'audio_tools', 'davinci', 'server',
        # web + gui + audio stack
        'flask', 'werkzeug', 'jinja2', 'clr',
        'pycaw', 'pycaw.pycaw', 'comtypes', 'comtypes.client',
        'pystray', 'pystray._win32', 'pystray.backend.win32',
        'PIL', 'PIL.Image', 'PIL.ImageDraw', 'psutil',
        *web_hidden, *clr_hidden, *pn_hidden,
    ],
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=['matplotlib', 'numpy', 'scipy', 'pandas', 'IPython',
              'notebook', 'sphinx', 'tkinter'],
    noarchive=False,
    optimize=1,
)

pyz = PYZ(a.pure)

exe = EXE(
    pyz, a.scripts, [],
    exclude_binaries=True,
    name='CreatorStudio',
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,                 # UPX can corrupt the WebView2 / .NET DLLs
    console=False,             # windowed app — no console
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
    icon=str(ROOT / 'assets' / 'icon.ico'),
)

coll = COLLECT(
    exe, a.binaries, a.datas,
    strip=False, upx=False,
    name='CreatorStudio',
)
