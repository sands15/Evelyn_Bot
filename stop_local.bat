@echo off
chcp 65001 >nul
setlocal

REM Root stop shim for Evelyn local-only mode.
call "%~dp0evelyn_core\stop_local.bat" %*
set "EXITCODE=%ERRORLEVEL%"

endlocal & exit /b %EXITCODE%
