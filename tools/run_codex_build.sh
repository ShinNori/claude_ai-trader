#!/usr/bin/env bash
# Claude Code → Codex への自動引き継ぎ（Linux/macOS）
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$ROOT"
command -v codex >/dev/null || { echo "codex が見つかりません (npm install -g @openai/codex)"; exit 1; }
TASK="${1:-}"
PROMPT="Codex引き渡しプロンプト.md を読み、「A. 固定プロンプト」の規則と「B. 今回の依頼」に従って作業してください。
完了条件は「B. 今回の依頼」に従う。
common/ と build-claude/ は編集禁止。質問があれば build-codex/QUESTIONS.md に書いて止めてください。
$TASK"
mkdir -p logs
STAMP="$(date +%Y%m%d_%H%M%S)"
codex exec --sandbox workspace-write --skip-git-repo-check -C "$ROOT" --output-last-message "logs/codex_${STAMP}_last.md" "$PROMPT" < /dev/null 2>&1 | tee "logs/codex_${STAMP}.log"
[ -f build-codex/QUESTIONS.md ] && echo "[handoff] Codex からの質問: build-codex/QUESTIONS.md" || true
