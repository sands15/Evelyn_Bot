@echo off
chcp 65001 >nul
setlocal
call "%~dp0start_env.bat"

if not exist "%OMNIVOICE_PROFILE_DIR%" mkdir "%OMNIVOICE_PROFILE_DIR%"

call "%~dp0start_main_llm.bat"
call "%~dp0start_router_llm.bat"
call "%~dp0start_sub_llm.bat"
call "%~dp0start_tts.bat"
call "%~dp0start_bot.bat"

endlocal
