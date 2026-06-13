@echo off
chcp 65001 >nul
setlocal
call "%~dp0start_env.bat"

if /I "%~1"=="--inline" if /I not "%EVELYN_ALLOW_LEGACY_HOST_START%"=="true" goto :run_docker
if /I not "%~1"=="--legacy-host" if /I not "%~1"=="--inline" goto :run_docker
if /I "%~1"=="--legacy-host" if /I not "%EVELYN_ALLOW_LEGACY_HOST_START%"=="true" (
    echo [Evelyn] Legacy host Codex-Gateway launch is blocked by default.
    echo [Evelyn] Set EVELYN_ALLOW_LEGACY_HOST_START=true only for explicit host-attached debugging.
    endlocal & exit /b 2
)

if "%VOYAGER_CODEX_GATEWAY_COMMAND%"=="" (
    echo [Evelyn] VOYAGER_CODEX_GATEWAY_COMMAND is not set.
    echo [Evelyn] Falling back to native ^`codex exec^` for gateway requests.
)

call :port_ready %VOYAGER_CODEX_GATEWAY_PORT% "Codex-Gateway"
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
    start "Codex-Gateway" powershell.exe -NoLogo -NoProfile -ExecutionPolicy Bypass -File "%~dp0runtime\launchers\start_codex_gateway.ps1"
) else (
    "%WT_EXE%" new-tab --title "Codex-Gateway" powershell.exe -NoLogo -NoProfile -ExecutionPolicy Bypass -File "%~dp0runtime\launchers\start_codex_gateway.ps1"
)

endlocal
exit /b 0

:run_inline
pushd "%~dp0.."
if exist "%VOYAGER_CODEX_GATEWAY_PYTHON_EXE%" (
    "%VOYAGER_CODEX_GATEWAY_PYTHON_EXE%" -m evelyn_core.codex_gateway_server --host "%VOYAGER_CODEX_GATEWAY_HOST%" --port %VOYAGER_CODEX_GATEWAY_PORT%
) else (
    py -3 -m evelyn_core.codex_gateway_server --host "%VOYAGER_CODEX_GATEWAY_HOST%" --port %VOYAGER_CODEX_GATEWAY_PORT%
)
set "EXIT_CODE=%ERRORLEVEL%"
popd
endlocal & exit /b %EXIT_CODE%

:port_ready
powershell -NoProfile -ExecutionPolicy Bypass -Command "if (Get-NetTCPConnection -State Listen -LocalPort %~1 -ErrorAction SilentlyContinue) { exit 0 } else { exit 1 }"
if errorlevel 1 exit /b 1

echo [Evelyn] %~2 already listening on port %~1, skipping new launch
exit /b 2

:run_docker
powershell.exe -NoLogo -NoProfile -ExecutionPolicy Bypass -File "%~dp0runtime\launchers\start_docker_compose_services.ps1" -Profiles voyager -Services codex_gateway
set "EXIT_CODE=%ERRORLEVEL%"
endlocal & exit /b %EXIT_CODE%
