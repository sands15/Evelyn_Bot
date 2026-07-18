from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable


@dataclass(frozen=True)
class CachedTtsRuntimeDeps:
    resolve_cached_tts_audio_path: Callable[..., Path | None]
    cached_audio_enabled: bool
    canned_wake_reply_text: str
    canned_wake_reply_audio: str
    project_root: Path
    cached_wave_audio_source_factory: Callable[..., Any]
    tts_source_playback_request_factory: Callable[..., Any]
    tts_playback_manager: Any
    clean_text: Callable[[str], str]
    log_turn_event: Callable[..., Any]
    log_voice_latency: Callable[[dict | None, str, str], Any]


def cached_audio_path_for_answer_from_runtime(answer: str, *, deps: CachedTtsRuntimeDeps) -> Path | None:
    return deps.resolve_cached_tts_audio_path(
        answer,
        enabled=deps.cached_audio_enabled,
        canned_text=deps.canned_wake_reply_text,
        canned_audio_path=deps.canned_wake_reply_audio,
        project_root=deps.project_root,
    )


async def play_cached_answer_audio_from_runtime(
    vc: Any,
    answer: str,
    *,
    deps: CachedTtsRuntimeDeps,
    turn_id: str | None = None,
    session_key: str | None = None,
    metrics: dict | None = None,
) -> bool:
    path = cached_audio_path_for_answer_from_runtime(answer, deps=deps)
    if path is None:
        return False

    guild_id = getattr(getattr(vc, "guild", None), "id", None)
    source = deps.cached_wave_audio_source_factory(
        path,
        on_first_packet_sent=lambda: deps.log_turn_event(
            "first_packet_sent",
            turn_id=turn_id,
            chunk_index=1,
            session_key=session_key,
            source_type="CachedWaveAudioSource",
        ) or deps.log_voice_latency(metrics, "first_packet_sent_logged", "캐시 오디오 첫 패킷 송신 시간"),
    )
    deps.log_turn_event(
        "cached_audio_playback_selected",
        turn_id=turn_id,
        session_key=session_key,
        path=str(path),
        answer=deps.clean_text(answer),
    )
    await deps.tts_playback_manager.play_source_once(
        deps.tts_source_playback_request_factory(
            vc,
            source,
            guild_id=guild_id,
            turn_id=turn_id,
            session_key=session_key,
            metrics=metrics,
            trace_payload={
                "cached_audio_path": str(path),
            },
            cleanup_source=True,
        )
    )
    return True
