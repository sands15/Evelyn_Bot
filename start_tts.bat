@echo off
chcp 65001 >nul
setlocal
call "%~dp0start_env.bat"

if not exist "%OMNIVOICE_PROFILE_DIR%" mkdir "%OMNIVOICE_PROFILE_DIR%"

where wt >nul 2>nul
if errorlevel 1 (
    start "TTS" cmd.exe /k ""%~dp0run_tts_server.bat""
) else (
    wt new-tab --title "TTS" cmd.exe /k ""%~dp0run_tts_server.bat""
)

endlocal
