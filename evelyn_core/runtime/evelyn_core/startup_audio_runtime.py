from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Callable


@dataclass(frozen=True)
class OpusStartupRuntimeDeps:
    opus_is_loaded: Callable[[], bool]
    load_default_opus: Callable[[], Any]
    mark_startup_component: Callable[[str, str, str], Any]
    log: Callable[[str], Any] = print


@dataclass(frozen=True)
class SttWarmupRuntimeDeps:
    mark_startup_component: Callable[[str, str, str], Any]
    zeros: Callable[[int], Any]
    transcribe_audio16k_sync: Callable[..., str]
    target_rate: int
    wake_max_tokens: int
    log: Callable[[str], Any] = print


def ensure_opus_loaded_from_runtime(*, deps: OpusStartupRuntimeDeps) -> None:
    if deps.opus_is_loaded():
        deps.mark_startup_component("opus", "done", "already loaded")
        deps.log("[OPUS LOAD] already_loaded")
        return
    deps.mark_startup_component("opus", "running", "loading Opus")
    try:
        deps.load_default_opus()
    except Exception as exc:
        deps.mark_startup_component(
            "opus", "failed", f"opus_load_failed:{type(exc).__name__}"
        )
        raise RuntimeError("Opus library load failed") from None
    if not deps.opus_is_loaded():
        deps.mark_startup_component("opus", "failed", "library did not report loaded")
        raise RuntimeError("Opus library did not report loaded after default load")
    deps.mark_startup_component("opus", "done", "")
    deps.log("[OPUS LOAD] done")


def warmup_stt_sync_from_runtime(*, deps: SttWarmupRuntimeDeps) -> None:
    deps.mark_startup_component("stt", "running", "STT model warmup")
    deps.log("[STARTUP] stt_warmup_begin")
    silence = deps.zeros(deps.target_rate)
    try:
        deps.transcribe_audio16k_sync(
            silence,
            max_new_tokens=min(32, max(8, deps.wake_max_tokens)),
            sampling_rate=deps.target_rate,
            stage="warmup",
        )
    except Exception as exc:
        deps.mark_startup_component(
            "stt", "failed", f"stt_warmup_failed:{type(exc).__name__}"
        )
        raise RuntimeError("STT warmup failed") from None
    deps.mark_startup_component("stt", "done", "")
    deps.log("[STARTUP] stt_warmup_done")
