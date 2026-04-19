@echo off
chcp 65001 >nul
setlocal
call "%~dp0start_env.bat"

set "WSL_CMD=%VENV_ACT% && export CUDA_VISIBLE_DEVICES=%MAIN_LLM_GPU% && if [ ! -f '%MAIN_LLM_MODEL%' ]; then echo '[Main-LLM] model file not found:' '%MAIN_LLM_MODEL%'; exit 1; fi && cd %LLAMA_DIR% && ./build/bin/llama-server -m '%MAIN_LLM_MODEL%' --host 0.0.0.0 --port %MAIN_LLM_PORT% --flash-attn on -ngl 999 -c %MAIN_LLM_CONTEXT% --reasoning on --reasoning-budget %MAIN_LLM_REASONING_BUDGET%"

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
    start "Main-LLM" cmd.exe /k ""%~f0" --inline"
) else (
    "%WT_EXE%" new-tab --title "Main-LLM" cmd.exe /k ""%~f0" --inline"
)

endlocal
exit /b 0

:run_inline
wsl.exe bash -lc "%WSL_CMD%"
set "EXIT_CODE=%ERRORLEVEL%"
endlocal & exit /b %EXIT_CODE%
