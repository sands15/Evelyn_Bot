@echo off
chcp 65001 >nul
setlocal

REM ===== 네 환경에 맞게 수정할 부분 =====
set "LLAMA_DIR=/mnt/c/Users/Admin/llama.cpp"
set "VENV_ACT=source ~/venvs/vllm-env/bin/activate"
set "OMNIVOICE_VENV=C:\Users\Admin\omnivoice-server\.venv"
set "OMNIVOICE_PROFILE_DIR=C:\Evelyn\omnivoice_profiles"
REM =====================================

if not exist "%OMNIVOICE_PROFILE_DIR%" mkdir "%OMNIVOICE_PROFILE_DIR%"

start "llama-9821" cmd /k wsl bash -lc "%VENV_ACT% && cd %LLAMA_DIR% && ./build/bin/llama-server -m /home/sands12/.cache/huggingface/hub/models--Qwen--Qwen2.5-1.5B-Instruct-GGUF/snapshots/91cad51170dc346986eccefdc2dd33a9da36ead9/qwen2.5-1.5b-instruct-q8_0.gguf --host 0.0.0.0 --port 9821 --flash-attn on -ngl 999 -c 2048"

timeout /t 2 >nul

start "llama-9820" cmd /k wsl bash -lc "%VENV_ACT% && cd %LLAMA_DIR% && ./build/bin/llama-server -m /home/sands12/.cache/huggingface/hub/models--HauhauCS--Qwen3.5-9B-Uncensored-HauhauCS-Aggressive/snapshots/335e9ef38ada3edf9f9a3a6c2836022c1ab76ea1/Qwen3.5-9B-Uncensored-HauhauCS-Aggressive-Q8_0.gguf --host 0.0.0.0 --port 9820 --flash-attn on -ngl 999 -c 2048 --reasoning-budget 0"

timeout /t 2 >nul

start "omnivoice-8880" cmd /k "set OMNIVOICE_PROFILE_DIR=%OMNIVOICE_PROFILE_DIR% && %OMNIVOICE_VENV%\Scripts\python.exe -m omnivoice_server.cli --host 127.0.0.1 --port 8880 --device cuda --profile-dir %OMNIVOICE_PROFILE_DIR%"

endlocal
