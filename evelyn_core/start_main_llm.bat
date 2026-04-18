@echo off
chcp 65001 >nul
setlocal
call "%~dp0start_env.bat"

set "WSL_CMD=%VENV_ACT% && export CUDA_VISIBLE_DEVICES=%MAIN_LLM_GPU% && cd %LLAMA_DIR% && ./build/bin/llama-server -m %MAIN_LLM_MODEL% --host 0.0.0.0 --port %MAIN_LLM_PORT% --flash-attn on -ngl 999 -c %MAIN_LLM_CONTEXT% --reasoning on --reasoning-budget %MAIN_LLM_REASONING_BUDGET%"

if /I "%~1"=="--inline" goto :run_inline

where wt >nul 2>nul
if errorlevel 1 (
    start "Main-LLM" wsl.exe bash -lc "%WSL_CMD%"
) else (
    wt new-tab --title "Main-LLM" wsl.exe bash -lc "%WSL_CMD%"
)

endlocal
exit /b 0

:run_inline
wsl.exe bash -lc "%WSL_CMD%"
set "EXIT_CODE=%ERRORLEVEL%"
endlocal & exit /b %EXIT_CODE%
