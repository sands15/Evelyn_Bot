@echo off
chcp 65001 >nul
setlocal

title TTS

if "%OMNIVOICE_VENV%"=="" set "OMNIVOICE_VENV=C:\Users\Admin\omnivoice-server\.venv"
if "%OMNIVOICE_PROFILE_DIR%"=="" set "OMNIVOICE_PROFILE_DIR=%~dp0omnivoice_profiles"

if not exist "%OMNIVOICE_PROFILE_DIR%" mkdir "%OMNIVOICE_PROFILE_DIR%"

set "CUDA_VISIBLE_DEVICES=1"
"%OMNIVOICE_VENV%\Scripts\python.exe" -m omnivoice_server.cli --host 127.0.0.1 --port 8880 --device cuda --profile-dir "%OMNIVOICE_PROFILE_DIR%"

endlocal
