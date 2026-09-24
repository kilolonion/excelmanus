$ErrorActionPreference = 'Stop'
$desktopRoot = Split-Path $PSScriptRoot -Parent
$version = (Get-Content (Join-Path $desktopRoot 'package.json') -Raw | ConvertFrom-Json).version
$installer = Get-Item (Join-Path $desktopRoot "dist/ExcelManus Setup $version.exe")
$installRoot = Join-Path $env:RUNNER_TEMP 'ExcelManus 安装 空格'
$profileRoot = Join-Path $env:RUNNER_TEMP 'ExcelManus 数据 空格'
$exe = Join-Path $installRoot 'ExcelManus.exe'
$nodeExe = (Get-Command node.exe).Source
$smoke = Join-Path $PSScriptRoot 'smoke-app.mjs'
$timings = [System.Collections.Generic.List[object]]::new()
$timingReport = Join-Path $desktopRoot 'dist/install-timings.json'
function Record-Timing($Stage, $Timer, $ExitCode) {
    $Timer.Stop()
    $timings.Add([ordered]@{ stage = $Stage; ms = $Timer.ElapsedMilliseconds; exitCode = $ExitCode })
    [ordered]@{
        schema = 1
        generatedAt = [DateTime]::UtcNow.ToString('o')
        os = [Environment]::OSVersion.VersionString
        installer = $installer.Name
        installerBytes = $installer.Length
        stages = @($timings.ToArray())
    } | ConvertTo-Json -Depth 5 | Set-Content -Path $timingReport -Encoding utf8
    Write-Host "$Stage took $($Timer.ElapsedMilliseconds) ms (exit $ExitCode)"
}
function Install-App($Stage) {
    # NSIS requires /D last and its value unquoted (the rest of the command line).
    $timer = [Diagnostics.Stopwatch]::StartNew()
    $exitCode = -1
    try {
        $process = Start-Process -FilePath $installer.FullName -ArgumentList "/S /D=$installRoot" -Wait -PassThru
        $exitCode = $process.ExitCode
    } finally { Record-Timing $Stage $timer $exitCode }
    if ($process.ExitCode -ne 0) { throw "Installer exited $($process.ExitCode)" }
    if (!(Test-Path $exe)) { throw 'Installed EXE missing' }
}
function Check-App {
    & $nodeExe (Join-Path $PSScriptRoot 'verify-installed.mjs') (Join-Path $desktopRoot 'dist/win-unpacked') $installRoot
    if ($LASTEXITCODE -ne 0) { throw "Installed payload byte comparison failed: $LASTEXITCODE" }
    # smoke-app invokes smoke.mjs on these installed resources before testing
    # the real Electron supervisor, so do not run that expensive probe twice.
    & $nodeExe $smoke $exe $profileRoot
    if ($LASTEXITCODE -ne 0) { throw "Installed App smoke failed: $LASTEXITCODE" }
}
Install-App 'clean-install'
try {
    Check-App
    # Force the preserved-directory merge path during a real upgrade.
    $userFile = Join-Path $installRoot 'resources/用户保留文件.txt'
    Set-Content -Path $userFile -Value 'keep' -NoNewline -Encoding utf8
    Install-App 'upgrade-with-user-files'
    if ((Get-Content $userFile -Raw) -ne 'keep') { throw 'Upgrade lost user file' }
    if ((Get-Content (Join-Path $profileRoot 'smoke-profile-marker') -Raw) -ne 'keep') { throw 'Upgrade lost profile marker' }
    Check-App
} finally {
    $uninstaller = Join-Path $installRoot 'Uninstall ExcelManus.exe'
    if (Test-Path $uninstaller) {
        # Run the real uninstaller in place, then wait for its own process.
        $timer = [Diagnostics.Stopwatch]::StartNew()
        $exitCode = -1
        try {
            $process = Start-Process -FilePath $uninstaller -ArgumentList "/S _?=$installRoot" -Wait -PassThru
            $exitCode = $process.ExitCode
        } finally { Record-Timing 'uninstall' $timer $exitCode }
        if ($process.ExitCode -ne 0) { throw "Uninstall exited $($process.ExitCode)" }
    }
}
if (Test-Path $exe) { throw 'Application EXE remains after uninstall' }
if (!(Test-Path (Join-Path $profileRoot 'excelmanus.db'))) { throw 'Uninstall lost profile database' }
Write-Host 'WINDOWS_INSTALL_UPGRADE_UNINSTALL_OK'
