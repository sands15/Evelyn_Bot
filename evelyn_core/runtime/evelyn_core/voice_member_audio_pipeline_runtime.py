from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Awaitable, Callable


@dataclass(frozen=True)
class VoiceMemberAudioPipelineDeps:
    prepare_audio_ingress: Callable[..., Any]
    build_audio_ingress_deps: Callable[[], Any]
    run_wake_probe: Callable[..., Awaitable[Any]]
    build_wake_probe_deps: Callable[[], Any]
    run_tts_interrupt_gate: Callable[..., Awaitable[Any]]
    build_tts_interrupt_gate_deps: Callable[[], Any]
    run_stt_execution: Callable[..., Awaitable[Any]]
    build_stt_execution_deps: Callable[[], Any]
    finalize_transcript: Callable[..., Any]
    build_transcript_finalize_deps: Callable[[], Any]
    run_session_gate: Callable[..., Any]
    build_session_gate_deps: Callable[[], Any]
    dispatch_voice_reply: Callable[..., Awaitable[Any]]
    build_transcript_reply_deps: Callable[[Any], Any]
    build_reply_dispatch_deps: Callable[[], Any]


async def process_member_audio_pipeline_from_runtime(
    member: Any,
    pcm_bytes: bytes,
    debug_meta: dict[str, Any] | None = None,
    *,
    session_key: str,
    room_session_key: str,
    room_key: str | None,
    person_key: str | None,
    session_memory_key: str | None,
    turn_id: str,
    segment_id: int,
    ingress_during_reply: bool = False,
    owner_user_id_on_ingress: int | None = None,
    deps: VoiceMemberAudioPipelineDeps,
) -> None:
    ingress = deps.prepare_audio_ingress(
        member,
        pcm_bytes,
        debug_meta,
        session_key=session_key,
        room_session_key=room_session_key,
        turn_id=turn_id,
        segment_id=segment_id,
        ingress_during_reply=ingress_during_reply,
        owner_user_id_on_ingress=owner_user_id_on_ingress,
        deps=deps.build_audio_ingress_deps(),
    )
    if ingress is None:
        return

    guild = ingress.guild
    guild_id = ingress.guild_id
    speaker_name = ingress.speaker_name
    owner_user_id = ingress.owner_user_id
    metrics = ingress.metrics
    audio16k = ingress.audio16k
    audio_for_wake = ingress.audio_for_wake
    stt_sampling_rate = ingress.stt_sampling_rate
    wake_sampling_rate = ingress.wake_sampling_rate
    raw_seconds = ingress.raw_seconds
    duration_sec = ingress.duration_sec
    voice_segment = ingress.voice_segment
    voiced_ms = ingress.voiced_ms
    body_rms = ingress.body_rms
    voice_like_prob = ingress.voice_like_prob

    wake = await deps.run_wake_probe(
        member=member,
        pcm_bytes=pcm_bytes,
        debug_meta=debug_meta,
        session_key=session_key,
        room_session_key=room_session_key,
        owner_user_id=owner_user_id,
        guild_id=guild_id,
        speaker_name=speaker_name,
        audio16k=audio16k,
        audio_for_wake=audio_for_wake,
        wake_sampling_rate=wake_sampling_rate,
        raw_seconds=raw_seconds,
        duration_sec=duration_sec,
        metrics=metrics,
        deps=deps.build_wake_probe_deps(),
    )
    if wake is None:
        return

    interrupt_gate = await deps.run_tts_interrupt_gate(
        member=member,
        guild_id=guild_id,
        session_key=session_key,
        room_session_key=room_session_key,
        owner_user_id=owner_user_id,
        active_speaker_user_id=wake.active_speaker_user_id,
        wake_probe=wake.wake_probe,
        wake_detected=wake.wake_detected,
        voice_like_prob=voice_like_prob,
        duration_sec=duration_sec,
        body_rms=body_rms,
        audio16k=audio16k,
        stt_sampling_rate=stt_sampling_rate,
        metrics=metrics,
        deps=deps.build_tts_interrupt_gate_deps(),
    )
    if interrupt_gate is None:
        return

    stt_execution = await deps.run_stt_execution(
        member=member,
        guild_id=guild_id,
        speaker_name=speaker_name,
        pcm_bytes=pcm_bytes,
        debug_meta=debug_meta,
        session_key=session_key,
        room_session_key=room_session_key,
        audio16k=audio16k,
        stt_sampling_rate=stt_sampling_rate,
        duration_sec=duration_sec,
        wake_probe=wake.wake_probe,
        wake_detected=wake.wake_detected,
        metrics=metrics,
        deps=deps.build_stt_execution_deps(),
    )
    if stt_execution is None:
        return

    transcript_finalization = deps.finalize_transcript(
        member=member,
        text=stt_execution.text,
        partial_text=stt_execution.partial_text,
        session_key=session_key,
        room_session_key=room_session_key,
        turn_id=turn_id,
        wake_detected=wake.wake_detected,
        wake_match_mode=wake.wake_match_mode,
        wake_alias=wake.wake_alias,
        wake_probe=wake.wake_probe,
        wake_confirm=wake.wake_confirm,
        wake_reject_reason=wake.wake_reject_reason,
        duration_sec=duration_sec,
        metrics=metrics,
        deps=deps.build_transcript_finalize_deps(),
    )

    session_gate = deps.run_session_gate(
        member=member,
        transcript_result=transcript_finalization.transcript_result,
        text=transcript_finalization.text,
        pcm_bytes=pcm_bytes,
        audio16k=audio16k,
        debug_meta=debug_meta,
        stt_meta=stt_execution.stt_meta,
        guild_id=guild_id,
        speaker_name=speaker_name,
        session_key=session_key,
        room_session_key=room_session_key,
        owner_user_id=owner_user_id,
        owner_followup_active=wake.owner_followup_active,
        wake_probe=wake.wake_probe,
        wake_confirm=wake.wake_confirm,
        wake_detected=wake.wake_detected,
        wake_alias=wake.wake_alias,
        metrics=metrics,
        deps=deps.build_session_gate_deps(),
    )
    if session_gate is None:
        return

    await deps.dispatch_voice_reply(
        guild_id=guild_id,
        transcript=transcript_finalization.transcript_result,
        voice_segment=voice_segment,
        session_key=session_key,
        room_session_key=room_session_key,
        owner_user_id=owner_user_id,
        source_turn_id=turn_id,
        segment_id=segment_id,
        voiced_ms=voiced_ms,
        raw_seconds=raw_seconds,
        rms=body_rms,
        wake_detected=wake.wake_detected,
        metrics=metrics,
        member=member,
        room_key=room_key,
        person_key=person_key,
        session_memory_key=session_memory_key,
        reply_deps=deps.build_transcript_reply_deps(guild),
        deps=deps.build_reply_dispatch_deps(),
    )
