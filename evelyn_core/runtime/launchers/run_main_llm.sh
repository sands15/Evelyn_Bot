#!/usr/bin/env bash
set -euo pipefail

MAIN_LLM_BACKEND="${MAIN_LLM_BACKEND:-llama}"
MAIN_LLM_VENV_ACT="${MAIN_LLM_VENV_ACT:-source /home/sands12/venvs/evelyn-gemma4-vllm/bin/activate}"
LLAMA_DIR="${LLAMA_DIR:-/mnt/c/Users/Admin/llama.cpp}"
MAIN_LLM_BUILD_DIR="${MAIN_LLM_BUILD_DIR:-$LLAMA_DIR/build-sm120-v1}"
MAIN_LLM_GPU="${MAIN_LLM_GPU:-GPU-0cb1f962-418e-41f0-cdfc-4bfca6b9486f}"
MAIN_LLM_PORT="${MAIN_LLM_PORT:-9820}"
MAIN_LLM_CONTEXT="${MAIN_LLM_CONTEXT:-8192}"
MAIN_LLM_N_PARALLEL="${MAIN_LLM_N_PARALLEL:-1}"
MAIN_LLM_BATCH_SIZE="${MAIN_LLM_BATCH_SIZE:-2048}"
MAIN_LLM_UBATCH_SIZE="${MAIN_LLM_UBATCH_SIZE:-2048}"
MAIN_LLM_CACHE_RAM_MIB="${MAIN_LLM_CACHE_RAM_MIB:-8192}"
MAIN_LLM_CACHE_REUSE="${MAIN_LLM_CACHE_REUSE:-256}"
MAIN_LLM_CUDA_GRAPHS_ENABLED="${MAIN_LLM_CUDA_GRAPHS_ENABLED:-1}"
MAIN_LLM_CUDA_GRAPH_OPT="${MAIN_LLM_CUDA_GRAPH_OPT:-1}"
MAIN_LLM_SWA_FULL_ENABLED="${MAIN_LLM_SWA_FULL_ENABLED:-1}"
MAIN_LLM_REASONING="${MAIN_LLM_REASONING:-off}"
MAIN_LLM_REASONING_BUDGET="${MAIN_LLM_REASONING_BUDGET:-0}"
MAIN_LLM_HF="${MAIN_LLM_HF:-off}"
MAIN_LLM_MODEL="${MAIN_LLM_MODEL:-/mnt/c/Users/Admin/llama.cpp/models/gemma4-12b-batiai-iq4xs/google-gemma-4-12B-it-IQ4_XS.gguf}"
MAIN_LLM_LORA="${MAIN_LLM_LORA:-off}"
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
MAIN_LLM_JINJA="${MAIN_LLM_JINJA:-true}"
MAIN_LLM_MMPROJ="${MAIN_LLM_MMPROJ:-off}"
MAIN_LLM_CHAT_TEMPLATE_KWARGS="${MAIN_LLM_CHAT_TEMPLATE_KWARGS:-{\"enable_thinking\":false}}"
MAIN_LLM_REASONING_FORMAT="${MAIN_LLM_REASONING_FORMAT:-none}"

export CUDA_DEVICE_ORDER="${CUDA_DEVICE_ORDER:-PCI_BUS_ID}"
export CUDA_VISIBLE_DEVICES="$MAIN_LLM_GPU"
export LLAMA_CHAT_TEMPLATE_KWARGS="$MAIN_LLM_CHAT_TEMPLATE_KWARGS"
export GGML_CUDA_GRAPH_OPT="$MAIN_LLM_CUDA_GRAPH_OPT"
case "$MAIN_LLM_CUDA_GRAPHS_ENABLED" in
  1) unset GGML_CUDA_DISABLE_GRAPHS ;;
  0) export GGML_CUDA_DISABLE_GRAPHS=1 ;;
  *) echo "[Main-LLM] MAIN_LLM_CUDA_GRAPHS_ENABLED must be 0 or 1" >&2; exit 64 ;;
esac
swa_full_args=()
case "$MAIN_LLM_SWA_FULL_ENABLED" in
  1) swa_full_args=(--swa-full) ;;
  0) ;;
  *) echo "[Main-LLM] MAIN_LLM_SWA_FULL_ENABLED must be 0 or 1" >&2; exit 64 ;;
esac

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

cd "$LLAMA_DIR"
main_llm_server="$MAIN_LLM_BUILD_DIR/bin/llama-server"
if [[ ! -x "$main_llm_server" ]]; then
  echo "[Main-LLM] native build is missing: $MAIN_LLM_BUILD_DIR" >&2
  exit 78
fi
if ! grep -Eq '^CMAKE_CUDA_ARCHITECTURES:[^=]+=120a-real$' "$MAIN_LLM_BUILD_DIR/CMakeCache.txt"; then
  echo "[Main-LLM] MAIN_LLM_BUILD_DIR must select the native 120a build" >&2
  exit 78
fi

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

template_args=()
if [[ "${MAIN_LLM_JINJA,,}" == "true" || "$MAIN_LLM_JINJA" == "1" || "${MAIN_LLM_JINJA,,}" == "yes" ]]; then
  template_args+=(--jinja)
fi
if [[ "${MAIN_LLM_MMPROJ,,}" == "off" || "${MAIN_LLM_MMPROJ,,}" == "none" || "$MAIN_LLM_MMPROJ" == "0" ]]; then
  template_args+=(--no-mmproj)
fi

export LD_LIBRARY_PATH="$MAIN_LLM_BUILD_DIR/bin:${LD_LIBRARY_PATH:-}"
exec "$main_llm_server" \
  "${model_args[@]}" \
  "${lora_args[@]}" \
  "${swa_full_args[@]}" \
  "${template_args[@]}" \
  --host 0.0.0.0 \
  --port "$MAIN_LLM_PORT" \
  --flash-attn on \
  -ngl 999 \
  -c "$MAIN_LLM_CONTEXT" \
  -np "$MAIN_LLM_N_PARALLEL" \
  --batch-size "$MAIN_LLM_BATCH_SIZE" \
  --ubatch-size "$MAIN_LLM_UBATCH_SIZE" \
  --cache-ram "$MAIN_LLM_CACHE_RAM_MIB" \
  --cache-prompt \
  --cache-reuse "$MAIN_LLM_CACHE_REUSE" \
  --metrics \
  --repeat-last-n "$MAIN_LLM_REPEAT_LAST_N" \
  --repeat-penalty "$MAIN_LLM_REPEAT_PENALTY" \
  --presence-penalty "$MAIN_LLM_PRESENCE_PENALTY" \
  --frequency-penalty "$MAIN_LLM_FREQUENCY_PENALTY" \
  --reasoning "$MAIN_LLM_REASONING" \
  --reasoning-budget "$MAIN_LLM_REASONING_BUDGET" \
  --reasoning-format "$MAIN_LLM_REASONING_FORMAT"
