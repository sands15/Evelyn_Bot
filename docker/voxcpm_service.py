from __future__ import annotations

import json
import os
import random
import re
import threading
from pathlib import Path
from typing import Iterator

import numpy as np
import soxr
import torch
from fastapi import FastAPI, HTTPException
from fastapi.responses import Response, StreamingResponse
from pydantic import BaseModel, ConfigDict, Field
from voxcpm import VoxCPM


MODEL_ID = os.getenv("VOXCPM_MODEL_ID", "openbmb/VoxCPM2")
DEVICE = os.getenv("VOXCPM_DEVICE", "cuda")
REFERENCE_AUDIO = Path(
    os.getenv("VOXCPM_REFERENCE_AUDIO", "/app/profiles/evelyn/ref_audio.wav")
)
REFERENCE_META = Path(
    os.getenv("VOXCPM_REFERENCE_META", "/app/profiles/evelyn/meta.json")
)
CFG_VALUE = float(os.getenv("VOXCPM_CFG_VALUE", "2.0"))
DEFAULT_STEPS = int(os.getenv("VOXCPM_INFERENCE_STEPS", "10"))
SEED = int(os.getenv("VOXCPM_SEED", "42"))
OUTPUT_RATE = int(os.getenv("VOXCPM_OUTPUT_RATE", "24000"))
SUPPORTED_VOICES = {"auto", "evelyn", "clone:evelyn"}
TAG_PATTERN = re.compile(r"\[[^\]\r\n]{1,48}\]")


class SpeechRequest(BaseModel):
    model_config = ConfigDict(extra="ignore")

    model: str = "voxcpm2"
    input: str = Field(min_length=1, max_length=4096)
    voice: str = "clone:evelyn"
    response_format: str = "pcm"
    stream: bool = True
    num_step: int | None = None
    speed: float = 1.0
    language: str | None = None


def _load_reference_text() -> str:
    if not REFERENCE_AUDIO.is_file():
        raise RuntimeError(f"VoxCPM reference audio not found: {REFERENCE_AUDIO}")
    if not REFERENCE_META.is_file():
        raise RuntimeError(f"VoxCPM reference metadata not found: {REFERENCE_META}")
    payload = json.loads(REFERENCE_META.read_text(encoding="utf-8"))
    text = str(payload.get("ref_text") or "").strip()
    if not text:
        raise RuntimeError(f"VoxCPM reference transcript is empty: {REFERENCE_META}")
    return text


def _seed_generation() -> None:
    random.seed(SEED)
    np.random.seed(SEED)
    torch.manual_seed(SEED)
    if torch.cuda.is_available():
        torch.cuda.manual_seed_all(SEED)


def _clean_input(text: str) -> str:
    text = TAG_PATTERN.sub(" ", text)
    return re.sub(r"\s+", " ", text).strip()


PROMPT_TEXT = _load_reference_text()
if DEVICE.startswith("cuda") and not torch.cuda.is_available():
    raise RuntimeError("VoxCPM CUDA device requested but CUDA is unavailable")

MODEL = VoxCPM.from_pretrained(
    MODEL_ID,
    device=DEVICE,
    load_denoiser=False,
    optimize=True,
)
MODEL_RATE = int(MODEL.tts_model.sample_rate)
GENERATION_LOCK = threading.Lock()

app = FastAPI(title="Evelyn VoxCPM2 TTS", version="1.0.0")


def _pcm_chunks(text: str, steps: int) -> Iterator[bytes]:
    with GENERATION_LOCK:
        _seed_generation()
        resampler = soxr.ResampleStream(
            MODEL_RATE,
            OUTPUT_RATE,
            1,
            dtype="float32",
            quality="HQ",
        )
        generator = MODEL.generate_streaming(
            text=text,
            prompt_wav_path=str(REFERENCE_AUDIO),
            prompt_text=PROMPT_TEXT,
            reference_wav_path=str(REFERENCE_AUDIO),
            cfg_value=CFG_VALUE,
            inference_timesteps=steps,
            normalize=True,
            denoise=False,
            retry_badcase=True,
            retry_badcase_max_times=3,
        )
        try:
            for chunk in generator:
                source = np.asarray(chunk, dtype=np.float32).reshape(-1)
                if source.size == 0:
                    continue
                output = resampler.resample_chunk(source, last=False)
                if output.size:
                    pcm = (np.clip(output, -1.0, 1.0) * 32767.0).astype("<i2")
                    yield pcm.tobytes()
            tail = resampler.resample_chunk(np.empty(0, dtype=np.float32), last=True)
            if tail.size:
                pcm = (np.clip(tail, -1.0, 1.0) * 32767.0).astype("<i2")
                yield pcm.tobytes()
        finally:
            generator.close()


@app.get("/health")
def health() -> dict[str, object]:
    gpu = torch.cuda.get_device_name(0) if torch.cuda.is_available() else None
    return {
        "ok": True,
        "ready": True,
        "backend": "voxcpm2",
        "model": MODEL_ID,
        "device": DEVICE,
        "gpu": gpu,
        "voice": "clone:evelyn",
        "streaming": True,
        "model_sample_rate": MODEL_RATE,
        "output_sample_rate": OUTPUT_RATE,
        "inference_steps": DEFAULT_STEPS,
        "cfg_value": CFG_VALUE,
    }


@app.post("/v1/audio/speech")
def create_speech(request: SpeechRequest):
    if request.response_format.lower() != "pcm":
        raise HTTPException(status_code=400, detail="Only response_format=pcm is supported")
    if request.voice.lower() not in SUPPORTED_VOICES:
        raise HTTPException(status_code=404, detail=f"Clone profile '{request.voice}' not found")
    text = _clean_input(request.input)
    if not text:
        raise HTTPException(status_code=400, detail="Speech input is empty after normalization")
    model_name = request.model.strip().lower()
    steps = 20 if model_name in {"voxcpm2-hq", "voxcpm2-20"} else DEFAULT_STEPS

    chunks = _pcm_chunks(text, steps)
    headers = {
        "X-TTS-Backend": "voxcpm2",
        "X-TTS-Voice": "clone:evelyn",
        "X-Audio-Sample-Rate": str(OUTPUT_RATE),
        "X-Audio-Channels": "1",
        "X-Audio-Sample-Format": "s16le",
    }
    if request.stream:
        return StreamingResponse(chunks, media_type="audio/pcm", headers=headers)
    return Response(
        content=b"".join(chunks),
        media_type="audio/pcm",
        headers=headers,
    )
