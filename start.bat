@echo off
chcp 65001 >nul
setlocal

REM ===== 네 환경에 맞게 수정할 부분 =====
set "LLAMA_DIR=/mnt/c/Users/Admin/llama.cpp"
set "VENV_ACT=source ~/venvs/vllm-env/bin/activate"
set "OMNIVOICE_VENV=C:\Users\Admin\omnivoice-server\.venv"
set "OMNIVOICE_PROFILE_DIR=C:\Evelyn\omnivoice_profiles"
set "OPUS_ERROR_TO_SILENCE=false"
set "STT_USE_RAW_48K=false"
REM =====================================

if not exist "%OMNIVOICE_PROFILE_DIR%" mkdir "%OMNIVOICE_PROFILE_DIR%"

wt ^
 new-tab --title "Main-LLM" wsl.exe bash -lc "%VENV_ACT% && export CUDA_VISIBLE_DEVICES=1 && cd %LLAMA_DIR% && ./build/bin/llama-server -m /home/sands12/.cache/huggingface/hub/models--unsloth--gemma-4-E4B-it-GGUF/snapshots/ce152932ac27bc40bc9c727386760424d50bb456/gemma-4-E4B-it-Q5_K_M.gguf --host 0.0.0.0 --port 9820 --flash-attn on -ngl 999 -c 4096 --reasoning on --reasoning-budget 64" ^
  ; new-tab --title "Sub-LLM" wsl.exe bash -lc "%VENV_ACT% && export CUDA_VISIBLE_DEVICES=0 && cd %LLAMA_DIR% && ./build/bin/llama-server -m /home/sands12/.cache/huggingface/hub/models--unsloth--Qwen3.6-35B-A3B-GGUF/snapshots/9280dd353ab587157920d5bd391ada414d84e552/Qwen3.6-35B-A3B-UD-Q3_K_XL.gguf --host 0.0.0.0 --port 9821 --flash-attn on -ngl 999 -c 8192 --reasoning on --reasoning-budget 64" ^
  ; new-tab --title "TTS" cmd /k "%~dp0run_tts_server.bat"

endlocal

