from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Awaitable, Callable

from .discord_session_policy import TtsInterruptMeta
from .voice_validation import validation_attempt_binding_is_current


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


@dataclass(frozen=True)
class VoiceTtsInterruptGateDeps:
    should_interrupt_tts: Callable[[TtsInterruptMeta], bool]
    local_tts_playback_manager: Any
    tts_playback_manager: Any
    verify_speaker_for_tts_interrupt: Callable[..., Awaitable[Any]]
    speaker_verification_allows_tts_interrupt: Callable[[Any], bool]
    stop_active_tts_playback: Callable[..., Awaitable[bool]]
    register_drop_reason: Callable[..., Any]
    log_voice_stage: Callable[..., Any]
    log_voice_bottleneck_summary: Callable[..., Any]
    start_voice_barge_in_continuity_probe: Callable[..., Any]
    log_turn_event: Callable[..., Any]
    sleep: Callable[[float], Awaitable[Any]]
    monotonic: Callable[[], float]
    local_only_mode: bool
    post_tts_ignore_sec: float
    tts_interrupt_debounce_sec: float
    voice_waveform_body_rms_min: float


@dataclass(frozen=True)
class VoiceTtsInterruptGateResult:
    qualified_tts_interrupt: bool
    local_tts_interrupted: bool = False
    discord_tts_interrupted: bool = False


async def stop_active_tts_playback_from_runtime(
    guild_id: int | None,
    *,
    deps: TtsInterruptRuntimeDeps,
    reason: str = "interrupt",
) -> bool:
    source_context = deps.tts_playback_manager.source_context(guild_id) or {}
    stopped = await deps.tts_playback_manager.cancel_guild(
        guild_id,
        reason=reason,
    )
    if not stopped:
        return False
    deps.log_turn_event(
        "tts_interrupt",
        guild_id=guild_id,
        reason=reason,
        qualified=reason == "qualified_user_audio",
        source_turn_id=source_context.get("source_turn_id"),
        source_session_key=source_context.get("source_session_key"),
        output_mode=source_context.get("output_mode") or "discord_voice",
        validation_session_id=source_context.get("validation_session_id"),
        validation_step_id=source_context.get("validation_step_id"),
        validation_attempt_id=source_context.get("validation_attempt_id"),
    )
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
    return (
        getattr(result, "status", None) == "skipped"
        or getattr(result, "matched", None) is True
    )


def _register_gate_drop(
    deps: VoiceTtsInterruptGateDeps,
    metrics: dict[str, Any],
    reason: str,
    *,
    session_key: str,
    room_session_key: str,
    owner_user_id: int | None,
    wake_probe: str,
    wake_detected: bool,
    **extra: Any,
) -> None:
    deps.register_drop_reason(
        metrics,
        reason,
        session_key=session_key,
        room_session_key=room_session_key,
        owner_user_id=owner_user_id,
        wake_probe_text=wake_probe,
        wake_detected=wake_detected,
        **extra,
    )


def _log_gate_drop_summary(
    deps: VoiceTtsInterruptGateDeps,
    metrics: dict[str, Any],
    reason: str,
) -> None:
    deps.log_voice_bottleneck_summary(
        metrics,
        label="voice_drop",
        extra=f"drop={reason}",
        event_name="voice_drop_summary",
    )


async def run_voice_tts_interrupt_gate_from_runtime(
    *,
    member: Any,
    guild_id: int,
    session_key: str,
    room_session_key: str,
    owner_user_id: int | None,
    active_speaker_user_id: int | None,
    wake_probe: str,
    wake_detected: bool,
    voice_like_prob: float,
    duration_sec: float,
    body_rms: float,
    audio16k: Any,
    stt_sampling_rate: int,
    metrics: dict[str, Any],
    deps: VoiceTtsInterruptGateDeps,
    source_is_current: Callable[[], bool] | None = None,
) -> VoiceTtsInterruptGateResult | None:
    def interrupt_source_is_current() -> bool:
        if source_is_current is None:
            return True
        try:
            return bool(source_is_current())
        except Exception:
            return False

    validation_meta = metrics.get("meta") if isinstance(metrics, dict) else None
    if not interrupt_source_is_current() or not validation_attempt_binding_is_current(
        validation_meta,
        surface="discord",
        reject_unbound_when_active=True,
    ):
        return None
    display_name = getattr(member, "display_name", None)
    interrupt_meta = TtsInterruptMeta(
        active_speaker_match=active_speaker_user_id == member.id,
        wake_detected=wake_detected,
        vad_prob=voice_like_prob,
        audio_sec=duration_sec,
        rms_ok=body_rms >= deps.voice_waveform_body_rms_min,
        voice_like=voice_like_prob >= 0.45,
    )
    qualified_tts_interrupt = deps.should_interrupt_tts(interrupt_meta)
    local_tts_active = bool(
        deps.local_only_mode and deps.local_tts_playback_manager.snapshot().get("active")
    )
    tts_suppression = deps.tts_playback_manager.input_suppression_reason(
        guild_id=guild_id,
        post_tts_ignore_sec=deps.post_tts_ignore_sec,
    )

    if qualified_tts_interrupt and (local_tts_active or tts_suppression == "bot_is_speaking"):
        speaker_verification = await deps.verify_speaker_for_tts_interrupt(
            audio16k,
            sampling_rate=stt_sampling_rate,
            source=str(metrics.setdefault("meta", {}).get("ingress_source") or "discord_voice"),
            metrics=metrics,
        )
        if not interrupt_source_is_current() or not validation_attempt_binding_is_current(
            validation_meta,
            surface="discord",
            reject_unbound_when_active=True,
        ):
            return None
        if not deps.speaker_verification_allows_tts_interrupt(speaker_verification):
            _register_gate_drop(
                deps,
                metrics,
                "speaker_verification_rejected",
                session_key=session_key,
                room_session_key=room_session_key,
                owner_user_id=owner_user_id,
                wake_probe=wake_probe,
                wake_detected=wake_detected,
                speaker_verification=speaker_verification.to_dict(),
            )
            deps.log_voice_stage(
                metrics,
                "speaker verification rejected TTS interrupt",
                extra=f"speaker={display_name} score={speaker_verification.score}",
            )
            _log_gate_drop_summary(deps, metrics, "speaker_verification_rejected")
            return None

    local_tts_interrupted = False
    if local_tts_active:
        if qualified_tts_interrupt:
            stopped_context = await deps.local_tts_playback_manager.request_stop_and_wait(
                reason="qualified_user_audio"
            )
            if not interrupt_source_is_current():
                return None
            local_tts_interrupted = bool(stopped_context)
            metrics.setdefault("meta", {})["local_tts_interrupted_by_user_audio"] = bool(
                stopped_context
            )
            if stopped_context:
                deps.start_voice_barge_in_continuity_probe(metrics, source="local_tts")
                metrics.setdefault("meta", {})["tts_interrupted_at"] = deps.monotonic()
                deps.log_turn_event(
                    "tts_interrupt",
                    guild_id=guild_id,
                    reason="qualified_user_audio",
                    qualified=True,
                    output_mode=getattr(stopped_context, "output_mode", "local_speaker"),
                    source_turn_id=getattr(stopped_context, "source_turn_id", None),
                    source_session_key=getattr(stopped_context, "source_session_key", None),
                    validation_session_id=getattr(
                        stopped_context,
                        "validation_session_id",
                        None,
                    ),
                    validation_step_id=getattr(
                        stopped_context,
                        "validation_step_id",
                        None,
                    ),
                    validation_attempt_id=getattr(
                        stopped_context,
                        "validation_attempt_id",
                        None,
                    ),
                )
                deps.log_voice_stage(
                    metrics,
                    "로컬 TTS 사용자 발화로 중단",
                    extra=f"speaker={display_name} wake_detected={wake_detected}",
                )
        else:
            reason = "local_tts_active_input_suppressed"
            _register_gate_drop(
                deps,
                metrics,
                reason,
                session_key=session_key,
                room_session_key=room_session_key,
                owner_user_id=owner_user_id,
                wake_probe=wake_probe,
                wake_detected=wake_detected,
            )
            deps.log_voice_stage(
                metrics,
                "로컬 TTS 재생 중 약한 입력 무시",
                extra=f"speaker={display_name} wake_detected={wake_detected}",
            )
            _log_gate_drop_summary(deps, metrics, reason)
            return None

    discord_tts_interrupted = False
    if tts_suppression == "bot_is_speaking" and qualified_tts_interrupt:
        await deps.sleep(deps.tts_interrupt_debounce_sec)
        if not interrupt_source_is_current() or not validation_attempt_binding_is_current(
            validation_meta,
            surface="discord",
            reject_unbound_when_active=True,
        ):
            return None
        tts_suppression = deps.tts_playback_manager.input_suppression_reason(
            guild_id=guild_id,
            post_tts_ignore_sec=deps.post_tts_ignore_sec,
        )
        if tts_suppression == "bot_is_speaking":
            stopped = await deps.stop_active_tts_playback(guild_id, reason="qualified_user_audio")
            if not interrupt_source_is_current():
                return None
            discord_tts_interrupted = bool(stopped)
            if stopped:
                deps.start_voice_barge_in_continuity_probe(metrics, source="discord_voice")
                metrics.setdefault("meta", {}).update(
                    {
                        "tts_interrupted_by_user_audio": True,
                        "tts_interrupted_at": deps.monotonic(),
                    }
                )
            tts_suppression = None
        elif tts_suppression is not None:
            _register_gate_drop(
                deps,
                metrics,
                tts_suppression,
                session_key=session_key,
                room_session_key=room_session_key,
                owner_user_id=owner_user_id,
                wake_probe=wake_probe,
                wake_detected=wake_detected,
            )
            deps.log_voice_stage(
                metrics,
                "디바운스 후 TTS 직후 입력 무시",
                extra=f"speaker={display_name} wake_detected={wake_detected}",
            )
            _log_gate_drop_summary(deps, metrics, tts_suppression)
            return None
    elif tts_suppression is not None:
        _register_gate_drop(
            deps,
            metrics,
            tts_suppression,
            session_key=session_key,
            room_session_key=room_session_key,
            owner_user_id=owner_user_id,
            wake_probe=wake_probe,
            wake_detected=wake_detected,
        )
        stage_label = "봇 재생 중 약한 입력 무시" if tts_suppression == "bot_is_speaking" else "TTS 직후 입력 무시"
        deps.log_voice_stage(
            metrics,
            stage_label,
            extra=f"speaker={display_name} wake_detected={wake_detected}",
        )
        _log_gate_drop_summary(deps, metrics, tts_suppression)
        return None

    return VoiceTtsInterruptGateResult(
        qualified_tts_interrupt=qualified_tts_interrupt,
        local_tts_interrupted=local_tts_interrupted,
        discord_tts_interrupted=discord_tts_interrupted,
    )
