from __future__ import annotations

import argparse
import asyncio
import base64
import contextlib
import json
import os
from pathlib import Path
import subprocess
import time
from typing import Any

import aiohttp
import numpy as np

from .audio import prepare_stt_audio
from .config import (
    LOCAL_MIC_BLOCK_MS,
    LOCAL_MIC_CONTINUE_THRESHOLD,
    LOCAL_MIC_DEVICE,
    LOCAL_MIC_ENV_NOISE_FILTER_ENABLED,
    LOCAL_MIC_MAX_SEGMENT_SEC,
    LOCAL_MIC_MIN_VOICED_MS,
    LOCAL_MIC_PREROLL_MS,
    LOCAL_MIC_QUEUE_MAX,
    LOCAL_MIC_SAMPLE_RATE,
    LOCAL_MIC_START_CONSECUTIVE,
    LOCAL_MIC_START_THRESHOLD,
    LOCAL_MIC_VAD_FILTER_ENABLED,
    LOCAL_MIC_WAVEFORM_FILTER_ENABLED,
    OMNIVOICE_LANGUAGE,
    OMNIVOICE_MODEL,
    OMNIVOICE_NUM_STEP,
    OMNIVOICE_SPEED,
    OMNIVOICE_STREAM_BLOCK_SIZE,
    OMNIVOICE_STREAM_BLOCK_STEPS,
    OMNIVOICE_STREAM_FIRST_BLOCK_STEPS,
    OMNIVOICE_STREAM_FIRST_IMMEDIATE_CAP_MS,
    OMNIVOICE_STREAM_LOOKAHEAD_CROSSFADE_MS,
    OMNIVOICE_STREAM_STRATEGY,
    OMNIVOICE_VOICE,
    TARGET_RATE,
)
from .local_mic import LocalMicCaptureService
from .local_tts_playback import normalize_output_device
from .text import clean_text, clean_tts_text

try:
    import sounddevice as sd
except Exception:  # pragma: no cover - host audio dependency
    sd = None


BOT_API_BASE = os.getenv("LOCAL_BRIDGE_BOT_API_BASE", "http://127.0.0.1:8798").rstrip("/")
STT_SERVICE_URL = os.getenv("STT_SERVICE_URL", "http://127.0.0.1:8892").rstrip("/")
OMNIVOICE_SERVER_URL = os.getenv("OMNIVOICE_SERVER_URL", "http://127.0.0.1:8880").rstrip("/")
LOCAL_TTS_OUTPUT_DEVICE = os.getenv("LOCAL_TTS_OUTPUT_DEVICE") or os.getenv("LOCAL_AUDIO_OUTPUT_DEVICE")
LOCAL_BRIDGE_TTS_ENABLED = os.getenv("LOCAL_BRIDGE_TTS_ENABLED", "true").strip().lower() in {"1", "true", "yes", "on"}
LOCAL_BRIDGE_STREAMING_TTS_ENABLED = os.getenv("LOCAL_BRIDGE_STREAMING_TTS_ENABLED", "true").strip().lower() in {"1", "true", "yes", "on"}
LOCAL_BRIDGE_MIN_TEXT_CHARS = max(1, int(os.getenv("LOCAL_BRIDGE_MIN_TEXT_CHARS", "2")))
LOCAL_BRIDGE_STATUS_INTERVAL_SEC = max(0.2, float(os.getenv("LOCAL_BRIDGE_STATUS_INTERVAL_SEC", "0.25")))
LOCAL_BRIDGE_EXIT_AFTER_SHUTDOWN = os.getenv("LOCAL_BRIDGE_EXIT_AFTER_SHUTDOWN", "true").strip().lower() in {"1", "true", "yes", "on"}
LOCAL_BRIDGE_SHUTDOWN_EXIT_DELAY_SEC = max(0.2, float(os.getenv("LOCAL_BRIDGE_SHUTDOWN_EXIT_DELAY_SEC", "1.5")))
LOCAL_BRIDGE_TTS_WARMUP_ENABLED = os.getenv("LOCAL_BRIDGE_TTS_WARMUP_ENABLED", "true").strip().lower() in {"1", "true", "yes", "on"}
LOCAL_BRIDGE_TTS_WARMUP_TEXT = os.getenv("LOCAL_BRIDGE_TTS_WARMUP_TEXT", "\uc751.")
LOCAL_BRIDGE_TTS_WARMUP_DELAY_SEC = max(0.0, float(os.getenv("LOCAL_BRIDGE_TTS_WARMUP_DELAY_SEC", "0.5")))
LOCAL_BRIDGE_TTS_WARMUP_TIMEOUT_SEC = max(1.0, float(os.getenv("LOCAL_BRIDGE_TTS_WARMUP_TIMEOUT_SEC", "30")))
TTS_PCM_RATE = int(os.getenv("OMNIVOICE_PCM_RATE", "24000"))
TTS_PCM_CHANNELS = int(os.getenv("OMNIVOICE_PCM_CHANNELS", "1"))
TTS_SAMPLE_WIDTH_BYTES = 2
PROJECT_ROOT = Path(os.getenv("EVELYN_PROJECT_ROOT") or Path(__file__).resolve().parents[3])
STOP_SCRIPT = PROJECT_ROOT / "evelyn_core" / "runtime" / "launchers" / "stop_evelyn_local.ps1"
START_LOCAL_BAT = PROJECT_ROOT / "evelyn_core" / "start_local.bat"


def iter_pcm_aligned_chunks(chunks: list[bytes], *, sample_width: int = TTS_SAMPLE_WIDTH_BYTES):
    remainder = b""
    for chunk in chunks:
        if not chunk:
            continue
        data = remainder + chunk
        aligned_len = len(data) - (len(data) % sample_width)
        if aligned_len > 0:
            yield data[:aligned_len]
        remainder = data[aligned_len:]


class LocalIoBridge:
    def __init__(self) -> None:
        self.queue: asyncio.Queue[tuple[bytes, dict[str, Any]]] = asyncio.Queue(maxsize=8)
        self.session: aiohttp.ClientSession | None = None
        self.service: LocalMicCaptureService | None = None
        self.ready = False
        self.speaking = False
        self.segment_count = 0
        self.transcript_count = 0
        self.play_count = 0
        self.last_error = ""
        self.last_latency: dict[str, Any] = {}
        self.last_tts_playback: dict[str, Any] = {}
        self.started_at = time.time()
        self.output_device = normalize_output_device(LOCAL_TTS_OUTPUT_DEVICE)
        self.shutdown_started = False
        self.restart_started = False
        self.speak_request_queue: asyncio.Queue[dict[str, Any]] = asyncio.Queue(maxsize=8)
        self.speak_worker_task: asyncio.Task | None = None
        self.tts_warmup_task: asyncio.Task | None = None
        self.tts_warmup_done = False
        self.tts_warmup_error = ""
        self.tts_warmup_ms: float | None = None

    async def run(self) -> None:
        timeout = aiohttp.ClientTimeout(total=None, connect=10, sock_connect=10)
        async with aiohttp.ClientSession(timeout=timeout) as session:
            self.session = session
            await self._start_mic()
            await self._post_status()
            self._ensure_tts_warmup()
            while True:
                try:
                    pcm_bytes, meta = await asyncio.wait_for(self.queue.get(), timeout=LOCAL_BRIDGE_STATUS_INTERVAL_SEC)
                except asyncio.TimeoutError:
                    await self._post_status()
                    continue
                try:
                    await self._handle_segment(pcm_bytes, meta)
                finally:
                    self.queue.task_done()

    async def _start_mic(self) -> None:
        loop = asyncio.get_running_loop()

        def on_segment(pcm_bytes: bytes, meta: dict[str, Any]) -> None:
            def enqueue() -> None:
                if self.queue.full():
                    try:
                        self.queue.get_nowait()
                        self.queue.task_done()
                    except Exception:
                        pass
                self.queue.put_nowait((pcm_bytes, meta))

            loop.call_soon_threadsafe(enqueue)

        self.service = LocalMicCaptureService(
            on_segment=on_segment,
            sample_rate=LOCAL_MIC_SAMPLE_RATE,
            block_ms=LOCAL_MIC_BLOCK_MS,
            start_threshold=LOCAL_MIC_START_THRESHOLD,
            continue_threshold=LOCAL_MIC_CONTINUE_THRESHOLD,
            start_consecutive=LOCAL_MIC_START_CONSECUTIVE,
            min_voiced_ms=LOCAL_MIC_MIN_VOICED_MS,
            max_silence_ms=int(os.getenv("LOCAL_MIC_MAX_SILENCE_MS", "950")),
            preroll_ms=LOCAL_MIC_PREROLL_MS,
            max_segment_sec=LOCAL_MIC_MAX_SEGMENT_SEC,
            device=LOCAL_MIC_DEVICE,
            queue_max=LOCAL_MIC_QUEUE_MAX,
            vad_filter_enabled=LOCAL_MIC_VAD_FILTER_ENABLED,
            env_noise_filter_enabled=LOCAL_MIC_ENV_NOISE_FILTER_ENABLED,
            waveform_filter_enabled=LOCAL_MIC_WAVEFORM_FILTER_ENABLED,
        )
        started = await asyncio.to_thread(self.service.start)
        self.ready = bool(started and self.service.capture_ready)
        self.last_error = "" if self.ready else (self.service.last_error or "local_mic_not_ready")
        print(f"[LOCAL BRIDGE] mic_ready={self.ready} device={LOCAL_MIC_DEVICE or 'default'} error={self.last_error or 'none'}", flush=True)

    async def _handle_segment(self, pcm_bytes: bytes, meta: dict[str, Any]) -> None:
        if self.speaking:
            self.last_error = "input_suppressed_while_speaking"
            await self._post_status()
            return
        turn_started = time.perf_counter()
        stt_ms: float | None = None
        chat_ms: float | None = None
        tts_ms: float | None = None
        self.segment_count += 1
        await self._post_status(extra={"lastSegmentMeta": meta})
        try:
            stage_started = time.perf_counter()
            text = await self._transcribe(pcm_bytes)
            stt_ms = (time.perf_counter() - stage_started) * 1000.0
            if len(text) < LOCAL_BRIDGE_MIN_TEXT_CHARS:
                return
            self.transcript_count += 1
            print(f"[LOCAL BRIDGE] transcript={text!r}", flush=True)
            if LOCAL_BRIDGE_STREAMING_TTS_ENABLED and LOCAL_BRIDGE_TTS_ENABLED:
                try:
                    stream_result = await self._chat_stream_and_speak(text)
                    reply = clean_text(stream_result.get("reply"))
                    chat_ms = stream_result.get("chatMs")
                    tts_ms = stream_result.get("ttsMs")
                except Exception as stream_exc:
                    print(f"[LOCAL BRIDGE] chat_stream_failed fallback_to_full err={stream_exc!r}", flush=True)
                    stage_started = time.perf_counter()
                    reply = await self._chat(text)
                    chat_ms = (time.perf_counter() - stage_started) * 1000.0
                    if reply:
                        stage_started = time.perf_counter()
                        await self._speak(reply)
                        tts_ms = (time.perf_counter() - stage_started) * 1000.0
            else:
                stage_started = time.perf_counter()
                reply = await self._chat(text)
                chat_ms = (time.perf_counter() - stage_started) * 1000.0
                if reply and LOCAL_BRIDGE_TTS_ENABLED:
                    stage_started = time.perf_counter()
                    await self._speak(reply)
                    tts_ms = (time.perf_counter() - stage_started) * 1000.0
            print(f"[LOCAL BRIDGE] reply={reply!r}", flush=True)
        except Exception as exc:
            self.last_error = repr(exc)
            print(f"[LOCAL BRIDGE] segment_failed err={exc!r}", flush=True)
        finally:
            total_ms = (time.perf_counter() - turn_started) * 1000.0
            self.last_latency = {
                "sttMs": round(stt_ms, 1) if stt_ms is not None else None,
                "chatMs": round(chat_ms, 1) if chat_ms is not None else None,
                "ttsMs": round(tts_ms, 1) if tts_ms is not None else None,
                "totalMs": round(total_ms, 1),
            }
            print(
                "[LOCAL BRIDGE] turn_timing "
                f"stt_ms={self.last_latency['sttMs']} "
                f"chat_ms={self.last_latency['chatMs']} "
                f"tts_ms={self.last_latency['ttsMs']} "
                f"total_ms={self.last_latency['totalMs']}",
                flush=True,
            )
            await self._post_status()

    async def _transcribe(self, pcm_bytes: bytes) -> str:
        audio16k = np.asarray(prepare_stt_audio(pcm_bytes), dtype=np.float32)
        payload = {
            "audio_f32_base64": base64.b64encode(audio16k.tobytes()).decode("ascii"),
            "sample_count": int(audio16k.size),
            "sampling_rate": TARGET_RATE,
            "stage": "local_bridge",
            "language": "Korean",
        }
        assert self.session is not None
        async with self.session.post(f"{STT_SERVICE_URL}/v1/stt/transcribe", json=payload, timeout=aiohttp.ClientTimeout(total=45)) as resp:
            data = await resp.json(content_type=None)
            if resp.status != 200:
                raise RuntimeError(f"stt_failed {resp.status}: {data}")
            return clean_text(data.get("text"))

    async def _chat(self, text: str) -> str:
        assert self.session is not None
        payload = {"text": text, "source": "local_bridge"}
        async with self.session.post(f"{BOT_API_BASE}/api/control-page/chat", json=payload, timeout=aiohttp.ClientTimeout(total=150)) as resp:
            data = await resp.json(content_type=None)
            if resp.status != 200 or not data.get("ok"):
                raise RuntimeError(f"chat_failed {resp.status}: {data}")
            return clean_text(data.get("reply"))

    async def _chat_stream_and_speak(self, text: str) -> dict[str, Any]:
        assert self.session is not None
        payload = {"text": text, "source": "local_bridge"}
        started_at = time.perf_counter()
        tts_ms = 0.0
        sentence_count = 0
        first_sentence_ms: float | None = None
        final_reply = ""
        async with self.session.post(
            f"{BOT_API_BASE}/api/control-page/chat-stream",
            json=payload,
            timeout=aiohttp.ClientTimeout(total=180),
        ) as resp:
            if resp.status != 200:
                detail = await resp.text()
                raise RuntimeError(f"chat_stream_failed {resp.status}: {detail[:300]}")
            async for raw_line in resp.content:
                line = raw_line.decode("utf-8", errors="ignore").strip()
                if not line:
                    continue
                try:
                    event = json.loads(line)
                except json.JSONDecodeError:
                    continue
                event_type = clean_text(event.get("type"))
                if event_type == "sentence":
                    sentence = clean_text(event.get("text"))
                    if not sentence:
                        continue
                    sentence_count += 1
                    if first_sentence_ms is None:
                        first_sentence_ms = (time.perf_counter() - started_at) * 1000.0
                    speak_started = time.perf_counter()
                    await self._speak(sentence)
                    tts_ms += (time.perf_counter() - speak_started) * 1000.0
                    continue
                if event_type == "done":
                    final_reply = clean_text(event.get("reply"))
                    continue
                if event_type == "error":
                    raise RuntimeError(clean_text(event.get("error")) or "chat_stream_failed")
        elapsed_ms = (time.perf_counter() - started_at) * 1000.0
        chat_ms = max(0.0, elapsed_ms - tts_ms)
        print(
            "[LOCAL BRIDGE] stream_reply "
            f"sentence_count={sentence_count} "
            f"first_sentence_ms={round(first_sentence_ms, 1) if first_sentence_ms is not None else None} "
            f"chat_ms={round(chat_ms, 1)} "
            f"tts_ms={round(tts_ms, 1)}",
            flush=True,
        )
        return {
            "reply": final_reply,
            "sentenceCount": sentence_count,
            "firstSentenceMs": round(first_sentence_ms, 1) if first_sentence_ms is not None else None,
            "chatMs": chat_ms,
            "ttsMs": tts_ms,
        }

    async def _speak(self, text: str) -> None:
        if sd is None:
            self.last_error = "sounddevice import failed"
            return
        tts_text = clean_tts_text(text)
        if not tts_text:
            return
        await self._speak_with_payload(self._build_tts_payload(tts_text))

    def _build_tts_payload(self, text: str) -> dict[str, Any]:
        payload: dict[str, Any] = {
            "model": OMNIVOICE_MODEL,
            "input": text,
            "voice": OMNIVOICE_VOICE if OMNIVOICE_VOICE else "auto",
            "response_format": "pcm",
            "stream": True,
            "num_step": OMNIVOICE_NUM_STEP,
            "stream_strategy": OMNIVOICE_STREAM_STRATEGY,
            "stream_block_size": OMNIVOICE_STREAM_BLOCK_SIZE,
            "stream_first_block_steps": OMNIVOICE_STREAM_FIRST_BLOCK_STEPS,
            "stream_block_steps": OMNIVOICE_STREAM_BLOCK_STEPS,
            "stream_first_immediate_cap_ms": OMNIVOICE_STREAM_FIRST_IMMEDIATE_CAP_MS,
            "stream_lookahead_crossfade_ms": OMNIVOICE_STREAM_LOOKAHEAD_CROSSFADE_MS,
        }
        if OMNIVOICE_LANGUAGE:
            payload["language"] = OMNIVOICE_LANGUAGE
        if OMNIVOICE_SPEED > 0 and abs(OMNIVOICE_SPEED - 1.0) > 0.001:
            payload["speed"] = OMNIVOICE_SPEED
        return payload

    def _ensure_tts_warmup(self) -> None:
        if not LOCAL_BRIDGE_TTS_ENABLED or not LOCAL_BRIDGE_TTS_WARMUP_ENABLED:
            return
        if self.tts_warmup_task is not None and not self.tts_warmup_task.done():
            return
        try:
            loop = asyncio.get_running_loop()
        except RuntimeError:
            return
        self.tts_warmup_task = loop.create_task(self._warmup_tts_after_delay())

    async def _warmup_tts_after_delay(self) -> None:
        await asyncio.sleep(LOCAL_BRIDGE_TTS_WARMUP_DELAY_SEC)
        text = clean_tts_text(LOCAL_BRIDGE_TTS_WARMUP_TEXT)
        if not text or self.session is None:
            return
        started = time.perf_counter()
        try:
            audio_bytes = await self._drain_tts_payload(self._build_tts_payload(text))
            if audio_bytes <= 0:
                raise RuntimeError("tts_warmup_empty_audio")
            self.tts_warmup_ms = round((time.perf_counter() - started) * 1000.0, 1)
            self.tts_warmup_done = True
            self.tts_warmup_error = ""
            print(f"[LOCAL BRIDGE] tts_warmup_done bytes={audio_bytes} ms={self.tts_warmup_ms}", flush=True)
        except Exception as exc:
            self.tts_warmup_error = repr(exc)
            print(f"[LOCAL BRIDGE] tts_warmup_failed err={exc!r}", flush=True)
        finally:
            await self._post_status()

    async def _drain_tts_payload(self, payload: dict[str, Any]) -> int:
        assert self.session is not None
        async with self.session.post(
            f"{OMNIVOICE_SERVER_URL}/v1/audio/speech",
            json=payload,
            timeout=aiohttp.ClientTimeout(total=LOCAL_BRIDGE_TTS_WARMUP_TIMEOUT_SEC),
        ) as resp:
            if resp.status != 200:
                detail = await resp.text()
                raise RuntimeError(f"tts_warmup_failed {resp.status}: {detail[:300]}")
            audio_bytes = 0
            async for chunk in resp.content.iter_chunked(4096):
                audio_bytes += len(chunk)
            return audio_bytes

    async def _speak_with_payload(self, payload: dict[str, Any]) -> None:
        assert self.session is not None
        self.speaking = True
        await self._post_status()
        try:
            request_started = time.perf_counter()
            async with self.session.post(
                f"{OMNIVOICE_SERVER_URL}/v1/audio/speech",
                json=payload,
                timeout=aiohttp.ClientTimeout(total=180),
            ) as resp:
                if resp.status != 200 and str(payload.get("voice") or "").startswith("clone:"):
                    resp.release()
                    fallback_payload = dict(payload)
                    fallback_payload["voice"] = "auto"
                    await self._speak_with_payload(fallback_payload)
                    return
                if resp.status != 200:
                    detail = await resp.text()
                    raise RuntimeError(f"tts_failed {resp.status}: {detail[:300]}")
                audio_bytes, played_bytes, first_playback_ms = await self._play_streaming_pcm_response(
                    resp,
                    started_at=request_started,
                )
            if audio_bytes <= 0:
                raise RuntimeError(f"tts_empty_audio voice={payload.get('voice') or 'auto'}")
            if played_bytes <= 0:
                raise RuntimeError(f"tts_playback_empty voice={payload.get('voice') or 'auto'} bytes={audio_bytes}")
            self.play_count += 1
            self.last_error = ""
            self.last_tts_playback = {
                "voice": str(payload.get("voice") or "auto"),
                "audioBytes": audio_bytes,
                "playedBytes": played_bytes,
                "firstPlaybackMs": round(first_playback_ms, 1) if first_playback_ms is not None else None,
            }
            print(
                f"[LOCAL BRIDGE] tts_played_streaming bytes={audio_bytes} played_bytes={played_bytes} first_playback_ms={self.last_tts_playback['firstPlaybackMs']}",
                flush=True,
            )
        finally:
            self.speaking = False

    async def _play_streaming_pcm_response(
        self,
        resp: aiohttp.ClientResponse,
        *,
        started_at: float,
    ) -> tuple[int, int, float | None]:
        if sd is None:
            return 0, 0, None
        audio_bytes = 0
        played_bytes = 0
        first_playback_ms: float | None = None
        remainder = b""
        with sd.RawOutputStream(
            samplerate=TTS_PCM_RATE,
            channels=TTS_PCM_CHANNELS,
            dtype="int16",
            device=self.output_device,
        ) as stream:
            async for chunk in resp.content.iter_chunked(4096):
                if not chunk:
                    continue
                audio_bytes += len(chunk)
                data = remainder + chunk
                aligned_len = len(data) - (len(data) % TTS_SAMPLE_WIDTH_BYTES)
                if aligned_len > 0:
                    playable = data[:aligned_len]
                    stream.write(playable)
                    if first_playback_ms is None:
                        first_playback_ms = (time.perf_counter() - started_at) * 1000.0
                    played_bytes += len(playable)
                remainder = data[aligned_len:]
            if remainder:
                padded = remainder + (b"\x00" * (TTS_SAMPLE_WIDTH_BYTES - len(remainder)))
                stream.write(padded)
                if first_playback_ms is None:
                    first_playback_ms = (time.perf_counter() - started_at) * 1000.0
                played_bytes += len(padded)
            if played_bytes > 0:
                stream.write(b"\x00" * int(TTS_PCM_RATE * TTS_PCM_CHANNELS * 2 * 0.18))
        return audio_bytes, played_bytes, first_playback_ms

    def _play_pcm(self, chunks: list[bytes]) -> int:
        if not chunks or sd is None:
            return 0
        played_bytes = 0
        with sd.RawOutputStream(
            samplerate=TTS_PCM_RATE,
            channels=TTS_PCM_CHANNELS,
            dtype="int16",
            device=self.output_device,
        ) as stream:
            for chunk in iter_pcm_aligned_chunks(chunks):
                stream.write(chunk)
                played_bytes += len(chunk)
            stream.write(b"\x00" * int(TTS_PCM_RATE * TTS_PCM_CHANNELS * 2 * 0.18))
        return played_bytes

    async def _post_status(self, extra: dict[str, Any] | None = None) -> None:
        if self.session is None:
            return
        mic_stats: dict[str, Any] = {}
        if self.service is not None:
            last_input_at = self.service.last_input_at
            mic_stats = {
                "captureReady": self.service.capture_ready,
                "captureActive": bool(getattr(self.service, "_capture_active", False)),
                "inputBlockCount": self.service.input_block_count,
                "lastInputAgeSec": round(time.time() - last_input_at, 2) if last_input_at else None,
                "lastInputLevel": round(float(self.service.last_input_level), 6),
                "maxInputLevel": round(float(self.service.max_input_level), 6),
                "lastInputStatus": self.service.last_input_status,
                "rejectedSegmentCount": self.service.rejected_segment_count,
                "lastRejectedReason": self.service.last_rejected_reason,
                "lastSegmentFilter": self.service.last_segment_filter,
                "startThreshold": self.service.start_threshold,
                "continueThreshold": self.service.continue_threshold,
                "minVoicedMs": self.service.min_voiced_ms,
                "vadFilterEnabled": self.service.vad_filter_enabled,
                "envNoiseFilterEnabled": self.service.env_noise_filter_enabled,
                "waveformFilterEnabled": self.service.waveform_filter_enabled,
            }
        payload: dict[str, Any] = {
            "enabled": True,
            "ready": self.ready,
            "speaking": self.speaking,
            "mode": "windows_io_bridge",
            "segmentCount": self.segment_count,
            "transcriptCount": self.transcript_count,
            "playCount": self.play_count,
            "lastError": self.last_error,
            "startedAt": self.started_at,
            "device": LOCAL_MIC_DEVICE or "default",
            "outputDevice": str(self.output_device if self.output_device is not None else "default"),
            "streamingTts": LOCAL_BRIDGE_STREAMING_TTS_ENABLED,
            "botApiBase": BOT_API_BASE,
            "sttUrl": STT_SERVICE_URL,
            "ttsUrl": OMNIVOICE_SERVER_URL,
            "mic": mic_stats,
            "lastLatency": dict(self.last_latency),
            "lastTtsPlayback": dict(self.last_tts_playback),
            "ttsWarmup": {
                "enabled": LOCAL_BRIDGE_TTS_ENABLED and LOCAL_BRIDGE_TTS_WARMUP_ENABLED,
                "done": self.tts_warmup_done,
                "error": self.tts_warmup_error,
                "ms": self.tts_warmup_ms,
            },
        }
        if extra:
            payload.update(extra)
        try:
            async with self.session.post(f"{BOT_API_BASE}/api/local-bridge/status", json=payload, timeout=aiohttp.ClientTimeout(total=2)) as resp:
                data = await resp.json(content_type=None)
                self._handle_control_response(data)
        except Exception:
            pass

    def _handle_control_response(self, data: dict[str, Any]) -> None:
        restart = data.get("restart") if isinstance(data, dict) else None
        if isinstance(restart, dict) and restart.get("requested") and not self.restart_started:
            self.restart_started = True
            self.last_error = "restart_requested"
            self._start_restart_script()
            self._schedule_bridge_exit()
            return

        speak_requests = data.get("speakRequests") if isinstance(data, dict) else None
        if isinstance(speak_requests, list):
            for request in speak_requests:
                if not isinstance(request, dict):
                    continue
                text = clean_text(request.get("text"))
                if not text:
                    continue
                try:
                    self.speak_request_queue.put_nowait({**request, "text": text})
                except asyncio.QueueFull:
                    with contextlib.suppress(Exception):
                        self.speak_request_queue.get_nowait()
                        self.speak_request_queue.task_done()
                    with contextlib.suppress(Exception):
                        self.speak_request_queue.put_nowait({**request, "text": text})
            self._ensure_speak_worker()

        shutdown = data.get("shutdown") if isinstance(data, dict) else None
        if not isinstance(shutdown, dict) or not shutdown.get("requested") or self.shutdown_started:
            return
        self.shutdown_started = True
        self.last_error = "shutdown_requested"
        self._start_shutdown_script()
        self._schedule_bridge_exit()

    def _ensure_speak_worker(self) -> None:
        if self.speak_worker_task is not None and not self.speak_worker_task.done():
            return
        try:
            loop = asyncio.get_running_loop()
        except RuntimeError:
            return
        self.speak_worker_task = loop.create_task(self._speak_request_worker())

    async def _speak_request_worker(self) -> None:
        while True:
            try:
                request = self.speak_request_queue.get_nowait()
            except asyncio.QueueEmpty:
                return
            try:
                text = clean_text(request.get("text"))
                if text and LOCAL_BRIDGE_TTS_ENABLED:
                    started = time.perf_counter()
                    await self._speak(text)
                    tts_playback = dict(self.last_tts_playback)
                    self.last_latency = {
                        **dict(self.last_latency),
                        "controlTtsMs": round((time.perf_counter() - started) * 1000.0, 1),
                        "controlTtsFirstPlaybackMs": tts_playback.get("firstPlaybackMs"),
                    }
                    await self._post_status()
            except Exception as exc:
                self.last_error = repr(exc)
                print(f"[LOCAL BRIDGE] control_tts_failed err={exc!r}", flush=True)
                await self._post_status()
            finally:
                self.speak_request_queue.task_done()

    def _schedule_bridge_exit(self) -> None:
        if not LOCAL_BRIDGE_EXIT_AFTER_SHUTDOWN:
            return
        try:
            loop = asyncio.get_running_loop()
        except RuntimeError:
            os._exit(0)
        loop.create_task(self._exit_after_shutdown_delay())

    async def _exit_after_shutdown_delay(self) -> None:
        await asyncio.sleep(LOCAL_BRIDGE_SHUTDOWN_EXIT_DELAY_SEC)
        if self.service is not None:
            try:
                await asyncio.to_thread(self.service.stop)
            except Exception:
                pass
        print("[LOCAL BRIDGE] exiting after shutdown request", flush=True)
        os._exit(0)

    def _start_shutdown_script(self) -> None:
        if not STOP_SCRIPT.exists():
            self.last_error = f"shutdown helper not found: {STOP_SCRIPT}"
            print(f"[LOCAL BRIDGE] {self.last_error}", flush=True)
            return
        try:
            subprocess.Popen(
                [
                    "powershell.exe",
                    "-NoLogo",
                    "-NoProfile",
                    "-ExecutionPolicy",
                    "Bypass",
                    "-File",
                    str(STOP_SCRIPT),
                    "-DelayMs",
                    "200",
                ],
                cwd=str(PROJECT_ROOT),
                creationflags=getattr(subprocess, "CREATE_NEW_CONSOLE", 0),
            )
            print(f"[LOCAL BRIDGE] shutdown script started: {STOP_SCRIPT}", flush=True)
        except Exception as exc:
            self.last_error = f"shutdown start failed: {exc!r}"
            print(f"[LOCAL BRIDGE] {self.last_error}", flush=True)

    def _start_restart_script(self) -> None:
        if not STOP_SCRIPT.exists():
            self.last_error = f"restart stop helper not found: {STOP_SCRIPT}"
            print(f"[LOCAL BRIDGE] {self.last_error}", flush=True)
            return
        if not START_LOCAL_BAT.exists():
            self.last_error = f"restart start helper not found: {START_LOCAL_BAT}"
            print(f"[LOCAL BRIDGE] {self.last_error}", flush=True)
            return
        try:
            restart_script = (
                "$ErrorActionPreference = 'Continue'; "
                f"& '{STOP_SCRIPT}' -DelayMs 200; "
                "Start-Sleep -Seconds 2; "
                f"& '{START_LOCAL_BAT}' --background"
            )
            subprocess.Popen(
                [
                    "powershell.exe",
                    "-NoLogo",
                    "-NoProfile",
                    "-ExecutionPolicy",
                    "Bypass",
                    "-Command",
                    restart_script,
                ],
                cwd=str(PROJECT_ROOT),
                creationflags=getattr(subprocess, "CREATE_NEW_CONSOLE", 0),
            )
            print(f"[LOCAL BRIDGE] restart script started: {START_LOCAL_BAT}", flush=True)
        except Exception as exc:
            self.last_error = f"restart start failed: {exc!r}"
            print(f"[LOCAL BRIDGE] {self.last_error}", flush=True)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Run Evelyn Windows local microphone/speaker bridge against Docker core.")
    parser.add_argument("--project-root", default="", help="Project root marker used by launchers to detect an existing bridge process.")
    return parser.parse_args()


def main() -> None:
    parse_args()
    asyncio.run(LocalIoBridge().run())


if __name__ == "__main__":
    main()
