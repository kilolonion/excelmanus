@echo off
REM ═══════════════════════════════════════════════════════════════════════
REM  ExcelManus 一键启动脚本 (Windows CMD 版本)
REM  同时启动 FastAPI 后端 + Next.js 前端
REM
REM  用法:  deploy\start.bat [选项]
REM
REM  选项:
REM    --prod               生产模式
REM    --backend-only       仅启动后端
REM    --frontend-only      仅启动前端
REM    --backend-port PORT  后端端口（默认 8000）
REM    --frontend-port PORT 前端端口（默认 3000）
REM    --skip-deps          跳过依赖检查
REM    --no-open            不自动打开浏览器
REM    --help               显示帮助
REM ═══════════════════════════════════════════════════════════════════════

setlocal enabledelayedexpansion

REM ── 定位项目根目录 ──
set "SCRIPT_DIR=%~dp0"
pushd "%SCRIPT_DIR%.."
set "PROJECT_ROOT=%CD%"

REM ── 默认值 ──
set "PRODUCTION=0"
set "BACKEND_ONLY=0"
set "FRONTEND_ONLY=0"
set "BACKEND_PORT=8000"
set "FRONTEND_PORT=3000"
set "SKIP_DEPS=0"
set "AUTO_OPEN=1"
set "BACKEND_PID="
set "FRONTEND_PID="

REM ── 解析参数 ──
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
echo [XX] 未知参数: %~1 （使用 --help 查看帮助）
exit /b 1

:args_done

REM ── 互斥检查 ──
if "%BACKEND_ONLY%"=="1" if "%FRONTEND_ONLY%"=="1" (
    echo [XX] --backend-only 与 --frontend-only 不能同时使用
    exit /b 1
)

REM ── 加载 .env（如存在）──
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

REM ── 环境变量覆盖端口 ──
if defined EXCELMANUS_BACKEND_PORT  set "BACKEND_PORT=%EXCELMANUS_BACKEND_PORT%"
if defined EXCELMANUS_FRONTEND_PORT set "FRONTEND_PORT=%EXCELMANUS_FRONTEND_PORT%"

REM ── 查找 Python 解释器 ──
set "PYTHON_BIN="
if exist "%PROJECT_ROOT%\.venv\Scripts\python.exe" (
    set "PYTHON_BIN=%PROJECT_ROOT%\.venv\Scripts\python.exe"
) else if exist "%PROJECT_ROOT%\.venv\bin\python.exe" (
    set "PYTHON_BIN=%PROJECT_ROOT%\.venv\bin\python.exe"
)

REM ── 依赖检查 ──
if "%SKIP_DEPS%"=="1" goto :deps_done

if "%FRONTEND_ONLY%"=="1" goto :check_node

REM 检查 Python venv
if "%PYTHON_BIN%"=="" (
    echo [XX] 未找到 .venv 虚拟环境
    echo     请先运行: python -m venv .venv
    echo     然后运行: .venv\Scripts\pip install -e .
    exit /b 1
)

REM 检查 uvicorn
"%PYTHON_BIN%" -c "import uvicorn" >nul 2>&1
if errorlevel 1 (
    echo [!!] uvicorn 未安装，尝试自动安装...
    "%PYTHON_BIN%" -m pip install uvicorn -q >nul 2>&1
    if errorlevel 1 (
        echo [XX] uvicorn 安装失败
        exit /b 1
    )
)

:check_node
if "%BACKEND_ONLY%"=="1" goto :deps_done

REM 检查 Node.js
where node >nul 2>&1
if errorlevel 1 (
    echo [XX] 未找到 Node.js，请安装: https://nodejs.org/
    exit /b 1
)

REM 检查 npm
where npm >nul 2>&1
if errorlevel 1 (
    echo [XX] 未找到 npm，请安装: https://nodejs.org/
    exit /b 1
)

REM 检查 web/node_modules
if not exist "%PROJECT_ROOT%\web\node_modules" (
    echo [--] 首次启动，安装前端依赖...
    pushd "%PROJECT_ROOT%\web"
    call npm install
    if errorlevel 1 (
        echo [XX] npm install 失败
        popd
        exit /b 1
    )
    popd
)

REM 生产模式需要先构建
if "%PRODUCTION%"=="1" (
    if not exist "%PROJECT_ROOT%\web\.next" (
        echo [--] 生产模式首次启动，构建前端...
        pushd "%PROJECT_ROOT%\web"
        call npm run build
        if errorlevel 1 (
            echo [XX] npm run build 失败
            popd
            exit /b 1
        )
        popd
    )
)

:deps_done

REM ── 清理残留端口 ──
if "%FRONTEND_ONLY%"=="0" call :kill_port %BACKEND_PORT%
if "%BACKEND_ONLY%"=="0"  call :kill_port %FRONTEND_PORT%

REM ── 启动信息 ──
echo.
echo   ExcelManus 启动中...
if "%PRODUCTION%"=="1" ( echo   模式: 生产 ) else ( echo   模式: 开发 )
echo.

REM ── 启动后端 ──
if "%FRONTEND_ONLY%"=="1" goto :skip_backend

echo [--] 启动 FastAPI 后端 (0.0.0.0:%BACKEND_PORT%)...
start "" /b "%PYTHON_BIN%" -c "import uvicorn; uvicorn.run('excelmanus.api:app', host='0.0.0.0', port=%BACKEND_PORT%, log_level='info')"

REM 等待后端就绪
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
echo [XX] 后端启动超时（30s）
exit /b 1

:backend_ready
:skip_backend

REM ── 启动前端 ──
if "%BACKEND_ONLY%"=="1" goto :skip_frontend

if "%PRODUCTION%"=="1" (
    echo [--] 启动 Next.js 前端 [start] (端口 %FRONTEND_PORT%)...
    start "" /b cmd /c "cd /d "%PROJECT_ROOT%\web" && npm run start -- -p %FRONTEND_PORT%"
) else (
    echo [--] 启动 Next.js 前端 [dev] (端口 %FRONTEND_PORT%)...
    start "" /b cmd /c "cd /d "%PROJECT_ROOT%\web" && npm run dev -- -p %FRONTEND_PORT%"
)

REM 等待前端启动
timeout /t 3 /nobreak >nul 2>&1

REM ── 自动打开浏览器 ──
if "%AUTO_OPEN%"=="1" (
    start "" "http://localhost:%FRONTEND_PORT%"
)

:skip_frontend

REM ── 启动摘要 ──
echo.
echo   ========================================
echo     ExcelManus 已启动！
if "%PRODUCTION%"=="1" ( echo     模式: 生产 ) else ( echo     模式: 开发 )
if "%BACKEND_ONLY%"=="0"  echo     前端: http://localhost:%FRONTEND_PORT%
if "%FRONTEND_ONLY%"=="0" echo     后端: http://localhost:%BACKEND_PORT%
echo     按 Ctrl+C 停止所有服务
echo   ========================================
echo.

REM ── 保持窗口（等待 Ctrl+C）──
echo 按任意键或 Ctrl+C 停止服务...
pause >nul

REM ── 清理 ──
echo [--] 正在关闭服务...
if "%FRONTEND_ONLY%"=="0" call :kill_port %BACKEND_PORT%
if "%BACKEND_ONLY%"=="0"  call :kill_port %FRONTEND_PORT%
echo [OK] 已关闭

popd
endlocal
exit /b 0

REM ═══════════════════════════════════════════════════════════════
REM  子程序
REM ═══════════════════════════════════════════════════════════════

:kill_port
REM 终止占用指定端口的进程
set "_port=%~1"
for /f "tokens=5" %%p in ('netstat -ano 2^>nul ^| findstr ":%_port% " ^| findstr "LISTENING"') do (
    if not "%%p"=="0" (
        echo [!!] 端口 %_port% 被占用 (PID %%p)，正在清理...
        taskkill /PID %%p /F >nul 2>&1
    )
)
exit /b 0

:show_help
echo.
echo   ExcelManus 一键启动脚本 (Windows CMD)
echo.
echo   用法:  deploy\start.bat [选项]
echo.
echo   选项:
echo     --prod               生产模式（npm run start 代替 npm run dev）
echo     --backend-only       仅启动后端
echo     --frontend-only      仅启动前端
echo     --backend-port PORT  后端端口（默认 8000）
echo     --frontend-port PORT 前端端口（默认 3000）
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
