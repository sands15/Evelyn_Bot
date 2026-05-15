@echo off
chcp 65001 >nul
setlocal

REM Root launcher shim for the Voyager Codex gateway.
call "%~dp0evelyn_core\start_codex_gateway.bat" %*
set "EXITCODE=%ERRORLEVEL%"

endlocal & exit /b %EXITCODE%
