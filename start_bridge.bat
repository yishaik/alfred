@echo off
REM Launch the Telegram<->Claude bridge. supervisor.py owns restarts,
REM crash-loop backoff and bridge.log rotation; this outer loop only
REM survives the (unlikely) death of the supervisor itself.
REM Uses the dedicated uv venv (a real, accessible Python) rather than the
REM Microsoft Store Python, so it also works from a no-login scheduled task.
set PYTHONUTF8=1
cd /d "D:\Projects\telegram-claude-bridge"
set "PY=D:\Projects\telegram-claude-bridge\.venv\Scripts\python.exe"
if not exist "%PY%" set "PY=python"
:loop
"%PY%" -u supervisor.py
timeout /t 30 /nobreak >nul
goto loop
