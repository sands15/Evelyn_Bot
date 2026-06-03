@echo off
chcp 65001 >nul
setlocal
call "%~dp0start_env.bat"

if /I "%~1"=="--help" goto :usage
if /I "%~1"=="-h" goto :usage
if /I "%~1"=="/?" goto :usage

set "DISCORD_ENABLED=false"
set "LOCAL_ONLY=true"
set "LOCAL_MIC_ENABLED=false"
if "%CONTROL_PAGE_PORT%"=="" set "CONTROL_PAGE_PORT=8799"

echo [Evelyn] Starting local mode without Discord.
echo [Evelyn] Control page: http://127.0.0.1:%CONTROL_PAGE_PORT%/

call "%~dp0start_main_llm.bat"
call "%~dp0start_router_llm.bat"
call "%~dp0start_sub_llm.bat"
call "%~dp0start_tts.bat"

pushd "%~dp0.."

call :wait_for_port 127.0.0.1 %MAIN_LLM_PORT% Main-LLM
if errorlevel 1 goto :fail

call :wait_for_port 127.0.0.1 %ROUTER_LLM_PORT% Router-LLM
if errorlevel 1 goto :fail

call :wait_for_port 127.0.0.1 %SUB_LLM_PORT% Sub-LLM
if errorlevel 1 goto :fail

call :wait_for_port 127.0.0.1 %TTS_PORT% OmniVoice-TTS
if errorlevel 1 goto :fail

echo [Evelyn] Launching local control process.
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

:usage
echo Usage: start_local.bat
echo.
echo Starts Evelyn in local-only mode:
echo   - Discord gateway disabled
echo   - Control page served by main.py
echo   - Main/Router/Sub/TTS services started if needed
echo.
endlocal & exit /b 0
