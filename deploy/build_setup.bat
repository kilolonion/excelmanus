@echo off
REM ═══════════════════════════════════════════════════════════
REM  Build ExcelManusSetup.exe from setup_app.py using Nuitka
REM ═══════════════════════════════════════════════════════════
setlocal

cd /d "%~dp0"

echo [1/3] Installing build dependencies...
pip install -r requirements-setup.txt
if errorlevel 1 (
    echo ERROR: Failed to install dependencies
    pause
    exit /b 1
)

echo.
echo [2/3] Building setup-ui (Vite React app)...
if exist "setup-ui\package.json" (
    cd setup-ui
    if not exist "node_modules" (
        call npm install
    )
    call npm run build
    cd ..
    if exist "setup-ui\dist\index.html" (
        echo    setup-ui built successfully
        set ADD_UI=--include-data-files=setup-ui/dist/index.html=index.html
    ) else (
        echo    WARNING: setup-ui build failed, using fallback HTML
        set ADD_UI=
    )
) else (
    echo    setup-ui not found, using fallback HTML
    set ADD_UI=
)

echo.
echo [3/3] Building EXE with Nuitka...

REM Determine icon path
set ICON_ARG=
if exist "icon.ico" set ICON_ARG=--windows-icon-from-ico=icon.ico
if exist "..\web\public\favicon.ico" if not defined ICON_ARG set ICON_ARG=--windows-icon-from-ico=..\web\public\favicon.ico

REM Add icon.ico as data if available
set ADD_ICON=
if exist "icon.ico" set ADD_ICON=--include-data-files=icon.ico=icon.ico

python -m nuitka --standalone --onefile ^
    --windows-console-mode=disable ^
    --output-filename=ExcelManusSetup.exe ^
    --output-dir=dist ^
    --remove-output ^
    --mingw64 ^
    --assume-yes-for-downloads ^
    %ICON_ARG% ^
    %ADD_UI% ^
    %ADD_ICON% ^
    setup_app.py

if errorlevel 1 (
    echo.
    echo ERROR: Nuitka build failed
    pause
    exit /b 1
)

echo.
echo ═══════════════════════════════════════════════════════════
echo  BUILD SUCCESSFUL
echo  Output: dist\ExcelManusSetup.exe
echo ═══════════════════════════════════════════════════════════

REM Copy to project root if building from deploy/
if exist "..\ExcelManusSetup.exe" (
    copy /y "dist\ExcelManusSetup.exe" "..\ExcelManusSetup.exe" >nul
    echo  Also copied to: ..\ExcelManusSetup.exe
)

echo.
pause
