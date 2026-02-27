@echo off
REM -- Self-relaunch with UTF-8 codepage so CMD parses Chinese correctly --
if "%_EXCELMANUS_UTF8%"=="1" goto :main
chcp 65001 >nul
set "_EXCELMANUS_UTF8=1"
call "%~f0" %*
set "_EC=%errorlevel%"
set "_EXCELMANUS_UTF8="
exit /b %_EC%

:main
REM =======================================================================
REM  ExcelManus Quick Start Script (Windows CMD)
REM  Start FastAPI backend + Next.js frontend
REM
REM  Usage:  deploy\start.bat [options]
REM
REM  Options:
REM    --prod               Production mode
REM    --backend-only       Backend only
REM    --frontend-only      Frontend only
REM    --backend-port PORT  Backend port (default 8000)
REM    --frontend-port PORT Frontend port (default 3000)
REM    --skip-deps          Skip dependency check
REM    --no-open            Do not open browser
REM    --help               Show help
REM =======================================================================

setlocal enabledelayedexpansion

REM -- Locate project root --
set "SCRIPT_DIR=%~dp0"
pushd "%SCRIPT_DIR%.."
set "PROJECT_ROOT=%CD%"

REM -- GitHub repo config --
set "REPO_URL=https://github.com/kilolonion/excelmanus"
set "REPO_BRANCH=main"

REM -- Check if project is complete (clone from GitHub if missing) --
if not exist "%PROJECT_ROOT%\pyproject.toml" (
    echo [!!] 未检测到完整项目文件
    where git >nul 2>&1
    if errorlevel 1 (
        echo [XX] 未找到 Git，请先安装: https://git-scm.com/
        echo     或手动下载项目: %REPO_URL%
        goto :exit_with_pause
    )
    echo [--] 正在从 GitHub 克隆项目...
    echo     仓库: %REPO_URL%
    echo     分支: %REPO_BRANCH%
    git clone -b %REPO_BRANCH% %REPO_URL% "%PROJECT_ROOT%_tmp"
    if errorlevel 1 (
        echo [XX] Git 克隆失败，请检查网络连接
        goto :exit_with_pause
    )
    REM Move files from tmp to project root
    xcopy /E /Y /Q "%PROJECT_ROOT%_tmp\*" "%PROJECT_ROOT%\" >nul 2>&1
    rmdir /S /Q "%PROJECT_ROOT%_tmp" >nul 2>&1
    echo [OK] 项目已从 GitHub 克隆完成
)

REM -- Interactive .env setup if missing --
if not exist "%PROJECT_ROOT%\.env" (
    echo.
    echo   ========================================
    echo     首次启动 - 配置 ExcelManus
    echo   ========================================
    echo.
    echo   需要配置 LLM API 信息才能使用。
    echo   [直接按回车可跳过, 稍后手动编辑 .env 文件]
    echo.
    set "INPUT_API_KEY="
    set "INPUT_BASE_URL="
    set "INPUT_MODEL="
    set /p "INPUT_API_KEY=  API Key: "
    set /p "INPUT_BASE_URL=  Base URL [例: https://api.openai.com/v1]: "
    set /p "INPUT_MODEL=  Model [例: gpt-4o]: "
    echo.
    if "!INPUT_API_KEY!"=="" (
        echo [!!] 未填写 API Key，创建空模板 .env 文件
        echo [!!] 请稍后编辑 %PROJECT_ROOT%\.env 填入配置
        (
            echo # ExcelManus Configuration
            echo # Please fill in your LLM API settings
            echo EXCELMANUS_API_KEY=your-api-key
            echo EXCELMANUS_BASE_URL=https://your-llm-endpoint/v1
            echo EXCELMANUS_MODEL=your-model-id
        ) > "%PROJECT_ROOT%\.env"
    ) else (
        (
            echo # ExcelManus Configuration
            echo EXCELMANUS_API_KEY=!INPUT_API_KEY!
            echo EXCELMANUS_BASE_URL=!INPUT_BASE_URL!
            echo EXCELMANUS_MODEL=!INPUT_MODEL!
        ) > "%PROJECT_ROOT%\.env"
        echo [OK] .env 配置文件已创建
    )
    echo.
)

REM -- Defaults --
set "PRODUCTION=0"
set "BACKEND_ONLY=0"
set "FRONTEND_ONLY=0"
set "BACKEND_PORT=8000"
set "FRONTEND_PORT=3000"
set "SKIP_DEPS=0"
set "AUTO_OPEN=1"
set "BACKEND_PID="
set "FRONTEND_PID="

REM -- Parse arguments --
:parse_args
if "%~1"=="" goto :args_done
if /i "%~1"=="--prod"           ( set "PRODUCTION=1"    & shift & goto :parse_args )
if /i "%~1"=="--production"     ( set "PRODUCTION=1"    & shift & goto :parse_args )
if /i "%~1"=="--backend-only"   ( set "BACKEND_ONLY=1"  & shift & goto :parse_args )
if /i "%~1"=="--frontend-only"  ( set "FRONTEND_ONLY=1" & shift & goto :parse_args )
if /i "%~1"=="--backend-port"   ( set "BACKEND_PORT=%~2" & shift & shift & goto :parse_args )
if /i "%~1"=="--frontend-port"  ( set "FRONTEND_PORT=%~2" & shift & shift & goto :parse_args )
if /i "%~1"=="--skip-deps"      ( set "SKIP_DEPS=1"    & shift & goto :parse_args )
if /i "%~1"=="--no-open"        ( set "AUTO_OPEN=0"    & shift & goto :parse_args )
if /i "%~1"=="--help"           ( goto :show_help )
if /i "%~1"=="-h"               ( goto :show_help )
echo [XX] 未知参数: %~1 [使用 --help 查看帮助]
goto :exit_with_pause

:args_done

REM -- Mutual exclusion check --
if "%BACKEND_ONLY%"=="1" if "%FRONTEND_ONLY%"=="1" (
    echo [XX] --backend-only 与 --frontend-only 不能同时使用
    goto :exit_with_pause
)

REM -- Load .env if exists --
if exist "%PROJECT_ROOT%\.env" (
    for /f "usebackq tokens=1,* delims==" %%a in ("%PROJECT_ROOT%\.env") do (
        set "line=%%a"
        if not "!line:~0,1!"=="#" (
            if not "%%a"=="" if not "%%b"=="" (
                set "%%a=%%b"
            )
        )
    )
)
if exist "%PROJECT_ROOT%\.env.local" (
    for /f "usebackq tokens=1,* delims==" %%a in ("%PROJECT_ROOT%\.env.local") do (
        set "line=%%a"
        if not "!line:~0,1!"=="#" (
            if not "%%a"=="" if not "%%b"=="" (
                set "%%a=%%b"
            )
        )
    )
)

REM -- Env vars override ports --
if defined EXCELMANUS_BACKEND_PORT  set "BACKEND_PORT=%EXCELMANUS_BACKEND_PORT%"
if defined EXCELMANUS_FRONTEND_PORT set "FRONTEND_PORT=%EXCELMANUS_FRONTEND_PORT%"

REM -- Find Python interpreter --
set "PYTHON_BIN="
if exist "%PROJECT_ROOT%\.venv\Scripts\python.exe" (
    set "PYTHON_BIN=%PROJECT_ROOT%\.venv\Scripts\python.exe"
) else if exist "%PROJECT_ROOT%\.venv\bin\python.exe" (
    set "PYTHON_BIN=%PROJECT_ROOT%\.venv\bin\python.exe"
)

REM -- Dependency check --
if "%SKIP_DEPS%"=="1" goto :deps_done

if "%FRONTEND_ONLY%"=="1" goto :check_node

REM Check Python venv - auto create if missing
if "%PYTHON_BIN%"=="" (
    echo [--] 未找到 .venv 虚拟环境，正在自动创建...
    where python >nul 2>&1
    if errorlevel 1 (
        echo [XX] 未找到 Python，请先安装: https://www.python.org/
        goto :exit_with_pause
    )
    python -m venv "%PROJECT_ROOT%\.venv"
    if errorlevel 1 (
        echo [XX] 创建虚拟环境失败
        goto :exit_with_pause
    )
    set "PYTHON_BIN=%PROJECT_ROOT%\.venv\Scripts\python.exe"
    echo [OK] 虚拟环境已创建
)

REM Use Tsinghua pip mirror by default (fallback to PyPI on failure)
set "PIP_MIRROR=https://pypi.tuna.tsinghua.edu.cn/simple"
set "PIP_MIRROR_HOST=pypi.tuna.tsinghua.edu.cn"

REM Auto install project dependencies
"%PYTHON_BIN%" -c "import fastapi; import uvicorn; import rich" >nul 2>&1
if errorlevel 1 goto :install_deps
goto :deps_python_ok

:install_deps
echo [--] 正在安装项目依赖 [首次启动可能需要几分钟]...
if defined PIP_MIRROR (
    echo [--] 使用镜像: %PIP_MIRROR%
)
call :pip_install -e "%PROJECT_ROOT%[all]"
if errorlevel 1 (
    echo [XX] 依赖安装失败
    goto :exit_with_pause
)
REM Verify critical dependency after install
"%PYTHON_BIN%" -c "import fastapi; import uvicorn; import rich" >nul 2>&1
if errorlevel 1 (
    echo [XX] 依赖未正确安装，请检查网络连接后重试
    goto :exit_with_pause
)
echo [OK] 项目依赖已安装

:deps_python_ok

:check_node
if "%BACKEND_ONLY%"=="1" goto :deps_done

REM Check Node.js
where node >nul 2>&1
if errorlevel 1 (
    echo [XX] 未找到 Node.js，请安装: https://nodejs.org/
    goto :exit_with_pause
)

REM Check npm
where npm >nul 2>&1
if errorlevel 1 (
    echo [XX] 未找到 npm，请安装: https://nodejs.org/
    goto :exit_with_pause
)

REM Check web/node_modules
if not exist "%PROJECT_ROOT%\web\node_modules" (
    echo [--] 首次启动，安装前端依赖...
    pushd "%PROJECT_ROOT%\web"
    call npm install
    if errorlevel 1 (
        echo [XX] npm install 失败
        popd
        goto :exit_with_pause
    )
    popd
)

REM Production mode needs build first
if "%PRODUCTION%"=="1" (
    if not exist "%PROJECT_ROOT%\web\.next" (
        echo [--] 生产模式首次启动，构建前端...
        pushd "%PROJECT_ROOT%\web"
        call npm run build
        if errorlevel 1 (
            echo [XX] npm run build 失败
            popd
            goto :exit_with_pause
        )
        popd
    )
)

:deps_done

REM -- Kill leftover ports --
if "%FRONTEND_ONLY%"=="0" call :kill_port %BACKEND_PORT%
if "%BACKEND_ONLY%"=="0"  call :kill_port %FRONTEND_PORT%

REM -- Startup info --
echo.
echo   ExcelManus 启动中...
if "%PRODUCTION%"=="1" ( echo   模式: 生产 ) else ( echo   模式: 开发 )
echo.

REM -- Start backend --
if "%FRONTEND_ONLY%"=="1" goto :skip_backend

echo [--] 启动 FastAPI 后端 [0.0.0.0:%BACKEND_PORT%]...
start "" /b "%PYTHON_BIN%" -c "import uvicorn; uvicorn.run('excelmanus.api:app', host='0.0.0.0', port=%BACKEND_PORT%, log_level='info')"

REM Wait for backend ready
set "BACKEND_READY=0"
set "WAIT_COUNT=0"
:wait_backend
if %WAIT_COUNT% geq 30 goto :backend_timeout
curl -s "http://localhost:%BACKEND_PORT%/api/v1/health" >nul 2>&1
if not errorlevel 1 (
    echo [OK] 后端已就绪
    set "BACKEND_READY=1"
    goto :backend_ready
)
set /a WAIT_COUNT+=1
timeout /t 1 /nobreak >nul 2>&1
goto :wait_backend

:backend_timeout
echo [XX] 后端启动超时 [30s]
goto :exit_with_pause

:backend_ready
:skip_backend

REM -- Start frontend --
if "%BACKEND_ONLY%"=="1" goto :skip_frontend

if "%PRODUCTION%"=="1" (
    echo [--] 启动 Next.js 前端 [start] [端口 %FRONTEND_PORT%]...
    start "" /b cmd /c "cd /d %PROJECT_ROOT%\web && npm run start -- -p %FRONTEND_PORT%"
) else (
    echo [--] 启动 Next.js 前端 [dev] [端口 %FRONTEND_PORT%]...
    start "" /b cmd /c "cd /d %PROJECT_ROOT%\web && npm run dev -- -p %FRONTEND_PORT%"
)

REM Wait for frontend
timeout /t 3 /nobreak >nul 2>&1

REM -- Auto open browser --
if "%AUTO_OPEN%"=="1" (
    start "" "http://localhost:%FRONTEND_PORT%"
)

:skip_frontend

REM -- Startup summary --
echo.
echo   ========================================
echo     ExcelManus 已启动！
if "%PRODUCTION%"=="1" ( echo     模式: 生产 ) else ( echo     模式: 开发 )
if "%BACKEND_ONLY%"=="0"  echo     前端: http://localhost:%FRONTEND_PORT%
if "%FRONTEND_ONLY%"=="0" echo     后端: http://localhost:%BACKEND_PORT%
echo     按 Ctrl+C 停止所有服务
echo   ========================================
echo.

REM -- Keep window (wait for Ctrl+C) --
echo 按任意键或 Ctrl+C 停止服务...
pause >nul

REM -- Cleanup --
echo [--] 正在关闭服务...
if "%FRONTEND_ONLY%"=="0" call :kill_port %BACKEND_PORT%
if "%BACKEND_ONLY%"=="0"  call :kill_port %FRONTEND_PORT%
echo [OK] 已关闭

popd
endlocal
exit /b 0

:exit_with_pause
echo.
echo   按任意键退出...
pause >nul
popd
endlocal
exit /b 1

REM ===============================================================
REM  Subroutines
REM ===============================================================

:pip_install
REM Install with mirror if available, fallback to default
if defined PIP_MIRROR (
    "%PYTHON_BIN%" -m pip install %* -i %PIP_MIRROR% --trusted-host %PIP_MIRROR_HOST%
    if not errorlevel 1 exit /b 0
    echo [!!] 镜像安装失败, 尝试默认源...
)
"%PYTHON_BIN%" -m pip install %*
exit /b %errorlevel%

:kill_port
REM Kill process on specified port
set "_port=%~1"
for /f "tokens=5" %%p in ('netstat -ano 2^>nul ^| findstr ":%_port% " ^| findstr "LISTENING"') do (
    if not "%%p"=="0" (
        echo [!!] 端口 %_port% 被占用 [PID %%p], 正在清理...
        taskkill /PID %%p /F >nul 2>&1
    )
)
exit /b 0

:show_help
echo.
echo   ExcelManus 一键启动脚本 [Windows CMD]
echo.
echo   用法:  deploy\start.bat [选项]
echo.
echo   选项:
echo     --prod               生产模式 [npm run start 代替 npm run dev]
echo     --backend-only       仅启动后端
echo     --frontend-only      仅启动前端
echo     --backend-port PORT  后端端口 [默认 8000]
echo     --frontend-port PORT 前端端口 [默认 3000]
echo     --skip-deps          跳过依赖检查
echo     --no-open            不自动打开浏览器
echo     --help               显示帮助
echo.
echo   示例:
echo     deploy\start.bat                          开发模式默认启动
echo     deploy\start.bat --prod                   生产模式启动
echo     deploy\start.bat --backend-port 9000      自定义后端端口
echo     deploy\start.bat --backend-only           仅启动后端
echo     deploy\start.bat --prod --frontend-only   仅生产模式前端
echo.
exit /b 0
