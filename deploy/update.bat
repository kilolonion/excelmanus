@echo off
chcp 65001 >nul 2>&1
setlocal enabledelayedexpansion

REM ExcelManus 一键更新脚本 (Windows 批处理) — 转发到 update.ps1 / python -m excelmanus.upgrade
REM 用法:  deploy\update.bat [选项]
REM 选项:  --check  --skip-backup  --skip-deps  --mirror  --rollback  --list-backups  -y

set "SCRIPT_DIR=%~dp0"
powershell -NoProfile -ExecutionPolicy Bypass -File "%SCRIPT_DIR%update.ps1" %*
exit /b %ERRORLEVEL%
