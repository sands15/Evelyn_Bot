import asyncio
import audioop
import contextlib
import hashlib
import queue
import re
import threading
import time
import wave
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Awaitable, Callable

import discord

from .observability_metrics import mark_voice_latency_stage
from .text import clean_text, clean_tts_text, strip_omnivoice_tags
from .voice_validation import validation_attempt_binding_is_current

from .config import (
    DISCORD_FRAME_BYTES,
    DISCORD_PCM_RATE,
    OMNIVOICE_PCM_CHANNELS,
    OMNIVOICE_PCM_RATE,
    OMNIVOICE_PLAYBACK_ADAPTIVE_JITTER,
    OMNIVOICE_PLAYBACK_BLOCK_GAP_MIN_MS,
    OMNIVOICE_PLAYBACK_GAP_MULTIPLIER,
    OMNIVOICE_PLAYBACK_GAP_SAFETY_MS,
    OMNIVOICE_PLAYBACK_MAX_BUFFER_MS,
    OMNIVOICE_PLAYBACK_MIN_BUFFER_MS,
    OMNIVOICE_PLAYBACK_START_BUFFER_MS,
    OMNIVOICE_STREAM_BLOCK_SIZE,
    OMNIVOICE_STREAM_BLOCK_STEPS,
    OMNIVOICE_STREAM_FIRST_BLOCK_STEPS,
    OMNIVOICE_STREAM_FIRST_IMMEDIATE_CAP_MS,
    OMNIVOICE_STREAM_FOLLOWUP_STRATEGY,
    OMNIVOICE_STREAM_LOOKAHEAD_CROSSFADE_MS,
    OMNIVOICE_STREAM_STRATEGY,
    OMNIVOICE_TIMEOUT_SEC,
    TTS_CHUNK_TAIL_SILENCE_MS,
)

TurnEventLogger = Callable[..., None]


def _noop_log_turn_event(event: str, **payload: Any) -> None:
    return None


_log_turn_event: TurnEventLogger = _noop_log_turn_event
_playback_gap_ema_ms = max(0.0, OMNIVOICE_PLAYBACK_BLOCK_GAP_MIN_MS)
_playback_gap_peak_ms = max(0.0, OMNIVOICE_PLAYBACK_BLOCK_GAP_MIN_MS)


def configure_tts_playback_logging(log_turn_event: TurnEventLogger) -> None:
    global _log_turn_event
    _log_turn_event = log_turn_event


def merge_log_event_payload(*, explicit: dict[str, Any], extra: dict[str, Any] | None = None) -> dict[str, Any]:
    merged = dict(extra or {})
    for key in explicit.keys():
        merged.pop(key, None)
    merged.update(explicit)
    return merged


def clamp_tts_playback_buffer_ms(value: float) -> float:
    minimum = max(0.0, float(OMNIVOICE_PLAYBACK_MIN_BUFFER_MS))
    maximum = max(minimum, float(OMNIVOICE_PLAYBACK_MAX_BUFFER_MS))
    return min(max(float(value), minimum), maximum)


def current_tts_playback_buffer_ms() -> float:
    base = float(OMNIVOICE_PLAYBACK_START_BUFFER_MS)
    if not OMNIVOICE_PLAYBACK_ADAPTIVE_JITTER:
        return clamp_tts_playback_buffer_ms(base)
    gap_basis = max(float(_playback_gap_ema_ms), float(_playback_gap_peak_ms))
    adaptive = base + (gap_basis * float(OMNIVOICE_PLAYBACK_GAP_MULTIPLIER)) + float(OMNIVOICE_PLAYBACK_GAP_SAFETY_MS)
    return clamp_tts_playback_buffer_ms(adaptive)


def playback_buffer_bytes_for_ms(buffer_ms: float) -> int:
    stereo_bytes_per_second = DISCORD_PCM_RATE * 2 * 2
    return max(DISCORD_FRAME_BYTES, int(stereo_bytes_per_second * (max(0.0, buffer_ms) / 1000.0)))


def discord_pcm_silence_bytes(ms: int | float) -> bytes:
    duration_ms = max(0.0, float(ms))
    if duration_ms <= 0.0:
        return b""
    stereo_bytes_per_second = DISCORD_PCM_RATE * 2 * 2
    byte_count = int(stereo_bytes_per_second * (duration_ms / 1000.0))
    if byte_count <= 0:
        return b""
    frame_aligned = ((byte_count + DISCORD_FRAME_BYTES - 1) // DISCORD_FRAME_BYTES) * DISCORD_FRAME_BYTES
    return b"\x00" * frame_aligned


def omnivoice_stream_contract_payload(*, chunk_index: int | None = None) -> dict[str, Any]:
    is_followup = chunk_index is not None and int(chunk_index) > 1
    return {
        "chunk_index": int(chunk_index or 0),
        "stream_strategy": OMNIVOICE_STREAM_FOLLOWUP_STRATEGY if is_followup else OMNIVOICE_STREAM_STRATEGY,
        "stream_block_size": OMNIVOICE_STREAM_BLOCK_SIZE,
        "stream_first_block_steps": OMNIVOICE_STREAM_FIRST_BLOCK_STEPS,
        "stream_block_steps": OMNIVOICE_STREAM_BLOCK_STEPS,
        "stream_first_immediate_cap_ms": OMNIVOICE_STREAM_FIRST_IMMEDIATE_CAP_MS,
        "stream_lookahead_crossfade_ms": OMNIVOICE_STREAM_LOOKAHEAD_CROSSFADE_MS,
        "playback_start_buffer_ms": current_tts_playback_buffer_ms(),
        "playback_adaptive_jitter": bool(OMNIVOICE_PLAYBACK_ADAPTIVE_JITTER),
    }


def add_omnivoice_stream_contract(
    payload: dict[str, Any],
    *,
    request_id: str | None = None,
    chunk_index: int | None = None,
) -> dict[str, Any]:
    enriched = dict(payload)
    contract = omnivoice_stream_contract_payload(chunk_index=chunk_index)
    if request_id:
        contract["request_id"] = request_id
    enriched.update(contract)
    return enriched


def observe_tts_playback_gap_ms(gap_ms: float) -> None:
    global _playback_gap_ema_ms, _playback_gap_peak_ms
    gap = max(0.0, float(gap_ms))
    if gap < max(0.0, float(OMNIVOICE_PLAYBACK_BLOCK_GAP_MIN_MS)):
        return
    if _playback_gap_ema_ms <= 0.0:
        _playback_gap_ema_ms = gap
    else:
        _playback_gap_ema_ms = (_playback_gap_ema_ms * 0.75) + (gap * 0.25)
    _playback_gap_peak_ms = max(gap, _playback_gap_peak_ms * 0.9)


class OmniVoicePCMStream(discord.AudioSource):
    def __init__(
        self,
        *,
        on_first_frame: Callable[[], None] | None = None,
        on_first_packet_sent: Callable[[], None] | None = None,
        trace_payload: dict[str, Any] | None = None,
    ):
        self._queue: queue.Queue[bytes | None] = queue.Queue()
        self._buffer = bytearray()
        self._done = False
        self._closed = False
        self._rate_state = None
        self._input_remainder = b""
        self._first_frame_sent = False
        self._first_packet_sent = False
        self._on_first_frame = on_first_frame
        self._on_first_packet_sent = on_first_packet_sent
        self._ready_event = threading.Event()
        self._queued_audio_bytes = 0
        self._ready_buffer_ms = current_tts_playback_buffer_ms()
        self._ready_buffer_bytes = playback_buffer_bytes_for_ms(self._ready_buffer_ms)
        self._last_pcm_feed_at: float | None = None
        self._trace_payload = dict(trace_payload or {})
        self.error: Exception | None = None

    def feed_pcm24_mono(self, chunk: bytes) -> None:
        if self._closed or not chunk:
            return

        pcm = self._input_remainder + chunk
        if len(pcm) % 2 == 1:
            self._input_remainder = pcm[-1:]
            pcm = pcm[:-1]
        else:
            self._input_remainder = b""

        if not pcm:
            return

        upsampled, self._rate_state = audioop.ratecv(
            pcm,
            2,
            OMNIVOICE_PCM_CHANNELS,
            OMNIVOICE_PCM_RATE,
            DISCORD_PCM_RATE,
            self._rate_state,
        )
        stereo = audioop.tostereo(upsampled, 2, 1, 1)
        if stereo:
            now = time.monotonic()
            if self._last_pcm_feed_at is not None:
                observe_tts_playback_gap_ms((now - self._last_pcm_feed_at) * 1000.0)
                if OMNIVOICE_PLAYBACK_ADAPTIVE_JITTER and not self._ready_event.is_set():
                    self._ready_buffer_ms = current_tts_playback_buffer_ms()
                    self._ready_buffer_bytes = playback_buffer_bytes_for_ms(self._ready_buffer_ms)
            self._last_pcm_feed_at = now
            self._queue.put(stereo)
            self._queued_audio_bytes += len(stereo)
            if not self._ready_event.is_set() and self._queued_audio_bytes >= self._ready_buffer_bytes:
                _log_turn_event(
                    "tts_playback_start_buffer_ready",
                    **merge_log_event_payload(
                        explicit={
                            "queued_audio_bytes": self._queued_audio_bytes,
                            "ready_buffer_bytes": self._ready_buffer_bytes,
                            "ready_buffer_ms": round(self._ready_buffer_ms, 1),
                            "gap_ema_ms": round(float(_playback_gap_ema_ms), 1),
                            "gap_peak_ms": round(float(_playback_gap_peak_ms), 1),
                        },
                        extra=self._trace_payload,
                    ),
                )
                self._ready_event.set()
            _log_turn_event(
                "playback_queue_put",
                **merge_log_event_payload(explicit={"bytes": len(stereo)}, extra=self._trace_payload),
            )

    def finish(self) -> None:
        if self._closed:
            return
        self._done = True
        self._ready_event.set()
        tail_silence = discord_pcm_silence_bytes(TTS_CHUNK_TAIL_SILENCE_MS)
        if tail_silence:
            self._queue.put(tail_silence)
        self._queue.put(None)

    def fail(self, err: Exception) -> None:
        self.error = err
        self.finish()

    def has_audio_ready(self) -> bool:
        return bool(self._buffer) or not self._queue.empty() or self._ready_event.is_set()

    def is_exhausted(self) -> bool:
        return self._done and not self._buffer and self._queue.empty()

    async def wait_until_ready(self, timeout: float = 1.0) -> bool:
        return await asyncio.to_thread(self._ready_event.wait, timeout)

    def read(self) -> bytes:
        while len(self._buffer) < DISCORD_FRAME_BYTES:
            try:
                item = self._queue.get(timeout=0.02)
            except queue.Empty:
                if self._done:
                    break
                continue

            if item is None:
                self._done = True
                break

            _log_turn_event(
                "playback_queue_get",
                **merge_log_event_payload(explicit={"bytes": len(item)}, extra=self._trace_payload),
            )
            self._buffer.extend(item)

        if len(self._buffer) >= DISCORD_FRAME_BYTES:
            chunk = bytes(self._buffer[:DISCORD_FRAME_BYTES])
            del self._buffer[:DISCORD_FRAME_BYTES]
            if not self._first_frame_sent and any(chunk):
                self._first_frame_sent = True
                if self._on_first_frame is not None:
                    self._on_first_frame()
            return chunk

        if self._done and self._buffer:
            chunk = bytes(self._buffer)
            self._buffer.clear()
            padded = chunk + (b"\x00" * (DISCORD_FRAME_BYTES - len(chunk)))
            if not self._first_frame_sent and any(padded):
                self._first_frame_sent = True
                if self._on_first_frame is not None:
                    self._on_first_frame()
            return padded

        return b""

    def mark_packet_sent(self, chunk: bytes) -> None:
        if chunk and not self._first_packet_sent and any(chunk):
            self._first_packet_sent = True
            if self._on_first_packet_sent is not None:
                self._on_first_packet_sent()

    def cleanup(self) -> None:
        self._closed = True
        self._done = True
        self._ready_event.set()
        while True:
            try:
                self._queue.get_nowait()
            except queue.Empty:
                break
        self._buffer.clear()
        self._input_remainder = b""
        self._rate_state = None
        self._queued_audio_bytes = 0


class CachedWaveAudioSource(discord.AudioSource):
    def __init__(
        self,
        path: Path,
        *,
        on_first_packet_sent: Callable[[], None] | None = None,
    ) -> None:
        self.path = Path(path)
        self._offset = 0
        self._closed = False
        self._first_packet_sent = False
        self._on_first_packet_sent = on_first_packet_sent
        self.error: Exception | None = None

        with wave.open(str(self.path), "rb") as wav:
            channels = wav.getnchannels()
            sample_width = wav.getsampwidth()
            sample_rate = wav.getframerate()
            frames = wav.readframes(wav.getnframes())

        if sample_width != 2:
            raise ValueError(f"cached audio must be 16-bit PCM wav: {self.path}")
        if channels not in {1, 2}:
            raise ValueError(f"cached audio must be mono or stereo wav: {self.path}")

        pcm = frames
        if channels == 2:
            pcm = audioop.tomono(pcm, sample_width, 0.5, 0.5)
            channels = 1

        if sample_rate != DISCORD_PCM_RATE:
            pcm, _state = audioop.ratecv(
                pcm,
                sample_width,
                channels,
                sample_rate,
                DISCORD_PCM_RATE,
                None,
            )

        self._pcm = audioop.tostereo(pcm, sample_width, 1, 1) if channels == 1 else pcm

    def read(self) -> bytes:
        if self._closed or self._offset >= len(self._pcm):
            return b""

        chunk = self._pcm[self._offset : self._offset + DISCORD_FRAME_BYTES]
        self._offset += len(chunk)
        if len(chunk) < DISCORD_FRAME_BYTES:
            chunk += b"\x00" * (DISCORD_FRAME_BYTES - len(chunk))

        return chunk

    def mark_packet_sent(self, chunk: bytes) -> None:
        if chunk and not self._first_packet_sent and any(chunk):
            self._first_packet_sent = True
            if self._on_first_packet_sent is not None:
                self._on_first_packet_sent()

    def cleanup(self) -> None:
        self._closed = True

    def finish(self) -> None:
        self.cleanup()


def resolve_cached_tts_audio_path(
    answer: str,
    *,
    enabled: bool,
    canned_text: str,
    canned_audio_path: Path,
    project_root: Path,
) -> Path | None:
    if not enabled:
        return None
    if clean_tts_text(answer) != clean_tts_text(canned_text):
        return None
    path = Path(canned_audio_path)
    if not path.is_absolute():
        path = Path(project_root) / path
    return path if path.is_file() else None


class QueuedAudioSource(discord.AudioSource):
    def __init__(self, *, trace_payload: dict[str, Any] | None = None) -> None:
        self._sources: queue.Queue[OmniVoicePCMStream | None] = queue.Queue()
        self._current: OmniVoicePCMStream | None = None
        self._closed = False
        self._done = False
        self.error: Exception | None = None
        self._trace_payload = dict(trace_payload or {})
        self._silence_frames_by_reason: dict[str, int] = {}

    def _silence_frame(self, reason: str) -> bytes:
        count = self._silence_frames_by_reason.get(reason, 0) + 1
        self._silence_frames_by_reason[reason] = count
        if count in {1, 5, 25} or count % 50 == 0:
            _log_turn_event(
                "playback_underrun_silence",
                **merge_log_event_payload(
                    explicit={"reason": reason, "silence_frames": count},
                    extra=self._trace_payload,
                ),
            )
        return b"\x00" * DISCORD_FRAME_BYTES

    def add_source(self, source: OmniVoicePCMStream) -> None:
        if self._closed:
            source.cleanup()
            return
        self._sources.put(source)

    def finish(self) -> None:
        if self._closed:
            return
        self._done = True
        self._sources.put(None)

    def read(self) -> bytes:
        while True:
            if self._current is None:
                try:
                    next_source = self._sources.get(timeout=0.02)
                except queue.Empty:
                    if self._done:
                        return b""
                    return self._silence_frame("waiting_for_prefetched_source")
                if next_source is None:
                    self._done = True
                    return b""
                self._current = next_source

            chunk = self._current.read()
            if chunk:
                return chunk

            if self._current.error is not None and self.error is None:
                self.error = self._current.error
            if self._current.is_exhausted():
                self._current.cleanup()
                self._current = None
                continue

            return self._silence_frame("current_source_starved")

    def cleanup(self) -> None:
        self._closed = True
        self._done = True
        if self._current is not None:
            self._current.cleanup()
            self._current = None
        while True:
            try:
                item = self._sources.get_nowait()
            except queue.Empty:
                break
            if item is not None:
                item.cleanup()

    def mark_packet_sent(self, chunk: bytes) -> None:
        mark_packet_sent = getattr(self._current, "mark_packet_sent", None)
        if callable(mark_packet_sent):
            mark_packet_sent(chunk)


BAD_TAIL_WORDS = (
    "그리고",
    "근데",
    "하지만",
    "다만",
    "그래서",
    "그래도",
)

BAD_TAIL_SUFFIXES = (
    "은", "는", "이", "가", "을", "를", "의", "에", "고",
    "로", "면", "과", "와", "며", "도", "만",
)

GOOD_END_SUFFIXES = (
    "다", "요", "지", "네", "까", "어", "아", "야",
)


@dataclass(frozen=True)
class ChunkWindow:
    min_chars: int
    target_chars: int
    max_chars: int
    allow_soft_breaks: bool = True
    soft_break_overflow_only: bool = False


@dataclass
class ChunkerConfig:
    hard_breaks: tuple[str, ...] = (".", "!", "?", "\n", "。", "！", "？")
    soft_breaks: tuple[str, ...] = (",", ";", ":", "그리고", "근데")
    hard_break_grace_chars: int = 10
    candidate_unstable_penalty: int = 80
    natural_end_bonus: int = 12
    allow_forced_cuts: bool = False
    short_hard_min_chars: int = 4
    first_window: ChunkWindow = field(default_factory=lambda: ChunkWindow(18, 24, 40, True, False))
    next_window: ChunkWindow = field(default_factory=lambda: ChunkWindow(12, 36, 72, False, True))
    structured_first_window: ChunkWindow = field(default_factory=lambda: ChunkWindow(22, 30, 48, False, False))
    structured_next_window: ChunkWindow = field(default_factory=lambda: ChunkWindow(18, 40, 84, False, True))


def _normalized_tail_probe(text: str) -> str:
    s = clean_tts_text(text).strip()
    if not s:
        return ""
    while s and s[-1] in " \t\r\n,，;；:：….!?。！？)]}>'\"”’」』】":
        s = s[:-1].rstrip()
    return s


def has_unbalanced_pairs(text: str) -> bool:
    s = text or ""
    pairs = (("(", ")"), ("[", "]"), ("{", "}"))
    for left, right in pairs:
        if s.count(left) != s.count(right):
            return True
    if s.count('"') % 2 == 1:
        return True
    if s.count("'") % 2 == 1:
        return True
    if s.count("```") % 2 == 1:
        return True
    return False


def is_unstable_tail(chunk: str) -> bool:
    s = clean_tts_text(chunk).strip()
    if not s:
        return True
    if has_unbalanced_pairs(s):
        return True

    tail_probe = _normalized_tail_probe(s)
    if not tail_probe:
        return True

    for word in BAD_TAIL_WORDS:
        if tail_probe.endswith(word):
            return True

    for suffix in BAD_TAIL_SUFFIXES:
        if tail_probe.endswith(suffix):
            return True

    return False


def has_natural_end(chunk: str) -> bool:
    tail_probe = _normalized_tail_probe(chunk)
    if not tail_probe:
        return False
    return tail_probe.endswith(GOOD_END_SUFFIXES)


def detect_output_shape(text: str) -> str:
    s = text or ""
    stripped = s.lstrip()
    if not stripped:
        return "chat"
    if stripped.startswith(("```", "`")):
        return "structured"
    if re.search(r"(?m)^\s*(?:[-*•]|\d+[.)])\s+\S", stripped):
        return "structured"
    if re.search(r"(?m)^\s*\|.+\|\s*$", stripped):
        return "structured"
    return "chat"


@dataclass
class SpeechChunker:
    config: ChunkerConfig = field(default_factory=ChunkerConfig)
    buf: str = ""
    sent_first: bool = False
    mode: str = "chat"

    def push(self, delta: str, *, max_chunks: int | None = 1) -> list[str]:
        if delta:
            self.buf += delta
        self.mode = detect_output_shape(self.buf)

        out: list[str] = []
        while True:
            cut = self._find_dispatch_point(self.buf)
            if cut is None:
                break

            chunk = self._consume(cut)
            if not chunk:
                continue

            out.append(chunk)
            self.sent_first = True
            if max_chunks is not None and len(out) >= max_chunks:
                break

        return out

    def flush(self) -> list[str]:
        tail = clean_tts_text(self.buf)
        self.buf = ""
        return [tail] if tail else []

    def _window(self) -> ChunkWindow:
        if self.mode == "structured":
            return self.config.structured_next_window if self.sent_first else self.config.structured_first_window
        return self.config.next_window if self.sent_first else self.config.first_window

    def _consume(self, cut: int) -> str:
        raw = self.buf[:cut]
        self.buf = self.buf[cut:].lstrip()
        return clean_tts_text(raw)

    def _find_dispatch_point(self, text: str) -> int | None:
        if not text.strip():
            return None

        window = self._window()
        best_idx: int | None = None
        best_score = -(10 ** 9)
        best_kind: str | None = None
        best_visible_len = 0
        clean_len = len(clean_text(text))

        for i, ch in enumerate(text):
            raw_idx = i + 1
            chunk = clean_tts_text(text[:raw_idx])
            visible_len = len(clean_text(chunk))
            is_hard = ch in self.config.hard_breaks
            is_soft = ch in self.config.soft_breaks
            if visible_len < window.min_chars:
                short_complete = bool(
                    is_hard
                    and visible_len >= self.config.short_hard_min_chars
                    and not is_unstable_tail(chunk)
                )
                if not short_complete:
                    continue
            if not is_hard:
                if not is_soft:
                    continue
                if not window.allow_soft_breaks:
                    if not window.soft_break_overflow_only or visible_len < window.max_chars:
                        continue

            score = 100 if is_hard else 55
            score -= abs(visible_len - window.target_chars)
            if visible_len > window.max_chars:
                score -= 20
            if is_unstable_tail(chunk):
                score -= self.config.candidate_unstable_penalty
            if has_natural_end(chunk):
                score += self.config.natural_end_bonus

            if score > best_score:
                best_score = score
                best_idx = raw_idx
                best_kind = "hard" if is_hard else "soft"
                best_visible_len = visible_len

        if best_idx is not None and best_kind == "soft":
            for i, ch in enumerate(text):
                raw_idx = i + 1
                if raw_idx <= best_idx or ch not in self.config.hard_breaks:
                    continue
                candidate = clean_tts_text(text[:raw_idx])
                visible_len = len(clean_text(candidate))
                if visible_len > window.max_chars:
                    continue
                if visible_len - best_visible_len > self.config.hard_break_grace_chars:
                    continue
                if is_unstable_tail(candidate):
                    continue
                return raw_idx

        if (
            self.config.allow_forced_cuts
            and best_idx is None
            and clean_len >= window.max_chars
        ):
            forced_idx = self._find_forced_cut(text, window.max_chars)
            forced_chunk = clean_tts_text(text[:forced_idx])
            if forced_chunk and not is_unstable_tail(forced_chunk):
                return forced_idx

        return best_idx

    def _find_forced_cut(self, text: str, max_chars: int) -> int:
        visible_count = 0
        target_raw_idx = len(text)

        for i, ch in enumerate(text):
            if not ch.isspace() or visible_count > 0:
                visible_count += 1
            if visible_count >= max_chars:
                target_raw_idx = i + 1
                break

        search_start = max(1, target_raw_idx - 14)
        window = text[search_start - 1:target_raw_idx]
        for j in range(len(window) - 1, -1, -1):
            ch = window[j]
            raw_idx = (search_start - 1) + j + 1
            if ch.isspace() or ch in self.config.soft_breaks or ch in self.config.hard_breaks:
                return raw_idx
        return target_raw_idx


class SpeechCommitContractError(RuntimeError):
    pass


@dataclass(frozen=True, slots=True)
class SpeechCommit:
    """Internal TTS handoff; the content-derived hash must not enter telemetry."""

    turn_id: str
    response_generation: object
    prefix_index: int
    prefix_hash: str
    text: str = field(repr=False)


@dataclass
class SpeechCommitGate:
    """Bind shared chunk candidates to one current, immutable speech prefix."""

    turn_id: str
    response_generation: object
    generation_is_current: Callable[[object], bool]
    commit_allowed: Callable[[], bool] | None = None
    memory_bound: bool = False
    chunker: SpeechChunker = field(default_factory=SpeechChunker)
    _safe_stream: str = field(default="", init=False, repr=False)
    _pending: str = field(default="", init=False, repr=False)
    _committed_prefix: str = field(default="", init=False, repr=False)
    _next_prefix_index: int = field(default=0, init=False, repr=False)
    _final_text: str | None = field(default=None, init=False, repr=False)
    _closed: bool = field(default=False, init=False, repr=False)
    _stale: bool = field(default=False, init=False, repr=False)

    def __post_init__(self) -> None:
        self.turn_id = clean_text(self.turn_id)
        if not self.turn_id:
            raise ValueError("speech commit turn_id is required")
        if self.response_generation is None:
            raise ValueError("speech commit response_generation is required")
        if not callable(self.generation_is_current):
            raise TypeError("speech commit generation check is required")
        if self.memory_bound and not callable(self.commit_allowed):
            raise ValueError("memory-bound speech requires an explicit handoff barrier")

    @property
    def committed_prefix(self) -> str:
        return self._committed_prefix

    @property
    def stale(self) -> bool:
        return self._stale

    @property
    def closed(self) -> bool:
        return self._closed

    def push(self, safe_delta: str) -> list[SpeechCommit]:
        if self._closed:
            raise SpeechCommitContractError("speech commit gate is closed")
        if safe_delta:
            self._safe_stream += safe_delta
            self._pending += safe_delta
        if not self._generation_current():
            self._invalidate()
            return []
        if self.memory_bound or not self._commit_is_allowed():
            return []
        if self._pending:
            chunks = self.chunker.push(self._pending, max_chunks=None)
            self._pending = ""
        else:
            chunks = self.chunker.push("", max_chunks=None)
        return self._bind_chunks(chunks, final_text=None)

    def observe_safe_delta(self, safe_delta: str) -> bool:
        """Record post-policy speech text without making it speakable yet."""

        if self._closed:
            raise SpeechCommitContractError("speech commit gate is closed")
        if not self._generation_current():
            self._invalidate()
            return False
        visible_delta = self._canonical_prefix(safe_delta)
        if visible_delta:
            self._safe_stream = clean_text(
                f"{self._safe_stream} {visible_delta}"
            )
        return True

    def commit_candidate(self, text: str) -> list[SpeechCommit]:
        """Bind one already-chunked, post-policy candidate to this turn."""

        if self._closed:
            raise SpeechCommitContractError("speech commit gate is closed")
        if not self._generation_current():
            self._invalidate()
            return []
        if not self._commit_is_allowed():
            return []
        speech_text = clean_tts_text(text)
        if not speech_text:
            return []
        return self._bind_chunks([speech_text], final_text=None)

    def validate_final(self, final_text: str) -> None:
        """Close after proving every committed chunk is an immutable prefix."""

        if self._closed:
            if self._final_text is not None and final_text == self._final_text:
                return
            raise SpeechCommitContractError("speech commit gate is closed")
        if not self._generation_current():
            self._invalidate()
            raise SpeechCommitContractError("speech response generation is stale")
        if not self._commit_is_allowed():
            raise SpeechCommitContractError(
                "speech final validation is not authorized"
            )
        if self._final_text is not None and final_text != self._final_text:
            raise SpeechCommitContractError("speech final text changed")
        self._final_text = final_text
        final_prefix = self._canonical_prefix(final_text)
        if self._committed_prefix and not final_prefix.startswith(
            self._committed_prefix
        ):
            raise SpeechCommitContractError(
                "committed speech is not an immutable final prefix"
            )
        self._closed = True

    def finish(self, final_text: str) -> list[SpeechCommit]:
        if self._closed:
            return []
        if self._final_text is not None and final_text != self._final_text:
            raise SpeechCommitContractError("speech final text changed")
        self._final_text = final_text
        if not self._generation_current():
            self._invalidate()
            return []
        if not self._commit_is_allowed():
            return []

        final_prefix = self._canonical_prefix(final_text)
        if self._committed_prefix and not final_prefix.startswith(
            self._committed_prefix
        ):
            raise SpeechCommitContractError(
                "committed speech is not an immutable final prefix"
            )

        if self.memory_bound:
            self.chunker.buf = ""
            chunks = self.chunker.push(final_text, max_chunks=None)
        else:
            chunks = self.chunker.push(self._pending, max_chunks=None)
        self._pending = ""
        chunks.extend(self.chunker.flush())
        commits = self._bind_chunks(chunks, final_text=final_text)
        self._closed = True
        return commits

    def cancel(self) -> None:
        self._invalidate()

    def _generation_current(self) -> bool:
        try:
            return bool(self.generation_is_current(self.response_generation))
        except Exception:
            return False

    def _commit_is_allowed(self) -> bool:
        if self.commit_allowed is None:
            return True
        try:
            return bool(self.commit_allowed())
        except Exception:
            return False

    @staticmethod
    def _canonical_prefix(text: str) -> str:
        return clean_text(
            strip_omnivoice_tags(clean_tts_text(text))
        )

    def _bind_chunks(
        self,
        chunks: list[str],
        *,
        final_text: str | None,
    ) -> list[SpeechCommit]:
        source_prefix = self._canonical_prefix(
            self._safe_stream if final_text is None else final_text
        )
        commits: list[SpeechCommit] = []
        for text in chunks:
            if not self._generation_current():
                self._invalidate()
                break
            speech_text = clean_tts_text(text)
            visible_chunk = self._canonical_prefix(speech_text)
            if not visible_chunk:
                continue
            candidate_prefix = clean_text(
                f"{self._committed_prefix} {visible_chunk}"
            )
            if not source_prefix.startswith(candidate_prefix):
                raise SpeechCommitContractError(
                    "speech candidate is not a safe output prefix"
                )
            commit = SpeechCommit(
                turn_id=self.turn_id,
                response_generation=self.response_generation,
                prefix_index=self._next_prefix_index,
                prefix_hash=hashlib.sha256(
                    candidate_prefix.encode("utf-8")
                ).hexdigest(),
                text=speech_text,
            )
            commits.append(commit)
            self._committed_prefix = candidate_prefix
            self._next_prefix_index += 1
        return commits

    def _invalidate(self) -> None:
        self._stale = True
        self._closed = True
        self._pending = ""
        self.chunker.buf = ""


def split_tts_sentences(
    buffer: str,
    *,
    force: bool = False,
    emitted_chunks: int = 0,
) -> tuple[list[str], str]:
    chunker = SpeechChunker(sent_first=emitted_chunks > 0)
    chunks = chunker.push(buffer or "", max_chunks=None)
    if not force:
        return chunks, chunker.buf
    chunks.extend(chunker.flush())
    return chunks, ""


async def prefetch_tts_sources(
    sentence_queue: "asyncio.Queue[str | None]",
    prepared_queue: "asyncio.Queue[object]",
    *,
    synthesize_source: Callable[[str, int], Awaitable[Any]],
    ready_timeout_sec: float,
    check_cancelled: Callable[[], None] | None = None,
    on_failure: Callable[[Exception], Any] | None = None,
) -> None:
    chunk_index = 0

    try:
        while True:
            if check_cancelled is not None:
                check_cancelled()
            sentence = await sentence_queue.get()
            if sentence is None:
                await prepared_queue.put(None)
                return

            sentence = clean_tts_text(sentence)
            if not sentence:
                continue

            if check_cancelled is not None:
                check_cancelled()
            chunk_index += 1
            source = await synthesize_source(sentence, chunk_index)
            wait_until_ready = getattr(source, "wait_until_ready", None)
            if wait_until_ready is not None:
                await wait_until_ready(timeout=max(0.2, float(ready_timeout_sec)))
            await prepared_queue.put((chunk_index, source))
    except Exception as exc:
        if on_failure is not None:
            on_failure(exc)
        await prepared_queue.put(exc)


class PreparedTtsPlaybackQueue:
    def __init__(
        self,
        prepared_queue: "asyncio.Queue[object]",
        playback_source: QueuedAudioSource,
        *,
        turn_id: str | None = None,
        session_key: str | None = None,
        lookahead_chunks: int = 1,
        lookahead_timeout_ms: float = 0.0,
        log: Callable[[str], None] | None = None,
        on_source_ready: Callable[[], Any] | None = None,
        on_failure: Callable[[Exception], Any] | None = None,
    ) -> None:
        self.prepared_queue = prepared_queue
        self.playback_source = playback_source
        self.turn_id = turn_id
        self.session_key = session_key
        self.lookahead_chunks = max(1, int(lookahead_chunks))
        self.lookahead_timeout_ms = max(0.0, float(lookahead_timeout_ms))
        self._log = log
        self._on_source_ready = on_source_ready
        self._on_failure = on_failure
        self.prepared_source_count = 0
        self.playback_finished = False

    def _emit(self, message: str) -> None:
        if self._log is not None:
            self._log(message)

    def handle_prepared_item(self, item: object) -> str:
        self._emit(f"[TTS PLAYBACK] prepared_item type={type(item).__name__}")
        if item is None:
            self._emit("[TTS PLAYBACK] received_sentinel")
            self.playback_finished = True
            self.playback_source.finish()
            return "done"
        if isinstance(item, Exception):
            self._emit(f"[TTS PLAYBACK] prepared_exception err={item!r}")
            if self._on_failure is not None:
                self._on_failure(item)
            self.playback_finished = True
            self.playback_source.finish()
            raise item

        _chunk_index, source = item
        self.prepared_source_count += 1
        self.playback_source.add_source(source)
        self._emit("[TTS PLAYBACK] source_added")
        if self._on_source_ready is not None:
            self._on_source_ready()
        return "source"

    async def fill_initial_lookahead(self) -> None:
        timeout_sec = self.lookahead_timeout_ms / 1000.0
        while self.prepared_source_count < self.lookahead_chunks and not self.playback_finished:
            try:
                item = await asyncio.wait_for(self.prepared_queue.get(), timeout=timeout_sec)
            except asyncio.TimeoutError:
                _log_turn_event(
                    "tts_playback_lookahead_timeout",
                    turn_id=self.turn_id,
                    session_key=self.session_key,
                    prepared_sources=self.prepared_source_count,
                    target_sources=self.lookahead_chunks,
                    timeout_ms=self.lookahead_timeout_ms,
                )
                return
            self.handle_prepared_item(item)


class PreparedPlaybackStarter:
    def __init__(
        self,
        playback_queue: PreparedTtsPlaybackQueue,
        *,
        create_playback_task: Callable[[], Any],
        on_started: Callable[[Any], Any] | None = None,
        log: Callable[[str], None] | None = None,
    ) -> None:
        self.playback_queue = playback_queue
        self._create_playback_task = create_playback_task
        self._on_started = on_started
        self._log = log
        self.playback_task: Any | None = None
        self.did_start = False

    def _emit(self, message: str) -> None:
        if self._log is not None:
            self._log(message)

    def start_once(self) -> Any | None:
        if self.playback_task is not None or self.playback_queue.prepared_source_count <= 0:
            return self.playback_task
        self.did_start = True
        self._emit("[TTS PLAYBACK] starting_discord_playback")
        self.playback_task = self._create_playback_task()
        if self._on_started is not None:
            self._on_started(self.playback_task)
        return self.playback_task

    def get_task(self) -> Any | None:
        return self.playback_task


async def drain_prepared_tts_playback(
    prepared_queue: "asyncio.Queue[object]",
    playback_queue: PreparedTtsPlaybackQueue,
    *,
    start_playback_once: Callable[[], Any],
    get_playback_task: Callable[[], Any | None],
    check_cancelled: Callable[[], None] | None = None,
) -> None:
    while True:
        if check_cancelled is not None:
            check_cancelled()
        item = await prepared_queue.get()
        state = playback_queue.handle_prepared_item(item)
        if state == "done":
            if get_playback_task() is None and playback_queue.prepared_source_count > 0:
                start_playback_once()
            break

        if get_playback_task() is None:
            await playback_queue.fill_initial_lookahead()
            start_playback_once()
            if playback_queue.playback_finished:
                break

    playback_task = get_playback_task()
    if playback_task is not None:
        await playback_task


@dataclass(slots=True)
class TtsPlaybackState:
    vc: Any | None = None
    sentence_queue: Any | None = None
    prepared_queue: Any | None = None
    playback_source: Any | None = None
    prefetch_task: Any | None = None
    playback_task: Any | None = None
    turn_id: str | None = None
    session_key: str | None = None
    source_type: str | None = None
    target: Any | None = None
    generation: object = field(default_factory=object, repr=False)

    @classmethod
    def from_mapping(cls, value: dict[str, Any]) -> "TtsPlaybackState":
        return cls(
            vc=value.get("vc"),
            sentence_queue=value.get("sentence_queue"),
            prepared_queue=value.get("prepared_queue"),
            playback_source=value.get("playback_source"),
            prefetch_task=value.get("prefetch_task"),
            playback_task=value.get("playback_task"),
            turn_id=value.get("turn_id"),
            session_key=value.get("session_key"),
            source_type=value.get("source_type"),
            target=value.get("target"),
            generation=value.get("_generation") or object(),
        )

    def to_mapping(self) -> dict[str, Any]:
        return {
            "vc": self.vc,
            "sentence_queue": self.sentence_queue,
            "prepared_queue": self.prepared_queue,
            "playback_source": self.playback_source,
            "prefetch_task": self.prefetch_task,
            "playback_task": self.playback_task,
            "turn_id": self.turn_id,
            "session_key": self.session_key,
            "source_type": self.source_type,
            "target": self.target,
        }

    def clear_runtime_refs(self) -> None:
        self.vc = None
        self.sentence_queue = None
        self.prepared_queue = None
        self.playback_source = None
        self.prefetch_task = None
        self.playback_task = None


class TtsPlaybackRegistry:
    def __init__(self) -> None:
        self._states: dict[int, TtsPlaybackState] = {}

    def __len__(self) -> int:
        return len(self._states)

    def __contains__(self, guild_id: object) -> bool:
        try:
            key = int(guild_id)  # type: ignore[arg-type]
        except (TypeError, ValueError):
            return False
        return key in self._states

    def keys(self) -> list[int]:
        return list(self._states.keys())

    def get(self, guild_id: int | None) -> dict[str, Any] | None:
        if guild_id is None:
            return None
        state = self._states.get(int(guild_id))
        return state.to_mapping() if state is not None else None

    def generation(self, guild_id: int | None) -> object | None:
        if guild_id is None:
            return None
        state = self._states.get(int(guild_id))
        return state.generation if state is not None else None

    def state(self, guild_id: int | None) -> TtsPlaybackState | None:
        if guild_id is None:
            return None
        return self._states.get(int(guild_id))

    def set(self, guild_id: int | None, **state: Any) -> dict[str, Any] | None:
        if guild_id is None:
            return None
        playback_state = TtsPlaybackState.from_mapping(state)
        self._states[int(guild_id)] = playback_state
        return playback_state.to_mapping()

    def update(self, guild_id: int | None, **state: Any) -> dict[str, Any] | None:
        if guild_id is None:
            return None
        key = int(guild_id)
        current = self._states.get(key)
        if current is None:
            return self.set(key, **state)
        merged = current.to_mapping()
        merged.update(state)
        merged["_generation"] = current.generation
        self._states[key] = TtsPlaybackState.from_mapping(merged)
        return self._states[key].to_mapping()

    def pop(self, guild_id: int | None) -> dict[str, Any] | None:
        if guild_id is None:
            return None
        state = self._states.pop(int(guild_id), None)
        return state.to_mapping() if state is not None else None

    def clear(self) -> None:
        self._states.clear()


@dataclass
class TtsPlaybackTracker:
    registry: TtsPlaybackRegistry = field(default_factory=TtsPlaybackRegistry)
    speaking_guilds: set[int] = field(default_factory=set)
    last_audio_end_at: dict[int, float] = field(default_factory=dict)


@dataclass(slots=True)
class TtsStreamingPlaybackRequest:
    vc: Any
    sentence_queue: "asyncio.Queue[str | None]"
    synthesize_source: Callable[[str, int], Awaitable[Any]]
    guild_id: int | None = None
    turn_id: str | None = None
    session_key: str | None = None
    metrics: dict[str, Any] | None = None
    ready_timeout_sec: float = 180.0
    prefetch_chunks: int = 2
    lookahead_chunks: int = 2
    lookahead_timeout_ms: float = 350.0
    create_task: Callable[[Awaitable[Any]], Any] | None = None
    check_cancelled: Callable[[], None] | None = None
    log: Callable[[str], None] | None = None
    on_prefetch_failure: Callable[[Exception], Any] | None = None
    on_prepared_failure: Callable[[Exception], Any] | None = None
    target: dict[str, Any] | None = None


@dataclass(slots=True)
class TtsSourcePlaybackRequest:
    vc: Any
    source: Any
    guild_id: int | None = None
    turn_id: str | None = None
    session_key: str | None = None
    metrics: dict[str, Any] | None = None
    trace_payload: dict[str, Any] = field(default_factory=dict)
    mark_speaking: bool = True
    mark_audio_end: bool = True
    clear_registry_on_finish: bool = True
    cleanup_source: bool = False
    target: dict[str, Any] | None = None


_SOURCE_METRICS_BINDING_UNSET = object()
_PLAYBACK_GENERATION_UNSET = object()


class TtsPlaybackManager:
    """Small facade over the existing playback tracker/registry helpers."""

    def __init__(
        self,
        tracker: TtsPlaybackTracker | None = None,
        *,
        target_is_current: Callable[[dict[str, Any]], bool] | None = None,
    ) -> None:
        self.tracker = tracker or TtsPlaybackTracker()
        self._source_metrics: dict[int, tuple[str, dict[str, Any]]] = {}
        self._target_is_current = target_is_current

    @staticmethod
    def _request_target(request: Any, guild_id: int | None) -> dict[str, Any]:
        target = dict(request.target or {})
        target.setdefault("guild_id", guild_id)
        target.setdefault("turn_id", request.turn_id)
        target.setdefault("session_key", request.session_key)
        return target

    def _request_is_current(self, target: dict[str, Any]) -> bool:
        callback = self._target_is_current
        if callback is None:
            return True
        try:
            return callback(dict(target)) is True
        except Exception:
            return False

    @staticmethod
    def _cleanup_rejected_source(source: Any) -> None:
        cleanup = getattr(source, "cleanup", None)
        if callable(cleanup):
            with contextlib.suppress(Exception):
                cleanup()

    def active_count(self) -> int:
        return tracked_tts_playback_count(self.tracker)

    def active_guild_ids(self) -> list[int]:
        return tracked_tts_playback_guild_ids(self.tracker)

    def is_active(self, guild_id: int | None) -> bool:
        return is_tracked_tts_playback_active(self.tracker, guild_id)

    def get(self, guild_id: int | None) -> dict[str, Any] | None:
        return get_tracked_tts_playback(self.tracker, guild_id)

    def start(self, *, guild_id: int | None, mark_speaking: bool = False, **state: Any) -> dict[str, Any] | None:
        return start_tts_playback_tracking(
            tracker=self.tracker,
            guild_id=guild_id,
            mark_speaking=mark_speaking,
            **state,
        )

    def update(self, *, guild_id: int | None, **state: Any) -> dict[str, Any] | None:
        return update_tts_playback_tracking(
            tracker=self.tracker,
            guild_id=guild_id,
            **state,
        )

    def mark_speaking(self, guild_id: int | None) -> None:
        mark_tts_speaking(tracker=self.tracker, guild_id=guild_id)

    def finish(
        self,
        *,
        guild_id: int | None,
        mark_audio_end: bool = False,
        now: float | None = None,
        clear_registry: bool = True,
        source_metrics_binding: tuple[str, dict[str, Any]] | None | object = (
            _SOURCE_METRICS_BINDING_UNSET
        ),
        playback_generation: object = _PLAYBACK_GENERATION_UNSET,
    ) -> None:
        generation_matches = bool(
            playback_generation is _PLAYBACK_GENERATION_UNSET
            or self.tracker.registry.generation(guild_id) is playback_generation
        )
        if generation_matches:
            finish_tts_playback_tracking(
                tracker=self.tracker,
                guild_id=guild_id,
                mark_audio_end=mark_audio_end,
                now=now,
                clear_registry=clear_registry,
            )
        if clear_registry and guild_id is not None:
            if source_metrics_binding is _SOURCE_METRICS_BINDING_UNSET:
                self._source_metrics.pop(int(guild_id), None)
            elif source_metrics_binding is not None:
                self._clear_source_metrics_binding(
                    guild_id,
                    source_metrics_binding,
                )

    def clear(self, guild_id: int | None) -> None:
        clear_tts_playback_tracking(tracker=self.tracker, guild_id=guild_id)
        if guild_id is not None:
            self._source_metrics.pop(int(guild_id), None)

    def _clear_source_metrics_binding(
        self,
        guild_id: int | None,
        binding: object,
    ) -> None:
        if guild_id is None:
            return
        key = int(guild_id)
        if self._source_metrics.get(key) is binding:
            self._source_metrics.pop(key, None)

    def input_suppression_reason(
        self,
        *,
        guild_id: int | None,
        post_tts_ignore_sec: float,
        now: float | None = None,
    ) -> str | None:
        return tts_input_suppression_reason(
            tracker=self.tracker,
            guild_id=guild_id,
            post_tts_ignore_sec=post_tts_ignore_sec,
            now=now,
        )

    def source_context(self, guild_id: int | None) -> dict[str, Any] | None:
        if guild_id is None:
            return None
        state = self.get(guild_id)
        tracked = self._source_metrics.get(int(guild_id))
        if state is None or tracked is None:
            return None
        source_turn_id = str(state.get("turn_id") or "").strip()
        if not source_turn_id or tracked[0] != source_turn_id:
            return None
        metrics = tracked[1]
        meta = (
            metrics.get("meta", {})
            if isinstance(metrics, dict) and isinstance(metrics.get("meta"), dict)
            else {}
        )

        def optional_text(value: Any) -> str | None:
            cleaned = str(value or "").strip()
            return cleaned or None

        return {
            "source_turn_id": source_turn_id,
            "source_session_key": optional_text(state.get("session_key")),
            "output_mode": "discord_voice",
            "validation_session_id": optional_text(meta.get("validation_session_id")),
            "validation_step_id": optional_text(meta.get("validation_step_id")),
            "validation_attempt_id": optional_text(meta.get("validation_attempt_id")),
        }

    async def cancel_guild(
        self,
        guild_id: int | None,
        *,
        now: float | None = None,
        reason: str = "interrupt",
    ) -> bool:
        state = self.get(guild_id)
        playback_generation = self.tracker.registry.generation(guild_id)
        source_metrics = (
            self._source_metrics.get(int(guild_id))
            if guild_id is not None
            else None
        )
        source_meta = (
            source_metrics[1].get("meta")
            if source_metrics is not None
            and isinstance(source_metrics[1], dict)
            and isinstance(source_metrics[1].get("meta"), dict)
            else None
        )
        should_mark_qualified_source = bool(
            reason == "qualified_user_audio"
            and state is not None
            and source_metrics is not None
            and source_metrics[0]
            and source_metrics[0] == str(state.get("turn_id") or "")
            and source_meta is not None
            and source_meta.get("playback_started") is True
        )
        lease_meta: dict[str, Any] | None = None
        original_meta_present = False
        original_meta: Any = None
        original_flag_present = False
        original_flag: Any = None
        if should_mark_qualified_source and source_metrics is not None:
            metrics = source_metrics[1]
            if isinstance(metrics, dict):
                original_meta_present = "meta" in metrics
                original_meta = metrics.get("meta")
                meta = metrics.get("meta")
                if not isinstance(meta, dict):
                    meta = {}
                    metrics["meta"] = meta
                lease_meta = meta
                original_flag_present = "qualified_tts_interrupt" in meta
                original_flag = meta.get("qualified_tts_interrupt")
                meta["qualified_tts_interrupt"] = True
        try:
            stopped = await stop_tracked_tts_playback(
                tracker=self.tracker,
                guild_id=guild_id,
                now=now,
                expected_generation=playback_generation,
            )
        except BaseException:
            stopped = False
            raise
        finally:
            if (
                not stopped
                and guild_id is not None
                and source_metrics is not None
                and isinstance(source_metrics[1], dict)
                and lease_meta is not None
            ):
                metrics = source_metrics[1]
                if isinstance(original_meta, dict):
                    if original_flag_present:
                        original_meta["qualified_tts_interrupt"] = original_flag
                    else:
                        original_meta.pop("qualified_tts_interrupt", None)
                elif metrics.get("meta") is lease_meta:
                    if original_meta_present:
                        metrics["meta"] = original_meta
                    else:
                        metrics.pop("meta", None)
        if stopped and guild_id is not None:
            current_source_metrics = self._source_metrics.get(int(guild_id))
            if current_source_metrics is source_metrics:
                self._source_metrics.pop(int(guild_id), None)
        return stopped

    async def cancel_turn(self, turn_id: str | None, *, now: float | None = None) -> bool:
        if not turn_id:
            return False
        stopped = False
        for guild_id in self.active_guild_ids():
            state = self.get(guild_id)
            if state and state.get("turn_id") == turn_id:
                stopped = bool(await self.cancel_guild(guild_id, now=now)) or stopped
        return stopped

    def snapshot(self, guild_id: int | None = None) -> dict[str, Any]:
        active_guild_ids = self.active_guild_ids()
        payload: dict[str, Any] = {
            "active_count": len(active_guild_ids),
            "active_guild_ids": active_guild_ids,
            "speaking_guild_ids": sorted(self.tracker.speaking_guilds),
            "last_audio_end_guild_ids": sorted(self.tracker.last_audio_end_at.keys()),
        }
        if guild_id is not None:
            payload["guild_id"] = int(guild_id)
            payload["guild_active"] = self.is_active(guild_id)
            payload["guild_state"] = self.get(guild_id)
            payload["guild_speaking"] = int(guild_id) in self.tracker.speaking_guilds
            payload["guild_last_audio_end_at"] = self.tracker.last_audio_end_at.get(int(guild_id))
        return payload

    async def play_source_once(self, request: TtsSourcePlaybackRequest) -> bool:
        guild_id = request.guild_id
        if guild_id is None:
            guild_id = getattr(getattr(request.vc, "guild", None), "id", None)
        target = self._request_target(request, guild_id)
        if not self._request_is_current(target):
            self._cleanup_rejected_source(request.source)
            return False
        playback_task = asyncio.current_task()
        self.start(
            guild_id=guild_id,
            mark_speaking=request.mark_speaking,
            vc=request.vc,
            playback_source=request.source,
            playback_task=playback_task,
            turn_id=request.turn_id,
            session_key=request.session_key,
            source_type=type(request.source).__name__,
            target=target,
        )
        playback_generation = self.tracker.registry.generation(guild_id)
        source_metrics_binding: tuple[str, dict[str, Any]] | None = None
        if guild_id is not None and request.metrics is not None and request.turn_id:
            source_metrics_binding = (
                str(request.turn_id),
                request.metrics,
            )
            self._source_metrics[int(guild_id)] = source_metrics_binding

        did_start = False
        completed = False

        def on_play_start() -> None:
            nonlocal did_start
            if self.tracker.registry.generation(guild_id) is playback_generation:
                mark_voice_latency_stage(
                    request.metrics,
                    "playback_first_write",
                )
            did_start = True
            mark_tts_playback_summary_state(request.metrics, started=True)

        try:
            trace_payload = {
                "turn_id": request.turn_id,
                "chunk_index": 1,
                "session_key": request.session_key,
                "source_type": type(request.source).__name__,
            }
            trace_payload.update(request.trace_payload)
            transport_completed = await play_audio_source(
                request.vc,
                request.source,
                on_play_start=on_play_start,
                validation_metadata=(
                    request.metrics.get("meta")
                    if isinstance(request.metrics, dict)
                    and isinstance(request.metrics.get("meta"), dict)
                    else None
                ),
                trace_payload=trace_payload,
                target_is_current=self._target_is_current,
                target=target,
            )
            completed = bool(transport_completed and did_start)
            return completed
        finally:
            if request.cleanup_source:
                cleanup = getattr(request.source, "cleanup", None)
                if cleanup is not None:
                    with contextlib.suppress(Exception):
                        cleanup()
            mark_tts_playback_summary_state(
                request.metrics,
                started=did_start,
                completed=completed,
            )
            self.finish(
                guild_id=guild_id,
                mark_audio_end=request.mark_audio_end and did_start,
                clear_registry=request.clear_registry_on_finish,
                source_metrics_binding=source_metrics_binding,
                playback_generation=playback_generation,
            )
            if source_metrics_binding is not None:
                self._clear_source_metrics_binding(guild_id, source_metrics_binding)

    async def stream_sentences(self, request: TtsStreamingPlaybackRequest) -> None:
        guild_id = request.guild_id
        if guild_id is None:
            guild_id = getattr(getattr(request.vc, "guild", None), "id", None)
        target = self._request_target(request, guild_id)
        if not self._request_is_current(target):
            _drain_tts_queue(request.sentence_queue)
            return
        did_speak = False
        playback_completed = False
        create_task = request.create_task or asyncio.create_task

        prepared_queue: asyncio.Queue[object] = asyncio.Queue(maxsize=max(1, int(request.prefetch_chunks)))
        playback_source = QueuedAudioSource(
            trace_payload={
                "turn_id": request.turn_id,
                "session_key": request.session_key,
                "source_type": "QueuedAudioSource",
            }
        )
        prefetch_task = create_task(
            prefetch_tts_sources(
                request.sentence_queue,
                prepared_queue,
                synthesize_source=request.synthesize_source,
                ready_timeout_sec=request.ready_timeout_sec,
                check_cancelled=request.check_cancelled,
                on_failure=request.on_prefetch_failure,
            )
        )
        playback_task: Any | None = None

        def create_playback_task() -> Any:
            if not self._request_is_current(target):
                playback_source.finish()
                return create_task(asyncio.sleep(0))
            return create_task(
                play_audio_source(
                    request.vc,
                    playback_source,
                    on_play_start=on_play_start,
                    validation_metadata=(
                        request.metrics.get("meta")
                        if isinstance(request.metrics, dict)
                        and isinstance(request.metrics.get("meta"), dict)
                        else None
                    ),
                    trace_payload={
                        "turn_id": request.turn_id,
                        "session_key": request.session_key,
                        "source_type": type(playback_source).__name__,
                    },
                    target_is_current=self._target_is_current,
                    target=target,
                )
            )

        def on_playback_task_created(task: Any) -> None:
            nonlocal playback_task
            playback_task = task
            self.update(guild_id=guild_id, playback_task=playback_task)

        def on_play_start() -> None:
            nonlocal did_speak
            if self.tracker.registry.generation(guild_id) is playback_generation:
                mark_voice_latency_stage(
                    request.metrics,
                    "playback_first_write",
                )
            did_speak = True
            mark_tts_playback_summary_state(request.metrics, started=True)

        def on_source_ready() -> None:
            if guild_id is not None and not did_speak:
                self.mark_speaking(guild_id)

        playback_queue = PreparedTtsPlaybackQueue(
            prepared_queue,
            playback_source,
            turn_id=request.turn_id,
            session_key=request.session_key,
            lookahead_chunks=request.lookahead_chunks,
            lookahead_timeout_ms=request.lookahead_timeout_ms,
            log=request.log,
            on_source_ready=on_source_ready,
            on_failure=request.on_prepared_failure,
        )
        playback_starter = PreparedPlaybackStarter(
            playback_queue,
            create_playback_task=create_playback_task,
            on_started=on_playback_task_created,
            log=request.log,
        )

        self.start(
            guild_id=guild_id,
            vc=request.vc,
            sentence_queue=request.sentence_queue,
            prepared_queue=prepared_queue,
            playback_source=playback_source,
            prefetch_task=prefetch_task,
            playback_task=playback_task,
            turn_id=request.turn_id,
            session_key=request.session_key,
            target=target,
        )
        playback_generation = self.tracker.registry.generation(guild_id)
        source_metrics_binding: tuple[str, dict[str, Any]] | None = None
        if guild_id is not None and request.metrics is not None and request.turn_id:
            source_metrics_binding = (
                str(request.turn_id),
                request.metrics,
            )
            self._source_metrics[int(guild_id)] = source_metrics_binding
        try:
            await drain_prepared_tts_playback(
                prepared_queue,
                playback_queue,
                start_playback_once=playback_starter.start_once,
                get_playback_task=playback_starter.get_task,
                check_cancelled=request.check_cancelled,
            )
            playback_completed = did_speak
        finally:
            mark_tts_playback_summary_state(
                request.metrics,
                started=did_speak,
                completed=playback_completed,
            )
            await cleanup_tts_stream_tasks(
                playback_source=playback_source,
                playback_task=playback_task,
                prefetch_task=prefetch_task,
            )
            self.finish(
                guild_id=guild_id,
                mark_audio_end=did_speak,
                source_metrics_binding=source_metrics_binding,
                playback_generation=playback_generation,
            )
            if source_metrics_binding is not None:
                self._clear_source_metrics_binding(guild_id, source_metrics_binding)


def _resolve_tracker_parts(
    tracker: TtsPlaybackTracker | None = None,
    *,
    registry: TtsPlaybackRegistry | None = None,
    speaking_guilds: set[int] | None = None,
    last_audio_end_at: dict[int, float] | None = None,
) -> tuple[TtsPlaybackRegistry | None, set[int] | None, dict[int, float] | None]:
    if tracker is not None:
        return (
            tracker.registry if registry is None else registry,
            tracker.speaking_guilds if speaking_guilds is None else speaking_guilds,
            tracker.last_audio_end_at if last_audio_end_at is None else last_audio_end_at,
        )
    return registry, speaking_guilds, last_audio_end_at


def clear_tts_playback_tracking(
    *,
    tracker: TtsPlaybackTracker | None = None,
    registry: TtsPlaybackRegistry | None = None,
    speaking_guilds: set[int] | None = None,
    last_audio_end_at: dict[int, float] | None = None,
    guild_id: int | None,
) -> None:
    if guild_id is None:
        return
    registry, speaking_guilds, last_audio_end_at = _resolve_tracker_parts(
        tracker,
        registry=registry,
        speaking_guilds=speaking_guilds,
        last_audio_end_at=last_audio_end_at,
    )
    key = int(guild_id)
    if registry is not None:
        registry.pop(key)
    if speaking_guilds is not None:
        speaking_guilds.discard(key)
    if last_audio_end_at is not None:
        last_audio_end_at.pop(key, None)


def mark_tts_speaking(
    *,
    tracker: TtsPlaybackTracker | None = None,
    speaking_guilds: set[int] | None = None,
    guild_id: int | None,
) -> None:
    _registry, speaking_guilds, _last_audio_end_at = _resolve_tracker_parts(
        tracker,
        speaking_guilds=speaking_guilds,
    )
    if speaking_guilds is None or guild_id is None:
        return
    speaking_guilds.add(int(guild_id))


def start_tts_playback_tracking(
    *,
    tracker: TtsPlaybackTracker | None = None,
    registry: TtsPlaybackRegistry | None = None,
    speaking_guilds: set[int] | None = None,
    guild_id: int | None,
    mark_speaking: bool = False,
    **state: Any,
) -> dict[str, Any] | None:
    registry, speaking_guilds, _last_audio_end_at = _resolve_tracker_parts(
        tracker,
        registry=registry,
        speaking_guilds=speaking_guilds,
    )
    if registry is None or guild_id is None:
        return None
    tracked_state = registry.set(guild_id, **state)
    if mark_speaking:
        mark_tts_speaking(speaking_guilds=speaking_guilds, guild_id=guild_id)
    return tracked_state


def update_tts_playback_tracking(
    *,
    tracker: TtsPlaybackTracker | None = None,
    registry: TtsPlaybackRegistry | None = None,
    guild_id: int | None,
    **state: Any,
) -> dict[str, Any] | None:
    registry, _speaking_guilds, _last_audio_end_at = _resolve_tracker_parts(tracker, registry=registry)
    if registry is None or guild_id is None:
        return None
    return registry.update(guild_id, **state)


def get_tracked_tts_playback(
    registry: TtsPlaybackRegistry | TtsPlaybackTracker | None,
    guild_id: int | None,
) -> dict[str, Any] | None:
    if isinstance(registry, TtsPlaybackTracker):
        registry = registry.registry
    if registry is None or guild_id is None:
        return None
    return registry.get(guild_id)


def is_tracked_tts_playback_active(
    registry: TtsPlaybackRegistry | TtsPlaybackTracker | None,
    guild_id: int | None,
) -> bool:
    if isinstance(registry, TtsPlaybackTracker):
        registry = registry.registry
    if registry is None or guild_id is None:
        return False
    return guild_id in registry


def tracked_tts_playback_count(registry: TtsPlaybackRegistry | TtsPlaybackTracker | None) -> int:
    if isinstance(registry, TtsPlaybackTracker):
        registry = registry.registry
    return len(registry) if registry is not None else 0


def tracked_tts_playback_guild_ids(registry: TtsPlaybackRegistry | TtsPlaybackTracker | None) -> list[int]:
    if isinstance(registry, TtsPlaybackTracker):
        registry = registry.registry
    return registry.keys() if registry is not None else []


def tts_input_suppression_reason(
    *,
    tracker: TtsPlaybackTracker | None = None,
    speaking_guilds: set[int] | None = None,
    last_audio_end_at: dict[int, float] | None = None,
    guild_id: int | None,
    post_tts_ignore_sec: float,
    now: float | None = None,
) -> str | None:
    if guild_id is None:
        return None
    _registry, speaking_guilds, last_audio_end_at = _resolve_tracker_parts(
        tracker,
        speaking_guilds=speaking_guilds,
        last_audio_end_at=last_audio_end_at,
    )
    key = int(guild_id)
    if speaking_guilds is not None and key in speaking_guilds:
        return "bot_is_speaking"
    if last_audio_end_at is None:
        return None
    now_value = time.monotonic() if now is None else float(now)
    if now_value - float(last_audio_end_at.get(key, 0.0) or 0.0) < float(post_tts_ignore_sec):
        return "post_tts_ignore"
    return None


def mark_tts_playback_summary_state(
    metrics: dict[str, Any] | None,
    *,
    started: bool | None = None,
    completed: bool | None = None,
    cancelled: bool | None = None,
) -> None:
    if metrics is None:
        return
    meta = metrics.setdefault("meta", {})
    if started is not None:
        meta["playback_started"] = bool(started)
    if completed is not None:
        meta["playback_completed"] = bool(completed)
    if cancelled is not None:
        meta["playback_cancelled"] = bool(cancelled)


def finish_tts_playback_tracking(
    *,
    tracker: TtsPlaybackTracker | None = None,
    registry: TtsPlaybackRegistry | None = None,
    speaking_guilds: set[int] | None = None,
    last_audio_end_at: dict[int, float] | None = None,
    guild_id: int | None,
    mark_audio_end: bool = False,
    now: float | None = None,
    clear_registry: bool = True,
) -> None:
    if guild_id is None:
        return
    registry, speaking_guilds, last_audio_end_at = _resolve_tracker_parts(
        tracker,
        registry=registry,
        speaking_guilds=speaking_guilds,
        last_audio_end_at=last_audio_end_at,
    )
    key = int(guild_id)
    if clear_registry and registry is not None:
        registry.pop(key)
    if speaking_guilds is not None:
        speaking_guilds.discard(key)
    if mark_audio_end and last_audio_end_at is not None:
        last_audio_end_at[key] = time.monotonic() if now is None else float(now)


def _drain_tts_queue(queue_value: Any) -> None:
    if queue_value is None:
        return
    while True:
        try:
            item = queue_value.get_nowait()
        except (asyncio.QueueEmpty, queue.Empty):
            break
        source = item[1] if isinstance(item, tuple) and len(item) == 2 else None
        cleanup = getattr(source, "cleanup", None)
        if callable(cleanup):
            with contextlib.suppress(Exception):
                cleanup()
        task_done = getattr(queue_value, "task_done", None)
        if callable(task_done):
            with contextlib.suppress(Exception):
                task_done()


def _clear_tts_state_refs(state: dict[str, Any]) -> None:
    for key in (
        "vc",
        "sentence_queue",
        "prepared_queue",
        "playback_source",
        "prefetch_task",
        "playback_task",
    ):
        state[key] = None


async def stop_tts_playback_state(
    state: dict[str, Any] | None,
    *,
    cleanup_timeout_sec: float | None = None,
) -> bool:
    if not state:
        return False

    vc = state.get("vc")
    playback_task = state.get("playback_task")
    prefetch_task = state.get("prefetch_task")
    sentence_queue = state.get("sentence_queue")
    prepared_queue = state.get("prepared_queue")
    playback_source = state.get("playback_source")

    stopped = True
    if playback_source is not None:
        try:
            playback_source.finish()
        except Exception:
            stopped = False
    if vc is not None:
        try:
            vc_active = bool(vc.is_playing() or vc.is_paused())
        except Exception:
            vc_active = True
            stopped = False
        if vc_active:
            try:
                vc.stop()
            except Exception:
                stopped = False

    # These queues are bounded during streaming. Never await a sentinel write:
    # a full orphaned queue would postpone vc.stop() and let a replacement
    # generation become the victim of this teardown. Task cancellation below
    # is the authoritative shutdown signal when a queue is already full.
    for queue in (sentence_queue, prepared_queue):
        if queue is None:
            continue
        with contextlib.suppress(Exception):
            queue.put_nowait(None)

    tasks_to_join: list[Any] = []
    for task in (playback_task, prefetch_task):
        if task is None:
            continue
        try:
            if task.done():
                continue
            task.cancel()
            tasks_to_join.append(task)
        except Exception:
            stopped = False
    if tasks_to_join and cleanup_timeout_sec is not None:
        done, pending = await asyncio.wait(
            tasks_to_join,
            timeout=max(0.0, float(cleanup_timeout_sec)),
        )
        stopped = stopped and not pending
        for task in done:
            with contextlib.suppress(asyncio.CancelledError, Exception):
                task.result()
    else:
        for task in tasks_to_join:
            try:
                await task
            except asyncio.CancelledError:
                pass
            except Exception:
                stopped = False
    _drain_tts_queue(sentence_queue)
    _drain_tts_queue(prepared_queue)
    cleanup = getattr(playback_source, "cleanup", None)
    if callable(cleanup):
        try:
            cleanup()
        except Exception:
            stopped = False
    if stopped:
        _clear_tts_state_refs(state)
    return stopped


async def stop_tracked_tts_playback(
    *,
    tracker: TtsPlaybackTracker | None = None,
    registry: TtsPlaybackRegistry | None = None,
    speaking_guilds: set[int] | None = None,
    last_audio_end_at: dict[int, float] | None = None,
    guild_id: int | None,
    now: float | None = None,
    expected_generation: object = _PLAYBACK_GENERATION_UNSET,
    cleanup_timeout_sec: float | None = None,
) -> bool:
    registry, speaking_guilds, last_audio_end_at = _resolve_tracker_parts(
        tracker,
        registry=registry,
        speaking_guilds=speaking_guilds,
        last_audio_end_at=last_audio_end_at,
    )
    if registry is None or guild_id is None:
        return False
    playback_generation = registry.generation(guild_id)
    if (
        expected_generation is not _PLAYBACK_GENERATION_UNSET
        and playback_generation is not expected_generation
    ):
        return False
    playback_state = registry.state(guild_id)
    state = playback_state.to_mapping() if playback_state is not None else None
    if state is None:
        return False
    stopped = await stop_tts_playback_state(
        state,
        cleanup_timeout_sec=cleanup_timeout_sec,
    )
    if not stopped:
        return False
    playback_state.clear_runtime_refs()
    if registry.generation(guild_id) is playback_generation:
        finish_tts_playback_tracking(
            registry=registry,
            speaking_guilds=speaking_guilds,
            last_audio_end_at=last_audio_end_at,
            guild_id=guild_id,
            mark_audio_end=True,
            now=now,
        )
    return True


async def cleanup_tts_playback_targets(
    target_predicate: Callable[[dict[str, Any]], bool],
    *,
    tracker: TtsPlaybackTracker | None = None,
    registry: TtsPlaybackRegistry | None = None,
    speaking_guilds: set[int] | None = None,
    last_audio_end_at: dict[int, float] | None = None,
    cleanup_timeout_sec: float = 1.0,
) -> tuple[int, int]:
    """Stop exact matching generations and report (removed, still_recalled)."""

    registry, speaking_guilds, last_audio_end_at = _resolve_tracker_parts(
        tracker,
        registry=registry,
        speaking_guilds=speaking_guilds,
        last_audio_end_at=last_audio_end_at,
    )
    if registry is None:
        return 0, 0
    removed = 0
    for guild_id in registry.keys():
        generation = registry.generation(guild_id)
        state = registry.get(guild_id)
        if state is None or not target_predicate(state):
            continue
        if await stop_tracked_tts_playback(
            registry=registry,
            speaking_guilds=speaking_guilds,
            last_audio_end_at=last_audio_end_at,
            guild_id=guild_id,
            expected_generation=generation,
            cleanup_timeout_sec=cleanup_timeout_sec,
        ):
            removed += 1
    remaining = sum(
        target_predicate(state)
        for guild_id in registry.keys()
        if (state := registry.get(guild_id)) is not None
    )
    return removed, remaining


async def cleanup_tts_stream_tasks(
    *,
    playback_source: Any | None = None,
    playback_task: Any | None = None,
    prefetch_task: Any | None = None,
) -> None:
    if playback_source is not None:
        with contextlib.suppress(Exception):
            playback_source.finish()
    if playback_task is not None and not playback_task.done():
        playback_task.cancel()
        with contextlib.suppress(asyncio.CancelledError, Exception):
            await playback_task
    if prefetch_task is not None and not prefetch_task.done():
        prefetch_task.cancel()
        with contextlib.suppress(asyncio.CancelledError, Exception):
            await prefetch_task
    cleanup = getattr(playback_source, "cleanup", None)
    if callable(cleanup):
        with contextlib.suppress(Exception):
            cleanup()


async def wait_until_not_playing(vc: discord.VoiceClient) -> None:
    while vc.is_playing() or vc.is_paused():
        await asyncio.sleep(0.05)


class _PlaybackReceiptAudioSource(discord.AudioSource):
    def __init__(
        self,
        source: discord.AudioSource,
        on_first_nonzero_frame: Callable[[], None],
    ) -> None:
        self._source = source
        self._on_first_nonzero_frame = on_first_nonzero_frame
        self._sent_nonzero_frame = False

    def read(self) -> bytes:
        return self._source.read()

    def mark_packet_sent(self, chunk: bytes) -> None:
        raw_mark_packet_sent = getattr(self._source, "mark_packet_sent", None)
        try:
            if callable(raw_mark_packet_sent):
                raw_mark_packet_sent(chunk)
        finally:
            if chunk and not self._sent_nonzero_frame and any(chunk):
                self._sent_nonzero_frame = True
                self._on_first_nonzero_frame()

    def is_opus(self) -> bool:
        return self._source.is_opus()

    def cleanup(self) -> None:
        cleanup = getattr(self._source, "cleanup", None)
        if callable(cleanup):
            cleanup()


async def play_audio_source(
    vc: discord.VoiceClient,
    source: discord.AudioSource,
    *,
    on_play_start: Callable[[], None] | None = None,
    validation_metadata: dict[str, Any] | None = None,
    trace_payload: dict[str, Any] | None = None,
    timeout_sec: float = OMNIVOICE_TIMEOUT_SEC,
    target_is_current: Callable[[dict[str, Any]], bool] | None = None,
    target: dict[str, Any] | None = None,
) -> bool:
    raw_source = source
    payload = dict(trace_payload or {})
    payload.setdefault("source_type", type(source).__name__)
    timeout_sec = max(0.001, float(timeout_sec))
    prior_source = getattr(vc, "source", None)
    try:
        await asyncio.wait_for(
            wait_until_not_playing(vc),
            timeout=timeout_sec,
        )
    except asyncio.TimeoutError:
        if (
            prior_source is not None
            and getattr(vc, "source", None) is prior_source
        ):
            with contextlib.suppress(Exception):
                vc.stop()
        error = TimeoutError("discord_playback_idle_timeout")
        _log_turn_event(
            "discord_playback_exception",
            **merge_log_event_payload(
                explicit={"stage": "wait_until_idle", "error": repr(error)},
                extra=payload,
            ),
        )
        raise error from None

    if target_is_current is not None:
        try:
            current = target_is_current(dict(target or {})) is True
        except Exception:
            current = False
        if not current:
            cleanup = getattr(raw_source, "cleanup", None)
            if callable(cleanup):
                with contextlib.suppress(Exception):
                    cleanup()
            _log_turn_event("discord_playback_stale_target_blocked", **payload)
            return False

    if not validation_attempt_binding_is_current(
        validation_metadata,
        surface="discord",
        reject_unbound_when_active=True,
    ):
        _log_turn_event("discord_playback_stale_validation_blocked", **payload)
        return False
    done = asyncio.Event()
    playback_error: list[Exception | None] = [None]
    loop = asyncio.get_running_loop()

    def after_play(err: Exception | None) -> None:
        if err:
            playback_error[0] = err
        loop.call_soon_threadsafe(done.set)

    if on_play_start is not None:
        def dispatch_play_start() -> None:
            loop.call_soon_threadsafe(on_play_start)

        source = _PlaybackReceiptAudioSource(source, dispatch_play_start)

    try:
        _log_turn_event("discord_playback_play_invoked", **payload)
        vc.play(source, after=after_play)
        await asyncio.wait_for(done.wait(), timeout=timeout_sec)
    except asyncio.CancelledError:
        if getattr(vc, "source", None) is source:
            with contextlib.suppress(Exception):
                vc.stop()
        raise
    except asyncio.TimeoutError:
        if getattr(vc, "source", None) is source:
            with contextlib.suppress(Exception):
                vc.stop()
        error = TimeoutError("discord_playback_callback_timeout")
        _log_turn_event(
            "discord_playback_exception",
            **merge_log_event_payload(
                explicit={"stage": "after_play", "error": repr(error)},
                extra=payload,
            ),
        )
        raise error from None
    except Exception as exc:
        _log_turn_event(
            "discord_playback_exception",
            **merge_log_event_payload(
                explicit={
                    "stage": "vc_play",
                    "error": "discord_playback_failed",
                    "error_type": type(exc).__name__,
                },
                extra=payload,
            ),
        )
        raise

    if playback_error[0] is not None:
        _log_turn_event(
            "discord_playback_exception",
            **merge_log_event_payload(
                explicit={
                    "stage": "after_play",
                    "error": "discord_playback_failed",
                    "error_type": type(playback_error[0]).__name__,
                },
                extra=payload,
            ),
        )
        raise playback_error[0]

    if (
        isinstance(raw_source, (OmniVoicePCMStream, QueuedAudioSource))
        and raw_source.error is not None
    ):
        _log_turn_event(
            "discord_playback_exception",
            **merge_log_event_payload(
                explicit={
                    "stage": "source_error",
                    "error": "discord_playback_failed",
                    "error_type": type(raw_source.error).__name__,
                },
                extra=payload,
            ),
        )
        raise raw_source.error

    _log_turn_event("discord_playback_finished", **payload)
    return True


class TTSQueueSink:
    def __init__(
        self,
        sentence_queue: "asyncio.Queue[str | None]",
        *,
        log: Callable[[str], None] | None = None,
    ) -> None:
        self.sentence_queue = sentence_queue
        self.queued_sentence_count = 0
        self._log = log

    def _emit(self, message: str) -> None:
        if self._log is not None:
            self._log(message)

    async def on_chunk(self, text: str) -> None:
        cleaned = clean_tts_text(text)
        self._emit(f"[TTS QUEUE] on_chunk raw={text!r} cleaned={cleaned!r}")
        if not cleaned:
            self._emit("[TTS QUEUE] drop_empty_chunk")
            return
        self.queued_sentence_count += 1
        await self.sentence_queue.put(cleaned)
        self._emit(f"[TTS QUEUE] queued count={self.queued_sentence_count} qsize={self.sentence_queue.qsize()}")

    async def close(self, _final_text: str) -> None:
        self._emit(f"[TTS QUEUE] close qsize_before={self.sentence_queue.qsize()}")
        await self.sentence_queue.put(None)


class StreamingVoiceDelivery:
    def __init__(
        self,
        sentence_queue: "asyncio.Queue[str | None]",
        tts_sink: TTSQueueSink,
        playback_task: asyncio.Task,
        *,
        metrics: dict,
        log_stage: Callable[..., Any] | None = None,
        prefetch_chunks: int | None = None,
    ):
        self.sentence_queue = sentence_queue
        self.tts_sink = tts_sink
        self.playback_task = playback_task
        self.metrics = metrics
        self._log_stage = log_stage
        self._prefetch_chunks = prefetch_chunks

    async def on_chunk(self, text: str) -> None:
        await self.tts_sink.on_chunk(text)

    async def close(self, final_text: str) -> None:
        await self.tts_sink.close(final_text)

    async def finalize(self) -> int:
        if self._log_stage is not None:
            self._log_stage(
                self.metrics,
                "sentence TTS queued",
                extra=f"sentence_count={self.tts_sink.queued_sentence_count} prefetch={self._prefetch_chunks}",
            )
        await self.playback_task
        return self.tts_sink.queued_sentence_count

    async def abort(self) -> None:
        if not self.playback_task.done():
            await self.sentence_queue.put(None)
            self.playback_task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await self.playback_task


class LazyStreamingVoiceDelivery:
    """Start ordinary speech eagerly; buffer memory-bound speech to handoff."""

    def __init__(
        self,
        sentence_queue: "asyncio.Queue[str | None]",
        tts_sink: TTSQueueSink,
        playback_task_factory: Callable[[], asyncio.Task],
        *,
        metrics: dict,
        log_stage: Callable[..., Any] | None = None,
        prefetch_chunks: int | None = None,
        eager_start_allowed: Callable[[], bool] | None = None,
    ) -> None:
        self.sentence_queue = sentence_queue
        self.tts_sink = tts_sink
        self._playback_task_factory = playback_task_factory
        self.playback_task: asyncio.Task | None = None
        self.metrics = metrics
        self._log_stage = log_stage
        self._prefetch_chunks = prefetch_chunks
        self._eager_start_allowed = eager_start_allowed

    def _ensure_started(self) -> asyncio.Task:
        if self.playback_task is None:
            self.playback_task = self._playback_task_factory()
        return self.playback_task

    async def on_chunk(self, text: str) -> None:
        await self.tts_sink.on_chunk(text)
        if (
            self._eager_start_allowed is not None
            and self._eager_start_allowed()
        ):
            self._ensure_started()

    async def close(self, final_text: str) -> None:
        self._ensure_started()
        await self.tts_sink.close(final_text)

    async def finalize(self) -> int:
        if self._log_stage is not None:
            self._log_stage(
                self.metrics,
                "sentence TTS queued",
                extra=(
                    "sentence_count="
                    f"{self.tts_sink.queued_sentence_count} "
                    f"prefetch={self._prefetch_chunks}"
                ),
            )
        await self._ensure_started()
        return self.tts_sink.queued_sentence_count

    async def abort(self) -> None:
        task = self.playback_task
        if task is not None and not task.done():
            await self.sentence_queue.put(None)
            task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await task
