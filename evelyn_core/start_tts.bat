@echo off
chcp 65001 >nul
setlocal
call "%~dp0start_env.bat"

if /I not "%~1"=="--legacy-host" goto :run_docker
if /I not "%EVELYN_ALLOW_LEGACY_HOST_START%"=="true" (
    echo [Evelyn] Legacy host TTS launch is blocked by default.
    echo [Evelyn] Set EVELYN_ALLOW_LEGACY_HOST_START=true only for explicit host-attached debugging.
    endlocal & exit /b 2
)

if not exist "%OMNIVOICE_PROFILE_DIR%" mkdir "%OMNIVOICE_PROFILE_DIR%"

call :port_ready %TTS_PORT% "OmniVoice-TTS"
if %ERRORLEVEL%==2 exit /b 0

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
    start "TTS" powershell.exe -NoLogo -NoProfile -ExecutionPolicy Bypass -File "%~dp0runtime\launchers\start_tts.ps1"
) else (
    "%WT_EXE%" new-tab --title "TTS" powershell.exe -NoLogo -NoProfile -ExecutionPolicy Bypass -File "%~dp0runtime\launchers\start_tts.ps1"
)

endlocal
exit /b 0

:port_ready
powershell -NoProfile -ExecutionPolicy Bypass -Command "if (Get-NetTCPConnection -State Listen -LocalPort %~1 -ErrorAction SilentlyContinue) { exit 0 } else { exit 1 }"
if errorlevel 1 exit /b 1

echo [Evelyn] %~2 already listening on port %~1, skipping new launch
exit /b 2

:run_docker
powershell.exe -NoLogo -NoProfile -ExecutionPolicy Bypass -File "%~dp0runtime\launchers\start_docker_compose_services.ps1" -Profiles tts -Services tts
set "EXIT_CODE=%ERRORLEVEL%"
endlocal & exit /b %EXIT_CODE%
