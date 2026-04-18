@echo off
chcp 65001 >nul
setlocal
call "%~dp0start_env.bat"

if not exist "%OMNIVOICE_PROFILE_DIR%" mkdir "%OMNIVOICE_PROFILE_DIR%"

if /I "%~1"=="--inline" goto :run_inline

where wt >nul 2>nul
if errorlevel 1 (
    start "TTS" cmd.exe /k ""%~f0" --inline"
) else (
    wt new-tab --title "TTS" cmd.exe /k ""%~f0" --inline"
)

endlocal
exit /b 0

:run_inline
pushd "%~dp0.."
title TTS
set "CUDA_VISIBLE_DEVICES=1"
"%OMNIVOICE_VENV%\Scripts\python.exe" -m omnivoice_server.cli --host 127.0.0.1 --port %TTS_PORT% --device cuda --profile-dir "%OMNIVOICE_PROFILE_DIR%"
set "EXIT_CODE=%ERRORLEVEL%"
popd
endlocal & exit /b %EXIT_CODE%
