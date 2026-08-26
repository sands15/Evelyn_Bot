from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Callable


@dataclass(frozen=True)
class SttTranscriptionRuntimeDeps:
    stt_service_url: str
    stt_service_timeout_sec: float
    stt_service_fallback_local: bool
    stt_language: str
    stt_force_language: bool
    target_rate: int
    normalize_stt_language: Callable[..., str | None]
    transcribe_via_service: Callable[..., dict[str, Any]]
    get_stt_model: Callable[[], tuple[Any, Any, Any]]
    as_float32_array: Callable[[Any], Any]
    resample_audio_float: Callable[[Any, int, int], Any]
    clean_text: Callable[[str], str]
    log: Callable[[str], Any]


def _text_for_log(value: Any, *, validation_bound: bool) -> Any:
    label = "validation-text" if validation_bound else "transcript"
    return f"<{label} chars={len(str(value or ''))}>"


def transcribe_audio16k_from_runtime(
    audio16k: Any,
    max_new_tokens: int = 256,
    *,
    deps: SttTranscriptionRuntimeDeps,
    sampling_rate: int,
    stage: str = "full",
    validation_bound: bool = False,
) -> str:
    if audio16k.size == 0:
        return ""

    effective_rate = max(1, int(sampling_rate))
    deps.log(
        f"[STT INPUT][{stage}] sampling_rate={effective_rate} "
        f"samples={audio16k.size} sec={audio16k.size / float(effective_rate):.2f}"
    )
    if deps.stt_service_url:
        try:
            language = (
                deps.normalize_stt_language(None, default_language=deps.stt_language)
                if deps.stt_force_language
                else None
            )
            remote_kwargs: dict[str, Any] = {
                "service_url": deps.stt_service_url,
                "timeout_sec": deps.stt_service_timeout_sec,
                "sampling_rate": effective_rate,
                "max_new_tokens": max_new_tokens,
                "stage": stage,
                "language": language,
            }
            if validation_bound:
                remote_kwargs["validation_bound"] = True
            result = deps.transcribe_via_service(audio16k, **remote_kwargs)
            text = deps.clean_text(str(result.get("text") or ""))
            deps.log(
                f"[STT REMOTE DONE][{stage}] "
                f"text={_text_for_log(text, validation_bound=validation_bound)!r}"
            )
            return text
        except Exception as exc:
            error_detail = f"errorType={type(exc).__name__}"
            deps.log(f"[STT REMOTE FAIL][{stage}] {error_detail}")
            if not deps.stt_service_fallback_local:
                raise

    _backend, _processor, model = deps.get_stt_model()
    stt_audio = deps.as_float32_array(audio16k)

    if effective_rate != deps.target_rate:
        stt_audio = deps.resample_audio_float(stt_audio, effective_rate, deps.target_rate)
        effective_rate = deps.target_rate
        deps.log(
            f"[STT RESAMPLE][{stage}] {sampling_rate} -> {deps.target_rate} "
            f"samples={stt_audio.size}"
        )

    language = (
        deps.normalize_stt_language(None, default_language=deps.stt_language)
        if deps.stt_force_language
        else None
    )
    results = model.transcribe(
        audio=(stt_audio, effective_rate),
        language=language,
        return_time_stamps=False,
    )
    if not results:
        deps.log(f"[STT DONE][{stage}] empty_result")
        return ""

    text = deps.clean_text(getattr(results[0], "text", "") or "")
    deps.log(
        f"[STT DONE][{stage}] "
        f"text={_text_for_log(text, validation_bound=validation_bound)!r}"
    )
    return text


__all__ = ["SttTranscriptionRuntimeDeps", "transcribe_audio16k_from_runtime"]
