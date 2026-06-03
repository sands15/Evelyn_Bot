@echo off
chcp 65001 >nul
setlocal

REM Root launcher shim for Evelyn local-only mode.
call "%~dp0evelyn_core\start_local.bat" %*
set "EXITCODE=%ERRORLEVEL%"

endlocal & exit /b %EXITCODE%
