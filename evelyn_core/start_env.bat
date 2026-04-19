@echo off
if defined EVELYN_START_ENV_LOADED goto :eof
set "EVELYN_START_ENV_LOADED=1"

REM ===== Shared launch settings =====
if "%LLAMA_DIR%"=="" set "LLAMA_DIR=/mnt/c/Users/Admin/llama.cpp"
if "%VENV_ACT%"=="" set "VENV_ACT=source ~/venvs/vllm-env/bin/activate"

if "%MAIN_LLM_GPU%"=="" set "MAIN_LLM_GPU=1"
if "%MAIN_LLM_PORT%"=="" set "MAIN_LLM_PORT=9820"
if "%MAIN_LLM_CONTEXT%"=="" set "MAIN_LLM_CONTEXT=4096"
if "%MAIN_LLM_N_PARALLEL%"=="" set "MAIN_LLM_N_PARALLEL=1"
if "%MAIN_LLM_REASONING%"=="" set "MAIN_LLM_REASONING=off"
if "%MAIN_LLM_REASONING_BUDGET%"=="" set "MAIN_LLM_REASONING_BUDGET=0"
if "%MAIN_LLM_MODEL%"=="" set "MAIN_LLM_MODEL=/home/sands12/.cache/huggingface/hub/models--unsloth--gemma-4-E4B-it-GGUF/snapshots/ce152932ac27bc40bc9c727386760424d50bb456/gemma-4-E4B-it-Q5_K_M.gguf"

if "%SUB_LLM_GPU%"=="" set "SUB_LLM_GPU=0"
if "%SUB_LLM_PORT%"=="" set "SUB_LLM_PORT=9821"
if "%SUB_LLM_CONTEXT%"=="" set "SUB_LLM_CONTEXT=8192"
if "%SUB_LLM_REASONING_BUDGET%"=="" set "SUB_LLM_REASONING_BUDGET=96"
if "%SUB_LLM_MODEL%"=="" set "SUB_LLM_MODEL=/home/sands12/.cache/huggingface/hub/models--LGAI-EXAONE--EXAONE-3.5-7.8B-Instruct-GGUF/snapshots/c618bf67338171760c72c3f109f2900cb7d79855/EXAONE-3.5-7.8B-Instruct-BF16.gguf"

if "%ROUTER_LLM_GPU%"=="" set "ROUTER_LLM_GPU=1"
if "%ROUTER_LLM_PORT%"=="" set "ROUTER_LLM_PORT=9822"
if "%ROUTER_LLM_CONTEXT%"=="" set "ROUTER_LLM_CONTEXT=4096"
if "%ROUTER_LLM_REASONING_BUDGET%"=="" set "ROUTER_LLM_REASONING_BUDGET=96"
if "%ROUTER_LLM_MODEL%"=="" set "ROUTER_LLM_MODEL=/home/sands12/.cache/huggingface/hub/models--unsloth--gemma-4-E2B-it-GGUF/snapshots/f064409f340b34190993560b2168133e5dbae558/gemma-4-E2B-it-UD-Q6_K_XL.gguf"

if "%OMNIVOICE_VENV%"=="" set "OMNIVOICE_VENV=C:\Users\Admin\omnivoice-server\.venv"
if "%OMNIVOICE_PROFILE_DIR%"=="" set "OMNIVOICE_PROFILE_DIR=%~dp0..\omnivoice_profiles"
if "%TTS_PORT%"=="" set "TTS_PORT=8880"
if "%TTS_GPU%"=="" set "TTS_GPU=0"
if "%TTS_DEVICE%"=="" set "TTS_DEVICE=cuda"

if "%OPUS_ERROR_TO_SILENCE%"=="" set "OPUS_ERROR_TO_SILENCE=true"
if "%STT_USE_RAW_48K%"=="" set "STT_USE_RAW_48K=false"

if "%START_WAIT_TIMEOUT_SEC%"=="" set "START_WAIT_TIMEOUT_SEC=120"
if "%START_WAIT_INTERVAL_SEC%"=="" set "START_WAIT_INTERVAL_SEC=2"
if "%SUPERVISOR_RESTART_DELAY_SEC%"=="" set "SUPERVISOR_RESTART_DELAY_SEC=3"
