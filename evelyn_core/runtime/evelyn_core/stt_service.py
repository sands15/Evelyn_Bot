from __future__ import annotations

import base64
import secrets
import time
from dataclasses import dataclass
from importlib.util import find_spec
from threading import Lock, Timer
from typing import Any

import numpy as np
import torch
import uvicorn
from fastapi import Body, FastAPI, Header, HTTPException
from pydantic import BaseModel, ConfigDict, Field

from .audio import resample_audio_float
from .runtime_config_schema import STT_SERVICE_SETTINGS, load_runtime_settings
from .runtime_error_observability import RuntimeErrorCounter
from .text import clean_text

_STT_CONFIG = load_runtime_settings("stt", STT_SERVICE_SETTINGS)
_RUNTIME_ERRORS = RuntimeErrorCounter()

try:
    from qwen_asr import Qwen3ASRModel
except Exception as exc:  # noqa: BLE001 - surfaced through /health and startup logs.
    Qwen3ASRModel = None
    QWEN_ASR_IMPORT_ERROR = exc
    _RUNTIME_ERRORS.record("stt_import_failed", exc)
else:
    QWEN_ASR_IMPORT_ERROR = None


STT_MODEL_NAME = str(_STT_CONFIG["STT_MODEL_NAME"])
STT_LANGUAGE = str(_STT_CONFIG["STT_LANGUAGE"])
STT_FORCE_LANGUAGE = bool(_STT_CONFIG["STT_FORCE_LANGUAGE"])
STT_COMPUTE_TYPE = str(_STT_CONFIG["STT_COMPUTE_TYPE"])
STT_HOST = str(_STT_CONFIG["STT_HOST"])
STT_PORT = int(_STT_CONFIG["STT_PORT"])
STT_LOAD_ON_START = bool(_STT_CONFIG["STT_LOAD_ON_START"])
STT_MAX_AUDIO_SEC = float(_STT_CONFIG["STT_MAX_AUDIO_SEC"])
TARGET_RATE = 16000
STREAM_TTL_SEC = 60.0
STREAM_MAX_SESSIONS = 4
STREAM_DECODER_PROFILE = "realtime-ko"
STT_VLLM_GPU_MEMORY_UTILIZATION = float(
    _STT_CONFIG.get("STT_VLLM_GPU_MEMORY_UTILIZATION", 0.35)
)
STT_VLLM_MAX_MODEL_LEN = 8192
STT_VLLM_MAX_NUM_SEQS = 1
STT_VLLM_AUDIO_PER_PROMPT = 1
_EXPECTED_VLLM_ENGINE_CONFIGURATION = {
    "maxModelLen": STT_VLLM_MAX_MODEL_LEN,
    "gpuMemoryUtilization": STT_VLLM_GPU_MEMORY_UTILIZATION,
    "maxNumSeqs": STT_VLLM_MAX_NUM_SEQS,
    "audioPerPrompt": STT_VLLM_AUDIO_PER_PROMPT,
}

app = FastAPI(title="Evelyn STT Service", version="0.1")
_model: Any | None = None
_loaded_at: float | None = None
_engine_configuration: dict[str, int | float] | None = None
_model_lock = Lock()
_inference_lock = Lock()


@dataclass
class _StreamSession:
    state: Any
    expires_at: float
    timer: Timer | None = None
    next_sequence: int = 0
    sample_count: int = 0
    revision: int = 0
    busy: bool = False


_streams: dict[str, _StreamSession] = {}
_streams_lock = Lock()


class TranscribeRequest(BaseModel):
    audio_f32_base64: str
    sample_count: int
    sampling_rate: int = TARGET_RATE
    max_new_tokens: int = 256
    stage: str = "full"
    language: str | None = None
    validation_bound: bool = False


class StreamStartRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    sampling_rate: int = TARGET_RATE
    language: str | None = "Korean"
    decoder_profile: str = STREAM_DECODER_PROFILE
    context_terms: list[str] = Field(default_factory=list, max_length=24)


def validation_text_for_log(value: Any, *, validation_bound: bool) -> Any:
    label = "validation-text" if validation_bound else "transcript"
    return f"<{label} chars={len(str(value or ''))}>"


def normalize_language(language: str | None = None) -> str | None:
    value = str(language if language is not None else STT_LANGUAGE).strip()
    if not value:
        return None
    aliases = {
        "korean": "Korean",
        "kor": "Korean",
        "kr": "Korean",
        "ko": "Korean",
        "ko-kr": "Korean",
        "ko_kr": "Korean",
        "english": "English",
        "en": "English",
        "chinese": "Chinese",
        "zh": "Chinese",
        "japanese": "Japanese",
        "ja": "Japanese",
    }
    return aliases.get(value.lower(), value)


def gpu_snapshot() -> dict[str, Any]:
    if not torch.cuda.is_available():
        return {"cuda": False}
    index = torch.cuda.current_device()
    props = torch.cuda.get_device_properties(index)
    free, total = torch.cuda.mem_get_info(index)
    return {
        "cuda": True,
        "device": f"cuda:{index}",
        "name": props.name,
        "total_mb": round(total / 1024 / 1024),
        "free_mb": round(free / 1024 / 1024),
        "used_mb": round((total - free) / 1024 / 1024),
        "allocated_mb": round(torch.cuda.memory_allocated(index) / 1024 / 1024),
        "reserved_mb": round(torch.cuda.memory_reserved(index) / 1024 / 1024),
    }


def _read_vllm_engine_configuration(model: Any) -> dict[str, int | float]:
    configuration = model.model.llm_engine.vllm_config
    multimodal = configuration.model_config.get_multimodal_config()
    return {
        "maxModelLen": int(configuration.model_config.max_model_len),
        "gpuMemoryUtilization": float(
            configuration.cache_config.gpu_memory_utilization
        ),
        "maxNumSeqs": int(configuration.scheduler_config.max_num_seqs),
        "audioPerPrompt": int(multimodal.get_limit_per_prompt("audio")),
    }


def get_model() -> Any:
    global _model, _loaded_at, _engine_configuration
    if _model is not None:
        return _model
    with _model_lock:
        if _model is not None:
            return _model
        if Qwen3ASRModel is None:
            detail = f"{type(QWEN_ASR_IMPORT_ERROR).__name__}: {QWEN_ASR_IMPORT_ERROR}" if QWEN_ASR_IMPORT_ERROR else "unknown"
            raise RuntimeError(f"qwen-asr import failed: {detail}")

        load_kwargs: dict[str, Any] = {
            "dtype": STT_COMPUTE_TYPE,
            "gpu_memory_utilization": STT_VLLM_GPU_MEMORY_UTILIZATION,
        }
        token = str(_STT_CONFIG["HF_TOKEN"] or "")
        if token:
            load_kwargs["hf_token"] = token

        print(f"[STT SERVICE LOAD] start model={STT_MODEL_NAME} backend=vllm", flush=True)
        try:
            candidate = Qwen3ASRModel.LLM(
                model=STT_MODEL_NAME,
                max_inference_batch_size=1,
                max_new_tokens=256,
                max_model_len=STT_VLLM_MAX_MODEL_LEN,
                max_num_seqs=STT_VLLM_MAX_NUM_SEQS,
                limit_mm_per_prompt={"audio": STT_VLLM_AUDIO_PER_PROMPT},
                **load_kwargs,
            )
            observed_configuration = _read_vllm_engine_configuration(candidate)
            if observed_configuration != _EXPECTED_VLLM_ENGINE_CONFIGURATION:
                raise RuntimeError("stt_vllm_engine_contract_mismatch")
        except Exception as exc:
            _RUNTIME_ERRORS.record("stt_model_load_failed", exc)
            raise
        _model = candidate
        _engine_configuration = observed_configuration
        _loaded_at = time.time()
        print(f"[STT SERVICE LOAD] done model={STT_MODEL_NAME} gpu={gpu_snapshot()}", flush=True)
        return _model


def _remove_stream_locked(stream_id: str) -> _StreamSession | None:
    session = _streams.pop(stream_id, None)
    if session is not None and session.timer is not None:
        session.timer.cancel()
        session.timer = None
    return session


def _expire_stream(stream_id: str, deadline: float) -> None:
    with _streams_lock:
        session = _streams.get(stream_id)
        if session is not None and session.expires_at == deadline and time.monotonic() >= deadline:
            _remove_stream_locked(stream_id)


def _renew_stream_locked(stream_id: str, session: _StreamSession) -> None:
    if session.timer is not None:
        session.timer.cancel()
    session.expires_at = time.monotonic() + STREAM_TTL_SEC
    session.timer = Timer(STREAM_TTL_SEC, _expire_stream, args=(stream_id, session.expires_at))
    session.timer.daemon = True
    session.timer.start()


def _purge_expired_streams(now: float | None = None) -> None:
    cutoff = time.monotonic() if now is None else float(now)
    with _streams_lock:
        for stream_id, session in list(_streams.items()):
            if session.expires_at <= cutoff:
                _remove_stream_locked(stream_id)


def _stream_response(session: _StreamSession, *, is_final: bool) -> dict[str, Any]:
    return {
        "revision": session.revision,
        "text": clean_text(getattr(session.state, "text", "")),
        "isFinal": bool(is_final),
    }


def decode_audio(request_payload: TranscribeRequest) -> np.ndarray:
    try:
        raw = base64.b64decode(request_payload.audio_f32_base64, validate=True)
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(status_code=400, detail="invalid_audio_f32_base64") from exc

    audio = np.frombuffer(raw, dtype=np.float32)
    if int(request_payload.sample_count) != int(audio.size):
        raise HTTPException(status_code=400, detail="sample_count does not match audio payload")

    max_samples = int(max(1.0, STT_MAX_AUDIO_SEC) * max(1, int(request_payload.sampling_rate)))
    if audio.size > max_samples:
        raise HTTPException(status_code=413, detail=f"audio too long: {audio.size} samples")
    return np.asarray(audio, dtype=np.float32)


@app.on_event("startup")
def startup() -> None:
    if STT_LOAD_ON_START:
        get_model()


@app.get("/health")
def health() -> dict[str, Any]:
    ready = (
        _model is not None
        and _engine_configuration == _EXPECTED_VLLM_ENGINE_CONFIGURATION
    )
    return {
        "ok": Qwen3ASRModel is not None and find_spec("vllm") is not None,
        "ready": ready,
        "model": STT_MODEL_NAME,
        "backend": "vllm",
        "maxAudioSec": STT_MAX_AUDIO_SEC,
        "engine": (
            None
            if _engine_configuration is None
            else dict(_engine_configuration)
        ),
        "loadedAt": _loaded_at,
        "loadOnStart": STT_LOAD_ON_START,
        "gpu": gpu_snapshot(),
        "importErrorType": (
            None
            if QWEN_ASR_IMPORT_ERROR is None
            else type(QWEN_ASR_IMPORT_ERROR).__name__
        ),
        "configuration": _STT_CONFIG.public_summary(),
        **_RUNTIME_ERRORS.snapshot(),
    }


@app.post("/v1/stt/streams", status_code=201)
def start_stream(payload: StreamStartRequest) -> dict[str, Any]:
    if int(payload.sampling_rate) != TARGET_RATE:
        raise HTTPException(status_code=400, detail="stream_requires_16khz")
    if payload.decoder_profile != STREAM_DECODER_PROFILE:
        raise HTTPException(status_code=400, detail="unsupported_decoder_profile")
    if payload.context_terms:
        raise HTTPException(status_code=400, detail="context_terms_disabled")

    _purge_expired_streams()
    with _streams_lock:
        if len(_streams) >= STREAM_MAX_SESSIONS:
            raise HTTPException(status_code=503, detail="stream_capacity_reached")

    language = normalize_language(payload.language) if (payload.language or STT_FORCE_LANGUAGE) else None
    try:
        model = get_model()
        with _inference_lock:
            state = model.init_streaming_state(context="", language=language, chunk_size_sec=2.0)
    except Exception as exc:
        _RUNTIME_ERRORS.record("stt_transcribe_failed", exc)
        raise HTTPException(status_code=503, detail="streaming_backend_unavailable") from exc

    stream_id = secrets.token_urlsafe(18)
    session = _StreamSession(state=state, expires_at=0.0)
    with _streams_lock:
        if len(_streams) >= STREAM_MAX_SESSIONS:
            raise HTTPException(status_code=503, detail="stream_capacity_reached")
        _streams[stream_id] = session
        _renew_stream_locked(stream_id, session)
    return {
        "streamId": stream_id,
        "samplingRate": TARGET_RATE,
        "decoderProfile": STREAM_DECODER_PROFILE,
        "nextSequence": 0,
    }


@app.post("/v1/stt/streams/{stream_id}/chunks")
def push_stream_chunk(
    stream_id: str,
    pcm16: bytes = Body(..., media_type="application/octet-stream"),
    sequence: int = Header(..., alias="X-Audio-Sequence"),
) -> dict[str, Any]:
    if not pcm16 or len(pcm16) % 2:
        raise HTTPException(status_code=400, detail="invalid_pcm16_payload")

    _purge_expired_streams()
    with _streams_lock:
        session = _streams.get(stream_id)
        if session is None:
            raise HTTPException(status_code=404, detail="stream_not_found")
        if session.busy:
            raise HTTPException(status_code=409, detail="stream_busy")
        if int(sequence) != session.next_sequence:
            raise HTTPException(status_code=409, detail="stream_sequence_mismatch")
        sample_count = len(pcm16) // 2
        max_samples = int(max(1.0, STT_MAX_AUDIO_SEC) * TARGET_RATE)
        if session.sample_count + sample_count > max_samples:
            _remove_stream_locked(stream_id)
            raise HTTPException(status_code=413, detail="stream_audio_too_long")
        session.busy = True
        if session.timer is not None:
            session.timer.cancel()
            session.timer = None

    audio = np.frombuffer(pcm16, dtype="<i2")
    try:
        with _inference_lock:
            with _streams_lock:
                if _streams.get(stream_id) is not session:
                    raise HTTPException(status_code=410, detail="stream_cancelled")
            session.state = get_model().streaming_transcribe(audio, session.state)
    except HTTPException:
        raise
    except Exception as exc:
        with _streams_lock:
            if _streams.get(stream_id) is session:
                _remove_stream_locked(stream_id)
        _RUNTIME_ERRORS.record("stt_transcribe_failed", exc)
        raise HTTPException(status_code=503, detail="stream_decode_failed") from exc

    with _streams_lock:
        if _streams.get(stream_id) is not session:
            raise HTTPException(status_code=410, detail="stream_cancelled")
        session.sample_count += sample_count
        session.next_sequence += 1
        session.revision += 1
        session.busy = False
        _renew_stream_locked(stream_id, session)
        return _stream_response(session, is_final=False)


@app.post("/v1/stt/streams/{stream_id}/finish")
def finish_stream(stream_id: str) -> dict[str, Any]:
    _purge_expired_streams()
    with _streams_lock:
        session = _streams.get(stream_id)
        if session is None:
            raise HTTPException(status_code=404, detail="stream_not_found")
        if session.busy:
            raise HTTPException(status_code=409, detail="stream_busy")
        session.busy = True
        if session.timer is not None:
            session.timer.cancel()
            session.timer = None

    try:
        with _inference_lock:
            with _streams_lock:
                if _streams.get(stream_id) is not session:
                    raise HTTPException(status_code=410, detail="stream_cancelled")
            session.state = get_model().finish_streaming_transcribe(session.state)
    except HTTPException:
        raise
    except Exception as exc:
        with _streams_lock:
            if _streams.get(stream_id) is session:
                _remove_stream_locked(stream_id)
        _RUNTIME_ERRORS.record("stt_transcribe_failed", exc)
        raise HTTPException(status_code=503, detail="stream_finish_failed") from exc
    with _streams_lock:
        if _streams.get(stream_id) is not session:
            raise HTTPException(status_code=410, detail="stream_cancelled")
        session.revision += 1
        response = _stream_response(session, is_final=True)
        _remove_stream_locked(stream_id)
        return response


@app.delete("/v1/stt/streams/{stream_id}")
def cancel_stream(stream_id: str) -> dict[str, bool]:
    with _streams_lock:
        if _remove_stream_locked(stream_id) is None:
            raise HTTPException(status_code=404, detail="stream_not_found")
    return {"cancelled": True}


@app.post("/v1/stt/transcribe")
def transcribe(payload: TranscribeRequest) -> dict[str, Any]:
    started = time.monotonic()
    audio = decode_audio(payload)
    sampling_rate = max(1, int(payload.sampling_rate))
    if sampling_rate != TARGET_RATE:
        audio = resample_audio_float(audio, sampling_rate, TARGET_RATE)
        sampling_rate = TARGET_RATE

    if audio.size == 0:
        return {"text": "", "durationMs": 0.0, "model": STT_MODEL_NAME, "stage": payload.stage}

    model = get_model()
    language = normalize_language(payload.language) if (payload.language or STT_FORCE_LANGUAGE) else None
    try:
        with _inference_lock:
            results = model.transcribe(
                audio=(audio, sampling_rate),
                language=language,
                return_time_stamps=False,
            )
    except Exception as exc:
        _RUNTIME_ERRORS.record("stt_transcribe_failed", exc)
        raise
    text = clean_text(getattr(results[0], "text", "") if results else "")
    duration_ms = (time.monotonic() - started) * 1000.0
    print(
        f"[STT SERVICE DONE][{payload.stage}] "
        f"sec={audio.size / float(sampling_rate):.2f} "
        f"text={validation_text_for_log(text, validation_bound=payload.validation_bound)!r}",
        flush=True,
    )
    return {
        "text": text,
        "durationMs": round(duration_ms, 1),
        "model": STT_MODEL_NAME,
        "stage": payload.stage,
        "language": language,
        "sampleCount": int(audio.size),
        "samplingRate": sampling_rate,
    }


def main() -> None:
    uvicorn.run(app, host=STT_HOST, port=STT_PORT)


if __name__ == "__main__":
    main()
