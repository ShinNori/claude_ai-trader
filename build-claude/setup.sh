#!/usr/bin/env bash
# build-claude を他 PC で動かすためのセットアップ（Linux / macOS）
#   bash setup.sh [--home ~/ai-trader-home] [--cash 3000000] [--skip-tests]
set -euo pipefail
cd "$(dirname "$0")"
HOME_DIR="${AI_TRADER_HOME:-$HOME/.ai-trader-claude}"; CASH=3000000; SKIP=0
while [ $# -gt 0 ]; do case "$1" in
  --home) HOME_DIR="$2"; shift 2;; --cash) CASH="$2"; shift 2;; --skip-tests) SKIP=1; shift;; *) echo "unknown arg $1"; exit 2;; esac; done
PY=python3; command -v $PY >/dev/null || PY=python
$PY -c 'import sys; sys.exit(0 if sys.version_info >= (3, 11) else 1)' || { echo "Python 3.11 以上が必要です"; exit 1; }
[ -d .venv ] || $PY -m venv .venv
.venv/bin/python -m pip install --upgrade pip -q
.venv/bin/python -m pip install -r requirements.txt -q
[ -f .env ] || cp .env.example .env
export AI_TRADER_HOME="$HOME_DIR"; mkdir -p "$HOME_DIR"; echo "AI_TRADER_HOME = $HOME_DIR"
[ "$SKIP" = 1 ] || .venv/bin/python -m pytest tests -q
.venv/bin/python smoke.py --cash "$CASH"
echo; echo "完了。毎回の起動は:"; echo "  source .venv/bin/activate; export AI_TRADER_HOME=$HOME_DIR"
echo "  python -m aitrader daily --strategy margin_bucket_long --as-of 2025-06-06 --events events.json"
