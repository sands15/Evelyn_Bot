@echo off
chcp 65001 >nul
setlocal
call "%~dp0start_env.bat"

set "WSL_CMD=VENV_ACT='%VENV_ACT%' LLAMA_DIR='%LLAMA_DIR%' ROUTER_LLM_GPU='%ROUTER_LLM_GPU%' ROUTER_LLM_PORT='%ROUTER_LLM_PORT%' ROUTER_LLM_CONTEXT='%ROUTER_LLM_CONTEXT%' ROUTER_LLM_REASONING='%ROUTER_LLM_REASONING%' ROUTER_LLM_REASONING_BUDGET='%ROUTER_LLM_REASONING_BUDGET%' ROUTER_LLM_HF='%ROUTER_LLM_HF%' ROUTER_LLM_MODEL='%ROUTER_LLM_MODEL%' bash /mnt/c/Evelyn/evelyn_core/runtime/launchers/run_router_llm.sh"

call :port_ready %ROUTER_LLM_PORT% "Router-LLM"
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
set "TERM_CMD=title Router-LLM && wsl.exe bash /mnt/c/Evelyn/evelyn_core/runtime/launchers/run_router_llm.sh"
if not defined WT_READY (
    start "Router-LLM" cmd.exe /q /d /c "%TERM_CMD%"
) else (
    "%WT_EXE%" new-tab --title "Router-LLM" cmd.exe /q /d /c "%TERM_CMD%"
)

endlocal
exit /b 0

:run_inline
title Router-LLM
wsl.exe bash -lc "%WSL_CMD%"
set "EXIT_CODE=%ERRORLEVEL%"
endlocal & exit /b %EXIT_CODE%

:port_ready
powershell -NoProfile -ExecutionPolicy Bypass -Command "if (Get-NetTCPConnection -State Listen -LocalPort %~1 -ErrorAction SilentlyContinue) { exit 0 } else { exit 1 }"
if errorlevel 1 exit /b 1

exit /b 2
