@echo off
chcp 65001 >nul
setlocal
set OPUS_ERROR_TO_SILENCE=false

if "%DISCORD_BOT_TOKEN%"=="" (
  echo [Evelyn] DISCORD_BOT_TOKEN 환경변수가 설정되지 않았습니다.
  echo [Evelyn] .env.example 을 참고해서 먼저 환경변수를 준비하세요.
  exit /b 1
)

if exist .venv\Scripts\python.exe (
  .venv\Scripts\python.exe main.py
) else (
  py -3 main.py
)

endlocal
