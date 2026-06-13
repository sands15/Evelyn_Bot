@echo off
chcp 65001 >nul
setlocal
call "%~dp0start_env.bat"

if /I "%~1"=="--inline" if /I not "%EVELYN_ALLOW_LEGACY_HOST_START%"=="true" goto :run_docker
if /I "%~1"=="--legacy-host" set "EVELYN_ALLOW_LEGACY_HOST_START=true"
if /I not "%~1"=="--inline" if /I not "%~1"=="--legacy-host" goto :run_docker

set "WSL_CMD=VENV_ACT='%VENV_ACT%' LLAMA_DIR='%LLAMA_DIR%' SUB_LLM_GPU='%SUB_LLM_GPU%' SUB_LLM_PORT='%SUB_LLM_PORT%' SUB_LLM_CONTEXT='%SUB_LLM_CONTEXT%' SUB_LLM_REASONING_BUDGET='%SUB_LLM_REASONING_BUDGET%' SUB_LLM_HF='%SUB_LLM_HF%' SUB_LLM_MODEL='%SUB_LLM_MODEL%' SUB_LLM_N_GPU_LAYERS='%SUB_LLM_N_GPU_LAYERS%' SUB_LLM_THREADS='%SUB_LLM_THREADS%' SUB_LLM_CACHE_TYPE_K='%SUB_LLM_CACHE_TYPE_K%' SUB_LLM_CACHE_TYPE_V='%SUB_LLM_CACHE_TYPE_V%' bash /mnt/c/Evelyn/evelyn_core/runtime/launchers/run_sub_llm.sh"

call :port_ready %SUB_LLM_PORT% "Sub-LLM"
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
set "TERM_CMD=title Sub-LLM && wsl.exe bash /mnt/c/Evelyn/evelyn_core/runtime/launchers/run_sub_llm.sh"
if not defined WT_READY (
    start "Sub-LLM" cmd.exe /q /d /c "%TERM_CMD%"
) else (
    "%WT_EXE%" new-tab --title "Sub-LLM" cmd.exe /q /d /c "%TERM_CMD%"
)

endlocal
exit /b 0

:run_inline
title Sub-LLM
wsl.exe bash -lc "%WSL_CMD%"
set "EXIT_CODE=%ERRORLEVEL%"
endlocal & exit /b %EXIT_CODE%

:port_ready
powershell -NoProfile -ExecutionPolicy Bypass -Command "if (Get-NetTCPConnection -State Listen -LocalPort %~1 -ErrorAction SilentlyContinue) { exit 0 } else { exit 1 }"
if errorlevel 1 exit /b 1

exit /b 2

:run_docker
powershell.exe -NoLogo -NoProfile -ExecutionPolicy Bypass -File "%~dp0runtime\launchers\start_docker_compose_services.ps1" -Profiles llm -Services sub_llm
set "EXIT_CODE=%ERRORLEVEL%"
endlocal & exit /b %EXIT_CODE%
