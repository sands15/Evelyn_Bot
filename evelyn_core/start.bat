@echo off
chcp 65001 >nul
setlocal
call "%~dp0start_env.bat"

if not exist "%OMNIVOICE_PROFILE_DIR%" mkdir "%OMNIVOICE_PROFILE_DIR%"
if /I "%~1"=="--legacy-host" (
  if /I not "%EVELYN_ALLOW_LEGACY_HOST_START%"=="true" (
    echo [Evelyn] Legacy host stack launch is blocked by default.
    echo [Evelyn] Set EVELYN_ALLOW_LEGACY_HOST_START=true only for explicit host-attached debugging.
    endlocal & exit /b 2
  )
  powershell.exe -NoLogo -NoProfile -ExecutionPolicy Bypass -File "%~dp0runtime\launchers\start_background_stack.ps1"
  set "EXITCODE=%ERRORLEVEL%"
  endlocal & exit /b %EXITCODE%
)

call "%~dp0start_local.bat" %*
set "EXITCODE=%ERRORLEVEL%"
endlocal & exit /b %EXITCODE%
