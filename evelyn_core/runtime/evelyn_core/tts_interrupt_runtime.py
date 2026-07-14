from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Awaitable, Callable


@dataclass(frozen=True)
class TtsInterruptRuntimeDeps:
    tts_playback_manager: Any
    log_turn_event: Callable[..., Any]
    speaker_verification_applies: Callable[..., bool]
    speaker_verification_result_factory: Callable[..., Any]
    speaker_verifier: Any
    speaker_verification_apply_to: str
    speaker_verification_threshold: float
    to_thread: Callable[..., Awaitable[Any]]


async def stop_active_tts_playback_from_runtime(
    guild_id: int | None,
    *,
    deps: TtsInterruptRuntimeDeps,
    reason: str = "interrupt",
) -> bool:
    stopped = await deps.tts_playback_manager.cancel_guild(guild_id)
    if not stopped:
        return False
    deps.log_turn_event("tts_interrupt", guild_id=guild_id, reason=reason)
    return True


async def verify_speaker_for_tts_interrupt_from_runtime(
    audio: Any,
    *,
    deps: TtsInterruptRuntimeDeps,
    sampling_rate: int,
    source: str | None,
    metrics: dict | None = None,
) -> Any:
    if not deps.speaker_verification_applies(source=source, apply_to=deps.speaker_verification_apply_to):
        result = deps.speaker_verification_result_factory(
            "skipped",
            threshold=deps.speaker_verification_threshold,
            detail=f"source={source or ''}",
        )
    else:
        result = await deps.to_thread(deps.speaker_verifier.verify, audio, sampling_rate=sampling_rate)
    if metrics is not None:
        metrics.setdefault("meta", {})["speaker_verification"] = result.to_dict()
    return result


def speaker_verification_allows_tts_interrupt_from_runtime(result: Any) -> bool:
    return getattr(result, "matched", None) is not False
