from __future__ import annotations

import json
import math
import time
from pathlib import Path
from typing import Any, Callable

from .runtime_error_observability import (
    sanitize_runtime_error_code,
    sanitize_runtime_error_type,
)
from .text import clean_text


def _safe_failure_timestamp(value: Any) -> float | None:
    try:
        timestamp = float(value)
    except (TypeError, ValueError):
        return None
    return timestamp if math.isfinite(timestamp) else None


def default_voice_pipeline_counters() -> dict[str, int]:
    return {
        "queue_full_drop_count": 0,
        "queue_stale_drop_count": 0,
        "utterance_assembly_merge_count": 0,
        "utterance_assembly_flush_count": 0,
        "stt_busy_drop_count": 0,
        "stt_timeout_count": 0,
        "tts_request_failed_count": 0,
        "tts_producer_cancelled_count": 0,
        "tts_playback_failed_count": 0,
        "llm_failed_count": 0,
        "voice_delivery_failed_count": 0,
        "voice_rejoin_attempts": 0,
        "voice_rejoin_success": 0,
        "voice_rejoin_fail": 0,
    }


def default_voice_pipeline_state() -> dict[str, Any]:
    return {
        "last_voice_segment_at": None,
        "last_voice_channel": None,
        "last_voice_rejoin_at": None,
        "last_voice_rejoin_error": None,
        "last_voice_rejoin_error_type": "",
        "last_failure": None,
    }


def increment_voice_counter(counters: dict[str, int], name: str, amount: int = 1) -> None:
    counters[name] = int(counters.get(name, 0)) + int(amount)


def record_voice_failure_state(
    counters: dict[str, int],
    state: dict[str, Any],
    kind: str,
    err: BaseException | str,
    *,
    now: float | None = None,
) -> str:
    counter_map = {
        "llm_failed": "llm_failed_count",
        "stt_timeout": "stt_timeout_count",
        "tts_request_failed": "tts_request_failed_count",
        "tts_producer_cancelled": "tts_producer_cancelled_count",
        "tts_playback_failed": "tts_playback_failed_count",
        "voice_connection_unavailable": (
            "voice_delivery_failed_count"
        ),
        "voice_delivery_empty": "voice_delivery_failed_count",
        "voice_delivery_failed": "voice_delivery_failed_count",
    }
    safe_kind = sanitize_runtime_error_code(kind)
    counter = counter_map.get(safe_kind)
    if counter:
        increment_voice_counter(counters, counter)
    error_type = (
        sanitize_runtime_error_type(type(err).__name__)
        if isinstance(err, BaseException)
        else ""
    )
    state["last_failure"] = {
        "kind": safe_kind,
        "errorType": error_type,
        "at": time.time() if now is None else float(now),
        "contentFree": True,
    }
    return safe_kind


def record_voice_pipeline_failure_from_runtime(
    counters: dict[str, int],
    state: dict[str, Any],
    kind: str,
    err: BaseException | str,
    *,
    merge_log_event_payload: Callable[..., dict[str, Any]],
    log_turn_event: Callable[..., Any],
    metrics: dict | None = None,
    **extra: Any,
) -> None:
    error_code = record_voice_failure_state(
        counters,
        state,
        kind,
        err,
    )
    error_type = (
        sanitize_runtime_error_type(type(err).__name__)
        if isinstance(err, BaseException)
        else ""
    )
    meta = (metrics or {}).get("meta") or {}
    if metrics is not None and error_code == "tts_playback_failed":
        meta = metrics.setdefault("meta", {})
        meta["playback_failed"] = True
    log_turn_event(
        error_code,
        **merge_log_event_payload(
            explicit={
                "turn_id": meta.get("turn_id"),
                "segment_id": meta.get("segment_id"),
                "chunk_index": meta.get("chunk_index"),
                "session_key": meta.get("session_key"),
                "room_session_key": meta.get("room_session_key"),
                "guild_id": meta.get("guild_id"),
                "source": meta.get("source"),
                "error": error_code,
                "error_type": error_type,
            },
            extra=extra,
        ),
    )


def voice_last_channel_state_path(project_root: Path, configured_path: str) -> Path:
    path = Path(configured_path)
    if not path.is_absolute():
        path = project_root / path
    return path


def load_last_voice_channel_state(project_root: Path, configured_path: str) -> dict[str, Any]:
    path = voice_last_channel_state_path(project_root, configured_path)
    if not path.exists():
        return {}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return {}
    return data if isinstance(data, dict) else {}


def save_last_voice_channel_state(
    project_root: Path,
    configured_path: str,
    state: dict[str, Any],
    guild: Any,
    channel: Any,
    *,
    reason: str,
    manual_disconnect: bool = False,
    now: float | None = None,
) -> None:
    payload = {
        "guild_id": int(guild.id),
        "guild_name": clean_text(getattr(guild, "name", "") or ""),
        "channel_id": int(channel.id),
        "channel_name": clean_text(getattr(channel, "name", "") or ""),
        "updated_at": time.time() if now is None else float(now),
        "reason": clean_text(reason),
        "manual_disconnect": bool(manual_disconnect),
    }
    path = voice_last_channel_state_path(project_root, configured_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    state["last_voice_channel"] = dict(payload)


def save_last_voice_channel_state_from_runtime(
    project_root: Path,
    configured_path: str,
    state: dict[str, Any],
    guild: Any,
    channel: Any,
    *,
    reason: str,
    manual_disconnect: bool = False,
    log: Callable[..., Any] | None = None,
) -> bool:
    try:
        save_last_voice_channel_state(
            project_root,
            configured_path,
            state,
            guild,
            channel,
            reason=reason,
            manual_disconnect=manual_disconnect,
        )
    except Exception as exc:
        if log is not None:
            log(f"[VOICE STATE SAVE FAIL] errorType={type(exc).__name__}")
        return False
    return True


def mark_last_voice_manual_disconnect(
    project_root: Path,
    configured_path: str,
    state: dict[str, Any],
    guild: Any | None,
    *,
    reason: str,
    now: float | None = None,
) -> None:
    if guild is None:
        return
    data = load_last_voice_channel_state(project_root, configured_path)
    if not data or int(data.get("guild_id") or 0) != int(guild.id):
        return
    data["manual_disconnect"] = True
    data["reason"] = clean_text(reason)
    data["updated_at"] = time.time() if now is None else float(now)
    path = voice_last_channel_state_path(project_root, configured_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    state["last_voice_channel"] = dict(data)


def build_voice_pipeline_snapshot_payload(
    *,
    counters: dict[str, int],
    state: dict[str, Any],
    p95: dict[str, float | int],
    now_time: float,
    now_mono: float,
    stt_lock_locked: bool,
    stt_cooldown_until: float,
    last_channel_state: dict[str, Any],
    output_mode: str,
    local_tts_output: dict[str, Any],
    queue_depth: int,
    queue_max: int,
    live_recent_sec: float,
    utterance_assembly_enabled: bool,
    utterance_pending_count: int,
    utterance_commit_wait_sec: float,
    barge_in_continuity: dict[str, Any],
    turn_path_metrics: dict[str, Any],
) -> dict[str, Any]:
    last_segment_at = state.get("last_voice_segment_at")
    last_segment_age_sec = None
    if isinstance(last_segment_at, (int, float)):
        last_segment_age_sec = round(max(0.0, float(now_time) - float(last_segment_at)), 3)
    cooldown_remaining = max(0.0, float(stt_cooldown_until) - float(now_mono))
    if last_channel_state:
        state["last_voice_channel"] = dict(last_channel_state)
    raw_last_failure = state.get("last_failure")
    last_failure = None
    if isinstance(raw_last_failure, dict):
        last_failure = {
            "kind": sanitize_runtime_error_code(
                raw_last_failure.get("kind")
            ),
            "errorType": sanitize_runtime_error_type(
                raw_last_failure.get("errorType")
            ),
            "at": _safe_failure_timestamp(raw_last_failure.get("at")),
            "contentFree": True,
        }
    last_rejoin_error = (
        "voice_rearm_failed"
        if state.get("last_voice_rejoin_error")
        else ""
    )
    return {
        "outputMode": clean_text(output_mode) or "discord_voice",
        "localTtsOutput": dict(local_tts_output or {}),
        "queueDepth": int(queue_depth),
        "queueMax": int(queue_max),
        "liveRecent": last_segment_age_sec is not None and last_segment_age_sec <= live_recent_sec,
        "lastVoiceSegmentAgeSec": last_segment_age_sec,
        "sttBusy": bool(stt_lock_locked),
        "sttCooldownRemainingSec": round(cooldown_remaining, 3),
        "sttTimeoutCount": counters.get("stt_timeout_count", 0),
        "sttBusyDropCount": counters.get("stt_busy_drop_count", 0),
        "queueFullDropCount": counters.get("queue_full_drop_count", 0),
        "queueStaleDropCount": counters.get("queue_stale_drop_count", 0),
        "utteranceAssemblyEnabled": bool(utterance_assembly_enabled),
        "utteranceAssemblyPendingCount": int(utterance_pending_count),
        "utteranceAssemblyCommitWaitSec": round(float(utterance_commit_wait_sec), 3),
        "utteranceAssemblyMergeCount": counters.get("utterance_assembly_merge_count", 0),
        "utteranceAssemblyFlushCount": counters.get("utterance_assembly_flush_count", 0),
        "ttsRequestFailedCount": counters.get("tts_request_failed_count", 0),
        "ttsPlaybackFailedCount": counters.get("tts_playback_failed_count", 0),
        "llmFailedCount": counters.get("llm_failed_count", 0),
        "voiceDeliveryFailedCount": counters.get("voice_delivery_failed_count", 0),
        "rejoinAttempts": counters.get("voice_rejoin_attempts", 0),
        "rejoinSuccess": counters.get("voice_rejoin_success", 0),
        "rejoinFail": counters.get("voice_rejoin_fail", 0),
        "bargeInContinuity": dict(barge_in_continuity or {}),
        "lastVoiceChannel": state.get("last_voice_channel"),
        "lastVoiceRejoinAt": state.get("last_voice_rejoin_at"),
        "lastVoiceRejoinError": last_rejoin_error,
        "lastVoiceRejoinErrorType": sanitize_runtime_error_type(
            state.get("last_voice_rejoin_error_type")
        ),
        "lastFailure": last_failure,
        "sttMsP95": p95.get("stt_ms_p95", 0),
        "ttsFirstAudioMsP95": p95.get("tts_first_audio_ms_p95", 0),
        "mainFirstTokenMsP95": p95.get("main_first_token_ms_p95", 0),
        "turnPathMetrics": dict(turn_path_metrics or {}),
    }


__all__ = [
    "build_voice_pipeline_snapshot_payload",
    "default_voice_pipeline_counters",
    "default_voice_pipeline_state",
    "increment_voice_counter",
    "load_last_voice_channel_state",
    "mark_last_voice_manual_disconnect",
    "record_voice_failure_state",
    "record_voice_pipeline_failure_from_runtime",
    "save_last_voice_channel_state",
    "save_last_voice_channel_state_from_runtime",
    "voice_last_channel_state_path",
]
