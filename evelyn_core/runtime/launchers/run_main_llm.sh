#!/usr/bin/env bash
set -euo pipefail

VENV_ACT="${VENV_ACT:-source ~/venvs/vllm-env/bin/activate}"
MAIN_LLM_BACKEND="${MAIN_LLM_BACKEND:-llama}"
MAIN_LLM_VENV_ACT="${MAIN_LLM_VENV_ACT:-source /home/sands12/venvs/evelyn-gemma4-vllm/bin/activate}"
LLAMA_DIR="${LLAMA_DIR:-/mnt/c/Users/Admin/llama.cpp}"
MAIN_LLM_GPU="${MAIN_LLM_GPU:-GPU-96c554e6-feef-2980-6722-efcb0af098f9}"
MAIN_LLM_PORT="${MAIN_LLM_PORT:-9820}"
MAIN_LLM_CONTEXT="${MAIN_LLM_CONTEXT:-8192}"
MAIN_LLM_N_PARALLEL="${MAIN_LLM_N_PARALLEL:-1}"
MAIN_LLM_CACHE_REUSE="${MAIN_LLM_CACHE_REUSE:-256}"
MAIN_LLM_REASONING="${MAIN_LLM_REASONING:-off}"
MAIN_LLM_REASONING_BUDGET="${MAIN_LLM_REASONING_BUDGET:-0}"
MAIN_LLM_HF="${MAIN_LLM_HF:-off}"
MAIN_LLM_MODEL="${MAIN_LLM_MODEL:-/mnt/c/Users/Admin/llama.cpp/models/kanana-1.5-8b-instruct-2505-q4_k_m.gguf}"
MAIN_LLM_LORA="${MAIN_LLM_LORA:-/mnt/c/Evelyn/training/evelyn_lora_kanana_v1/outputs/kanana_evelyn_core_clean_v46_lora.gguf}"
MAIN_LLM_LORA_SCALE="${MAIN_LLM_LORA_SCALE:-1.0}"
MAIN_LLM_REPEAT_LAST_N="${MAIN_LLM_REPEAT_LAST_N:-256}"
MAIN_LLM_REPEAT_PENALTY="${MAIN_LLM_REPEAT_PENALTY:-1.10}"
MAIN_LLM_PRESENCE_PENALTY="${MAIN_LLM_PRESENCE_PENALTY:-0.00}"
MAIN_LLM_FREQUENCY_PENALTY="${MAIN_LLM_FREQUENCY_PENALTY:-0.20}"
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
if [[ "${MAIN_LLM_HF,,}" == "off" || "${MAIN_LLM_HF,,}" == "none" || "$MAIN_LLM_HF" == "0" ]]; then
  MAIN_LLM_HF=""
fi

if [[ -n "$MAIN_LLM_HF" ]]; then
  model_args=(-hf "$MAIN_LLM_HF")
elif [[ -f "$MAIN_LLM_MODEL" ]]; then
  model_args=(-m "$MAIN_LLM_MODEL")
else
  echo "[Main-LLM] model file not found: $MAIN_LLM_MODEL"
  exit 1
fi

lora_args=()
if [[ "${MAIN_LLM_LORA,,}" == "off" || "${MAIN_LLM_LORA,,}" == "none" || "$MAIN_LLM_LORA" == "0" ]]; then
  MAIN_LLM_LORA=""
fi
if [[ -n "$MAIN_LLM_LORA" ]]; then
  if [[ "$MAIN_LLM_LORA" =~ ^[A-Za-z]:\\ ]]; then
    MAIN_LLM_LORA="$(wslpath -a "$MAIN_LLM_LORA")"
  fi
  if [[ ! -f "$MAIN_LLM_LORA" ]]; then
    echo "[Main-LLM] LoRA adapter file not found: $MAIN_LLM_LORA"
    exit 1
  fi
  lora_args=(--lora-scaled "${MAIN_LLM_LORA}:${MAIN_LLM_LORA_SCALE}")
fi

exec ./build/bin/llama-server \
  "${model_args[@]}" \
  "${lora_args[@]}" \
  --host 0.0.0.0 \
  --port "$MAIN_LLM_PORT" \
  --flash-attn on \
  -ngl 999 \
  -c "$MAIN_LLM_CONTEXT" \
  -np "$MAIN_LLM_N_PARALLEL" \
  --cache-prompt \
  --cache-reuse "$MAIN_LLM_CACHE_REUSE" \
  --metrics \
  --repeat-last-n "$MAIN_LLM_REPEAT_LAST_N" \
  --repeat-penalty "$MAIN_LLM_REPEAT_PENALTY" \
  --presence-penalty "$MAIN_LLM_PRESENCE_PENALTY" \
  --frequency-penalty "$MAIN_LLM_FREQUENCY_PENALTY" \
  --reasoning "$MAIN_LLM_REASONING" \
  --reasoning-budget "$MAIN_LLM_REASONING_BUDGET"
