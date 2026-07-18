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
    load_runtime_recent_errors: Callable[[], list[str]]
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
    if not force and state.cache.get("text") and (deps.now() - cached_at) <= deps.refresh_sec:
        return str(state.cache.get("text") or "")

    if state.lock is None:
        state.lock = asyncio.Lock()

    async with state.lock:
        cached_at = float(state.cache.get("cached_at") or 0.0)
        if not force and state.cache.get("text") and (deps.now() - cached_at) <= deps.refresh_sec:
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

        recent_errors = deps.load_runtime_recent_errors()
        if recent_errors:
            status_parts.append("recent_errors=" + " | ".join(recent_errors))
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


def read_text_tail(path: Path, *, max_bytes: int = 4096) -> str:
    try:
        with path.open("rb") as handle:
            handle.seek(0, os.SEEK_END)
            size = handle.tell()
            handle.seek(max(0, size - max_bytes), os.SEEK_SET)
            return handle.read(max_bytes).decode("utf-8", errors="replace")
    except Exception:
        return ""


def compact_runtime_error(value: Any, *, max_chars: int | None = None) -> str:
    limit = max_chars if max_chars is not None else DEFAULT_MAX_ERROR_CHARS
    text = clean_text(str(value or ""))
    if not text:
        return ""
    text = re.sub(r"\s+", " ", text).strip()
    if len(text) > limit:
        return text[: max(0, limit - 3)].rstrip() + "..."
    return text


def runtime_file_age_label(path: Path) -> str:
    try:
        age_sec = max(0.0, time.time() - path.stat().st_mtime)
    except Exception:
        return ""
    if age_sec < 60:
        return f"{int(age_sec)}s ago"
    if age_sec < 3600:
        return f"{int(age_sec // 60)}m ago"
    if age_sec < 86400:
        return f"{int(age_sec // 3600)}h ago"
    return f"{int(age_sec // 86400)}d ago"


def load_runtime_recent_errors() -> list[str]:
    errors: list[str] = []
    codex_path = RUNTIME_ARTIFACTS_ROOT / "codex_gateway" / "last_request.json"
    try:
        codex_payload = json.loads(codex_path.read_text(encoding="utf-8"))
        codex_error = (
            codex_payload.get("error")
            or codex_payload.get("stderr_tail")
            or codex_payload.get("message")
        )
        codex_status = clean_text(str(codex_payload.get("status") or codex_payload.get("phase") or ""))
        if codex_error:
            codex_age = runtime_file_age_label(codex_path)
            codex_meta = ", ".join(part for part in (codex_status, codex_age) if part)
            prefix = f"codex({codex_meta})" if codex_meta else "codex"
            errors.append(f"{prefix}: {compact_runtime_error(codex_error)}")
    except Exception:
        pass

    voyager_status_path = RUNTIME_ARTIFACTS_ROOT / "voyager" / "upstream_bridge_status.json"
    try:
        status_payload = json.loads(voyager_status_path.read_text(encoding="utf-8"))
        voyager_error = (
            status_payload.get("last_error")
            or status_payload.get("last_critique")
            or status_payload.get("last_completion_reason")
        )
        if voyager_error:
            voyager_age = runtime_file_age_label(voyager_status_path)
            prefix = f"voyager({voyager_age})" if voyager_age else "voyager"
            errors.append(f"{prefix}: {compact_runtime_error(voyager_error)}")
    except Exception:
        pass

    for label, log_path in (
        ("voyager_service", RUNTIME_ARTIFACTS_ROOT / "logs" / "voyager_service_errors.log"),
        ("upstream_bridge", RUNTIME_ARTIFACTS_ROOT / "logs" / "upstream_bridge_errors.log"),
    ):
        if len(errors) >= 3:
            break
        tail = read_text_tail(log_path)
        lines = [compact_runtime_error(line) for line in tail.splitlines() if compact_runtime_error(line)]
        if lines:
            log_age = runtime_file_age_label(log_path)
            prefix = f"{label}({log_age})" if log_age else label
            errors.append(f"{prefix}: {lines[-1]}")

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
