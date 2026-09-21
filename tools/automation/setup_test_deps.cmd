@echo off
rem Double-click: install the Python test dependencies into <ai-trader>\.test-deps (readable by the sandboxed Codex/Claude CLI).
rem Uses the Codex-bundled Python 3.12 when found, else python.exe on PATH. Needs network (run from a normal window, not the sandbox).
cd /d "%~dp0..\.."
set "PY=%USERPROFILE%\.cache\codex-runtimes\codex-primary-runtime\dependencies\python\python.exe"
if not exist "%PY%" set "PY=python"
echo Using: %PY%
"%PY%" -m pip install --upgrade --target ".test-deps" pytest duckdb pyyaml requests pandas numpy
if errorlevel 1 ( echo. & echo INSTALL FAILED & pause & exit /b 1 )
set "PYTHONPATH=%CD%\.test-deps"
"%PY%" -c "import pytest,duckdb,yaml,requests,pandas,numpy;print('deps OK: pytest',pytest.__version__)"
echo.
echo Done. Answer build-codex\QUESTIONS.md (3rd block), add the resolved marker, then run handoff_status.ps1 -Resume and start both tasks.
pause
