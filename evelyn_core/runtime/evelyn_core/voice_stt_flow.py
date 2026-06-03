from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Awaitable, Callable


@dataclass(frozen=True)
class WakeSttResult:
    wake_detected: bool
    probe_text: str
    confirm_text: str
    wake_match_mode: str
    wake_alias: str | None
    wake_reject_reason: str | None

    @classmethod
    def from_mapping(
        cls,
        value: dict[str, Any] | None,
        *,
        clean_text: Callable[[str], str],
    ) -> "WakeSttResult":
        data = value if isinstance(value, dict) else {}
        wake_detected = bool(data.get("wake_detected"))
        return cls(
            wake_detected=wake_detected,
            probe_text=str(data.get("wake_probe_text") or ""),
            confirm_text=str(data.get("wake_confirm_text") or ""),
            wake_match_mode=str(data.get("wake_match_mode") or ("exact" if wake_detected else "rejected")),
            wake_alias=clean_text(str(data.get("wake_alias") or "")) or None,
            wake_reject_reason=clean_text(str(data.get("wake_reject_reason") or "")) or None,
        )


@dataclass(frozen=True)
class WakeProbeInterpretation:
    probe_text: str
    confirm_text: str
    wake_detected: bool
    wake_match_mode: str
    wake_alias: str | None
    wake_reject_reason: str | None
    near_miss: bool = False


@dataclass(frozen=True)
class FullSttResult:
    text: str
    primary_text: str
    stt_meta: dict[str, Any]


@dataclass(frozen=True)
class PartialSttResult:
    partial_text: str
    committed_text: str
    speculative_policy: dict[str, Any] | None = None
    skipped_reason: str | None = None


@dataclass(frozen=True)
class FinalWakeVetoDecision:
    accepted: bool
    wake_alias: str | None = None
    reject_reason: str | None = None


@dataclass(frozen=True)
class FinalTranscriptFlowResult:
    corrected_text: str
    committed_text: str
    transcript_result: Any
    speculative_policy: dict[str, Any] | None = None
    was_corrected: bool = False


def interpret_wake_probe_result(
    value: dict[str, Any] | None,
    *,
    clean_text: Callable[[str], str],
    apply_post_corrections: Callable[..., str],
) -> WakeProbeInterpretation:
    wake_stt = WakeSttResult.from_mapping(value, clean_text=clean_text)
    return WakeProbeInterpretation(
        probe_text=apply_post_corrections(wake_stt.probe_text, wake_detected=False),
        confirm_text=apply_post_corrections(wake_stt.confirm_text, wake_detected=False),
        wake_detected=wake_stt.wake_detected,
        wake_match_mode=wake_stt.wake_match_mode,
        wake_alias=wake_stt.wake_alias,
        wake_reject_reason=wake_stt.wake_reject_reason,
    )


def apply_strict_wake_confirm_policy(
    wake: WakeProbeInterpretation,
    *,
    strict_confirm_required: bool,
) -> WakeProbeInterpretation:
    if not strict_confirm_required or wake.wake_match_mode == "exact":
        return wake
    return WakeProbeInterpretation(
        probe_text=wake.probe_text,
        confirm_text=wake.confirm_text,
        wake_detected=False,
        wake_match_mode="rejected",
        wake_alias=wake.wake_alias,
        wake_reject_reason="unstable_audio",
        near_miss=wake.near_miss,
    )


def apply_fuzzy_wake_near_miss(
    wake: WakeProbeInterpretation,
    *,
    fuzzy_leading_wake_alias: Callable[[str], str | None],
) -> WakeProbeInterpretation:
    if wake.wake_detected:
        return wake
    fuzzy_probe_alias = fuzzy_leading_wake_alias(wake.probe_text)
    fuzzy_confirm_alias = fuzzy_leading_wake_alias(wake.confirm_text)
    wake_alias = fuzzy_probe_alias or fuzzy_confirm_alias
    if not wake_alias:
        return wake
    return WakeProbeInterpretation(
        probe_text=wake.probe_text,
        confirm_text=wake.confirm_text,
        wake_detected=True,
        wake_match_mode="fuzzy",
        wake_alias=wake_alias,
        wake_reject_reason=None,
        near_miss=True,
    )


def decide_final_wake_veto(
    *,
    final_text: str,
    owner_followup_active: bool,
    extract_leading_wake_alias: Callable[[str], str | None],
) -> FinalWakeVetoDecision:
    if owner_followup_active:
        return FinalWakeVetoDecision(True)

    wake_alias = extract_leading_wake_alias(final_text)
    if wake_alias is None:
        return FinalWakeVetoDecision(False, reject_reason="full_text_veto")
    return FinalWakeVetoDecision(True, wake_alias=wake_alias)


def build_final_transcript_flow(
    *,
    text: str,
    partial_text: str,
    session_key: str | None,
    wake_detected: bool,
    wake_match_mode: str | None,
    wake_alias: str | None,
    wake_probe: str,
    wake_confirm: str,
    wake_reject_reason: str | None,
    speaker_user_id: int | None,
    duration_sec: float,
    room_state: dict | None,
    apply_post_corrections: Callable[..., str],
    clean_text: Callable[[str], str],
    set_partial_text: Callable[[str | None, str], Any],
    commit_stable_transcript: Callable[..., str],
    build_transcript_result: Callable[..., Any],
    speculate_from_committed_stt: Callable[[str, dict | None], dict[str, Any] | None],
) -> FinalTranscriptFlowResult:
    corrected_text = apply_post_corrections(text, wake_detected=wake_detected)
    set_partial_text(session_key, clean_text(partial_text))
    committed_text = commit_stable_transcript(session_key, new_partial_text=corrected_text)
    transcript_result = build_transcript_result(
        wake_detected=wake_detected,
        wake_match_mode=wake_match_mode,
        wake_alias=wake_alias,
        probe_text=wake_probe,
        confirm_text=wake_confirm,
        reject_reason=wake_reject_reason,
        partial_text=clean_text(partial_text),
        committed_text=committed_text,
        final_text=clean_text(corrected_text),
        speaker_user_id=speaker_user_id,
        duration_sec=duration_sec,
    )
    speculative = speculate_from_committed_stt(committed_text, room_state)
    return FinalTranscriptFlowResult(
        corrected_text=corrected_text,
        committed_text=committed_text,
        transcript_result=transcript_result,
        speculative_policy=speculative,
        was_corrected=corrected_text != text,
    )


async def run_partial_stt_flow(
    audio: Any,
    *,
    sampling_rate: int,
    session_key: str | None,
    timeout_sec: float,
    build_partial_stt_window: Callable[..., Any],
    get_partial_transcript: Callable[..., tuple[str, str]],
    read_committed_text: Callable[[str | None], str],
    run_blocking_stt_task: Callable[..., Awaitable[tuple[str, str]]],
    speculate_from_committed_stt: Callable[[str, dict | None], dict[str, Any] | None],
    room_state: dict | None,
    clean_text: Callable[[str], str],
    metrics: dict[str, Any] | None = None,
    print_fn: Callable[[str], Any] | None = None,
) -> PartialSttResult:
    partial_audio = build_partial_stt_window(audio, sampling_rate=sampling_rate)
    partial_min_samples = max(1, int(float(sampling_rate) * 0.85))
    partial_should_run = getattr(partial_audio, "size", 0) >= partial_min_samples
    if not partial_should_run:
        if metrics is not None:
            metrics.setdefault("meta", {})["partial_stt_skip_reason"] = "insufficient_audio"
        committed_text = clean_text(read_committed_text(session_key))
        return PartialSttResult("", committed_text, skipped_reason="insufficient_audio")

    partial_text, committed_text = await run_blocking_stt_task(
        lambda: get_partial_transcript(session_key, audio, sampling_rate=sampling_rate),
        stage="partial",
        timeout_sec=timeout_sec,
        metrics=metrics,
    )
    if print_fn is not None and partial_text:
        print_fn(f"[STT RESULT][partial] text={partial_text!r} committed={committed_text!r}")

    speculative = speculate_from_committed_stt(committed_text or partial_text, room_state)
    return PartialSttResult(partial_text, committed_text, speculative_policy=speculative)


async def run_full_stt_with_optional_rescore(
    audio: Any,
    *,
    sampling_rate: int,
    duration_sec: float,
    wake_probe: str,
    max_new_tokens: int,
    full_timeout_sec: float,
    rescore_enabled: bool,
    rescore_extra_tokens: int,
    rescore_min_audio_sec: float,
    rescore_min_text_len: int,
    rescore_timeout_sec: float,
    run_blocking_stt_task: Callable[..., Awaitable[str]],
    transcribe_audio: Callable[..., str],
    choose_candidate: Callable[[str, str], tuple[str, dict[str, Any]]],
    clean_text: Callable[[str], str],
    log_stage: Callable[..., Any] | None = None,
    metrics: dict[str, Any] | None = None,
    print_fn: Callable[[str], Any] | None = None,
    speaker_name: str = "",
) -> FullSttResult:
    del wake_probe
    primary_text = await run_blocking_stt_task(
        lambda: transcribe_audio(
            audio,
            max_new_tokens,
            sampling_rate=sampling_rate,
            stage="full",
        ),
        stage="full",
        timeout_sec=max(8.0, full_timeout_sec),
        metrics=metrics,
    )

    if print_fn is not None:
        print_fn(f"[STT RESULT][full-primary] text={primary_text!r}")

    text = primary_text
    clean_primary_text = clean_text(primary_text)
    rescore_skip_reason = ""
    if duration_sec < rescore_min_audio_sec:
        rescore_skip_reason = "audio_too_short"
    elif len(clean_primary_text) < rescore_min_text_len:
        rescore_skip_reason = "text_too_short"

    if rescore_enabled and not rescore_skip_reason:
        if log_stage is not None:
            log_stage(metrics, "full STT rescore start")
        try:
            rescore_text = await run_blocking_stt_task(
                lambda: transcribe_audio(
                    audio,
                    max_new_tokens + max(0, rescore_extra_tokens),
                    sampling_rate=sampling_rate,
                    stage="full-rescore",
                ),
                stage="full-rescore",
                timeout_sec=max(4.0, rescore_timeout_sec),
                metrics=metrics,
            )
            text, stt_meta = choose_candidate(primary_text, rescore_text)
            if print_fn is not None:
                print_fn(
                    f"[STT RESCORE] speaker={speaker_name} selected={stt_meta['selected']} "
                    f"primary_score={stt_meta['primary_score']:.3f} rescore_score={stt_meta['rescore_score']:.3f}"
                )
                print_fn(f"[STT RESULT][full-rescore] text={rescore_text!r}")
                if stt_meta["selected"] == "rescore":
                    print_fn(f"[STT RESCORE PICK] primary={primary_text!r} -> rescore={rescore_text!r}")
            if log_stage is not None:
                log_stage(metrics, "full STT rescore done", extra=f"selected={stt_meta['selected']}")
        except Exception as exc:
            stt_meta = {"enabled": True, "selected": "primary", "rescore_error": repr(exc), "primary_text": primary_text}
            if print_fn is not None:
                print_fn(f"[STT RESCORE FAIL] {exc}")
            if log_stage is not None:
                log_stage(metrics, "full STT rescore failed", extra=repr(exc))
    else:
        stt_meta = {
            "enabled": bool(rescore_enabled),
            "selected": "primary",
            "primary_text": primary_text,
            "skipped_reason": rescore_skip_reason or "disabled",
        }
        if rescore_skip_reason and log_stage is not None:
            log_stage(metrics, "STT rescore skip", extra=rescore_skip_reason)

    return FullSttResult(text=text, primary_text=primary_text, stt_meta=stt_meta)
