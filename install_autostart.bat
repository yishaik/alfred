@echo off
REM Double-click this to make the bridge start at boot with no login.
REM A UAC prompt will appear (registering a pre-login task needs admin) - click Yes.
powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0install_autostart.ps1"
