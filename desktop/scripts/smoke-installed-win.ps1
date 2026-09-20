$ErrorActionPreference = 'Stop'
$desktopRoot = Split-Path $PSScriptRoot -Parent
$installer = Get-ChildItem (Join-Path $desktopRoot 'dist') -Filter '*Setup*.exe' | Select-Object -First 1
if (!$installer) { throw 'NSIS installer not found' }
$installRoot = Join-Path $env:RUNNER_TEMP 'ExcelManus 安装 空格'
$profileRoot = Join-Path $env:RUNNER_TEMP 'ExcelManus 数据 空格'
$exe = Join-Path $installRoot 'ExcelManus.exe'
$nodeExe = (Get-Command node.exe).Source
$smoke = Join-Path $PSScriptRoot 'smoke-app.mjs'
function Install-App {
    # NSIS requires /D last and its value unquoted (the rest of the command line).
    $process = Start-Process -FilePath $installer.FullName -ArgumentList "/S /D=$installRoot" -Wait -PassThru
    if ($process.ExitCode -ne 0) { throw "Installer exited $($process.ExitCode)" }
    if (!(Test-Path $exe)) { throw 'Installed EXE missing' }
}
function Check-App {
    & $nodeExe $smoke $exe $profileRoot
    if ($LASTEXITCODE -ne 0) { throw "Installed App smoke failed: $LASTEXITCODE" }
}
Install-App
try {
    Check-App
    Install-App
    if ((Get-Content (Join-Path $profileRoot 'smoke-profile-marker') -Raw) -ne 'keep') { throw 'Upgrade lost profile marker' }
    Check-App
} finally {
    $uninstaller = Join-Path $installRoot 'Uninstall ExcelManus.exe'
    if (Test-Path $uninstaller) {
        # Run the real uninstaller in place, then wait for its own process.
        $process = Start-Process -FilePath $uninstaller -ArgumentList "/S _?=$installRoot" -Wait -PassThru
        if ($process.ExitCode -ne 0) { throw "Uninstall exited $($process.ExitCode)" }
    }
}
if (Test-Path $exe) { throw 'Application EXE remains after uninstall' }
if (!(Test-Path (Join-Path $profileRoot 'excelmanus.db'))) { throw 'Uninstall lost profile database' }
Write-Host 'WINDOWS_INSTALL_UPGRADE_UNINSTALL_OK'
