@echo off
chcp 65001 >nul
setlocal
call "%~dp0start_env.bat"

if /I "%~1"=="--help" goto :usage
if /I "%~1"=="-h" goto :usage
if /I "%~1"=="/?" goto :usage
if /I "%~1"=="--inline" if /I not "%EVELYN_ALLOW_LEGACY_HOST_START%"=="true" goto :run_docker
if /I not "%~1"=="--legacy-host" if /I not "%~1"=="--inline" goto :run_docker
if /I "%~1"=="--legacy-host" if /I not "%EVELYN_ALLOW_LEGACY_HOST_START%"=="true" (
    echo [Evelyn] Legacy host Vision launch is blocked by default.
    echo [Evelyn] Set EVELYN_ALLOW_LEGACY_HOST_START=true only for explicit host-attached debugging.
    endlocal & exit /b 2
)

call :port_ready %VISION_PORT% "Vision"
if %ERRORLEVEL%==2 exit /b 0

if /I "%~1"=="--inline" goto :run_inline

set "WT_EXE=%LOCALAPPDATA%\Microsoft\WindowsApps\wt.exe"
set "WT_READY="
if exist "%WT_EXE%" set "WT_READY=1"
if not defined WT_READY (
    where.exe wt.exe >nul 2>nul
    if not errorlevel 1 (
        set "WT_EXE=wt.exe"
        set "WT_READY=1"
    )
)
if /I not "%EVELYN_USE_WINDOWS_TERMINAL%"=="true" set "WT_READY="

if not defined WT_READY (
    start "Vision" powershell.exe -NoLogo -NoProfile -ExecutionPolicy Bypass -File "%~dp0runtime\launchers\start_vision.ps1"
) else (
    "%WT_EXE%" new-tab --title "Vision" powershell.exe -NoLogo -NoProfile -ExecutionPolicy Bypass -File "%~dp0runtime\launchers\start_vision.ps1"
)

endlocal
exit /b 0

:run_inline
title Vision
powershell.exe -NoLogo -NoProfile -ExecutionPolicy Bypass -File "%~dp0runtime\launchers\start_vision.ps1"
set "EXIT_CODE=%ERRORLEVEL%"
endlocal & exit /b %EXIT_CODE%

:port_ready
powershell -NoProfile -ExecutionPolicy Bypass -Command "if (Get-NetTCPConnection -State Listen -LocalPort %~1 -ErrorAction SilentlyContinue) { exit 0 } else { exit 1 }"
if errorlevel 1 exit /b 1

echo [Evelyn] %~2 already listening on port %~1, skipping new launch
exit /b 2

:usage
echo Usage: start_vision.bat [--inline]
echo.
echo Starts Evelyn Vision service:
echo   - default backend: WSL
echo   - default GPU: RTX 3090 as WSL CUDA_VISIBLE_DEVICES=0
echo   - default port: %VISION_PORT%
echo   - models: %VISION_SMOL_MODEL% + %VISION_OCR_MODEL%
echo.
endlocal & exit /b 0

:run_docker
powershell.exe -NoLogo -NoProfile -ExecutionPolicy Bypass -File "%~dp0runtime\launchers\start_docker_compose_services.ps1" -Profiles vision -Services vision
set "EXIT_CODE=%ERRORLEVEL%"
endlocal & exit /b %EXIT_CODE%
