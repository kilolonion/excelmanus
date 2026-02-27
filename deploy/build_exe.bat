@echo off
chcp 65001 >nul
echo.
echo   ╔══════════════════════════════════════════════╗
echo   ║  ExcelManus 部署工具 - 编译脚本              ║
echo   ║  使用 Windows 内置 .NET Framework 编译器     ║
echo   ╚══════════════════════════════════════════════╝
echo.

set "SCRIPT_DIR=%~dp0"
set "SRC=%SCRIPT_DIR%ExcelManusSetup.cs"
set "OUT=%SCRIPT_DIR%ExcelManus部署工具.exe"

REM 查找 csc.exe (优先 64 位)
set "CSC="
if exist "%WINDIR%\Microsoft.NET\Framework64\v4.0.30319\csc.exe" (
    set "CSC=%WINDIR%\Microsoft.NET\Framework64\v4.0.30319\csc.exe"
) else if exist "%WINDIR%\Microsoft.NET\Framework\v4.0.30319\csc.exe" (
    set "CSC=%WINDIR%\Microsoft.NET\Framework\v4.0.30319\csc.exe"
)

if "%CSC%"=="" (
    echo [XX] 未找到 .NET Framework C# 编译器 csc.exe
    echo     请确保已安装 .NET Framework 4.0+（Windows 10/11 自带）
    pause
    exit /b 1
)

echo [OK] 编译器: %CSC%
echo [..] 正在编译...
echo.

"%CSC%" /nologo /target:winexe /out:"%OUT%" /optimize+ /platform:anycpu /win32manifest:NUL /reference:System.dll /reference:System.Drawing.dll /reference:System.Windows.Forms.dll /reference:System.Net.dll "%SRC%"

if errorlevel 1 (
    echo.
    echo [XX] 编译失败！
    pause
    exit /b 1
)

echo.
echo [OK] 编译成功！
echo [OK] 输出: %OUT%
echo.

REM 复制到项目根目录方便使用
copy /Y "%OUT%" "%SCRIPT_DIR%..\ExcelManus部署工具.exe" >nul 2>&1
if not errorlevel 1 (
    echo [OK] 已复制到项目根目录: ExcelManus部署工具.exe
)

echo.
echo   按任意键退出...
pause >nul
