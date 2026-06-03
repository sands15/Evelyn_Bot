@echo off
chcp 65001 >nul
setlocal

REM Stop Evelyn local-only runtime using narrow Evelyn ownership checks.
powershell.exe -NoLogo -NoProfile -ExecutionPolicy Bypass -File "%~dp0runtime\launchers\stop_evelyn_local.ps1" %*
set "EXITCODE=%ERRORLEVEL%"

endlocal & exit /b %EXITCODE%
