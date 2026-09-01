from __future__ import annotations

import time
from dataclasses import dataclass
from typing import Any, Awaitable, Callable

from .main_inference_contract import (
    MainForegroundReservationRejected,
    bind_main_realtime_pre_admission,
)
from .voice_ingress_runtime import voice_listener_binding_is_current
from .voice_validation import validation_attempt_binding_is_current


MAIN_FOREGROUND_FRESHNESS_MARGIN_SEC = 0.2


def _main_foreground_monotonic() -> float:
    return time.monotonic()


@dataclass(frozen=True)
class PrecomputedSttFinal:
    final_text: str
    authoritative: bool
    call_count: int
    fallback_reason: str | None = None
    partial_text: str = ""
    committed_text: str = ""


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
    voice_ingress_epoch_is_current: Callable[[int, Any], bool]
    transcribe_completed_audio: Callable[..., Awaitable[Any]] | None = None
    reserve_main_foreground: Callable[..., Awaitable[Any]] | None = None
    cancel_main_foreground: Callable[..., Awaitable[Any]] | None = None
    authorize_archive_capture: Callable[..., Awaitable[bool]] | None = None
    archive_final_transcript: Callable[..., Awaitable[Any]] | None = None


async def _try_reserve_main_foreground(
    deps: VoiceMemberAudioPipelineDeps,
    capture_generation: int,
    metrics: dict[str, Any],
) -> Any | None:
    if deps.reserve_main_foreground is None:
        return None
    try:
        return await deps.reserve_main_foreground(
            capture_generation,
            metrics=metrics,
        )
    except MainForegroundReservationRejected:
        metrics.setdefault("meta", {})["main_foreground_reservation"] = {
            "state": "rejected",
            "failureType": "",
            "contentFree": True,
        }
        return None


def _main_foreground_reservation_is_stale(
    reservation: Any,
    issued_at: Any,
) -> bool:
    return bool(
        not isinstance(issued_at, (int, float))
        or isinstance(issued_at, bool)
        or _main_foreground_monotonic() - float(issued_at)
        >= max(
            0.0,
            reservation.ttl_ms / 1000.0
            - MAIN_FOREGROUND_FRESHNESS_MARGIN_SEC,
        )
    )


async def _reserve_main_foreground_for_turn(
    deps: VoiceMemberAudioPipelineDeps,
    reservation_state: dict[str, Any],
    capture_generation: int,
    metrics: dict[str, Any],
) -> Any | None:
    issued_at = _main_foreground_monotonic()
    reservation = await _try_reserve_main_foreground(
        deps,
        capture_generation,
        metrics,
    )
    reservation_state["reservation"] = reservation
    reservation_state["issuedAtMonotonic"] = (
        issued_at if reservation is not None else None
    )
    return reservation


async def _refresh_stale_main_foreground_for_turn(
    deps: VoiceMemberAudioPipelineDeps,
    reservation_state: dict[str, Any],
    capture_generation: int,
    metrics: dict[str, Any],
) -> None:
    previous = reservation_state.get("reservation")
    if previous is None or not _main_foreground_reservation_is_stale(
        previous,
        reservation_state.get("issuedAtMonotonic"),
    ):
        return
    if deps.cancel_main_foreground is None:
        raise RuntimeError("main_foreground_reservation_cancel_unavailable")
    await deps.cancel_main_foreground(previous, metrics=metrics)
    reservation_state["reservation"] = None
    reservation_state["issuedAtMonotonic"] = None
    refreshed = await _reserve_main_foreground_for_turn(
        deps,
        reservation_state,
        capture_generation,
        metrics,
    )
    if refreshed is not None and (
        refreshed.capture_generation != previous.capture_generation
        or refreshed.backend_epoch != previous.backend_epoch
    ):
        await deps.cancel_main_foreground(refreshed, metrics=metrics)
        reservation_state["reservation"] = None
        reservation_state["issuedAtMonotonic"] = None
        raise RuntimeError("main_foreground_reservation_refresh_mismatch")


async def _process_member_audio_pipeline_from_runtime(
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
    voice_ingress_epoch: int,
    voice_listener_binding: Any = None,
    release_ingress_worker: Callable[[], Any] | None = None,
    reservation_state: dict[str, Any],
    deps: VoiceMemberAudioPipelineDeps,
) -> None:
    def source_is_current() -> bool:
        guild_id = getattr(getattr(member, "guild", None), "id", None)
        try:
            epoch_is_current = deps.voice_ingress_epoch_is_current(
                guild_id,
                voice_ingress_epoch,
            )
        except Exception:
            epoch_is_current = False
        return bool(epoch_is_current) and voice_listener_binding_is_current(
            member,
            voice_listener_binding,
        )

    if not source_is_current() or not validation_attempt_binding_is_current(
        debug_meta,
        surface="discord",
        reject_unbound_when_active=True,
    ):
        return
    if deps.authorize_archive_capture is not None:
        voice_state = getattr(member, "voice", None)
        voice_channel = getattr(voice_state, "channel", None)
        voice_channel_id = getattr(voice_channel, "id", None)
        if voice_channel_id is None:
            return
        try:
            archive_capture_allowed = await deps.authorize_archive_capture(
                guild_id=int(getattr(getattr(member, "guild", None), "id")),
                channel_id=int(voice_channel_id),
                user_id=int(member.id),
                voice_ingress_epoch=int(voice_ingress_epoch),
            )
        except Exception:
            # The archive is an admission boundary.  When enabled, an
            # unavailable/ambiguous authorization cannot fall back to STT.
            return
        if archive_capture_allowed is not True:
            return
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
    if not source_is_current() or not validation_attempt_binding_is_current(
        debug_meta,
        surface="discord",
        reject_unbound_when_active=True,
    ):
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
    reservation_state["metrics"] = metrics

    wake_probe_deps = deps.build_wake_probe_deps()
    is_room_owner_active = getattr(
        wake_probe_deps,
        "is_room_owner_active",
        None,
    )
    is_session_active_for_user = getattr(
        wake_probe_deps,
        "is_session_active_for_user",
        None,
    )
    owner_followup_active = bool(
        callable(is_room_owner_active)
        and callable(is_session_active_for_user)
        and is_room_owner_active(room_session_key, int(member.id))
        and is_session_active_for_user(session_key, int(member.id))
    )
    if owner_followup_active and deps.reserve_main_foreground is not None:
        reservation_state["attempted"] = True
        await _reserve_main_foreground_for_turn(
            deps,
            reservation_state,
            segment_id,
            metrics,
        )

    stream_result = None
    if deps.transcribe_completed_audio is not None:
        try:
            response = await deps.transcribe_completed_audio(
                audio16k,
                sampling_rate=stt_sampling_rate,
            )
        except Exception as exc:
            metrics.setdefault("meta", {})["asr_completed_batch"] = {
                "authoritative": False,
                "callCount": 1,
                "fallbackReason": "batch_error",
                "errorType": type(exc).__name__,
            }
            return
        if not isinstance(response, dict) or not isinstance(response.get("text"), str):
            candidate = PrecomputedSttFinal(
                final_text="",
                authoritative=False,
                call_count=1,
                fallback_reason="batch_response_invalid",
            )
        else:
            final_text = response["text"].strip()
            candidate = PrecomputedSttFinal(
                final_text=final_text,
                authoritative=bool(final_text),
                call_count=1,
                fallback_reason=None if final_text else "empty_final",
            )
        metrics.setdefault("meta", {})["asr_completed_batch"] = {
            "authoritative": candidate.authoritative,
            "callCount": candidate.call_count,
            "fallbackReason": candidate.fallback_reason,
        }
        if not candidate.authoritative:
            return
        stream_result = candidate

        if not source_is_current() or not validation_attempt_binding_is_current(
            debug_meta,
            surface="discord",
            reject_unbound_when_active=True,
        ):
            return

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
        stream_result=stream_result,
        deps=wake_probe_deps,
        source_is_current=source_is_current,
    )
    if wake is None:
        return
    if not source_is_current() or not validation_attempt_binding_is_current(
        debug_meta,
        surface="discord",
        reject_unbound_when_active=True,
    ):
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
        source_is_current=source_is_current,
    )
    if interrupt_gate is None:
        return
    if not source_is_current() or not validation_attempt_binding_is_current(
        debug_meta,
        surface="discord",
        reject_unbound_when_active=True,
    ):
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
        stream_result=stream_result,
        source_is_current=source_is_current,
        deps=deps.build_stt_execution_deps(),
    )
    if stt_execution is None:
        return
    if not source_is_current() or not validation_attempt_binding_is_current(
        debug_meta,
        surface="discord",
        reject_unbound_when_active=True,
    ):
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
    if deps.archive_final_transcript is not None:
        final_text = str(
            getattr(transcript_finalization.transcript_result, "final_text", "")
            or ""
        ).strip()
        if final_text:
            voice_channel = getattr(getattr(member, "voice", None), "channel", None)
            voice_channel_id = getattr(voice_channel, "id", None)
            if voice_channel_id is None:
                return
            ended_at = time.time()
            try:
                await deps.archive_final_transcript(
                    guild_id=int(guild_id),
                    channel_id=int(voice_channel_id),
                    user_id=int(member.id),
                    owner_name=str(
                        getattr(member, "display_name", None)
                        or getattr(member, "global_name", None)
                        or getattr(member, "name", None)
                        or member.id
                    ),
                    turn_id=str(turn_id),
                    segment_id=int(segment_id),
                    started_at=max(0.0, ended_at - max(0.0, float(duration_sec))),
                    ended_at=ended_at,
                    text=final_text,
                )
            except Exception:
                # Do not run the LLM or create downstream state when the
                # canonical archive cannot durably accept the final STT.
                return

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
    if not source_is_current() or not validation_attempt_binding_is_current(
        debug_meta,
        surface="discord",
        reject_unbound_when_active=True,
    ):
        return

    if (
        reservation_state.get("reservation") is None
        and not reservation_state.get("attempted", False)
        and deps.reserve_main_foreground is not None
    ):
        reservation_state["attempted"] = True
        await _reserve_main_foreground_for_turn(
            deps,
            reservation_state,
            segment_id,
            metrics,
        )

    dispatch_kwargs = {
        "guild_id": guild_id,
        "transcript": transcript_finalization.transcript_result,
        "voice_segment": voice_segment,
        "session_key": session_key,
        "room_session_key": room_session_key,
        "owner_user_id": owner_user_id,
        "source_turn_id": turn_id,
        "segment_id": segment_id,
        "voiced_ms": voiced_ms,
        "raw_seconds": raw_seconds,
        "rms": body_rms,
        "wake_detected": wake.wake_detected,
        "metrics": metrics,
        "member": member,
        "room_key": room_key,
        "person_key": person_key,
        "session_memory_key": session_memory_key,
        "voice_ingress_epoch": voice_ingress_epoch,
        "voice_listener_binding": voice_listener_binding,
        "release_ingress_worker": release_ingress_worker,
        "reply_deps": deps.build_transcript_reply_deps(guild),
        "deps": deps.build_reply_dispatch_deps(),
    }
    async def activate_main_foreground_for_request() -> Any | None:
        await _refresh_stale_main_foreground_for_turn(
            deps,
            reservation_state,
            segment_id,
            metrics,
        )
        reservation = reservation_state.get("reservation")
        return reservation

    with bind_main_realtime_pre_admission(
        activate_main_foreground_for_request
    ):
        await deps.dispatch_voice_reply(**dispatch_kwargs)


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
    voice_ingress_epoch: int,
    voice_listener_binding: Any = None,
    release_ingress_worker: Callable[[], Any] | None = None,
    deps: VoiceMemberAudioPipelineDeps,
) -> None:
    reservation_state: dict[str, Any] = {}
    try:
        await _process_member_audio_pipeline_from_runtime(
            member,
            pcm_bytes,
            debug_meta,
            session_key=session_key,
            room_session_key=room_session_key,
            room_key=room_key,
            person_key=person_key,
            session_memory_key=session_memory_key,
            turn_id=turn_id,
            segment_id=segment_id,
            ingress_during_reply=ingress_during_reply,
            owner_user_id_on_ingress=owner_user_id_on_ingress,
            voice_ingress_epoch=voice_ingress_epoch,
            voice_listener_binding=voice_listener_binding,
            release_ingress_worker=release_ingress_worker,
            reservation_state=reservation_state,
            deps=deps,
        )
    finally:
        reservation = reservation_state.get("reservation")
        if (
            reservation is not None
            and deps.cancel_main_foreground is not None
        ):
            try:
                await deps.cancel_main_foreground(
                    reservation,
                    metrics=reservation_state.get("metrics"),
                )
            except Exception as exc:
                metrics = reservation_state.get("metrics")
                if isinstance(metrics, dict):
                    metrics.setdefault("meta", {})[
                        "main_foreground_reservation"
                    ] = {
                        "state": "cancel_failed",
                        "failureType": type(exc).__name__,
                        "contentFree": True,
                    }
