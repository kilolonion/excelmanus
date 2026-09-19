# 从仓库根目录读取 test.env 凭据清单，导入本次隔离主库，再跑 bench。
# 不在脚本里打印 key。
# 注意：本文件含中文注释，必须保存为 UTF-8 with BOM，
# 否则 Windows PowerShell 5.1 按 ANSI 解析会直接报语法错误（pwsh 7 不受影响）。
param(
    [string]$TestEnv = "test.env",
    [string]$Suite = "bench/cases/suite_experiential.json",
    [string]$OutputDir = "",
    [string]$Model = "",
    [string]$Wave = "",
    [string[]]$CaseId = @(),
    [double]$TurnTimeout = 0,
    # 夹具生成器：experiential / realistic / all / none（按套件文件名自动推断时留空）
    [string]$Fixtures = "",
    # 大表夹具行数（realistic）；调试时可减小
    [int]$BigRows = 60000,
    # 夹具随机种子（realistic）；nightly 传别的 seed 生成同构异值夹具防过拟合
    [int]$Seed = 20240914,
    # 严格模式：效率预算超限也判 fail（默认只告警）
    [switch]$Strict
)

$ErrorActionPreference = "Stop"
$root = Split-Path -Parent $PSScriptRoot
Set-Location $root

if (-not (Test-Path $TestEnv)) {
    throw "找不到 $TestEnv"
}

$url = $null
$key = $null
Get-Content -LiteralPath $TestEnv -Encoding UTF8 | ForEach-Object {
    $line = $_.Trim()
    if (-not $line -or $line.StartsWith("#")) { return }
    $parts = $line.Split("=", 2)
    if ($parts.Count -ne 2) { return }
    $name = $parts[0].Trim()
    $value = $parts[1].Trim()
    if ($name -eq "url") { $url = $value }
    elseif ($name -eq "key") { $key = $value }
    elseif ($name -eq "EXCELMANUS_BASE_URL") { $url = $value }
    elseif ($name -eq "EXCELMANUS_API_KEY") { $key = $value }
    elseif (($name -eq "model" -or $name -eq "EXCELMANUS_MODEL") -and -not $Model) { $Model = $value }
}

if (-not $url -or -not $key) {
    throw "$TestEnv 需要 url/key 或 EXCELMANUS_BASE_URL/EXCELMANUS_API_KEY"
}

$base = $url.TrimEnd("/")
if ($base.ToLower().EndsWith("/chat/completions")) {
    $base = $base.Substring(0, $base.Length - "/chat/completions".Length)
}
if (-not $base.ToLower().EndsWith("/v1")) {
    $base = "$base/v1"
}

$env:EXCELMANUS_BASE_URL = $base
$env:EXCELMANUS_API_KEY = $key
$env:EXCELMANUS_PROTOCOL = "openai"
if ($Model) {
    $env:EXCELMANUS_MODEL = $Model
}
elseif (-not $env:EXCELMANUS_MODEL) {
    $env:EXCELMANUS_MODEL = "mimo-v2.5-pro"
}

# 输出目录：未显式给 -OutputDir 时，按套件名（去掉 suite_ 前缀）落到 outputs/<suite>/，
# 再按用例 wave 标签落到 wave-N 子目录
if (-not $OutputDir) {
    $suiteStem = [System.IO.Path]::GetFileNameWithoutExtension($Suite)
    if ($suiteStem.StartsWith("suite_")) { $suiteStem = $suiteStem.Substring(6) }
    $OutputDir = "outputs/$suiteStem"
    $waveTag = ""
    if ($Wave) {
        $waveTag = "wave-$Wave"
    }
    elseif ($CaseId.Count -gt 0 -and (Test-Path $Suite)) {
        try {
            $suiteData = Get-Content -LiteralPath $Suite -Raw -Encoding UTF8 | ConvertFrom-Json
            $waveTags = @{}
            foreach ($c in $suiteData.cases) {
                if ($CaseId -contains $c.id) {
                    foreach ($t in @($c.tags)) {
                        if ($t -match '^wave-[A-Za-z0-9]+$') { $waveTags[$t] = $true }
                    }
                }
            }
            if ($waveTags.Count -eq 1) {
                $waveTag = @($waveTags.Keys)[0]
            }
        }
        catch {
            Write-Warning "读取 $Suite 判断 wave 目录失败，用根输出目录: $_"
        }
    }
    if ($waveTag) {
        $OutputDir = Join-Path $OutputDir $waveTag
    }
}

$homeDir = Join-Path $root "$OutputDir\runtime-home"
New-Item -ItemType Directory -Force -Path $homeDir | Out-Null
$env:EXCELMANUS_HOME = $homeDir

$python = Join-Path $root ".venv\Scripts\python.exe"
if (-not (Test-Path $python)) {
    $uv = Get-Command uv -ErrorAction SilentlyContinue
    if ($uv) {
        $python = "uv"
    } else {
        throw "找不到 .venv\Scripts\python.exe，也找不到 uv"
    }
}

function Invoke-EvalPython {
    param([Parameter(ValueFromRemainingArguments = $true)][string[]]$PyArgs)
    if ($python -eq "uv") {
        & uv run python @PyArgs
    } else {
        & $python @PyArgs
    }
    if ($LASTEXITCODE -ne 0) {
        throw "python 退出码 $LASTEXITCODE : $($PyArgs -join ' ')"
    }
}

if (-not $Fixtures) {
    if ($Suite -match 'realistic') { $Fixtures = "realistic" }
    elseif ($Suite -match 'experiential') { $Fixtures = "experiential" }
    else { $Fixtures = "none" }
}
if ($Fixtures -eq "experiential" -or $Fixtures -eq "all") {
    Write-Host "生成体验夹具..."
    Invoke-EvalPython bench/fixtures/build_experiential.py
}
if ($Fixtures -eq "realistic" -or $Fixtures -eq "all") {
    Write-Host "生成真实场景夹具（大表 $BigRows 行，种子 $Seed）..."
    Invoke-EvalPython bench/fixtures/build_realistic.py "$BigRows" "$Seed"
}

$benchArgs = @('-m', 'excelmanus.bench', '--suite', $Suite, '--output-dir', $OutputDir)
if ($Wave) { $benchArgs += @('--wave', $Wave) }
foreach ($id in $CaseId) { $benchArgs += @('--case', $id) }
if ($TurnTimeout -gt 0) { $benchArgs += @('--turn-timeout', "$TurnTimeout") }
if ($Strict) { $benchArgs += @('--strict-efficiency') }

Write-Host "运行 $Suite  model=$($env:EXCELMANUS_MODEL)  out=$OutputDir"
Invoke-EvalPython @benchArgs
