@echo off
chcp 65001 >nul
setlocal
call "%~dp0start_env.bat"

if /I "%~1"=="--inline" if /I not "%EVELYN_ALLOW_LEGACY_HOST_START%"=="true" goto :run_docker
if /I not "%~1"=="--legacy-host" if /I not "%~1"=="--inline" goto :run_docker
if /I "%~1"=="--legacy-host" if /I not "%EVELYN_ALLOW_LEGACY_HOST_START%"=="true" (
    echo [Evelyn] Legacy host Voyager service launch is blocked by default.
    echo [Evelyn] Set EVELYN_ALLOW_LEGACY_HOST_START=true only for explicit host-attached debugging.
    endlocal & exit /b 2
)

if "%MINECRAFT_AUTONOMY_SERVICE_HOST%"=="" set "MINECRAFT_AUTONOMY_SERVICE_HOST=127.0.0.1"
if "%MINECRAFT_AUTONOMY_SERVICE_PORT%"=="" set "MINECRAFT_AUTONOMY_SERVICE_PORT=8765"

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
set "CMD_EXIT_SWITCH=/c"
if /I "%EVELYN_KEEP_CONSOLE_ON_EXIT%"=="true" set "CMD_EXIT_SWITCH=/k"
if not defined WT_READY (
    start "Voyager Service" cmd.exe %CMD_EXIT_SWITCH% "cd /d %~dp0.. && call \"%~f0\" --inline"
) else (
    "%WT_EXE%" new-tab --title "Voyager Service" cmd.exe %CMD_EXIT_SWITCH% "cd /d %~dp0.. && call \"%~f0\" --inline"
)

endlocal
exit /b 0

:run_inline
pushd "%~dp0.."

if exist .venv-voyager\Scripts\python.exe (
  .venv-voyager\Scripts\python.exe -m evelyn_core.voyager_service --host %MINECRAFT_AUTONOMY_SERVICE_HOST% --port %MINECRAFT_AUTONOMY_SERVICE_PORT%
) else (
  py -3 -m evelyn_core.voyager_service --host %MINECRAFT_AUTONOMY_SERVICE_HOST% --port %MINECRAFT_AUTONOMY_SERVICE_PORT%
)

set "EXIT_CODE=%ERRORLEVEL%"
popd
endlocal & exit /b %EXIT_CODE%

:run_docker
powershell.exe -NoLogo -NoProfile -ExecutionPolicy Bypass -File "%~dp0runtime\launchers\start_docker_compose_services.ps1" -Profiles voyager -Services voyager
set "EXIT_CODE=%ERRORLEVEL%"
endlocal & exit /b %EXIT_CODE%
