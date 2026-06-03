import asyncio
import audioop
import contextlib
import queue
import re
import threading
import time
import wave
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Awaitable, Callable

import discord

from .text import clean_text, clean_tts_text

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
        self._done = True
        self._ready_event.set()
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
                if self._on_first_packet_sent is not None:
                    self._on_first_packet_sent()
            return chunk

        if self._done and self._buffer:
            chunk = bytes(self._buffer)
            self._buffer.clear()
            padded = chunk + (b"\x00" * (DISCORD_FRAME_BYTES - len(chunk)))
            if not self._first_frame_sent and any(padded):
                self._first_frame_sent = True
                if self._on_first_frame is not None:
                    self._on_first_frame()
                if self._on_first_packet_sent is not None:
                    self._on_first_packet_sent()
            return padded

        return b""

    def cleanup(self) -> None:
        self._closed = True
        self._done = True
        self._ready_event.set()
        try:
            self._queue.put_nowait(None)
        except Exception:
            pass


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
        self._first_frame_sent = False
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

        if not self._first_frame_sent and any(chunk):
            self._first_frame_sent = True
            if self._on_first_packet_sent is not None:
                self._on_first_packet_sent()
        return chunk

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
            if visible_len < window.min_chars:
                continue

            is_hard = ch in self.config.hard_breaks
            is_soft = ch in self.config.soft_breaks
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

        if best_idx is None and clean_len >= window.max_chars:
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
        }


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


class TtsPlaybackManager:
    """Small facade over the existing playback tracker/registry helpers."""

    def __init__(self, tracker: TtsPlaybackTracker | None = None) -> None:
        self.tracker = tracker or TtsPlaybackTracker()

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
    ) -> None:
        finish_tts_playback_tracking(
            tracker=self.tracker,
            guild_id=guild_id,
            mark_audio_end=mark_audio_end,
            now=now,
            clear_registry=clear_registry,
        )

    def clear(self, guild_id: int | None) -> None:
        clear_tts_playback_tracking(tracker=self.tracker, guild_id=guild_id)

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

    async def cancel_guild(self, guild_id: int | None, *, now: float | None = None) -> bool:
        return await stop_tracked_tts_playback(
            tracker=self.tracker,
            guild_id=guild_id,
            now=now,
        )

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
        )

        completed = False
        try:
            trace_payload = {
                "turn_id": request.turn_id,
                "chunk_index": 1,
                "session_key": request.session_key,
                "source_type": type(request.source).__name__,
            }
            trace_payload.update(request.trace_payload)
            await play_audio_source(
                request.vc,
                request.source,
                trace_payload=trace_payload,
            )
            completed = True
            return True
        finally:
            if request.cleanup_source:
                cleanup = getattr(request.source, "cleanup", None)
                if cleanup is not None:
                    with contextlib.suppress(Exception):
                        cleanup()
            mark_tts_playback_summary_state(request.metrics, started=completed, completed=completed)
            self.finish(
                guild_id=guild_id,
                mark_audio_end=request.mark_audio_end,
                clear_registry=request.clear_registry_on_finish,
            )

    async def stream_sentences(self, request: TtsStreamingPlaybackRequest) -> None:
        guild_id = request.guild_id
        if guild_id is None:
            guild_id = getattr(getattr(request.vc, "guild", None), "id", None)
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
            return create_task(
                play_audio_source(
                    request.vc,
                    playback_source,
                    trace_payload={
                        "turn_id": request.turn_id,
                        "session_key": request.session_key,
                        "source_type": type(playback_source).__name__,
                    },
                )
            )

        def on_playback_started(task: Any) -> None:
            nonlocal did_speak, playback_task
            did_speak = True
            playback_task = task
            mark_tts_playback_summary_state(request.metrics, started=True)
            self.update(guild_id=guild_id, playback_task=playback_task)

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
            on_started=on_playback_started,
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
        )
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
            )


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


async def stop_tts_playback_state(state: dict[str, Any] | None) -> None:
    if not state:
        return

    vc = state.get("vc")
    playback_task = state.get("playback_task")
    prefetch_task = state.get("prefetch_task")
    sentence_queue = state.get("sentence_queue")
    prepared_queue = state.get("prepared_queue")
    playback_source = state.get("playback_source")

    if sentence_queue is not None:
        with contextlib.suppress(Exception):
            await sentence_queue.put(None)
    if prepared_queue is not None:
        with contextlib.suppress(Exception):
            await prepared_queue.put(None)
    if playback_source is not None:
        with contextlib.suppress(Exception):
            playback_source.finish()
    if vc is not None and (vc.is_playing() or vc.is_paused()):
        with contextlib.suppress(Exception):
            vc.stop()
    if playback_task is not None and not playback_task.done():
        playback_task.cancel()
        with contextlib.suppress(asyncio.CancelledError, Exception):
            await playback_task
    if prefetch_task is not None and not prefetch_task.done():
        prefetch_task.cancel()
        with contextlib.suppress(asyncio.CancelledError, Exception):
            await prefetch_task


async def stop_tracked_tts_playback(
    *,
    tracker: TtsPlaybackTracker | None = None,
    registry: TtsPlaybackRegistry | None = None,
    speaking_guilds: set[int] | None = None,
    last_audio_end_at: dict[int, float] | None = None,
    guild_id: int | None,
    now: float | None = None,
) -> bool:
    registry, speaking_guilds, last_audio_end_at = _resolve_tracker_parts(
        tracker,
        registry=registry,
        speaking_guilds=speaking_guilds,
        last_audio_end_at=last_audio_end_at,
    )
    if registry is None or guild_id is None:
        return False
    state = registry.get(guild_id)
    if not state:
        return False
    await stop_tts_playback_state(state)
    finish_tts_playback_tracking(
        registry=registry,
        speaking_guilds=speaking_guilds,
        last_audio_end_at=last_audio_end_at,
        guild_id=guild_id,
        mark_audio_end=True,
        now=now,
    )
    return True


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


async def wait_until_not_playing(vc: discord.VoiceClient) -> None:
    while vc.is_playing() or vc.is_paused():
        await asyncio.sleep(0.05)


async def play_audio_source(
    vc: discord.VoiceClient,
    source: discord.AudioSource,
    *,
    on_play_start: Callable[[], None] | None = None,
    trace_payload: dict[str, Any] | None = None,
) -> None:
    await wait_until_not_playing(vc)

    payload = dict(trace_payload or {})
    payload.setdefault("source_type", type(source).__name__)
    done = asyncio.Event()
    playback_error: list[Exception | None] = [None]
    loop = asyncio.get_running_loop()

    def after_play(err: Exception | None) -> None:
        if err:
            playback_error[0] = err
        loop.call_soon_threadsafe(done.set)

    try:
        if on_play_start is not None:
            on_play_start()
        _log_turn_event("discord_playback_play_invoked", **payload)
        vc.play(source, after=after_play)
        await done.wait()
    except Exception as exc:
        _log_turn_event(
            "discord_playback_exception",
            **merge_log_event_payload(explicit={"stage": "vc_play", "error": repr(exc)}, extra=payload),
        )
        raise

    if playback_error[0] is not None:
        _log_turn_event(
            "discord_playback_exception",
            **merge_log_event_payload(explicit={"stage": "after_play", "error": repr(playback_error[0])}, extra=payload),
        )
        raise playback_error[0]

    if isinstance(source, (OmniVoicePCMStream, QueuedAudioSource)) and source.error is not None:
        _log_turn_event(
            "discord_playback_exception",
            **merge_log_event_payload(explicit={"stage": "source_error", "error": repr(source.error)}, extra=payload),
        )
        raise source.error

    _log_turn_event("discord_playback_finished", **payload)


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
