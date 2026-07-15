from __future__ import annotations

import asyncio
from dataclasses import dataclass
from typing import Any, Awaitable, Callable

from .omnivoice_request_runtime import (
    build_omnivoice_tts_request_bundle_from_runtime,
    build_omnivoice_tts_result_from_runtime,
    run_omnivoice_tts_with_fallback_from_runtime,
)


@dataclass(frozen=True)
class OmniVoiceSourceRuntimeDeps:
    clean_tts_text: Callable[[str], str]
    merge_log_event_payload: Callable[..., dict[str, Any]]
    source_factory: Callable[..., Any]
    get_http_session: Callable[[], Awaitable[Any]]
    client_timeout_factory: Callable[..., Any]
    omnivoice_timeout_sec: float
    omnivoice_server_url: str
    omnivoice_voice: str
    request_runtime_deps_factory: Callable[[], Any]
    monotonic: Callable[[], float]
    log_turn_event: Callable[..., None]
    record_voice_pipeline_failure: Callable[..., None]
    create_turn_scoped_task: Callable[..., Any]
    log: Callable[[str], Any]


async def create_omnivoice_source_from_runtime(
    text: str,
    *,
    deps: OmniVoiceSourceRuntimeDeps,
    on_task_started: Callable[[], None] | None = None,
    on_request_start: Callable[[], None] | None = None,
    on_response_headers: Callable[[], None] | None = None,
    on_first_byte: Callable[[], None] | None = None,
    on_first_frame: Callable[[], None] | None = None,
    on_first_packet_sent: Callable[[], None] | None = None,
    turn_id: str | None = None,
    chunk_index: int | None = None,
    session_key: str | None = None,
    turn_scope: Any = None,
    trace_payload: dict[str, Any] | None = None,
) -> Any:
    text = deps.clean_tts_text(text)
    if not text:
        raise ValueError("TTS 텍스트가 비어 있습니다.")

    trace = deps.merge_log_event_payload(
        explicit={
            "turn_id": turn_id,
            "chunk_index": chunk_index,
            "session_key": session_key,
        },
        extra=trace_payload,
    )

    source = deps.source_factory(
        on_first_frame=on_first_frame,
        on_first_packet_sent=on_first_packet_sent,
        trace_payload=trace,
    )

    async def producer() -> None:
        session = await deps.get_http_session()
        timeout = deps.client_timeout_factory(total=deps.omnivoice_timeout_sec)
        first_pcm_logged = False

        if on_task_started is not None:
            on_task_started()
        deps.log_turn_event("playback_task_started", **trace)

        async def stream_with_voice(voice_name: str) -> Any:
            request_deps = deps.request_runtime_deps_factory()
            request_bundle = build_omnivoice_tts_request_bundle_from_runtime(
                text=text,
                voice_name=voice_name,
                deps=request_deps,
                turn_id=turn_id,
                chunk_index=chunk_index,
                session_key=session_key,
            )
            tts_request = request_bundle.request
            payload = request_bundle.payload

            nonlocal first_pcm_logged
            request_started_mono = deps.monotonic()
            first_audio_ms: float | None = None

            if on_request_start is not None:
                on_request_start()
            deps.log_turn_event(
                "tts_request_started",
                **deps.merge_log_event_payload(
                    explicit={
                        "request_id": tts_request.request_id,
                        "voice": tts_request.voice,
                        "voice_profile": tts_request.voice_profile,
                    },
                    extra=trace,
                ),
            )
            async with session.post(
                f"{deps.omnivoice_server_url}/v1/audio/speech",
                json=payload,
                timeout=timeout,
            ) as resp:
                if on_response_headers is not None:
                    on_response_headers()
                if resp.status != 200:
                    error_text = await resp.text()
                    return build_omnivoice_tts_result_from_runtime(
                        tts_request,
                        deps=request_deps,
                        ok=False,
                        status_code=resp.status,
                        latency_ms=(deps.monotonic() - request_started_mono) * 1000.0,
                        first_audio_ms=first_audio_ms,
                        error_code="http_error",
                        error_text=error_text,
                    )

                async for chunk in resp.content.iter_chunked(8192):
                    if chunk:
                        if on_first_byte is not None and not first_pcm_logged:
                            on_first_byte()
                        if not first_pcm_logged:
                            first_pcm_logged = True
                            first_audio_ms = (deps.monotonic() - request_started_mono) * 1000.0
                            deps.log_turn_event(
                                "tts_first_pcm_received",
                                **deps.merge_log_event_payload(
                                    explicit={"request_id": tts_request.request_id, "bytes": len(chunk)},
                                    extra=trace,
                                ),
                            )
                        source.feed_pcm24_mono(chunk)
                if not first_pcm_logged:
                    return build_omnivoice_tts_result_from_runtime(
                        tts_request,
                        deps=request_deps,
                        ok=False,
                        status_code=resp.status,
                        latency_ms=(deps.monotonic() - request_started_mono) * 1000.0,
                        first_audio_ms=first_audio_ms,
                        error_code="empty_audio",
                        error_text="OmniVoice returned no PCM bytes.",
                    )
                return build_omnivoice_tts_result_from_runtime(
                    tts_request,
                    deps=request_deps,
                    ok=True,
                    status_code=resp.status,
                    latency_ms=(deps.monotonic() - request_started_mono) * 1000.0,
                    first_audio_ms=first_audio_ms,
                )

        try:
            await run_omnivoice_tts_with_fallback_from_runtime(
                primary_voice=deps.omnivoice_voice,
                stream_with_voice=stream_with_voice,
                log=deps.log,
            )
        except asyncio.CancelledError:
            deps.record_voice_pipeline_failure("tts_producer_cancelled", "cancelled", None, **trace)
            source.cleanup()
            raise
        except Exception as exc:
            deps.record_voice_pipeline_failure("tts_request_failed", exc, None, **trace)
            source.fail(exc)
            return

        source.finish()

    deps.create_turn_scoped_task(producer(), turn_scope=turn_scope)
    return source
