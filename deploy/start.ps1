#Requires -Version 5.1
<#
.SYNOPSIS
    ExcelManus 一键启动脚本 (Windows PowerShell 版本)
    同时启动 FastAPI 后端 + Next.js 前端（开发或生产模式）

.DESCRIPTION
    用法:  .\deploy\start.ps1 [选项]

    选项:
      -Production            生产模式（npm run start 代替 npm run dev）
      -BackendOnly           仅启动后端
      -FrontendOnly          仅启动前端
      -BackendPort PORT      后端端口（默认 8000）
      -FrontendPort PORT     前端端口（默认 3000）
      -ListenHost HOST       后端监听地址（默认 0.0.0.0）
      -Workers N             后端 uvicorn worker 数量（默认 1）
      -SkipDeps              跳过依赖检查与自动安装
      -NoOpen                不自动打开浏览器
      -LogDir DIR            日志输出目录（默认 不写日志）
      -HealthTimeout SEC     后端健康检查超时秒数（默认 30）
      -NoKillPorts           不清理残留端口
      -Verbose               详细输出

.EXAMPLE
    .\deploy\start.ps1
    开发模式默认启动

.EXAMPLE
    .\deploy\start.ps1 -Production
    生产模式启动

.EXAMPLE
    .\deploy\start.ps1 -BackendPort 9000
    自定义后端端口

.EXAMPLE
    .\deploy\start.ps1 -BackendOnly
    仅启动后端

.EXAMPLE
    .\deploy\start.ps1 -Production -Workers 4 -LogDir .\logs
    生产模式 4 workers，日志输出到 .\logs
#>

[CmdletBinding()]
param(
    [switch]$Production,
    [switch]$BackendOnly,
    [switch]$FrontendOnly,
    [int]$BackendPort = 0,
    [int]$FrontendPort = 0,
    [string]$ListenHost = "",
    [int]$Workers = 0,
    [switch]$SkipDeps,
    [switch]$NoOpen,
    [string]$LogDir = "",
    [int]$HealthTimeout = 0,
    [switch]$NoKillPorts,
    [switch]$ShowVersion
)

# ═══════════════════════════════════════════════════════════════
#  常量与路径
# ═══════════════════════════════════════════════════════════════

$Script:VERSION = "2.0.0"
$Script:SCRIPT_DIR = Split-Path -Parent $MyInvocation.MyCommand.Path
$Script:PROJECT_ROOT = Split-Path -Parent $Script:SCRIPT_DIR
$Script:LOG_FILE = ""
$Script:BackendProcess = $null
$Script:FrontendProcess = $null

if ($ShowVersion) {
    Write-Output "ExcelManus Start v$($Script:VERSION) (PowerShell)"
    exit 0
}

# ═══════════════════════════════════════════════════════════════
#  日志函数
# ═══════════════════════════════════════════════════════════════

function Write-LogToFile {
    param([string]$Message)
    if ($Script:LOG_FILE -and (Test-Path (Split-Path $Script:LOG_FILE -Parent) -ErrorAction SilentlyContinue)) {
        $ts = Get-Date -Format "HH:mm:ss"
        Add-Content -Path $Script:LOG_FILE -Value "[$ts] $Message" -ErrorAction SilentlyContinue
    }
}

function Write-Log {
    param([string]$Message)
    Write-LogToFile "OK  $Message"
    Write-Host "[OK] $Message" -ForegroundColor Green
}

function Write-Info {
    param([string]$Message)
    Write-LogToFile "INF $Message"
    Write-Host "[--] $Message" -ForegroundColor Cyan
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

function Write-Dbg {
    param([string]$Message)
    Write-LogToFile "DBG $Message"
    if ($VerbosePreference -eq "Continue") {
        Write-Host "[..] $Message" -ForegroundColor DarkCyan
    }
}

# ═══════════════════════════════════════════════════════════════
#  GitHub 仓库配置
# ═══════════════════════════════════════════════════════════════

$Script:REPO_URL = "https://github.com/kilolonion/excelmanus"
$Script:REPO_BRANCH = "main"

# ═══════════════════════════════════════════════════════════════
#  检查项目完整性（缺失则从 GitHub 克隆）
# ═══════════════════════════════════════════════════════════════

$pyprojectPath = Join-Path $Script:PROJECT_ROOT "pyproject.toml"
if (-not (Test-Path $pyprojectPath)) {
    Write-Host "[!!] 未检测到完整项目文件" -ForegroundColor Yellow
    if (-not (Get-Command git -ErrorAction SilentlyContinue)) {
        Write-Host "[XX] 未找到 Git，请先安装: https://git-scm.com/" -ForegroundColor Red
        Write-Host "     或手动下载项目: $($Script:REPO_URL)" -ForegroundColor Red
        exit 1
    }
    Write-Host "[--] 正在从 GitHub 克隆项目..." -ForegroundColor Cyan
    Write-Host "     仓库: $($Script:REPO_URL)" -ForegroundColor Cyan
    Write-Host "     分支: $($Script:REPO_BRANCH)" -ForegroundColor Cyan
    $tmpDir = "$($Script:PROJECT_ROOT)_tmp"
    & git clone -b $Script:REPO_BRANCH $Script:REPO_URL $tmpDir
    if ($LASTEXITCODE -ne 0) {
        Write-Host "[XX] Git 克隆失败，请检查网络连接" -ForegroundColor Red
        exit 1
    }
    Copy-Item -Path "$tmpDir\*" -Destination $Script:PROJECT_ROOT -Recurse -Force
    Remove-Item -Path $tmpDir -Recurse -Force -ErrorAction SilentlyContinue
    Write-Host "[OK] 项目已从 GitHub 克隆完成" -ForegroundColor Green
}

# ═══════════════════════════════════════════════════════════════
#  交互式 .env 配置（首次启动）
# ═══════════════════════════════════════════════════════════════

$envFilePath = Join-Path $Script:PROJECT_ROOT ".env"
if (-not (Test-Path $envFilePath)) {
    Write-Host ""
    Write-Host "  ========================================" -ForegroundColor Cyan
    Write-Host "    首次启动 - 配置 ExcelManus" -ForegroundColor Cyan
    Write-Host "  ========================================" -ForegroundColor Cyan
    Write-Host ""
    Write-Host "  需要配置 LLM API 信息才能使用。" -ForegroundColor White
    Write-Host "  （直接按回车可跳过，稍后手动编辑 .env 文件）" -ForegroundColor DarkGray
    Write-Host ""
    $inputApiKey = Read-Host "  API Key"
    $inputBaseUrl = Read-Host "  Base URL (例: https://api.openai.com/v1)"
    $inputModel = Read-Host "  Model (例: gpt-4o)"
    Write-Host ""
    if ([string]::IsNullOrWhiteSpace($inputApiKey)) {
        Write-Host "[!!] 未填写 API Key，创建空模板 .env 文件" -ForegroundColor Yellow
        Write-Host "[!!] 请稍后编辑 $envFilePath 填入配置" -ForegroundColor Yellow
        @"
# ExcelManus Configuration
# Please fill in your LLM API settings
EXCELMANUS_API_KEY=your-api-key
EXCELMANUS_BASE_URL=https://your-llm-endpoint/v1
EXCELMANUS_MODEL=your-model-id
"@ | Set-Content -Path $envFilePath -Encoding UTF8
    } else {
        @"
# ExcelManus Configuration
EXCELMANUS_API_KEY=$inputApiKey
EXCELMANUS_BASE_URL=$inputBaseUrl
EXCELMANUS_MODEL=$inputModel
"@ | Set-Content -Path $envFilePath -Encoding UTF8
        Write-Host "[OK] .env 配置文件已创建" -ForegroundColor Green
    }
    Write-Host ""
}

# ═══════════════════════════════════════════════════════════════
#  互斥检查
# ═══════════════════════════════════════════════════════════════

if ($BackendOnly -and $FrontendOnly) {
    Write-Err "-BackendOnly 与 -FrontendOnly 不能同时使用"
    exit 1
}

# ═══════════════════════════════════════════════════════════════
#  加载 .env
# ═══════════════════════════════════════════════════════════════

function Load-EnvFile {
    param([string]$FilePath)
    if (Test-Path $FilePath) {
        Write-Dbg "加载环境变量: $FilePath"
        Get-Content $FilePath | ForEach-Object {
            $line = $_.Trim()
            if ($line -and -not $line.StartsWith("#")) {
                $parts = $line -split "=", 2
                if ($parts.Count -eq 2) {
                    $key = $parts[0].Trim()
                    $val = $parts[1].Trim()
                    # 去掉可能的引号
                    $val = $val -replace '^["'']|["'']$', ''
                    [System.Environment]::SetEnvironmentVariable($key, $val, "Process")
                }
            }
        }
    }
}

# 优先级: .env.local > .env
Load-EnvFile (Join-Path $Script:PROJECT_ROOT ".env")
Load-EnvFile (Join-Path $Script:PROJECT_ROOT ".env.local")

# ═══════════════════════════════════════════════════════════════
#  应用默认值（命令行 > 环境变量 > 默认）
# ═══════════════════════════════════════════════════════════════

if ($BackendPort -eq 0) {
    $envPort = [System.Environment]::GetEnvironmentVariable("EXCELMANUS_BACKEND_PORT")
    $BackendPort = if ($envPort) { [int]$envPort } else { 8000 }
}
if ($FrontendPort -eq 0) {
    $envPort = [System.Environment]::GetEnvironmentVariable("EXCELMANUS_FRONTEND_PORT")
    $FrontendPort = if ($envPort) { [int]$envPort } else { 3000 }
}
if (-not $ListenHost) { $ListenHost = "0.0.0.0" }
if ($Workers -eq 0)   { $Workers = 1 }
if ($HealthTimeout -eq 0) { $HealthTimeout = 30 }

# ═══════════════════════════════════════════════════════════════
#  初始化日志文件
# ═══════════════════════════════════════════════════════════════

if ($LogDir) {
    if (-not (Test-Path $LogDir)) { New-Item -ItemType Directory -Path $LogDir -Force | Out-Null }
    $ts = Get-Date -Format "yyyyMMddTHHmmss"
    $Script:LOG_FILE = Join-Path $LogDir "start_$ts.log"
    Set-Content -Path $Script:LOG_FILE -Value "# ExcelManus Start - $(Get-Date -Format 'yyyy-MM-dd HH:mm:ss')"
    Write-Info "日志输出到: $($Script:LOG_FILE)"
}

# ═══════════════════════════════════════════════════════════════
#  依赖检查
# ═══════════════════════════════════════════════════════════════

function Test-CommandExists {
    param([string]$Cmd, [string]$Label, [string]$Hint)
    if (-not (Get-Command $Cmd -ErrorAction SilentlyContinue)) {
        Write-Err "未找到 $Label ($Cmd)，请安装: $Hint"
        return $false
    }
    return $true
}

function Get-PipMirror {
    return @{ Url = "https://pypi.tuna.tsinghua.edu.cn/simple"; Host = "pypi.tuna.tsinghua.edu.cn" }
}

function Invoke-PipInstall {
    param([string[]]$PipArgs)
    if ($Script:PipMirror) {
        $mirrorArgs = $PipArgs + @("-i", $Script:PipMirror.Url, "--trusted-host", $Script:PipMirror.Host)
        & $Script:PythonBin -m pip install @mirrorArgs
        if ($LASTEXITCODE -eq 0) { return }
        Write-Warn "镜像安装失败, 尝试默认源..."
    }
    & $Script:PythonBin -m pip install @PipArgs
}

function Test-Dependencies {
    $ok = $true

    # ── pip 镜像（默认清华，失败回退 PyPI） ──
    $Script:PipMirror = Get-PipMirror

    # ── Python / venv ──
    if (-not $FrontendOnly) {
        # 查找 Python 解释器：优先 .venv，其次系统
        $venvPython = $null
        $venvDir = Join-Path $Script:PROJECT_ROOT ".venv"

        # Windows venv 路径
        $candidates = @(
            (Join-Path $venvDir "Scripts\python.exe"),
            (Join-Path $venvDir "bin\python.exe"),
            (Join-Path $venvDir "bin\python")
        )
        foreach ($c in $candidates) {
            if (Test-Path $c) { $venvPython = $c; break }
        }

        if ($venvPython) {
            $pyVer = & $venvPython --version 2>&1
            Write-Dbg "Python: $pyVer ($venvPython)"
            $Script:PythonBin = $venvPython
        } elseif (Test-Path $venvDir) {
            Write-Err ".venv 目录存在但未找到 python 可执行文件"
            $ok = $false
        } else {
            # 尝试用 uv 或 python 创建 venv
            if (Get-Command uv -ErrorAction SilentlyContinue) {
                Write-Warn "未找到 .venv 虚拟环境"
                Write-Info "检测到 uv，尝试自动创建虚拟环境并安装依赖..."
                try {
                    Push-Location $Script:PROJECT_ROOT
                    & uv venv .venv
                    # 重新检测
                    foreach ($c in $candidates) {
                        if (Test-Path $c) { $venvPython = $c; break }
                    }
                    if ($venvPython) {
                        $Script:PythonBin = $venvPython
                        Invoke-PipInstall @("-e", ".[all]")
                    } else {
                        Write-Err "uv venv 创建成功但未找到 python"
                        $ok = $false
                    }
                } catch {
                    Write-Err "自动创建虚拟环境失败: $_"
                    $ok = $false
                } finally {
                    Pop-Location
                }
            } elseif (Get-Command python -ErrorAction SilentlyContinue) {
                Write-Warn "未找到 .venv，尝试用系统 python 创建..."
                try {
                    Push-Location $Script:PROJECT_ROOT
                    & python -m venv .venv
                    foreach ($c in $candidates) {
                        if (Test-Path $c) { $venvPython = $c; break }
                    }
                    if ($venvPython) {
                        $Script:PythonBin = $venvPython
                        Invoke-PipInstall @("-e", ".[all]")
                    }
                } catch {
                    Write-Err "创建虚拟环境失败，请手动运行: python -m venv .venv"
                    $ok = $false
                } finally {
                    Pop-Location
                }
            } else {
                Write-Err "未找到 .venv 且无可用 Python，请先安装 Python 3.10+ 并运行: python -m venv .venv && pip install -e '.[all]'"
                $ok = $false
            }
        }

        # 检查项目依赖是否已安装
        if ($Script:PythonBin -and (Test-Path $Script:PythonBin)) {
            & $Script:PythonBin -c "import fastapi; import uvicorn; import rich" 2>&1 | Out-Null
            if ($LASTEXITCODE -ne 0) {
                Write-Info "正在安装项目依赖（首次启动可能需要几分钟）..."
                try {
                    Push-Location $Script:PROJECT_ROOT
                    Invoke-PipInstall @("-e", ".[all]")
                    if ($LASTEXITCODE -ne 0) {
                        Write-Err "项目依赖安装失败"
                        $ok = $false
                    } else {
                        Write-Log "项目依赖已安装"
                    }
                } finally {
                    Pop-Location
                }
            }
        }
    }

    # ── Node.js / npm ──
    if (-not $BackendOnly) {
        if (-not (Test-CommandExists "node" "Node.js" "https://nodejs.org/")) { $ok = $false }
        if (-not (Test-CommandExists "npm" "npm" "https://nodejs.org/")) { $ok = $false }

        if (Get-Command node -ErrorAction SilentlyContinue) {
            $nodeVer = & node --version 2>&1
            Write-Dbg "Node.js: $nodeVer"
        }

        # web/node_modules
        $webDir = Join-Path $Script:PROJECT_ROOT "web"
        $nodeModules = Join-Path $webDir "node_modules"
        if (-not (Test-Path $nodeModules)) {
            Write-Info "首次启动，安装前端依赖..."
            Push-Location $webDir
            & npm install
            if ($LASTEXITCODE -ne 0) { Write-Err "npm install 失败"; $ok = $false }
            Pop-Location
        }

        # 生产模式需要先构建
        $nextDir = Join-Path $webDir ".next"
        if ($Production -and -not (Test-Path $nextDir)) {
            Write-Info "生产模式首次启动，构建前端..."
            Push-Location $webDir
            & npm run build
            if ($LASTEXITCODE -ne 0) { Write-Err "npm run build 失败"; $ok = $false }
            Pop-Location
        }
    }

    return $ok
}

if (-not $SkipDeps) {
    if (-not (Test-Dependencies)) {
        Write-Err "依赖检查失败，无法启动"
        exit 1
    }
}

# 如果跳过依赖检查，仍需定位 Python
if (-not $Script:PythonBin) {
    $venvDir = Join-Path $Script:PROJECT_ROOT ".venv"
    $candidates = @(
        (Join-Path $venvDir "Scripts\python.exe"),
        (Join-Path $venvDir "bin\python.exe"),
        (Join-Path $venvDir "bin\python")
    )
    foreach ($c in $candidates) {
        if (Test-Path $c) { $Script:PythonBin = $c; break }
    }
    if (-not $Script:PythonBin) { $Script:PythonBin = "python" }
}

# ═══════════════════════════════════════════════════════════════
#  启动信息
# ═══════════════════════════════════════════════════════════════

$modeLabel = if ($Production) { "生产" } else { "开发" }
Write-Host ""
Write-Host "  ExcelManus 启动中..." -ForegroundColor Green
Write-Host "  模式: $modeLabel" -ForegroundColor White
Write-Host ""

# ═══════════════════════════════════════════════════════════════
#  清理残留端口
# ═══════════════════════════════════════════════════════════════

function Stop-PortProcess {
    param([int]$Port)
    try {
        $connections = Get-NetTCPConnection -LocalPort $Port -ErrorAction SilentlyContinue
        if ($connections) {
            $pids = $connections | Select-Object -ExpandProperty OwningProcess -Unique
            foreach ($pid in $pids) {
                if ($pid -and $pid -ne 0) {
                    $proc = Get-Process -Id $pid -ErrorAction SilentlyContinue
                    if ($proc) {
                        Write-Warn "端口 $Port 被占用 (PID $pid - $($proc.ProcessName))，正在清理..."
                        # 优雅关闭
                        $proc.CloseMainWindow() | Out-Null
                        if (-not $proc.WaitForExit(3000)) {
                            # 强制终止
                            Stop-Process -Id $pid -Force -ErrorAction SilentlyContinue
                        }
                    }
                }
            }
            Start-Sleep -Seconds 1
        }
    } catch {
        # Get-NetTCPConnection 在某些环境不可用，fallback 到 netstat
        try {
            $lines = netstat -ano | Select-String ":$Port\s"
            foreach ($line in $lines) {
                if ($line -match '\s(\d+)$') {
                    $pid = [int]$Matches[1]
                    if ($pid -and $pid -ne 0) {
                        Write-Warn "端口 $Port 被占用 (PID $pid)，正在清理..."
                        Stop-Process -Id $pid -Force -ErrorAction SilentlyContinue
                    }
                }
            }
            Start-Sleep -Seconds 1
        } catch {
            Write-Dbg "无法检查端口 $Port 占用: $_"
        }
    }
}

if (-not $NoKillPorts) {
    if (-not $FrontendOnly) { Stop-PortProcess $BackendPort }
    if (-not $BackendOnly)  { Stop-PortProcess $FrontendPort }
}

# ═══════════════════════════════════════════════════════════════
#  启动后端
# ═══════════════════════════════════════════════════════════════

function Start-Backend {
    Write-Info "启动 FastAPI 后端 (${ListenHost}:${BackendPort})..."

    $uvicornCmd = if ($Workers -gt 1) {
        "import uvicorn; uvicorn.run('excelmanus.api:app', host='$ListenHost', port=$BackendPort, log_level='info', workers=$Workers)"
    } else {
        "import uvicorn; uvicorn.run('excelmanus.api:app', host='$ListenHost', port=$BackendPort, log_level='info')"
    }

    $startInfo = New-Object System.Diagnostics.ProcessStartInfo
    $startInfo.FileName = $Script:PythonBin
    $startInfo.Arguments = "-c `"$uvicornCmd`""
    $startInfo.WorkingDirectory = $Script:PROJECT_ROOT
    $startInfo.UseShellExecute = $false

    if ($LogDir) {
        $backendLog = Join-Path $LogDir "backend.log"
        $startInfo.RedirectStandardOutput = $true
        $startInfo.RedirectStandardError = $true
    }

    try {
        $proc = [System.Diagnostics.Process]::Start($startInfo)
        $Script:BackendProcess = $proc

        if ($LogDir) {
            # Use async event handlers instead of Start-Job (which serializes the Process object)
            Register-ObjectEvent -InputObject $proc -EventName OutputDataReceived -Action {
                if ($EventArgs.Data) { Add-Content -Path $Event.MessageData -Value $EventArgs.Data }
            } -MessageData $backendLog | Out-Null
            Register-ObjectEvent -InputObject $proc -EventName ErrorDataReceived -Action {
                if ($EventArgs.Data) { Add-Content -Path $Event.MessageData -Value "[ERR] $($EventArgs.Data)" }
            } -MessageData $backendLog | Out-Null
            $proc.BeginOutputReadLine()
            $proc.BeginErrorReadLine()
        }

        Write-Dbg "后端进程已启动 (PID $($proc.Id))"
    } catch {
        Write-Err "后端启动失败: $_"
        exit 1
    }

    # 等待健康检查
    $ready = $false
    for ($i = 0; $i -lt $HealthTimeout; $i++) {
        try {
            $response = Invoke-RestMethod -Uri "http://localhost:${BackendPort}/api/v1/health" -TimeoutSec 3 -ErrorAction Stop
            if ($response.status -eq "ok" -or $response) {
                Write-Log "后端已就绪 (PID $($proc.Id))"
                $ready = $true
                break
            }
        } catch {
            # 检查进程是否已退出
            if ($proc.HasExited) {
                Write-Err "后端启动失败（退出码 $($proc.ExitCode)），请检查配置（.env 文件）"
                if ($LogDir) { Write-Err "查看日志: $(Join-Path $LogDir 'backend.log')" }
                exit 1
            }
        }
        Start-Sleep -Seconds 1
    }

    if (-not $ready) {
        Write-Err "后端启动超时（${HealthTimeout}s）"
        exit 1
    }
}

# ═══════════════════════════════════════════════════════════════
#  启动前端
# ═══════════════════════════════════════════════════════════════

function Start-Frontend {
    $npmCmd = if ($Production) { "start" } else { "dev" }
    $label = if ($Production) { "start" } else { "dev" }

    Write-Info "启动 Next.js 前端 [$label] (端口 ${FrontendPort})..."

    $webDir = Join-Path $Script:PROJECT_ROOT "web"

    $startInfo = New-Object System.Diagnostics.ProcessStartInfo
    # npm is npm.cmd on Windows; .cmd files need cmd.exe to execute with UseShellExecute=false
    $startInfo.FileName = "cmd.exe"
    $startInfo.Arguments = "/c npm run $npmCmd -- -p $FrontendPort"
    $startInfo.WorkingDirectory = $webDir
    $startInfo.UseShellExecute = $false

    if ($LogDir) {
        $frontendLog = Join-Path $LogDir "frontend.log"
        $startInfo.RedirectStandardOutput = $true
        $startInfo.RedirectStandardError = $true
    }

    try {
        $proc = [System.Diagnostics.Process]::Start($startInfo)
        $Script:FrontendProcess = $proc

        if ($LogDir) {
            Register-ObjectEvent -InputObject $proc -EventName OutputDataReceived -Action {
                if ($EventArgs.Data) { Add-Content -Path $Event.MessageData -Value $EventArgs.Data }
            } -MessageData $frontendLog | Out-Null
            Register-ObjectEvent -InputObject $proc -EventName ErrorDataReceived -Action {
                if ($EventArgs.Data) { Add-Content -Path $Event.MessageData -Value "[ERR] $($EventArgs.Data)" }
            } -MessageData $frontendLog | Out-Null
            $proc.BeginOutputReadLine()
            $proc.BeginErrorReadLine()
        }

        Write-Dbg "前端进程已启动 (PID $($proc.Id))"
    } catch {
        Write-Err "前端启动失败: $_"
        exit 1
    }
}

# ═══════════════════════════════════════════════════════════════
#  优雅关闭
# ═══════════════════════════════════════════════════════════════

function Stop-AllServices {
    Write-Host ""
    Write-Host "[--] 正在关闭服务..." -ForegroundColor Cyan

    $processes = @()
    if ($Script:FrontendProcess -and -not $Script:FrontendProcess.HasExited) {
        $processes += $Script:FrontendProcess
    }
    if ($Script:BackendProcess -and -not $Script:BackendProcess.HasExited) {
        $processes += $Script:BackendProcess
    }

    # 第一阶段：优雅关闭
    foreach ($proc in $processes) {
        try {
            $proc.CloseMainWindow() | Out-Null
            # 发送 Ctrl+C 信号
            if (-not $proc.HasExited) {
                Stop-Process -Id $proc.Id -ErrorAction SilentlyContinue
            }
        } catch {}
    }

    # 等待最多 5 秒
    $deadline = (Get-Date).AddSeconds(5)
    $allDone = $false
    while ((Get-Date) -lt $deadline -and -not $allDone) {
        $allDone = $true
        foreach ($proc in $processes) {
            if (-not $proc.HasExited) { $allDone = $false; break }
        }
        if (-not $allDone) { Start-Sleep -Milliseconds 500 }
    }

    # 第二阶段：强制终止
    foreach ($proc in $processes) {
        if (-not $proc.HasExited) {
            Write-Dbg "进程 $($proc.Id) 未响应，强制终止"
            try { $proc | Stop-Process -Force -ErrorAction SilentlyContinue } catch {}
        }
    }

    # 清理子进程（npm 会产生 node 子进程）
    foreach ($port in @($BackendPort, $FrontendPort)) {
        try {
            $conns = Get-NetTCPConnection -LocalPort $port -ErrorAction SilentlyContinue
            foreach ($conn in $conns) {
                if ($conn.OwningProcess -and $conn.OwningProcess -ne 0) {
                    Stop-Process -Id $conn.OwningProcess -Force -ErrorAction SilentlyContinue
                }
            }
        } catch {}
    }

    Write-Host "[OK] 已关闭" -ForegroundColor Green
    if ($Script:LOG_FILE) { Write-Info "日志已保存到: $($Script:LOG_FILE)" }
}

# 注册 Ctrl+C 处理
$null = Register-EngineEvent -SourceIdentifier PowerShell.Exiting -Action { Stop-AllServices } -ErrorAction SilentlyContinue

try {
    [Console]::TreatControlCAsInput = $false
} catch {}

# ═══════════════════════════════════════════════════════════════
#  主流程
# ═══════════════════════════════════════════════════════════════

if (-not $FrontendOnly) {
    Start-Backend
}

if (-not $BackendOnly) {
    Start-Frontend
}

# 等待前端启动
Start-Sleep -Seconds 3

# ── 自动打开浏览器 ──
if (-not $NoOpen -and -not $BackendOnly) {
    $url = "http://localhost:${FrontendPort}"
    try { Start-Process $url } catch { Write-Dbg "无法自动打开浏览器: $_" }
}

# ── 启动摘要 ──
Write-Host ""
Write-Host "  ========================================" -ForegroundColor Green
Write-Host "    ExcelManus 已启动！" -ForegroundColor Green
Write-Host "    模式: $modeLabel" -ForegroundColor Green
if (-not $BackendOnly)  { Write-Host "    前端: http://localhost:${FrontendPort}" -ForegroundColor Green }
if (-not $FrontendOnly) { Write-Host "    后端: http://localhost:${BackendPort}" -ForegroundColor Green }
if ($LogDir)            { Write-Host "    日志: $LogDir" -ForegroundColor Green }
Write-Host "    按 Ctrl+C 停止所有服务" -ForegroundColor Green
Write-Host "  ========================================" -ForegroundColor Green
Write-Host ""

# ── 等待进程退出 ──
try {
    $watchProcesses = @()
    if ($Script:BackendProcess -and -not $Script:BackendProcess.HasExited) {
        $watchProcesses += $Script:BackendProcess
    }
    if ($Script:FrontendProcess -and -not $Script:FrontendProcess.HasExited) {
        $watchProcesses += $Script:FrontendProcess
    }

    if ($watchProcesses.Count -gt 0) {
        Write-Dbg "监控 $($watchProcesses.Count) 个进程..."
        while ($true) {
            $anyAlive = $false
            foreach ($proc in $watchProcesses) {
                if (-not $proc.HasExited) { $anyAlive = $true; break }
            }
            if (-not $anyAlive) {
                Write-Warn "所有服务进程已退出"
                break
            }
            Start-Sleep -Seconds 2
        }
    }
} catch {
    # Ctrl+C 触发
} finally {
    Stop-AllServices
}
