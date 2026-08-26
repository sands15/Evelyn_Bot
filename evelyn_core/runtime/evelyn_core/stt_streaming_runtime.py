from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Callable

import numpy as np

from .stt_client import (
    run_stt_client_operation_with_cancellation_drain,
    start_stt_stream_with_cleanup,
)
from .voice_asr_stream import AsrStreamSession


@dataclass(frozen=True)
class CompletedSttStream:
    final_text: str
    partial_text: str
    committed_text: str
    authoritative: bool
    revision_count: int
    fallback_reason: str | None = None
    fallback_exhausted: bool = False


def _apply_response(
    tracker: AsrStreamSession,
    payload: Any,
    *,
    expected_final: bool,
):
    if (
        not isinstance(payload, dict)
        or type(payload.get("revision")) is not int
        or not isinstance(payload.get("text"), str)
        or payload.get("isFinal") is not expected_final
    ):
        raise RuntimeError("stt_stream_response_invalid")
    return tracker.apply(
        revision=payload["revision"],
        text=payload["text"],
        is_final=expected_final,
    )


async def transcribe_complete_audio_stream(
    audio16k: np.ndarray,
    *,
    sampling_rate: int,
    service_url: str,
    timeout_sec: float,
    start_stream: Callable[..., dict[str, Any]],
    push_chunk: Callable[..., dict[str, Any]],
    finish_stream: Callable[..., dict[str, Any]],
    cancel_stream: Callable[..., dict[str, Any]],
    chunk_samples: int = 8000,
) -> CompletedSttStream:
    """Run one already-buffered utterance through the shared stateful STT contract."""

    if int(sampling_rate) != 16000:
        raise ValueError("stt_stream_requires_16khz")
    audio = np.asarray(audio16k, dtype=np.float32).reshape(-1)
    if audio.size == 0:
        raise ValueError("stt_stream_audio_empty")
    if chunk_samples <= 0:
        raise ValueError("stt_stream_chunk_samples_must_be_positive")

    pcm16 = np.clip(audio, -1.0, 1.0)
    pcm16 = np.rint(pcm16 * 32767.0).astype("<i2")
    stream_id = ""
    tracker = AsrStreamSession()
    last_partial = ""

    try:
        started = await start_stt_stream_with_cleanup(
            service_url=service_url,
            timeout_sec=timeout_sec,
            language="Korean",
            start_stream=start_stream,
            cancel_stream=cancel_stream,
        )
        if not isinstance(started, dict):
            raise RuntimeError("stt_stream_start_contract_invalid")
        stream_id = started.get("streamId")
        if (
            not isinstance(stream_id, str)
            or not stream_id
            or type(started.get("samplingRate")) is not int
            or started["samplingRate"] != 16000
            or not isinstance(started.get("decoderProfile"), str)
            or started["decoderProfile"] != "realtime-ko"
            or type(started.get("nextSequence")) is not int
            or started["nextSequence"] != 0
        ):
            raise RuntimeError("stt_stream_start_contract_invalid")

        for sequence, offset in enumerate(range(0, pcm16.size, chunk_samples)):
            response = await run_stt_client_operation_with_cancellation_drain(
                push_chunk,
                pcm16[offset : offset + chunk_samples],
                service_url=service_url,
                stream_id=stream_id,
                sequence=sequence,
                timeout_sec=timeout_sec,
            )
            revision = _apply_response(tracker, response, expected_final=False)
            last_partial = revision.text

        response = await run_stt_client_operation_with_cancellation_drain(
            finish_stream,
            service_url=service_url,
            stream_id=stream_id,
            timeout_sec=timeout_sec,
        )
        final = _apply_response(tracker, response, expected_final=True)
        stream_id = ""
        fallback_reason = None
        if not final.text:
            fallback_reason = "empty_final"
        elif final.conflicts_with_stable_prefix:
            fallback_reason = "stable_prefix_conflict"
        return CompletedSttStream(
            final_text=final.text,
            partial_text=last_partial,
            committed_text=final.stable_prefix,
            authoritative=final.authoritative,
            revision_count=final.revision,
            fallback_reason=fallback_reason,
        )
    except BaseException:
        if stream_id:
            try:
                await run_stt_client_operation_with_cancellation_drain(
                    cancel_stream,
                    service_url=service_url,
                    stream_id=stream_id,
                    timeout_sec=min(max(1.0, timeout_sec), 3.0),
                )
            except Exception:
                pass
        raise


__all__ = ["CompletedSttStream", "transcribe_complete_audio_stream"]
