@echo off
chcp 65001 >nul
setlocal
call "%~dp0start_env.bat"

set "WSL_CMD=%VENV_ACT% && export CUDA_VISIBLE_DEVICES=%SUB_LLM_GPU% && MODEL_PATH='%SUB_LLM_MODEL%' && if [ -z \"$MODEL_PATH\" ]; then SNAPSHOT=$(cat '%SUB_LLM_MODEL_REPO%/refs/main' 2>/dev/null) && MODEL_PATH='%SUB_LLM_MODEL_REPO%/snapshots/'\"$SNAPSHOT\"'/%SUB_LLM_MODEL_FILE%'; fi && if [ ! -f \"$MODEL_PATH\" ]; then echo '[Sub-LLM] model file not found:' \"$MODEL_PATH\"; exit 1; fi && cd %LLAMA_DIR% && ./build/bin/llama-server -m \"$MODEL_PATH\" --host 0.0.0.0 --port %SUB_LLM_PORT% --flash-attn on -ngl 999 -c %SUB_LLM_CONTEXT% --reasoning on --reasoning-budget %SUB_LLM_REASONING_BUDGET%"

if /I "%~1"=="--inline" goto :run_inline

where wt >nul 2>nul
if errorlevel 1 (
    start "Sub-LLM" wsl.exe bash -lc "%WSL_CMD%"
) else (
    wt new-tab --title "Sub-LLM" wsl.exe bash -lc "%WSL_CMD%"
)

endlocal
exit /b 0

:run_inline
wsl.exe bash -lc "%WSL_CMD%"
set "EXIT_CODE=%ERRORLEVEL%"
endlocal & exit /b %EXIT_CODE%
