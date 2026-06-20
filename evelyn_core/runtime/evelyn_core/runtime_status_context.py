from __future__ import annotations

import asyncio
import contextlib
import json
import os
import re
import subprocess
import time
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

from .config import RUNTIME_ARTIFACTS_ROOT
from .text import clean_text


DEFAULT_CONNECT_TIMEOUT_SEC = float(os.getenv("RUNTIME_STATUS_CONTEXT_CONNECT_TIMEOUT_SEC", "0.18"))
DEFAULT_MAX_ERROR_CHARS = int(os.getenv("RUNTIME_STATUS_CONTEXT_MAX_ERROR_CHARS", "160"))


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
