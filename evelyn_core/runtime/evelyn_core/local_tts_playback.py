from __future__ import annotations

import asyncio
import threading
import time
from dataclasses import dataclass
from typing import Any, Callable

from .config import DISCORD_FRAME_BYTES, DISCORD_PCM_CHANNELS, DISCORD_PCM_RATE, LOCAL_TTS_TAIL_SILENCE_MS
from .text import clean_text

try:
    import sounddevice as sd
except Exception:
    sd = None


def normalize_output_device(device: str | int | None) -> str | int | None:
    if device is None:
        return None
    if isinstance(device, int):
        return device
    value = clean_text(str(device)).strip()
    if not value or value.lower() in {"default", "auto"}:
        return None
    try:
        return int(value)
    except ValueError:
        return value


def local_tts_tail_silence_bytes(ms: int | float = LOCAL_TTS_TAIL_SILENCE_MS) -> bytes:
    duration_ms = max(0.0, float(ms))
    if duration_ms <= 0.0:
        return b""
    stereo_bytes_per_second = DISCORD_PCM_RATE * DISCORD_PCM_CHANNELS * 2
    byte_count = int(stereo_bytes_per_second * (duration_ms / 1000.0))
    if byte_count <= 0:
        return b""
    frame_aligned = ((byte_count + DISCORD_FRAME_BYTES - 1) // DISCORD_FRAME_BYTES) * DISCORD_FRAME_BYTES
    return b"\x00" * frame_aligned


@dataclass(slots=True)
class LocalTtsPlaybackSnapshot:
    enabled: bool
    active: bool
    device: str
    play_count: int
    played_bytes: int
    last_error: str
    last_started_at: float | None
    last_finished_at: float | None

    def to_dict(self) -> dict[str, Any]:
        return {
            "enabled": self.enabled,
            "active": self.active,
            "device": self.device,
            "playCount": self.play_count,
            "playedBytes": self.played_bytes,
            "lastError": self.last_error,
            "lastStartedAt": self.last_started_at,
            "lastFinishedAt": self.last_finished_at,
        }


class LocalTtsPlaybackManager:
    def __init__(
        self,
        *,
        enabled: bool,
        device: str | int | None = None,
        log: Callable[[str], None] | None = None,
    ) -> None:
        self.enabled = bool(enabled)
        self.device = normalize_output_device(device)
        self._log = log or (lambda _message: None)
        self._lock = asyncio.Lock()
        self.active = False
        self.play_count = 0
        self.played_bytes = 0
        self.last_error = ""
        self.last_started_at: float | None = None
        self.last_finished_at: float | None = None
        self._state_lock = threading.Lock()
        self._current_source: Any | None = None
        self._current_stream: Any | None = None
        self._stop_requested = False

    def snapshot(self) -> dict[str, Any]:
        return LocalTtsPlaybackSnapshot(
            enabled=self.enabled,
            active=self.active,
            device=str(self.device if self.device is not None else "default"),
            play_count=self.play_count,
            played_bytes=self.played_bytes,
            last_error=self.last_error,
            last_started_at=self.last_started_at,
            last_finished_at=self.last_finished_at,
        ).to_dict()

    def request_stop(self, *, reason: str = "interrupt") -> bool:
        with self._state_lock:
            was_active = bool(self.active or self._current_source is not None or self._current_stream is not None)
            if not was_active:
                return False
            self._stop_requested = True
            source = self._current_source
            stream = self._current_stream

        if source is not None:
            finish = getattr(source, "finish", None)
            cleanup = getattr(source, "cleanup", None)
            try:
                if finish is not None:
                    finish()
                elif cleanup is not None:
                    cleanup()
            except Exception:
                pass

        if stream is not None:
            for method_name in ("abort", "stop"):
                method = getattr(stream, method_name, None)
                if method is None:
                    continue
                try:
                    method()
                except Exception:
                    pass
                break

        self._log(f"[LOCAL TTS] stop_requested reason={reason}")
        return True

    async def play_source(
        self,
        source: Any,
        *,
        cleanup_source: bool = True,
        on_first_playback: Callable[[], None] | None = None,
    ) -> bool:
        if not self.enabled:
            return False
        if sd is None:
            self.last_error = "sounddevice import failed"
            self._log(f"[LOCAL TTS] unavailable err={self.last_error}")
            if cleanup_source:
                self._cleanup_source(source)
            return False

        async with self._lock:
            with self._state_lock:
                self._stop_requested = False
                self._current_source = source
                self._current_stream = None
                self.active = True
                self.last_error = ""
                self.last_started_at = time.time()
            self._log(f"[LOCAL TTS] start device={self.device if self.device is not None else 'default'}")
            try:
                played = await asyncio.to_thread(
                    self._play_source_sync,
                    source,
                    on_first_playback=on_first_playback,
                )
                self.played_bytes += played
                self.play_count += 1
                if played > 0:
                    self._log(f"[LOCAL TTS] finished bytes={played} play_count={self.play_count}")
                else:
                    self._log("[LOCAL TTS] no_audio")
                return played > 0
            except asyncio.CancelledError:
                self._cleanup_source(source)
                raise
            except Exception as exc:
                self.last_error = repr(exc)
                self._log(f"[LOCAL TTS] playback_failed err={exc!r}")
                return False
            finally:
                if cleanup_source:
                    self._cleanup_source(source)
                with self._state_lock:
                    self.last_finished_at = time.time()
                    self.active = False
                    self._current_source = None
                    self._current_stream = None
                    self._stop_requested = False

    def _play_source_sync(self, source: Any, *, on_first_playback: Callable[[], None] | None = None) -> int:
        if self._is_stop_requested():
            return 0
        first_chunk = source.read()
        source_error = getattr(source, "error", None)
        if source_error is not None:
            raise source_error
        if not first_chunk:
            return 0

        played = 0
        with sd.RawOutputStream(
            samplerate=DISCORD_PCM_RATE,
            channels=DISCORD_PCM_CHANNELS,
            dtype="int16",
            device=self.device,
            blocksize=max(1, DISCORD_FRAME_BYTES // (DISCORD_PCM_CHANNELS * 2)),
        ) as stream:
            with self._state_lock:
                self._current_stream = stream
            if self._is_stop_requested():
                return played
            stream.write(first_chunk)
            if on_first_playback is not None:
                try:
                    on_first_playback()
                except Exception:
                    pass
            played += len(first_chunk)
            while True:
                if self._is_stop_requested():
                    break
                chunk = source.read()
                if not chunk:
                    break
                if self._is_stop_requested():
                    break
                stream.write(chunk)
                played += len(chunk)
            source_error = getattr(source, "error", None)
            if source_error is not None:
                raise source_error
            tail_silence = local_tts_tail_silence_bytes()
            if played > 0 and tail_silence and not self._is_stop_requested():
                stream.write(tail_silence)
                played += len(tail_silence)
        return played

    def _is_stop_requested(self) -> bool:
        with self._state_lock:
            return self._stop_requested

    @staticmethod
    def _cleanup_source(source: Any) -> None:
        cleanup = getattr(source, "cleanup", None)
        if cleanup is not None:
            try:
                cleanup()
            except Exception:
                pass
