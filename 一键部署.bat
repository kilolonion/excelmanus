@echo off
chcp 65001 >nul
title ExcelManus 一键部署

set "ROOT=%~dp0"
set "EXE=%ROOT%ExcelManus部署工具.exe"
set "BUILD=%ROOT%deploy\build_exe.bat"

REM 如果 exe 已存在则直接运行
if exist "%EXE%" (
    start "" "%EXE%"
    exit /b 0
)

REM 如果 deploy 目录下有 exe 也可以
if exist "%ROOT%deploy\ExcelManus部署工具.exe" (
    start "" "%ROOT%deploy\ExcelManus部署工具.exe"
    exit /b 0
)

REM 否则先编译
echo.
echo   ExcelManus 部署工具首次使用，正在编译...
echo.

if not exist "%BUILD%" (
    echo [XX] 未找到编译脚本: deploy\build_exe.bat
    pause
    exit /b 1
)

call "%BUILD%"

if exist "%EXE%" (
    echo.
    echo [OK] 正在启动部署工具...
    start "" "%EXE%"
) else (
    echo [XX] 编译失败，请检查错误信息
    pause
)
