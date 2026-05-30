#!/usr/bin/env bash
set -euo pipefail

VENV_ACT="${VENV_ACT:-source ~/venvs/vllm-env/bin/activate}"
LLAMA_DIR="${LLAMA_DIR:-/mnt/c/Users/Admin/llama.cpp}"
ROUTER_LLM_GPU="${ROUTER_LLM_GPU:-GPU-a352a7f9-1fcf-3d18-9973-6f9114addf7b}"
ROUTER_LLM_PORT="${ROUTER_LLM_PORT:-9822}"
ROUTER_LLM_CONTEXT="${ROUTER_LLM_CONTEXT:-1536}"
ROUTER_LLM_REASONING="${ROUTER_LLM_REASONING:-off}"
ROUTER_LLM_REASONING_BUDGET="${ROUTER_LLM_REASONING_BUDGET:-12}"
ROUTER_LLM_MODEL="${ROUTER_LLM_MODEL:-/home/sands12/.cache/huggingface/hub/models--unsloth--gemma-4-E2B-it-GGUF/snapshots/f064409f340b34190993560b2168133e5dbae558/gemma-4-E2B-it-UD-Q6_K_XL.gguf}"

eval "$VENV_ACT"
export CUDA_VISIBLE_DEVICES="$ROUTER_LLM_GPU"

if [[ ! -f "$ROUTER_LLM_MODEL" ]]; then
  echo "[Router-LLM] model file not found: $ROUTER_LLM_MODEL"
  exit 1
fi

cd "$LLAMA_DIR"
exec ./build/bin/llama-server \
  -m "$ROUTER_LLM_MODEL" \
  --host 0.0.0.0 \
  --port "$ROUTER_LLM_PORT" \
  --flash-attn on \
  -ngl 999 \
  -c "$ROUTER_LLM_CONTEXT" \
  --reasoning "$ROUTER_LLM_REASONING" \
  --reasoning-budget "$ROUTER_LLM_REASONING_BUDGET"
