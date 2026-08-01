from __future__ import annotations

import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Awaitable, Callable

from .memory_exposure import (
    current_memory_exposure_position,
    memory_exposure_guard,
)


@dataclass(frozen=True)
class LocalControlTtsRuntimeDeps:
    local_only_mode: bool
    local_tts_enabled: Callable[[], bool]
    speak_answer_local: Callable[..., Awaitable[bool]]
    create_turn_scoped_task: Callable[..., Any]
    log_voice_bottleneck_summary: Callable[..., Any]
    memory_index_dir: Path
    monotonic: Callable[[], float] = time.monotonic


def build_local_control_tts_runtime_deps(
    *,
    local_only_mode: bool,
    local_tts_enabled: Callable[[], bool],
    speak_answer_local: Callable[..., Awaitable[bool]],
    create_turn_scoped_task: Callable[..., Any],
    log_voice_bottleneck_summary: Callable[..., Any],
    memory_index_dir: Path,
    monotonic: Callable[[], float] = time.monotonic,
) -> LocalControlTtsRuntimeDeps:
    return LocalControlTtsRuntimeDeps(
        local_only_mode=local_only_mode,
        local_tts_enabled=local_tts_enabled,
        speak_answer_local=speak_answer_local,
        create_turn_scoped_task=create_turn_scoped_task,
        log_voice_bottleneck_summary=log_voice_bottleneck_summary,
        memory_index_dir=memory_index_dir,
        monotonic=monotonic,
    )


def schedule_local_control_tts_from_runtime(
    answer: str,
    *,
    turn_id: str | None = None,
    session_key: str | None = None,
    turn_scope: Any = None,
    deps: LocalControlTtsRuntimeDeps,
) -> Any | None:
    if not deps.local_only_mode or not deps.local_tts_enabled():
        return None
    metrics: dict[str, Any] = {
        "started_at": deps.monotonic(),
        "meta": {
            "turn_id": turn_id,
            "source": "control_page",
            "session_key": session_key,
            "turn_type": "control_page_local_tts",
            "selected_path": "local_speaker",
            "needs_tts": True,
        },
        "marks": {},
    }
    exposure_position = current_memory_exposure_position()

    async def _runner() -> None:
        ok = False
        try:
            with memory_exposure_guard(
                expected_position=exposure_position,
                required=(exposure_position is not None),
                index_dir=deps.memory_index_dir,
            ):
                ok = await deps.speak_answer_local(
                    answer,
                    turn_id=turn_id,
                    session_key=session_key,
                    turn_scope=turn_scope,
                    metrics=metrics,
                )
        finally:
            deps.log_voice_bottleneck_summary(
                metrics,
                label="local_tts",
                extra=f"control_page=true playback={'ok' if ok else 'skipped_or_failed'}",
                event_name="local_tts_summary",
            )

    return deps.create_turn_scoped_task(_runner(), turn_scope=turn_scope)
