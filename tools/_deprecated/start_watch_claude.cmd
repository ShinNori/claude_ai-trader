@echo off
rem Start the Claude-side watcher on this PC. Do NOT run while Cowork is acting as the Claude side.
chcp 65001>nul
cd /d "%~dp0.."
powershell -NoExit -ExecutionPolicy Bypass -File "%~dp0watch_handoff.ps1" -Agent claude %*
