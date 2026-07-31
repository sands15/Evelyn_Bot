from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Awaitable, Callable


@dataclass(frozen=True)
class VoiceSttExecutionDeps:
    run_partial_stt_flow: Callable[..., Awaitable[Any]]
    run_full_stt_with_optional_rescore: Callable[..., Awaitable[Any]]
    build_partial_stt_window: Callable[..., Any]
    get_partial_transcript: Callable[..., Any]
    read_committed_text: Callable[[str | None], str]
    run_blocking_stt_task: Callable[..., Awaitable[Any]]
    speculate_from_committed_stt: Callable[..., Any]
    room_state_snapshot: Callable[[str], dict[str, Any]]
    clean_text: Callable[[str], str]
    remember_speculative_policy: Callable[[str, dict[str, Any]], Any]
    transcribe_audio: Callable[..., Any]
    choose_full_stt_candidate: Callable[..., Any]
    log_voice_stage: Callable[..., Any]
    mark_turn_stage: Callable[..., Any]
    save_voice_debug_audio: Callable[..., Any]
    print_fn: Callable[..., Any]
    full_stt_timeout_sec: float
    voice_stt_max_new_tokens: int
    rescore_enabled: bool
    rescore_extra_tokens: int
    rescore_min_audio_sec: float
    rescore_min_text_len: int
    rescore_timeout_sec: float


@dataclass(frozen=True)
class VoiceSttExecutionResult:
    text: str
    stt_meta: dict[str, Any]
    partial_text: str
    committed_partial_text: str


async def run_voice_stt_execution_from_runtime(
    *,
    member: Any,
    guild_id: int,
    speaker_name: str,
    pcm_bytes: bytes,
    debug_meta: dict[str, Any] | None,
    session_key: str,
    room_session_key: str,
    audio16k: Any,
    stt_sampling_rate: int,
    duration_sec: float,
    wake_probe: str,
    wake_detected: bool,
    metrics: dict[str, Any],
    deps: VoiceSttExecutionDeps,
) -> VoiceSttExecutionResult | None:
    display_name = getattr(member, "display_name", None)
    deps.print_fn(
        f"[FULL STT ENTER] speaker={display_name} sampling_rate={stt_sampling_rate} "
        f"samples={audio16k.size} wake_detected={wake_detected}"
    )
    deps.log_voice_stage(metrics, "본문 STT 시작", extra=f"samples={audio16k.size}")

    partial_text = ""
    committed_partial_text = ""
    try:
        partial_result = await deps.run_partial_stt_flow(
            audio16k,
            sampling_rate=stt_sampling_rate,
            session_key=session_key,
            timeout_sec=max(3.0, min(10.0, deps.full_stt_timeout_sec * 0.5)),
            build_partial_stt_window=deps.build_partial_stt_window,
            get_partial_transcript=deps.get_partial_transcript,
            read_committed_text=deps.read_committed_text,
            run_blocking_stt_task=deps.run_blocking_stt_task,
            speculate_from_committed_stt=deps.speculate_from_committed_stt,
            room_state=deps.room_state_snapshot(room_session_key),
            clean_text=deps.clean_text,
            metrics=metrics,
            print_fn=deps.print_fn,
        )
        partial_text = partial_result.partial_text
        committed_partial_text = partial_result.committed_text
        metrics.setdefault("meta", {}).update(
            {
                "partial_stt_text": partial_text,
                "committed_stt_text": committed_partial_text,
            }
        )
        speculative = partial_result.speculative_policy
        if speculative is not None:
            deps.remember_speculative_policy(session_key, speculative)
            metrics.setdefault("meta", {})["speculative_policy"] = dict(speculative.get("policy") or {})
    except Exception as exc:
        deps.print_fn(
            f"[STT PARTIAL] errorType={type(exc).__name__}"
        )

    try:
        full_stt_result = await deps.run_full_stt_with_optional_rescore(
            audio16k,
            sampling_rate=stt_sampling_rate,
            duration_sec=duration_sec,
            wake_probe=wake_probe,
            max_new_tokens=deps.voice_stt_max_new_tokens,
            full_timeout_sec=deps.full_stt_timeout_sec,
            rescore_enabled=deps.rescore_enabled,
            rescore_extra_tokens=deps.rescore_extra_tokens,
            rescore_min_audio_sec=deps.rescore_min_audio_sec,
            rescore_min_text_len=deps.rescore_min_text_len,
            rescore_timeout_sec=deps.rescore_timeout_sec,
            run_blocking_stt_task=deps.run_blocking_stt_task,
            transcribe_audio=deps.transcribe_audio,
            choose_candidate=lambda primary, rescore: deps.choose_full_stt_candidate(
                primary,
                rescore,
                wake_probe=wake_probe,
            ),
            clean_text=deps.clean_text,
            log_stage=deps.log_voice_stage,
            metrics=metrics,
            print_fn=deps.print_fn,
            speaker_name=display_name,
        )
    except Exception as exc:
        deps.print_fn(f"[STT] errorType={type(exc).__name__}")
        deps.log_voice_stage(
            metrics,
            "본문 STT 실패",
            extra=f"errorType={type(exc).__name__}",
        )
        return None

    text = full_stt_result.text
    stt_meta = full_stt_result.stt_meta
    deps.mark_turn_stage(metrics, "stt_full_done", event_name="stt_full_done", text_len=len(text))
    deps.log_voice_stage(metrics, "본문 STT 완료", extra=f"text_len={len(text)}", key="stt_done")

    if not text:
        deps.save_voice_debug_audio(
            guild_id,
            speaker_name,
            pcm_bytes,
            audio16k,
            wake_probe=wake_probe,
            final_text="[EMPTY STT]",
            debug_meta=debug_meta,
            stt_meta=stt_meta,
            session_key=session_key,
            stage_label="drop",
        )
        deps.log_voice_stage(metrics, "본문 STT 빈 결과")
        return None

    return VoiceSttExecutionResult(
        text=text,
        stt_meta=stt_meta,
        partial_text=partial_text,
        committed_partial_text=committed_partial_text,
    )
