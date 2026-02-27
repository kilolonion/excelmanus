@echo off
setlocal

set "AUTO=0"
if /I "%~1"=="/auto" set "AUTO=1"

set "SCRIPT_DIR=%~dp0"
set "SRC=%SCRIPT_DIR%ExcelManusSetup.cs"
set "OUT=%SCRIPT_DIR%ExcelManusDeployTool.exe"

REM Find csc.exe (prefer x64)
set "CSC="
if exist "%WINDIR%\Microsoft.NET\Framework64\v4.0.30319\csc.exe" (
    set "CSC=%WINDIR%\Microsoft.NET\Framework64\v4.0.30319\csc.exe"
) else if exist "%WINDIR%\Microsoft.NET\Framework\v4.0.30319\csc.exe" (
    set "CSC=%WINDIR%\Microsoft.NET\Framework\v4.0.30319\csc.exe"
)

if "%CSC%"=="" (
    echo [ERR] csc.exe not found under .NET Framework v4.0.30319
    echo       Make sure .NET Framework 4.x is available on this Windows machine.
    if "%AUTO%"=="0" pause
    exit /b 1
)

echo [OK] Compiler: %CSC%
echo [..] Building EXE...

"%CSC%" /nologo /codepage:65001 /langversion:5 /target:winexe /out:"%OUT%" /optimize+ /platform:anycpu /reference:System.dll /reference:System.Drawing.dll /reference:System.Windows.Forms.dll /reference:System.Net.dll "%SRC%"

if errorlevel 1 (
    echo [ERR] Build failed.
    if "%AUTO%"=="0" pause
    exit /b 1
)

echo [OK] Build succeeded.
echo [OK] Output: %OUT%

REM Copy to repo root for direct run
copy /Y "%OUT%" "%SCRIPT_DIR%..\ExcelManusDeployTool.exe" >nul 2>&1
if not errorlevel 1 (
    echo [OK] Copied to repo root: ExcelManusDeployTool.exe
)

if "%AUTO%"=="0" (
    echo.
    echo Press any key to exit...
    pause >nul
)
exit /b 0
