@echo off
rem Makes the desktop and Start menu shortcuts for the desktop program (app.py); see create_shortcut.ps1.
powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0create_shortcut.ps1"
pause
