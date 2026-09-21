$ErrorActionPreference = 'Stop'
$project = [IO.Path]::GetFullPath((Join-Path $PSScriptRoot '../..'))
$target = [IO.Path]::GetFullPath((Join-Path $project 'common/tests/phase2'))
$expected = 'D:\Desktop\Ace-1_Dropbox\Ace-1 Dropbox\Norimitsu Shin\!!!※則光用\999_投資関係\ai-trader\common\tests\phase2'
if ($target -ne $expected -or (Get-Item -LiteralPath $target).LinkType) { throw 'Unexpected ACL target' }
$runtime = 'C:\Users\s\.ai-trader\automation-handoff\13e7542ead2d199e'
$resultPath = Join-Path $runtime 'phase2-acl-repair-result.json'
try {
    $before = (Get-Acl -LiteralPath $target).Sddl
    $backup = Join-Path $runtime ('phase2-acl-admin-before-'+(Get-Date -Format 'yyyyMMdd_HHmmss')+'.txt')
    [IO.File]::WriteAllText($backup,$before)
    $output = & "$env:SystemRoot\System32\icacls.exe" $target /inheritance:e 2>&1
    if ($LASTEXITCODE -ne 0) { throw "icacls failed: $output" }
    $after = (Get-Acl -LiteralPath $target).Sddl
    [pscustomobject]@{ok=$true;target=$target;backup=$backup;before=$before;after=$after;at=(Get-Date -Format o)} | ConvertTo-Json | Set-Content -LiteralPath $resultPath -Encoding UTF8
} catch {
    [pscustomobject]@{ok=$false;error=$_.ToString();at=(Get-Date -Format o)} | ConvertTo-Json | Set-Content -LiteralPath $resultPath -Encoding UTF8
    exit 1
}
