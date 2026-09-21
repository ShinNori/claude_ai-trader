@echo off
rem Double-click to start the Codex-side watcher (auto + dev channels) in a visible window.
rem Stop it with Ctrl+C or by closing the window. For logon auto-start use register_watch_task.cmd instead.
cd /d "%~dp0..\.."
powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0watch_handoff.ps1" -Agent codex -DryRun
echo.
echo ---- DryRun above. Starting the resident watcher now (Ctrl+C to stop) ----
powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0watch_handoff.ps1" -Agent codex
pause
