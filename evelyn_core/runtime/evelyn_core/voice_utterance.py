from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Mapping


DISCORD_PCM_RATE = 48000
DISCORD_PCM_CHANNELS = 2
DISCORD_PCM_SAMPLE_WIDTH = 2


@dataclass(frozen=True)
class UtteranceAssemblyConfig:
    enabled: bool = True
    commit_wait_sec: float = 0.22
    pad_ms: int = 180
    max_audio_sec: float = 14.0


def discord_pcm_silence(*, ms: int, rate: int = DISCORD_PCM_RATE, channels: int = DISCORD_PCM_CHANNELS) -> bytes:
    frame_count = max(0, int(round(rate * (max(0, ms) / 1000.0))))
    return b"\x00" * frame_count * channels * DISCORD_PCM_SAMPLE_WIDTH


def discord_pcm_seconds(pcm_bytes: bytes, *, rate: int = DISCORD_PCM_RATE, channels: int = DISCORD_PCM_CHANNELS) -> float:
    bytes_per_second = max(1, rate * channels * DISCORD_PCM_SAMPLE_WIDTH)
    return len(pcm_bytes or b"") / float(bytes_per_second)


def merge_discord_pcm_segments(segments: list[bytes], *, pad_ms: int) -> bytes:
    non_empty = [bytes(segment) for segment in segments if segment]
    if not non_empty:
        return b""
    if len(non_empty) == 1:
        return non_empty[0]
    pad = discord_pcm_silence(ms=pad_ms)
    chunks: list[bytes] = []
    for index, segment in enumerate(non_empty):
        if index:
            chunks.append(pad)
        chunks.append(segment)
    return b"".join(chunks)


def merge_debug_meta(
    base_meta: Mapping[str, Any] | None,
    *,
    segment_count: int,
    added_pad_ms: int,
    total_audio_sec: float,
) -> dict[str, Any]:
    meta = dict(base_meta or {})
    meta["utterance_assembly"] = {
        "enabled": True,
        "segment_count": int(max(1, segment_count)),
        "added_pad_ms": int(max(0, added_pad_ms)),
        "total_audio_sec": round(max(0.0, total_audio_sec), 3),
    }
    if segment_count > 1:
        meta["assembled_utterance"] = True
    return meta
