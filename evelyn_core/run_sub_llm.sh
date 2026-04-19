#!/usr/bin/env bash
set -euo pipefail

VENV_ACT="${VENV_ACT:-source ~/venvs/vllm-env/bin/activate}"
LLAMA_DIR="${LLAMA_DIR:-/mnt/c/Users/Admin/llama.cpp}"
SUB_LLM_GPU="${SUB_LLM_GPU:-0}"
SUB_LLM_PORT="${SUB_LLM_PORT:-9821}"
SUB_LLM_CONTEXT="${SUB_LLM_CONTEXT:-8192}"
SUB_LLM_REASONING_BUDGET="${SUB_LLM_REASONING_BUDGET:-96}"
SUB_LLM_MODEL="${SUB_LLM_MODEL:-/home/sands12/.cache/huggingface/hub/models--LGAI-EXAONE--EXAONE-3.5-7.8B-Instruct-GGUF/snapshots/c618bf67338171760c72c3f109f2900cb7d79855/EXAONE-3.5-7.8B-Instruct-BF16.gguf}"

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
  -ngl 999 \
  -c "$SUB_LLM_CONTEXT" \
  --reasoning on \
  --reasoning-budget "$SUB_LLM_REASONING_BUDGET"
