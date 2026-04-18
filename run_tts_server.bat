@echo off
chcp 65001 >nul
setlocal
call "%~dp0start_env.bat"
pushd "%~dp0"

title TTS

if not exist "%OMNIVOICE_PROFILE_DIR%" mkdir "%OMNIVOICE_PROFILE_DIR%"

set "CUDA_VISIBLE_DEVICES=1"
"%OMNIVOICE_VENV%\Scripts\python.exe" -m omnivoice_server.cli --host 127.0.0.1 --port %TTS_PORT% --device cuda --profile-dir "%OMNIVOICE_PROFILE_DIR%"

set "EXIT_CODE=%ERRORLEVEL%"
popd
endlocal & exit /b %EXIT_CODE%
