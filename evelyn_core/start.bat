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
    set "MAIN_TERM_CMD=title Main-LLM && call \"%~dp0start_main_llm.bat\" --inline"
    set "ROUTER_TERM_CMD=title Router-LLM && call \"%~dp0start_router_llm.bat\" --inline"
    set "SUB_TERM_CMD=title Sub-LLM && call \"%~dp0start_sub_llm.bat\" --inline"
    "%WT_EXE%" ^
      new-tab --title "Main-LLM" cmd.exe /q /d /c "%MAIN_TERM_CMD%" ^
      ; new-tab --title "Router-LLM" cmd.exe /q /d /k "%ROUTER_TERM_CMD%" ^
      ; new-tab --title "Sub-LLM" cmd.exe /q /d /k "%SUB_TERM_CMD%" ^
      ; new-tab --title "TTS" cmd.exe /q /d /k "title TTS && call \"%~dp0start_tts.bat\" --inline" ^
      ; new-tab --title "Bot" powershell.exe -NoExit -ExecutionPolicy Bypass -File "%~dp0start_bot.ps1"
)

endlocal
