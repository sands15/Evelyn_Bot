@echo off
chcp 65001 >nul
setlocal
call "%~dp0start_env.bat"

set "WSL_CMD=%VENV_ACT% && export CUDA_VISIBLE_DEVICES=%ROUTER_LLM_GPU% && if [ ! -f '%ROUTER_LLM_MODEL%' ]; then echo '[Router-LLM] model file not found:' '%ROUTER_LLM_MODEL%'; exit 1; fi && cd %LLAMA_DIR% && ./build/bin/llama-server -m '%ROUTER_LLM_MODEL%' --host 0.0.0.0 --port %ROUTER_LLM_PORT% --flash-attn on -ngl 999 -c %ROUTER_LLM_CONTEXT% --reasoning on --reasoning-budget %ROUTER_LLM_REASONING_BUDGET%"

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
if not defined WT_READY (
    start "Router-LLM" wsl.exe bash -lc "%WSL_CMD%"
) else (
    "%WT_EXE%" new-tab --title "Router-LLM" wsl.exe bash -lc "%WSL_CMD%"
)

endlocal
exit /b 0

:run_inline
wsl.exe bash -lc "%WSL_CMD%"
set "EXIT_CODE=%ERRORLEVEL%"
endlocal & exit /b %EXIT_CODE%
