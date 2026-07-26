@echo off
REM Supervised launcher for the railway watcher sidecar.
REM It polls GitHub for [railway]-labelled issues and drops prompt files into
REM state\webhook_inbox. Previously it ran bare under pythonw: a hard crash left
REM it dead with no log and no restart. This loop restarts it after 30s.
cd /d "%~dp0"
:loop
".venv\Scripts\python.exe" -u railway_watch.py >> "state\railway_watch.out" 2>&1
timeout /t 30 /nobreak >nul
goto loop
