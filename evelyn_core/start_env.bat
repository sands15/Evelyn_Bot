@echo off
if defined EVELYN_START_ENV_LOADED goto :eof
set "EVELYN_START_ENV_LOADED=1"

REM ===== Shared launch settings =====
if "%LLAMA_DIR%"=="" set "LLAMA_DIR=/mnt/c/Users/Admin/llama.cpp"
if "%VENV_ACT%"=="" set "VENV_ACT=source ~/venvs/vllm-env/bin/activate"

if "%MAIN_LLM_GPU%"=="" set "MAIN_LLM_GPU=1"
if "%MAIN_LLM_PORT%"=="" set "MAIN_LLM_PORT=9820"
if "%MAIN_LLM_CONTEXT%"=="" set "MAIN_LLM_CONTEXT=4096"
if "%MAIN_LLM_REASONING_BUDGET%"=="" set "MAIN_LLM_REASONING_BUDGET=64"
if "%MAIN_LLM_MODEL_REPO%"=="" set "MAIN_LLM_MODEL_REPO=/home/sands12/.cache/huggingface/hub/models--unsloth--gemma-4-E4B-it-GGUF"
if "%MAIN_LLM_MODEL_FILE%"=="" set "MAIN_LLM_MODEL_FILE=gemma-4-E4B-it-Q5_K_M.gguf"

if "%SUB_LLM_GPU%"=="" set "SUB_LLM_GPU=0"
if "%SUB_LLM_PORT%"=="" set "SUB_LLM_PORT=9821"
if "%SUB_LLM_CONTEXT%"=="" set "SUB_LLM_CONTEXT=8192"
if "%SUB_LLM_REASONING_BUDGET%"=="" set "SUB_LLM_REASONING_BUDGET=64"
if "%SUB_LLM_MODEL_REPO%"=="" set "SUB_LLM_MODEL_REPO=/home/sands12/.cache/huggingface/hub/models--bartowski--EXAONE-3.5-7.8B-Instruct-GGUF"
if "%SUB_LLM_MODEL_FILE%"=="" set "SUB_LLM_MODEL_FILE=EXAONE-3.5-7.8B-Instruct-Q4_K_M.gguf"

if "%OMNIVOICE_VENV%"=="" set "OMNIVOICE_VENV=C:\Users\Admin\omnivoice-server\.venv"
if "%OMNIVOICE_PROFILE_DIR%"=="" set "OMNIVOICE_PROFILE_DIR=%~dp0..\omnivoice_profiles"
if "%TTS_PORT%"=="" set "TTS_PORT=8880"

if "%OPUS_ERROR_TO_SILENCE%"=="" set "OPUS_ERROR_TO_SILENCE=false"
if "%STT_USE_RAW_48K%"=="" set "STT_USE_RAW_48K=false"

if "%START_WAIT_TIMEOUT_SEC%"=="" set "START_WAIT_TIMEOUT_SEC=120"
if "%START_WAIT_INTERVAL_SEC%"=="" set "START_WAIT_INTERVAL_SEC=2"
