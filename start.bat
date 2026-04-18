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
      new-tab --title "Main-LLM" cmd.exe /k ""%~dp0start_main_llm.bat" --inline" ^
      ; new-tab --title "Sub-LLM" cmd.exe /k ""%~dp0start_sub_llm.bat" --inline" ^
      ; new-tab --title "TTS" cmd.exe /k ""%~dp0start_tts.bat" --inline" ^
      ; new-tab --title "Bot" cmd.exe /k ""%~dp0start_bot.bat""
)

endlocal

