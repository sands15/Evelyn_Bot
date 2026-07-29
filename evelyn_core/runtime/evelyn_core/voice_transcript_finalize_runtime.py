from __future__ import annotations

from dataclasses import dataclass, replace
from typing import Any, Callable, MutableMapping


@dataclass(frozen=True)
class VoiceTranscriptFinalizeDeps:
    build_final_transcript_flow: Callable[..., Any]
    room_state_snapshot: Callable[[str], dict[str, Any]]
    apply_stt_post_corrections: Callable[..., str]
    clean_text: Callable[[str], str]
    set_partial_text: Callable[[str, str], Any]
    commit_stable_transcript: Callable[..., Any]
    build_transcript_result: Callable[..., Any]
    speculate_from_committed_stt: Callable[..., Any]
    remember_speculative_policy: Callable[[str, dict[str, Any]], Any]
    room_last_voice_utterance_for_merge: MutableMapping[str, Any]
    maybe_merge_barge_in_utterance: Callable[..., tuple[str, dict[str, Any] | None]]
    log_voice_stage: Callable[..., Any]
    print_fn: Callable[..., Any]
    merge_window_sec: float
    tts_interrupted_window_sec: float
    incomplete_window_sec: float
    complete_question_window_sec: float
    adaptive_window_enabled: bool
    validation_transcript_observer: Callable[..., Any] | None = None


@dataclass(frozen=True)
class VoiceTranscriptFinalizeResult:
    text: str
    committed_text: str
    transcript_result: Any


def finalize_voice_transcript_from_runtime(
    *,
    member: Any,
    text: str,
    partial_text: str,
    session_key: str,
    room_session_key: str,
    turn_id: str,
    wake_detected: bool,
    wake_match_mode: str,
    wake_alias: str | None,
    wake_probe: str,
    wake_confirm: str,
    wake_reject_reason: str | None,
    duration_sec: float,
    metrics: dict[str, Any],
    deps: VoiceTranscriptFinalizeDeps,
) -> VoiceTranscriptFinalizeResult:
    raw_text = text
    final_transcript = deps.build_final_transcript_flow(
        text=text,
        partial_text=partial_text,
        session_key=session_key,
        wake_detected=wake_detected,
        wake_match_mode=wake_match_mode,
        wake_alias=wake_alias,
        wake_probe=wake_probe,
        wake_confirm=wake_confirm,
        wake_reject_reason=wake_reject_reason,
        speaker_user_id=member.id,
        duration_sec=duration_sec,
        room_state=deps.room_state_snapshot(room_session_key),
        apply_post_corrections=deps.apply_stt_post_corrections,
        clean_text=deps.clean_text,
        set_partial_text=deps.set_partial_text,
        commit_stable_transcript=deps.commit_stable_transcript,
        build_transcript_result=deps.build_transcript_result,
        speculate_from_committed_stt=deps.speculate_from_committed_stt,
    )
    if final_transcript.was_corrected:
        deps.print_fn(f"[STT CORRECT] raw={raw_text!r} -> corrected={final_transcript.corrected_text!r}")

    text = final_transcript.corrected_text
    committed_text = final_transcript.committed_text
    transcript_result = final_transcript.transcript_result
    if final_transcript.speculative_policy is not None:
        deps.remember_speculative_policy(session_key, final_transcript.speculative_policy)
    if committed_text and len(deps.clean_text(text)) >= len(committed_text):
        text = deps.clean_text(text)

    meta = metrics.setdefault("meta", {})
    interrupted_at_raw = meta.get("tts_interrupted_at")
    interrupted_at = None
    if interrupted_at_raw is not None:
        try:
            interrupted_at = float(interrupted_at_raw)
        except (TypeError, ValueError):
            interrupted_at = None

    if meta.get("tts_interrupted_by_user_audio") or meta.get("local_tts_interrupted_by_user_audio"):
        merged_text, merge_meta = deps.maybe_merge_barge_in_utterance(
            deps.room_last_voice_utterance_for_merge,
            room_session_key=room_session_key,
            session_key=session_key,
            user_id=member.id,
            current_text=transcript_result.final_text,
            current_turn_id=turn_id,
            interrupted_at=interrupted_at,
            merge_window_sec=deps.merge_window_sec,
            tts_interrupted_window_sec=deps.tts_interrupted_window_sec,
            incomplete_window_sec=deps.incomplete_window_sec,
            complete_question_window_sec=deps.complete_question_window_sec,
            adaptive_window_enabled=deps.adaptive_window_enabled,
            clean_text=deps.clean_text,
        )
        if merge_meta:
            transcript_result = replace(
                transcript_result,
                final_text=merged_text,
                committed_text=merged_text,
            )
            text = merged_text
            committed_text = merged_text
            meta["barge_in_utterance_merge"] = merge_meta
            deps.log_voice_stage(
                metrics,
                "TTS barge-in utterance merged",
                extra=f"delta={merge_meta.get('delta_sec')} text={merged_text!r}",
            )

    deps.print_fn(
        f"[STT RESULT][full-final] text={transcript_result.final_text!r} "
        f"committed={transcript_result.committed_text!r} wake_detected={transcript_result.wake_detected}"
    )
    if deps.validation_transcript_observer is not None:
        try:
            validation_event = deps.validation_transcript_observer(
                "discord",
                transcript_result.final_text,
                turnId=turn_id,
                prefer_interrupt=bool(
                    metrics.setdefault("meta", {}).get("tts_interrupted_by_user_audio")
                    or metrics.setdefault("meta", {}).get("local_tts_interrupted_by_user_audio")
                ),
            )
        except Exception:
            validation_event = None
        if isinstance(validation_event, dict):
            metrics.setdefault("meta", {}).update(
                {
                    "validation_session_id": validation_event.get("sessionId"),
                    "validation_step_id": validation_event.get("stepId"),
                    "validation_transcript_match": bool(validation_event.get("matched")),
                }
            )
    return VoiceTranscriptFinalizeResult(
        text=text,
        committed_text=committed_text,
        transcript_result=transcript_result,
    )
