#!/usr/bin/env bash
set -euo pipefail

VENV_ACT="${VENV_ACT:-source ~/venvs/vllm-env/bin/activate}"
LLAMA_DIR="${LLAMA_DIR:-/mnt/c/Users/Admin/llama.cpp}"
ROUTER_LLM_GPU="${ROUTER_LLM_GPU:-GPU-a352a7f9-1fcf-3d18-9973-6f9114addf7b}"
ROUTER_LLM_PORT="${ROUTER_LLM_PORT:-9822}"
ROUTER_LLM_CONTEXT="${ROUTER_LLM_CONTEXT:-1536}"
ROUTER_LLM_REASONING="${ROUTER_LLM_REASONING:-off}"
ROUTER_LLM_REASONING_BUDGET="${ROUTER_LLM_REASONING_BUDGET:-12}"
ROUTER_LLM_HF="${ROUTER_LLM_HF:-LGAI-EXAONE/EXAONE-3.5-2.4B-Instruct-GGUF:EXAONE-3.5-2.4B-Instruct-Q4_K_M.gguf}"
ROUTER_LLM_MODEL="${ROUTER_LLM_MODEL:-}"

eval "$VENV_ACT"
export CUDA_VISIBLE_DEVICES="$ROUTER_LLM_GPU"

model_args=()
if [[ -n "$ROUTER_LLM_HF" ]]; then
  model_args=(-hf "$ROUTER_LLM_HF")
elif [[ -f "$ROUTER_LLM_MODEL" ]]; then
  model_args=(-m "$ROUTER_LLM_MODEL")
else
  echo "[Router-LLM] model file not found: $ROUTER_LLM_MODEL"
  exit 1
fi

cd "$LLAMA_DIR"
exec ./build/bin/llama-server \
  "${model_args[@]}" \
  --host 0.0.0.0 \
  --port "$ROUTER_LLM_PORT" \
  --flash-attn on \
  -ngl 999 \
  -c "$ROUTER_LLM_CONTEXT" \
  --reasoning "$ROUTER_LLM_REASONING" \
  --reasoning-budget "$ROUTER_LLM_REASONING_BUDGET"
