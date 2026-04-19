@echo off
chcp 65001 >nul
setlocal
call "%~dp0start_env.bat"

if not exist "%OMNIVOICE_PROFILE_DIR%" mkdir "%OMNIVOICE_PROFILE_DIR%"

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
    call "%~dp0start_main_llm.bat"
    call "%~dp0start_router_llm.bat"
    call "%~dp0start_sub_llm.bat"
    call "%~dp0start_tts.bat"
    call "%~dp0start_bot.bat"
) else (
    set "MAIN_TERM_CMD=title Main-LLM && wsl.exe bash -lc \"%VENV_ACT% && export CUDA_VISIBLE_DEVICES=%MAIN_LLM_GPU% && if [ ! -f '%MAIN_LLM_MODEL%' ]; then echo '[Main-LLM] model file not found:' '%MAIN_LLM_MODEL%'; exit 1; fi && cd %LLAMA_DIR% && ./build/bin/llama-server -m '%MAIN_LLM_MODEL%' --host 0.0.0.0 --port %MAIN_LLM_PORT% --flash-attn on -ngl 999 -c %MAIN_LLM_CONTEXT% -np %MAIN_LLM_N_PARALLEL% --reasoning %MAIN_LLM_REASONING% --reasoning-budget %MAIN_LLM_REASONING_BUDGET%\""
    set "ROUTER_TERM_CMD=title Router-LLM && wsl.exe bash -lc \"%VENV_ACT% && export CUDA_VISIBLE_DEVICES=%ROUTER_LLM_GPU% && if [ ! -f '%ROUTER_LLM_MODEL%' ]; then echo '[Router-LLM] model file not found:' '%ROUTER_LLM_MODEL%'; exit 1; fi && cd %LLAMA_DIR% && ./build/bin/llama-server -m '%ROUTER_LLM_MODEL%' --host 0.0.0.0 --port %ROUTER_LLM_PORT% --flash-attn on -ngl 999 -c %ROUTER_LLM_CONTEXT% --reasoning on --reasoning-budget %ROUTER_LLM_REASONING_BUDGET%\""
    set "SUB_TERM_CMD=title Sub-LLM && wsl.exe bash -lc \"%VENV_ACT% && export CUDA_VISIBLE_DEVICES=%SUB_LLM_GPU% && if [ ! -f '%SUB_LLM_MODEL%' ]; then echo '[Sub-LLM] model file not found:' '%SUB_LLM_MODEL%'; exit 1; fi && cd %LLAMA_DIR% && ./build/bin/llama-server -m '%SUB_LLM_MODEL%' --host 0.0.0.0 --port %SUB_LLM_PORT% --flash-attn on -ngl 999 -c %SUB_LLM_CONTEXT% --reasoning on --reasoning-budget %SUB_LLM_REASONING_BUDGET%\""
    "%WT_EXE%" ^
      new-tab --title "Main-LLM" cmd.exe /q /d /c "%MAIN_TERM_CMD%" ^
      ; new-tab --title "Router-LLM" cmd.exe /q /d /k "%ROUTER_TERM_CMD%" ^
      ; new-tab --title "Sub-LLM" cmd.exe /q /d /k "%SUB_TERM_CMD%" ^
      ; new-tab --title "TTS" cmd.exe /q /d /k ""%~dp0start_tts.bat" --inline" ^
      ; new-tab --title "Bot" powershell.exe -NoExit -ExecutionPolicy Bypass -File "%~dp0start_bot.ps1"
)

endlocal
