@echo off
rem Creator Studio — dev launcher (home PC). Opens the windowed app.
cd /d "%~dp0"
py -3.10 -m pip install -r requirements.txt --quiet
py -3.10 tracker.py
