from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Callable


@dataclass(frozen=True)
class VoiceSessionGateDeps:
    is_short_followup_candidate: Callable[..., bool]
    should_ignore_short_transcription: Callable[..., bool]
    decide_final_wake_veto: Callable[..., Any]
    extract_leading_wake_alias: Callable[[str], str | None]
    register_drop_reason: Callable[..., Any]
    save_voice_debug_audio: Callable[..., Any]
    log_voice_stage: Callable[..., Any]
    log_voice_bottleneck_summary: Callable[..., Any]
    print_fn: Callable[..., Any]


@dataclass(frozen=True)
class VoiceSessionGateResult:
    wake_alias: str | None
    short_followup_candidate: bool


def run_voice_session_gate_from_runtime(
    *,
    member: Any,
    transcript_result: Any,
    text: str,
    pcm_bytes: bytes,
    audio16k: Any,
    debug_meta: dict[str, Any] | None,
    stt_meta: dict[str, Any],
    guild_id: int,
    speaker_name: str,
    session_key: str,
    room_session_key: str,
    owner_user_id: int | None,
    owner_followup_active: bool,
    wake_probe: str,
    wake_confirm: str,
    wake_detected: bool,
    wake_alias: str | None,
    metrics: dict[str, Any],
    deps: VoiceSessionGateDeps,
) -> VoiceSessionGateResult | None:
    final_text = transcript_result.final_text
    short_followup_candidate = deps.is_short_followup_candidate(
        final_text,
        pcm_bytes,
        wake_detected=transcript_result.wake_detected,
        owner_followup_active=owner_followup_active,
    )
    if deps.should_ignore_short_transcription(
        final_text,
        pcm_bytes,
        wake_detected=transcript_result.wake_detected,
    ):
        if short_followup_candidate:
            deps.print_fn(f"[SHORT FOLLOWUP CANDIDATE] text={final_text!r}")
            metrics.setdefault("meta", {})["short_followup_candidate"] = True
            deps.save_voice_debug_audio(
                guild_id,
                speaker_name,
                pcm_bytes,
                audio16k,
                wake_probe=wake_probe,
                final_text=f"[SHORT FOLLOWUP CANDIDATE] {text}",
                debug_meta=debug_meta,
                stt_meta=stt_meta,
                session_key=session_key,
                stage_label="drop",
            )
        else:
            deps.print_fn(f"[STT IGNORE] short_noise: {final_text!r}")
            deps.save_voice_debug_audio(
                guild_id,
                speaker_name,
                pcm_bytes,
                audio16k,
                wake_probe=wake_probe,
                final_text=final_text,
                debug_meta=debug_meta,
                stt_meta=stt_meta,
                session_key=session_key,
                stage_label="drop",
            )
            deps.log_voice_stage(metrics, "짧은 STT 무시", extra=f"text={final_text!r}")
            return None

    final_wake_decision = deps.decide_final_wake_veto(
        final_text=final_text,
        owner_followup_active=owner_followup_active,
        extract_leading_wake_alias=deps.extract_leading_wake_alias,
    )
    if not final_wake_decision.accepted:
        wake_reject_reason = final_wake_decision.reject_reason or "full_text_veto"
        deps.register_drop_reason(
            metrics,
            "full_text_veto",
            session_key=session_key,
            room_session_key=room_session_key,
            owner_user_id=owner_user_id,
            wake_probe_text=wake_probe,
            wake_confirm_text=wake_confirm,
            final_text=final_text,
        )
        deps.save_voice_debug_audio(
            guild_id,
            speaker_name,
            pcm_bytes,
            audio16k,
            wake_probe=wake_probe,
            final_text=final_text,
            debug_meta=debug_meta,
            stt_meta=stt_meta,
            session_key=session_key,
            stage_label="drop",
        )
        deps.log_voice_stage(
            metrics,
            "final text veto",
            extra=f"wake_reject_reason={wake_reject_reason} text={final_text!r}",
        )
        deps.log_voice_bottleneck_summary(
            metrics,
            label="voice_drop",
            extra="drop=full_text_veto",
            event_name="voice_drop_summary",
        )
        return None

    if final_wake_decision.wake_alias is not None:
        wake_alias = final_wake_decision.wake_alias

    deps.save_voice_debug_audio(
        guild_id,
        speaker_name,
        pcm_bytes,
        audio16k,
        wake_probe=wake_probe,
        final_text=final_text,
        debug_meta=debug_meta,
        stt_meta=stt_meta,
        session_key=session_key,
        stage_label="final",
    )
    deps.print_fn(
        f"🎤 [{member.display_name}] wake_detected={transcript_result.wake_detected} "
        f"wake_match_mode={transcript_result.wake_match_mode} wake_alias={transcript_result.wake_alias!r} "
        f"wake_probe_text={transcript_result.probe_text!r} "
        f"wake_confirm_text={transcript_result.confirm_text!r} "
        f"wake_reject_reason={transcript_result.reject_reason!r} text={final_text}"
    )
    return VoiceSessionGateResult(
        wake_alias=wake_alias,
        short_followup_candidate=short_followup_candidate,
    )
