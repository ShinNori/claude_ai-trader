@echo off
rem Double-click to register the Codex-side watcher in Task Scheduler (starts now and at every logon, hidden).
rem First registration only. To update an existing task run: register_watch_task.ps1 -Replace (stop the task first).
cd /d "%~dp0..\.."
powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0register_watch_task.ps1"
pause
