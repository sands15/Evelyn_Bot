#!/bin/sh
set -eu

expected_model_revision="c5fdb5ccb189668d56333f77ba2629f4cd7535f4"
if [ "${OMNIVOICE_MODEL_REVISION:-}" != "${expected_model_revision}" ]; then
    echo "[Evelyn] OmniVoice runtime revision differs from the verified image recipe." >&2
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
