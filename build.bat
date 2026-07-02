@echo off
setlocal

set APP=CreatorStudio
set INNO="C:\Program Files (x86)\Inno Setup 6\ISCC.exe"

echo.
echo ============================================================
echo  Building %APP%
echo ============================================================
echo.

echo [1/3] Cleaning previous build...
if exist dist\%APP% rmdir /s /q dist\%APP%
if exist build rmdir /s /q build

echo [2/3] Bundling with PyInstaller...
py -3.10 -m PyInstaller mic_tracker.spec --clean --noconfirm
if errorlevel 1 (
    echo.
    echo ERROR: PyInstaller failed. See output above.
    pause
    exit /b 1
)
echo PyInstaller done. Output: dist\%APP%\

echo [3/3] Building installer with Inno Setup...
if not exist %INNO% (
    echo WARNING: Inno Setup not found at %INNO%
    echo Skipping installer step. PyInstaller output is in dist\%APP%\
    goto done
)
mkdir dist\installer 2>nul
%INNO% installer.iss
if errorlevel 1 (
    echo.
    echo ERROR: Inno Setup failed. See output above.
    pause
    exit /b 1
)

:done
echo.
echo ============================================================
echo  Build complete!
echo.
echo  Installer: dist\installer\%APP%-Setup-2.0.0.exe
echo  Raw build:  dist\%APP%\%APP%.exe
echo ============================================================
echo.
pause
