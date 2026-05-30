#!/usr/bin/env bash
set -euo pipefail

VENV_ACT="${VENV_ACT:-source ~/venvs/vllm-env/bin/activate}"
MAIN_LLM_BACKEND="${MAIN_LLM_BACKEND:-llama}"
MAIN_LLM_VENV_ACT="${MAIN_LLM_VENV_ACT:-source /home/sands12/venvs/evelyn-gemma4-vllm/bin/activate}"
LLAMA_DIR="${LLAMA_DIR:-/mnt/c/Users/Admin/llama.cpp}"
MAIN_LLM_GPU="${MAIN_LLM_GPU:-1}"
MAIN_LLM_PORT="${MAIN_LLM_PORT:-9820}"
MAIN_LLM_CONTEXT="${MAIN_LLM_CONTEXT:-8192}"
MAIN_LLM_N_PARALLEL="${MAIN_LLM_N_PARALLEL:-1}"
MAIN_LLM_CACHE_REUSE="${MAIN_LLM_CACHE_REUSE:-256}"
MAIN_LLM_REASONING="${MAIN_LLM_REASONING:-off}"
MAIN_LLM_REASONING_BUDGET="${MAIN_LLM_REASONING_BUDGET:-0}"
MAIN_LLM_HF="${MAIN_LLM_HF:-LGAI-EXAONE/EXAONE-3.5-7.8B-Instruct-GGUF:Q4_K_M}"
MAIN_LLM_MODEL="${MAIN_LLM_MODEL:-LGAI-EXAONE/EXAONE-3.5-7.8B-Instruct-GGUF:Q4_K_M}"
MAIN_LLM_QUANTIZATION="${MAIN_LLM_QUANTIZATION:-gptq}"
MAIN_LLM_DTYPE="${MAIN_LLM_DTYPE:-bfloat16}"
MAIN_LLM_GPU_MEMORY_UTILIZATION="${MAIN_LLM_GPU_MEMORY_UTILIZATION:-0.60}"
MAIN_LLM_CHAT_TEMPLATE_CONTENT_FORMAT="${MAIN_LLM_CHAT_TEMPLATE_CONTENT_FORMAT:-openai}"
MAIN_LLM_FLASHINFER_SAMPLER="${MAIN_LLM_FLASHINFER_SAMPLER:-0}"
MAIN_LLM_ENFORCE_EAGER="${MAIN_LLM_ENFORCE_EAGER:-true}"

export CUDA_DEVICE_ORDER="${CUDA_DEVICE_ORDER:-PCI_BUS_ID}"
export CUDA_VISIBLE_DEVICES="$MAIN_LLM_GPU"

if [[ "$MAIN_LLM_BACKEND" == "vllm" ]]; then
  eval "$MAIN_LLM_VENV_ACT"
  export VLLM_USE_FLASHINFER_SAMPLER="$MAIN_LLM_FLASHINFER_SAMPLER"

  cmd=(
    vllm serve "$MAIN_LLM_MODEL"
    --quantization "$MAIN_LLM_QUANTIZATION"
    --dtype "$MAIN_LLM_DTYPE"
    --max-model-len "$MAIN_LLM_CONTEXT"
    --gpu-memory-utilization "$MAIN_LLM_GPU_MEMORY_UTILIZATION"
    --chat-template-content-format "$MAIN_LLM_CHAT_TEMPLATE_CONTENT_FORMAT"
    --host 0.0.0.0
    --port "$MAIN_LLM_PORT"
  )
  if [[ "${MAIN_LLM_ENFORCE_EAGER,,}" == "true" ]]; then
    cmd+=(--enforce-eager)
  fi
  exec "${cmd[@]}"
fi

eval "$VENV_ACT"
cd "$LLAMA_DIR"

model_args=()
if [[ -n "$MAIN_LLM_HF" ]]; then
  model_args=(-hf "$MAIN_LLM_HF")
elif [[ -f "$MAIN_LLM_MODEL" ]]; then
  model_args=(-m "$MAIN_LLM_MODEL")
else
  echo "[Main-LLM] model file not found: $MAIN_LLM_MODEL"
  exit 1
fi

exec ./build/bin/llama-server \
  "${model_args[@]}" \
  --host 0.0.0.0 \
  --port "$MAIN_LLM_PORT" \
  --flash-attn on \
  -ngl 999 \
  -c "$MAIN_LLM_CONTEXT" \
  -np "$MAIN_LLM_N_PARALLEL" \
  --cache-prompt \
  --cache-reuse "$MAIN_LLM_CACHE_REUSE" \
  --metrics \
  --reasoning "$MAIN_LLM_REASONING" \
  --reasoning-budget "$MAIN_LLM_REASONING_BUDGET"
