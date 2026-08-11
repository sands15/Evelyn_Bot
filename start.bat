@echo off
chcp 65001 >nul
setlocal
if "%EVELYN_KEEP_CONSOLE_ON_EXIT%"=="" set "EVELYN_KEEP_CONSOLE_ON_EXIT=true"

REM Root launcher shim. Delegate to the maintained unified launcher.
call "%~dp0evelyn_core\start.bat"
set "EXITCODE=%ERRORLEVEL%"

if not "%EXITCODE%"=="0" (
  echo.
  echo [Evelyn] Startup stopped. See the errorCode above and README.md.
  if /I "%EVELYN_KEEP_CONSOLE_ON_EXIT%"=="true" pause
)

endlocal & exit /b %EXITCODE%

