from __future__ import annotations

import asyncio
import base64
import json
from typing import Any, Callable
from urllib.parse import quote
from urllib import request

import numpy as np


MAX_STT_RESPONSE_BYTES = 1024 * 1024


def _json_request(req: request.Request, *, timeout_sec: float) -> dict[str, Any]:
    with request.urlopen(req, timeout=max(1.0, float(timeout_sec))) as resp:
        raw = resp.read(MAX_STT_RESPONSE_BYTES + 1)
    if len(raw) > MAX_STT_RESPONSE_BYTES:
        raise RuntimeError("stt_response_too_large")
    return json.loads(raw.decode("utf-8")) if raw else {}


def _stream_url(service_url: str, stream_id: str, suffix: str = "") -> str:
    root = service_url.rstrip("/")
    return f"{root}/v1/stt/streams/{quote(str(stream_id), safe='')}{suffix}"


def transcribe_audio16k_via_service(
    audio: np.ndarray,
    *,
    service_url: str,
    timeout_sec: float,
    sampling_rate: int,
    max_new_tokens: int,
    stage: str,
    language: str | None = None,
    validation_bound: bool = False,
) -> dict[str, Any]:
    stt_audio = np.asarray(audio, dtype=np.float32)
    payload: dict[str, Any] = {
        "audio_f32_base64": base64.b64encode(stt_audio.tobytes()).decode("ascii"),
        "sample_count": int(stt_audio.size),
        "sampling_rate": int(sampling_rate),
        "max_new_tokens": int(max_new_tokens),
        "stage": str(stage or "full"),
    }
    if language:
        payload["language"] = language
    if validation_bound:
        payload["validation_bound"] = True

    root = service_url.rstrip("/")
    req = request.Request(
        f"{root}/v1/stt/transcribe",
        data=json.dumps(payload).encode("utf-8"),
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    return _json_request(req, timeout_sec=timeout_sec)


async def transcribe_completed_audio16k_via_service(
    audio: Any,
    *,
    service_url: str,
    timeout_sec: float,
    sampling_rate: int,
    max_new_tokens: int,
    language: str | None = None,
) -> dict[str, Any]:
    if not service_url:
        raise RuntimeError("stt_service_not_configured")
    stt_audio = np.asarray(audio, dtype=np.float32)
    if (
        type(sampling_rate) is not int
        or sampling_rate != 16000
        or stt_audio.ndim != 1
        or stt_audio.size == 0
        or not np.isfinite(stt_audio).all()
    ):
        raise ValueError("discord_stt_audio_invalid")
    return await run_stt_client_operation_with_cancellation_drain(
        transcribe_audio16k_via_service,
        stt_audio,
        service_url=service_url,
        timeout_sec=timeout_sec,
        sampling_rate=sampling_rate,
        max_new_tokens=max_new_tokens,
        stage="discord-completed",
        language=language,
    )


def start_stt_stream_via_service(
    *,
    service_url: str,
    timeout_sec: float,
    language: str | None = "Korean",
) -> dict[str, Any]:
    payload: dict[str, Any] = {
        "sampling_rate": 16000,
        "decoder_profile": "realtime-ko",
        "context_terms": [],
    }
    if language:
        payload["language"] = language
    req = request.Request(
        f"{service_url.rstrip('/')}/v1/stt/streams",
        data=json.dumps(payload).encode("utf-8"),
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    return _json_request(req, timeout_sec=timeout_sec)


def push_stt_stream_chunk_via_service(
    pcm16: bytes | np.ndarray,
    *,
    service_url: str,
    stream_id: str,
    sequence: int,
    timeout_sec: float,
) -> dict[str, Any]:
    if isinstance(pcm16, np.ndarray):
        audio = np.asarray(pcm16)
        if audio.ndim != 1 or audio.dtype != np.int16:
            raise ValueError("pcm16 must be a one-dimensional int16 array")
        body = audio.astype("<i2", copy=False).tobytes()
    else:
        body = bytes(pcm16)
    if not body or len(body) % 2:
        raise ValueError("pcm16 must contain complete signed 16-bit samples")
    if int(sequence) < 0:
        raise ValueError("sequence must be non-negative")
    req = request.Request(
        _stream_url(service_url, stream_id, "/chunks"),
        data=body,
        headers={
            "Content-Type": "application/octet-stream",
            "X-Audio-Sequence": str(int(sequence)),
        },
        method="POST",
    )
    return _json_request(req, timeout_sec=timeout_sec)


def finish_stt_stream_via_service(
    *,
    service_url: str,
    stream_id: str,
    timeout_sec: float,
) -> dict[str, Any]:
    req = request.Request(
        _stream_url(service_url, stream_id, "/finish"),
        data=b"",
        method="POST",
    )
    return _json_request(req, timeout_sec=timeout_sec)


def cancel_stt_stream_via_service(
    *,
    service_url: str,
    stream_id: str,
    timeout_sec: float,
) -> dict[str, Any]:
    req = request.Request(
        _stream_url(service_url, stream_id),
        method="DELETE",
    )
    return _json_request(req, timeout_sec=timeout_sec)


async def run_stt_client_operation_with_cancellation_drain(
    operation: Callable[..., Any],
    *args: Any,
    **kwargs: Any,
) -> Any:
    """Keep a started STT request owned until its physical thread exits."""

    worker = asyncio.create_task(asyncio.to_thread(operation, *args, **kwargs))
    try:
        return await asyncio.shield(worker)
    except asyncio.CancelledError as cancellation:
        while not worker.done():
            try:
                await asyncio.shield(worker)
            except asyncio.CancelledError:
                continue
            except BaseException:
                break
        if not worker.cancelled():
            try:
                worker.result()
            except BaseException:
                pass
        raise cancellation


async def start_stt_stream_with_cleanup(
    *,
    service_url: str,
    timeout_sec: float,
    language: str | None,
    start_stream: Callable[..., dict[str, Any]],
    cancel_stream: Callable[..., dict[str, Any]],
) -> dict[str, Any]:
    """Preserve and cancel a remote stream created during caller cancellation."""

    start_task = asyncio.create_task(
        asyncio.to_thread(
            start_stream,
            service_url=service_url,
            timeout_sec=timeout_sec,
            language=language,
        ),
        name="stt-stream-start",
    )
    try:
        return await asyncio.shield(start_task)
    except asyncio.CancelledError as cancellation:
        while not start_task.done():
            try:
                await asyncio.shield(start_task)
            except asyncio.CancelledError:
                continue
            except BaseException:
                break

        started: Any = None
        if not start_task.cancelled():
            try:
                started = start_task.result()
            except BaseException:
                pass
        stream_id = (
            started.get("streamId")
            if isinstance(started, dict)
            else None
        )
        if isinstance(stream_id, str) and stream_id.strip():
            cleanup_task = asyncio.create_task(
                asyncio.to_thread(
                    cancel_stream,
                    service_url=service_url,
                    stream_id=stream_id,
                    timeout_sec=min(max(1.0, timeout_sec), 3.0),
                ),
                name="stt-stream-start-cancel",
            )
            while not cleanup_task.done():
                try:
                    await asyncio.shield(cleanup_task)
                except asyncio.CancelledError:
                    continue
                except BaseException:
                    break
            if not cleanup_task.cancelled():
                try:
                    cleanup_task.result()
                except BaseException:
                    pass
        raise cancellation
