from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Callable


@dataclass(frozen=True)
class RuntimeModeResolver:
    tts_backlog_get: Callable[[], int]
    inflight_llm_requests_get: Callable[[], int]

    def __call__(self, metrics: dict | None) -> str:
        return compute_runtime_mode_from_state(
            metrics,
            tts_backlog=self.tts_backlog_get(),
            inflight_llm_requests=self.inflight_llm_requests_get(),
        )


def compute_runtime_mode_from_state(
    metrics: dict | None,
    *,
    tts_backlog: int,
    inflight_llm_requests: int,
) -> str:
    meta = (metrics or {}).get("meta") or {}
    marks = (metrics or {}).get("marks") or {}
    voice_queue_wait_ms = float(meta.get("voice_queue_wait_ms") or marks.get("voice_queue_wait_ms") or 0.0)
    if tts_backlog >= 2:
        return "realtime"
    if voice_queue_wait_ms >= 250.0:
        return "realtime"
    if inflight_llm_requests >= 2:
        return "congested"
    return "normal"


def apply_runtime_mode_policy(mode: str, opts: dict[str, Any] | None = None) -> dict[str, Any]:
    merged = dict(opts or {})
    merged.setdefault("skip_router", False)
    merged.setdefault("skip_search_followup", False)
    merged.setdefault("memory_update_mode", "normal")
    merged.setdefault("tts_chunk_min_chars", 12)
    if mode == "realtime":
        merged["skip_router"] = True
        merged["skip_search_followup"] = True
        merged["memory_update_mode"] = "defer"
        merged["tts_chunk_min_chars"] = 18
    elif mode == "congested":
        merged["skip_router"] = False
        merged["memory_update_mode"] = "batch"
    return merged
