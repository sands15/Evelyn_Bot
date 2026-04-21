@echo off
chcp 65001 >nul
setlocal

REM Root launcher shim. Delegate to the maintained unified launcher.
call "%~dp0evelyn_core\start.bat"
set "EXITCODE=%ERRORLEVEL%"

endlocal & exit /b %EXITCODE%

