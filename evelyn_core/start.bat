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
    endlocal
    exit /b 0
)

set "WT_WINDOW=evelyn"

start "" "%WT_EXE%" -w %WT_WINDOW% new-tab --title "Main-LLM" wsl.exe bash /mnt/c/Evelyn/evelyn_core/run_main_llm.sh
timeout /t 1 /nobreak >nul
"%WT_EXE%" -w %WT_WINDOW% new-tab --title "Router-LLM" wsl.exe bash /mnt/c/Evelyn/evelyn_core/run_router_llm.sh
"%WT_EXE%" -w %WT_WINDOW% new-tab --title "Sub-LLM" wsl.exe bash /mnt/c/Evelyn/evelyn_core/run_sub_llm.sh
"%WT_EXE%" -w %WT_WINDOW% new-tab --title "TTS" powershell.exe -NoLogo -NoProfile -ExecutionPolicy Bypass -File "%~dp0start_tts.ps1"
"%WT_EXE%" -w %WT_WINDOW% new-tab --title "Bot" powershell.exe -NoLogo -NoProfile -ExecutionPolicy Bypass -File "%~dp0start_bot.ps1"

endlocal
