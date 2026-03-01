#Requires -Version 5.1
<#
.SYNOPSIS
    ExcelManus 一键更新脚本 (Windows PowerShell)
    傻瓜式覆盖旧版本，自动保留用户数据

.DESCRIPTION
    用法:  .\deploy\update.ps1 [选项]

    选项:
      -CheckOnly           仅检查是否有更新
      -SkipBackup          跳过数据备份（不推荐）
      -SkipDeps            跳过依赖重装
      -Mirror              使用国内镜像
      -Force               强制覆盖（git reset --hard）
      -Rollback            从最近的备份恢复
      -ListBackups         列出所有备份
      -Yes                 跳过确认提示
      -Verbose             详细输出

.EXAMPLE
    .\deploy\update.ps1
    交互式更新

.EXAMPLE
    .\deploy\update.ps1 -CheckOnly
    仅检查更新

.EXAMPLE
    .\deploy\update.ps1 -Mirror -Yes
    使用国内镜像，跳过确认
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

function Write-Log { param([string]$M) Write-Host "[OK] $M" -ForegroundColor Green }
function Write-Info { param([string]$M) Write-Host "[--] $M" -ForegroundColor Cyan }
function Write-Warn { param([string]$M) Write-Host "[!!] $M" -ForegroundColor Yellow }
function Write-Err { param([string]$M) Write-Host "[XX] $M" -ForegroundColor Red }
function Write-Step { param([string]$M) Write-Host "`n-- $M --" -ForegroundColor White }

function Get-ProjectVersion {
    $toml = Join-Path $Script:PROJECT_ROOT "pyproject.toml"
    if (Test-Path $toml) {
        $line = Get-Content $toml | Where-Object { $_ -match '^\s*version\s*=' } | Select-Object -First 1
        if ($line -match '"([^"]+)"') { return $Matches[1] }
    }
    return "unknown"
}

$CurrentVersion = Get-ProjectVersion

# ── 列出备份 ──
if ($ListBackups) {
    $backupDir = Join-Path $Script:PROJECT_ROOT "backups"
    if (-not (Test-Path $backupDir)) { Write-Info "暂无备份"; exit 0 }
    Write-Host "可用备份:" -ForegroundColor White
    Get-ChildItem $backupDir -Directory | Where-Object { $_.Name -like "backup_*" } |
        Sort-Object Name -Descending | ForEach-Object {
            $size = "{0:N1} MB" -f ((Get-ChildItem $_.FullName -Recurse -File |
                Measure-Object Length -Sum).Sum / 1MB)
            Write-Host "  $($_.Name)  ($size)" -ForegroundColor Cyan
        }
    exit 0
}

# ── 回滚 ──
if ($Rollback) {
    $backupDir = Join-Path $Script:PROJECT_ROOT "backups"
    $latest = Get-ChildItem $backupDir -Directory -ErrorAction SilentlyContinue |
        Where-Object { $_.Name -like "backup_*" } |
        Sort-Object Name -Descending | Select-Object -First 1
    if (-not $latest) { Write-Err "未找到任何备份"; exit 1 }
    Write-Step "从备份恢复: $($latest.Name)"
    $envSrc = Join-Path $latest.FullName ".env"
    if (Test-Path $envSrc) {
        Copy-Item $envSrc (Join-Path $Script:PROJECT_ROOT ".env") -Force
        Write-Log "恢复 .env"
    }
    foreach ($dir in @("users", "outputs", "uploads")) {
        $src = Join-Path $latest.FullName $dir
        if (Test-Path $src) {
            $dst = Join-Path $Script:PROJECT_ROOT $dir
            if (Test-Path $dst) { Remove-Item $dst -Recurse -Force }
            Copy-Item $src $dst -Recurse
            Write-Log "恢复 $dir/"
        }
    }
    $dbBackup = Join-Path $latest.FullName ".excelmanus_home"
    if (Test-Path $dbBackup) {
        $homeDb = Join-Path $env:USERPROFILE ".excelmanus"
        if (-not (Test-Path $homeDb)) { New-Item $homeDb -ItemType Directory -Force | Out-Null }
        Copy-Item (Join-Path $dbBackup "*.db*") $homeDb -Force -ErrorAction SilentlyContinue
        Write-Log "恢复数据库"
    }
    Write-Log "回滚完成！请重启服务。"
    exit 0
}

# ── 主流程 ──
Write-Host ""
Write-Host "  +======================================+" -ForegroundColor Cyan
Write-Host "  |     ExcelManus 更新工具 v1.0         |" -ForegroundColor Cyan
Write-Host "  +======================================+" -ForegroundColor Cyan
Write-Host ""
Write-Info "当前版本: $CurrentVersion"
Write-Info "项目目录: $($Script:PROJECT_ROOT)"
Write-Host ""

# 检查 Git
$gitDir = Join-Path $Script:PROJECT_ROOT ".git"
if (-not (Test-Path $gitDir)) {
    Write-Err "项目不是 Git 仓库，无法更新"
    exit 1
}
if (-not (Get-Command git -ErrorAction SilentlyContinue)) {
    Write-Err "未找到 Git，请先安装"
    exit 1
}

# Step 1: 检查更新
Write-Step "检查更新"
try {
    git -C $Script:PROJECT_ROOT fetch origin --tags --quiet 2>$null
} catch {
    Write-Warn "git fetch 失败，请检查网络"
    exit 1
}

$branch = git -C $Script:PROJECT_ROOT rev-parse --abbrev-ref HEAD 2>$null
if (-not $branch) { $branch = "main" }
$behind = git -C $Script:PROJECT_ROOT rev-list --count "HEAD..origin/$branch" 2>$null
if (-not $behind) { $behind = "0" }

if ([int]$behind -eq 0) {
    Write-Log "已是最新版本 ($CurrentVersion)"
    exit 0
}

$remoteToml = git -C $Script:PROJECT_ROOT show "origin/${branch}:pyproject.toml" 2>$null
$remoteVersion = "unknown"
if ($remoteToml) {
    $vline = $remoteToml | Where-Object { $_ -match '^\s*version\s*=' } | Select-Object -First 1
    if ($vline -match '"([^"]+)"') { $remoteVersion = $Matches[1] }
}

Write-Host ""
Write-Info "发现新版本!"
Write-Host "  当前: " -NoNewline; Write-Host $CurrentVersion -ForegroundColor Red
Write-Host "  最新: " -NoNewline; Write-Host $remoteVersion -ForegroundColor Green
Write-Info "  落后: $behind 个提交"
Write-Host ""

if ($CheckOnly) { exit 0 }

# 确认
if (-not $Yes) {
    $confirm = Read-Host "是否开始更新？[Y/n]"
    if ($confirm -match "^[Nn]") { Write-Info "已取消"; exit 0 }
}

# Step 2: 备份
if (-not $SkipBackup) {
    Write-Step "备份用户数据"
    $ts = Get-Date -Format "yyyyMMdd_HHmmss"
    $backupPath = Join-Path $Script:PROJECT_ROOT "backups" "backup_${CurrentVersion}_${ts}"
    New-Item $backupPath -ItemType Directory -Force | Out-Null

    foreach ($item in @(".env", "users", "outputs", "uploads")) {
        $src = Join-Path $Script:PROJECT_ROOT $item
        if (Test-Path $src -PathType Leaf) {
            Copy-Item $src (Join-Path $backupPath $item) -Force
            Write-Log "备份: $item"
        } elseif ((Test-Path $src -PathType Container) -and (Get-ChildItem $src -ErrorAction SilentlyContinue)) {
            Copy-Item $src (Join-Path $backupPath $item) -Recurse
            Write-Log "备份: $item/"
        }
    }
    $homeDb = Join-Path $env:USERPROFILE ".excelmanus"
    if (Test-Path $homeDb) {
        $dbDst = Join-Path $backupPath ".excelmanus_home"
        New-Item $dbDst -ItemType Directory -Force | Out-Null
        Copy-Item (Join-Path $homeDb "*.db*") $dbDst -Force -ErrorAction SilentlyContinue
        Write-Log "备份: 数据库"
    }
    Write-Log "备份完成: $backupPath"
} else {
    Write-Warn "跳过备份 (-SkipBackup)"
}

# Step 3: 更新代码
Write-Step "拉取最新代码"
$status = git -C $Script:PROJECT_ROOT status --porcelain 2>$null
if ($status) {
    Write-Info "暂存本地修改..."
    git -C $Script:PROJECT_ROOT stash --include-untracked --quiet 2>$null
}

if ($Force) {
    Write-Info "强制覆盖模式"
    git -C $Script:PROJECT_ROOT reset --hard "origin/$branch" --quiet
} else {
    try {
        git -C $Script:PROJECT_ROOT pull origin $branch --ff-only --quiet 2>$null
    } catch {
        Write-Warn "fast-forward 失败，执行强制覆盖..."
        git -C $Script:PROJECT_ROOT reset --hard "origin/$branch" --quiet
    }
}
$NewVersion = Get-ProjectVersion
Write-Log "代码已更新: $CurrentVersion -> $NewVersion"

# Step 4: 依赖
if (-not $SkipDeps) {
    Write-Step "更新后端依赖"
    $venvPy = Join-Path $Script:PROJECT_ROOT ".venv" "Scripts" "python.exe"
    if (-not (Test-Path $venvPy)) { $venvPy = "python" }

    $pipArgs = @("-m", "pip", "install", "-e", "$($Script:PROJECT_ROOT)[all]", "--quiet")
    if ($Mirror) { $pipArgs += @("-i", "https://pypi.tuna.tsinghua.edu.cn/simple") }

    try {
        & $venvPy $pipArgs 2>$null
        Write-Log "后端依赖已更新"
    } catch {
        Write-Warn "pip 失败，尝试清华镜像..."
        try {
            & $venvPy -m pip install -e "$($Script:PROJECT_ROOT)[all]" -i "https://pypi.tuna.tsinghua.edu.cn/simple" --quiet 2>$null
            Write-Log "后端依赖已更新"
        } catch {
            Write-Err "后端依赖安装失败"
        }
    }

    $webDir = Join-Path $Script:PROJECT_ROOT "web"
    if ((Test-Path $webDir) -and (Test-Path (Join-Path $webDir "package.json"))) {
        Write-Step "更新前端依赖"
        $npmArgs = @("install")
        if ($Mirror) { $npmArgs += "--registry=https://registry.npmmirror.com" }
        try {
            Push-Location $webDir
            & npm $npmArgs --silent 2>$null
            Write-Log "前端依赖已更新"
        } catch {
            Write-Warn "前端依赖安装失败（非致命）"
        } finally {
            Pop-Location
        }
    }
} else {
    Write-Warn "跳过依赖更新 (-SkipDeps)"
}

# Step 5: 预验证数据库迁移
Write-Step "预验证数据库迁移"
$venvPyCheck = Join-Path $Script:PROJECT_ROOT ".venv" "Scripts" "python.exe"
if (-not (Test-Path $venvPyCheck)) { $venvPyCheck = "python" }
try {
    $dbCheckResult = & $venvPyCheck -c @"
import sys
try:
    from excelmanus.updater import verify_database_migration
    ok, msg = verify_database_migration('$($Script:PROJECT_ROOT -replace '\\','\\\\')')
    print(msg)
    sys.exit(0 if ok else 1)
except Exception as e:
    print(f'跳过预验证: {e}')
    sys.exit(0)
"@ 2>&1
    if ($LASTEXITCODE -eq 0) {
        Write-Log "数据库迁移验证: $dbCheckResult"
    } else {
        Write-Warn "数据库迁移预验证警告: $dbCheckResult（将在启动时自动重试）"
    }
} catch {
    Write-Warn "数据库迁移预验证跳过: $_"
}

# 完成
Write-Host ""
Write-Host "  +======================================+" -ForegroundColor Green
Write-Host "  |         更新成功！                   |" -ForegroundColor Green
Write-Host "  +======================================+" -ForegroundColor Green
Write-Host ""
Write-Info "版本: $CurrentVersion -> $NewVersion"
Write-Info "数据库迁移将在下次启动时自动执行"
Write-Host ""
Write-Info "请重启服务以应用更新:"
Write-Info "  .\deploy\start.ps1"
Write-Host ""
