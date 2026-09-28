# build-claude を他 PC で動かすためのセットアップ（Windows PowerShell 5.1 以上）
#   powershell -ExecutionPolicy Bypass -File setup.ps1 [-Home D:\ai-trader-home] [-Cash 3000000] [-SkipTests]
param(
  [string]$Home = $(if ($env:AI_TRADER_HOME) { $env:AI_TRADER_HOME } else { Join-Path $env:LOCALAPPDATA 'ai-trader-claude' }),
  [int]$Cash = 3000000,
  [switch]$SkipTests
)
$ErrorActionPreference = 'Stop'
Set-Location $PSScriptRoot
$py = Get-Command python -ErrorAction SilentlyContinue
if (-not $py) { Write-Error 'python が見つかりません。Python 3.11 以上を https://www.python.org/ から入れて PATH に追加してください。'; exit 1 }
$ver = & python -c "import sys; print(sys.version_info >= (3, 11))"
if ($ver -ne 'True') { Write-Error 'Python 3.11 以上が必要です。'; exit 1 }

if (-not (Test-Path .venv)) { python -m venv .venv }
& .\.venv\Scripts\python.exe -m pip install --upgrade pip -q
& .\.venv\Scripts\python.exe -m pip install -r requirements.txt -q
if (-not (Test-Path .env)) { Copy-Item .env.example .env }

$env:AI_TRADER_HOME = $Home
New-Item -ItemType Directory -Force -Path $Home | Out-Null
Write-Host "AI_TRADER_HOME = $Home"

if (-not $SkipTests) {
  & .\.venv\Scripts\python.exe -m pytest tests -q
  if ($LASTEXITCODE -ne 0) { Write-Error 'テストが失敗しました'; exit 1 }
}
& .\.venv\Scripts\python.exe smoke.py --cash $Cash
if ($LASTEXITCODE -ne 0) { exit 1 }
Write-Host ''
Write-Host '完了。毎回の起動は:'
Write-Host "  .\.venv\Scripts\Activate.ps1 ; `$env:AI_TRADER_HOME = '$Home'"
Write-Host '  python -m aitrader daily --strategy margin_bucket_long --as-of 2025-06-06 --events events.json'
