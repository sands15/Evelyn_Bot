#!/usr/bin/env bash
set -euo pipefail

VENV_ACT="${VENV_ACT:-source ~/venvs/vllm-env/bin/activate}"
LLAMA_DIR="${LLAMA_DIR:-/mnt/c/Users/Admin/llama.cpp}"
MAIN_LLM_GPU="${MAIN_LLM_GPU:-1}"
MAIN_LLM_PORT="${MAIN_LLM_PORT:-9820}"
MAIN_LLM_CONTEXT="${MAIN_LLM_CONTEXT:-4096}"
MAIN_LLM_N_PARALLEL="${MAIN_LLM_N_PARALLEL:-1}"
MAIN_LLM_REASONING="${MAIN_LLM_REASONING:-off}"
MAIN_LLM_REASONING_BUDGET="${MAIN_LLM_REASONING_BUDGET:-0}"
MAIN_LLM_MODEL="${MAIN_LLM_MODEL:-/home/sands12/.cache/huggingface/hub/models--unsloth--gemma-4-E4B-it-GGUF/snapshots/ce152932ac27bc40bc9c727386760424d50bb456/gemma-4-E4B-it-Q5_K_M.gguf}"

eval "$VENV_ACT"
export CUDA_VISIBLE_DEVICES="$MAIN_LLM_GPU"

if [[ ! -f "$MAIN_LLM_MODEL" ]]; then
  echo "[Main-LLM] model file not found: $MAIN_LLM_MODEL"
  exit 1
fi

cd "$LLAMA_DIR"
exec ./build/bin/llama-server \
  -m "$MAIN_LLM_MODEL" \
  --host 0.0.0.0 \
  --port "$MAIN_LLM_PORT" \
  --flash-attn on \
  -ngl 999 \
  -c "$MAIN_LLM_CONTEXT" \
  -np "$MAIN_LLM_N_PARALLEL" \
  --reasoning "$MAIN_LLM_REASONING" \
  --reasoning-budget "$MAIN_LLM_REASONING_BUDGET"
