@echo off
chcp 65001 >nul
setlocal
call "%~dp0start_env.bat"

if not exist "%OMNIVOICE_PROFILE_DIR%" mkdir "%OMNIVOICE_PROFILE_DIR%"

where wt >nul 2>nul
if errorlevel 1 (
    call "%~dp0start_main_llm.bat"
    call "%~dp0start_sub_llm.bat"
    call "%~dp0start_tts.bat"
    call "%~dp0start_bot.bat"
) else (
    wt ^
      new-tab --title "Main-LLM" wsl.exe bash -lc "%VENV_ACT% && export CUDA_VISIBLE_DEVICES=%MAIN_LLM_GPU% && cd %LLAMA_DIR% && ./build/bin/llama-server -m %MAIN_LLM_MODEL% --host 0.0.0.0 --port %MAIN_LLM_PORT% --flash-attn on -ngl 999 -c %MAIN_LLM_CONTEXT% --reasoning on --reasoning-budget %MAIN_LLM_REASONING_BUDGET%" ^
      ; new-tab --title "Sub-LLM" wsl.exe bash -lc "%VENV_ACT% && export CUDA_VISIBLE_DEVICES=%SUB_LLM_GPU% && cd %LLAMA_DIR% && ./build/bin/llama-server -m %SUB_LLM_MODEL% --host 0.0.0.0 --port %SUB_LLM_PORT% --flash-attn on -ngl 999 -c %SUB_LLM_CONTEXT% --reasoning on --reasoning-budget %SUB_LLM_REASONING_BUDGET%" ^
      ; new-tab --title "TTS" powershell.exe -NoExit -ExecutionPolicy Bypass -File "%~dp0start_tts.ps1" ^
      ; new-tab --title "Bot" powershell.exe -NoExit -ExecutionPolicy Bypass -File "%~dp0start_bot.ps1"
)

endlocal

