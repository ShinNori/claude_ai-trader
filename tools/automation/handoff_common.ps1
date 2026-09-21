function Resolve-HandoffPython {
    param([string]$PythonExe)
    if (-not $PythonExe) { $PythonExe = $env:AI_TRADER_PYTHON }
    if (-not $PythonExe) {
        $candidate = Get-Command python.exe -ErrorAction SilentlyContinue
        if ($candidate -and $candidate.Source -notmatch 'WindowsApps') { $PythonExe = $candidate.Source }
    }
    if (-not $PythonExe) {
        $candidate = Join-Path $env:USERPROFILE '.cache\codex-runtimes\codex-primary-runtime\dependencies\python\python.exe'
        if (Test-Path -LiteralPath $candidate) { $PythonExe = $candidate }
    }
    if (-not $PythonExe -or -not (Test-Path -LiteralPath $PythonExe)) { throw 'Set -PythonExe or AI_TRADER_PYTHON to Python 3.10+.' }
    if ([IO.Path]::GetExtension($PythonExe) -ne '.exe') { throw 'PythonExe must be a native .exe.' }
    return (Resolve-Path -LiteralPath $PythonExe).Path
}

# The engine emits UTF-8 JSON. Windows PowerShell 5.1 decodes native
# process output using Console.OutputEncoding, including registration previews.
[Console]::OutputEncoding = [Text.UTF8Encoding]::new($false)

function Invoke-HandoffEngine {
    param([string[]]$EngineArgs, [string]$PythonExe)
    $PythonExe = Resolve-HandoffPython -PythonExe $PythonExe
    $env:PYTHONUTF8 = '1'
    $env:PYTHONDONTWRITEBYTECODE = '1'
    & $PythonExe (Join-Path $PSScriptRoot 'codex_engine.py') @EngineArgs
    if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }
}
