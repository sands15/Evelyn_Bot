@echo off
chcp 65001 >nul
setlocal
call "%~dp0start_env.bat"

powershell.exe -NoLogo -NoProfile -ExecutionPolicy Bypass -File "%~dp0runtime\launchers\start_docker_compose_services.ps1" -Profiles codex-gateway -Services codex_gateway
set "EXIT_CODE=%ERRORLEVEL%"
endlocal & exit /b %EXIT_CODE%
