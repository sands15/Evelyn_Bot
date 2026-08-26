from __future__ import annotations

import argparse
import base64
import hashlib
import json
import math
import os
import re
import socket
import stat
import subprocess
import sys
import threading
import time
import wave
from array import array
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from urllib import error, request


REPO_ROOT = next(
    path for path in Path(__file__).resolve().parents if (path / "main.py").exists()
)
TOOLS_ROOT = Path(__file__).resolve().parent
RUNTIME_ROOT = REPO_ROOT / "evelyn_core" / "runtime"
if str(TOOLS_ROOT) not in sys.path:
    sys.path.insert(0, str(TOOLS_ROOT))
if str(RUNTIME_ROOT) not in sys.path:
    sys.path.insert(0, str(RUNTIME_ROOT))

from post_stt_latency_benchmark import (  # noqa: E402
    LLAMA_TIMING_METRIC_NAMES,
    extract_llama_timing_metrics,
)
from evelyn_core.assistant_prompt_contract import (  # noqa: E402
    FAST_MAIN_LLM_USER_PREFIX,
    build_evelyn_system_prompt,
)
from evelyn_core.runtime_artifact_io import (  # noqa: E402
    atomic_json_write,
)

_main_timing_metrics = extract_llama_timing_metrics


DEFAULT_AUDIO = REPO_ROOT / "tools" / "probes" / "sample_input.wav"
DEFAULT_OUTPUT = (
    REPO_ROOT
    / "runtime_artifacts"
    / "benchmarks"
    / "gpu1_concurrency_latest.json"
)
MAIN_PROMPT_CHARS = 938
GPU1_BENCHMARK_AUDIO_SHA256 = (
    "6aa48d50a8a5efed11fcb5b30896c52565c70abed14ed067bf47ca09a3e98d3f"
)
GPU1_CONCURRENCY_REPORT_SCHEMA = "evelyn.gpu1-latency-budget.v2"
P0_4_MAIN_URL = "http://127.0.0.1:9820/v1/chat/completions"
P0_4_MAIN_MODEL = "gemma-4-12B-it-IQ4_XS-text-only"
P0_4_QWEN_URL = "http://127.0.0.1:9823/v1/chat/completions"
P0_4_QWEN_MODEL = "Qwen3-14B-Q4_K_M.gguf"
P0_4_STT_URL = "http://127.0.0.1:8892/v1/stt/transcribe"
P0_4_STT_MODEL = "Qwen/Qwen3-ASR-1.7B"
P0_4_STT_BACKEND = "vllm"
P0_4_GPU_SAMPLE_INTERVAL_MS = 50.0
P0_4_MAIN_TIMEOUT_SEC = 15.0
P0_4_STT_TIMEOUT_SEC = 15.0
P0_4_MAIN_TTFT_BUDGET_MS = 1_000.0
P0_4_QWEN_TIMEOUT_MS = 6_000.0
P0_4_STT_FINAL_BUDGET_MS = 1_200.0
P0_4_GPU_MIN_FREE_MB = 2_048.0
P0_4_STT_MEMORY_UTILIZATION = 0.35
P0_4_MAIN_MODEL_PATH = (
    "/llama/models/gemma4-12b-batiai-iq4xs/"
    "google-gemma-4-12B-it-IQ4_XS.gguf"
)
P0_4_MAIN_MODEL_SHA256 = (
    "3d927b9611062f5bd374ac7db0cbd7cfe4a840be8ab659cba966acce4e5dfe08"
)
P0_4_QWEN_MODEL_PATH = "/llama/models/qwen3-14b/Qwen3-14B-Q4_K_M.gguf"
P0_4_QWEN_MODEL_SHA256 = (
    "500a8806e85ee9c83f3ae08420295592451379b4f8cf2d0f41c15dffeb6b81f0"
)
P0_4_STT_CACHE_ROOT = "/root/.cache/huggingface/models--Qwen--Qwen3-ASR-1.7B"
P0_4_STT_SOURCE_ROOT = REPO_ROOT / "evelyn_core" / "runtime" / "evelyn_core"
P0_4_STT_DOCKERFILE = REPO_ROOT / "docker" / "Dockerfile.stt"
P0_4_STT_REQUIREMENTS = REPO_ROOT / "docker" / "requirements.stt.txt"
P0_4_STT_BASE_DIGEST = (
    "sha256:05de765c12d993316f770e8e4396b9516afe38b7c52189bce2d5b64ef812db58"
)
_ATTEMPT_ID = re.compile(r"[A-Za-z0-9][A-Za-z0-9_.-]{0,79}\Z", re.ASCII)
_COMMIT = re.compile(r"[0-9a-f]{40}\Z", re.ASCII)
_IMAGE_ID = re.compile(r"sha256:[0-9a-f]{64}\Z", re.ASCII)
_GPU_UUID = re.compile(
    r"GPU-[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}\Z",
    re.ASCII,
)
_SHA256 = re.compile(r"[0-9a-f]{64}\Z", re.ASCII)
_CONTAINER_ID = re.compile(r"[0-9a-f]{64}\Z", re.ASCII)
_COMPOSE_PROJECT = re.compile(r"[a-z0-9][a-z0-9_-]{0,62}\Z", re.ASCII)
_MAX_REPORT_BYTES = 4 * 1024 * 1024
_P0_4_CONTAINERS = {
    "main": ("evelyn-p04-main-llm", "main_llm", "0"),
    "qwen": ("evelyn-p04-qwen-llm", "minecraft_llm", "1"),
    "stt": ("evelyn-p04-stt", "stt", "1"),
}
_MAIN_SAMPLE_KEYS = frozenset({"ok", "ttftMs", "totalMs", *LLAMA_TIMING_METRIC_NAMES})


class _ValidationFailure(RuntimeError):
    def __init__(self, code: str) -> None:
        self.code = code
        super().__init__(code)


def _finite_number(value: Any) -> bool:
    return (
        isinstance(value, (int, float))
        and not isinstance(value, bool)
        and math.isfinite(float(value))
    )


def _run_text(command: list[str], *, timeout_sec: float = 15.0) -> str:
    completed = subprocess.run(
        command,
        check=True,
        capture_output=True,
        text=True,
        timeout=timeout_sec,
    )
    if len(completed.stdout.encode("utf-8")) > 1024 * 1024:
        raise ValueError("command_output_too_large")
    return completed.stdout


def _required(condition: bool) -> None:
    if not condition:
        raise ValueError("validation_mismatch")


def _command_line(command: list[str], *, timeout_sec: float = 15.0) -> str:
    lines = [
        line.strip()
        for line in _run_text(command, timeout_sec=timeout_sec).splitlines()
        if line.strip()
    ]
    _required(len(lines) == 1)
    return lines[0]


def _environment_map(values: Any) -> dict[str, str]:
    if not isinstance(values, list):
        raise ValueError("container_environment_invalid")
    result: dict[str, str] = {}
    for item in values:
        if not isinstance(item, str) or "=" not in item:
            raise ValueError("container_environment_invalid")
        name, value = item.split("=", 1)
        if not name or name in result:
            raise ValueError("container_environment_invalid")
        result[name] = value
    return result


def _canonical_host_path(path: str | Path) -> str:
    raw = os.fspath(path).replace("\\", "/")
    docker_desktop = re.fullmatch(
        r"/(?:run/desktop/mnt/host|host_mnt|mnt/host)/([A-Za-z])(?:/(.*))?",
        raw,
    )
    if docker_desktop:
        raw = f"{docker_desktop.group(1)}:/{docker_desktop.group(2) or ''}"
    return os.path.normcase(os.path.abspath(raw)).replace("\\", "/")


def _expected_hf_hub_source() -> Path:
    configured = os.environ.get("EVELYN_HUGGINGFACE_CACHE_DIR")
    if configured:
        return Path(configured) / "hub"
    profile = os.environ.get("USERPROFILE")
    _required(bool(profile))
    return Path(str(profile)) / ".cache" / "huggingface" / "hub"


def _expected_llama_sources() -> tuple[Path, Path]:
    profile = os.environ.get("USERPROFILE")
    llama = os.environ.get("EVELYN_LLAMA_CPP_DIR")
    if not llama:
        _required(bool(profile))
        llama = str(Path(str(profile)) / "llama.cpp")
    build = os.environ.get("EVELYN_MAIN_LLM_BUILD_DIR") or str(
        Path(llama) / "build-sm120-v1"
    )
    return Path(llama), Path(build)


def _tree_sha256(root: Path, *, domain: str, exclude_bytecode: bool) -> str:
    _required(root.is_dir())
    digest = hashlib.sha256()
    digest.update(domain.encode("ascii") + b"\0")
    count = 0
    for path in sorted(root.rglob("*"), key=lambda item: item.relative_to(root).as_posix()):
        relative = path.relative_to(root)
        if (
            not path.is_file()
            or (exclude_bytecode and "__pycache__" in relative.parts)
            or (exclude_bytecode and path.suffix in {".pyc", ".pyo"})
        ):
            continue
        _required(path.resolve(strict=True).is_relative_to(root.resolve(strict=True)))
        raw = path.read_bytes()
        digest.update(relative.as_posix().encode("utf-8") + b"\0")
        digest.update(str(len(raw)).encode("ascii") + b"\0")
        digest.update(raw + b"\0")
        count += 1
    _required(count > 0)
    return digest.hexdigest()


_CONTAINER_TREE_SHA256_SCRIPT = r"""
from pathlib import Path
import hashlib
import sys

root = Path(sys.argv[1])
domain = sys.argv[2]
exclude_bytecode = sys.argv[3] == "1"
allowed_root = Path(sys.argv[4]).resolve(strict=True)
digest = hashlib.sha256()
digest.update(domain.encode("ascii") + b"\0")
count = 0
for path in sorted(root.rglob("*"), key=lambda item: item.relative_to(root).as_posix()):
    relative = path.relative_to(root)
    if not path.is_file():
        continue
    if exclude_bytecode and ("__pycache__" in relative.parts or path.suffix in {".pyc", ".pyo"}):
        continue
    if not path.resolve(strict=True).is_relative_to(allowed_root):
        raise SystemExit(66)
    raw = path.read_bytes()
    digest.update(relative.as_posix().encode("utf-8") + b"\0")
    digest.update(str(len(raw)).encode("ascii") + b"\0")
    digest.update(raw + b"\0")
    count += 1
if count < 1:
    raise SystemExit(65)
print(digest.hexdigest())
""".strip()


def _container_tree_sha256(
    container_id: str,
    root: str,
    *,
    domain: str,
    exclude_bytecode: bool = False,
    allowed_root: str | None = None,
) -> str:
    value = _command_line(
        [
            "docker",
            "exec",
            container_id,
            "python",
            "-c",
            _CONTAINER_TREE_SHA256_SCRIPT,
            root,
            domain,
            "1" if exclude_bytecode else "0",
            allowed_root or root,
        ],
        timeout_sec=900.0,
    )
    _required(_SHA256.fullmatch(value) is not None)
    return value


def _container_file_sha256(container_id: str, path: str) -> str:
    fields = _command_line(
        ["docker", "exec", container_id, "sha256sum", path],
        timeout_sec=900.0,
    ).split(maxsplit=1)
    _required(len(fields) == 2 and _SHA256.fullmatch(fields[0]) is not None)
    return fields[0]


_LLAMA_RUNTIME_SHA256_SCRIPT = r"""
set -euo pipefail
root="$1"
server="$root/llama-server"
test -x "$server"
{
  printf 'evelyn.llama-server-runtime.v1\n'
  while IFS= read -r runtime_path; do
    test -f "$runtime_path"
    printf 'local\t%s\t' "$runtime_path"
    sha256sum "$runtime_path" | awk '{print $1}'
  done < <(find "$root" -maxdepth 1 \( -type f -o -type l \) \( -name llama-server -o -name '*.so*' \) -print | LC_ALL=C sort)
  while IFS= read -r runtime_path; do
    test -f "$runtime_path"
    printf 'linked\t%s\t' "$runtime_path"
    sha256sum "$runtime_path" | awk '{print $1}'
  done < <(ldd "$server" | awk '$2 == "=>" && $3 ~ /^\// {print $3} $1 ~ /^\// {print $1}' | LC_ALL=C sort -u)
} | sha256sum | awk '{print $1}'
""".strip()


def _container_llama_runtime_sha256(container_id: str) -> str:
    value = _command_line(
        [
            "docker",
            "exec",
            container_id,
            "bash",
            "-lc",
            _LLAMA_RUNTIME_SHA256_SCRIPT,
            "benchmark",
            "/llama/build/bin",
        ],
        timeout_sec=300.0,
    )
    _required(_SHA256.fullmatch(value) is not None)
    return value


def _get_json(url: str) -> dict[str, Any]:
    with request.urlopen(url, timeout=5.0) as response:
        payload = json.loads(response.read().decode("utf-8"))
    _required(isinstance(payload, dict))
    return payload


def _gpu_uuid(*, index: int | None = None, container_id: str | None = None) -> str:
    prefix = (
        ["nvidia-smi", "--id", str(index)]
        if container_id is None
        else ["docker", "exec", container_id, "nvidia-smi"]
    )
    value = _command_line(
        [*prefix, "--query-gpu=uuid", "--format=csv,noheader,nounits"]
    )
    _required(_GPU_UUID.fullmatch(value) is not None)
    return value


def _observe_p0_4_environment(args: argparse.Namespace) -> dict[str, Any]:
    try:
        head = _command_line(
            ["git", "-C", str(REPO_ROOT), "rev-parse", "HEAD"]
        )
        clean = not _run_text(
            ["git", "-C", str(REPO_ROOT), "status", "--porcelain=v1", "--untracked-files=all"]
        ).strip()
        _required(head == args.source_revision and _COMMIT.fullmatch(head) is not None and clean)

        main_gpu_uuid = _gpu_uuid(index=0)
        shared_gpu_uuid = _gpu_uuid(index=1)
        _required(shared_gpu_uuid == args.gpu_uuid and main_gpu_uuid != shared_gpu_uuid)

        names = [item[0] for item in _P0_4_CONTAINERS.values()]
        inspected = json.loads(_run_text(["docker", "container", "inspect", *names]))
        _required(isinstance(inspected, list) and len(inspected) == len(names))
        by_name = {
            str(item.get("Name") or "").removeprefix("/"): item
            for item in inspected
            if isinstance(item, dict)
        }
        _required(set(by_name) == set(names) and len(by_name) == len(inspected))
        expected_container_ids = {str(item["Id"]) for item in inspected}
        project_filter = f"label=com.docker.compose.project={args.compose_project}"
        for all_flag in (["-aq"], ["-q"]):
            project_ids = set(
                _run_text(
                    [
                        "docker",
                        "ps",
                        *all_flag,
                        "--no-trunc",
                        "--filter",
                        project_filter,
                    ]
                ).split()
            )
            _required(
                project_ids == expected_container_ids
                and all(_CONTAINER_ID.fullmatch(value) for value in project_ids)
            )
        expected_images = {
            "main": args.main_image_id,
            "qwen": args.qwen_image_id,
            "stt": args.stt_image_id,
        }
        containers: dict[str, dict[str, Any]] = {}
        for role, (name, service, gpu_index) in _P0_4_CONTAINERS.items():
            item = by_name[name]
            container_id = item.get("Id")
            state = item.get("State")
            config = item.get("Config")
            labels = config.get("Labels") if isinstance(config, dict) else None
            _required(isinstance(container_id, str))
            _required(
                _CONTAINER_ID.fullmatch(container_id) is not None
                and item.get("Image") == expected_images[role]
                and type(item.get("RestartCount")) is int
                and item["RestartCount"] == 0
                and isinstance(state, dict)
                and state.get("Running") is True
                and isinstance(state.get("StartedAt"), str)
                and bool(state["StartedAt"])
                and isinstance(state.get("Health"), dict)
                and state["Health"].get("Status") == "healthy"
                and isinstance(labels, dict)
                and labels.get("com.docker.compose.project") == args.compose_project
                and labels.get("com.docker.compose.service") == service
                and labels.get("com.docker.compose.oneoff") == "False"
            )
            environment = _environment_map(config.get("Env"))
            _required(
                environment.get("NVIDIA_VISIBLE_DEVICES") == gpu_index
                and environment.get("CUDA_VISIBLE_DEVICES") == gpu_index
            )
            observed_gpu_uuid = _gpu_uuid(container_id=container_id)
            expected_gpu_uuid = main_gpu_uuid if role == "main" else shared_gpu_uuid
            _required(observed_gpu_uuid == expected_gpu_uuid)
            containers[role] = {
                "id": container_id,
                "imageId": item["Image"],
                "service": service,
                "startedAt": state["StartedAt"],
                "restartCount": 0,
                "gpuUuid": observed_gpu_uuid,
            }

        stt_image_provenance = None
        if args.phase == "new-stt":
            _required(
                P0_4_STT_DOCKERFILE.is_file()
                and P0_4_STT_REQUIREMENTS.is_file()
            )
            dockerfile_sha256 = hashlib.sha256(
                P0_4_STT_DOCKERFILE.read_bytes()
            ).hexdigest()
            requirements_sha256 = hashlib.sha256(
                P0_4_STT_REQUIREMENTS.read_bytes()
            ).hexdigest()
            image_rows = json.loads(
                _run_text(["docker", "image", "inspect", args.stt_image_id])
            )
            _required(isinstance(image_rows, list) and len(image_rows) == 1)
            image_row = image_rows[0]
            image_config = image_row.get("Config") if isinstance(image_row, dict) else None
            image_labels = (
                image_config.get("Labels") if isinstance(image_config, dict) else None
            )
            expected_labels = {
                "org.opencontainers.image.revision": head,
                "org.opencontainers.image.base.digest": P0_4_STT_BASE_DIGEST,
                "io.evelyn.stt.dockerfile.sha256": dockerfile_sha256,
                "io.evelyn.stt.requirements.sha256": requirements_sha256,
            }
            _required(
                image_row.get("Id") == args.stt_image_id
                and isinstance(image_labels, dict)
                and all(image_labels.get(key) == value for key, value in expected_labels.items())
            )
            stt_image_provenance = {
                "sourceRevision": head,
                "baseDigest": P0_4_STT_BASE_DIGEST,
                "dockerfileSha256": dockerfile_sha256,
                "requirementsSha256": requirements_sha256,
            }

        stt_item = by_name[_P0_4_CONTAINERS["stt"][0]]
        stt_config = stt_item["Config"]
        stt_environment = _environment_map(stt_config.get("Env"))
        _required(not any(
            stt_environment.get(name) != expected
            for name, expected in (
                ("STT_MODEL_NAME", P0_4_STT_MODEL),
                ("STT_LOAD_ON_START", "true"),
                ("STT_VLLM_GPU_MEMORY_UTILIZATION", "0.35"),
                ("HF_HUB_OFFLINE", "1"),
                ("HF_HUB_DISABLE_IMPLICIT_TOKEN", "1"),
                ("HF_HOME", "/tmp/huggingface-empty"),
                ("HF_HUB_CACHE", "/root/.cache/huggingface"),
                ("HF_TOKEN", ""),
                ("HUGGING_FACE_HUB_TOKEN", ""),
                ("TRANSFORMERS_OFFLINE", "1"),
            )
        ))
        cache_mounts = [
            mount
            for mount in stt_item.get("Mounts", [])
            if isinstance(mount, dict)
            and mount.get("Destination") == "/root/.cache/huggingface"
        ]
        expected_cache_source = _canonical_host_path(_expected_hf_hub_source())
        cache_source = cache_mounts[0].get("Source") if cache_mounts else None
        _required(
            len(cache_mounts) == 1
            and cache_mounts[0].get("Type") == "bind"
            and cache_mounts[0].get("RW") is False
            and isinstance(cache_source, str)
            and _canonical_host_path(cache_source) == expected_cache_source
        )
        qwen_mounts = [
            mount
            for mount in by_name[_P0_4_CONTAINERS["qwen"][0]].get("Mounts", [])
            if isinstance(mount, dict) and mount.get("Destination") == "/llama"
        ]
        main_item = by_name[_P0_4_CONTAINERS["main"][0]]
        main_llama_mounts = [
            mount
            for mount in main_item.get("Mounts", [])
            if isinstance(mount, dict) and mount.get("Destination") == "/llama"
        ]
        main_build_mounts = [
            mount
            for mount in main_item.get("Mounts", [])
            if isinstance(mount, dict) and mount.get("Destination") == "/llama/build"
        ]
        log_mounts = [
            mount
            for mount in stt_item.get("Mounts", [])
            if isinstance(mount, dict) and mount.get("Destination") == "/app/logs"
        ]
        expected_llama, expected_main_build = _expected_llama_sources()
        _required(
            len(qwen_mounts) == 1
            and qwen_mounts[0].get("Type") == "bind"
            and qwen_mounts[0].get("RW") is False
            and isinstance(qwen_mounts[0].get("Source"), str)
            and _canonical_host_path(qwen_mounts[0]["Source"])
            == _canonical_host_path(expected_llama)
            and len(main_llama_mounts) == 1
            and main_llama_mounts[0].get("Type") == "bind"
            and main_llama_mounts[0].get("RW") is False
            and isinstance(main_llama_mounts[0].get("Source"), str)
            and _canonical_host_path(main_llama_mounts[0]["Source"])
            == _canonical_host_path(expected_llama)
            and len(main_build_mounts) == 1
            and main_build_mounts[0].get("Type") == "bind"
            and main_build_mounts[0].get("RW") is False
            and isinstance(main_build_mounts[0].get("Source"), str)
            and _canonical_host_path(main_build_mounts[0]["Source"])
            == _canonical_host_path(expected_main_build)
            and len(log_mounts) == 1
            and log_mounts[0].get("Type") == "volume"
        )

        main_id = containers["main"]["id"]
        main_epoch_identities = [
            line.strip()
            for line in _run_text(
                [
                    "docker",
                    "exec",
                    main_id,
                    "cat",
                    "/main-llm-epoch/identity",
                    "/main-llm-epoch/server-identity",
                    "/main-llm-epoch/runtime-template-identity",
                ]
            ).splitlines()
            if line.strip()
        ]
        _required(
            len(main_epoch_identities) == 3
            and all(_SHA256.fullmatch(value) for value in main_epoch_identities)
        )
        main_model_sha256 = _container_file_sha256(main_id, P0_4_MAIN_MODEL_PATH)
        main_runtime_sha256 = _container_llama_runtime_sha256(main_id)
        _required(
            main_model_sha256 == main_epoch_identities[0]
            and main_model_sha256 == P0_4_MAIN_MODEL_SHA256
            and main_runtime_sha256 == main_epoch_identities[1]
        )
        qwen_id = containers["qwen"]["id"]
        qwen_model_sha256 = _container_file_sha256(qwen_id, P0_4_QWEN_MODEL_PATH)
        qwen_runtime_sha256 = _container_llama_runtime_sha256(qwen_id)
        _required(qwen_model_sha256 == P0_4_QWEN_MODEL_SHA256)
        main_identity = {
            "modelSha256": main_model_sha256,
            "serverRuntimeSha256": main_runtime_sha256,
            "runtimeTemplateSha256": main_epoch_identities[2],
            "llamaMountSourceSha256": hashlib.sha256(
                _canonical_host_path(main_llama_mounts[0]["Source"]).encode("utf-8")
            ).hexdigest(),
            "buildMountSourceSha256": hashlib.sha256(
                _canonical_host_path(main_build_mounts[0]["Source"]).encode("utf-8")
            ).hexdigest(),
        }
        qwen_identity = {
            "modelSha256": qwen_model_sha256,
            "serverRuntimeSha256": qwen_runtime_sha256,
            "llamaMountSourceSha256": hashlib.sha256(
                _canonical_host_path(qwen_mounts[0]["Source"]).encode("utf-8")
            ).hexdigest(),
        }

        health = _get_json("http://127.0.0.1:8892/health")
        health_gpu = health.get("gpu")
        _required(
            health.get("ok") is True
            and health.get("ready") is True
            and health.get("model") == P0_4_STT_MODEL
            and health.get("backend") == P0_4_STT_BACKEND
            and health.get("loadOnStart") is True
            and isinstance(health_gpu, dict)
            and health_gpu.get("cuda") is True
            and isinstance(health_gpu.get("name"), str)
            and bool(health_gpu["name"])
        )
        cache_probe = (
            f"from pathlib import Path; p=Path('{P0_4_STT_CACHE_ROOT}'); "
            "r=(p/'refs/main').read_text().strip(); "
            "print(r+'|'+str(int((p/'snapshots'/r).is_dir())))"
        )
        raw_cache = _command_line(
            ["docker", "exec", containers["stt"]["id"], "python", "-c", cache_probe]
        )
        cache_revision, separator, snapshot_exists = raw_cache.partition("|")
        _required(
            separator == "|"
            and snapshot_exists == "1"
            and cache_revision == args.model_cache_revision
            and _COMMIT.fullmatch(cache_revision) is not None
        )
        snapshot_sha256 = _container_tree_sha256(
            containers["stt"]["id"],
            f"{P0_4_STT_CACHE_ROOT}/snapshots/{cache_revision}",
            domain="evelyn.hf-snapshot.v1",
            allowed_root=P0_4_STT_CACHE_ROOT,
        )
        checkout_source_sha256 = _tree_sha256(
            P0_4_STT_SOURCE_ROOT,
            domain="evelyn.stt-source-tree.v1",
            exclude_bytecode=True,
        )
        runtime_source_sha256 = _container_tree_sha256(
            containers["stt"]["id"],
            "/app/evelyn_core/runtime/evelyn_core",
            domain="evelyn.stt-source-tree.v1",
            exclude_bytecode=True,
        )
        source_matches_checkout = runtime_source_sha256 == checkout_source_sha256
        if args.phase == "new-stt":
            _required(source_matches_checkout)
        freeze_lines = [
            line.strip()
            for line in _run_text(
                [
                    "docker",
                    "exec",
                    containers["stt"]["id"],
                    "python",
                    "-m",
                    "pip",
                    "freeze",
                    "--all",
                ],
                timeout_sec=60.0,
            ).splitlines()
            if line.strip()
        ]
        _required(bool(freeze_lines) and len(freeze_lines) == len(set(freeze_lines)))
        dependency_sha256 = hashlib.sha256(
            ("\n".join(sorted(freeze_lines)) + "\n").encode("utf-8")
        ).hexdigest()
        embedded_package_sha256 = None
        if args.phase == "new-stt":
            embedded_package_sha256 = _container_file_sha256(
                containers["stt"]["id"],
                "/opt/evelyn/stt-package-set.txt",
            )
            _required(embedded_package_sha256 == dependency_sha256)

        return {
            "source": {"revision": head, "clean": True},
            "composeProject": args.compose_project,
            "gpus": {
                "main": {"index": 0, "uuid": main_gpu_uuid},
                "shared": {"index": 1, "uuid": shared_gpu_uuid},
            },
            "containers": containers,
            "main": main_identity,
            "qwen": qwen_identity,
            "stt": {
                "model": health["model"],
                "backend": health["backend"],
                "memoryUtilization": float(
                    stt_environment["STT_VLLM_GPU_MEMORY_UTILIZATION"]
                ),
                "modelCacheRevision": cache_revision,
                "modelContentSha256": snapshot_sha256,
                "cacheSourceSha256": hashlib.sha256(
                    expected_cache_source.encode("utf-8")
                ).hexdigest(),
                "packageSetSha256": dependency_sha256,
                "embeddedPackageSetSha256": embedded_package_sha256,
                "runtimeSourceTreeSha256": runtime_source_sha256,
                "checkoutSourceTreeSha256": checkout_source_sha256,
                "sourceMatchesCheckout": source_matches_checkout,
                "imageProvenance": stt_image_provenance,
                "cuda": True,
                "gpuName": health_gpu["name"],
                "cacheReadOnly": True,
                "offline": True,
            },
        }
    except _ValidationFailure:
        raise
    except Exception:
        raise _ValidationFailure("validation_preflight_failed") from None


def budget_violations(
    report: dict[str, Any],
    *,
    minimum_samples: int = 5,
) -> tuple[str, ...]:
    budgets = report.get("budgets") or {}
    summary = report.get("summary") or {}
    violations: list[str] = []
    sample_count = summary.get("sampleCount")
    if not _finite_number(sample_count) or sample_count < minimum_samples:
        violations.append("insufficient_samples")
    gpu_sample_count = summary.get("gpuSampleCount")
    if not _finite_number(gpu_sample_count) or gpu_sample_count < 1:
        violations.append("gpu_metrics_missing")
    for reason, observed_key, budget_key, direction in (
        ("fast_main_ttft", "fastMainTtftP95Ms", "fastMainTtftP95Ms", "max"),
        ("qwen_latency", "qwenLatencyP95Ms", "qwenTimeoutMs", "max"),
        ("stt_final_latency", "sttFinalLatencyP95Ms", "sttFinalLatencyP95Ms", "max"),
        ("gpu_free_memory", "gpuMinFreeMb", "gpuMinFreeMb", "min"),
    ):
        observed = summary.get(observed_key)
        limit = budgets.get(budget_key)
        if not _finite_number(observed) or not _finite_number(limit):
            violations.append(f"{reason}_missing")
        elif (direction == "max" and observed > limit) or (
            direction == "min" and observed < limit
        ):
            violations.append(f"{reason}_budget_exceeded")
    for key, reason in (
        ("mainErrorCount", "fast_main_error"),
        ("qwenErrorCount", "qwen_error"),
        ("sttErrorCount", "stt_error"),
        ("gpuErrorCount", "gpu_metrics_error"),
    ):
        value = summary.get(key)
        if not _finite_number(value) or value > 0:
            violations.append(reason)
    timeout_count = summary.get("qwenTimeoutCount")
    timeout_limit = budgets.get("qwenTimeoutCountMax")
    if (
        not _finite_number(timeout_count)
        or not _finite_number(timeout_limit)
        or timeout_count > timeout_limit
    ):
        violations.append("qwen_timeout_budget_exceeded")
    return tuple(dict.fromkeys(violations))


def percentile_p95(values: list[float]) -> float | None:
    if not values or not all(_finite_number(value) for value in values):
        return None
    ordered = sorted(float(value) for value in values)
    return round(ordered[max(0, math.ceil(len(ordered) * 0.95) - 1)], 1)


def _fixed_main_prompt() -> str:
    prompt = "\n".join((build_evelyn_system_prompt(), FAST_MAIN_LLM_USER_PREFIX))
    if len(prompt) != MAIN_PROMPT_CHARS:
        raise RuntimeError("fast_main_prompt_contract_changed")
    return prompt


def _post_json(
    url: str,
    payload: dict[str, Any],
    *,
    timeout_sec: float,
) -> dict[str, Any]:
    req = request.Request(
        url,
        data=json.dumps(payload, separators=(",", ":")).encode("utf-8"),
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    with request.urlopen(req, timeout=max(0.1, timeout_sec)) as response:
        body = json.loads(response.read().decode("utf-8"))
    if not isinstance(body, dict):
        raise RuntimeError("response_not_json_object")
    return body


def _main_ttft(
    start: threading.Event,
    *,
    url: str,
    model: str,
    timeout_sec: float,
) -> dict[str, Any]:
    payload = {
        "model": model,
        "messages": [
            {"role": "system", "content": _fixed_main_prompt()},
            {"role": "user", "content": "한 문장으로 준비됐다고 답해줘."},
        ],
        "temperature": 0,
        "max_tokens": 32,
        "stream": True,
        "cache_prompt": True,
        "timings_per_token": True,
    }
    start.wait()
    started = time.perf_counter()
    try:
        req = request.Request(
            url,
            data=json.dumps(payload, separators=(",", ":")).encode("utf-8"),
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        first_token_ms: float | None = None
        timing_metrics: dict[str, Any] = {}
        with request.urlopen(req, timeout=max(0.1, timeout_sec)) as response:
            for raw_line in response:
                line = raw_line.decode("utf-8", errors="replace").strip()
                if not line.startswith("data:"):
                    continue
                data = line[5:].strip()
                if data == "[DONE]":
                    break
                event_payload = json.loads(data)
                timing_metrics.update(extract_llama_timing_metrics(event_payload))
                choices = event_payload.get("choices") or []
                delta = choices[0].get("delta") if choices else None
                content = delta.get("content") if isinstance(delta, dict) else None
                if content and first_token_ms is None:
                    first_token_ms = (time.perf_counter() - started) * 1000.0
        if first_token_ms is None:
            raise RuntimeError("main_first_token_missing")
        return {
            "ok": True,
            "ttftMs": round(first_token_ms, 1),
            "totalMs": round((time.perf_counter() - started) * 1000.0, 1),
            **timing_metrics,
        }
    except Exception as exc:  # noqa: BLE001 - report stores type only.
        return {"ok": False, "errorType": type(exc).__name__}


def _qwen_specialist(
    start: threading.Event,
    *,
    url: str,
    model: str,
    timeout_sec: float,
) -> dict[str, Any]:
    payload = {
        "model": model,
        "messages": [
            {
                "role": "system",
                "content": (
                    "You are Evelyn's deep-analysis specialist. Return compact evidence, "
                    "not a user-facing answer."
                ),
            },
            {
                "role": "user",
                "content": "Compare two safe implementation options and list three checks.",
            },
        ],
        "temperature": 0,
        "top_p": 0.8,
        "max_tokens": 256,
        "stream": False,
        "chat_template_kwargs": {"enable_thinking": False},
    }
    start.wait()
    started = time.perf_counter()
    try:
        body = _post_json(url, payload, timeout_sec=timeout_sec)
        choices = body.get("choices") or []
        message = choices[0].get("message") if choices else None
        if not isinstance(message, dict) or not str(message.get("content") or "").strip():
            raise RuntimeError("qwen_response_invalid")
        return {
            "ok": True,
            "timedOut": False,
            "latencyMs": round((time.perf_counter() - started) * 1000.0, 1),
        }
    except Exception as exc:  # noqa: BLE001 - report stores type only.
        timed_out = isinstance(exc, (TimeoutError, socket.timeout)) or (
            isinstance(exc, error.URLError)
            and isinstance(getattr(exc, "reason", None), (TimeoutError, socket.timeout))
        )
        return {
            "ok": False,
            "timedOut": timed_out,
            "latencyMs": round((time.perf_counter() - started) * 1000.0, 1),
            "errorType": type(exc).__name__,
        }


def _load_audio(path: Path) -> tuple[dict[str, Any], dict[str, Any]]:
    with wave.open(str(path), "rb") as wav:
        if (
            wav.getnchannels() != 1
            or wav.getsampwidth() != 2
            or wav.getframerate() != 16_000
            or wav.getcomptype() != "NONE"
        ):
            raise ValueError("audio_must_be_pcm16_mono_16khz")
        frames = wav.readframes(wav.getnframes())
        frame_count = wav.getnframes()
    samples = array("h")
    samples.frombytes(frames)
    if sys.byteorder != "little":
        samples.byteswap()
    audio_f32 = array("f", (sample / 32768.0 for sample in samples))
    raw_f32 = audio_f32.tobytes()
    payload = {
        "audio_f32_base64": base64.b64encode(raw_f32).decode("ascii"),
        "sample_count": len(audio_f32),
        "sampling_rate": 16_000,
        "max_new_tokens": 256,
        "stage": "gpu1-concurrency-benchmark",
        "language": "Korean",
        "validation_bound": True,
    }
    metadata = {
        "sha256": hashlib.sha256(frames).hexdigest(),
        "durationMs": round(frame_count / 16_000.0 * 1000.0, 1),
        "sampleCount": frame_count,
    }
    return payload, metadata


def _stt_final(
    start: threading.Event,
    *,
    url: str,
    payload: dict[str, Any],
    timeout_sec: float,
    expected_model: str,
) -> dict[str, Any]:
    start.wait()
    started = time.perf_counter()
    try:
        body = _post_json(url, payload, timeout_sec=timeout_sec)
        if not isinstance(body.get("text"), str):
            raise RuntimeError("stt_response_invalid")
        if body.get("model") != expected_model:
            raise RuntimeError("stt_model_identity_mismatch")
        duration_ms = body.get("durationMs")
        if not _finite_number(duration_ms) or float(duration_ms) < 0.0:
            raise RuntimeError("stt_duration_invalid")
        return {
            "ok": True,
            "latencyMs": round((time.perf_counter() - started) * 1000.0, 1),
            "serviceDurationMs": round(float(duration_ms), 1),
        }
    except Exception as exc:  # noqa: BLE001 - report stores type only.
        return {"ok": False, "errorType": type(exc).__name__}


def _gpu_samples(
    start: threading.Event,
    done: threading.Event,
    *,
    gpu_index: int,
    interval_sec: float,
) -> dict[str, Any]:
    start.wait()
    samples: list[dict[str, float]] = []
    errors = 0
    while True:
        try:
            completed = subprocess.run(
                [
                    "nvidia-smi",
                    "-i",
                    str(gpu_index),
                    "--query-gpu=memory.used,memory.free,memory.total,utilization.gpu",
                    "--format=csv,noheader,nounits",
                ],
                check=True,
                capture_output=True,
                text=True,
                timeout=2.0,
            )
            values = [float(item.strip()) for item in completed.stdout.splitlines()[0].split(",")]
            if (
                len(values) != 4
                or not all(_finite_number(value) for value in values)
                or values[0] < 0.0
                or values[1] < 0.0
                or values[2] <= 0.0
                or not 0.0 <= values[3] <= 100.0
            ):
                raise ValueError("gpu_metrics_invalid")
            samples.append(
                {
                    "usedMb": values[0],
                    "freeMb": values[1],
                    "totalMb": values[2],
                    "utilizationPct": values[3],
                }
            )
        except Exception:  # noqa: BLE001 - counter is sufficient and content-free.
            errors += 1
        if done.wait(max(0.01, interval_sec)):
            break
    return {"samples": samples, "errorCount": errors}


def run_iteration(args: argparse.Namespace, stt_payload: dict[str, Any]) -> dict[str, Any]:
    start = threading.Event()
    done = threading.Event()
    with ThreadPoolExecutor(max_workers=4) as pool:
        main_future = pool.submit(
            _main_ttft,
            start,
            url=args.main_url,
            model=args.main_model,
            timeout_sec=args.main_timeout_sec,
        )
        qwen_future = pool.submit(
            _qwen_specialist,
            start,
            url=args.qwen_url,
            model=args.qwen_model,
            timeout_sec=args.qwen_timeout_ms / 1000.0,
        )
        stt_future = pool.submit(
            _stt_final,
            start,
            url=args.stt_url,
            payload=stt_payload,
            timeout_sec=args.stt_timeout_sec,
            expected_model=args.stt_model,
        )
        gpu_future = pool.submit(
            _gpu_samples,
            start,
            done,
            gpu_index=args.gpu_index,
            interval_sec=args.gpu_sample_interval_ms / 1000.0,
        )
        start.set()
        main_result = main_future.result()
        qwen_result = qwen_future.result()
        stt_result = stt_future.result()
        done.set()
        gpu_result = gpu_future.result()
    return {
        "main": main_result,
        "qwen": qwen_result,
        "stt": stt_result,
        "gpu": gpu_result,
    }


def summarize(iterations: list[dict[str, Any]]) -> dict[str, Any]:
    main_ttft = [row["main"]["ttftMs"] for row in iterations if row["main"].get("ok")]
    main_prompt_eval = [
        row["main"]["promptEvalMs"]
        for row in iterations
        if row["main"].get("ok")
        and isinstance(row["main"].get("promptEvalMs"), (int, float))
    ]
    main_prompt_rate = [
        row["main"]["promptTokensPerSec"]
        for row in iterations
        if row["main"].get("ok")
        and isinstance(row["main"].get("promptTokensPerSec"), (int, float))
    ]
    main_cache_ratio = [
        row["main"]["promptCacheHitRatio"]
        for row in iterations
        if row["main"].get("ok")
        and isinstance(row["main"].get("promptCacheHitRatio"), (int, float))
    ]
    qwen_latency = [row["qwen"]["latencyMs"] for row in iterations if row["qwen"].get("ok")]
    stt_latency = [row["stt"]["latencyMs"] for row in iterations if row["stt"].get("ok")]
    gpu_samples = [sample for row in iterations for sample in row["gpu"]["samples"]]
    return {
        "sampleCount": len(iterations),
        "fastMainTtftP95Ms": percentile_p95(main_ttft),
        "mainPromptEvalP95Ms": percentile_p95(main_prompt_eval),
        "mainPromptTokensPerSecAvg": (
            round(sum(main_prompt_rate) / len(main_prompt_rate), 1)
            if main_prompt_rate
            else None
        ),
        "mainPromptCacheHitRatioMin": min(main_cache_ratio, default=None),
        "mainPromptCacheHitRatioAvg": (
            round(sum(main_cache_ratio) / len(main_cache_ratio), 4)
            if main_cache_ratio
            else None
        ),
        "qwenLatencyP95Ms": percentile_p95(qwen_latency),
        "qwenTimeoutCount": sum(bool(row["qwen"].get("timedOut")) for row in iterations),
        "sttFinalLatencyP95Ms": percentile_p95(stt_latency),
        "gpuPeakUsedMb": max((sample["usedMb"] for sample in gpu_samples), default=None),
        "gpuMinFreeMb": min((sample["freeMb"] for sample in gpu_samples), default=None),
        "gpuPeakUtilizationPct": max((sample["utilizationPct"] for sample in gpu_samples), default=None),
        "gpuSampleCount": len(gpu_samples),
        "mainErrorCount": sum(not row["main"].get("ok") for row in iterations),
        "qwenErrorCount": sum(
            not row["qwen"].get("ok") and not row["qwen"].get("timedOut")
            for row in iterations
        ),
        "sttErrorCount": sum(not row["stt"].get("ok") for row in iterations),
        "gpuErrorCount": sum(row["gpu"]["errorCount"] for row in iterations),
    }


def _validation_binding(
    args: argparse.Namespace,
    observed: dict[str, Any] | None,
) -> dict[str, Any] | None:
    if args.phase == "diagnostic":
        return None
    if not isinstance(observed, dict):
        raise _ValidationFailure("validation_preflight_failed")
    containers = observed["containers"]
    binding = {
        "attemptId": args.attempt_id,
        "phase": args.phase,
        "sourceRevision": observed["source"]["revision"],
        "composeProject": observed["composeProject"],
        "images": {
            role: containers[role]["imageId"]
            for role in ("main", "qwen", "stt")
        },
        "models": {
            "main": args.main_model,
            "qwen": args.qwen_model,
            "stt": observed["stt"]["model"],
        },
        "gpuUuid": observed["gpus"]["shared"]["uuid"],
        "mainGpuUuid": observed["gpus"]["main"]["uuid"],
        "main": dict(observed["main"]),
        "qwen": dict(observed["qwen"]),
        "stt": dict(observed["stt"]),
    }
    return binding


def _sample_contract_valid(iterations: Any) -> bool:
    def numbers(mapping: Any, keys: set[str]) -> bool:
        return isinstance(mapping, dict) and all(
            _finite_number(mapping.get(key)) and float(mapping[key]) >= 0.0
            for key in keys
        )

    if not isinstance(iterations, list):
        return False
    for row in iterations:
        if not isinstance(row, dict) or set(row) != {"main", "qwen", "stt", "gpu"}:
            return False
        main, qwen, stt, gpu = (row[key] for key in ("main", "qwen", "stt", "gpu"))
        if not (
            isinstance(main, dict)
            and numbers(main, set(main) - {"ok"})
            and main.get("ok") is True
            and {"ok", "ttftMs", "totalMs"}.issubset(main)
            and set(main).issubset(_MAIN_SAMPLE_KEYS)
            and 0.0 <= float(main.get("promptCacheHitRatio", 0.0)) <= 1.0
            and numbers(qwen, {"latencyMs"})
            and qwen == {"ok": True, "timedOut": False, "latencyMs": qwen["latencyMs"]}
            and numbers(stt, {"latencyMs", "serviceDurationMs"})
            and stt == {
                "ok": True,
                "latencyMs": stt["latencyMs"],
                "serviceDurationMs": stt["serviceDurationMs"],
            }
            and isinstance(gpu, dict)
            and set(gpu) == {"samples", "errorCount"}
            and type(gpu["errorCount"]) is int
            and gpu["errorCount"] == 0
            and isinstance(gpu["samples"], list)
            and bool(gpu["samples"])
        ):
            return False
        for sample in gpu["samples"]:
            if not (
                numbers(sample, {"usedMb", "freeMb", "totalMb", "utilizationPct"})
                and set(sample) == {"usedMb", "freeMb", "totalMb", "utilizationPct"}
                and 0.0 < float(sample["totalMb"])
                and max(float(sample["usedMb"]), float(sample["freeMb"]))
                <= float(sample["totalMb"])
                and float(sample["utilizationPct"]) <= 100.0
            ):
                return False
    return True


def _fixed_p0_budgets() -> dict[str, Any]:
    return {
        "fastMainTtftP95Ms": P0_4_MAIN_TTFT_BUDGET_MS,
        "qwenTimeoutMs": P0_4_QWEN_TIMEOUT_MS,
        "qwenTimeoutCountMax": 0,
        "sttFinalLatencyP95Ms": P0_4_STT_FINAL_BUDGET_MS,
        "gpuMinFreeMb": P0_4_GPU_MIN_FREE_MB,
    }


def _content_sha256(value: Any) -> str:
    raw = json.dumps(
        value,
        ensure_ascii=True,
        allow_nan=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return hashlib.sha256(raw).hexdigest()


def _p0_report_violations(
    report: Any,
    *,
    expected_phase: str,
) -> list[str]:
    violations: list[str] = []
    if not isinstance(report, dict) or report.get("schema") != GPU1_CONCURRENCY_REPORT_SCHEMA:
        return [f"{expected_phase}_report_schema_invalid"]
    scenario = report.get("scenario")
    samples = report.get("samples")
    summary = report.get("summary")
    binding = report.get("binding")
    proof = report.get("environmentProof")
    expected_scenario = {
        "name": "fast-main-qwen-specialist-stt-overlap",
        "gpuIndex": 1,
        "warmupIterations": 2,
        "iterations": 20,
        "parallelStart": True,
        "mainPromptChars": MAIN_PROMPT_CHARS,
        "mainPromptSha256": hashlib.sha256(
            _fixed_main_prompt().encode("utf-8")
        ).hexdigest(),
        "audio": {
            "sha256": GPU1_BENCHMARK_AUDIO_SHA256,
            "durationMs": 1_640.0,
            "sampleCount": 26_240,
        },
    }
    if scenario != expected_scenario:
        violations.append(f"{expected_phase}_scenario_invalid")
    if report.get("budgets") != _fixed_p0_budgets():
        violations.append(f"{expected_phase}_budget_contract_invalid")
    if (
        not _sample_contract_valid(samples)
        or len(samples) != 20
        or not isinstance(summary, dict)
        or (_sample_contract_valid(samples) and summary != summarize(samples))
    ):
        violations.append(f"{expected_phase}_sample_integrity_invalid")
    if (
        report.get("status") != "pass"
        or report.get("violations") != []
        or not isinstance(summary, dict)
        or budget_violations(
            {"budgets": report.get("budgets"), "summary": summary},
            minimum_samples=20,
        )
    ):
        violations.append(f"{expected_phase}_absolute_gate_failed")
    binding_map = binding if isinstance(binding, dict) else {}
    main_identity = binding_map.get("main")
    qwen_identity = binding_map.get("qwen")
    stt_identity = binding_map.get("stt")
    main_identity_valid = (
        isinstance(main_identity, dict)
        and set(main_identity)
        == {
            "modelSha256",
            "serverRuntimeSha256",
            "runtimeTemplateSha256",
            "llamaMountSourceSha256",
            "buildMountSourceSha256",
        }
        and all(_SHA256.fullmatch(str(value or "")) for value in main_identity.values())
        and main_identity.get("modelSha256") == P0_4_MAIN_MODEL_SHA256
    )
    qwen_identity_valid = (
        isinstance(qwen_identity, dict)
        and set(qwen_identity)
        == {"modelSha256", "serverRuntimeSha256", "llamaMountSourceSha256"}
        and all(_SHA256.fullmatch(str(value or "")) for value in qwen_identity.values())
        and qwen_identity.get("modelSha256") == P0_4_QWEN_MODEL_SHA256
    )
    stt_hash_keys = (
        "modelContentSha256",
        "cacheSourceSha256",
        "packageSetSha256",
        "runtimeSourceTreeSha256",
        "checkoutSourceTreeSha256",
    )
    stt_identity_valid = (
        isinstance(stt_identity, dict)
        and stt_identity.get("model") == P0_4_STT_MODEL
        and stt_identity.get("backend") == P0_4_STT_BACKEND
        and stt_identity.get("memoryUtilization") == P0_4_STT_MEMORY_UTILIZATION
        and _COMMIT.fullmatch(str(stt_identity.get("modelCacheRevision") or ""))
        is not None
        and stt_identity.get("cuda") is True
        and isinstance(stt_identity.get("gpuName"), str)
        and bool(stt_identity["gpuName"])
        and stt_identity.get("cacheReadOnly") is True
        and stt_identity.get("offline") is True
        and all(
            _SHA256.fullmatch(str(stt_identity.get(key) or ""))
            for key in stt_hash_keys
        )
        and type(stt_identity.get("sourceMatchesCheckout")) is bool
        and (
            expected_phase == "old-stt"
            and stt_identity.get("embeddedPackageSetSha256") is None
            and stt_identity.get("imageProvenance") is None
            or expected_phase == "new-stt"
            and stt_identity.get("sourceMatchesCheckout") is True
            and stt_identity.get("runtimeSourceTreeSha256")
            == stt_identity.get("checkoutSourceTreeSha256")
            and stt_identity.get("embeddedPackageSetSha256")
            == stt_identity.get("packageSetSha256")
            and isinstance(stt_identity.get("imageProvenance"), dict)
            and set(stt_identity["imageProvenance"])
            == {
                "sourceRevision",
                "baseDigest",
                "dockerfileSha256",
                "requirementsSha256",
            }
            and stt_identity["imageProvenance"].get("sourceRevision")
            == binding_map.get("sourceRevision")
            and stt_identity["imageProvenance"].get("baseDigest")
            == P0_4_STT_BASE_DIGEST
            and _SHA256.fullmatch(
                str(stt_identity["imageProvenance"].get("dockerfileSha256") or "")
            )
            is not None
            and _SHA256.fullmatch(
                str(stt_identity["imageProvenance"].get("requirementsSha256") or "")
            )
            is not None
            and stt_identity["imageProvenance"].get("dockerfileSha256")
            == hashlib.sha256(P0_4_STT_DOCKERFILE.read_bytes()).hexdigest()
            and stt_identity["imageProvenance"].get("requirementsSha256")
            == hashlib.sha256(P0_4_STT_REQUIREMENTS.read_bytes()).hexdigest()
        )
    )
    if (
        not isinstance(binding, dict)
        or binding.get("phase") != expected_phase
        or _ATTEMPT_ID.fullmatch(str(binding.get("attemptId") or "")) is None
        or _COMMIT.fullmatch(str(binding.get("sourceRevision") or "")) is None
        or _GPU_UUID.fullmatch(str(binding.get("gpuUuid") or "")) is None
        or _GPU_UUID.fullmatch(str(binding.get("mainGpuUuid") or "")) is None
        or not isinstance(binding.get("images"), dict)
        or any(
            _IMAGE_ID.fullmatch(str(binding["images"].get(role) or "")) is None
            for role in ("main", "qwen", "stt")
        )
        or binding.get("models")
        != {
            "main": P0_4_MAIN_MODEL,
            "qwen": P0_4_QWEN_MODEL,
            "stt": P0_4_STT_MODEL,
        }
        or not main_identity_valid
        or not qwen_identity_valid
        or not stt_identity_valid
    ):
        violations.append(f"{expected_phase}_binding_invalid")
    if (
        not isinstance(proof, dict)
        or proof.get("stable") is not True
        or _SHA256.fullmatch(str(proof.get("preflightSha256") or "")) is None
        or proof.get("preflightSha256") != proof.get("postflightSha256")
    ):
        violations.append(f"{expected_phase}_environment_proof_invalid")
    if expected_phase == "old-stt":
        if "baselineReportSha256" in report:
            violations.append("old_baseline_hash_forbidden")
    elif _SHA256.fullmatch(str(report.get("baselineReportSha256") or "")) is None:
        violations.append("new_baseline_hash_invalid")
    return list(dict.fromkeys(violations))


def _binding_comparison_violations(
    old_binding: dict[str, Any],
    new_binding: dict[str, Any],
) -> list[str]:
    violations: list[str] = []
    for key in (
        "attemptId",
        "sourceRevision",
        "composeProject",
        "gpuUuid",
        "mainGpuUuid",
    ):
        if old_binding.get(key) != new_binding.get(key):
            violations.append(f"{key}_mismatch")
    old_images = old_binding.get("images") or {}
    new_images = new_binding.get("images") or {}
    for key in ("main", "qwen"):
        if old_images.get(key) != new_images.get(key):
            violations.append(f"{key}_image_mismatch")
    if old_images.get("stt") == new_images.get("stt"):
        violations.append("stt_image_unchanged")
    if old_binding.get("models") != new_binding.get("models"):
        violations.append("model_identity_mismatch")
    if old_binding.get("main") != new_binding.get("main"):
        violations.append("main_runtime_identity_mismatch")
    if old_binding.get("qwen") != new_binding.get("qwen"):
        violations.append("qwen_runtime_identity_mismatch")
    stable_stt_keys = (
        "model",
        "backend",
        "memoryUtilization",
        "modelCacheRevision",
        "modelContentSha256",
        "cacheSourceSha256",
        "checkoutSourceTreeSha256",
        "cuda",
        "gpuName",
        "cacheReadOnly",
        "offline",
    )
    if any(
        (old_binding.get("stt") or {}).get(key)
        != (new_binding.get("stt") or {}).get(key)
        for key in stable_stt_keys
    ):
        violations.append("stt_runtime_identity_mismatch")
    return violations


def compare_stt_baseline(
    baseline: dict[str, Any],
    candidate: dict[str, Any],
    *,
    baseline_sha256: str,
) -> dict[str, Any]:
    violations = [
        *_p0_report_violations(baseline, expected_phase="old-stt"),
        *_p0_report_violations(candidate, expected_phase="new-stt"),
    ]
    old_binding = baseline.get("binding")
    new_binding = candidate.get("binding")
    if isinstance(old_binding, dict) and isinstance(new_binding, dict):
        violations.extend(_binding_comparison_violations(old_binding, new_binding))
    if (
        _SHA256.fullmatch(str(baseline_sha256 or "")) is None
        or candidate.get("baselineReportSha256") != baseline_sha256
    ):
        violations.append("baseline_report_hash_mismatch")
    old_p95 = (baseline.get("summary") or {}).get("sttFinalLatencyP95Ms")
    new_p95 = (candidate.get("summary") or {}).get("sttFinalLatencyP95Ms")
    limit = None
    if _finite_number(old_p95) and _finite_number(new_p95):
        limit = round(float(old_p95) * 1.10, 3)
        if float(new_p95) > limit:
            violations.append("stt_relative_regression")
    else:
        violations.append("stt_relative_metric_missing")
    violations = list(dict.fromkeys(violations))
    return {
        "status": "pass" if not violations else "fail",
        "oldP95Ms": old_p95,
        "newP95Ms": new_p95,
        "maximumNewP95Ms": limit,
        "violations": violations,
    }


def build_report(
    args: argparse.Namespace,
    *,
    iterations: list[dict[str, Any]],
    audio_metadata: dict[str, Any],
    observed_environment: dict[str, Any] | None = None,
    baseline_sha256: str | None = None,
    generated_at: float | None = None,
) -> dict[str, Any]:
    created = time.time() if generated_at is None else float(generated_at)
    budgets = {
        "fastMainTtftP95Ms": float(args.main_ttft_budget_ms),
        "qwenTimeoutMs": float(args.qwen_timeout_ms),
        "qwenTimeoutCountMax": 0,
        "sttFinalLatencyP95Ms": float(args.stt_final_budget_ms),
        "gpuMinFreeMb": float(args.gpu_min_free_mb),
    }
    summary = summarize(iterations)
    candidate = {"budgets": budgets, "summary": summary}
    violations = list(budget_violations(candidate, minimum_samples=5))
    if not _sample_contract_valid(iterations) and iterations:
        violations.append("sample_integrity_invalid")
    if audio_metadata.get("sha256") != GPU1_BENCHMARK_AUDIO_SHA256:
        violations.append("audio_fixture_mismatch")
    report = {
        "schema": GPU1_CONCURRENCY_REPORT_SCHEMA,
        "generatedAt": datetime.fromtimestamp(created, timezone.utc).isoformat(),
        "generatedAtEpochSec": created,
        "status": "pass" if not violations else "fail",
        "scenario": {
            "name": "fast-main-qwen-specialist-stt-overlap",
            "gpuIndex": int(args.gpu_index),
            "warmupIterations": int(args.warmup_iterations),
            "iterations": int(args.iterations),
            "parallelStart": True,
            "mainPromptChars": MAIN_PROMPT_CHARS,
            "mainPromptSha256": hashlib.sha256(
                _fixed_main_prompt().encode("utf-8")
            ).hexdigest(),
            "audio": audio_metadata,
        },
        "budgets": budgets,
        "summary": summary,
        "violations": violations,
        "samples": iterations,
    }
    binding = _validation_binding(args, observed_environment)
    if binding is not None:
        report["binding"] = binding
        report["environmentProof"] = {
            "preflightSha256": _content_sha256(observed_environment),
            "postflightSha256": None,
            "stable": False,
        }
    if baseline_sha256 is not None:
        report["baselineReportSha256"] = baseline_sha256
    return report


def _write_report(path: Path, report: dict[str, Any]) -> None:
    atomic_json_write(path, report, durable=True)


def _unique_json_object(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("duplicate_json_key")
        result[key] = value
    return result


def _reject_json_constant(_: str) -> None:
    raise ValueError("nonfinite_json_number")


def _read_report_with_sha256(path: Path) -> tuple[dict[str, Any], str]:
    candidate = Path(path)
    before = candidate.lstat()
    file_attributes = int(getattr(before, "st_file_attributes", 0) or 0)
    reparse_flag = int(getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0) or 0)
    if (
        not stat.S_ISREG(before.st_mode)
        or candidate.is_symlink()
        or bool(file_attributes & reparse_flag)
        or before.st_size < 2
        or before.st_size > _MAX_REPORT_BYTES
    ):
        raise ValueError("baseline_report_path_invalid")
    raw = candidate.read_bytes()
    after = candidate.lstat()
    identity_fields = ("st_dev", "st_ino", "st_size", "st_mtime_ns")
    if (
        len(raw) != before.st_size
        or any(getattr(before, name) != getattr(after, name) for name in identity_fields)
    ):
        raise ValueError("baseline_report_changed")
    payload = json.loads(
        raw.decode("utf-8"),
        object_pairs_hook=_unique_json_object,
        parse_constant=_reject_json_constant,
    )
    if not isinstance(payload, dict):
        raise ValueError("baseline_report_invalid")
    return payload, hashlib.sha256(raw).hexdigest()


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Measure concurrent Fast Main, Qwen specialist, STT, and GPU1 budgets."
    )
    parser.add_argument("--main-url", default="http://127.0.0.1:9820/v1/chat/completions")
    parser.add_argument("--main-model", default="gemma-4-12B-it-IQ4_XS-text-only")
    parser.add_argument("--qwen-url", default="http://127.0.0.1:9823/v1/chat/completions")
    parser.add_argument("--qwen-model", default="Qwen3-14B-Q4_K_M.gguf")
    parser.add_argument("--stt-url", default="http://127.0.0.1:8892/v1/stt/transcribe")
    parser.add_argument("--audio", type=Path, default=DEFAULT_AUDIO)
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    parser.add_argument("--gpu-index", type=int, default=1)
    parser.add_argument("--warmup-iterations", type=int, default=1)
    parser.add_argument("--iterations", type=int, default=5)
    parser.add_argument("--gpu-sample-interval-ms", type=float, default=50.0)
    parser.add_argument("--main-timeout-sec", type=float, default=15.0)
    parser.add_argument("--stt-timeout-sec", type=float, default=15.0)
    parser.add_argument("--main-ttft-budget-ms", type=float, default=1_000.0)
    parser.add_argument("--qwen-timeout-ms", type=float, default=6_000.0)
    parser.add_argument("--stt-final-budget-ms", type=float, default=1_200.0)
    parser.add_argument("--gpu-min-free-mb", type=float, default=2_048.0)
    parser.add_argument(
        "--phase",
        choices=("diagnostic", "old-stt", "new-stt"),
        default="diagnostic",
    )
    parser.add_argument("--attempt-id", default="")
    parser.add_argument("--compose-project", default="")
    parser.add_argument("--source-revision", default="")
    parser.add_argument("--main-image-id", default="")
    parser.add_argument("--qwen-image-id", default="")
    parser.add_argument("--stt-image-id", default="")
    parser.add_argument("--gpu-uuid", default="")
    parser.add_argument("--model-cache-revision", default="")
    parser.add_argument("--stt-model", default="Qwen/Qwen3-ASR-1.7B")
    parser.add_argument("--stt-backend", default="vllm")
    parser.add_argument("--stt-memory-utilization", type=float, default=0.35)
    parser.add_argument("--baseline-report", type=Path)
    parser.add_argument("--baseline-sha256", default="")
    args = parser.parse_args(argv)
    if args.iterations < 1 or args.warmup_iterations < 0:
        parser.error("iterations must be positive and warmup-iterations nonnegative")
    numeric_values = (
        args.gpu_sample_interval_ms,
        args.main_timeout_sec,
        args.stt_timeout_sec,
        args.main_ttft_budget_ms,
        args.qwen_timeout_ms,
        args.stt_final_budget_ms,
        args.gpu_min_free_mb,
        args.stt_memory_utilization,
    )
    if (
        args.gpu_index != 1
        or not all(_finite_number(value) and float(value) > 0.0 for value in numeric_values)
        or float(args.stt_memory_utilization) > 1.0
    ):
        parser.error("gpu-index must be 1 and sample interval positive")
    if args.phase != "diagnostic":
        identities = (
            _ATTEMPT_ID.fullmatch(args.attempt_id),
            _COMPOSE_PROJECT.fullmatch(args.compose_project),
            _COMMIT.fullmatch(args.source_revision),
            _IMAGE_ID.fullmatch(args.main_image_id),
            _IMAGE_ID.fullmatch(args.qwen_image_id),
            _IMAGE_ID.fullmatch(args.stt_image_id),
            _GPU_UUID.fullmatch(args.gpu_uuid),
            _COMMIT.fullmatch(args.model_cache_revision),
        )
        if not all(identities):
            parser.error("P0-4 phases require exact attempt/source/image/GPU/model identities")
        if args.warmup_iterations != 2 or args.iterations != 20:
            parser.error("P0-4 phases require warmup 2 and measured 20")
        if (
            args.main_url != P0_4_MAIN_URL
            or args.main_model != P0_4_MAIN_MODEL
            or args.qwen_url != P0_4_QWEN_URL
            or args.qwen_model != P0_4_QWEN_MODEL
            or args.stt_url != P0_4_STT_URL
            or args.audio.resolve() != DEFAULT_AUDIO.resolve()
            or args.gpu_sample_interval_ms != P0_4_GPU_SAMPLE_INTERVAL_MS
            or args.main_timeout_sec != P0_4_MAIN_TIMEOUT_SEC
            or args.stt_timeout_sec != P0_4_STT_TIMEOUT_SEC
            or args.main_ttft_budget_ms != P0_4_MAIN_TTFT_BUDGET_MS
            or args.qwen_timeout_ms != P0_4_QWEN_TIMEOUT_MS
            or args.stt_final_budget_ms != P0_4_STT_FINAL_BUDGET_MS
            or args.gpu_min_free_mb != P0_4_GPU_MIN_FREE_MB
            or args.stt_model != P0_4_STT_MODEL
            or args.stt_backend != P0_4_STT_BACKEND
            or args.stt_memory_utilization != P0_4_STT_MEMORY_UTILIZATION
        ):
            parser.error("P0-4 fixed endpoint/model/budget contract changed")
        baseline_present = args.baseline_report is not None
        baseline_hash_valid = _SHA256.fullmatch(args.baseline_sha256) is not None
        if args.phase == "new-stt":
            if not baseline_present or not baseline_hash_valid:
                parser.error("new-stt requires one exact baseline report hash")
            try:
                paths_alias = (
                    args.output.resolve(strict=False)
                    == args.baseline_report.resolve(strict=False)
                    or args.output.exists()
                    and args.baseline_report.exists()
                    and args.output.samefile(args.baseline_report)
                )
            except OSError:
                parser.error("baseline/output path identity could not be verified")
            if paths_alias:
                parser.error("baseline report and candidate output must be distinct")
        elif baseline_present or args.baseline_sha256:
            parser.error("old-stt forbids a baseline report and hash")
    return args


def _terminal_preflight_failure(
    args: argparse.Namespace,
    code: str,
) -> int:
    created = time.time()
    report = {
        "schema": GPU1_CONCURRENCY_REPORT_SCHEMA,
        "generatedAt": datetime.fromtimestamp(created, timezone.utc).isoformat(),
        "generatedAtEpochSec": created,
        "status": "fail",
        "phase": args.phase,
        "violations": [code],
        "samples": [],
    }
    _write_report(args.output, report)
    print(
        json.dumps(
            {
                "status": "fail",
                "violations": [code],
                "output": str(args.output),
            },
            ensure_ascii=False,
        )
    )
    return 2


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    baseline: dict[str, Any] | None = None
    baseline_sha256: str | None = None
    observed_preflight: dict[str, Any] | None = None
    try:
        if args.phase == "new-stt":
            baseline, baseline_sha256 = _read_report_with_sha256(
                args.baseline_report
            )
            if baseline_sha256 != args.baseline_sha256:
                raise _ValidationFailure("baseline_report_hash_mismatch")
            baseline_violations = _p0_report_violations(
                baseline,
                expected_phase="old-stt",
            )
            old_binding = baseline.get("binding")
            if baseline_violations or not isinstance(old_binding, dict):
                raise _ValidationFailure("baseline_report_invalid")
        stt_payload, audio_metadata = _load_audio(args.audio)
        if args.phase != "diagnostic":
            observed_preflight = _observe_p0_4_environment(args)
        if args.phase == "new-stt":
            candidate_binding = _validation_binding(args, observed_preflight)
            if _binding_comparison_violations(old_binding, candidate_binding):
                raise _ValidationFailure("baseline_binding_mismatch")
    except _ValidationFailure as exc:
        if args.phase != "diagnostic":
            return _terminal_preflight_failure(args, exc.code)
        raise
    except Exception:
        if args.phase != "diagnostic":
            return _terminal_preflight_failure(args, "validation_preflight_failed")
        raise

    in_progress = build_report(
        args,
        iterations=[],
        audio_metadata=audio_metadata,
        observed_environment=observed_preflight,
        baseline_sha256=baseline_sha256,
    )
    in_progress["status"] = "running"
    in_progress["violations"] = ["benchmark_in_progress"]
    _write_report(args.output, in_progress)
    for _ in range(args.warmup_iterations):
        run_iteration(args, stt_payload)
    iterations = [run_iteration(args, stt_payload) for _ in range(args.iterations)]
    report = build_report(
        args,
        iterations=iterations,
        audio_metadata=audio_metadata,
        observed_environment=observed_preflight,
        baseline_sha256=baseline_sha256,
    )
    if args.phase != "diagnostic":
        try:
            observed_postflight = _observe_p0_4_environment(args)
        except Exception:
            observed_postflight = None
            report["violations"].append("validation_postflight_failed")
        proof = report["environmentProof"]
        proof["postflightSha256"] = (
            _content_sha256(observed_postflight)
            if observed_postflight is not None
            else None
        )
        proof["stable"] = observed_postflight == observed_preflight
        if observed_postflight is not None and not proof["stable"]:
            report["violations"].append("validation_environment_drift")
        if args.phase == "new-stt":
            try:
                baseline_after, baseline_sha256_after = _read_report_with_sha256(
                    args.baseline_report
                )
            except Exception:
                baseline_after = None
                baseline_sha256_after = None
            if (
                baseline_after != baseline
                or baseline_sha256_after != baseline_sha256
                or baseline_sha256_after != args.baseline_sha256
            ):
                report["violations"].append("baseline_report_changed")
        report["violations"] = list(dict.fromkeys(report["violations"]))
        report["status"] = "pass" if not report["violations"] else "fail"
    if args.phase == "new-stt":
        comparison = compare_stt_baseline(
            baseline,
            report,
            baseline_sha256=baseline_sha256,
        )
        report["comparison"] = comparison
        report["violations"] = list(
            dict.fromkeys([*report["violations"], *comparison["violations"]])
        )
        report["status"] = "pass" if not report["violations"] else "fail"
    _write_report(args.output, report)
    print(
        json.dumps(
            {
                "status": report["status"],
                "summary": report["summary"],
                "violations": report["violations"],
                "output": str(args.output),
            },
            ensure_ascii=False,
        )
    )
    return 0 if report["status"] == "pass" else 2


if __name__ == "__main__":
    raise SystemExit(main())
