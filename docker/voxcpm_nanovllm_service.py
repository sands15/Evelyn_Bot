from __future__ import annotations

import asyncio
import io
import json
import os
import random
import re
import time
from collections import deque
from contextlib import asynccontextmanager, suppress
from dataclasses import dataclass, field
from pathlib import Path
from typing import AsyncIterator

import numpy as np
import soundfile as sf
import soxr
import torch
from fastapi import FastAPI, HTTPException, WebSocket, WebSocketDisconnect
from fastapi.responses import Response, StreamingResponse
from huggingface_hub import snapshot_download
from nanovllm_voxcpm import VoxCPM
from pydantic import BaseModel, ConfigDict, Field


MODEL_ID = os.getenv("VOXCPM_MODEL_ID", "openbmb/VoxCPM2")
REFERENCE_AUDIO = Path(
    os.getenv("VOXCPM_REFERENCE_AUDIO", "/app/profiles/evelyn/ref_audio.wav")
)
REFERENCE_META = Path(
    os.getenv("VOXCPM_REFERENCE_META", "/app/profiles/evelyn/meta.json")
)
CFG_VALUE = float(os.getenv("VOXCPM_CFG_VALUE", "2.0"))
DEFAULT_STEPS = int(os.getenv("VOXCPM_INFERENCE_STEPS", "20"))
SEED = int(os.getenv("VOXCPM_SEED", "42"))
DEFAULT_CONTROL_INSTRUCTION = os.getenv(
    "VOXCPM_DEFAULT_CONTROL_INSTRUCTION",
    "warm, natural and expressive tone, clear pronunciation, stable pace",
).strip()
OUTPUT_RATE = int(os.getenv("VOXCPM_OUTPUT_RATE", "24000"))
GPU_MEMORY_UTILIZATION = float(os.getenv("VOXCPM_GPU_MEMORY_UTILIZATION", "0.90"))
MAX_MODEL_LEN = int(os.getenv("VOXCPM_MAX_MODEL_LEN", "4096"))
MAX_BATCHED_TOKENS = int(os.getenv("VOXCPM_MAX_BATCHED_TOKENS", "4096"))
MAX_SEQS = int(os.getenv("VOXCPM_MAX_SEQS", "2"))
CONTINUATION_SEGMENTS = max(1, int(os.getenv("VOXCPM_CONTINUATION_SEGMENTS", "2")))
SHORT_FULL_DECODE_MAX_CHARS = max(
    1,
    int(os.getenv("VOXCPM_SHORT_FULL_DECODE_MAX_CHARS", "32")),
)
SHORT_QUALITY_RETRIES = max(
    0,
    int(os.getenv("VOXCPM_SHORT_QUALITY_RETRIES", "1")),
)
SHORT_TARGET_RMS = max(
    0.001,
    float(os.getenv("VOXCPM_SHORT_TARGET_RMS", "0.032")),
)
SHORT_MAX_GAIN = max(
    1.0,
    float(os.getenv("VOXCPM_SHORT_MAX_GAIN", "2.5")),
)
SHORT_MAX_ROUGHNESS = max(
    1.0,
    float(os.getenv("VOXCPM_SHORT_MAX_ROUGHNESS", "3.5")),
)
STARTUP_WARMUP = os.getenv("VOXCPM_STARTUP_WARMUP", "true").strip().lower() in {
    "1",
    "true",
    "yes",
    "on",
}
DEFAULT_CONTINUATION_SOURCE = os.getenv(
    "VOXCPM_CONTINUATION_SOURCE",
    "model_exact_no_terminal",
).strip().lower()
CONTINUATION_SOURCES = {
    "model_exact",
    "model_exact_no_terminal",
    "waveform_reencode",
}
SUPPORTED_VOICES = {"auto", "evelyn", "clone:evelyn"}
TAG_PATTERN = re.compile(r"\[[^\]\r\n]{1,48}\]")
CONTROL_INSTRUCTION_PATTERN = re.compile(r"^\s*[\(（][^()（）\r\n]{1,240}[\)）]")
STRONG_BOUNDARY_PATTERN = re.compile(r"[.!?。！？…]+[\"'”’)]*\s*|[\r\n]+")
CLAUSE_BOUNDARY_PATTERN = re.compile(r"[,，、;；:：]\s*")


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


@dataclass
class CommitWindow:
    min_chars: int
    target_chars: int
    max_chars: int


@dataclass
class IncrementalTextCommitter:
    first_window: CommitWindow = field(default_factory=lambda: CommitWindow(10, 16, 28))
    next_window: CommitWindow = field(default_factory=lambda: CommitWindow(24, 48, 80))
    buffer: str = ""
    committed_count: int = 0

    def append(self, fragment: str) -> list[str]:
        self.buffer += fragment
        ready: list[str] = []
        while True:
            chunk = self._next_chunk()
            if chunk is None:
                return ready
            ready.append(chunk)

    def flush(self) -> list[str]:
        tail = self.buffer.strip()
        self.buffer = ""
        if not tail:
            return []
        self.committed_count += 1
        return [tail]

    def _next_chunk(self) -> str | None:
        window = self.first_window if self.committed_count == 0 else self.next_window
        visible = self.buffer.strip()
        if len(visible) < window.min_chars:
            return None

        leading = len(self.buffer) - len(self.buffer.lstrip())
        search_text = self.buffer[leading:]
        strong_end = self._first_boundary_end(
            search_text,
            STRONG_BOUNDARY_PATTERN,
            window.min_chars,
            window.max_chars,
        )
        if strong_end is not None:
            return self._commit(leading + strong_end)

        if len(search_text) < window.target_chars:
            return None

        clause_end = self._last_boundary_end(
            search_text,
            CLAUSE_BOUNDARY_PATTERN,
            window.min_chars,
            window.max_chars,
        )
        if clause_end is not None:
            return self._commit(leading + clause_end)

        whitespace_end = self._last_whitespace_end(
            search_text,
            window.min_chars,
            min(len(search_text), window.max_chars),
        )
        if whitespace_end is not None:
            return self._commit(leading + whitespace_end)

        if len(search_text) <= window.max_chars:
            return None

        hard_whitespace_end = self._last_whitespace_end(
            search_text,
            window.min_chars,
            min(len(search_text), window.max_chars + 40),
        )
        if hard_whitespace_end is not None:
            return self._commit(leading + hard_whitespace_end)
        return None

    def _commit(self, end: int) -> str:
        chunk = self.buffer[:end].strip()
        self.buffer = self.buffer[end:].lstrip()
        if not chunk:
            raise RuntimeError("Incremental text committer produced an empty chunk")
        self.committed_count += 1
        return chunk

    @staticmethod
    def _first_boundary_end(
        text: str,
        pattern: re.Pattern[str],
        minimum: int,
        maximum: int,
    ) -> int | None:
        for match in pattern.finditer(text):
            if minimum <= match.end() <= maximum:
                return match.end()
        return None

    @staticmethod
    def _last_boundary_end(
        text: str,
        pattern: re.Pattern[str],
        minimum: int,
        maximum: int,
    ) -> int | None:
        candidates = [
            match.end()
            for match in pattern.finditer(text[:maximum])
            if match.end() >= minimum
        ]
        return candidates[-1] if candidates else None

    @staticmethod
    def _last_whitespace_end(text: str, minimum: int, maximum: int) -> int | None:
        candidates = [
            match.end()
            for match in re.finditer(r"\s+", text[:maximum])
            if match.end() >= minimum
        ]
        return candidates[-1] if candidates else None


@dataclass
class LeadingSilenceGate:
    sample_rate: int
    threshold: float = 0.0075
    analysis_ms: int = 8
    preroll_ms: int = 35
    max_wait_ms: int = 1800
    pending: np.ndarray = field(default_factory=lambda: np.empty(0, dtype=np.float32))
    opened: bool = False
    trimmed_samples: int = 0

    def process(self, chunk: np.ndarray) -> np.ndarray:
        source = np.asarray(chunk, dtype=np.float32).reshape(-1)
        if self.opened:
            return source
        if source.size:
            self.pending = np.concatenate([self.pending, source])

        frame = max(1, int(self.sample_rate * self.analysis_ms / 1000))
        for start in range(0, max(0, self.pending.size - frame + 1), frame):
            window = self.pending[start : start + frame]
            if float(np.sqrt(np.mean(np.square(window), dtype=np.float64))) >= self.threshold:
                keep_from = max(0, start - int(self.sample_rate * self.preroll_ms / 1000))
                self.trimmed_samples += keep_from
                output = self.pending[keep_from:]
                self.pending = np.empty(0, dtype=np.float32)
                self.opened = True
                return output

        max_wait = int(self.sample_rate * self.max_wait_ms / 1000)
        if self.pending.size >= max_wait:
            self.opened = True
            output = self.pending
            self.pending = np.empty(0, dtype=np.float32)
            return output
        return np.empty(0, dtype=np.float32)

    def flush(self) -> np.ndarray:
        output = self.pending
        self.pending = np.empty(0, dtype=np.float32)
        self.opened = True
        return output


@dataclass
class ContinuationHistory:
    max_segments: int
    entries: deque[tuple[str, bytes]] = field(init=False)

    def __post_init__(self) -> None:
        self.entries = deque(maxlen=self.max_segments)

    def add(self, text: str, latent_bytes: bytes) -> None:
        if text and latent_bytes:
            self.entries.append((text, latent_bytes))

    @property
    def prompt_text(self) -> str:
        return " ".join(text.strip() for text, _ in self.entries if text.strip())

    @property
    def prompt_latents(self) -> bytes | None:
        if not self.entries:
            return None
        return b"".join(latents for _, latents in self.entries)


@dataclass
class RuntimeState:
    server: object
    model_path: str
    model_rate: int
    encoder_rate: int
    feat_dim: int
    patch_size: int
    reference_latents: bytes
    startup_warmup_ms: float | None = None


@dataclass
class BufferedSegment:
    model_audio: np.ndarray
    output_audio: np.ndarray
    latent_patches: list[bytes]
    leading_silence_trimmed_ms: float
    quality: dict[str, float | bool]
    attempts: int


RUNTIME: RuntimeState | None = None


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


def _resolve_model_path(model_id: str) -> str:
    expanded = os.path.expanduser(model_id)
    if os.path.isdir(expanded):
        return expanded
    return snapshot_download(repo_id=model_id)


def _clean_input(text: str) -> str:
    text = TAG_PATTERN.sub(" ", text)
    return re.sub(r"\s+", " ", text).strip()


def _apply_default_control_instruction(text: str) -> str:
    text = text.strip()
    if not text or not DEFAULT_CONTROL_INSTRUCTION:
        return text
    if CONTROL_INSTRUCTION_PATTERN.match(text):
        return text
    return f"({DEFAULT_CONTROL_INSTRUCTION}){text}"


def _max_generate_length(text: str) -> int:
    return min(512, max(48, len(text) * 7 + 24))


def _pcm_bytes(samples: np.ndarray) -> bytes:
    pcm = (np.clip(samples, -1.0, 1.0) * 32767.0).astype("<i2")
    return pcm.tobytes()


def _wave_bytes(samples: np.ndarray, sample_rate: int) -> bytes:
    output = io.BytesIO()
    sf.write(output, np.asarray(samples, dtype=np.float32), sample_rate, format="WAV", subtype="PCM_16")
    return output.getvalue()


def _join_exact_latents(runtime: RuntimeState, patches: list[bytes]) -> bytes:
    if not patches:
        return b""
    patch_bytes = runtime.patch_size * runtime.feat_dim * np.dtype(np.float32).itemsize
    for index, patch in enumerate(patches, start=1):
        if len(patch) != patch_bytes:
            raise RuntimeError(
                "Invalid exact latent patch size "
                f"at chunk {index}: got {len(patch)} bytes, expected {patch_bytes}"
            )
    return b"".join(patches)


def _trim_leading_silence(samples: np.ndarray) -> tuple[np.ndarray, float]:
    gate = LeadingSilenceGate(OUTPUT_RATE)
    audible = gate.process(np.asarray(samples, dtype=np.float32).reshape(-1))
    pending = gate.flush()
    if audible.size and pending.size:
        output = np.concatenate([audible, pending])
    elif audible.size:
        output = audible
    else:
        output = pending
    return output.astype(np.float32, copy=False), gate.trimmed_samples * 1000 / OUTPUT_RATE


def _quality_metrics(samples: np.ndarray) -> dict[str, float | bool]:
    source = np.asarray(samples, dtype=np.float32).reshape(-1)
    if not source.size or not np.all(np.isfinite(source)):
        return {
            "bad": True,
            "peak": 0.0,
            "rms": 0.0,
            "roughness": float("inf"),
            "max_delta": float("inf"),
        }
    peak = float(np.max(np.abs(source)))
    rms = float(np.sqrt(np.mean(np.square(source), dtype=np.float64)))
    if source.size > 1:
        delta = np.abs(np.diff(source))
        max_delta = float(np.max(delta))
        p999_delta = float(np.quantile(delta, 0.999))
    else:
        max_delta = 0.0
        p999_delta = 0.0
    roughness = p999_delta / max(rms, 1e-6)
    bad = (
        peak >= 0.995
        or peak < 0.005
        or rms < 0.001
        or roughness > SHORT_MAX_ROUGHNESS
    )
    return {
        "bad": bad,
        "peak": peak,
        "rms": rms,
        "roughness": roughness,
        "max_delta": max_delta,
    }


def _condition_short_audio(samples: np.ndarray, *, fade_out: bool) -> np.ndarray:
    output = np.asarray(samples, dtype=np.float32).reshape(-1).copy()
    if not output.size:
        return output
    output = np.nan_to_num(output, nan=0.0, posinf=0.0, neginf=0.0)
    frame = max(1, int(OUTPUT_RATE * 0.02))
    frame_rms = np.array(
        [
            np.sqrt(np.mean(np.square(output[start : start + frame]), dtype=np.float64))
            for start in range(0, output.size, frame)
            if output[start : start + frame].size
        ],
        dtype=np.float64,
    )
    voiced = frame_rms[frame_rms >= 0.003]
    active_rms = float(np.median(voiced)) if voiced.size else float(np.sqrt(np.mean(np.square(output))))
    gain = np.clip(SHORT_TARGET_RMS / max(active_rms, 1e-6), 1.0 / SHORT_MAX_GAIN, SHORT_MAX_GAIN)
    output *= float(gain)
    peak = float(np.max(np.abs(output)))
    if peak > 0.92:
        output *= 0.92 / peak
    if fade_out:
        fade_samples = min(output.size, max(1, int(OUTPUT_RATE * 0.024)))
        output[-fade_samples:] *= np.linspace(1.0, 0.0, fade_samples, dtype=np.float32)
    return output


async def _decode_full_latents(
    runtime: RuntimeState,
    latent_patches: list[bytes],
    *,
    history: ContinuationHistory | None,
    generated_samples: int,
) -> np.ndarray:
    generated_latents = _join_exact_latents(runtime, latent_patches)
    if not generated_latents:
        return np.empty(0, dtype=np.float32)
    frame_bytes = runtime.feat_dim * np.dtype(np.float32).itemsize
    context_latents = history.prompt_latents if history else None
    context_tail = (context_latents or b"")[-12 * frame_bytes :]
    context_frames = len(context_tail) // frame_bytes
    decoded = np.asarray(
        await runtime.server.decode_latents(context_tail + generated_latents),
        dtype=np.float32,
    ).reshape(-1)
    if context_frames:
        generated_frames = len(generated_latents) // frame_bytes
        samples_per_frame = generated_samples // max(1, generated_frames)
        decoded = decoded[context_frames * samples_per_frame :]
    if decoded.size != generated_samples:
        raise RuntimeError(
            "Full latent decode length mismatch: "
            f"decoded={decoded.size}, expected={generated_samples}"
        )
    return decoded


async def _generate_buffered_segment(
    runtime: RuntimeState,
    text: str,
    *,
    history: ContinuationHistory | None,
    steps: int,
    seed: int,
    fade_out: bool,
) -> BufferedSegment:
    best: BufferedSegment | None = None
    for attempt in range(SHORT_QUALITY_RETRIES + 1):
        raw_audio: list[np.ndarray] = []
        latent_patches: list[bytes] = []
        async for source, latent_bytes in _generate_segment(
            runtime,
            text,
            history=history,
            steps=steps,
            seed=seed + attempt * 1009,
        ):
            latent_patches.append(latent_bytes)
            if source.size:
                raw_audio.append(source)
        generated_samples = sum(chunk.size for chunk in raw_audio)
        model_audio = await _decode_full_latents(
            runtime,
            latent_patches,
            history=history,
            generated_samples=generated_samples,
        )
        resampled = soxr.resample(
            model_audio,
            runtime.model_rate,
            OUTPUT_RATE,
            quality="HQ",
        ).astype(np.float32, copy=False)
        audible, trimmed_ms = _trim_leading_silence(resampled)
        quality = _quality_metrics(audible)
        candidate = BufferedSegment(
            model_audio=model_audio,
            output_audio=_condition_short_audio(audible, fade_out=fade_out),
            latent_patches=latent_patches,
            leading_silence_trimmed_ms=trimmed_ms,
            quality=quality,
            attempts=attempt + 1,
        )
        if best is None or float(quality["roughness"]) < float(best.quality["roughness"]):
            best = candidate
        if not bool(quality["bad"]):
            break
    if best is None:
        raise RuntimeError("Buffered VoxCPM generation produced no candidate")
    return best


async def _encode_generated_audio(runtime: RuntimeState, samples: list[np.ndarray]) -> bytes:
    if not samples:
        return b""
    audio = np.concatenate(samples).astype(np.float32, copy=False)
    return await runtime.server.encode_latents(_wave_bytes(audio, runtime.model_rate), "wav")


async def _generate_segment(
    runtime: RuntimeState,
    text: str,
    *,
    history: ContinuationHistory | None,
    steps: int,
    seed: int,
) -> AsyncIterator[tuple[np.ndarray, bytes]]:
    if steps != DEFAULT_STEPS:
        raise ValueError(
            f"Nano-vLLM inference steps are fixed at server startup ({DEFAULT_STEPS}); requested {steps}"
        )
    target_text = _apply_default_control_instruction(text) if history is None else text
    kwargs = {
        "target_text": target_text,
        "prompt_latents": history.prompt_latents if history else None,
        "prompt_text": history.prompt_text if history and history.prompt_latents else "",
        "ref_audio_latents": runtime.reference_latents,
        "max_generate_length": _max_generate_length(text),
        "cfg_value": CFG_VALUE,
        "seed": seed,
        "include_latents": True,
    }
    async for chunk, latent_bytes in runtime.server.generate(**kwargs):
        source = np.asarray(chunk, dtype=np.float32).reshape(-1)
        yield source, bytes(latent_bytes)


async def _run_startup_warmup(runtime: RuntimeState) -> float:
    started = time.perf_counter()
    history = ContinuationHistory(CONTINUATION_SEGMENTS)
    first_latent_patches: list[bytes] = []
    async for _source, latent_bytes in _generate_segment(
        runtime,
        "오늘은 기분이 좋아.",
        history=None,
        steps=DEFAULT_STEPS,
        seed=SEED,
    ):
        first_latent_patches.append(latent_bytes)
    first_latents = _join_exact_latents(runtime, first_latent_patches)
    await runtime.server.decode_latents(first_latents)
    history.add("오늘은 기분이 좋아.", first_latents)
    second_latent_patches: list[bytes] = []
    async for _source, _latent_bytes in _generate_segment(
        runtime,
        "같이 편하게 이야기하자.",
        history=history,
        steps=DEFAULT_STEPS,
        seed=SEED + 1,
    ):
        second_latent_patches.append(_latent_bytes)
    second_latents = _join_exact_latents(runtime, second_latent_patches)
    frame_bytes = runtime.feat_dim * np.dtype(np.float32).itemsize
    await runtime.server.decode_latents(first_latents[-12 * frame_bytes :] + second_latents)
    elapsed_ms = (time.perf_counter() - started) * 1000.0
    print(f"[VoxCPM] startup_warmup_done elapsed_ms={elapsed_ms:.1f}", flush=True)
    return elapsed_ms


@asynccontextmanager
async def lifespan(app: FastAPI):
    global RUNTIME

    _load_reference_text()
    model_path = _resolve_model_path(MODEL_ID)
    server = VoxCPM.from_pretrained(
        model=model_path,
        devices=[0],
        inference_timesteps=DEFAULT_STEPS,
        max_num_batched_tokens=MAX_BATCHED_TOKENS,
        max_num_seqs=MAX_SEQS,
        max_model_len=MAX_MODEL_LEN,
        gpu_memory_utilization=GPU_MEMORY_UTILIZATION,
        enforce_eager=False,
    )
    await server.wait_for_ready()
    info = await server.get_model_info()
    reference_latents = await server.encode_latents(REFERENCE_AUDIO.read_bytes(), "wav")
    RUNTIME = RuntimeState(
        server=server,
        model_path=model_path,
        model_rate=int(info["output_sample_rate"]),
        encoder_rate=int(info["encoder_sample_rate"]),
        feat_dim=int(info["feat_dim"]),
        patch_size=int(info["patch_size"]),
        reference_latents=reference_latents,
    )
    if STARTUP_WARMUP:
        RUNTIME.startup_warmup_ms = await _run_startup_warmup(RUNTIME)
    try:
        yield
    finally:
        RUNTIME = None
        await server.stop()


app = FastAPI(title="Evelyn VoxCPM2 Stateful TTS", version="2.0.0", lifespan=lifespan)


def _runtime() -> RuntimeState:
    if RUNTIME is None:
        raise HTTPException(status_code=503, detail="VoxCPM runtime is not ready")
    return RUNTIME


@app.get("/health")
def health() -> dict[str, object]:
    runtime = RUNTIME
    gpu = torch.cuda.get_device_name(0) if torch.cuda.is_available() else None
    return {
        "ok": runtime is not None,
        "ready": runtime is not None,
        "backend": "voxcpm2-nanovllm",
        "model": MODEL_ID,
        "device": "cuda",
        "gpu": gpu,
        "voice": "clone:evelyn",
        "streaming": True,
        "input_streaming": True,
        "continuation": True,
        "continuation_source": DEFAULT_CONTINUATION_SOURCE,
        "short_decode_mode": "full_latent",
        "short_full_decode_max_chars": SHORT_FULL_DECODE_MAX_CHARS,
        "short_quality_retries": SHORT_QUALITY_RETRIES,
        "model_sample_rate": runtime.model_rate if runtime else None,
        "output_sample_rate": OUTPUT_RATE,
        "inference_steps": DEFAULT_STEPS,
        "cfg_value": CFG_VALUE,
        "default_control_instruction": DEFAULT_CONTROL_INSTRUCTION,
        "startup_warmup": STARTUP_WARMUP,
        "startup_warmup_ms": runtime.startup_warmup_ms if runtime else None,
    }


@app.post("/v1/audio/speech")
async def create_speech(request: SpeechRequest):
    if request.response_format.lower() != "pcm":
        raise HTTPException(status_code=400, detail="Only response_format=pcm is supported")
    if request.voice.lower() not in SUPPORTED_VOICES:
        raise HTTPException(status_code=404, detail=f"Clone profile '{request.voice}' not found")
    text = _clean_input(request.input)
    if not text:
        raise HTTPException(status_code=400, detail="Speech input is empty after normalization")

    runtime = _runtime()
    steps = 20 if request.model.strip().lower() in {"voxcpm2-hq", "voxcpm2-20"} else DEFAULT_STEPS
    use_full_decode = len(text) <= SHORT_FULL_DECODE_MAX_CHARS

    async def chunks() -> AsyncIterator[bytes]:
        if use_full_decode:
            buffered = await _generate_buffered_segment(
                runtime,
                text,
                history=None,
                steps=steps,
                seed=SEED,
                fade_out=True,
            )
            if buffered.output_audio.size:
                yield _pcm_bytes(buffered.output_audio)
            return
        resampler = soxr.ResampleStream(
            runtime.model_rate,
            OUTPUT_RATE,
            1,
            dtype="float32",
            quality="HQ",
        )
        gate = LeadingSilenceGate(OUTPUT_RATE)
        async for source, _latent_bytes in _generate_segment(
            runtime,
            text,
            history=None,
            steps=steps,
            seed=SEED,
        ):
            output = resampler.resample_chunk(source, last=False)
            audible = gate.process(output)
            if audible.size:
                yield _pcm_bytes(audible)
        tail = resampler.resample_chunk(np.empty(0, dtype=np.float32), last=True)
        audible = gate.process(tail)
        if audible.size:
            yield _pcm_bytes(audible)
        final = gate.flush()
        if final.size:
            yield _pcm_bytes(final)

    headers = {
        "X-TTS-Backend": "voxcpm2-nanovllm",
        "X-TTS-Voice": "clone:evelyn",
        "X-Audio-Sample-Rate": str(OUTPUT_RATE),
        "X-Audio-Channels": "1",
        "X-Audio-Sample-Format": "s16le",
        "X-TTS-Decode-Mode": "full-latent" if use_full_decode else "streaming-patch",
    }
    if request.stream:
        return StreamingResponse(chunks(), media_type="audio/pcm", headers=headers)

    body = bytearray()
    async for chunk in chunks():
        body.extend(chunk)
    return Response(content=bytes(body), media_type="audio/pcm", headers=headers)


@app.websocket("/v1/audio/speech/stream")
async def stream_speech(websocket: WebSocket):
    await websocket.accept()
    if RUNTIME is None:
        await websocket.send_json({"type": "error", "error": "VoxCPM runtime is not ready"})
        await websocket.close(code=1013)
        return

    runtime = RUNTIME
    committer = IncrementalTextCommitter()
    history = ContinuationHistory(CONTINUATION_SEGMENTS)
    segment_queue: asyncio.Queue[str | None] = asyncio.Queue()
    send_lock = asyncio.Lock()
    started_at = time.perf_counter()
    closed = False
    continuation_source = (
        DEFAULT_CONTINUATION_SOURCE
        if DEFAULT_CONTINUATION_SOURCE in CONTINUATION_SOURCES
        else "model_exact"
    )

    async def send_json(payload: dict[str, object]) -> None:
        async with send_lock:
            await websocket.send_json(payload)

    async def send_bytes(payload: bytes) -> None:
        async with send_lock:
            await websocket.send_bytes(payload)

    async def synthesis_worker() -> None:
        resampler = soxr.ResampleStream(
            runtime.model_rate,
            OUTPUT_RATE,
            1,
            dtype="float32",
            quality="HQ",
        )
        first_audio_sent = False
        segment_index = 0
        while True:
            text = await segment_queue.get()
            if text is None:
                break
            segment_index += 1
            segment_started = time.perf_counter()
            raw_audio: list[np.ndarray] = []
            exact_latent_patches: list[bytes] = []
            segment_gate = LeadingSilenceGate(OUTPUT_RATE)
            segment_decode_mode = (
                "full_latent"
                if len(text) <= SHORT_FULL_DECODE_MAX_CHARS
                else "streaming_patch"
            )
            segment_quality: dict[str, float | bool] | None = None
            segment_attempts = 1
            leading_silence_trimmed_ms = 0.0
            await send_json(
                {
                    "type": "segment_start",
                    "index": segment_index,
                    "text": text,
                    "elapsed_ms": round((segment_started - started_at) * 1000, 1),
                }
            )
            first_segment_pcm_ms: float | None = None
            if segment_decode_mode == "full_latent":
                buffered = await _generate_buffered_segment(
                    runtime,
                    text,
                    history=history,
                    steps=DEFAULT_STEPS,
                    seed=SEED + segment_index - 1,
                    fade_out=False,
                )
                raw_audio.append(buffered.model_audio)
                exact_latent_patches.extend(buffered.latent_patches)
                segment_quality = buffered.quality
                segment_attempts = buffered.attempts
                leading_silence_trimmed_ms = buffered.leading_silence_trimmed_ms
                audible = buffered.output_audio
                if audible.size:
                    first_segment_pcm_ms = (time.perf_counter() - segment_started) * 1000
                    if not first_audio_sent:
                        first_audio_sent = True
                        await send_json(
                            {
                                "type": "first_audio",
                                "elapsed_ms": round((time.perf_counter() - started_at) * 1000, 1),
                            }
                        )
                    await send_bytes(_pcm_bytes(audible))
            else:
                async for source, latent_bytes in _generate_segment(
                    runtime,
                    text,
                    history=history,
                    steps=DEFAULT_STEPS,
                    seed=SEED + segment_index - 1,
                ):
                    exact_latent_patches.append(latent_bytes)
                    if source.size:
                        raw_audio.append(source)
                    output = resampler.resample_chunk(source, last=False)
                    audible = segment_gate.process(output)
                    if not audible.size:
                        continue
                    if first_segment_pcm_ms is None:
                        first_segment_pcm_ms = (time.perf_counter() - segment_started) * 1000
                    if not first_audio_sent:
                        first_audio_sent = True
                        await send_json(
                            {
                                "type": "first_audio",
                                "elapsed_ms": round((time.perf_counter() - started_at) * 1000, 1),
                            }
                        )
                    await send_bytes(_pcm_bytes(audible))

                pending = segment_gate.flush()
                leading_silence_trimmed_ms = (
                    segment_gate.trimmed_samples * 1000 / OUTPUT_RATE
                )
                if pending.size:
                    if first_segment_pcm_ms is None:
                        first_segment_pcm_ms = (time.perf_counter() - segment_started) * 1000
                    await send_bytes(_pcm_bytes(pending))

            if continuation_source == "waveform_reencode":
                latent_bytes = await _encode_generated_audio(runtime, raw_audio)
            elif continuation_source == "model_exact_no_terminal":
                retained_exact_patches = (
                    exact_latent_patches[:-1]
                    if len(exact_latent_patches) > 1
                    else exact_latent_patches
                )
                latent_bytes = _join_exact_latents(runtime, retained_exact_patches)
            else:
                latent_bytes = _join_exact_latents(runtime, exact_latent_patches)
            history.add(text, latent_bytes)
            await send_json(
                {
                    "type": "segment_end",
                    "index": segment_index,
                    "first_pcm_ms": round(first_segment_pcm_ms or 0.0, 1),
                    "generation_ms": round((time.perf_counter() - segment_started) * 1000, 1),
                    "leading_silence_trimmed_ms": round(leading_silence_trimmed_ms, 1),
                    "decode_mode": segment_decode_mode,
                    "quality_attempts": segment_attempts,
                    "quality_roughness": (
                        round(float(segment_quality["roughness"]), 3)
                        if segment_quality is not None
                        else None
                    ),
                    "continuation_segments": len(history.entries),
                    "continuation_latent_source": continuation_source,
                    "exact_latent_patch_count": len(exact_latent_patches),
                    "history_latent_bytes": len(latent_bytes),
                }
            )

        tail = resampler.resample_chunk(np.empty(0, dtype=np.float32), last=True)
        if tail.size:
            await send_bytes(_pcm_bytes(tail))
        await send_json(
            {
                "type": "done",
                "segments": segment_index,
                "elapsed_ms": round((time.perf_counter() - started_at) * 1000, 1),
            }
        )

    worker = asyncio.create_task(synthesis_worker(), name="voxcpm-streaming-worker")
    closer: asyncio.Task[None] | None = None
    flush_received = False

    async def close_when_done() -> None:
        nonlocal closed
        try:
            await worker
            closed = True
            await websocket.close(code=1000)
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            with suppress(RuntimeError):
                await send_json({"type": "error", "error": str(exc)})
            closed = True
            with suppress(RuntimeError):
                await websocket.close(code=1011)

    await send_json(
        {
            "type": "ready",
            "sample_rate": OUTPUT_RATE,
            "channels": 1,
            "sample_format": "s16le",
            "backend": "voxcpm2-nanovllm",
            "continuation": True,
            "continuation_source": continuation_source,
            "continuation_source_options": sorted(CONTINUATION_SOURCES),
        }
    )

    try:
        while True:
            message = await websocket.receive()
            if message.get("type") == "websocket.disconnect":
                break
            raw_text = message.get("text")
            if raw_text is None:
                continue
            try:
                payload = json.loads(raw_text)
            except json.JSONDecodeError:
                payload = {"type": "append", "text": raw_text}

            message_type = str(payload.get("type") or "append").lower()
            if message_type == "start":
                requested_source = str(
                    payload.get("continuation_source") or continuation_source
                ).strip().lower()
                if requested_source not in CONTINUATION_SOURCES:
                    await send_json(
                        {
                            "type": "error",
                            "error": f"Unknown continuation source: {requested_source}",
                        }
                    )
                    continue
                continuation_source = requested_source
                await send_json(
                    {
                        "type": "started",
                        "continuation_source": continuation_source,
                    }
                )
                continue
            if message_type == "append":
                if flush_received:
                    await send_json({"type": "error", "error": "Cannot append after flush"})
                    continue
                fragment = str(payload.get("text") or "")
                fragment = TAG_PATTERN.sub(" ", fragment)
                for segment in committer.append(fragment):
                    await segment_queue.put(segment)
                    await send_json({"type": "committed", "text": segment})
                continue
            if message_type == "commit":
                if flush_received:
                    await send_json({"type": "error", "error": "Cannot commit after flush"})
                    continue
                for segment in committer.flush():
                    await segment_queue.put(segment)
                    await send_json({"type": "committed", "text": segment})
                continue
            if message_type == "flush":
                if flush_received:
                    continue
                flush_received = True
                for segment in committer.flush():
                    await segment_queue.put(segment)
                    await send_json({"type": "committed", "text": segment})
                await segment_queue.put(None)
                closer = asyncio.create_task(close_when_done(), name="voxcpm-streaming-closer")
                continue
            if message_type == "cancel":
                worker.cancel()
                with suppress(asyncio.CancelledError):
                    await worker
                if closer is not None and not closer.done():
                    closer.cancel()
                    with suppress(asyncio.CancelledError):
                        await closer
                await send_json({"type": "canceled"})
                closed = True
                await websocket.close(code=1000)
                return
            await send_json({"type": "error", "error": f"Unknown message type: {message_type}"})
    except WebSocketDisconnect:
        pass
    finally:
        if not worker.done():
            worker.cancel()
            with suppress(asyncio.CancelledError):
                await worker
        if closer is not None and not closer.done():
            closer.cancel()
            with suppress(asyncio.CancelledError):
                await closer
        if not closed:
            with suppress(RuntimeError):
                await websocket.close()
