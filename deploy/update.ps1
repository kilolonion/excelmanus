#Requires -Version 5.1
<#
.SYNOPSIS
    ExcelManus update helper for Windows PowerShell.

.DESCRIPTION
    Thin wrapper around python -m excelmanus.upgrade.
    Stops only when the API is already down. Does not git reset --hard on conflict.

    Usage:
      .\deploy\update.ps1
      .\deploy\update.ps1 -CheckOnly
      .\deploy\update.ps1 -Yes -Mirror
      .\deploy\update.ps1 -Rollback
      .\deploy\update.ps1 -ListBackups
#>

[CmdletBinding()]
param(
    [switch]$CheckOnly,
    [switch]$SkipBackup,
    [switch]$SkipDeps,
    [switch]$Mirror,
    [switch]$Force,
    [switch]$Rollback,
    [switch]$ListBackups,
    [switch]$Yes
)

$ErrorActionPreference = "Stop"

$Script:SCRIPT_DIR = Split-Path -Parent $MyInvocation.MyCommand.Path
$Script:PROJECT_ROOT = Split-Path -Parent $Script:SCRIPT_DIR

function Get-PythonBin {
    $candidates = @(
        (Join-Path $Script:PROJECT_ROOT ".venv\Scripts\python.exe"),
        (Join-Path $Script:PROJECT_ROOT ".venv\bin\python.exe"),
        (Join-Path $Script:PROJECT_ROOT ".venv\bin\python")
    )
    foreach ($c in $candidates) {
        if (Test-Path $c) { return $c }
    }
    return "python"
}

if ($Force) {
    Write-Host "[XX] -Force 已取消：不再支持 git reset --hard 覆盖冲突。" -ForegroundColor Red
    exit 1
}

$py = Get-PythonBin
$argsList = @("-m", "excelmanus.upgrade", "--project-root", $Script:PROJECT_ROOT)

if ($ListBackups) {
    & $py @argsList --list-backups
    exit $LASTEXITCODE
}

if ($CheckOnly) {
    & $py @argsList --check
    exit $LASTEXITCODE
}

if ($Rollback) {
    $latest = & $py @argsList --list-backups | Select-Object -First 1
    $name = ($latest -split '\s+')[0]
    if (-not $name -or $name -eq "暂无备份") {
        Write-Host "[XX] 未找到任何备份" -ForegroundColor Red
        exit 1
    }
    & $py @argsList --restore $name
    exit $LASTEXITCODE
}

$argsList += "--offline"
if ($SkipBackup) { $argsList += "--skip-backup" }
if ($SkipDeps) { $argsList += "--skip-deps" }
if ($Mirror) { $argsList += "--mirror" }
if ($Yes) { $argsList += "-y" }

& $py @argsList
exit $LASTEXITCODE
