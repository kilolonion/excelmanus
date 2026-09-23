[CmdletBinding()]
param(
    [ValidateSet('all', 'desktop', 'android')]
    [string]$Target = 'all',
    [switch]$KeepOutput
)

$ErrorActionPreference = 'Stop'
$repoRoot = Split-Path -Parent $PSScriptRoot
$arguments = @('scripts/package-release.mjs', '--platform', 'windows')

if ($Target -eq 'desktop') {
    $arguments += '--desktop-only'
} elseif ($Target -eq 'android') {
    $arguments += '--android-only'
}
if ($KeepOutput) { $arguments += '--no-clean-output' }

Push-Location $repoRoot
try {
    & node @arguments
    if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }
} finally {
    Pop-Location
}
