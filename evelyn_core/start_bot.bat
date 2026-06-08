@echo off
chcp 65001 >nul
setlocal
call "%~dp0start_env.bat"

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
if /I not "%EVELYN_USE_WINDOWS_TERMINAL%"=="true" set "WT_READY="
if not defined WT_READY (
    start "Bot" powershell.exe -NoLogo -NoProfile -ExecutionPolicy Bypass -File "%~dp0runtime\launchers\start_bot.ps1"
) else (
    "%WT_EXE%" new-tab --title "Bot" powershell.exe -NoLogo -NoProfile -ExecutionPolicy Bypass -File "%~dp0runtime\launchers\start_bot.ps1"
)

endlocal
exit /b 0

:run_inline
pushd "%~dp0.."

call :wait_for_port 127.0.0.1 %MAIN_LLM_PORT% Main-LLM
if errorlevel 1 goto :fail

call :wait_for_port 127.0.0.1 %ROUTER_LLM_PORT% Router-LLM
if errorlevel 1 goto :fail

call :wait_for_port 127.0.0.1 %SUB_LLM_PORT% Sub-LLM
if errorlevel 1 goto :fail

call :wait_for_port 127.0.0.1 %TTS_PORT% OmniVoice-TTS
if errorlevel 1 goto :fail

if "%DISCORD_BOT_TOKEN%"=="" (
  echo [Evelyn] DISCORD_BOT_TOKEN 환경변수가 설정되지 않았습니다.
  echo [Evelyn] .env.example 을 참고해서 먼저 환경변수를 준비하세요.
  popd
  endlocal & exit /b 1
)

if exist .venv\Scripts\python.exe (
  .venv\Scripts\python.exe main.py
) else (
  py -3 main.py
)

set "EXIT_CODE=%ERRORLEVEL%"
popd
endlocal & exit /b %EXIT_CODE%

:fail
set "EXIT_CODE=1"
popd
endlocal & exit /b %EXIT_CODE%

:wait_for_port
setlocal
set "WAIT_HOST=%~1"
set "WAIT_PORT=%~2"
set "WAIT_LABEL=%~3"

echo [Evelyn] Waiting for %WAIT_LABEL% at %WAIT_HOST%:%WAIT_PORT%
powershell -NoProfile -ExecutionPolicy Bypass -Command ^
  "$deadline = (Get-Date).AddSeconds(%START_WAIT_TIMEOUT_SEC%);" ^
  "while ((Get-Date) -lt $deadline) {" ^
  "  try {" ^
  "    $client = New-Object System.Net.Sockets.TcpClient;" ^
  "    $iar = $client.BeginConnect('%WAIT_HOST%', %WAIT_PORT%, $null, $null);" ^
  "    if ($iar.AsyncWaitHandle.WaitOne(1000)) {" ^
  "      $client.EndConnect($iar) | Out-Null;" ^
  "      $client.Close();" ^
  "      exit 0;" ^
  "    }" ^
  "    $client.Close();" ^
  "  } catch {}" ^
  "  Start-Sleep -Seconds %START_WAIT_INTERVAL_SEC%;" ^
  "}" ^
  "exit 1"
if errorlevel 1 (
    echo [Evelyn] %WAIT_LABEL% was not ready in time
    endlocal & exit /b 1
)

echo [Evelyn] %WAIT_LABEL% is ready
endlocal & exit /b 0
