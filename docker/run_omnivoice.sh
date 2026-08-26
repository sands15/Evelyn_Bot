#!/bin/sh
set -eu

expected_model_revision="c5fdb5ccb189668d56333f77ba2629f4cd7535f4"
expected_runtime_revision="omnivoice-0.1.5"
expected_flashinfer_revision="28bc0889d92110491d726a9c79f26a895db5a074"
if [ "${OMNIVOICE_RUNTIME_REVISION:-}" != "${expected_runtime_revision}" ]; then
    echo "[Evelyn] OmniVoice package revision differs from the verified image recipe." >&2
    exit 78
fi
if [ "${OMNIVOICE_FLASHINFER_REVISION:-}" != "${expected_flashinfer_revision}" ]; then
    echo "[Evelyn] OmniVoice FlashInfer revision differs from the verified image recipe." >&2
    exit 78
fi
if [ "${OMNIVOICE_MODEL_REVISION:-}" != "${expected_model_revision}" ]; then
    echo "[Evelyn] OmniVoice model revision differs from the verified image recipe." >&2
    exit 78
fi
if [ "${FLASHINFER_DISABLE_JIT:-}" != "1" ]; then
    echo "[Evelyn] FlashInfer JIT must stay disabled for the verified image recipe." >&2
    exit 78
fi
if [ "${OMNIVOICE_MAX_CONCURRENT:-}" != "1" ]; then
    echo "[Evelyn] FlashInfer concurrency must be exactly one." >&2
    exit 78
fi
if [ "${OMNIVOICE_NUM_STEP:-}" != "12" ]; then
    echo "[Evelyn] OmniVoice generation steps differ from the verified recipe." >&2
    exit 78
fi
if [ "${OMNIVOICE_FLASHINFER_ENABLED:-}" != "true" ]; then
    echo "[Evelyn] FlashInfer must stay enabled for the verified image recipe." >&2
    exit 78
fi
if [ "${OMNIVOICE_FLASHINFER_CUDA_GRAPH:-}" != "true" ]; then
    echo "[Evelyn] FlashInfer CUDA graphs must stay enabled." >&2
    exit 78
fi
if [ "${OMNIVOICE_FLASHINFER_CUDA_GRAPH_BUCKETS:-}" != "[2,4,8]" ]; then
    echo "[Evelyn] FlashInfer CUDA graph buckets differ from the verified recipe." >&2
    exit 78
fi
if [ "${OMNIVOICE_FLASHINFER_CUDA_GRAPH_OVERHEAD_BUDGET:-}" != "512" ]; then
    echo "[Evelyn] FlashInfer CUDA graph overhead budget differs from the verified recipe." >&2
    exit 78
fi

model_root="${HF_HUB_CACHE}/models--k2-fsa--OmniVoice"
revision_file="${model_root}/refs/main"
snapshot_dir="${model_root}/snapshots/${expected_model_revision}"

if [ ! -f "${revision_file}" ] || [ "$(cat "${revision_file}")" != "${expected_model_revision}" ]; then
    echo "[Evelyn] OmniVoice model cache revision mismatch." >&2
    exit 78
fi
if [ ! -d "${snapshot_dir}" ]; then
    echo "[Evelyn] OmniVoice model snapshot is missing." >&2
    exit 78
fi
if ! (
    cd "${HF_HUB_CACHE}"
    sha256sum --check --strict /opt/omnivoice-server/omnivoice_model.sha256
); then
    echo "[Evelyn] OmniVoice model snapshot integrity check failed." >&2
    exit 78
fi

exec python -m omnivoice_server.cli \
    --host 0.0.0.0 \
    --port 8880 \
    --profile-dir /home/ubuntu/app/profiles \
    --device cuda
