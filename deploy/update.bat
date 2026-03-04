@echo off
chcp 65001 >nul 2>&1
setlocal enabledelayedexpansion

REM ═══════════════════════════════════════════════════════════
REM  ExcelManus 一键更新脚本 (Windows 批处理)
REM  用法:  deploy\update.bat [选项]
REM  选项:  --check  --skip-backup  --skip-deps  --mirror  --force  -y
REM ═══════════════════════════════════════════════════════════

set "SCRIPT_DIR=%~dp0"
set "PROJECT_ROOT=%SCRIPT_DIR%.."
cd /d "%PROJECT_ROOT%"

set CHECK_ONLY=0
set SKIP_BACKUP=0
set SKIP_DEPS=0
set USE_MIRROR=0
set FORCE_MODE=0
set AUTO_YES=0
set ROLLBACK=0
set LIST_BACKUPS=0

:parse_args
if "%~1"=="" goto args_done
if /i "%~1"=="--check" set CHECK_ONLY=1
if /i "%~1"=="--skip-backup" set SKIP_BACKUP=1
if /i "%~1"=="--skip-deps" set SKIP_DEPS=1
if /i "%~1"=="--mirror" set USE_MIRROR=1
if /i "%~1"=="--force" set FORCE_MODE=1
if /i "%~1"=="-y" set AUTO_YES=1
if /i "%~1"=="--yes" set AUTO_YES=1
if /i "%~1"=="--rollback" set ROLLBACK=1
if /i "%~1"=="--list-backups" set LIST_BACKUPS=1
if /i "%~1"=="--help" goto show_help
if /i "%~1"=="-h" goto show_help
shift
goto parse_args
:args_done

REM ── 读取版本 ──
set "CURRENT_VERSION=unknown"
for /f "tokens=2 delims==" %%a in ('findstr /r "^version" pyproject.toml 2^>nul') do (
    set "CURRENT_VERSION=%%a"
    set "CURRENT_VERSION=!CURRENT_VERSION: =!"
    set "CURRENT_VERSION=!CURRENT_VERSION:"=!"
)

REM ── 列出备份 ──
if %LIST_BACKUPS%==1 (
    if not exist "backups\backup_*" (
        echo [--] 暂无备份
        exit /b 0
    )
    echo 可用备份:
    for /f "delims=" %%d in ('dir /b /ad /o-n "backups\backup_*" 2^>nul') do (
        echo   %%d
    )
    exit /b 0
)

REM ── 回滚 ──
if %ROLLBACK%==1 (
    set "LATEST_BACKUP="
    for /f "delims=" %%d in ('dir /b /ad /o-n "backups\backup_*" 2^>nul') do (
        if not defined LATEST_BACKUP set "LATEST_BACKUP=%%d"
    )
    if not defined LATEST_BACKUP (
        echo [XX] 未找到任何备份
        exit /b 1
    )
    echo -- 从备份恢复: !LATEST_BACKUP! --
    set "BK=backups\!LATEST_BACKUP!"
    if exist "!BK!\.env" copy /y "!BK!\.env" ".env" >nul && echo [OK] 恢复 .env
    for %%d in (users outputs uploads) do (
        if exist "!BK!\%%d" (
            if exist "%%d" rmdir /s /q "%%d"
            xcopy /e /i /q "!BK!\%%d" "%%d" >nul 2>&1 && echo [OK] 恢复 %%d/
        )
    )
    if exist "!BK!\.excelmanus_home" (
        if not exist "%USERPROFILE%\.excelmanus" mkdir "%USERPROFILE%\.excelmanus"
        copy /y "!BK!\.excelmanus_home\*.db*" "%USERPROFILE%\.excelmanus\" >nul 2>&1 && echo [OK] 恢复数据库
    )
    echo [OK] 回滚完成！请重启服务。
    exit /b 0
)

echo.
echo   +======================================+
echo   ^|     ExcelManus 更新工具 v1.0         ^|
echo   +======================================+
echo.
echo [--] 当前版本: %CURRENT_VERSION%
echo [--] 项目目录: %PROJECT_ROOT%
echo.

REM ── 检查 Git ──
if not exist ".git" (
    echo [XX] 项目不是 Git 仓库，无法更新
    exit /b 1
)
where git >nul 2>&1 || (
    echo [XX] 未找到 Git，请先安装
    exit /b 1
)

REM ── Step 1: 检查更新 ──
echo -- 检查更新 --
git fetch origin --tags --quiet 2>nul
if errorlevel 1 (
    echo [!!] git fetch 失败，请检查网络
    exit /b 1
)

for /f %%b in ('git rev-parse --abbrev-ref HEAD 2^>nul') do set "BRANCH=%%b"
if not defined BRANCH set "BRANCH=main"

for /f %%c in ('git rev-list --count "HEAD..origin/%BRANCH%" 2^>nul') do set "BEHIND=%%c"
if not defined BEHIND set "BEHIND=0"

if %BEHIND%==0 (
    echo [OK] 已是最新版本 ^(%CURRENT_VERSION%^)
    exit /b 0
)

REM 获取远端版本
set "REMOTE_VERSION=unknown"
for /f "tokens=2 delims==" %%a in ('git show "origin/%BRANCH%:pyproject.toml" 2^>nul ^| findstr /r "^version"') do (
    set "REMOTE_VERSION=%%a"
    set "REMOTE_VERSION=!REMOTE_VERSION: =!"
    set "REMOTE_VERSION=!REMOTE_VERSION:"=!"
)

echo.
echo [--] 发现新版本!
echo   当前: %CURRENT_VERSION%
echo   最新: %REMOTE_VERSION%
echo   落后: %BEHIND% 个提交
echo.

if %CHECK_ONLY%==1 exit /b 0

REM ── 确认 ──
if %AUTO_YES%==0 (
    set /p "CONFIRM=是否开始更新？[Y/n] "
    if /i "!CONFIRM!"=="n" (
        echo [--] 已取消
        exit /b 0
    )
)

REM ── Step 2: 备份 ──
if %SKIP_BACKUP%==0 (
    echo -- 备份用户数据 --
    REM 使用 PowerShell 获取区域设置无关的时间戳
    for /f "delims=" %%t in ('powershell -NoProfile -Command "Get-Date -Format 'yyyyMMdd_HHmmss'"') do set "TS=%%t"
    if not defined TS (
        set "TS=%date:~0,4%%date:~5,2%%date:~8,2%_%time:~0,2%%time:~3,2%%time:~6,2%"
        set "TS=!TS: =0!"
    )
    set "BACKUP_PATH=backups\backup_%CURRENT_VERSION%_!TS!"
    mkdir "!BACKUP_PATH!" 2>nul

    if exist ".env" copy /y ".env" "!BACKUP_PATH!\.env" >nul && echo [OK] 备份: .env
    for %%d in (users outputs uploads) do (
        if exist "%%d" (
            xcopy /e /i /q "%%d" "!BACKUP_PATH!\%%d" >nul 2>&1 && echo [OK] 备份: %%d/
        )
    )
    if exist "%USERPROFILE%\.excelmanus" (
        mkdir "!BACKUP_PATH!\.excelmanus_home" 2>nul
        copy /y "%USERPROFILE%\.excelmanus\*.db*" "!BACKUP_PATH!\.excelmanus_home\" >nul 2>&1 && echo [OK] 备份: 数据库
    )
    echo [OK] 备份完成
) else (
    echo [!!] 跳过备份
)

REM ── Step 3: 更新代码 ──
echo -- 拉取最新代码 --
git status --porcelain 2>nul | findstr . >nul 2>&1 && (
    echo [--] 暂存本地修改...
    git stash --include-untracked --quiet 2>nul
)

if %FORCE_MODE%==1 (
    echo [--] 强制覆盖模式
    git reset --hard "origin/%BRANCH%" --quiet
) else (
    git pull origin %BRANCH% --ff-only --quiet 2>nul
    if errorlevel 1 (
        echo [!!] fast-forward 失败，执行强制覆盖...
        git reset --hard "origin/%BRANCH%" --quiet
    )
)

set "NEW_VERSION=unknown"
for /f "tokens=2 delims==" %%a in ('findstr /r "^version" pyproject.toml 2^>nul') do (
    set "NEW_VERSION=%%a"
    set "NEW_VERSION=!NEW_VERSION: =!"
    set "NEW_VERSION=!NEW_VERSION:"=!"
)
echo [OK] 代码已更新: %CURRENT_VERSION% -^> %NEW_VERSION%

REM ── Step 4: 依赖（uv 优先，pip 回退） ──
if %SKIP_DEPS%==0 (
    echo -- 更新后端依赖 --
    set "PY=python"
    if exist ".venv\Scripts\python.exe" set "PY=.venv\Scripts\python.exe"

    set "PIP_MIRROR="
    if %USE_MIRROR%==1 set "PIP_MIRROR=-i https://pypi.tuna.tsinghua.edu.cn/simple"

    set "BACKEND_OK=0"
    where uv >nul 2>&1 && (
        echo [--] 使用 uv sync 安装后端依赖...
        set "UV_MIRROR="
        if %USE_MIRROR%==1 set "UV_MIRROR=--index-url https://pypi.tuna.tsinghua.edu.cn/simple"
        uv sync --all-extras --project . !UV_MIRROR! --quiet 2>nul
        if not errorlevel 1 (
            set "BACKEND_OK=1"
            echo [OK] 后端依赖已更新（uv）
        ) else (
            echo [!!] uv sync 失败，回退到 pip...
        )
    )
    if !BACKEND_OK!==0 (
        !PY! -m pip install -e ".[all]" !PIP_MIRROR! --quiet 2>nul
        if errorlevel 1 (
            echo [!!] pip 失败，尝试清华镜像...
            !PY! -m pip install -e ".[all]" -i https://pypi.tuna.tsinghua.edu.cn/simple --quiet 2>nul
            if errorlevel 1 echo [XX] 后端依赖安装失败
        )
        if not errorlevel 1 echo [OK] 后端依赖已更新（pip）
    )

    if exist "web\package.json" (
        echo -- 更新前端依赖 --
        set "NPM_MIRROR="
        if %USE_MIRROR%==1 set "NPM_MIRROR=--registry=https://registry.npmmirror.com"
        cd web
        npm install !NPM_MIRROR! --silent 2>nul
        if errorlevel 1 (
            echo [!!] npm 失败，尝试 npmmirror...
            npm install --registry=https://registry.npmmirror.com --silent 2>nul
        )
        cd ..
        echo [OK] 前端依赖已更新
    )
) else (
    echo [!!] 跳过依赖更新
)

REM ── Step 5: 重新构建前端（生产模式必须，否则新代码不生效） ──
if exist "web\package.json" (
    if exist "web\.next" (
        echo -- 构建前端 --
        echo [--] 重新构建前端以使更新生效...
        cd web
        call npm run build 2>nul
        if errorlevel 1 (
            echo [!!] 默认构建失败，尝试 webpack 兜底...
            call npm run build:webpack 2>nul
            if errorlevel 1 (
                echo [!!] 前端构建失败（非致命）。生产模式下请手动执行: cd web ^&^& npm run build
            ) else (
                echo [OK] 前端构建完成（webpack 兜底）
            )
        ) else (
            echo [OK] 前端构建完成
        )
        cd ..
    )
)

REM ── Step 6: 预验证数据库迁移 ──
echo -- 预验证数据库迁移 --
set "DB_PY=python"
if exist ".venv\Scripts\python.exe" set "DB_PY=.venv\Scripts\python.exe"
!DB_PY! -c "import sys; exec('try:\n from excelmanus.updater import verify_database_migration\n ok, msg = verify_database_migration()\n print(msg)\n sys.exit(0 if ok else 1)\nexcept Exception as e:\n print(f\"跳过预验证: {e}\")\n sys.exit(0)')" 2>nul
if errorlevel 1 (
    echo [!!] 数据库迁移预验证警告（将在启动时自动重试）
) else (
    echo [OK] 数据库迁移验证通过
)

REM ── 写入 .deploy_meta.json ──
set "META_PY=python"
if exist ".venv\Scripts\python.exe" set "META_PY=.venv\Scripts\python.exe"
for /f "delims=" %%h in ('git rev-parse --short HEAD 2^>nul') do set "GIT_SHORT=%%h"
if not defined GIT_SHORT set "GIT_SHORT=unknown"
for /f "delims=" %%f in ('git rev-parse HEAD 2^>nul') do set "GIT_FULL=%%f"
if not defined GIT_FULL set "GIT_FULL="
for /f "delims=" %%t in ('powershell -NoProfile -Command "Get-Date -Format 'yyyyMMddTHHmmss'"') do set "RELEASE_ID=%%t"
if not defined RELEASE_ID set "RELEASE_ID=unknown"
for /f "delims=" %%d in ('powershell -NoProfile -Command "Get-Date -Format 'yyyy-MM-ddTHH:mm:ssZ'"') do set "DEPLOY_AT=%%d"
if not defined DEPLOY_AT set "DEPLOY_AT=unknown"
>  ".deploy_meta.json" echo {
>> ".deploy_meta.json" echo   "release_id": "!RELEASE_ID!",
>> ".deploy_meta.json" echo   "deployed_at": "!DEPLOY_AT!",
>> ".deploy_meta.json" echo   "git_commit": "!GIT_SHORT!",
>> ".deploy_meta.json" echo   "git_commit_full": "!GIT_FULL!",
>> ".deploy_meta.json" echo   "pre_deploy_commit": "",
>> ".deploy_meta.json" echo   "deploy_mode": "standalone",
>> ".deploy_meta.json" echo   "topology": "local",
>> ".deploy_meta.json" echo   "branch": "%BRANCH%",
>> ".deploy_meta.json" echo   "updater": "update.bat"
>> ".deploy_meta.json" echo }
echo [OK] 部署元数据已写入

REM ── 完成 ──
echo.
echo   +======================================+
echo   ^|         更新成功！                   ^|
echo   +======================================+
echo.
echo [--] 版本: %CURRENT_VERSION% -^> %NEW_VERSION%
echo [--] 数据库迁移将在下次启动时自动执行
echo.
echo [--] 请重启服务以应用更新
echo.
exit /b 0

:show_help
echo ExcelManus 更新脚本
echo 用法: deploy\update.bat [选项]
echo.
echo   --check         仅检查更新
echo   --skip-backup   跳过备份
echo   --skip-deps     跳过依赖重装
echo   --mirror        使用国内镜像
echo   --force         强制覆盖
echo   --rollback      从最近备份恢复
echo   --list-backups  列出所有备份
echo   -y, --yes       跳过确认
echo   -h, --help      显示帮助
exit /b 0
