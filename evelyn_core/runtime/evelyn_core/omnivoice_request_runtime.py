from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Awaitable, Callable


@dataclass(frozen=True)
class OmniVoiceRequestRuntimeDeps:
    request_id_suffix: Callable[[], str]
    tts_synth_request_factory: Callable[..., Any]
    tts_synth_result_factory: Callable[..., Any]
    omnivoice_model: str
    omnivoice_pcm_rate: int
    omnivoice_stream: bool
    omnivoice_num_step: int
    omnivoice_speed: float
    omnivoice_language: str


@dataclass(frozen=True)
class OmniVoiceRequestBundle:
    request: Any
    payload: dict[str, Any]


def build_omnivoice_tts_request_bundle_from_runtime(
    *,
    text: str,
    voice_name: str,
    deps: OmniVoiceRequestRuntimeDeps,
    turn_id: str | None = None,
    chunk_index: int | None = None,
    session_key: str | None = None,
) -> OmniVoiceRequestBundle:
    request_id = f"{turn_id or 'turnless'}:{chunk_index or 0}:{deps.request_id_suffix()}"
    tts_request = deps.tts_synth_request_factory(
        request_id=request_id,
        turn_id=turn_id or "",
        text=text,
        voice=voice_name,
        voice_profile=voice_name.split(":", 1)[1] if voice_name.startswith("clone:") else None,
        response_format="pcm",
        sample_rate_hz=deps.omnivoice_pcm_rate,
        stream=deps.omnivoice_stream,
        chunk_index=int(chunk_index or 0),
        metadata={"session_key": session_key or "", "text_len": len(text)},
    )
    payload: dict[str, Any] = {
        "model": deps.omnivoice_model,
        "input": text,
        "voice": tts_request.voice,
        "response_format": tts_request.response_format,
        "stream": tts_request.stream,
        "num_step": deps.omnivoice_num_step,
    }
    if deps.omnivoice_speed > 0 and abs(deps.omnivoice_speed - 1.0) > 0.001:
        payload["speed"] = deps.omnivoice_speed
    if deps.omnivoice_language:
        payload["language"] = deps.omnivoice_language
    if turn_id:
        payload["turn_id"] = turn_id
    if session_key:
        payload["session_key"] = session_key
    return OmniVoiceRequestBundle(request=tts_request, payload=payload)


def build_omnivoice_tts_result_from_runtime(
    request: Any,
    *,
    deps: OmniVoiceRequestRuntimeDeps,
    ok: bool,
    status_code: int,
    latency_ms: float,
    first_audio_ms: float | None = None,
    error_code: str | None = None,
    error_text: str | None = None,
) -> Any:
    kwargs: dict[str, Any] = {
        "request_id": request.request_id,
        "turn_id": request.turn_id,
        "backend": "omnivoice_http",
        "ok": ok,
        "response_format": request.response_format,
        "sample_rate_hz": request.sample_rate_hz,
        "profile_resolved": request.voice,
        "status_code": status_code,
        "latency_ms": latency_ms,
        "first_audio_ms": first_audio_ms,
        "metadata": request.metadata,
    }
    if error_code is not None:
        kwargs["error_code"] = error_code
    if error_text is not None:
        kwargs["error_text"] = error_text
    return deps.tts_synth_result_factory(**kwargs)


async def run_omnivoice_tts_with_fallback_from_runtime(
    *,
    primary_voice: str,
    stream_with_voice: Callable[[str], Awaitable[Any]],
    log: Callable[[str], Any] = print,
) -> Any:
    tts_result = await stream_with_voice(primary_voice)
    if not tts_result.ok:
        if primary_voice.startswith("clone:"):
            log(
                "[TTS FALLBACK] clone voice 실패 -> auto 사용 | "
                "errorCode=tts_request_failed"
            )
            tts_result = await stream_with_voice("auto")
        if not tts_result.ok:
            raise RuntimeError("omnivoice_request_failed")
    return tts_result
