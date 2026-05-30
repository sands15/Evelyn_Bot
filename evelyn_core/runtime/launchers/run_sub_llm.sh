#!/usr/bin/env bash
set -euo pipefail

VENV_ACT="${VENV_ACT:-source ~/venvs/vllm-env/bin/activate}"
LLAMA_DIR="${LLAMA_DIR:-/mnt/c/Users/Admin/llama.cpp}"
SUB_LLM_GPU="${SUB_LLM_GPU:-1}"
SUB_LLM_PORT="${SUB_LLM_PORT:-9821}"
SUB_LLM_CONTEXT="${SUB_LLM_CONTEXT:-8192}"
SUB_LLM_REASONING_BUDGET="${SUB_LLM_REASONING_BUDGET:-96}"
SUB_LLM_MODEL="${SUB_LLM_MODEL:-/mnt/c/Users/Admin/llama.cpp/models/supergemma4-e4b-abliterated-Q5_K_M.gguf}"
SUB_LLM_N_GPU_LAYERS="${SUB_LLM_N_GPU_LAYERS:-999}"
SUB_LLM_THREADS="${SUB_LLM_THREADS:-8}"
SUB_LLM_CACHE_TYPE_K="${SUB_LLM_CACHE_TYPE_K:-q8_0}"
SUB_LLM_CACHE_TYPE_V="${SUB_LLM_CACHE_TYPE_V:-f16}"

eval "$VENV_ACT"
export CUDA_VISIBLE_DEVICES="$SUB_LLM_GPU"

if [[ ! -f "$SUB_LLM_MODEL" ]]; then
  echo "[Sub-LLM] model file not found: $SUB_LLM_MODEL"
  exit 1
fi

cd "$LLAMA_DIR"
exec ./build/bin/llama-server \
  -m "$SUB_LLM_MODEL" \
  --host 0.0.0.0 \
  --port "$SUB_LLM_PORT" \
  --flash-attn on \
  -ngl "$SUB_LLM_N_GPU_LAYERS" \
  -c "$SUB_LLM_CONTEXT" \
  --threads "$SUB_LLM_THREADS" \
  --cache-type-k "$SUB_LLM_CACHE_TYPE_K" \
  --cache-type-v "$SUB_LLM_CACHE_TYPE_V" \
  --reasoning on \
  --reasoning-budget "$SUB_LLM_REASONING_BUDGET"
