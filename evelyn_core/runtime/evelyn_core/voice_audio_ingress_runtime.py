from __future__ import annotations

import time
from dataclasses import dataclass
from typing import Any, Callable, Mapping, MutableMapping

import numpy as np


@dataclass(frozen=True)
class VoiceAudioIngressDeps:
    voice_pipeline_state: MutableMapping[str, Any]
    prepare_stt_audio: Callable[[bytes], np.ndarray]
    save_voice_debug_audio: Callable[..., Any]
    room_state_snapshot: Callable[[str], dict[str, Any]]
    session_topic_ids: Mapping[str, str]
    build_topic_id: Callable[[str], str]
    new_turn_metrics: Callable[..., MutableMapping[str, Any]]
    log_voice_stage: Callable[..., Any]
    register_drop_reason: Callable[..., Any]
    log_voice_bottleneck_summary: Callable[..., Any]
    downmix_int16_stereo_to_mono_float: Callable[[bytes], np.ndarray]
    apply_light_denoise: Callable[..., np.ndarray]
    is_transport_corrupted_audio: Callable[[dict[str, Any] | None], bool]
    build_voice_segment: Callable[..., Any]
    compute_waveform_activity_stats: Callable[..., dict[str, float]]
    estimate_voice_like_probability: Callable[..., float]
    update_room_speaker_activity: Callable[..., Any]
    increment_session_bad_audio: Callable[[str | None], int]
    is_tail_fragment_candidate: Callable[..., bool]
    is_probably_silent: Callable[..., bool]
    print_fn: Callable[..., Any]
    stt_use_raw_48k: bool
    rate: int
    channels: int
    target_rate: int
    voice_min_total_sec: float
    tail_fragment_max_raw_sec: float
    vad_enabled: bool
    voice_waveform_min_voiced_ms: float
    voice_waveform_min_run_ms: float
    voice_waveform_body_rms_min: float
    voice_waveform_body_peak_min: float
    time_fn: Callable[[], float] = time.time


@dataclass(frozen=True)
class VoiceAudioIngressResult:
    guild: Any
    guild_id: int
    speaker_name: str
    owner_user_id: int | None
    metrics: MutableMapping[str, Any]
    audio16k: np.ndarray
    audio_for_wake: np.ndarray
    stt_sampling_rate: int
    wake_sampling_rate: int
    raw_seconds: float
    duration_sec: float
    voice_segment: Any
    voiced_ms: float
    body_rms: float
    voice_like_prob: float


def _record_drop(
    *,
    deps: VoiceAudioIngressDeps,
    metrics: MutableMapping[str, Any],
    reason: str,
    session_key: str,
    room_session_key: str | None = None,
    owner_user_id: int | None = None,
    **extra: Any,
) -> None:
    payload: dict[str, Any] = {"session_key": session_key, **extra}
    if room_session_key is not None:
        payload["room_session_key"] = room_session_key
        payload["owner_user_id"] = owner_user_id
    deps.register_drop_reason(metrics, reason, **payload)


def prepare_voice_audio_ingress_from_runtime(
    member: Any,
    pcm_bytes: bytes,
    debug_meta: dict[str, Any] | None,
    *,
    session_key: str,
    room_session_key: str,
    turn_id: str,
    segment_id: int,
    ingress_during_reply: bool,
    owner_user_id_on_ingress: int | None,
    deps: VoiceAudioIngressDeps,
) -> VoiceAudioIngressResult | None:
    if member is None or bool(getattr(member, "bot", False)):
        return None

    guild = getattr(member, "guild", None)
    if guild is None:
        return None

    guild_id = int(guild.id)
    member_id = int(member.id)
    display_name = str(getattr(member, "display_name", "") or member_id)
    deps.voice_pipeline_state["last_voice_segment_at"] = deps.time_fn()
    audio16k_ingress = deps.prepare_stt_audio(pcm_bytes)
    deps.save_voice_debug_audio(
        guild_id,
        display_name,
        pcm_bytes,
        audio16k_ingress,
        final_text="[INGRESS RAW]",
        debug_meta=debug_meta,
        save_stt_audio=True,
        session_key=session_key,
        stage_label="ingress",
    )
    room_state = deps.room_state_snapshot(room_session_key)
    owner_user_id = room_state.get("owner_user_id")
    topic_id = deps.session_topic_ids.get(session_key) or deps.build_topic_id(display_name)
    metrics = deps.new_turn_metrics(
        source="voice",
        session_key=session_key,
        room_session_key=room_session_key,
        guild_id=guild_id,
        user_id=member_id,
        owner_user_id=owner_user_id,
        topic_id=topic_id,
        turn_id=turn_id,
        segment_id=segment_id,
    )
    if isinstance(debug_meta, dict):
        meta = metrics.setdefault("meta", {})
        for key in (
            "validation_session_id",
            "validation_step_id",
            "validation_attempt",
            "validation_attempt_id",
        ):
            value = debug_meta.get(key)
            if value not in (None, ""):
                meta[key] = value
        queue_wait_ms = debug_meta.get("queue_wait_ms")
        if queue_wait_ms is not None:
            try:
                parsed_queue_wait_ms = float(queue_wait_ms)
            except (TypeError, ValueError):
                pass
            else:
                meta["voice_queue_wait_ms"] = parsed_queue_wait_ms
                metrics.setdefault("marks", {})["voice_queue_wait_ms"] = parsed_queue_wait_ms
        meta["ingress_source"] = str(debug_meta.get("source") or "discord_voice")
    deps.log_voice_stage(
        metrics,
        "voice_worker_turn 시작",
        extra=f"speaker={display_name} pcm_bytes={len(pcm_bytes)} owner={owner_user_id}",
    )

    if ingress_during_reply and owner_user_id_on_ingress is not None and owner_user_id_on_ingress != member_id:
        _record_drop(
            deps=deps,
            metrics=metrics,
            reason="other_speaker_during_reply",
            session_key=session_key,
            room_session_key=room_session_key,
            owner_user_id=owner_user_id_on_ingress,
        )
        deps.log_voice_stage(metrics, "다른 화자 중복 진입 차단", extra=f"owner_user_id={owner_user_id_on_ingress}")
        deps.log_voice_bottleneck_summary(
            metrics,
            label="voice_drop",
            extra="drop=other_speaker_during_reply",
            event_name="voice_drop_summary",
        )
        return None

    if deps.stt_use_raw_48k:
        audio16k = deps.downmix_int16_stereo_to_mono_float(pcm_bytes)
        audio_for_wake = deps.apply_light_denoise(audio16k, sampling_rate=deps.rate)
        stt_sampling_rate = deps.rate
        wake_sampling_rate = deps.rate
    else:
        audio16k = deps.prepare_stt_audio(pcm_bytes)
        audio_for_wake = audio16k
        stt_sampling_rate = deps.target_rate
        wake_sampling_rate = deps.target_rate

    if audio16k.size == 0:
        _record_drop(deps=deps, metrics=metrics, reason="empty_audio", session_key=session_key)
        deps.log_voice_stage(metrics, "오디오 비어있음")
        deps.log_voice_bottleneck_summary(
            metrics,
            label="voice_drop",
            extra="drop=empty_audio",
            event_name="voice_drop_summary",
        )
        return None

    raw_seconds = len(pcm_bytes) / float(deps.rate * deps.channels * 2)
    if raw_seconds <= deps.voice_min_total_sec:
        deps.print_fn(f"[FULL STT SKIP] reason=too_short_total speaker={display_name} raw_seconds={raw_seconds:.3f}")
        deps.print_fn(f"[SHORT AUDIO IGNORE] speaker={display_name} raw_seconds={raw_seconds:.3f}")
        deps.save_voice_debug_audio(
            guild_id,
            display_name,
            pcm_bytes,
            audio16k,
            final_text="[SHORT AUDIO IGNORE]",
            debug_meta=debug_meta,
            save_stt_audio=False,
            session_key=session_key,
            stage_label="drop",
        )
        _record_drop(
            deps=deps,
            metrics=metrics,
            reason="too_short_total",
            session_key=session_key,
            raw_seconds=round(raw_seconds, 3),
        )
        deps.log_voice_stage(metrics, "전체 길이 너무 짧아서 제외", extra=f"raw_seconds={raw_seconds:.3f}")
        deps.log_voice_bottleneck_summary(
            metrics,
            label="voice_drop",
            extra="drop=too_short_total",
            event_name="voice_drop_summary",
        )
        return None

    unstable_audio = bool(debug_meta and debug_meta.get("unstable"))
    transport_corrupted = deps.is_transport_corrupted_audio(debug_meta)
    if unstable_audio:
        reasons = ",".join(str(reason) for reason in (debug_meta or {}).get("reasons", []))
        deps.print_fn(f"[UNSTABLE AUDIO] speaker={display_name} reasons={reasons}")
        deps.log_voice_stage(metrics, "불안정 음성 감지", extra=f"reasons={reasons}")

    duration_sec = len(audio16k) / float(max(1, stt_sampling_rate))
    voice_segment = deps.build_voice_segment(
        guild_id=guild_id,
        room_session_key=room_session_key,
        session_key=session_key,
        speaker_user_id=member_id,
        speaker_name=display_name,
        audio16k=audio16k,
        sampling_rate=stt_sampling_rate,
        duration_sec=duration_sec,
        segment_id=segment_id,
        owner_user_id=owner_user_id,
    )
    metrics.setdefault("meta", {})["voice_segment_contract"] = voice_segment
    waveform_stats = deps.compute_waveform_activity_stats(audio16k, sampling_rate=stt_sampling_rate)
    voiced_ms = float(waveform_stats.get("voiced_ms") or 0.0)
    longest_voiced_ms = float(waveform_stats.get("longest_voiced_ms") or 0.0)
    body_rms = float(waveform_stats.get("body_rms") or 0.0)
    voice_like_prob = deps.estimate_voice_like_probability(
        voiced_ms=voiced_ms,
        audio_sec=duration_sec,
        body_rms=body_rms,
    )
    deps.update_room_speaker_activity(
        room_session_key,
        member_id,
        voiced_ms=voiced_ms,
        raw_seconds=raw_seconds,
        rms=body_rms,
        wake_detected=False,
    )

    if transport_corrupted and raw_seconds <= max(1.4, deps.tail_fragment_max_raw_sec + 0.5):
        bad_audio_count = deps.increment_session_bad_audio(session_key)
        _record_drop(
            deps=deps,
            metrics=metrics,
            reason="transport_corrupted",
            session_key=session_key,
            room_session_key=room_session_key,
            owner_user_id=owner_user_id,
            raw_seconds=round(raw_seconds, 3),
            voiced_ms=round(voiced_ms, 1),
            longest_voiced_ms=round(longest_voiced_ms, 1),
            bad_audio_count=bad_audio_count,
        )
        deps.log_voice_stage(
            metrics,
            "transport corrupted 조기 종료",
            extra=f"raw_seconds={raw_seconds:.3f} voiced_ms={voiced_ms:.0f} longest_ms={longest_voiced_ms:.0f}",
        )
        deps.log_voice_bottleneck_summary(
            metrics,
            label="voice_drop",
            extra="drop=transport_corrupted",
            event_name="voice_drop_summary",
        )
        return None

    if deps.is_tail_fragment_candidate(
        session_key=session_key,
        raw_seconds=raw_seconds,
        voiced_ms=voiced_ms,
        longest_voiced_ms=longest_voiced_ms,
        unstable=unstable_audio,
    ):
        bad_audio_count = deps.increment_session_bad_audio(session_key)
        _record_drop(
            deps=deps,
            metrics=metrics,
            reason="tail_fragment_drop",
            session_key=session_key,
            room_session_key=room_session_key,
            owner_user_id=owner_user_id,
            raw_seconds=round(raw_seconds, 3),
            voiced_ms=round(voiced_ms, 1),
            longest_voiced_ms=round(longest_voiced_ms, 1),
            bad_audio_count=bad_audio_count,
        )
        deps.log_voice_stage(
            metrics,
            "tail fragment 조기 종료",
            extra=f"raw_seconds={raw_seconds:.3f} voiced_ms={voiced_ms:.0f} longest_ms={longest_voiced_ms:.0f}",
        )
        deps.log_voice_bottleneck_summary(
            metrics,
            label="voice_drop",
            extra="drop=tail_fragment_drop",
            event_name="voice_drop_summary",
        )
        return None

    if deps.vad_enabled and deps.is_probably_silent(audio16k, sampling_rate=stt_sampling_rate):
        peak = float(np.max(np.abs(audio16k))) if audio16k.size else 0.0
        rms = float(np.sqrt(np.mean(np.square(audio16k)))) if audio16k.size else 0.0
        body_peak = float(waveform_stats.get("body_peak") or 0.0)
        waveform_override = (not transport_corrupted) and voiced_ms >= deps.voice_waveform_min_voiced_ms and (
            longest_voiced_ms >= deps.voice_waveform_min_run_ms
            or body_rms >= deps.voice_waveform_body_rms_min
            or body_peak >= deps.voice_waveform_body_peak_min
        )
        if waveform_override:
            deps.print_fn(
                f"[VAD OVERRIDE] speaker={display_name} sec={duration_sec:.2f} voiced_ms={voiced_ms:.0f} "
                f"longest_ms={longest_voiced_ms:.0f} body_rms={body_rms:.4f} body_peak={body_peak:.4f}"
            )
            deps.log_voice_stage(
                metrics,
                "VAD override",
                extra=(
                    f"voiced_ms={voiced_ms:.0f} longest_ms={longest_voiced_ms:.0f} "
                    f"body_rms={body_rms:.4f} body_peak={body_peak:.4f}"
                ),
            )
        else:
            deps.print_fn(
                f"[FULL STT SKIP] reason=vad_ignore speaker={display_name} sampling_rate={stt_sampling_rate} "
                f"sec={duration_sec:.2f} peak={peak:.4f} rms={rms:.4f} voiced_ms={voiced_ms:.0f} "
                f"longest_ms={longest_voiced_ms:.0f} body_rms={body_rms:.4f}"
            )
            deps.print_fn(
                f"[VAD IGNORE] speaker={display_name} sampling_rate={stt_sampling_rate} "
                f"sec={duration_sec:.2f} peak={peak:.4f} rms={rms:.4f}"
            )
            deps.save_voice_debug_audio(
                guild_id,
                display_name,
                pcm_bytes,
                audio16k,
                final_text="[VAD IGNORE]",
                debug_meta=debug_meta,
                session_key=session_key,
                stage_label="drop",
            )
            bad_audio_count = deps.increment_session_bad_audio(session_key)
            _record_drop(
                deps=deps,
                metrics=metrics,
                reason="vad_ignore",
                session_key=session_key,
                room_session_key=room_session_key,
                owner_user_id=owner_user_id,
                voiced_ms=round(voiced_ms, 1),
                bad_audio_count=bad_audio_count,
            )
            deps.log_voice_stage(
                metrics,
                "VAD 무시 처리",
                extra=(
                    f"sampling_rate={stt_sampling_rate} sec={duration_sec:.2f} peak={peak:.4f} rms={rms:.4f} "
                    f"voiced_ms={voiced_ms:.0f} longest_ms={longest_voiced_ms:.0f} body_rms={body_rms:.4f}"
                ),
            )
            deps.log_voice_bottleneck_summary(
                metrics,
                label="voice_drop",
                extra="drop=vad_ignore",
                event_name="voice_drop_summary",
            )
            return None

    return VoiceAudioIngressResult(
        guild=guild,
        guild_id=guild_id,
        speaker_name=display_name,
        owner_user_id=owner_user_id,
        metrics=metrics,
        audio16k=audio16k,
        audio_for_wake=audio_for_wake,
        stt_sampling_rate=stt_sampling_rate,
        wake_sampling_rate=wake_sampling_rate,
        raw_seconds=raw_seconds,
        duration_sec=duration_sec,
        voice_segment=voice_segment,
        voiced_ms=voiced_ms,
        body_rms=body_rms,
        voice_like_prob=voice_like_prob,
    )
