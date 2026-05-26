@echo off
chcp 65001 > nul
cd /d "%~dp0\.."
set PYTHONUTF8=1
set CUDA_VISIBLE_DEVICES=0
python tools\ko_stt_scoreboard.py %*
pause
