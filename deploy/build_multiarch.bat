@echo off
setlocal enabledelayedexpansion

:: ============================================================================
:: ExcelManus Multi-Arch Docker Image Build Script (Windows)
:: Platforms: linux/amd64, linux/arm64, linux/arm/v7
::
:: Usage:
::   deploy\build_multiarch.bat              Build only (no push)
::   deploy\build_multiarch.bat --push       Build and push to registry
::   deploy\build_multiarch.bat --load       Build and load locally (single platform)
::
:: Environment variables:
::   REGISTRY    Image registry prefix, e.g. "docker.io/myuser"
::   VERSION     Image version tag, defaults to pyproject.toml version
::   PLATFORMS   Target platforms, defaults to "linux/amd64,linux/arm64,linux/arm/v7"
:: ============================================================================

set "SCRIPT_DIR=%~dp0"
if "%SCRIPT_DIR:~-1%"=="\" set "SCRIPT_DIR=%SCRIPT_DIR:~0,-1%"
for %%I in ("%SCRIPT_DIR%\..") do set "PROJECT_ROOT=%%~fI"

:: Ensure Docker is in PATH
where docker >nul 2>&1
if errorlevel 1 (
    if exist "C:\Program Files\Docker\Docker\resources\bin\docker.exe" (
        set "PATH=C:\Program Files\Docker\Docker\resources\bin;%PATH%"
    ) else (
        echo [Error] Docker not found. Please install Docker Desktop.
        exit /b 1
    )
)

:: ---------- Defaults ----------
if "%REGISTRY%"=="" set "REGISTRY=excelmanus"
if "%PLATFORMS%"=="" set "PLATFORMS=linux/amd64,linux/arm64,linux/arm/v7"

:: Read version from pyproject.toml
if "%VERSION%"=="" (
    for /f "tokens=2 delims=^=""" %%V in ('findstr /C:"version" "%PROJECT_ROOT%\pyproject.toml"') do (
        set "VERSION=%%~V"
        goto :got_version
    )
)
:got_version
if "%VERSION%"=="" set "VERSION=latest"

:: ---------- Parse args ----------
set "ACTION="
if "%~1"=="--push" set "ACTION=--push"
if "%~1"=="--load" (
    set "ACTION=--load"
    echo [Warning] --load only supports single platform, building for current arch only
    set "PLATFORMS="
)

set "BUILDER_NAME=excelmanus-multiarch"

echo ============================================
echo  ExcelManus Multi-Arch Docker Build
echo ============================================
echo  Registry:   %REGISTRY%
echo  Version:    %VERSION%
if "%PLATFORMS%"=="" (
    echo  Platforms:  current arch
) else (
    echo  Platforms:  %PLATFORMS%
)
if "%ACTION%"=="" (
    echo  Action:     build only (no push)
) else (
    echo  Action:     %ACTION%
)
echo ============================================
echo.

:: ---------- Setup buildx builder ----------
echo [Setup] Checking buildx builder: %BUILDER_NAME%
docker buildx inspect %BUILDER_NAME% >nul 2>&1
if errorlevel 1 (
    echo [Setup] Creating buildx builder: %BUILDER_NAME%
    docker buildx create --name %BUILDER_NAME% --driver docker-container --use
) else (
    docker buildx use %BUILDER_NAME%
)
docker buildx inspect --bootstrap
if errorlevel 1 (
    echo [Error] Cannot bootstrap buildx builder. Make sure Docker Desktop is running.
    exit /b 1
)

:: ---------- Build images ----------

:: 1. Backend API
echo.
echo ==========================================
echo [1/3] Building excelmanus-api
echo ==========================================
set "CMD=docker buildx build -f "%PROJECT_ROOT%\deploy\Dockerfile" -t "%REGISTRY%/excelmanus-api:%VERSION%" -t "%REGISTRY%/excelmanus-api:latest""
if not "%PLATFORMS%"=="" set "CMD=!CMD! --platform %PLATFORMS%"
if not "%ACTION%"=="" set "CMD=!CMD! %ACTION%"
set "CMD=!CMD! "%PROJECT_ROOT%""
echo [Run] !CMD!
!CMD!
if errorlevel 1 (
    echo [Error] excelmanus-api build failed!
    exit /b 1
)
echo [OK] excelmanus-api done

:: 2. Sandbox
echo.
echo ==========================================
echo [2/3] Building excelmanus-sandbox
echo ==========================================
set "CMD=docker buildx build -f "%PROJECT_ROOT%\deploy\Dockerfile.sandbox" -t "%REGISTRY%/excelmanus-sandbox:%VERSION%" -t "%REGISTRY%/excelmanus-sandbox:latest""
if not "%PLATFORMS%"=="" set "CMD=!CMD! --platform %PLATFORMS%"
if not "%ACTION%"=="" set "CMD=!CMD! %ACTION%"
set "CMD=!CMD! "%PROJECT_ROOT%""
echo [Run] !CMD!
!CMD!
if errorlevel 1 (
    echo [Error] excelmanus-sandbox build failed!
    exit /b 1
)
echo [OK] excelmanus-sandbox done

:: 3. Frontend Web
echo.
echo ==========================================
echo [3/3] Building excelmanus-web
echo ==========================================
set "CMD=docker buildx build -f "%PROJECT_ROOT%\web\Dockerfile" -t "%REGISTRY%/excelmanus-web:%VERSION%" -t "%REGISTRY%/excelmanus-web:latest""
if not "%PLATFORMS%"=="" set "CMD=!CMD! --platform %PLATFORMS%"
if not "%ACTION%"=="" set "CMD=!CMD! %ACTION%"
set "CMD=!CMD! "%PROJECT_ROOT%\web""
echo [Run] !CMD!
!CMD!
if errorlevel 1 (
    echo [Error] excelmanus-web build failed!
    exit /b 1
)
echo [OK] excelmanus-web done

:: ---------- Done ----------
echo.
echo ============================================
echo  All images built successfully!
echo ============================================
echo.
echo Images:
echo   - %REGISTRY%/excelmanus-api:%VERSION%
echo   - %REGISTRY%/excelmanus-sandbox:%VERSION%
echo   - %REGISTRY%/excelmanus-web:%VERSION%
echo.
if "%ACTION%"=="" (
    echo [Tip] Images are cached in buildx.
    echo   Push to registry:  deploy\build_multiarch.bat --push
    echo   Load locally:      deploy\build_multiarch.bat --load  (single platform only)
)

endlocal
