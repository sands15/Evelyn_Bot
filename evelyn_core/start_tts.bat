@echo off
chcp 65001 >nul
setlocal
call "%~dp0start_env.bat"

if not exist "%OMNIVOICE_PROFILE_DIR%" mkdir "%OMNIVOICE_PROFILE_DIR%"

call :port_ready %TTS_PORT% "OmniVoice-TTS"
if %ERRORLEVEL%==2 exit /b 0

if /I "%~1"=="--inline" goto :run_inline

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
    start "TTS" cmd.exe /q /d /c "title TTS ^& pushd %~dp0.. ^& set CUDA_VISIBLE_DEVICES=1 ^& %OMNIVOICE_VENV%\Scripts\python.exe -m omnivoice_server.cli --host 127.0.0.1 --port %TTS_PORT% --device cuda --profile-dir %OMNIVOICE_PROFILE_DIR%"
) else (
    "%WT_EXE%" new-tab --title "TTS" cmd.exe /q /d /c "title TTS ^& pushd %~dp0.. ^& set CUDA_VISIBLE_DEVICES=1 ^& %OMNIVOICE_VENV%\Scripts\python.exe -m omnivoice_server.cli --host 127.0.0.1 --port %TTS_PORT% --device cuda --profile-dir %OMNIVOICE_PROFILE_DIR%"
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

:port_ready
powershell -NoProfile -ExecutionPolicy Bypass -Command "if (Get-NetTCPConnection -State Listen -LocalPort %~1 -ErrorAction SilentlyContinue) { exit 0 } else { exit 1 }"
if errorlevel 1 exit /b 1

echo [Evelyn] %~2 already listening on port %~1, skipping new launch
exit /b 2
