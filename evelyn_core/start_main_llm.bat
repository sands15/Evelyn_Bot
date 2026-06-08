@echo off
chcp 65001 >nul
setlocal
call "%~dp0start_env.bat"

set "WSL_CMD=MAIN_LLM_BACKEND='%MAIN_LLM_BACKEND%' MAIN_LLM_VENV_ACT='%MAIN_LLM_VENV_ACT%' MAIN_LLM_GPU='%MAIN_LLM_GPU%' MAIN_LLM_PORT='%MAIN_LLM_PORT%' MAIN_LLM_CONTEXT='%MAIN_LLM_CONTEXT%' MAIN_LLM_N_PARALLEL='%MAIN_LLM_N_PARALLEL%' MAIN_LLM_CACHE_REUSE='%MAIN_LLM_CACHE_REUSE%' MAIN_LLM_HF='%MAIN_LLM_HF%' MAIN_LLM_MODEL='%MAIN_LLM_MODEL%' MAIN_LLM_LORA='%MAIN_LLM_LORA%' MAIN_LLM_LORA_SCALE='%MAIN_LLM_LORA_SCALE%' MAIN_LLM_STOP_TOKENS='%MAIN_LLM_STOP_TOKENS%' MAIN_LLM_REPEAT_LAST_N='%MAIN_LLM_REPEAT_LAST_N%' MAIN_LLM_REPEAT_PENALTY='%MAIN_LLM_REPEAT_PENALTY%' MAIN_LLM_PRESENCE_PENALTY='%MAIN_LLM_PRESENCE_PENALTY%' MAIN_LLM_FREQUENCY_PENALTY='%MAIN_LLM_FREQUENCY_PENALTY%' MAIN_LLM_QUANTIZATION='%MAIN_LLM_QUANTIZATION%' MAIN_LLM_DTYPE='%MAIN_LLM_DTYPE%' MAIN_LLM_GPU_MEMORY_UTILIZATION='%MAIN_LLM_GPU_MEMORY_UTILIZATION%' MAIN_LLM_CHAT_TEMPLATE_CONTENT_FORMAT='%MAIN_LLM_CHAT_TEMPLATE_CONTENT_FORMAT%' MAIN_LLM_ENFORCE_EAGER='%MAIN_LLM_ENFORCE_EAGER%' MAIN_LLM_FLASHINFER_SAMPLER='%MAIN_LLM_FLASHINFER_SAMPLER%' bash /mnt/c/Evelyn/evelyn_core/runtime/launchers/run_main_llm.sh"

call :port_ready %MAIN_LLM_PORT% "Main-LLM"
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
set "TERM_CMD=title Main-LLM && wsl.exe bash /mnt/c/Evelyn/evelyn_core/runtime/launchers/run_main_llm.sh"
if not defined WT_READY (
    start "Main-LLM" cmd.exe /q /d /c "%TERM_CMD%"
) else (
    "%WT_EXE%" new-tab --title "Main-LLM" cmd.exe /q /d /c "%TERM_CMD%"
)

endlocal
exit /b 0

:run_inline
title Main-LLM
wsl.exe bash -lc "%WSL_CMD%"
set "EXIT_CODE=%ERRORLEVEL%"
endlocal & exit /b %EXIT_CODE%

:port_ready
powershell -NoProfile -ExecutionPolicy Bypass -Command "if (Get-NetTCPConnection -State Listen -LocalPort %~1 -ErrorAction SilentlyContinue) { exit 0 } else { exit 1 }"
if errorlevel 1 exit /b 1

echo [Evelyn] %~2 already listening on port %~1, skipping new launch
exit /b 2
