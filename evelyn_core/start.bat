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
set "SUPERVISOR=%~dp0runtime\launchers\supervise_service.ps1"

start "" "%WT_EXE%" -w %WT_WINDOW% new-tab --title "Main-LLM" powershell.exe -NoLogo -NoProfile -ExecutionPolicy Bypass -File "%SUPERVISOR%" -Name "Main-LLM" -Workdir "%~dp0.." -Command "& '%~dp0start_main_llm.bat' --inline"
timeout /t 1 /nobreak >nul
"%WT_EXE%" -w %WT_WINDOW% new-tab --title "Router-LLM" powershell.exe -NoLogo -NoProfile -ExecutionPolicy Bypass -File "%SUPERVISOR%" -Name "Router-LLM" -Workdir "%~dp0.." -Command "& '%~dp0start_router_llm.bat' --inline"
"%WT_EXE%" -w %WT_WINDOW% new-tab --title "Sub-LLM" powershell.exe -NoLogo -NoProfile -ExecutionPolicy Bypass -File "%SUPERVISOR%" -Name "Sub-LLM" -Workdir "%~dp0.." -Command "& '%~dp0start_sub_llm.bat' --inline"
"%WT_EXE%" -w %WT_WINDOW% new-tab --title "TTS" powershell.exe -NoLogo -NoProfile -ExecutionPolicy Bypass -File "%SUPERVISOR%" -Name "TTS" -Workdir "%~dp0.." -Command "& '%~dp0runtime\launchers\start_tts.ps1'"
"%WT_EXE%" -w %WT_WINDOW% new-tab --title "Bot" powershell.exe -NoLogo -NoProfile -ExecutionPolicy Bypass -File "%SUPERVISOR%" -Name "Bot" -Workdir "%~dp0.." -Command "& '%~dp0runtime\launchers\start_bot.ps1'"

endlocal
