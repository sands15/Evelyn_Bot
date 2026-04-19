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
    start "Bot" powershell.exe -NoLogo -NoProfile -ExecutionPolicy Bypass -File "%~dp0start_bot.ps1"
) else (
    "%WT_EXE%" ^
      new-tab --title "Main-LLM" cmd.exe /q /d /c "title Main-LLM ^& wsl.exe bash /mnt/c/Evelyn/evelyn_core/run_main_llm.sh" ^
      ; new-tab --title "Router-LLM" cmd.exe /q /d /c "title Router-LLM ^& wsl.exe bash /mnt/c/Evelyn/evelyn_core/run_router_llm.sh" ^
      ; new-tab --title "Sub-LLM" cmd.exe /q /d /c "title Sub-LLM ^& wsl.exe bash /mnt/c/Evelyn/evelyn_core/run_sub_llm.sh" ^
      ; new-tab --title "TTS" cmd.exe /q /d /c "title TTS ^& pushd %~dp0.. ^& set CUDA_VISIBLE_DEVICES=1 ^& %OMNIVOICE_VENV%\Scripts\python.exe -m omnivoice_server.cli --host 127.0.0.1 --port %TTS_PORT% --device cuda --profile-dir %OMNIVOICE_PROFILE_DIR%" ^
      ; new-tab --title "Bot" powershell.exe -NoLogo -NoProfile -ExecutionPolicy Bypass -File "%~dp0start_bot.ps1"
)

endlocal
