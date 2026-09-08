@echo off
rem Start the Codex-side watcher (dev + auto channels). Keep this window open.
chcp 65001>nul
cd /d "%~dp0.."
powershell -NoExit -ExecutionPolicy Bypass -File "%~dp0watch_handoff.ps1" -Agent codex %*
