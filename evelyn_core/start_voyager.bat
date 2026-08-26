@echo off
chcp 65001 >nul
setlocal
call "%~dp0start_env.bat"

if /I "%~1"=="--inline" if /I not "%EVELYN_ALLOW_LEGACY_HOST_START%"=="true" goto :run_docker
if /I not "%~1"=="--legacy-host" if /I not "%~1"=="--inline" goto :run_docker
if /I "%~1"=="--legacy-host" if /I not "%EVELYN_ALLOW_LEGACY_HOST_START%"=="true" (
    echo [Evelyn] Legacy host Voyager launch is blocked by default.
    echo [Evelyn] Set EVELYN_ALLOW_LEGACY_HOST_START=true only for explicit host-attached debugging.
    endlocal & exit /b 2
)

if "%MINECRAFT_AUTONOMY_SERVICE_HOST%"=="" set "MINECRAFT_AUTONOMY_SERVICE_HOST=127.0.0.1"
if "%MINECRAFT_AUTONOMY_SERVICE_PORT%"=="" set "MINECRAFT_AUTONOMY_SERVICE_PORT=8765"
if "%VOYAGER_PYTHON_EXE%"=="" set "VOYAGER_PYTHON_EXE=%~dp0..\.venv-voyager\Scripts\python.exe"
if "%VOYAGER_AUTO_START%"=="" set "VOYAGER_AUTO_START=false"
if "%VOYAGER_START_GOAL%"=="" set "VOYAGER_START_GOAL=discovering as many diverse things as possible"

set "VOYAGER_NO_AUTOSTART="
if /I "%~1"=="--inline" goto :run_inline
if /I "%~1"=="--no-autostart" set "VOYAGER_NO_AUTOSTART=1"

echo [Evelyn] start_voyager.bat only launches Voyager-specific services.
echo [Evelyn] Start the base Evelyn stack separately with start.bat if needed.

set "VOYAGER_SERVICE_ALREADY_RUNNING="
call :port_ready %MINECRAFT_AUTONOMY_SERVICE_PORT% "Voyager-Service"
if %ERRORLEVEL%==2 set "VOYAGER_SERVICE_ALREADY_RUNNING=1"

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

if not defined VOYAGER_SERVICE_ALREADY_RUNNING (
    if not defined WT_READY (
        start "Voyager-Service" powershell.exe -NoLogo -NoProfile -ExecutionPolicy Bypass -File "%~dp0runtime\launchers\start_voyager_service.ps1"
    ) else (
        "%WT_EXE%" new-tab --title "Voyager-Service" powershell.exe -NoLogo -NoProfile -ExecutionPolicy Bypass -File "%~dp0runtime\launchers\start_voyager_service.ps1"
    )
    call :wait_port %MINECRAFT_AUTONOMY_SERVICE_PORT% 30
    if errorlevel 1 (
        echo [Evelyn] Voyager-Service did not start listening on port %MINECRAFT_AUTONOMY_SERVICE_PORT% in time.
        goto :done
    )
)

if /I not "%VOYAGER_AUTO_START%"=="true" goto :done
if defined VOYAGER_NO_AUTOSTART goto :done
echo [Evelyn] Direct Voyager auto-start is disabled by the world-action lease policy.
echo [Evelyn] Use the Control Page Minecraft tool or the Discord guild-prefix minecraft-connect command.

:done
endlocal
exit /b 0

:run_inline
pushd "%~dp0.."
if exist "%VOYAGER_PYTHON_EXE%" (
    "%VOYAGER_PYTHON_EXE%" -m evelyn_core.voyager_service --host "%MINECRAFT_AUTONOMY_SERVICE_HOST%" --port %MINECRAFT_AUTONOMY_SERVICE_PORT%
) else (
    py -3 -m evelyn_core.voyager_service --host "%MINECRAFT_AUTONOMY_SERVICE_HOST%" --port %MINECRAFT_AUTONOMY_SERVICE_PORT%
)
set "EXIT_CODE=%ERRORLEVEL%"
popd
endlocal & exit /b %EXIT_CODE%

:port_ready
powershell -NoProfile -ExecutionPolicy Bypass -Command "if (Get-NetTCPConnection -State Listen -LocalPort %~1 -ErrorAction SilentlyContinue) { exit 0 } else { exit 1 }"
if errorlevel 1 exit /b 1

echo [Evelyn] %~2 already listening on port %~1, skipping new launch
exit /b 2

:wait_port
set "WAIT_PORT=%~1"
set "WAIT_SECONDS=%~2"
if "%WAIT_SECONDS%"=="" set "WAIT_SECONDS=30"
powershell -NoProfile -ExecutionPolicy Bypass -Command "$deadline=(Get-Date).AddSeconds(%WAIT_SECONDS%); while((Get-Date) -lt $deadline){ if(Get-NetTCPConnection -State Listen -LocalPort %WAIT_PORT% -ErrorAction SilentlyContinue){ exit 0 }; Start-Sleep -Milliseconds 500 }; exit 1"
exit /b %ERRORLEVEL%

:run_docker
powershell.exe -NoLogo -NoProfile -ExecutionPolicy Bypass -File "%~dp0runtime\launchers\start_docker_compose_services.ps1" -Profiles voyager -Services router_llm,minecraft_llm,voyager
set "EXIT_CODE=%ERRORLEVEL%"
endlocal & exit /b %EXIT_CODE%
