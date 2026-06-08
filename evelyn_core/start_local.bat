@echo off
chcp 65001 >nul
setlocal

if /I "%~1"=="--help" goto :usage
if /I "%~1"=="-h" goto :usage
if /I "%~1"=="/?" goto :usage

set "LOCAL_PROFILE=full"
if /I "%~1"=="--lightweight" set "LOCAL_PROFILE=lightweight"
if /I "%~2"=="--lightweight" set "LOCAL_PROFILE=lightweight"
if /I "%~1"=="--vision-light" set "LOCAL_PROFILE=lightweight"
if /I "%~2"=="--vision-light" set "LOCAL_PROFILE=lightweight"

if /I "%LOCAL_PROFILE%"=="lightweight" (
  if "%VISION_LOAD_OCR%"=="" set "VISION_LOAD_OCR=false"
  if "%VISION_WATCH_RUN_OCR%"=="" set "VISION_WATCH_RUN_OCR=false"
  if "%VISION_OCR_LAZY_LOAD%"=="" set "VISION_OCR_LAZY_LOAD=true"
  if "%VISION_OCR_UNLOAD_AFTER_REQUEST%"=="" set "VISION_OCR_UNLOAD_AFTER_REQUEST=true"
)

call "%~dp0start_env.bat"

set "DISCORD_ENABLED=false"
set "LOCAL_ONLY=true"
if "%LOCAL_BACKGROUND%"=="" set "LOCAL_BACKGROUND=true"
if /I "%~1"=="--background" set "LOCAL_BACKGROUND=true"
if /I "%~1"=="--foreground" set "LOCAL_BACKGROUND=false"
if /I "%~2"=="--background" set "LOCAL_BACKGROUND=true"
if /I "%~2"=="--foreground" set "LOCAL_BACKGROUND=false"
if "%LOCAL_MIC_ENABLED%"=="" set "LOCAL_MIC_ENABLED=true"
if "%LOCAL_MIC_START_THRESHOLD%"=="" set "LOCAL_MIC_START_THRESHOLD=0.002"
if "%LOCAL_MIC_CONTINUE_THRESHOLD%"=="" set "LOCAL_MIC_CONTINUE_THRESHOLD=0.001"
if "%LOCAL_MIC_MIN_VOICED_MS%"=="" set "LOCAL_MIC_MIN_VOICED_MS=160"
if "%LOCAL_MIC_WAVEFORM_FILTER_ENABLED%"=="" set "LOCAL_MIC_WAVEFORM_FILTER_ENABLED=false"
if "%CONTROL_PAGE_PORT%"=="" set "CONTROL_PAGE_PORT=8799"
if "%CONTROL_PAGE_AUTO_OPEN%"=="" set "CONTROL_PAGE_AUTO_OPEN=true"

echo [Evelyn] Starting local mode without Discord.
echo [Evelyn] Local profile: %LOCAL_PROFILE%
if /I "%LOCAL_PROFILE%"=="lightweight" echo [Evelyn] Lightweight profile: Vision OCR is not loaded at startup.
echo [Evelyn] Control page: http://127.0.0.1:%CONTROL_PAGE_PORT%/

if /I "%LOCAL_BACKGROUND%"=="true" (
  powershell.exe -NoLogo -NoProfile -ExecutionPolicy Bypass -File "%~dp0runtime\launchers\start_local_background.ps1"
  set "EXIT_CODE=%ERRORLEVEL%"
  endlocal & exit /b %EXIT_CODE%
)

call "%~dp0start_main_llm.bat"
call "%~dp0start_router_llm.bat"
call "%~dp0start_sub_llm.bat"
call "%~dp0start_tts.bat"
call "%~dp0start_vision.bat"

pushd "%~dp0.."

call :wait_for_port 127.0.0.1 %MAIN_LLM_PORT% Main-LLM
if errorlevel 1 goto :fail

call :wait_for_port 127.0.0.1 %ROUTER_LLM_PORT% Router-LLM
if errorlevel 1 goto :fail

call :wait_for_port 127.0.0.1 %SUB_LLM_PORT% Sub-LLM
if errorlevel 1 goto :fail

call :wait_for_port 127.0.0.1 %TTS_PORT% OmniVoice-TTS
if errorlevel 1 goto :fail

call :wait_for_port 127.0.0.1 %VISION_PORT% Vision
if errorlevel 1 goto :fail

echo [Evelyn] Launching local control process.
if /I not "%CONTROL_PAGE_AUTO_OPEN%"=="false" (
  start "Evelyn Control Page Opener" /min powershell.exe -NoProfile -ExecutionPolicy Bypass -WindowStyle Hidden -File "%~dp0runtime\launchers\open_control_page_when_ready.ps1" -Port %CONTROL_PAGE_PORT%
)
if exist .venv\Scripts\python.exe (
  .venv\Scripts\python.exe "%~dp0..\main.py"
) else (
  py -3 "%~dp0..\main.py"
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
echo Usage: start_local.bat [--background^|--foreground] [--lightweight]
echo.
echo Starts Evelyn in local-only mode:
echo   - Discord gateway disabled
echo   - Control page served by main.py
echo   - Main/Router/Sub/TTS services started if needed
echo   - Default: hidden background stack plus automatic control page open
echo   - Use --foreground to keep the bot process attached to this console
echo   - Use --lightweight to skip Falcon-OCR startup load for safer VRAM use
echo.
endlocal & exit /b 0
