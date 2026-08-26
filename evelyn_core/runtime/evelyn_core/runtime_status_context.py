from __future__ import annotations

import asyncio
import contextlib
import json
import os
import re
import subprocess
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Awaitable, Callable
from urllib.parse import urlparse

from .config import RUNTIME_ARTIFACTS_ROOT
from .text import clean_text


DEFAULT_CONNECT_TIMEOUT_SEC = float(os.getenv("RUNTIME_STATUS_CONTEXT_CONNECT_TIMEOUT_SEC", "0.18"))
DEFAULT_MAX_ERROR_CHARS = int(os.getenv("RUNTIME_STATUS_CONTEXT_MAX_ERROR_CHARS", "160"))
RUNTIME_RECENT_ERROR_SCHEMA = "runtime.recent-error.v1"
RUNTIME_RECENT_ERROR_OWNERS = frozenset(
    {
        "codex_gateway",
        "voyager",
        "voyager_service",
        "upstream_bridge",
    }
)
RUNTIME_RECENT_ERROR_CODES = frozenset(
    {
        "codex_backend_failed",
        "codex_request_timeout",
        "codex_empty_output",
        "codex_handler_failed",
        "codex_recent_failure",
        "voyager_runtime_failed",
        "voyager_task_failed",
        "voyager_service_log_present",
        "upstream_bridge_log_present",
    }
)
RUNTIME_RECENT_ERROR_AGE_BUCKETS = frozenset(
    {
        "lt_1m",
        "lt_1h",
        "lt_1d",
        "gte_1d",
        "unknown",
    }
)
_CODEX_FAILURE_CODES = {
    "error": "codex_backend_failed",
    "timeout": "codex_request_timeout",
    "empty_output": "codex_empty_output",
    "handler_exception": "codex_handler_failed",
}
_VOYAGER_FAILURE_COMPLETION_REASONS = frozenset(
    {
        "action_generation_failed",
        "death_recovery_required",
        "max_retries_exhausted",
        "action_parse_failed",
    }
)


@dataclass
class RuntimeStatusContextState:
    cache: dict[str, Any] = field(default_factory=lambda: {"text": "", "cached_at": 0.0})
    lock: asyncio.Lock | None = None


@dataclass(frozen=True)
class RuntimeStatusContextDeps:
    enabled: bool
    refresh_sec: float
    control_page_host: str
    control_page_port: int
    llm_server_url: str
    router_llm_url: str
    summary_llm_url: str
    omnivoice_server_url: str
    minecraft_autonomy_service_port: int
    voyager_action_backend: str
    voyager_codex_gateway_port: int
    get_control_page_runtime_services: Callable[[], Awaitable[dict]]
    is_control_api_ready_from_runtime_services: Callable[[dict], bool]
    probe_runtime_tcp_service: Callable[[str, str, int], Awaitable[tuple[str, bool]]]
    load_runtime_gpu_status: Callable[[], tuple[str, bool]]
    load_runtime_recent_errors: Callable[[], list[dict[str, str]]]
    now: Callable[[], float]


async def build_runtime_status_context_from_runtime(
    *,
    deps: RuntimeStatusContextDeps,
    state: RuntimeStatusContextState,
    force: bool = False,
) -> str:
    if not deps.enabled:
        return ""

    cached_at = float(state.cache.get("cached_at") or 0.0)
    if (
        not force
        and state.cache.get("text")
        and 0.0 <= (deps.now() - cached_at) <= deps.refresh_sec
    ):
        return str(state.cache.get("text") or "")

    if state.lock is None:
        state.lock = asyncio.Lock()

    async with state.lock:
        cached_at = float(state.cache.get("cached_at") or 0.0)
        if (
            not force
            and state.cache.get("text")
            and 0.0 <= (deps.now() - cached_at) <= deps.refresh_sec
        ):
            return str(state.cache.get("text") or "")

        probes: list[tuple[str, str, int]] = [
            ("bot/control", deps.control_page_host, deps.control_page_port),
        ]
        for label, url in (
            ("main_llm", deps.llm_server_url),
            ("router_llm", deps.router_llm_url),
            ("sub_llm", deps.summary_llm_url),
            ("tts", deps.omnivoice_server_url),
        ):
            target = runtime_status_port_from_url(url)
            if target is not None:
                probes.append((label, target[0], target[1]))
        probes.append(("voyager_service", "127.0.0.1", deps.minecraft_autonomy_service_port))
        if clean_text(str(deps.voyager_action_backend or "")).lower() == "codex-gateway":
            probes.append(("codex_gateway", "127.0.0.1", deps.voyager_codex_gateway_port))

        results = await asyncio.gather(
            *(deps.probe_runtime_tcp_service(label, host, port) for label, host, port in probes),
            return_exceptions=True,
        )
        status_parts: list[str] = []
        service_down_labels: list[str] = []
        for result in results:
            if isinstance(result, tuple) and len(result) == 2:
                label, ok = result
                status_parts.append(f"{label}={'up' if ok else 'down'}")
                if not ok:
                    service_down_labels.append(label)

        service_summary = ""
        try:
            services = await deps.get_control_page_runtime_services()
            service_summary = compact_runtime_error(services.get("summary"), max_chars=120)
            bot_api_ready = deps.is_control_api_ready_from_runtime_services(services)
            if services and not bot_api_ready:
                bot_api_reason = clean_text(
                    str(services.get("botApiReason") or services.get("botApiState") or "unknown")
                )
                service_down_labels.append("bot_api" if not bot_api_reason else f"bot_api:{bot_api_reason}")
        except Exception:
            service_summary = ""
        if service_summary:
            status_parts.append(f"summary={service_summary}")

        gpu_status, gpu_near_full = await asyncio.to_thread(deps.load_runtime_gpu_status)
        if gpu_status:
            status_parts.append("current_gpu_snapshot=" + gpu_status)
        oom_signal = "yes" if gpu_near_full or service_down_labels else "no"
        oom_reason = []
        if gpu_near_full:
            oom_reason.append("gpu_near_full")
        if service_down_labels:
            oom_reason.append("service_down=" + ",".join(service_down_labels[:4]))
        status_parts.append(
            "current_oom_signal="
            + oom_signal
            + (f" ({'; '.join(oom_reason)})" if oom_reason else "")
        )

        recent_errors: list[dict[str, str]] = []
        for item in deps.load_runtime_recent_errors():
            marker = sanitize_runtime_recent_error_marker(item)
            if marker is not None:
                recent_errors.append(marker)
            if len(recent_errors) >= 3:
                break
        if recent_errors:
            status_parts.append(
                "recent_errors="
                + " | ".join(
                    render_runtime_recent_error_marker(marker)
                    for marker in recent_errors
                )
            )
            status_parts.append(
                "recent_errors_are_historical=true; do_not_claim_current_oom_from_recent_errors_without_current_oom_signal=yes"
            )
        else:
            status_parts.append("recent_errors=none")

        text = "; ".join(part for part in status_parts if part)
        state.cache["text"] = text
        state.cache["cached_at"] = deps.now()
        return text


def runtime_status_port_from_url(url: str) -> tuple[str, int] | None:
    parsed = urlparse(clean_text(str(url or "")))
    if not parsed.hostname:
        return None
    if parsed.port is not None:
        return parsed.hostname, int(parsed.port)
    if parsed.scheme == "https":
        return parsed.hostname, 443
    if parsed.scheme == "http":
        return parsed.hostname, 80
    return None


async def probe_runtime_tcp_service(
    label: str,
    host: str,
    port: int,
    *,
    timeout_sec: float = DEFAULT_CONNECT_TIMEOUT_SEC,
) -> tuple[str, bool]:
    try:
        _reader, writer = await asyncio.wait_for(
            asyncio.open_connection(host, port),
            timeout=timeout_sec,
        )
        writer.close()
        with contextlib.suppress(Exception):
            await writer.wait_closed()
        return label, True
    except Exception:
        return label, False


def compact_runtime_error(value: Any, *, max_chars: int | None = None) -> str:
    limit = max_chars if max_chars is not None else DEFAULT_MAX_ERROR_CHARS
    text = clean_text(str(value or ""))
    if not text:
        return ""
    text = re.sub(r"\s+", " ", text).strip()
    if len(text) > limit:
        return text[: max(0, limit - 3)].rstrip() + "..."
    return text


def runtime_file_age_bucket(path: Path) -> str:
    try:
        age_sec = max(0.0, time.time() - path.stat().st_mtime)
    except Exception:
        return "unknown"
    if age_sec < 60:
        return "lt_1m"
    if age_sec < 3600:
        return "lt_1h"
    if age_sec < 86400:
        return "lt_1d"
    return "gte_1d"


def build_runtime_recent_error_marker(
    *,
    owner: str,
    code: str,
    path: Path,
) -> dict[str, str] | None:
    return sanitize_runtime_recent_error_marker(
        {
            "schema": RUNTIME_RECENT_ERROR_SCHEMA,
            "owner": owner,
            "code": code,
            "ageBucket": runtime_file_age_bucket(path),
        }
    )


def sanitize_runtime_recent_error_marker(
    value: Any,
) -> dict[str, str] | None:
    if not isinstance(value, dict):
        return None
    if value.get("schema") != RUNTIME_RECENT_ERROR_SCHEMA:
        return None
    owner = clean_text(str(value.get("owner") or "")).lower()
    code = clean_text(str(value.get("code") or "")).lower()
    age_bucket = clean_text(
        str(value.get("ageBucket") or "")
    ).lower()
    if (
        owner not in RUNTIME_RECENT_ERROR_OWNERS
        or code not in RUNTIME_RECENT_ERROR_CODES
        or age_bucket not in RUNTIME_RECENT_ERROR_AGE_BUCKETS
    ):
        return None
    return {
        "schema": RUNTIME_RECENT_ERROR_SCHEMA,
        "owner": owner,
        "code": code,
        "ageBucket": age_bucket,
    }


def render_runtime_recent_error_marker(
    marker: dict[str, str],
) -> str:
    safe = sanitize_runtime_recent_error_marker(marker)
    if safe is None:
        return ""
    return (
        f"owner={safe['owner']},"
        f"code={safe['code']},"
        f"age={safe['ageBucket']}"
    )


def load_runtime_recent_errors() -> list[dict[str, str]]:
    errors: list[dict[str, str]] = []
    codex_path = RUNTIME_ARTIFACTS_ROOT / "codex_gateway" / "last_request.json"
    try:
        codex_payload = json.loads(codex_path.read_text(encoding="utf-8"))
        has_legacy_error = bool(
            codex_payload.get("error")
            or codex_payload.get("stderr_tail")
            or codex_payload.get("message")
        )
        codex_phase = clean_text(
            str(
                codex_payload.get("status")
                or codex_payload.get("phase")
                or ""
            )
        ).lower()
        codex_code = _CODEX_FAILURE_CODES.get(codex_phase)
        if codex_code is not None or has_legacy_error:
            marker = build_runtime_recent_error_marker(
                owner="codex_gateway",
                code=codex_code or "codex_recent_failure",
                path=codex_path,
            )
            if marker is not None:
                errors.append(marker)
    except Exception:
        pass

    voyager_status_path = RUNTIME_ARTIFACTS_ROOT / "voyager" / "upstream_bridge_status.json"
    try:
        status_payload = json.loads(voyager_status_path.read_text(encoding="utf-8"))
        completion_reason = clean_text(
            str(status_payload.get("last_completion_reason") or "")
        ).lower()
        voyager_code = ""
        if status_payload.get("last_error"):
            voyager_code = "voyager_runtime_failed"
        elif completion_reason in _VOYAGER_FAILURE_COMPLETION_REASONS:
            voyager_code = "voyager_task_failed"
        if voyager_code:
            marker = build_runtime_recent_error_marker(
                owner="voyager",
                code=voyager_code,
                path=voyager_status_path,
            )
            if marker is not None:
                errors.append(marker)
    except Exception:
        pass

    for owner, code, log_path in (
        (
            "voyager_service",
            "voyager_service_log_present",
            RUNTIME_ARTIFACTS_ROOT
            / "logs"
            / "voyager_service_errors.log",
        ),
        (
            "upstream_bridge",
            "upstream_bridge_log_present",
            RUNTIME_ARTIFACTS_ROOT
            / "logs"
            / "upstream_bridge_errors.log",
        ),
    ):
        if len(errors) >= 3:
            break
        try:
            has_error_log = log_path.is_file() and log_path.stat().st_size > 0
        except OSError:
            has_error_log = False
        if has_error_log:
            marker = build_runtime_recent_error_marker(
                owner=owner,
                code=code,
                path=log_path,
            )
            if marker is not None:
                errors.append(marker)

    return errors[:3]


def load_runtime_gpu_status() -> tuple[str, bool]:
    try:
        completed = subprocess.run(
            [
                "nvidia-smi",
                "--query-gpu=index,name,memory.used,memory.total",
                "--format=csv,noheader,nounits",
            ],
            capture_output=True,
            text=True,
            timeout=5,
            check=False,
        )
    except Exception:
        return "", False
    if completed.returncode != 0:
        return "", False

    rows: list[str] = []
    near_full = False
    for line in completed.stdout.splitlines():
        parts = [part.strip() for part in line.split(",")]
        if len(parts) < 4:
            continue
        index, name, used_text, total_text = parts[:4]
        try:
            used_mb = int(float(used_text))
            total_mb = int(float(total_text))
        except Exception:
            continue
        if total_mb <= 0:
            continue
        pct = (used_mb / total_mb) * 100.0
        free_mb = max(0, total_mb - used_mb)
        if pct >= 95.0 or free_mb < 512:
            near_full = True
        rows.append(f"gpu{index} {name} used={used_mb}/{total_mb}MB ({pct:.1f}%), free={free_mb}MB")
    return " | ".join(rows), near_full


def answer_gpu_runtime_status_query(user_text: str) -> str:
    text = clean_text(user_text).lower()
    if not any(marker in text for marker in ("vram", "oom", "gpu", "브이램")):
        return ""
    gpu_status, gpu_near_full = load_runtime_gpu_status()
    if not gpu_status:
        return "지금 GPU VRAM 상태를 읽지 못했어. `nvidia-smi` 결과를 확인해야 해."
    if gpu_near_full:
        return f"현재 GPU 여유가 매우 낮아서 OOM 위험이 있어: {gpu_status}"
    return f"현재 OOM 신호는 없어. GPU 상태: {gpu_status}"
