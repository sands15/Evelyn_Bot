@echo off
chcp 65001 >nul
setlocal
call "%~dp0start_env.bat"

set "WSL_CMD=%VENV_ACT% && export CUDA_VISIBLE_DEVICES=%SUB_LLM_GPU% && cd %LLAMA_DIR% && ./build/bin/llama-server -m %SUB_LLM_MODEL% --host 0.0.0.0 --port %SUB_LLM_PORT% --flash-attn on -ngl 999 -c %SUB_LLM_CONTEXT% --reasoning on --reasoning-budget %SUB_LLM_REASONING_BUDGET%"

where wt >nul 2>nul
if errorlevel 1 (
    start "Sub-LLM" wsl.exe bash -lc "%WSL_CMD%"
) else (
    wt new-tab --title "Sub-LLM" wsl.exe bash -lc "%WSL_CMD%"
)

endlocal
