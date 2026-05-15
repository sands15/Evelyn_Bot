@echo off
chcp 65001 >nul
setlocal

REM Root launcher shim for the Evelyn + Voyager stack.
call "%~dp0evelyn_core\start_voyager.bat" %*
set "EXITCODE=%ERRORLEVEL%"

endlocal & exit /b %EXITCODE%
