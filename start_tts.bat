@echo off
chcp 65001 >nul
setlocal
call "%~dp0start_env.bat"

if not exist "%OMNIVOICE_PROFILE_DIR%" mkdir "%OMNIVOICE_PROFILE_DIR%"

if /I "%~1"=="--inline" goto :run_inline

where wt >nul 2>nul
if errorlevel 1 (
    start "TTS" cmd.exe /k ""%~dp0run_tts_server.bat""
) else (
    wt new-tab --title "TTS" cmd.exe /k ""%~dp0run_tts_server.bat""
)

endlocal
exit /b 0

:run_inline
call "%~dp0run_tts_server.bat"
set "EXIT_CODE=%ERRORLEVEL%"
endlocal & exit /b %EXIT_CODE%
