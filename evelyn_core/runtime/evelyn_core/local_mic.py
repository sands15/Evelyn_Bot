from __future__ import annotations

import logging
import queue
import threading
import time
from collections import deque
from dataclasses import dataclass
from typing import Any, Callable, Iterable

import numpy as np

from .audio import resample_audio_float

try:
    import sounddevice as sd
except Exception:  # pragma: no cover - optional runtime dependency
    sd = None


log = logging.getLogger(__name__)


def parse_user_id_set(value: str | None) -> set[int]:
    if value is None:
        return set()
    parsed: set[int] = set()
    for chunk in str(value).replace(";", ",").split(","):
        token = chunk.strip()
        if not token:
            continue
        try:
            parsed.add(int(token))
        except (TypeError, ValueError):
            continue
    return parsed


def should_route_discord_user_to_local_mic(
    user_id: int | None,
    *,
    preferred_user_ids: set[int],
    capture_ready: bool,
) -> bool:
    return bool(capture_ready and user_id is not None and user_id in preferred_user_ids)


@dataclass(frozen=True, slots=True)
class LocalMicTarget:
    guild_id: int
    voice_channel_id: int | None
    member: Any


def serialize_local_mic_target(target: LocalMicTarget | None) -> dict[str, Any] | None:
    if target is None:
        return None
    member = getattr(target, "member", None)
    member_id = getattr(member, "id", None)
    member_name = str(
        getattr(member, "display_name", None)
        or getattr(member, "name", None)
        or ""
    ).strip()
    return {
        "guildId": int(target.guild_id),
        "voiceChannelId": int(target.voice_channel_id) if isinstance(target.voice_channel_id, int) else None,
        "memberId": int(member_id) if isinstance(member_id, int) else None,
        "memberName": member_name or ("없음" if member is None else "unknown"),
    }


def resolve_local_mic_target(*, guilds: Iterable[Any], preferred_user_ids: set[int]) -> LocalMicTarget | None:
    if not preferred_user_ids:
        return None
    for guild in guilds:
        voice_client = getattr(guild, "voice_client", None)
        channel = getattr(voice_client, "channel", None)
        if channel is None:
            continue
        members = list(getattr(channel, "members", []) or [])
        for member in members:
            member_id = getattr(member, "id", None)
            if getattr(member, "bot", False):
                continue
            if member_id in preferred_user_ids:
                return LocalMicTarget(
                    guild_id=int(getattr(guild, "id", 0) or 0),
                    voice_channel_id=getattr(channel, "id", None),
                    member=member,
                )
    return None


def mono16k_float_to_discord_pcm(audio: np.ndarray, *, sampling_rate: int) -> bytes:
    audio = np.asarray(audio, dtype=np.float32)
    if audio.size == 0:
        return b""
    clipped = np.clip(audio, -1.0, 1.0)
    pcm48k = resample_audio_float(clipped, sampling_rate, 48000) if sampling_rate != 48000 else clipped
    stereo = np.repeat(np.asarray(pcm48k, dtype=np.float32)[:, None], 2, axis=1)
    pcm16 = np.clip(stereo * 32767.0, -32768.0, 32767.0).astype(np.int16)
    return pcm16.tobytes()


def normalize_sounddevice_identifier(device: str | int | None) -> str | int | None:
    if isinstance(device, str):
        token = device.strip()
        if not token:
            return None
        if token.lstrip("-").isdigit():
            return int(token)
        return token
    return device


def sounddevice_default_sample_rate(device: str | int | None) -> int | None:
    if sd is None:
        return None
    try:
        info = sd.query_devices(device=device, kind="input")
    except Exception:
        return None
    if not isinstance(info, dict):
        return None
    try:
        sample_rate = int(round(float(info.get("default_samplerate") or 0)))
    except (TypeError, ValueError):
        return None
    return sample_rate if sample_rate > 0 else None


class LocalMicCaptureService:
    def __init__(
        self,
        *,
        on_segment: Callable[[bytes, dict[str, Any]], None],
        sample_rate: int = 16000,
        block_ms: int = 30,
        start_threshold: float = 0.015,
        continue_threshold: float = 0.009,
        start_consecutive: int = 2,
        min_voiced_ms: int = 280,
        max_silence_ms: int = 650,
        preroll_ms: int = 180,
        max_segment_sec: float = 12.0,
        device: str | int | None = None,
        queue_max: int = 256,
    ) -> None:
        self.on_segment = on_segment
        self.sample_rate = max(8000, int(sample_rate))
        self.block_ms = max(10, int(block_ms))
        self.start_threshold = max(0.001, float(start_threshold))
        self.continue_threshold = max(0.0005, float(continue_threshold))
        self.start_consecutive = max(1, int(start_consecutive))
        self.min_voiced_ms = max(80, int(min_voiced_ms))
        self.max_silence_ms = max(self.block_ms, int(max_silence_ms))
        self.preroll_ms = max(0, int(preroll_ms))
        self.max_segment_sec = max(1.0, float(max_segment_sec))
        self.requested_device = device
        self.device = normalize_sounddevice_identifier(device)
        self.queue_max = max(8, int(queue_max))

        self.block_samples = max(1, int(round(self.sample_rate * (self.block_ms / 1000.0))))
        self._trailing_silence_blocks = max(1, int(round(self.max_silence_ms / self.block_ms)))
        self._preroll_blocks = max(1, int(round(self.preroll_ms / self.block_ms))) if self.preroll_ms > 0 else 1
        self._min_voiced_samples = max(1, int(round(self.sample_rate * (self.min_voiced_ms / 1000.0))))
        self._max_segment_samples = max(self.sample_rate, int(round(self.sample_rate * self.max_segment_sec)))

        self._thread: threading.Thread | None = None
        self._stop_event = threading.Event()
        self._ready_event = threading.Event()
        self._capture_ready = False
        self.last_error: str | None = None
        self._blocks: queue.Queue[tuple[np.ndarray, dict[str, Any]]] = queue.Queue(maxsize=self.queue_max)

        self._pre_roll: deque[tuple[np.ndarray, float]] = deque(maxlen=self._preroll_blocks)
        self._current_blocks: list[np.ndarray] = []
        self._capture_active = False
        self._capture_unstable = False
        self._consecutive_start_blocks = 0
        self._trailing_silence = 0
        self._voiced_samples = 0
        self._total_samples = 0
        self.input_block_count = 0
        self.last_input_at: float | None = None
        self.last_input_level = 0.0
        self.max_input_level = 0.0
        self.last_input_status: str | None = None

    @property
    def capture_ready(self) -> bool:
        return self._capture_ready

    def start(self, *, timeout_sec: float = 3.0) -> bool:
        if sd is None:
            self.last_error = "sounddevice import failed"
            return False
        if self._thread is not None and self._thread.is_alive():
            return self.capture_ready
        self.last_error = None
        self._stop_event.clear()
        self._ready_event.clear()
        self._thread = threading.Thread(target=self._run, name="EvelynLocalMic", daemon=True)
        self._thread.start()
        self._ready_event.wait(timeout=max(0.2, float(timeout_sec)))
        return self.capture_ready

    def stop(self, *, join_timeout_sec: float = 2.0) -> None:
        self._stop_event.set()
        if self._thread is not None and self._thread.is_alive():
            self._thread.join(timeout=max(0.2, float(join_timeout_sec)))
        self._thread = None
        self._capture_ready = False

    def _run(self) -> None:
        sample_rates = [self.sample_rate]
        default_sample_rate = sounddevice_default_sample_rate(self.device)
        if default_sample_rate and default_sample_rate not in sample_rates:
            sample_rates.append(default_sample_rate)
        last_exc: Exception | None = None
        for sample_rate in sample_rates:
            self.sample_rate = max(8000, int(sample_rate))
            self.block_samples = max(1, int(round(self.sample_rate * (self.block_ms / 1000.0))))
            self._min_voiced_samples = max(1, int(round(self.sample_rate * (self.min_voiced_ms / 1000.0))))
            self._max_segment_samples = max(self.sample_rate, int(round(self.sample_rate * self.max_segment_sec)))
            if self._run_stream_once():
                return
            if self.last_error:
                last_exc = RuntimeError(self.last_error)
        if last_exc is not None:
            self.last_error = str(last_exc)
        self._ready_event.set()

    def _run_stream_once(self) -> bool:
        try:
            with sd.InputStream(
                samplerate=self.sample_rate,
                channels=1,
                dtype="float32",
                blocksize=self.block_samples,
                device=self.device,
                callback=self._input_callback,
            ):
                self._capture_ready = True
                self.last_error = None
                self._ready_event.set()
                while not self._stop_event.is_set():
                    try:
                        block, meta = self._blocks.get(timeout=0.2)
                    except queue.Empty:
                        continue
                    self._consume_block(block, meta)
            return True
        except Exception as exc:  # pragma: no cover - depends on host audio stack
            self.last_error = repr(exc)
            log.warning("Local mic capture failed: %s", exc)
            self._capture_ready = False
            return False
        finally:
            self._capture_ready = False
            self._flush_active_segment(force=True)

    def _input_callback(self, indata: np.ndarray, frames: int, time_info: Any, status: Any) -> None:
        if self._stop_event.is_set():
            return
        block = np.asarray(indata[:, 0], dtype=np.float32).copy()
        meta: dict[str, Any] = {}
        if status:
            meta["unstable"] = True
            meta["status"] = str(status)
        try:
            self._blocks.put_nowait((block, meta))
        except queue.Full:
            try:
                self._blocks.get_nowait()
            except queue.Empty:
                pass
            try:
                self._blocks.put_nowait((block, {"unstable": True, "status": "queue_full"}))
            except queue.Full:
                pass

    def _consume_block(self, block: np.ndarray, meta: dict[str, Any]) -> None:
        if block.size == 0:
            return
        level = float(np.sqrt(np.mean(np.square(block))) + 1e-12)
        self.input_block_count += 1
        self.last_input_at = time.time()
        self.last_input_level = level
        self.max_input_level = max(self.max_input_level, level)
        status = str(meta.get("status") or "").strip()
        self.last_input_status = status or None
        self._pre_roll.append((block, level))
        if not self._capture_active:
            if level >= self.start_threshold:
                self._consecutive_start_blocks += 1
            else:
                self._consecutive_start_blocks = 0
            if self._consecutive_start_blocks >= self.start_consecutive:
                self._begin_capture(meta=meta)
            return

        self._append_block(block, level=level)
        self._capture_unstable = self._capture_unstable or bool(meta.get("unstable"))
        if self._total_samples >= self._max_segment_samples:
            self._flush_active_segment(force=False)
            return
        if level >= self.continue_threshold:
            self._trailing_silence = 0
            return
        self._trailing_silence += 1
        if self._trailing_silence >= self._trailing_silence_blocks:
            self._flush_active_segment(force=False)

    def _begin_capture(self, *, meta: dict[str, Any]) -> None:
        self._capture_active = True
        self._capture_unstable = bool(meta.get("unstable"))
        self._consecutive_start_blocks = 0
        self._trailing_silence = 0
        self._current_blocks = [block.copy() for block, _ in self._pre_roll]
        self._total_samples = sum(int(block.size) for block in self._current_blocks)
        self._voiced_samples = sum(int(block.size) for block, level in self._pre_roll if level >= self.continue_threshold)

    def _append_block(self, block: np.ndarray, *, level: float) -> None:
        self._current_blocks.append(block.copy())
        self._total_samples += int(block.size)
        if level >= self.continue_threshold:
            self._voiced_samples += int(block.size)

    def _flush_active_segment(self, *, force: bool) -> None:
        if not self._capture_active:
            return
        blocks = self._current_blocks
        voiced_samples = self._voiced_samples
        total_samples = self._total_samples
        unstable = self._capture_unstable
        self._capture_active = False
        self._capture_unstable = False
        self._current_blocks = []
        self._trailing_silence = 0
        self._voiced_samples = 0
        self._total_samples = 0
        if not blocks:
            return
        if not force and (voiced_samples < self._min_voiced_samples or total_samples < self._min_voiced_samples):
            return
        segment = np.concatenate(blocks).astype(np.float32, copy=False)
        pcm_bytes = mono16k_float_to_discord_pcm(segment, sampling_rate=self.sample_rate)
        if not pcm_bytes:
            return
        self.on_segment(
            pcm_bytes,
            {
                "source": "local_mic",
                "unstable": unstable,
                "duration_sec": round(segment.size / float(self.sample_rate), 3),
                "sampling_rate": self.sample_rate,
            },
        )
