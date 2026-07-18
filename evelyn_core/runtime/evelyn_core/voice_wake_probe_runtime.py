from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Awaitable, Callable, MutableMapping


HARD_WAKE_DROP_REASONS = frozenset(
    {
        "unstable_audio",
        "gibberish_probe",
        "probe_miss",
        "confirm_miss",
        "wake_probe_low_signal",
        "full_text_veto",
        "transport_corrupted",
    }
)


@dataclass(frozen=True)
class VoiceWakeProbeDeps:
    is_room_owner_active: Callable[[str, int], bool]
    is_session_active_for_user: Callable[[str, int], bool]
    pick_active_speaker: Callable[[str], int | None]
    log_voice_stage: Callable[..., Any]
    run_blocking_stt_task: Callable[..., Awaitable[Any]]
    detect_wake_word_sync: Callable[..., dict[str, Any]]
    interpret_wake_probe_result: Callable[..., Any]
    clean_text: Callable[[str], str]
    apply_stt_post_corrections: Callable[..., str]
    should_require_confirm_exact_for_wake: Callable[[dict[str, Any] | None], bool]
    apply_strict_wake_confirm_policy: Callable[..., Any]
    apply_fuzzy_wake_near_miss: Callable[..., Any]
    fuzzy_leading_wake_alias: Callable[[str], str | None]
    register_drop_reason: Callable[..., Any]
    log_voice_bottleneck_summary: Callable[..., Any]
    is_likely_environment_noise: Callable[..., bool]
    looks_like_brief_filler_text: Callable[[str], bool]
    looks_like_repetitive_noise_text: Callable[[str], bool]
    compute_voice_band_metrics: Callable[..., tuple[float, float, float]]
    save_voice_debug_audio: Callable[..., Any]
    increment_session_bad_audio: Callable[[str | None], int]
    should_skip_full_stt_after_wake_probe: Callable[..., bool]
    print_fn: Callable[..., Any]
    wake_stt_timeout_sec: float
    voice_no_wake_max_continue_sec: float


@dataclass(frozen=True)
class VoiceWakeProbeResult:
    owner_followup_active: bool
    active_speaker_user_id: int | None
    wake_probe: str
    wake_confirm: str
    wake_detected: bool
    wake_match_mode: str
    wake_alias: str | None
    wake_reject_reason: str | None


def _register_wake_drop(
    deps: VoiceWakeProbeDeps,
    metrics: MutableMapping[str, Any],
    reason: str,
    *,
    session_key: str,
    room_session_key: str,
    owner_user_id: int | None,
    **extra: Any,
) -> None:
    deps.register_drop_reason(
        metrics,
        reason,
        session_key=session_key,
        room_session_key=room_session_key,
        owner_user_id=owner_user_id,
        **extra,
    )


def _log_wake_drop_summary(
    deps: VoiceWakeProbeDeps,
    metrics: MutableMapping[str, Any],
    reason: str,
) -> None:
    deps.log_voice_bottleneck_summary(
        metrics,
        label="voice_drop",
        extra=f"drop={reason}",
        event_name="voice_drop_summary",
    )


async def run_voice_wake_probe_from_runtime(
    *,
    member: Any,
    pcm_bytes: bytes,
    debug_meta: dict[str, Any] | None,
    session_key: str,
    room_session_key: str,
    owner_user_id: int | None,
    guild_id: int,
    speaker_name: str,
    audio16k: Any,
    audio_for_wake: Any,
    wake_sampling_rate: int,
    raw_seconds: float,
    duration_sec: float,
    metrics: MutableMapping[str, Any],
    deps: VoiceWakeProbeDeps,
) -> VoiceWakeProbeResult | None:
    member_id = int(member.id)
    display_name = getattr(member, "display_name", None)
    owner_followup_active = deps.is_room_owner_active(room_session_key, member_id) and deps.is_session_active_for_user(
        session_key,
        member_id,
    )
    active_speaker_user_id = deps.pick_active_speaker(room_session_key)
    wake_probe = ""
    wake_confirm = ""
    wake_detected = False
    wake_match_mode = "owner_followup_active" if owner_followup_active else "rejected"
    wake_alias = None
    wake_reject_reason = None

    if owner_followup_active:
        deps.log_voice_stage(
            metrics,
            "active owner follow-up, wake probe 생략",
            extra=f"owner_user_id={member_id}",
            key="wake_done",
        )
    else:
        deps.log_voice_stage(
            metrics,
            "웨이크 프로브 시작",
            extra=f"samples={audio_for_wake.size} sampling_rate={wake_sampling_rate}",
        )
        try:
            wake_result = await deps.run_blocking_stt_task(
                lambda: deps.detect_wake_word_sync(audio_for_wake, sampling_rate=wake_sampling_rate),
                stage="wake",
                timeout_sec=max(5.0, deps.wake_stt_timeout_sec),
                metrics=metrics,
            )
        except Exception as exc:
            deps.print_fn(f"[WAKE STT] {exc}")
            _register_wake_drop(
                deps,
                metrics,
                "wake_probe_error",
                session_key=session_key,
                room_session_key=room_session_key,
                owner_user_id=owner_user_id,
                error=repr(exc),
            )
            deps.log_voice_stage(metrics, "웨이크 프로브 실패", extra=repr(exc))
            _log_wake_drop_summary(deps, metrics, "wake_probe_error")
            return None

        wake_interpretation = deps.interpret_wake_probe_result(
            wake_result,
            clean_text=deps.clean_text,
            apply_post_corrections=deps.apply_stt_post_corrections,
        )
        wake_probe = wake_interpretation.probe_text
        wake_confirm = wake_interpretation.confirm_text
        wake_detected = wake_interpretation.wake_detected
        wake_match_mode = wake_interpretation.wake_match_mode
        wake_alias = wake_interpretation.wake_alias
        wake_reject_reason = wake_interpretation.wake_reject_reason
        deps.print_fn(
            f"[STT RESULT][wake] probe={wake_probe!r} confirm={wake_confirm!r} detected={wake_detected} "
            f"mode={wake_match_mode} alias={wake_alias!r} reject={wake_reject_reason!r}"
        )

        wake_interpretation = deps.apply_strict_wake_confirm_policy(
            wake_interpretation,
            strict_confirm_required=deps.should_require_confirm_exact_for_wake(debug_meta),
        )
        wake_detected = wake_interpretation.wake_detected
        wake_match_mode = wake_interpretation.wake_match_mode
        wake_alias = wake_interpretation.wake_alias
        wake_reject_reason = wake_interpretation.wake_reject_reason

        deps.log_voice_stage(
            metrics,
            "웨이크 프로브 완료",
            extra=(
                f"wake_detected={wake_detected} wake_match_mode={wake_match_mode} wake_alias={wake_alias!r} "
                f"wake_probe_text={wake_probe!r} wake_confirm_text={wake_confirm!r} "
                f"wake_reject_reason={wake_reject_reason!r}"
            ),
            key="wake_done",
        )

        wake_interpretation = deps.apply_fuzzy_wake_near_miss(
            wake_interpretation,
            fuzzy_leading_wake_alias=deps.fuzzy_leading_wake_alias,
        )
        wake_detected = wake_interpretation.wake_detected
        wake_match_mode = wake_interpretation.wake_match_mode
        wake_alias = wake_interpretation.wake_alias
        wake_reject_reason = wake_interpretation.wake_reject_reason
        if wake_interpretation.near_miss:
            deps.log_voice_stage(
                metrics,
                "웨이크 근접오타 완화",
                extra=f"probe={wake_probe!r} confirm={wake_confirm!r} alias={wake_alias!r}",
            )
        if not wake_detected:
            reject_reason = wake_reject_reason or "confirm_miss"
            if reject_reason in HARD_WAKE_DROP_REASONS:
                _register_wake_drop(
                    deps,
                    metrics,
                    reject_reason,
                    session_key=session_key,
                    room_session_key=room_session_key,
                    owner_user_id=owner_user_id,
                    wake_probe_text=wake_probe,
                    wake_confirm_text=wake_confirm,
                    wake_match_mode=wake_match_mode,
                    wake_alias=wake_alias,
                )
                deps.log_voice_stage(
                    metrics,
                    "웨이크 거부",
                    extra=f"wake_reject_reason={reject_reason} wake_match_mode={wake_match_mode}",
                )
                _log_wake_drop_summary(deps, metrics, reject_reason)
                return None

        env_noise_candidate = deps.is_likely_environment_noise(audio_for_wake, sampling_rate=wake_sampling_rate)
        filler_candidate = deps.looks_like_brief_filler_text(wake_probe)
        repetitive_noise_candidate = deps.looks_like_repetitive_noise_text(wake_probe)

        if env_noise_candidate:
            band_ratio, flatness, rms = deps.compute_voice_band_metrics(
                audio_for_wake,
                sampling_rate=wake_sampling_rate,
            )
            if not wake_detected and raw_seconds <= deps.voice_no_wake_max_continue_sec:
                deps.print_fn(f"[FULL STT SKIP] reason=env_ignore speaker={display_name} probe={wake_probe!r}")
                deps.print_fn(
                    f"[ENV IGNORE] speaker={display_name} band_ratio={band_ratio:.3f} flatness={flatness:.3f} "
                    f"rms={rms:.4f} probe={wake_probe!r}"
                )
                deps.save_voice_debug_audio(
                    guild_id,
                    speaker_name,
                    pcm_bytes,
                    audio16k,
                    wake_probe=wake_probe,
                    final_text="[ENV IGNORE]",
                    debug_meta=debug_meta,
                    session_key=session_key,
                    stage_label="drop",
                )
                bad_audio_count = deps.increment_session_bad_audio(session_key)
                _register_wake_drop(
                    deps,
                    metrics,
                    "env_ignore",
                    session_key=session_key,
                    room_session_key=room_session_key,
                    owner_user_id=owner_user_id,
                    wake_probe_text=wake_probe,
                    bad_audio_count=bad_audio_count,
                )
                deps.log_voice_stage(metrics, "환경음 후보 조기 종료", extra=f"wake_probe_text={wake_probe!r}")
                _log_wake_drop_summary(deps, metrics, "env_ignore")
                return None
            deps.print_fn(f"[FULL STT CONTINUE] reason=env_ignore speaker={display_name} probe={wake_probe!r}")
            deps.print_fn(
                f"[ENV IGNORE] speaker={display_name} band_ratio={band_ratio:.3f} flatness={flatness:.3f} "
                f"rms={rms:.4f} probe={wake_probe!r}"
            )
            deps.log_voice_stage(metrics, "환경음 후보지만 본문 STT 진행", extra=f"wake_probe_text={wake_probe!r}")

        if filler_candidate:
            if not wake_detected and raw_seconds <= deps.voice_no_wake_max_continue_sec:
                deps.print_fn(f"[FULL STT SKIP] reason=filler_ignore speaker={display_name} probe={wake_probe!r}")
                deps.print_fn(f"[FILLER IGNORE] speaker={display_name} probe={wake_probe!r}")
                deps.save_voice_debug_audio(
                    guild_id,
                    speaker_name,
                    pcm_bytes,
                    audio16k,
                    wake_probe=wake_probe,
                    final_text="[FILLER IGNORE]",
                    debug_meta=debug_meta,
                    session_key=session_key,
                    stage_label="drop",
                )
                _register_wake_drop(
                    deps,
                    metrics,
                    "filler_ignore",
                    session_key=session_key,
                    room_session_key=room_session_key,
                    owner_user_id=owner_user_id,
                    wake_probe_text=wake_probe,
                )
                deps.log_voice_stage(metrics, "짧은 필러 후보 조기 종료", extra=f"wake_probe_text={wake_probe!r}")
                _log_wake_drop_summary(deps, metrics, "filler_ignore")
                return None
            deps.print_fn(f"[FULL STT CONTINUE] reason=filler_ignore speaker={display_name} probe={wake_probe!r}")
            deps.print_fn(f"[FILLER IGNORE] speaker={display_name} probe={wake_probe!r}")
            deps.log_voice_stage(metrics, "짧은 필러 후보지만 본문 STT 진행", extra=f"wake_probe_text={wake_probe!r}")

        if repetitive_noise_candidate:
            if not wake_detected:
                deps.print_fn(f"[FULL STT SKIP] reason=noise_text_ignore speaker={display_name} probe={wake_probe!r}")
                deps.print_fn(f"[NOISE TEXT IGNORE] speaker={display_name} probe={wake_probe!r}")
                deps.save_voice_debug_audio(
                    guild_id,
                    speaker_name,
                    pcm_bytes,
                    audio16k,
                    wake_probe=wake_probe,
                    final_text="[NOISE TEXT IGNORE]",
                    debug_meta=debug_meta,
                    session_key=session_key,
                    stage_label="drop",
                )
                _register_wake_drop(
                    deps,
                    metrics,
                    "noise_text_ignore",
                    session_key=session_key,
                    room_session_key=room_session_key,
                    owner_user_id=owner_user_id,
                    wake_probe_text=wake_probe,
                )
                deps.log_voice_stage(metrics, "반복 소음 후보 조기 종료", extra=f"wake_probe_text={wake_probe!r}")
                _log_wake_drop_summary(deps, metrics, "noise_text_ignore")
                return None
            deps.print_fn(f"[FULL STT CONTINUE] reason=noise_text_ignore speaker={display_name} probe={wake_probe!r}")
            deps.print_fn(f"[NOISE TEXT IGNORE] speaker={display_name} probe={wake_probe!r}")
            deps.log_voice_stage(metrics, "반복 소음 후보지만 본문 STT 진행", extra=f"wake_probe_text={wake_probe!r}")

        if not wake_detected:
            deps.print_fn(f"[FULL STT CONTINUE] reason=wake_ignore speaker={display_name} probe={wake_probe!r}")
            if wake_probe:
                deps.print_fn(f"[WAKE IGNORE] {display_name}: {wake_probe!r}")
            if deps.should_skip_full_stt_after_wake_probe(
                wake_detected=wake_detected,
                wake_probe=wake_probe,
                duration_sec=duration_sec,
            ):
                deps.print_fn(
                    f"[FULL STT SKIP] reason=wake_probe_low_signal speaker={display_name} "
                    f"probe={wake_probe!r} sec={duration_sec:.2f}"
                )
                deps.save_voice_debug_audio(
                    guild_id,
                    speaker_name,
                    pcm_bytes,
                    audio16k,
                    wake_probe=wake_probe,
                    final_text="[WAKE PROBE SKIP]",
                    debug_meta=debug_meta,
                    session_key=session_key,
                    stage_label="drop",
                )
                _register_wake_drop(
                    deps,
                    metrics,
                    "wake_probe_low_signal",
                    session_key=session_key,
                    room_session_key=room_session_key,
                    owner_user_id=owner_user_id,
                    wake_probe_text=wake_probe,
                    wake_detected=wake_detected,
                )
                deps.log_voice_stage(
                    metrics,
                    "웨이크 프로브 기반 조기 종료",
                    extra=f"wake_probe_text={wake_probe!r} sec={duration_sec:.2f}",
                )
                return None
            deps.log_voice_stage(metrics, "웨이크 미검출이지만 본문 STT 진행", extra=f"wake_probe_text={wake_probe!r}")

    return VoiceWakeProbeResult(
        owner_followup_active=owner_followup_active,
        active_speaker_user_id=active_speaker_user_id,
        wake_probe=wake_probe,
        wake_confirm=wake_confirm,
        wake_detected=wake_detected,
        wake_match_mode=wake_match_mode,
        wake_alias=wake_alias,
        wake_reject_reason=wake_reject_reason,
    )
