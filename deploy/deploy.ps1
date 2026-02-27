#Requires -Version 5.1
<#
.SYNOPSIS
    ExcelManus 通用部署脚本 (Windows PowerShell 版本)

.DESCRIPTION
    支持多种部署拓扑：
      - 单机部署（前后端同一台服务器）
      - 前后端分离（两台服务器）
      - Docker Compose 部署
      - 本地开发部署

    配置优先级：命令行参数 > 环境变量 > deploy\.env.deploy > 内置默认值

.PARAMETER Command
    执行的命令: deploy(默认) | rollback | status | check | init-env | history | logs

.PARAMETER BackendOnly
    只更新后端

.PARAMETER FrontendOnly
    只更新前端

.PARAMETER Full
    完整部署（默认）

.PARAMETER SkipBuild
    跳过前端构建（仅同步+重启）

.PARAMETER FrontendArtifact
    使用本地/CI 构建的前端制品（tar.gz），上传后原子切换

.PARAMETER SkipDeps
    跳过依赖安装

.PARAMETER ColdBuild
    远端构建前清理 web\.next\cache（高风险，默认关闭）

.PARAMETER FromLocal
    从本地同步（默认从 GitHub 拉取）

.PARAMETER DryRun
    仅打印将执行的操作，不实际执行

.PARAMETER NoLock
    跳过部署锁（允许并行部署，危险）

.PARAMETER Force
    强制执行（跳过确认提示）

.PARAMETER Topology
    部署拓扑: auto(默认) | single | split | docker | local

.PARAMETER BackendHost
    后端服务器地址

.PARAMETER FrontendHost
    前端服务器地址

.PARAMETER Host
    单机模式的服务器地址

.PARAMETER SshUser
    SSH 用户名（默认 root）

.PARAMETER SshKeyPath
    SSH 私钥路径

.PARAMETER SshPort
    SSH 端口（默认 22）

.PARAMETER BackendDir
    后端远程目录

.PARAMETER FrontendDir
    前端远程目录

.PARAMETER Dir
    单机模式的项目目录

.PARAMETER NodeBin
    Node.js bin 目录（远程服务器）

.PARAMETER PythonBin
    Python 可执行文件路径

.PARAMETER VenvDir
    Python venv 目录（相对于后端目录）

.PARAMETER Pm2Backend
    后端 PM2 进程名（默认 excelmanus-api）

.PARAMETER Pm2Frontend
    前端 PM2 进程名（默认 excelmanus-web）

.PARAMETER ServiceManager
    服务管理器: pm2(默认) | systemd | nssm

.PARAMETER BackendPort
    后端 API 端口（默认 8000）

.PARAMETER FrontendPort
    前端端口（默认 3000）

.PARAMETER KeepFrontendReleases
    前端制品部署后保留的回滚备份数量（默认 3）

.PARAMETER RepoUrl
    Git 仓库地址

.PARAMETER Branch
    Git 分支（默认 main）

.PARAMETER HealthUrl
    健康检查 URL

.PARAMETER NoVerify
    跳过部署后验证

.PARAMETER VerifyTimeout
    健康检查超时秒数（默认 30）

.PARAMETER PreDeployHook
    部署前执行的脚本

.PARAMETER PostDeployHook
    部署后执行的脚本

.PARAMETER Verbose
    详细输出

.PARAMETER Quiet
    静默模式（仅输出错误）

.EXAMPLE
    .\deploy\deploy.ps1 -Host 192.168.1.100 -Dir C:\excelmanus
    单机部署

.EXAMPLE
    .\deploy\deploy.ps1 -BackendHost 10.0.0.1 -FrontendHost 10.0.0.2
    前后端分离部署

.EXAMPLE
    .\deploy\deploy.ps1 rollback
    回滚到上一版本

.EXAMPLE
    .\deploy\deploy.ps1 check
    检查环境依赖

.EXAMPLE
    .\deploy\deploy.ps1 -Topology docker
    Docker Compose 部署
#>

[CmdletBinding()]
param(
    [Parameter(Position = 0)]
    [ValidateSet("deploy", "rollback", "status", "check", "init-env", "history", "logs")]
    [string]$Command = "deploy",

    [switch]$BackendOnly,
    [switch]$FrontendOnly,
    [switch]$Full,
    [switch]$SkipBuild,
    [string]$FrontendArtifact = "",
    [switch]$SkipDeps,
    [switch]$ColdBuild,
    [switch]$FromLocal,
    [switch]$DryRun,
    [switch]$NoLock,
    [switch]$Force,

    [ValidateSet("auto", "single", "split", "docker", "local")]
    [string]$Topology = "auto",

    [string]$BackendHost = "",
    [string]$FrontendHost = "",
    [Alias("Host")]
    [string]$SingleHost = "",
    [string]$SshUser = "",
    [string]$SshKeyPath = "",
    [int]$SshPort = 0,

    [string]$BackendDir = "",
    [string]$FrontendDir = "",
    [Alias("Dir")]
    [string]$SingleDir = "",

    [string]$NodeBin = "",
    [string]$PythonBin = "",
    [string]$VenvDir = "",
    [string]$Pm2Backend = "",
    [string]$Pm2Frontend = "",
    [ValidateSet("pm2", "systemd", "nssm")]
    [string]$ServiceManager = "",
    [int]$BackendPort = 0,
    [int]$FrontendPort = 0,
    [int]$KeepFrontendReleases = 0,

    [string]$RepoUrl = "",
    [string]$Branch = "",

    [string]$HealthUrl = "",
    [switch]$NoVerify,
    [int]$VerifyTimeout = 0,

    [string]$PreDeployHook = "",
    [string]$PostDeployHook = "",

    [switch]$Quiet,
    [switch]$ShowVersion
)

# ═══════════════════════════════════════════════════════════════
#  常量与路径
# ═══════════════════════════════════════════════════════════════

$Script:VERSION = "2.0.0"
$Script:SCRIPT_DIR = Split-Path -Parent $MyInvocation.MyCommand.Path
$Script:PROJECT_ROOT = Split-Path -Parent $Script:SCRIPT_DIR
$Script:DEPLOY_LOG_FILE = ""
$Script:LOCK_FILE = ""
$Script:DEPLOY_START_TIME = $null

if ($ShowVersion) {
    Write-Output "ExcelManus Deploy v$Script:VERSION (PowerShell)"
    exit 0
}

# ═══════════════════════════════════════════════════════════════
#  日志函数
# ═══════════════════════════════════════════════════════════════

function Write-LogToFile {
    param([string]$Message)
    if ($Script:DEPLOY_LOG_FILE -and (Test-Path (Split-Path $Script:DEPLOY_LOG_FILE -Parent) -ErrorAction SilentlyContinue)) {
        $ts = Get-Date -Format "HH:mm:ss"
        Add-Content -Path $Script:DEPLOY_LOG_FILE -Value "[$ts] $Message" -ErrorAction SilentlyContinue
    }
}

function Write-Log {
    param([string]$Message)
    Write-LogToFile "OK  $Message"
    if (-not $Quiet) { Write-Host "[OK] $Message" -ForegroundColor Green }
}

function Write-Info {
    param([string]$Message)
    Write-LogToFile "INF $Message"
    if (-not $Quiet) { Write-Host "[--] $Message" -ForegroundColor Cyan }
}

function Write-Warn {
    param([string]$Message)
    Write-LogToFile "WRN $Message"
    Write-Host "[!!] $Message" -ForegroundColor Yellow
}

function Write-Err {
    param([string]$Message)
    Write-LogToFile "ERR $Message"
    Write-Host "[XX] $Message" -ForegroundColor Red
}

function Write-Debug2 {
    param([string]$Message)
    Write-LogToFile "DBG $Message"
    if ($VerbosePreference -eq "Continue") { Write-Host "[..] $Message" -ForegroundColor DarkCyan }
}

function Write-Step {
    param([string]$Message)
    Write-LogToFile "=== $Message"
    if (-not $Quiet) { Write-Host "`n=== $Message ===" -ForegroundColor White }
}

function Invoke-Run {
    param([string]$Cmd, [string]$WorkDir = "")
    if ($DryRun) {
        Write-Host "[dry-run] $Cmd" -ForegroundColor Yellow
        Write-LogToFile "DRY $Cmd"
        return $true
    }
    Write-Debug2 "exec: $Cmd"
    if ($WorkDir) {
        $result = cmd /c "cd /d `"$WorkDir`" && $Cmd" 2>&1
    } else {
        $result = cmd /c $Cmd 2>&1
    }
    if ($result) { $result | ForEach-Object { Write-Host $_ } }
    return ($LASTEXITCODE -eq 0)
}

# ═══════════════════════════════════════════════════════════════
#  日志文件初始化
# ═══════════════════════════════════════════════════════════════

function Initialize-LogFile {
    $logDir = Join-Path $Script:SCRIPT_DIR ".deploy_logs"
    if (-not (Test-Path $logDir)) { New-Item -ItemType Directory -Path $logDir -Force | Out-Null }
    $ts = Get-Date -Format "yyyyMMddTHHmmss"
    $Script:DEPLOY_LOG_FILE = Join-Path $logDir "deploy_$ts.log"
    Set-Content -Path $Script:DEPLOY_LOG_FILE -Value "# ExcelManus Deploy v$Script:VERSION - $(Get-Date -Format 'yyyy-MM-dd HH:mm:ss')"
    # 保留最近 20 个日志
    $oldLogs = Get-ChildItem -Path $logDir -Filter "deploy_*.log" | Sort-Object LastWriteTime -Descending | Select-Object -Skip 20
    $oldLogs | Remove-Item -Force -ErrorAction SilentlyContinue
}

# ═══════════════════════════════════════════════════════════════
#  配置加载
# ═══════════════════════════════════════════════════════════════

# 当前生效配置（脚本级变量）
$Script:CFG = @{
    Topology         = $Topology
    Mode             = "full"
    BackendHost      = $BackendHost
    FrontendHost     = $FrontendHost
    SshUser          = $SshUser
    SshKeyPath       = $SshKeyPath
    SshPort          = $SshPort
    BackendDir       = $BackendDir
    FrontendDir      = $FrontendDir
    NodeBin          = $NodeBin
    PythonBin        = $PythonBin
    VenvDir          = $VenvDir
    Pm2Backend       = $Pm2Backend
    Pm2Frontend      = $Pm2Frontend
    ServiceManager   = $ServiceManager
    BackendPort      = $BackendPort
    FrontendPort     = $FrontendPort
    RepoUrl          = $RepoUrl
    Branch           = $Branch
    HealthUrl        = $HealthUrl
    VerifyTimeout    = $VerifyTimeout
    KeepFrontendReleases = $KeepFrontendReleases
}

function Load-Config {
    $configFile = Join-Path $Script:SCRIPT_DIR ".env.deploy"
    if (Test-Path $configFile) {
        Write-Debug2 "loading config: $configFile"
        Get-Content $configFile | ForEach-Object {
            $line = $_.Trim()
            if ($line -and -not $line.StartsWith("#")) {
                $parts = $line -split "=", 2
                if ($parts.Count -eq 2) {
                    $key = $parts[0].Trim()
                    $val = $parts[1].Trim()
                    # 映射配置名到 CFG
                    switch ($key) {
                        "BACKEND_SERVER"     { if (-not $Script:CFG.BackendHost)  { $Script:CFG.BackendHost  = $val } }
                        "FRONTEND_SERVER"    { if (-not $Script:CFG.FrontendHost) { $Script:CFG.FrontendHost = $val } }
                        "SERVER_USER"        { if (-not $Script:CFG.SshUser)      { $Script:CFG.SshUser      = $val } }
                        "BACKEND_REMOTE_DIR" { if (-not $Script:CFG.BackendDir)   { $Script:CFG.BackendDir   = $val } }
                        "FRONTEND_REMOTE_DIR"{ if (-not $Script:CFG.FrontendDir)  { $Script:CFG.FrontendDir  = $val } }
                        "FRONTEND_NODE_BIN"  { if (-not $Script:CFG.NodeBin)      { $Script:CFG.NodeBin      = $val } }
                        "BACKEND_NODE_BIN"   { if (-not $Script:CFG.NodeBin)      { $Script:CFG.NodeBin      = $val } }
                        "SSH_KEY_NAME"       { if (-not $Script:CFG.SshKeyPath)   { $Script:CFG.SshKeyPath   = Join-Path $Script:PROJECT_ROOT $val } }
                        "REPO_URL"           { if (-not $Script:CFG.RepoUrl)      { $Script:CFG.RepoUrl      = $val } }
                        "REPO_BRANCH"        { if (-not $Script:CFG.Branch)       { $Script:CFG.Branch       = $val } }
                        "BACKEND_PORT"       { if ($Script:CFG.BackendPort -eq 0) { $Script:CFG.BackendPort  = [int]$val } }
                        "FRONTEND_PORT"      { if ($Script:CFG.FrontendPort -eq 0){ $Script:CFG.FrontendPort = [int]$val } }
                        "SITE_URL"           { $Script:SITE_URL = $val }
                        "SITE_DOMAIN"        { $Script:SITE_DOMAIN = $val }
                    }
                }
            }
        }
    } else {
        Write-Debug2 "config not found: $configFile (using defaults)"
    }
}

function Apply-Defaults {
    # 处理 -SingleHost / -SingleDir 别名
    if ($SingleHost) {
        $Script:CFG.BackendHost  = $SingleHost
        $Script:CFG.FrontendHost = $SingleHost
        $Script:CFG.Topology     = "single"
    }
    if ($SingleDir) {
        $Script:CFG.BackendDir  = $SingleDir
        $Script:CFG.FrontendDir = $SingleDir
    }

    # 处理模式
    if ($BackendOnly)  { $Script:CFG.Mode = "backend" }
    if ($FrontendOnly) { $Script:CFG.Mode = "frontend" }
    if ($Full)         { $Script:CFG.Mode = "full" }

    # 应用默认值
    if (-not $Script:CFG.SshUser)        { $Script:CFG.SshUser        = "root" }
    if ($Script:CFG.SshPort -eq 0)       { $Script:CFG.SshPort        = 22 }
    if (-not $Script:CFG.BackendDir)     { $Script:CFG.BackendDir     = "/www/wwwroot/excelmanus" }
    if (-not $Script:CFG.FrontendDir)    { $Script:CFG.FrontendDir    = $Script:CFG.BackendDir }
    if (-not $Script:CFG.NodeBin)        { $Script:CFG.NodeBin        = "/usr/local/bin" }
    if (-not $Script:CFG.PythonBin)      { $Script:CFG.PythonBin      = "python3" }
    if (-not $Script:CFG.VenvDir)        { $Script:CFG.VenvDir        = "venv" }
    if (-not $Script:CFG.Pm2Backend)     { $Script:CFG.Pm2Backend     = "excelmanus-api" }
    if (-not $Script:CFG.Pm2Frontend)    { $Script:CFG.Pm2Frontend    = "excelmanus-web" }
    if (-not $Script:CFG.ServiceManager) { $Script:CFG.ServiceManager = "pm2" }
    if ($Script:CFG.BackendPort -eq 0)   { $Script:CFG.BackendPort    = 8000 }
    if ($Script:CFG.FrontendPort -eq 0)  { $Script:CFG.FrontendPort   = 3000 }
    if (-not $Script:CFG.RepoUrl)        { $Script:CFG.RepoUrl        = "https://github.com/kilolonion/excelmanus" }
    if (-not $Script:CFG.Branch)         { $Script:CFG.Branch         = "main" }
    if ($Script:CFG.VerifyTimeout -eq 0) { $Script:CFG.VerifyTimeout  = 30 }
    if ($Script:CFG.KeepFrontendReleases -eq 0) { $Script:CFG.KeepFrontendReleases = 3 }

    # 自动检测拓扑
    if ($Script:CFG.Topology -eq "auto") {
        if ($Script:CFG.BackendHost -and $Script:CFG.FrontendHost -and ($Script:CFG.BackendHost -ne $Script:CFG.FrontendHost)) {
            $Script:CFG.Topology = "split"
        } elseif ($Script:CFG.BackendHost -or $Script:CFG.FrontendHost) {
            $Script:CFG.Topology = "single"
            if (-not $Script:CFG.BackendHost)  { $Script:CFG.BackendHost  = $Script:CFG.FrontendHost }
            if (-not $Script:CFG.FrontendHost) { $Script:CFG.FrontendHost = $Script:CFG.BackendHost }
        } else {
            $Script:CFG.Topology = "local"
        }
    }

    # 单机模式统一
    if ($Script:CFG.Topology -eq "single") {
        if (-not $Script:CFG.FrontendDir -or $Script:CFG.FrontendDir -eq $Script:CFG.BackendDir) {
            $Script:CFG.FrontendDir = $Script:CFG.BackendDir
        }
        if (-not $Script:CFG.FrontendHost) { $Script:CFG.FrontendHost = $Script:CFG.BackendHost }
    }

    # 健康检查 URL
    if (-not $Script:CFG.HealthUrl) {
        if ($Script:SITE_URL) {
            $Script:CFG.HealthUrl = "$Script:SITE_URL/api/v1/health"
        } elseif ($Script:CFG.Topology -eq "local") {
            $Script:CFG.HealthUrl = "http://localhost:$($Script:CFG.BackendPort)/api/v1/health"
        } elseif ($Script:CFG.BackendHost) {
            $Script:CFG.HealthUrl = "http://$($Script:CFG.BackendHost):$($Script:CFG.BackendPort)/api/v1/health"
        }
    }
}

# ═══════════════════════════════════════════════════════════════
#  SSH 执行封装
# ═══════════════════════════════════════════════════════════════

function Get-SshOpts {
    $opts = @("-o", "StrictHostKeyChecking=no", "-o", "ConnectTimeout=10", "-o", "ServerAliveInterval=30")
    if ($Script:CFG.SshKeyPath) { $opts += @("-i", $Script:CFG.SshKeyPath) }
    if ($Script:CFG.SshPort -ne 22) { $opts += @("-p", $Script:CFG.SshPort) }
    return $opts
}

function Invoke-Remote {
    param([string]$TargetHost, [string]$RemoteCmd)
    if ($Script:CFG.Topology -eq "local") {
        return Invoke-Run "bash -c '$RemoteCmd'"
    }
    $sshOpts = (Get-SshOpts) -join " "
    $target = "$($Script:CFG.SshUser)@$TargetHost"
    return Invoke-Run "ssh $sshOpts $target `"$RemoteCmd`""
}

function Invoke-RemoteBackend {
    param([string]$Cmd)
    return Invoke-Remote -TargetHost $Script:CFG.BackendHost -RemoteCmd $Cmd
}

function Invoke-RemoteFrontend {
    param([string]$Cmd)
    return Invoke-Remote -TargetHost $Script:CFG.FrontendHost -RemoteCmd $Cmd
}

# ═══════════════════════════════════════════════════════════════
#  rsync 排除列表
# ═══════════════════════════════════════════════════════════════

$Script:RSYNC_EXCLUDES = @(
    ".git", "node_modules", "web/node_modules", "web/.next",
    "__pycache__", "*.pyc", ".env", ".env.local",
    "data/", "workspace/", "users/", "*.pem",
    ".venv", "venv", ".worktrees", ".excelmanus",
    ".cursor", ".codex", ".agents", ".kiro",
    "build", "dist", "*.egg-info",
    ".pytest_cache", ".mypy_cache",
    "bench_results", "agent-transcripts", ".DS_Store", "outputs/"
)

function Get-RsyncExcludeArgs {
    return ($Script:RSYNC_EXCLUDES | ForEach-Object { "--exclude='$_'" }) -join " "
}

# ═══════════════════════════════════════════════════════════════
#  代码同步
# ═══════════════════════════════════════════════════════════════

function Sync-Code {
    param([string]$TargetHost, [string]$RemoteDir, [string]$Label)

    if ($FromLocal) {
        Write-Info "rsync sync code -> $Label ($($TargetHost ?? 'localhost'))..."
        if ($Script:CFG.Topology -eq "local") {
            Write-Debug2 "local mode, skip sync"
            return $true
        }
        $sshOpts = (Get-SshOpts) -join " "
        $excludes = Get-RsyncExcludeArgs
        $target = "$($Script:CFG.SshUser)@${TargetHost}:${RemoteDir}/"
        # Windows: 需要 rsync（通过 Git Bash/WSL/MSYS2）
        $rsyncCmd = "rsync -az --partial --append-verify --timeout=120 $excludes --progress -e `"ssh $sshOpts`" `"$Script:PROJECT_ROOT/`" `"$target`""
        return Invoke-Run $rsyncCmd
    } else {
        Write-Info "git pull -> $Label ($($TargetHost ?? 'localhost'))..."
        $gitCmd = @"
set -e
cd '$RemoteDir'
if [ ! -d .git ]; then
    echo 'repo not found, cloning...'
    cd /
    rm -rf '$RemoteDir'
    git clone '$($Script:CFG.RepoUrl)' '$RemoteDir'
    cd '$RemoteDir'
else
    git fetch '$($Script:CFG.RepoUrl)' '$($Script:CFG.Branch)' && git reset --hard FETCH_HEAD
fi
"@
        if ($Script:CFG.Topology -eq "local") {
            return Invoke-Run "bash -c `"$gitCmd`""
        }
        return Invoke-Remote -TargetHost $TargetHost -RemoteCmd $gitCmd
    }
    Write-Log "$Label code sync complete"
    return $true
}

# ═══════════════════════════════════════════════════════════════
#  前端辅助函数
# ═══════════════════════════════════════════════════════════════

function Ensure-FrontendStandaloneAssets {
    Write-Info "copy standalone static assets..."
    $cmd = @"
cd '$($Script:CFG.FrontendDir)/web' && \
if [ -d .next/standalone ]; then
    cp -r public .next/standalone/ 2>/dev/null || true
    cp -r .next/static .next/standalone/.next/ 2>/dev/null || true
    echo 'standalone assets copied'
else
    echo 'no standalone output, skipped'
fi
"@
    Invoke-RemoteFrontend $cmd | Out-Null
}

function Restart-FrontendService {
    $cfg = $Script:CFG
    if ($cfg.ServiceManager -eq "systemd") {
        Invoke-RemoteFrontend "sudo systemctl restart '$($cfg.Pm2Frontend)' 2>/dev/null || sudo systemctl start '$($cfg.Pm2Frontend)'" | Out-Null
    } elseif ($cfg.ServiceManager -eq "nssm") {
        # NSSM: Windows 原生服务管理
        Write-Info "restart frontend via NSSM..."
        Invoke-Run "nssm restart $($cfg.Pm2Frontend)" | Out-Null
    } else {
        $cmd = @"
export PATH=$($cfg.NodeBin):`$PATH && \
cd '$($cfg.FrontendDir)/web' && \
pm2 restart '$($cfg.Pm2Frontend)' 2>/dev/null || \
pm2 start .next/standalone/server.js --name '$($cfg.Pm2Frontend)' --cwd '$($cfg.FrontendDir)/web' 2>/dev/null
"@
        Invoke-RemoteFrontend $cmd | Out-Null
    }
}

function Build-FrontendRemote {
    $cfg = $Script:CFG
    $coldCmd = ""
    if ($ColdBuild) {
        Write-Warn "--cold-build: clearing .next/cache before build"
        $coldCmd = "rm -rf .next/cache && "
    } else {
        Write-Info "keeping .next/cache to reduce memory. Use -ColdBuild for clean build."
    }

    Write-Info "building frontend (npm run build)..."
    $cmd = "export PATH=$($cfg.NodeBin):`$PATH && cd '$($cfg.FrontendDir)/web' && ${coldCmd}npm run build 2>&1 | tail -10"
    if (Invoke-RemoteFrontend $cmd) { return $true }

    Write-Warn "default build failed, trying webpack fallback..."
    $cmd = "export PATH=$($cfg.NodeBin):`$PATH && cd '$($cfg.FrontendDir)/web' && ${coldCmd}npm run build:webpack 2>&1 | tail -10"
    return (Invoke-RemoteFrontend $cmd)
}

function Upload-FrontendArtifact {
    param([string]$ArtifactPath)
    $cfg = $Script:CFG
    $artifactName = Split-Path $ArtifactPath -Leaf
    $remoteDir = "$($cfg.FrontendDir)/web/.deploy/artifacts"
    $remotePath = "$remoteDir/$artifactName"

    Write-Info "uploading frontend artifact..."
    Invoke-RemoteFrontend "mkdir -p '$remoteDir'" | Out-Null

    if ($cfg.Topology -eq "local") {
        Copy-Item $ArtifactPath $remotePath -Force
    } else {
        $sshOpts = (Get-SshOpts) -join " "
        $target = "$($cfg.SshUser)@$($cfg.FrontendHost):$remotePath"
        Invoke-Run "rsync -az --partial --append-verify --timeout=120 --progress -e `"ssh $sshOpts`" `"$ArtifactPath`" `"$target`""
    }
    return $remotePath
}

function Activate-FrontendArtifact {
    param([string]$RemoteArtifact, [string]$ReleaseId)
    $cfg = $Script:CFG
    $pruneOffset = $cfg.KeepFrontendReleases + 1

    $cmd = @"
set -e
WEB_DIR='$($cfg.FrontendDir)/web'
DEPLOY_DIR="`$WEB_DIR/.deploy"
STAGE_DIR="`$DEPLOY_DIR/stage-$ReleaseId"
BACKUP_DIR="`$DEPLOY_DIR/backups/$ReleaseId"

mkdir -p "`$DEPLOY_DIR/backups" "`$STAGE_DIR"
tar -xzf '$RemoteArtifact' -C "`$STAGE_DIR"

[ -f "`$STAGE_DIR/.next/standalone/server.js" ] || { echo 'missing .next/standalone/server.js'; exit 1; }
[ -d "`$STAGE_DIR/.next/static" ] || { echo 'missing .next/static'; exit 1; }
[ -d "`$STAGE_DIR/public" ] || mkdir -p "`$STAGE_DIR/public"

mkdir -p "`$BACKUP_DIR/.next"
[ -d "`$WEB_DIR/.next/standalone" ] && mv "`$WEB_DIR/.next/standalone" "`$BACKUP_DIR/.next/standalone"
[ -d "`$WEB_DIR/.next/static" ] && mv "`$WEB_DIR/.next/static" "`$BACKUP_DIR/.next/static"
[ -d "`$WEB_DIR/public" ] && mv "`$WEB_DIR/public" "`$BACKUP_DIR/public"

mkdir -p "`$WEB_DIR/.next"
mv "`$STAGE_DIR/.next/standalone" "`$WEB_DIR/.next/standalone"
mv "`$STAGE_DIR/.next/static" "`$WEB_DIR/.next/static"
rm -rf "`$WEB_DIR/public"
mv "`$STAGE_DIR/public" "`$WEB_DIR/public"

rm -rf "`$STAGE_DIR" '$RemoteArtifact'
printf '%s' "`$BACKUP_DIR" > "`$DEPLOY_DIR/last_backup_path"

if [ -d "`$DEPLOY_DIR/backups" ]; then
    old_backups=`$(ls -1dt "`$DEPLOY_DIR"/backups/* 2>/dev/null | tail -n +$pruneOffset || true)
    if [ -n "`$old_backups" ]; then
        while IFS= read -r b; do [ -n "`$b" ] && rm -rf "`$b"; done <<< "`$old_backups"
    fi
fi
"@
    return (Invoke-RemoteFrontend $cmd)
}

function Rollback-FrontendFromLastBackup {
    $cfg = $Script:CFG
    $cmd = @"
set -e
WEB_DIR='$($cfg.FrontendDir)/web'
DEPLOY_DIR="`$WEB_DIR/.deploy"
[ -f "`$DEPLOY_DIR/last_backup_path" ] || { echo 'no backup found'; exit 1; }

BACKUP_DIR=`$(cat "`$DEPLOY_DIR/last_backup_path")
[ -d "`$BACKUP_DIR" ] || { echo 'backup dir not found'; exit 1; }

if [ ! -d "`$BACKUP_DIR/.next/standalone" ] || [ ! -d "`$BACKUP_DIR/.next/static" ]; then
    echo 'incomplete backup, keeping current'
    exit 1
fi

mkdir -p "`$WEB_DIR/.next"
rm -rf "`$WEB_DIR/.next/standalone" "`$WEB_DIR/.next/static" "`$WEB_DIR/public"

[ -d "`$BACKUP_DIR/.next/standalone" ] && mv "`$BACKUP_DIR/.next/standalone" "`$WEB_DIR/.next/standalone"
[ -d "`$BACKUP_DIR/.next/static" ] && mv "`$BACKUP_DIR/.next/static" "`$WEB_DIR/.next/static"
[ -d "`$BACKUP_DIR/public" ] && mv "`$BACKUP_DIR/public" "`$WEB_DIR/public"
"@
    return (Invoke-RemoteFrontend $cmd)
}

# ═══════════════════════════════════════════════════════════════
#  部署流程
# ═══════════════════════════════════════════════════════════════

function Deploy-Backend {
    $cfg = $Script:CFG
    Write-Step "Deploy Backend"

    Sync-Code -TargetHost $cfg.BackendHost -RemoteDir $cfg.BackendDir -Label "backend" | Out-Null

    if (-not $SkipDeps) {
        Write-Info "installing Python deps..."
        Invoke-RemoteBackend "cd '$($cfg.BackendDir)' && source '$($cfg.VenvDir)/bin/activate' && pip install -e . -q && pip install 'httpx[socks]' -q 2>/dev/null || true" | Out-Null
    }

    Write-Info "restarting backend..."
    if ($cfg.ServiceManager -eq "systemd") {
        Invoke-RemoteBackend "sudo systemctl restart '$($cfg.Pm2Backend)' 2>/dev/null || sudo systemctl start '$($cfg.Pm2Backend)'" | Out-Null
    } elseif ($cfg.ServiceManager -eq "nssm") {
        Invoke-Run "nssm restart $($cfg.Pm2Backend)" | Out-Null
    } else {
        $restartCmd = @"
export PATH=$($cfg.NodeBin):`$PATH && \
pm2 restart '$($cfg.Pm2Backend)' --update-env 2>/dev/null || \
pm2 start '$($cfg.BackendDir)/$($cfg.VenvDir)/bin/python -c "import uvicorn; uvicorn.run(\"excelmanus.api:app\", host=\"0.0.0.0\", port=$($cfg.BackendPort), log_level=\"info\")"' \
    --name '$($cfg.Pm2Backend)' --cwd '$($cfg.BackendDir)' 2>/dev/null || true
"@
        Invoke-RemoteBackend $restartCmd | Out-Null
    }
    Write-Log "backend deploy complete"
}

function Deploy-Frontend {
    $cfg = $Script:CFG
    Write-Step "Deploy Frontend"

    # 分离模式同步代码
    if ($cfg.Topology -eq "split") {
        if ($FrontendArtifact) {
            Write-Info "artifact mode, skip repo sync"
            Invoke-RemoteFrontend "mkdir -p '$($cfg.FrontendDir)/web/.deploy/artifacts'" | Out-Null
        } else {
            Sync-Code -TargetHost $cfg.FrontendHost -RemoteDir $cfg.FrontendDir -Label "frontend" | Out-Null
        }
    }

    # 制品模式
    if ($FrontendArtifact) {
        $releaseId = Get-Date -Format "yyyyMMddTHHmmss"
        $remoteArtifact = Upload-FrontendArtifact -ArtifactPath $FrontendArtifact

        Write-Info "activating frontend artifact..."
        if (-not (Activate-FrontendArtifact -RemoteArtifact $remoteArtifact -ReleaseId $releaseId)) {
            Write-Warn "activation failed, rolling back..."
            Rollback-FrontendFromLastBackup | Out-Null
            Restart-FrontendService
            Write-Err "frontend artifact deploy failed (activation)"
            return $false
        }

        Write-Info "restarting frontend..."
        if (-not (Restart-FrontendService)) {
            Write-Warn "restart failed, rolling back..."
            Rollback-FrontendFromLastBackup | Out-Null
            Restart-FrontendService
            Write-Err "frontend artifact deploy failed (restart)"
            return $false
        }

        Write-Log "frontend artifact deploy complete"
        return $true
    }

    if ($SkipBuild) {
        Write-Info "skipping build, restart only..."
        Ensure-FrontendStandaloneAssets
        Restart-FrontendService
    } else {
        if (-not $SkipDeps) {
            Write-Info "installing frontend deps..."
            Invoke-RemoteFrontend "export PATH=$($cfg.NodeBin):`$PATH && cd '$($cfg.FrontendDir)/web' && npm install --production=false 2>&1 | tail -3" | Out-Null
        }

        Write-Warn "building on remote. For low-memory servers use -FrontendArtifact."
        Build-FrontendRemote | Out-Null
        Ensure-FrontendStandaloneAssets

        Write-Info "restarting frontend..."
        Restart-FrontendService
    }
    Write-Log "frontend deploy complete"
    return $true
}

function Deploy-Docker {
    $cfg = $Script:CFG
    Write-Step "Docker Compose Deploy"

    if (-not $FromLocal -and $cfg.Topology -ne "local") {
        Sync-Code -TargetHost ($cfg.BackendHost ?? "localhost") -RemoteDir $cfg.BackendDir -Label "Docker" | Out-Null
    }

    $composeCmd = "docker compose"
    # 兼容旧版
    $dockerCheck = & docker compose version 2>&1
    if ($LASTEXITCODE -ne 0) { $composeCmd = "docker-compose" }

    $dockerCmd = "cd '$($cfg.BackendDir)' && $composeCmd pull 2>/dev/null || true && $composeCmd up -d --build --remove-orphans"

    if ($cfg.Topology -eq "local" -or -not $cfg.BackendHost) {
        Invoke-Run "bash -c `"$dockerCmd`"" | Out-Null
    } else {
        Invoke-RemoteBackend $dockerCmd | Out-Null
    }
    Write-Log "Docker deploy complete"
}

# ═══════════════════════════════════════════════════════════════
#  健康检查
# ═══════════════════════════════════════════════════════════════

function Invoke-Verify {
    if ($NoVerify -or -not $Script:CFG.HealthUrl) { return }

    Write-Step "Verify Deployment"
    Write-Info "waiting for service startup..."
    Start-Sleep -Seconds 5

    $maxAttempts = [math]::Max(1, [math]::Floor($Script:CFG.VerifyTimeout / 5))
    for ($i = 0; $i -lt $maxAttempts; $i++) {
        try {
            $response = Invoke-RestMethod -Uri $Script:CFG.HealthUrl -TimeoutSec 10 -ErrorAction Stop
            if ($response.status -eq "ok") {
                Write-Log "deploy verified! service running normally"
                if ($Script:SITE_URL) { Write-Info "URL: $Script:SITE_URL" }
                return
            }
        } catch {
            # retry
        }
        if ($i -lt ($maxAttempts - 1)) { Start-Sleep -Seconds 5 }
    }

    Write-Warn "health check failed ($($Script:CFG.HealthUrl))"
    Write-Warn "check logs on remote servers"
}

# ═══════════════════════════════════════════════════════════════
#  部署锁
# ═══════════════════════════════════════════════════════════════

function Acquire-Lock {
    if ($NoLock -or $DryRun) { return }
    $Script:LOCK_FILE = Join-Path $Script:SCRIPT_DIR ".deploy.lock"
    if (Test-Path $Script:LOCK_FILE) {
        $content = Get-Content $Script:LOCK_FILE -ErrorAction SilentlyContinue
        if ($content -and $content.Count -ge 1) {
            $lockPid = $content[0]
            $lockTime = if ($content.Count -ge 2) { $content[1] } else { "unknown" }
            try {
                Get-Process -Id $lockPid -ErrorAction Stop | Out-Null
                Write-Err "another deploy is in progress (PID: $lockPid, started: $lockTime)"
                Write-Err "delete $($Script:LOCK_FILE) or use -NoLock to force"
                exit 1
            } catch {
                Write-Warn "stale lock found (PID $lockPid gone), cleaning up..."
                Remove-Item $Script:LOCK_FILE -Force -ErrorAction SilentlyContinue
            }
        }
    }
    @($PID, (Get-Date -Format "yyyy-MM-dd HH:mm:ss")) | Set-Content $Script:LOCK_FILE
}

function Release-Lock {
    if ($Script:LOCK_FILE -and (Test-Path $Script:LOCK_FILE -ErrorAction SilentlyContinue)) {
        Remove-Item $Script:LOCK_FILE -Force -ErrorAction SilentlyContinue
    }
}

# ═══════════════════════════════════════════════════════════════
#  部署历史
# ═══════════════════════════════════════════════════════════════

function Record-DeployHistory {
    param([string]$Status)
    $historyFile = Join-Path $Script:SCRIPT_DIR ".deploy_history"
    $elapsed = if ($Script:DEPLOY_START_TIME) { [int]((Get-Date) - $Script:DEPLOY_START_TIME).TotalSeconds } else { 0 }
    $entry = "$(Get-Date -Format 'yyyy-MM-dd HH:mm:ss') | $Status | $($Script:CFG.Topology)/$($Script:CFG.Mode) | $($Script:CFG.Branch) | ${elapsed}s"
    Add-Content -Path $historyFile -Value $entry
    # 保留最近 100 条
    if (Test-Path $historyFile) {
        $lines = Get-Content $historyFile
        if ($lines.Count -gt 100) {
            $lines[-100..-1] | Set-Content $historyFile
        }
    }
}

# ═══════════════════════════════════════════════════════════════
#  Hooks
# ═══════════════════════════════════════════════════════════════

function Invoke-Hook {
    param([string]$HookName, [string]$HookScript)
    if (-not $HookScript) { return }
    if (-not (Test-Path $HookScript)) {
        Write-Err "$HookName hook not found: $HookScript"
        throw "hook not found"
    }
    Write-Step "Running $HookName hook: $HookScript"
    if ($HookScript -match '\.ps1$') {
        & $HookScript
    } else {
        Invoke-Run "bash '$HookScript'"
    }
}

# ═══════════════════════════════════════════════════════════════
#  配置摘要
# ═══════════════════════════════════════════════════════════════

function Show-Summary {
    if ($Quiet) { return }
    Write-Host ""
    Write-Host "=====================================" -ForegroundColor White
    Write-Host "  ExcelManus Deploy v$Script:VERSION (PS)" -ForegroundColor White
    Write-Host "=====================================" -ForegroundColor White
    Write-Host ""
    Write-Host "  Topology:  $($Script:CFG.Topology)" -ForegroundColor Cyan
    Write-Host "  Mode:      $($Script:CFG.Mode)" -ForegroundColor Cyan

    switch ($Script:CFG.Topology) {
        "split" {
            Write-Host "  Backend:   $($Script:CFG.SshUser)@$($Script:CFG.BackendHost):$($Script:CFG.BackendDir)" -ForegroundColor Cyan
            Write-Host "  Frontend:  $($Script:CFG.SshUser)@$($Script:CFG.FrontendHost):$($Script:CFG.FrontendDir)" -ForegroundColor Cyan
        }
        "single" {
            Write-Host "  Server:    $($Script:CFG.SshUser)@$($Script:CFG.BackendHost):$($Script:CFG.BackendDir)" -ForegroundColor Cyan
        }
        default {
            Write-Host "  Directory: $($Script:CFG.BackendDir)" -ForegroundColor Cyan
        }
    }

    $src = if ($FromLocal) { "local rsync" } else { "GitHub ($($Script:CFG.Branch))" }
    Write-Host "  Source:    $src" -ForegroundColor Cyan
    if ($FrontendArtifact) { Write-Host "  Artifact:  $FrontendArtifact" -ForegroundColor Cyan }
    if ($DryRun) { Write-Host "  !! DRY RUN MODE" -ForegroundColor Yellow }
    Write-Host ""
}

# ═══════════════════════════════════════════════════════════════
#  前置检查
# ═══════════════════════════════════════════════════════════════

function Invoke-Preflight {
    if ($FrontendArtifact -and -not (Test-Path $FrontendArtifact)) {
        Write-Err "frontend artifact not found: $FrontendArtifact"
        exit 1
    }

    if ($Script:CFG.SshKeyPath -and -not (Test-Path $Script:CFG.SshKeyPath)) {
        Write-Err "SSH key not found: $($Script:CFG.SshKeyPath)"
        exit 1
    }

    # SSH 连通性检查
    if ($Script:CFG.Topology -ne "local" -and $Script:CFG.Topology -ne "docker") {
        if ($Script:CFG.Mode -ne "frontend" -and $Script:CFG.BackendHost) {
            Write-Debug2 "checking backend connectivity..."
            $sshOpts = (Get-SshOpts) -join " "
            $test = & ssh $sshOpts.Split(" ") -o BatchMode=yes "$($Script:CFG.SshUser)@$($Script:CFG.BackendHost)" "echo ok" 2>&1
            if ($LASTEXITCODE -ne 0) {
                Write-Err "cannot connect to backend: $($Script:CFG.SshUser)@$($Script:CFG.BackendHost)"
                exit 1
            }
        }
        if ($Script:CFG.Mode -ne "backend" -and $Script:CFG.FrontendHost -and $Script:CFG.FrontendHost -ne $Script:CFG.BackendHost) {
            Write-Debug2 "checking frontend connectivity..."
            $sshOpts = (Get-SshOpts) -join " "
            $test = & ssh $sshOpts.Split(" ") -o BatchMode=yes "$($Script:CFG.SshUser)@$($Script:CFG.FrontendHost)" "echo ok" 2>&1
            if ($LASTEXITCODE -ne 0) {
                Write-Err "cannot connect to frontend: $($Script:CFG.SshUser)@$($Script:CFG.FrontendHost)"
                exit 1
            }
        }
    }
}

# ═══════════════════════════════════════════════════════════════
#  命令: check
# ═══════════════════════════════════════════════════════════════

function Invoke-CmdCheck {
    Write-Step "Check deployment prerequisites"
    $ok = $true

    function Test-Tool {
        param([string]$Name, [string]$Cmd, [bool]$Required = $true)
        $found = Get-Command $Cmd -ErrorAction SilentlyContinue
        if ($found) {
            $ver = & $Cmd --version 2>&1 | Select-Object -First 1
            Write-Log "${Name}: $ver"
        } elseif ($Required) {
            Write-Err "${Name}: not installed (required)"
            $script:ok = $false
        } else {
            Write-Warn "${Name}: not installed (optional)"
        }
    }

    Write-Host "`nLocal tools:" -ForegroundColor White
    Test-Tool "Git" "git"
    Test-Tool "SSH" "ssh"
    Test-Tool "Python" "python" $false
    Test-Tool "Node" "node" $false
    Test-Tool "Docker" "docker" $false

    # Windows 特定检查
    Write-Host "`nWindows tools:" -ForegroundColor White
    $rsyncFound = Get-Command "rsync" -ErrorAction SilentlyContinue
    if ($rsyncFound) { Write-Log "rsync: available" }
    else { Write-Warn "rsync: not found (install via Git Bash, MSYS2, or WSL)" }

    $nssmFound = Get-Command "nssm" -ErrorAction SilentlyContinue
    if ($nssmFound) { Write-Log "NSSM: available (Windows service manager)" }
    else { Write-Warn "NSSM: not found (optional, for Windows service management)" }

    Write-Host ""
    if ($ok) { Write-Log "environment check passed" }
    else { Write-Err "issues found, please fix before deploying"; return $false }
    return $true
}

# ═══════════════════════════════════════════════════════════════
#  命令: status
# ═══════════════════════════════════════════════════════════════

function Invoke-CmdStatus {
    Write-Step "Deployment Status"
    $cfg = $Script:CFG

    if ($cfg.Topology -eq "local") {
        Write-Info "local mode"
        try {
            $health = Invoke-RestMethod -Uri "http://localhost:$($cfg.BackendPort)/api/v1/health" -TimeoutSec 5 -ErrorAction Stop
            Write-Host "  Backend:  $($health.status)" -ForegroundColor Green
        } catch {
            Write-Host "  Backend:  unreachable" -ForegroundColor Red
        }
        try {
            $fe = Invoke-WebRequest -Uri "http://localhost:$($cfg.FrontendPort)" -TimeoutSec 5 -ErrorAction Stop
            Write-Host "  Frontend: HTTP $($fe.StatusCode)" -ForegroundColor Green
        } catch {
            Write-Host "  Frontend: unreachable" -ForegroundColor Red
        }
        return
    }

    if ($cfg.BackendHost) {
        Write-Host "`nBackend ($($cfg.BackendHost)):" -ForegroundColor White
        Invoke-RemoteBackend "curl -s --max-time 5 http://localhost:$($cfg.BackendPort)/api/v1/health 2>/dev/null || echo 'unreachable'" | Out-Null
        Invoke-RemoteBackend "cd '$($cfg.BackendDir)' && git log -1 --format='commit: %h %s (%cr)' 2>/dev/null || echo 'git: unavailable'" | Out-Null
    }
    if ($cfg.FrontendHost) {
        Write-Host "`nFrontend ($($cfg.FrontendHost)):" -ForegroundColor White
        Invoke-RemoteFrontend "export PATH=$($cfg.NodeBin):`$PATH && pm2 describe '$($cfg.Pm2Frontend)' 2>/dev/null | grep -E 'status|uptime|memory' || echo 'process not found'" | Out-Null
    }
}

# ═══════════════════════════════════════════════════════════════
#  命令: rollback
# ═══════════════════════════════════════════════════════════════

function Invoke-CmdRollback {
    Write-Step "Rollback Deployment"
    $cfg = $Script:CFG

    if (-not $Force) {
        $confirm = Read-Host "Confirm rollback to previous version? (y/N)"
        if ($confirm -ne "y" -and $confirm -ne "Y") {
            Write-Info "cancelled"
            return
        }
    }

    $ok = $true

    # 回滚前端
    if ($cfg.Mode -eq "full" -or $cfg.Mode -eq "frontend") {
        Write-Info "rolling back frontend..."
        if (Rollback-FrontendFromLastBackup) {
            Restart-FrontendService
            Write-Log "frontend rollback complete"
        } else {
            Write-Warn "frontend rollback failed"
            $ok = $false
        }
    }

    # 回滚后端
    if ($cfg.Mode -eq "full" -or $cfg.Mode -eq "backend") {
        Write-Info "rolling back backend (git reset --hard HEAD~1)..."
        $result = Invoke-RemoteBackend "cd '$($cfg.BackendDir)' && git log -1 --oneline && git reset --hard HEAD~1 && echo 'rolled back to:' && git log -1 --oneline"
        if (-not $result) { $ok = $false }

        if (-not $SkipDeps) {
            Write-Info "reinstalling deps..."
            Invoke-RemoteBackend "cd '$($cfg.BackendDir)' && source '$($cfg.VenvDir)/bin/activate' && pip install -e . -q 2>/dev/null || true" | Out-Null
        }

        Write-Info "restarting backend..."
        if ($cfg.ServiceManager -eq "systemd") {
            Invoke-RemoteBackend "sudo systemctl restart '$($cfg.Pm2Backend)'" | Out-Null
        } elseif ($cfg.ServiceManager -eq "nssm") {
            Invoke-Run "nssm restart $($cfg.Pm2Backend)" | Out-Null
        } else {
            Invoke-RemoteBackend "export PATH=$($cfg.NodeBin):`$PATH && pm2 restart '$($cfg.Pm2Backend)' --update-env" | Out-Null
        }
        if ($ok) { Write-Log "backend rollback complete" } else { Write-Warn "backend rollback may be incomplete" }
    }

    Invoke-Verify

    if ($ok) { Write-Log "rollback complete" }
    else { Write-Err "issues during rollback, check manually" }
}

# ═══════════════════════════════════════════════════════════════
#  前后端互联检测
# ═══════════════════════════════════════════════════════════════

function Test-CrossConnectivity {
    param([string]$Label)
    $cfg = $Script:CFG

    if ($cfg.Topology -eq "local" -or $cfg.Topology -eq "docker") { return $true }

    Write-Host "`n=== ${Label}: cross-connectivity check ===" -ForegroundColor White
    $ok = $true

    # 1) Frontend -> Backend health
    if ($cfg.FrontendHost -and $cfg.BackendHost) {
        $backendUrl = "http://$($cfg.BackendHost):$($cfg.BackendPort)/api/v1/health"
        Write-Info "Frontend($($cfg.FrontendHost)) -> Backend($($cfg.BackendHost):$($cfg.BackendPort))..."
        $result = Invoke-RemoteFrontend "curl -s --max-time 10 '$backendUrl' 2>/dev/null || echo '__UNREACHABLE__'"
        if ($result -match '"status"') {
            Write-Log "Frontend -> Backend: connected ($backendUrl)"
        } elseif ($result -match '__UNREACHABLE__') {
            Write-Warn "Frontend -> Backend: unreachable ($backendUrl)"
            Write-Warn "  Possible: backend not running / firewall / not listening on 0.0.0.0"
            $ok = $false
        } else {
            Write-Warn "Frontend -> Backend: unexpected response"
            $ok = $false
        }
    }

    # 2) Backend -> Frontend (reverse, optional)
    if ($cfg.BackendHost -and $cfg.FrontendHost -and $cfg.Topology -eq "split") {
        $frontendUrl = "http://$($cfg.FrontendHost):$($cfg.FrontendPort)"
        Write-Info "Backend($($cfg.BackendHost)) -> Frontend($($cfg.FrontendHost):$($cfg.FrontendPort))..."
        $result = Invoke-RemoteBackend "curl -s --max-time 10 -o /dev/null -w '%{http_code}' '$frontendUrl' 2>/dev/null || echo '000'"
        if ($result -match '^(200|301|302|304)$') {
            Write-Log "Backend -> Frontend: connected (HTTP $result)"
        } else {
            Write-Warn "Backend -> Frontend: unreachable (HTTP $result) - frontend may not be started yet"
        }
    }

    # 3) CORS check
    if ($cfg.BackendHost -and $Script:SITE_URL) {
        Write-Info "Checking backend CORS config..."
        $corsCheck = Invoke-RemoteBackend "grep -i 'CORS_ALLOW_ORIGINS' '$($cfg.BackendDir)/.env' 2>/dev/null || echo '__NO_CORS__'"
        if ($corsCheck -match '__NO_CORS__') {
            Write-Warn "Backend .env missing EXCELMANUS_CORS_ALLOW_ORIGINS"
        } elseif ($corsCheck -match [regex]::Escape($Script:SITE_URL)) {
            Write-Log "CORS config includes $($Script:SITE_URL)"
        } else {
            Write-Warn "CORS may not include frontend domain $($Script:SITE_URL)"
        }
    }

    # 4) Frontend BACKEND_ORIGIN check
    if ($cfg.FrontendHost) {
        Write-Info "Checking frontend BACKEND_ORIGIN config..."
        $feOrigin = Invoke-RemoteFrontend "grep -i 'NEXT_PUBLIC_BACKEND_ORIGIN\|BACKEND_INTERNAL_URL' '$($cfg.FrontendDir)/web/.env.local' '$($cfg.FrontendDir)/web/.env' 2>/dev/null || echo '__NO_ORIGIN__'"
        if ($feOrigin -match '__NO_ORIGIN__') {
            Write-Info "Frontend has no BACKEND_ORIGIN set (will use default fallback)"
        } else {
            Write-Log "Frontend backend origin: $($feOrigin.Split("`n")[0])"
        }
    }

    if (-not $ok) {
        Write-Warn "Cross-connectivity issues detected"
    }
    return $ok
}

# ═══════════════════════════════════════════════════════════════
#  命令: init-env
# ═══════════════════════════════════════════════════════════════

function Invoke-CmdInitEnv {
    Write-Step "Initialize remote .env configs"
    $cfg = $Script:CFG

    $envTemplate = Join-Path $Script:PROJECT_ROOT ".env.example"
    if (-not (Test-Path $envTemplate)) {
        Write-Err ".env.example template not found: $envTemplate"
        return
    }

    # Backend .env
    if ($cfg.Mode -ne "frontend" -and $cfg.BackendHost) {
        $beEnvPath = "$($cfg.BackendDir)/.env"
        $beExists = Invoke-RemoteBackend "[[ -f '$beEnvPath' ]] && echo 'exists' || echo 'missing'"
        if ($beExists -match 'exists') {
            if (-not $Force) {
                Write-Warn "Backend $beEnvPath exists, skipping (use -Force to overwrite)"
            } else {
                Write-Warn "Backend $beEnvPath exists, overwriting with backup..."
                Invoke-RemoteBackend "cp '$beEnvPath' '${beEnvPath}.bak.$(Get-Date -Format yyyyMMddTHHmmss)'" | Out-Null
                Push-EnvToBackend -Template $envTemplate
            }
        } else {
            Push-EnvToBackend -Template $envTemplate
        }
    }

    # Frontend .env.local
    if ($cfg.Mode -ne "backend" -and $cfg.FrontendHost) {
        $feEnvPath = "$($cfg.FrontendDir)/web/.env.local"
        $feExists = Invoke-RemoteFrontend "[[ -f '$feEnvPath' ]] && echo 'exists' || echo 'missing'"
        if ($feExists -match 'exists') {
            if (-not $Force) {
                Write-Warn "Frontend $feEnvPath exists, skipping (use -Force to overwrite)"
            } else {
                Write-Warn "Frontend $feEnvPath exists, overwriting with backup..."
                Invoke-RemoteFrontend "cp '$feEnvPath' '${feEnvPath}.bak.$(Get-Date -Format yyyyMMddTHHmmss)'" | Out-Null
                Push-EnvToFrontend
            }
        } else {
            Push-EnvToFrontend
        }
    }

    Write-Host ""
    Write-Log "init-env complete"
    Write-Info "Edit .env files on remote servers to fill in real API keys"
    if ($cfg.BackendHost)  { Write-Info "  Backend:  ssh $($cfg.SshUser)@$($cfg.BackendHost) 'vi $($cfg.BackendDir)/.env'" }
    if ($cfg.FrontendHost) { Write-Info "  Frontend: ssh $($cfg.SshUser)@$($cfg.FrontendHost) 'vi $($cfg.FrontendDir)/web/.env.local'" }
}

function Push-EnvToBackend {
    param([string]$Template)
    $cfg = $Script:CFG
    Write-Info "Pushing .env template to backend $($cfg.BackendHost):$($cfg.BackendDir)/.env ..."

    $tmpEnv = [System.IO.Path]::GetTempFileName()
    Copy-Item $Template $tmpEnv -Force

    # Auto-fill CORS if SITE_URL known
    if ($Script:SITE_URL) {
        (Get-Content $tmpEnv) -replace '^# EXCELMANUS_CORS_ALLOW_ORIGINS=.*', "EXCELMANUS_CORS_ALLOW_ORIGINS=$($Script:SITE_URL),http://localhost:3000" | Set-Content $tmpEnv
    }

    $sshOpts = (Get-SshOpts) -join " "
    Invoke-Run "rsync -az -e `"ssh $sshOpts`" `"$tmpEnv`" `"$($cfg.SshUser)@$($cfg.BackendHost):$($cfg.BackendDir)/.env`""
    Remove-Item $tmpEnv -Force -ErrorAction SilentlyContinue
    Write-Log "Backend .env pushed"
}

function Push-EnvToFrontend {
    $cfg = $Script:CFG
    Write-Info "Pushing .env.local template to frontend $($cfg.FrontendHost):$($cfg.FrontendDir)/web/.env.local ..."

    $backendOrigin = if ($Script:SITE_URL) { "same-origin" }
                     elseif ($cfg.BackendHost) { "http://$($cfg.BackendHost):$($cfg.BackendPort)" }
                     else { "http://localhost:$($cfg.BackendPort)" }

    $backendInternal = "http://$(if ($cfg.BackendHost) { $cfg.BackendHost } else { 'localhost' }):$($cfg.BackendPort)"

    $tmpEnv = [System.IO.Path]::GetTempFileName()
    @"
# ExcelManus Frontend Config
# Generated by deploy.ps1 init-env on $(Get-Date -Format 'yyyy-MM-dd HH:mm:ss')

# Backend API URL
# same-origin = via Nginx reverse proxy (recommended for production)
# http://IP:PORT = direct connection (dev / no proxy)
NEXT_PUBLIC_BACKEND_ORIGIN=$backendOrigin

# Internal backend URL (for SSR server-side rendering)
BACKEND_INTERNAL_URL=$backendInternal
"@ | Set-Content $tmpEnv -Encoding UTF8

    Invoke-RemoteFrontend "mkdir -p '$($cfg.FrontendDir)/web'" | Out-Null
    $sshOpts = (Get-SshOpts) -join " "
    Invoke-Run "rsync -az -e `"ssh $sshOpts`" `"$tmpEnv`" `"$($cfg.SshUser)@$($cfg.FrontendHost):$($cfg.FrontendDir)/web/.env.local`""
    Remove-Item $tmpEnv -Force -ErrorAction SilentlyContinue
    Write-Log "Frontend .env.local pushed"
}

# ═══════════════════════════════════════════════════════════════
#  命令: history
# ═══════════════════════════════════════════════════════════════

function Invoke-CmdHistory {
    $historyFile = Join-Path $Script:SCRIPT_DIR ".deploy_history"
    if (-not (Test-Path $historyFile)) {
        Write-Info "no deploy history yet"
        return
    }
    Write-Step "Deploy History (last 20)"
    Write-Host "Time                    | Status   | Topology/Mode  | Branch   | Duration" -ForegroundColor White
    Write-Host "------------------------+----------+----------------+----------+--------"
    Get-Content $historyFile | Select-Object -Last 20 | ForEach-Object { Write-Host $_ }
}

# ═══════════════════════════════════════════════════════════════
#  命令: logs
# ═══════════════════════════════════════════════════════════════

function Invoke-CmdLogs {
    $logDir = Join-Path $Script:SCRIPT_DIR ".deploy_logs"
    if (-not (Test-Path $logDir)) {
        Write-Info "no deploy logs yet"
        return
    }
    $latest = Get-ChildItem -Path $logDir -Filter "deploy_*.log" | Sort-Object LastWriteTime -Descending | Select-Object -First 1
    if (-not $latest) {
        Write-Info "no deploy logs yet"
        return
    }
    Write-Step "Latest deploy log: $($latest.Name)"
    Get-Content $latest.FullName | ForEach-Object { Write-Host $_ }
}

# ═══════════════════════════════════════════════════════════════
#  主流程
# ═══════════════════════════════════════════════════════════════

try {
    Load-Config
    Apply-Defaults

    # 非部署命令
    switch ($Command) {
        "check"    { Show-Summary; Invoke-CmdCheck; Test-CrossConnectivity -Label "Check"; exit }
        "status"   { Show-Summary; Invoke-CmdStatus;   exit }
        "init-env" { Show-Summary; Invoke-Preflight; Invoke-CmdInitEnv; exit }
        "history"  { Invoke-CmdHistory;                 exit }
        "logs"     { Invoke-CmdLogs;                    exit }
        "rollback" { Show-Summary; Invoke-Preflight; Invoke-CmdRollback; exit }
    }

    # deploy 命令
    Initialize-LogFile
    Show-Summary
    Invoke-Preflight
    Acquire-Lock
    $Script:DEPLOY_START_TIME = Get-Date

    Invoke-Hook -HookName "pre-deploy" -HookScript $PreDeployHook

    switch ($Script:CFG.Topology) {
        "docker" { Deploy-Docker }
        default {
            if ($Script:CFG.Mode -eq "full" -or $Script:CFG.Mode -eq "backend")  { Deploy-Backend }
            if ($Script:CFG.Mode -eq "full" -or $Script:CFG.Mode -eq "frontend") { Deploy-Frontend }
        }
    }

    Invoke-Verify
    $crossOk = Test-CrossConnectivity -Label "Post-deploy"
    if (-not $crossOk) { Write-Warn "Cross-connectivity check not fully passed, please verify config" }
    Invoke-Hook -HookName "post-deploy" -HookScript $PostDeployHook

    $elapsed = [int]((Get-Date) - $Script:DEPLOY_START_TIME).TotalSeconds
    Record-DeployHistory -Status "SUCCESS"
    Release-Lock

    Write-Host ""
    Write-Host "=====================================" -ForegroundColor White
    Write-Host "  Deploy Complete (${elapsed}s)" -ForegroundColor Green
    Write-Host "=====================================" -ForegroundColor White
    if ($Script:DEPLOY_LOG_FILE) { Write-Info "log: $Script:DEPLOY_LOG_FILE" }

} catch {
    Release-Lock
    if ($Script:DEPLOY_START_TIME) {
        $elapsed = [int]((Get-Date) - $Script:DEPLOY_START_TIME).TotalSeconds
        Record-DeployHistory -Status "FAILED"
        Write-Err "deploy failed after ${elapsed}s: $_"
    } else {
        Write-Err "deploy failed: $_"
    }
    if ($Script:DEPLOY_LOG_FILE) { Write-Warn "log: $Script:DEPLOY_LOG_FILE" }
    exit 1
}
