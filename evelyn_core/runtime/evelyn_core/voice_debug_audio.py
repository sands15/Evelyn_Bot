from __future__ import annotations

import asyncio
import re
import time
import wave
from pathlib import Path
from typing import Any, Callable

import numpy as np

from .json_safety import safe_json_dumps, safe_json_value
from .text import clean_text


def sanitize_debug_label(value: str | None, *, fallback: str = "unknown") -> str:
    text = (value or "").strip()
    text = re.sub(r"[^0-9A-Za-z가-힣._-]+", "_", text)
    text = text.strip("._-")
    return text or fallback


def resolve_voice_debug_base_dir(project_root: Path, configured_dir: str) -> Path:
    base_dir = Path(configured_dir)
    if not base_dir.is_absolute():
        base_dir = project_root / base_dir
    return base_dir


def trim_voice_debug_dir(guild_dir: Path, *, max_files: int) -> None:
    if max_files <= 0 or not guild_dir.exists():
        return
    wavs = sorted(guild_dir.glob("*.wav"), key=lambda path: path.stat().st_mtime)
    overflow = len(wavs) - int(max_files)
    if overflow <= 0:
        return
    for path in wavs[:overflow]:
        try:
            path.unlink()
        except Exception:
            pass


def build_voice_debug_audio_item(
    *,
    guild_id: int,
    speaker: str,
    pcm_bytes: bytes,
    audio16k: np.ndarray,
    wake_probe: str | None = None,
    final_text: str | None = None,
    debug_meta: dict | None = None,
    save_stt_audio: bool = True,
    stt_meta: dict | None = None,
    session_key: str | None = None,
    stage_label: str | None = None,
) -> dict[str, Any]:
    return {
        "guild_id": guild_id,
        "speaker": speaker,
        "pcm_bytes": pcm_bytes,
        "audio16k": np.array(audio16k, copy=True),
        "wake_probe": wake_probe,
        "final_text": final_text,
        "debug_meta": dict(debug_meta) if isinstance(debug_meta, dict) else debug_meta,
        "save_stt_audio": save_stt_audio,
        "stt_meta": dict(stt_meta) if isinstance(stt_meta, dict) else stt_meta,
        "session_key": session_key,
        "stage_label": stage_label,
    }


def voice_debug_drop_message(*, speaker: str, stage_label: str | None) -> str:
    stage = clean_text(stage_label or "ingress") or "ingress"
    return f"[VOICE DEBUG DROP] speaker={speaker} stage={stage} reason=queue_full"


async def debug_write_worker_from_runtime(
    *,
    queue: Any,
    save_now: Callable[..., Any],
    to_thread: Callable[..., Any] = asyncio.to_thread,
    log: Callable[[str], Any] = print,
) -> None:
    while True:
        item = await queue.get()
        try:
            await to_thread(save_now, **item)
        except Exception as exc:
            log(f"[VOICE DEBUG WORKER FAIL] err={exc!r}")
        finally:
            queue.task_done()


def ensure_debug_write_worker_started_from_runtime(
    *,
    current_task: Any,
    create_task: Callable[[Any], Any],
    worker_coro_factory: Callable[[], Any],
) -> Any:
    if current_task is not None and not current_task.done():
        return current_task
    return create_task(worker_coro_factory())


def enqueue_voice_debug_audio_from_runtime(
    *,
    enabled: bool,
    ensure_worker_started: Callable[[], Any],
    queue: Any,
    log: Callable[[str], Any],
    guild_id: int,
    speaker: str,
    pcm_bytes: bytes,
    audio16k: np.ndarray,
    wake_probe: str | None = None,
    final_text: str | None = None,
    debug_meta: dict | None = None,
    save_stt_audio: bool = True,
    stt_meta: dict | None = None,
    session_key: str | None = None,
    stage_label: str | None = None,
) -> bool:
    if not enabled:
        return False
    ensure_worker_started()
    item = build_voice_debug_audio_item(
        guild_id=guild_id,
        speaker=speaker,
        pcm_bytes=pcm_bytes,
        audio16k=audio16k,
        wake_probe=wake_probe,
        final_text=final_text,
        debug_meta=debug_meta,
        save_stt_audio=save_stt_audio,
        stt_meta=stt_meta,
        session_key=session_key,
        stage_label=stage_label,
    )
    try:
        queue.put_nowait(item)
    except asyncio.QueueFull:
        log(voice_debug_drop_message(speaker=speaker, stage_label=stage_label))
        return False
    return True


def save_voice_debug_audio_now(
    *,
    project_root: Path,
    configured_dir: str,
    max_files_per_guild: int,
    raw_channels: int,
    raw_rate: int,
    stt_rate: int,
    counts: dict[int, int],
    stems: dict[tuple[int, str, str, str], str],
    log: Callable[[str], None] = print,
    guild_id: int,
    speaker: str,
    pcm_bytes: bytes,
    audio16k: np.ndarray,
    wake_probe: str | None = None,
    final_text: str | None = None,
    debug_meta: dict | None = None,
    save_stt_audio: bool = True,
    stt_meta: dict | None = None,
    session_key: str | None = None,
    stage_label: str | None = None,
) -> None:
    try:
        base_dir = resolve_voice_debug_base_dir(project_root, configured_dir)
        guild_dir = base_dir / str(guild_id)
        guild_dir.mkdir(parents=True, exist_ok=True)

        meta_turn_id = None
        meta_segment_id = None
        if isinstance(debug_meta, dict):
            meta_turn_id = clean_text(str(debug_meta.get("turn_id") or "")) or None
            raw_segment_id = debug_meta.get("segment_id")
            if raw_segment_id is not None:
                try:
                    meta_segment_id = int(raw_segment_id)
                except (TypeError, ValueError):
                    meta_segment_id = clean_text(str(raw_segment_id)) or None

        stamp = time.strftime("%Y%m%d-%H%M%S")
        stem_key = (guild_id, session_key or "", meta_turn_id or "", str(meta_segment_id or ""))
        stem = stems.get(stem_key)
        if stem is None:
            idx = counts.get(guild_id, 0) + 1
            counts[guild_id] = idx
            speaker_label = sanitize_debug_label(speaker)
            stem = f"{stamp}_{idx:04d}_{speaker_label}"
            stems[stem_key] = stem

        raw_path = guild_dir / f"{stem}_raw48k.wav"
        stt_path = guild_dir / f"{stem}_stt16k.wav"
        meta_path = guild_dir / f"{stem}.json"

        with wave.open(str(raw_path), "wb") as wf:
            wf.setnchannels(int(raw_channels))
            wf.setsampwidth(2)
            wf.setframerate(int(raw_rate))
            wf.writeframes(pcm_bytes)

        if save_stt_audio:
            audio16k_int16 = np.clip(audio16k, -1.0, 1.0)
            audio16k_int16 = (audio16k_int16 * 32767.0).astype(np.int16)
            with wave.open(str(stt_path), "wb") as wf:
                wf.setnchannels(1)
                wf.setsampwidth(2)
                wf.setframerate(int(stt_rate))
                wf.writeframes(audio16k_int16.tobytes())

        stage = clean_text(stage_label or "ingress") or "ingress"
        meta = {
            "saved_at": stamp,
            "guild_id": guild_id,
            "speaker": speaker,
            "raw_path": str(raw_path),
            "stt_path": str(stt_path) if save_stt_audio else None,
            "stt_saved": bool(save_stt_audio),
            "raw_bytes": len(pcm_bytes),
            "raw_seconds": round(len(pcm_bytes) / float(raw_rate * raw_channels * 2), 3),
            "stt_samples": int(audio16k.size),
            "stt_seconds": round(audio16k.size / float(stt_rate), 3),
            "wake_probe": wake_probe,
            "final_text": final_text,
            "session_key": session_key,
            "turn_id": meta_turn_id,
            "segment_id": meta_segment_id,
            "stage_label": stage,
        }
        if debug_meta is not None:
            meta["voice_receive"] = safe_json_value(debug_meta)
        if stt_meta is not None:
            meta["stt"] = safe_json_value(stt_meta)
        meta_path.write_text(safe_json_dumps(meta, ensure_ascii=False, indent=2), encoding="utf-8")
        trim_voice_debug_dir(guild_dir, max_files=max_files_per_guild)
        stt_log = str(stt_path) if save_stt_audio else "[SKIPPED]"
        log(f"[VOICE DEBUG SAVE] speaker={speaker} stage={stage} raw={raw_path} stt={stt_log}")
    except Exception as exc:
        log(f"[VOICE DEBUG SAVE FAIL] speaker={speaker} err={exc!r}")


__all__ = [
    "build_voice_debug_audio_item",
    "debug_write_worker_from_runtime",
    "enqueue_voice_debug_audio_from_runtime",
    "ensure_debug_write_worker_started_from_runtime",
    "sanitize_debug_label",
    "save_voice_debug_audio_now",
    "trim_voice_debug_dir",
    "voice_debug_drop_message",
]
