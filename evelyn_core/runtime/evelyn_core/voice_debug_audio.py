from __future__ import annotations

import asyncio
import argparse
import json
import os
import re
import time
import wave
from dataclasses import dataclass
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


@dataclass(frozen=True)
class VoiceDebugBundle:
    stem: str
    paths: tuple[Path, ...]
    size_bytes: int
    mtime: float


@dataclass(frozen=True)
class VoiceDebugTrimResult:
    bundle_count: int
    candidate_count: int
    candidate_bytes: int
    deleted_count: int
    deleted_bytes: int
    failed_count: int


def _voice_debug_stem(path: Path) -> str | None:
    name = path.name
    if name.endswith("_raw48k.wav"):
        return name[: -len("_raw48k.wav")]
    if name.endswith("_stt16k.wav"):
        return name[: -len("_stt16k.wav")]
    if path.suffix.lower() in {".wav", ".json"}:
        return path.stem
    return None


def inventory_voice_debug_bundles(guild_dir: Path) -> list[VoiceDebugBundle]:
    if not guild_dir.exists() or not guild_dir.is_dir():
        return []
    resolved_root = guild_dir.resolve()
    grouped: dict[str, list[Path]] = {}
    for path in guild_dir.iterdir():
        if not path.is_file():
            continue
        try:
            resolved = path.resolve()
            resolved.relative_to(resolved_root)
        except (OSError, ValueError):
            continue
        stem = _voice_debug_stem(resolved)
        if stem is not None:
            grouped.setdefault(stem, []).append(resolved)

    bundles: list[VoiceDebugBundle] = []
    for stem, paths in grouped.items():
        stats = []
        try:
            stats = [path.stat() for path in paths]
        except OSError:
            continue
        bundles.append(
            VoiceDebugBundle(
                stem=stem,
                paths=tuple(sorted(paths)),
                size_bytes=sum(int(stat.st_size) for stat in stats),
                mtime=max(float(stat.st_mtime) for stat in stats),
            )
        )
    return sorted(bundles, key=lambda item: (item.mtime, item.stem))


def select_voice_debug_cleanup_bundles(
    bundles: list[VoiceDebugBundle],
    *,
    max_files: int,
    max_age_days: float | None = None,
    max_total_bytes: int | None = None,
    preserve_newest: int = 1,
    now: float | None = None,
) -> list[VoiceDebugBundle]:
    if not bundles:
        return []
    current_time = time.time() if now is None else float(now)
    newest_first = sorted(bundles, key=lambda item: (item.mtime, item.stem), reverse=True)
    protected = {item.stem for item in newest_first[: max(0, int(preserve_newest))]}
    candidates: dict[str, VoiceDebugBundle] = {}

    if max_age_days is not None and max_age_days >= 0:
        max_age_sec = float(max_age_days) * 86400.0
        for bundle in bundles:
            if bundle.stem not in protected and max(0.0, current_time - bundle.mtime) > max_age_sec:
                candidates[bundle.stem] = bundle

    if max_files > 0:
        retained = [item for item in newest_first if item.stem not in candidates]
        for bundle in retained[max_files:]:
            if bundle.stem not in protected:
                candidates[bundle.stem] = bundle

    if max_total_bytes is not None and max_total_bytes > 0:
        total = sum(item.size_bytes for item in bundles if item.stem not in candidates)
        for bundle in bundles:
            if total <= max_total_bytes:
                break
            if bundle.stem in protected or bundle.stem in candidates:
                continue
            candidates[bundle.stem] = bundle
            total -= bundle.size_bytes

    return sorted(candidates.values(), key=lambda item: (item.mtime, item.stem))


def trim_voice_debug_dir(
    guild_dir: Path,
    *,
    max_files: int,
    max_age_days: float | None = None,
    max_total_bytes: int | None = None,
    preserve_newest: int = 1,
    dry_run: bool = False,
    now: float | None = None,
) -> VoiceDebugTrimResult:
    bundles = inventory_voice_debug_bundles(guild_dir)
    candidates = select_voice_debug_cleanup_bundles(
        bundles,
        max_files=max_files,
        max_age_days=max_age_days,
        max_total_bytes=max_total_bytes,
        preserve_newest=preserve_newest,
        now=now,
    )
    deleted_count = 0
    deleted_bytes = 0
    failed_count = 0
    resolved_root = guild_dir.resolve()
    if not dry_run:
        for bundle in candidates:
            bundle_deleted = True
            for path in bundle.paths:
                try:
                    resolved = path.resolve()
                    resolved.relative_to(resolved_root)
                    resolved.unlink()
                except Exception:
                    bundle_deleted = False
                    failed_count += 1
            if bundle_deleted:
                deleted_count += 1
                deleted_bytes += bundle.size_bytes
    return VoiceDebugTrimResult(
        bundle_count=len(bundles),
        candidate_count=len(candidates),
        candidate_bytes=sum(item.size_bytes for item in candidates),
        deleted_count=deleted_count,
        deleted_bytes=deleted_bytes,
        failed_count=failed_count,
    )


def trim_voice_debug_root(
    root: Path,
    *,
    max_files: int,
    max_age_days: float | None,
    max_total_bytes_per_guild: int | None,
    preserve_newest: int,
    dry_run: bool = True,
    now: float | None = None,
) -> dict[str, Any]:
    resolved_root = Path(root).resolve()
    guild_results: dict[str, dict[str, int]] = {}
    if resolved_root.exists():
        for guild_dir in sorted(path for path in resolved_root.iterdir() if path.is_dir()):
            result = trim_voice_debug_dir(
                guild_dir,
                max_files=max_files,
                max_age_days=max_age_days,
                max_total_bytes=max_total_bytes_per_guild,
                preserve_newest=preserve_newest,
                dry_run=dry_run,
                now=now,
            )
            guild_results[guild_dir.name] = {
                "bundle_count": result.bundle_count,
                "candidate_count": result.candidate_count,
                "candidate_bytes": result.candidate_bytes,
                "deleted_count": result.deleted_count,
                "deleted_bytes": result.deleted_bytes,
                "failed_count": result.failed_count,
            }
    return {
        "root": str(resolved_root),
        "dry_run": bool(dry_run),
        "guilds": guild_results,
        "candidate_count": sum(item["candidate_count"] for item in guild_results.values()),
        "candidate_bytes": sum(item["candidate_bytes"] for item in guild_results.values()),
        "deleted_count": sum(item["deleted_count"] for item in guild_results.values()),
        "deleted_bytes": sum(item["deleted_bytes"] for item in guild_results.values()),
        "failed_count": sum(item["failed_count"] for item in guild_results.values()),
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Plan or apply Evelyn voice debug retention cleanup.")
    parser.add_argument("--root", type=Path, default=Path("debug_audio"), help="voice debug root")
    parser.add_argument("--max-files", type=int, default=200, help="maximum logical recordings per guild")
    parser.add_argument("--max-age-days", type=float, default=7.0)
    parser.add_argument("--max-total-mb-per-guild", type=int, default=256)
    parser.add_argument("--preserve-newest", type=int, default=10)
    parser.add_argument("--apply", action="store_true", help="delete candidates; default is dry-run")
    args = parser.parse_args(argv)
    result = trim_voice_debug_root(
        args.root,
        max_files=args.max_files,
        max_age_days=args.max_age_days,
        max_total_bytes_per_guild=args.max_total_mb_per_guild * 1024 * 1024,
        preserve_newest=args.preserve_newest,
        dry_run=not args.apply,
    )
    print(json.dumps(result, ensure_ascii=False, indent=2, sort_keys=True))
    return 0


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
    max_age_days: float | None = None,
    max_total_bytes_per_guild: int | None = None,
    preserve_newest: int = 10,
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
        trim_voice_debug_dir(
            guild_dir,
            max_files=max_files_per_guild,
            max_age_days=max_age_days,
            max_total_bytes=max_total_bytes_per_guild,
            preserve_newest=preserve_newest,
        )
        stt_log = str(stt_path) if save_stt_audio else "[SKIPPED]"
        log(f"[VOICE DEBUG SAVE] speaker={speaker} stage={stage} raw={raw_path} stt={stt_log}")
    except Exception as exc:
        log(f"[VOICE DEBUG SAVE FAIL] speaker={speaker} err={exc!r}")


__all__ = [
    "build_voice_debug_audio_item",
    "debug_write_worker_from_runtime",
    "enqueue_voice_debug_audio_from_runtime",
    "ensure_debug_write_worker_started_from_runtime",
    "inventory_voice_debug_bundles",
    "sanitize_debug_label",
    "save_voice_debug_audio_now",
    "select_voice_debug_cleanup_bundles",
    "trim_voice_debug_dir",
    "trim_voice_debug_root",
    "VoiceDebugBundle",
    "VoiceDebugTrimResult",
    "voice_debug_drop_message",
]


if __name__ == "__main__":
    raise SystemExit(main())
