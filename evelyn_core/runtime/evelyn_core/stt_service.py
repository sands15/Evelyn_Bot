from __future__ import annotations

import base64
import os
import time
from typing import Any

import numpy as np
import torch
import uvicorn
from fastapi import FastAPI, HTTPException
from pydantic import BaseModel

from .audio import resample_audio_float
from .text import clean_text

try:
    from qwen_asr import Qwen3ASRModel
except Exception as exc:  # noqa: BLE001 - surfaced through /health and startup logs.
    Qwen3ASRModel = None
    QWEN_ASR_IMPORT_ERROR = exc
else:
    QWEN_ASR_IMPORT_ERROR = None


STT_MODEL_NAME = os.getenv("STT_MODEL_NAME", "Qwen/Qwen3-ASR-1.7B")
STT_LANGUAGE = os.getenv("STT_LANGUAGE", "ko")
STT_FORCE_LANGUAGE = os.getenv("STT_FORCE_LANGUAGE", "true").lower() in {"1", "true", "yes", "on"}
STT_COMPUTE_TYPE = os.getenv("STT_COMPUTE_TYPE", "float16")
STT_HOST = os.getenv("STT_HOST", "127.0.0.1")
STT_PORT = int(os.getenv("STT_PORT", "8892"))
STT_LOAD_ON_START = os.getenv("STT_LOAD_ON_START", "true").lower() in {"1", "true", "yes", "on"}
STT_MAX_AUDIO_SEC = max(1.0, float(os.getenv("STT_MAX_AUDIO_SEC", "30")))
TARGET_RATE = 16000

app = FastAPI(title="Evelyn STT Service", version="0.1")
_model: Any | None = None
_loaded_at: float | None = None


class TranscribeRequest(BaseModel):
    audio_f32_base64: str
    sample_count: int
    sampling_rate: int = TARGET_RATE
    max_new_tokens: int = 256
    stage: str = "full"
    language: str | None = None


def resolve_torch_dtype() -> torch.dtype:
    value = str(STT_COMPUTE_TYPE).strip().lower()
    mapping = {
        "float16": torch.float16,
        "fp16": torch.float16,
        "half": torch.float16,
        "bfloat16": torch.bfloat16,
        "bf16": torch.bfloat16,
        "float32": torch.float32,
        "fp32": torch.float32,
        "float": torch.float32,
    }
    return mapping.get(value, torch.float32)


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


def get_model() -> Any:
    global _model, _loaded_at
    if _model is not None:
        return _model
    if Qwen3ASRModel is None:
        detail = f"{type(QWEN_ASR_IMPORT_ERROR).__name__}: {QWEN_ASR_IMPORT_ERROR}" if QWEN_ASR_IMPORT_ERROR else "unknown"
        raise RuntimeError(f"qwen-asr import failed: {detail}")

    device = "cuda:0" if torch.cuda.is_available() else "cpu"
    load_kwargs: dict[str, Any] = {
        "dtype": resolve_torch_dtype(),
        "device_map": device,
        "max_inference_batch_size": 1,
        "max_new_tokens": 256,
    }
    token = os.getenv("HF_TOKEN")
    if token:
        load_kwargs["token"] = token

    print(f"[STT SERVICE LOAD] start model={STT_MODEL_NAME} device={device} dtype={load_kwargs['dtype']}", flush=True)
    _model = Qwen3ASRModel.from_pretrained(STT_MODEL_NAME, **load_kwargs)
    _loaded_at = time.time()
    print(f"[STT SERVICE LOAD] done model={STT_MODEL_NAME} gpu={gpu_snapshot()}", flush=True)
    return _model


def decode_audio(request_payload: TranscribeRequest) -> np.ndarray:
    try:
        raw = base64.b64decode(request_payload.audio_f32_base64, validate=True)
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(status_code=400, detail=f"invalid audio_f32_base64: {exc}") from exc

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
    return {
        "ok": Qwen3ASRModel is not None,
        "ready": _model is not None,
        "model": STT_MODEL_NAME,
        "loadedAt": _loaded_at,
        "loadOnStart": STT_LOAD_ON_START,
        "gpu": gpu_snapshot(),
        "importError": None if QWEN_ASR_IMPORT_ERROR is None else repr(QWEN_ASR_IMPORT_ERROR),
    }


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
    results = model.transcribe(
        audio=(audio, sampling_rate),
        language=language,
        return_time_stamps=False,
    )
    text = clean_text(getattr(results[0], "text", "") if results else "")
    duration_ms = (time.monotonic() - started) * 1000.0
    print(f"[STT SERVICE DONE][{payload.stage}] sec={audio.size / float(sampling_rate):.2f} text={text!r}", flush=True)
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
