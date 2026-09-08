# Claude Code → Codex への自動引き継ぎ（Windows / PowerShell）
# 使い方:  powershell -ExecutionPolicy Bypass -File tools\run_codex_build.ps1 [-Task "追加の指示"]
# 前提:   npm install -g @openai/codex 済み、codex login 済み（ChatGPT アカウント）
param(
  [string]$Task = "",
  [string]$Model = ""
)
$ErrorActionPreference = "Stop"
$root = Split-Path -Parent (Split-Path -Parent $MyInvocation.MyCommand.Path)   # ai-trader/
Set-Location $root
if (-not (Get-Command codex -ErrorAction SilentlyContinue)) { throw "codex が見つかりません。npm install -g @openai/codex を実行してください" }

$prompt = @"
Codex引き渡しプロンプト.md を読み、「A. 固定プロンプト」の規則と「B. 今回の依頼」に従って作業してください。
完了条件は「B. 今回の依頼」に従う。
common/ と build-claude/ は編集禁止。質問があれば build-codex/QUESTIONS.md に書いて止めてください。
$Task
"@

New-Item -ItemType Directory -Force -Path "$root\logs" | Out-Null
$stamp = Get-Date -Format "yyyyMMdd_HHmmss"
$log = "$root\logs\codex_$stamp.log"
$last = "$root\logs\codex_${stamp}_last.md"

$args = @("exec", "--sandbox", "workspace-write", "--skip-git-repo-check", "-C", "$root", "--output-last-message", $last)
if ($Model) { $args += @("-m", $Model) }
$args += $prompt

Write-Host "[handoff] codex $($args -join ' ')"
& codex @args 2>&1 | Tee-Object -FilePath $log
Write-Host "[handoff] 終了。ログ: $log / 最終メッセージ: $last"
if (Test-Path "$root\build-codex\QUESTIONS.md") { Write-Host "[handoff] Codex からの質問があります: build-codex\QUESTIONS.md" }
