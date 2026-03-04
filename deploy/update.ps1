#Requires -Version 5.1
<#
.SYNOPSIS
    ExcelManus update helper for Windows PowerShell.

.DESCRIPTION
    Pulls the latest git commits, optionally backs up local user data,
    updates Python and frontend dependencies, and runs a migration precheck.

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
$Script:PIP_MIRROR = "https://pypi.tuna.tsinghua.edu.cn/simple"
$Script:NPM_MIRROR = "https://registry.npmmirror.com"

function Write-Log {
    param([string]$Message)
    Write-Host "[OK] $Message" -ForegroundColor Green
}

function Write-Info {
    param([string]$Message)
    Write-Host "[--] $Message" -ForegroundColor Cyan
}

function Write-Warn {
    param([string]$Message)
    Write-Host "[!!] $Message" -ForegroundColor Yellow
}

function Write-Err {
    param([string]$Message)
    Write-Host "[XX] $Message" -ForegroundColor Red
}

function Write-Step {
    param([string]$Message)
    Write-Host ""
    Write-Host "-- $Message --" -ForegroundColor White
}

function Get-ProjectVersion {
    $toml = Join-Path $Script:PROJECT_ROOT "pyproject.toml"
    if (-not (Test-Path $toml)) { return "unknown" }

    $line = Get-Content $toml | Where-Object { $_ -match '^\s*version\s*=\s*".*"' } | Select-Object -First 1
    if ($line -and $line -match '"([^"]+)"') {
        return $Matches[1]
    }
    return "unknown"
}

function Get-LatestBackup {
    $backupRoot = Join-Path $Script:PROJECT_ROOT "backups"
    if (-not (Test-Path $backupRoot)) { return $null }

    return Get-ChildItem $backupRoot -Directory -ErrorAction SilentlyContinue |
        Where-Object { $_.Name -like "backup_*" } |
        Sort-Object Name -Descending |
        Select-Object -First 1
}

function Backup-UserData {
    param([string]$CurrentVersion)

    $timestamp = Get-Date -Format "yyyyMMdd_HHmmss"
    $backupPath = Join-Path $Script:PROJECT_ROOT "backups" "backup_${CurrentVersion}_${timestamp}"
    New-Item -Path $backupPath -ItemType Directory -Force | Out-Null

    foreach ($item in @(".env", "users", "outputs", "uploads")) {
        $src = Join-Path $Script:PROJECT_ROOT $item
        $dst = Join-Path $backupPath $item

        if (Test-Path $src -PathType Leaf) {
            Copy-Item $src $dst -Force
            Write-Log "Backed up: $item"
        } elseif ((Test-Path $src -PathType Container) -and (Get-ChildItem $src -ErrorAction SilentlyContinue)) {
            Copy-Item $src $dst -Recurse -Force
            Write-Log "Backed up: $item/"
        }
    }

    $homeDb = Join-Path $env:USERPROFILE ".excelmanus"
    if (Test-Path $homeDb) {
        $dbDst = Join-Path $backupPath ".excelmanus_home"
        New-Item -Path $dbDst -ItemType Directory -Force | Out-Null
        Copy-Item (Join-Path $homeDb "*.db*") $dbDst -Force -ErrorAction SilentlyContinue
        Write-Log "Backed up: .excelmanus DB files"
    }

    return $backupPath
}

function Restore-LatestBackup {
    $latest = Get-LatestBackup
    if (-not $latest) {
        Write-Err "No backup found."
        exit 1
    }

    Write-Step "Rollback from backup: $($latest.Name)"

    $envSrc = Join-Path $latest.FullName ".env"
    if (Test-Path $envSrc) {
        Copy-Item $envSrc (Join-Path $Script:PROJECT_ROOT ".env") -Force
        Write-Log "Restored .env"
    }

    foreach ($dir in @("users", "outputs", "uploads")) {
        $src = Join-Path $latest.FullName $dir
        $dst = Join-Path $Script:PROJECT_ROOT $dir
        if (Test-Path $src) {
            if (Test-Path $dst) { Remove-Item $dst -Recurse -Force }
            Copy-Item $src $dst -Recurse -Force
            Write-Log "Restored $dir/"
        }
    }

    $dbBackup = Join-Path $latest.FullName ".excelmanus_home"
    if (Test-Path $dbBackup) {
        $homeDb = Join-Path $env:USERPROFILE ".excelmanus"
        if (-not (Test-Path $homeDb)) { New-Item $homeDb -ItemType Directory -Force | Out-Null }
        Copy-Item (Join-Path $dbBackup "*.db*") $homeDb -Force -ErrorAction SilentlyContinue
        Write-Log "Restored .excelmanus DB files"
    }

    Write-Log "Rollback completed."
    exit 0
}

function Show-Backups {
    $backupRoot = Join-Path $Script:PROJECT_ROOT "backups"
    if (-not (Test-Path $backupRoot)) {
        Write-Info "No backups found."
        exit 0
    }

    $items = Get-ChildItem $backupRoot -Directory -ErrorAction SilentlyContinue |
        Where-Object { $_.Name -like "backup_*" } |
        Sort-Object Name -Descending

    if (-not $items) {
        Write-Info "No backups found."
        exit 0
    }

    Write-Host "Available backups:" -ForegroundColor White
    foreach ($item in $items) {
        $sizeBytes = (Get-ChildItem $item.FullName -Recurse -File -ErrorAction SilentlyContinue | Measure-Object Length -Sum).Sum
        if (-not $sizeBytes) { $sizeBytes = 0 }
        $sizeMb = "{0:N1}" -f ($sizeBytes / 1MB)
        Write-Host "  $($item.Name)  (${sizeMb} MB)" -ForegroundColor Cyan
    }
    exit 0
}

# ── 网络探测：自动识别国内网络，优先使用镜像 ──
function Test-DomesticNetwork {
    $pythonExe = "python"
    $venvPy = Join-Path $Script:PROJECT_ROOT ".venv\Scripts\python.exe"
    if (Test-Path $venvPy) { $pythonExe = $venvPy }
    try {
        $result = & $pythonExe -c @"
import socket, time, concurrent.futures
def ping(host):
    try:
        t=time.monotonic(); socket.create_connection((host,443),3); return time.monotonic()-t
    except: return 999
with concurrent.futures.ThreadPoolExecutor(2) as p:
    fm=p.submit(ping,'pypi.tuna.tsinghua.edu.cn')
    fp=p.submit(ping,'pypi.org')
    tm,tp=fm.result(5),fp.result(5)
print('1' if tm<5 and (tp>5 or tm<tp*0.8) else '0')
"@ 2>$null
        return ($result -eq "1")
    } catch {
        return $false
    }
}

function Get-PythonExe {
    $venvPy = Join-Path $Script:PROJECT_ROOT ".venv\Scripts\python.exe"
    if (Test-Path $venvPy) { return $venvPy }
    return "python"
}

function Install-Dependencies {
    Write-Step "Install dependencies (parallel)"
    Write-Info "Backend + frontend dependencies installing in parallel..."

    $useMirror = [bool]$Mirror
    $projectRoot = $Script:PROJECT_ROOT
    $pipMirror = $Script:PIP_MIRROR
    $npmMirror = $Script:NPM_MIRROR

    # ── 后端依赖 Job ──
    $backendJob = Start-Job -ScriptBlock {
        param($ProjectRoot, $UseMirror, $PipMirror)
        $venvPy = Join-Path $ProjectRoot ".venv\Scripts\python.exe"
        $pythonExe = if (Test-Path $venvPy) { $venvPy } else { "python" }

        # 优先 uv
        if (Get-Command uv -ErrorAction SilentlyContinue) {
            $uvArgs = @("sync", "--all-extras", "--project", $ProjectRoot, "--quiet")
            if ($UseMirror) { $uvArgs += @("--index-url", $PipMirror) }
            & uv @uvArgs 2>&1 | Out-Null
            if ($LASTEXITCODE -eq 0) { return "ok" }
        }
        # 回退 pip
        Push-Location $ProjectRoot
        try {
            $pipArgs = @("-m", "pip", "install", "-e", ".[all]", "--quiet")
            if ($UseMirror) { $pipArgs += @("-i", $PipMirror) }
            & $pythonExe @pipArgs 2>&1 | Out-Null
            if ($LASTEXITCODE -eq 0) { return "ok" }
            # 回退: 强制使用镜像
            if (-not $UseMirror) {
                & $pythonExe -m pip install -e ".[all]" -i $PipMirror --quiet 2>&1 | Out-Null
                if ($LASTEXITCODE -eq 0) { return "ok" }
            }
        } finally { Pop-Location }
        return "fail"
    } -ArgumentList $projectRoot, $useMirror, $pipMirror

    # ── 前端依赖 Job ──
    $webDir = Join-Path $projectRoot "web"
    $frontendJob = $null
    if ((Test-Path $webDir) -and (Test-Path (Join-Path $webDir "package.json"))) {
        $frontendJob = Start-Job -ScriptBlock {
            param($WebDir, $UseMirror, $NpmMirror)
            Push-Location $WebDir
            try {
                $npmArgs = @("install", "--silent")
                if ($UseMirror) { $npmArgs += "--registry=$NpmMirror" }
                & npm @npmArgs 2>&1 | Out-Null
                if ($LASTEXITCODE -eq 0) { return "ok" }
                # 回退: 强制使用 npmmirror
                if (-not $UseMirror) {
                    & npm install "--registry=$NpmMirror" --silent 2>&1 | Out-Null
                    if ($LASTEXITCODE -eq 0) { return "ok" }
                }
            } finally { Pop-Location }
            return "fail"
        } -ArgumentList $webDir, $useMirror, $npmMirror
    }

    # ── 等待并行完成 ──
    $beResult = Receive-Job -Job $backendJob -Wait -AutoRemoveJob
    if ($beResult -eq "ok") {
        Write-Log "Backend dependencies installed."
    } else {
        Write-Err "Backend dependency install failed."
    }

    if ($frontendJob) {
        $feResult = Receive-Job -Job $frontendJob -Wait -AutoRemoveJob
        if ($feResult -eq "ok") {
            Write-Log "Frontend dependencies installed."
        } else {
            Write-Warn "Frontend dependency install failed (non-fatal)."
        }
    }
}

function Verify-DatabaseMigration {
    Write-Step "Precheck database migration"
    $pythonExe = Get-PythonExe
    $env:EXCELMANUS_UPDATE_PROJECT_ROOT = $Script:PROJECT_ROOT
    try {
        $result = & $pythonExe -c @"
import os
import sys
try:
    from excelmanus.updater import verify_database_migration
    ok, msg = verify_database_migration(os.environ.get("EXCELMANUS_UPDATE_PROJECT_ROOT"))
    print(msg)
    sys.exit(0 if ok else 1)
except Exception as exc:
    print(f"skip migration precheck: {exc}")
    sys.exit(0)
"@ 2>&1
        if ($LASTEXITCODE -eq 0) {
            Write-Log "Migration precheck: $result"
        } else {
            Write-Warn "Migration precheck warning: $result"
        }
    } catch {
        Write-Warn "Migration precheck skipped: $_"
    } finally {
        Remove-Item Env:EXCELMANUS_UPDATE_PROJECT_ROOT -ErrorAction SilentlyContinue
    }
}

if ($ListBackups) {
    Show-Backups
}

if ($Rollback) {
    Restore-LatestBackup
}

# ── 自动探测国内网络 ──
if (-not $Mirror) {
    if (Test-DomesticNetwork) {
        $Mirror = [switch]::new($true)
        Write-Info "Detected domestic network, auto-enabling mirror."
    }
}

$currentVersion = Get-ProjectVersion

Write-Host ""
Write-Host "  +======================================+" -ForegroundColor Cyan
Write-Host "  |       ExcelManus Update Tool         |" -ForegroundColor Cyan
Write-Host "  +======================================+" -ForegroundColor Cyan
Write-Host ""
Write-Info "Current version: $currentVersion"
Write-Info "Project root: $($Script:PROJECT_ROOT)"
Write-Host ""

$gitDir = Join-Path $Script:PROJECT_ROOT ".git"
if (-not (Test-Path $gitDir)) {
    Write-Err "Project is not a git repository."
    exit 1
}

if (-not (Get-Command git -ErrorAction SilentlyContinue)) {
    Write-Err "Git is not installed."
    exit 1
}

Write-Step "Check updates"
try {
    git -C $Script:PROJECT_ROOT fetch origin --tags --quiet 2>$null
} catch {
    Write-Warn "git fetch failed, please check your network."
    exit 1
}

$branch = git -C $Script:PROJECT_ROOT rev-parse --abbrev-ref HEAD 2>$null
if (-not $branch) { $branch = "main" }

$behind = git -C $Script:PROJECT_ROOT rev-list --count "HEAD..origin/$branch" 2>$null
if (-not $behind) { $behind = "0" }

if ([int]$behind -eq 0) {
    Write-Log "Already up to date ($currentVersion)."
    exit 0
}

$remoteToml = git -C $Script:PROJECT_ROOT show "origin/${branch}:pyproject.toml" 2>$null
$remoteVersion = "unknown"
if ($remoteToml) {
    $versionLine = $remoteToml | Where-Object { $_ -match '^\s*version\s*=' } | Select-Object -First 1
    if ($versionLine -match '"([^"]+)"') { $remoteVersion = $Matches[1] }
}

Write-Host ""
Write-Info "Update available."
Write-Host "  Current: " -NoNewline
Write-Host $currentVersion -ForegroundColor Red
Write-Host "  Latest : " -NoNewline
Write-Host $remoteVersion -ForegroundColor Green
Write-Info "  Behind : $behind commits"
Write-Host ""

if ($CheckOnly) {
    exit 0
}

if (-not $Yes) {
    $confirm = Read-Host "Proceed with update? [Y/n]"
    if ($confirm -match "^[Nn]") {
        Write-Info "Cancelled."
        exit 0
    }
}

if (-not $SkipBackup) {
    Write-Step "Backup user data"
    $backupPath = Backup-UserData -CurrentVersion $currentVersion
    Write-Log "Backup complete: $backupPath"
} else {
    Write-Warn "Skip backup (-SkipBackup)."
}

Write-Step "Pull latest code"

# 记录更新前的 commit（供回滚精确回退）
$preDeployCommit = git -C $Script:PROJECT_ROOT rev-parse HEAD 2>$null

$hasLocalChanges = git -C $Script:PROJECT_ROOT status --porcelain 2>$null
if ($hasLocalChanges) {
    Write-Info "Stashing local changes..."
    git -C $Script:PROJECT_ROOT stash --include-untracked --quiet 2>$null | Out-Null
}

if ($Force) {
    Write-Warn "Force mode enabled: reset to origin/$branch"
    git -C $Script:PROJECT_ROOT reset --hard "origin/$branch" --quiet
} else {
    try {
        git -C $Script:PROJECT_ROOT pull origin $branch --ff-only --quiet 2>$null
    } catch {
        Write-Warn "Fast-forward pull failed; resetting to origin/$branch."
        git -C $Script:PROJECT_ROOT reset --hard "origin/$branch" --quiet
    }
}

$newVersion = Get-ProjectVersion
Write-Log "Code updated: $currentVersion -> $newVersion"

if (-not $SkipDeps) {
    Install-Dependencies
} else {
    Write-Warn "Skip dependency update (-SkipDeps)."
}

Verify-DatabaseMigration

# ── Step: 重新构建前端（生产模式必须，否则新代码不生效） ──
$webDir = Join-Path $Script:PROJECT_ROOT "web"
if ((Test-Path $webDir) -and (Test-Path (Join-Path $webDir "package.json"))) {
    $nextDir = Join-Path $webDir ".next"
    if ((Test-Path $nextDir) -or (-not $SkipDeps)) {
        Write-Step "Build frontend"
        Write-Info "Rebuilding frontend for production (npm run build)..."
        Push-Location $webDir
        try {
            & npm run build 2>&1
            if ($LASTEXITCODE -eq 0) {
                Write-Log "Frontend build complete."
            } else {
                Write-Warn "Default build failed, trying webpack fallback..."
                & npm run build:webpack 2>&1
                if ($LASTEXITCODE -eq 0) {
                    Write-Log "Frontend build complete (webpack fallback)."
                } else {
                    Write-Warn "Frontend build failed (non-fatal). For production, run manually: cd web && npm run build"
                }
            }
        } finally {
            Pop-Location
        }
    }
}

# 写入 .deploy_meta.json（与 deploy.sh 行为一致）
$metaFile = Join-Path $Script:PROJECT_ROOT ".deploy_meta.json"
try {
    $gitCommitShort = git -C $Script:PROJECT_ROOT rev-parse --short HEAD 2>$null
    $gitCommitFull = git -C $Script:PROJECT_ROOT rev-parse HEAD 2>$null
    $meta = @{
        release_id        = (Get-Date -Format "yyyyMMddTHHmmss")
        deployed_at       = (Get-Date -Format "yyyy-MM-ddTHH:mm:ssZ")
        git_commit        = $(if ($gitCommitShort) { $gitCommitShort } else { "unknown" })
        git_commit_full   = $(if ($gitCommitFull) { $gitCommitFull } else { "" })
        pre_deploy_commit = $(if ($preDeployCommit) { $preDeployCommit } else { "" })
        deploy_mode       = "standalone"
        topology          = "local"
        branch            = $(if ($branch) { $branch } else { "unknown" })
        updater           = "update.ps1"
    }
    $meta | ConvertTo-Json -Depth 2 | Set-Content -Path $metaFile -Encoding UTF8
    Write-Log "Deploy metadata written: $metaFile"
} catch {
    Write-Warn "Failed to write .deploy_meta.json (non-fatal): $_"
}

Write-Host ""
Write-Host "  +======================================+" -ForegroundColor Green
Write-Host "  |            Update Success            |" -ForegroundColor Green
Write-Host "  +======================================+" -ForegroundColor Green
Write-Host ""
Write-Info "Version: $currentVersion -> $newVersion"
Write-Info "Restart service to apply updates:"
Write-Info "  .\deploy\start.ps1"
Write-Host ""
