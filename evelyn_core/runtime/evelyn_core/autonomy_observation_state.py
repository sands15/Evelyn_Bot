from __future__ import annotations

import time
from typing import Any, Callable

from .conversation_memory_receipt import sanitize_memory_receipt_ref
from .text import clean_text


def pick_recent_user_text(history: list[dict[str, Any]]) -> str:
    for entry in reversed(history):
        if not isinstance(entry, dict):
            continue
        if clean_text(str(entry.get("role", ""))) != "user":
            continue
        text = clean_text(str(entry.get("content", "")))
        if text and text not in {"[autonomy]", "[autonomy:error]"}:
            return text
    return ""


def build_autonomy_summary_payload(
    history: list[dict[str, Any]],
    *,
    active_sessions: int,
    inflight_llm_requests: int,
) -> dict[str, Any]:
    recent = history[-4:] if len(history) > 4 else history[1:]
    summary = " | ".join(
        clean_text(str(item.get("content", "")))[:80]
        for item in recent
        if isinstance(item, dict) and clean_text(str(item.get("content", "")))
    )
    return {
        "status": "ok",
        "reason": "summary_ready",
        "summary": summary or f"active_sessions={int(active_sessions)} inflight_llm={int(inflight_llm_requests)}",
    }


def build_autonomy_status_payload(
    *,
    connected: bool,
    active_sessions: int,
    inflight_llm_requests: int,
    known_followup_channels: int,
) -> dict[str, Any]:
    return {
        "status": "ok",
        "reason": "status_checked",
        "connected": bool(connected),
        "active_sessions": int(active_sessions),
        "inflight_llm_requests": int(inflight_llm_requests),
        "known_followup_channels": int(known_followup_channels),
    }


def build_autonomy_recent_context_payload(history: list[dict[str, Any]]) -> dict[str, Any]:
    recent = history[-6:] if len(history) > 6 else history[1:]
    items = [
        clean_text(str(item.get("content", "")))[:120]
        for item in recent
        if isinstance(item, dict) and clean_text(str(item.get("content", "")))
    ]
    return {
        "status": "ok",
        "reason": "recent_context_summarized",
        "summary": " / ".join(items) if items else "최근 문맥 없음",
        "count": len(items),
    }


def build_default_autonomy_observation(
    *,
    connected: bool,
    known_followup_channels: int,
    inflight_llm_requests: int,
    active_sessions: int,
    history: list[dict[str, Any]],
    last_autonomy_ping_at: float,
    observe_channel_ids: list[int],
    command_only_channel_ids: list[int],
    observed_channels: list[dict[str, Any]],
    quiet_hours: bool,
    last_result: dict[str, Any],
    cached_cognitive: dict[str, Any] | None,
    last_cognitive_refresh_at: float,
    router_refresh_inflight: bool,
    autonomy_cognitive_stale_sec: float,
    autonomy_cognitive_min_interval_sec: float,
    autonomy_cognitive_force_refresh_sec: float,
    vision_watch: dict[str, Any] | None,
    vision_watch_interval_sec: float,
    local_tts_state: dict[str, Any],
    local_mic_state: dict[str, Any],
    queued_proactive_question_available: bool,
    answer_promises_search_fn: Callable[[str], bool],
    now_mono: float | None = None,
    now_time: float | None = None,
) -> dict[str, Any]:
    now_mono_value = time.monotonic() if now_mono is None else float(now_mono)
    now_time_value = time.time() if now_time is None else float(now_time)
    latest_user_text = pick_recent_user_text(history)
    recent_context_items = max(0, min(len(history) - 1, 6))
    last_ping_at = float(last_autonomy_ping_at or 0.0)
    last_ping_gap = 999999.0 if last_ping_at <= 0 else max(0.0, now_mono_value - last_ping_at)
    repeated_blocked_action = str((last_result or {}).get("reason", "")) in {
        "retry_suppressed",
        "action_not_allowed",
        "unsupported_default_action",
    }
    cognitive_updated_at = float((cached_cognitive or {}).get("updated_at", 0.0) or 0.0)
    cognitive_stale_sec = 999999.0 if cognitive_updated_at <= 0 else max(0.0, now_time_value - cognitive_updated_at)
    refresh_at = float(last_cognitive_refresh_at or 0.0)
    refresh_gap_sec = 999999.0 if refresh_at <= 0 else max(0.0, now_mono_value - refresh_at)
    recent_history = history[-8:]
    completed_autonomy_followup = -1
    for index in range(1, len(recent_history)):
        previous = recent_history[index - 1]
        current = recent_history[index]
        if not isinstance(previous, dict) or not isinstance(current, dict):
            continue
        receipt = sanitize_memory_receipt_ref(
            current.get("memoryReceiptRef")
        )
        if (
            clean_text(str(previous.get("role", ""))) == "user"
            and clean_text(str(previous.get("content", "")))
            == "[autonomy]"
            and clean_text(str(current.get("role", "")))
            == "assistant"
            and bool(clean_text(str(current.get("content", ""))))
            and receipt is not None
            and receipt["state"] != "unattributed"
        ):
            completed_autonomy_followup = index
    unresolved_items = 0
    user_unresolved_items = 0
    search_pending = False
    recent_visible: list[str] = []
    for index, entry in enumerate(recent_history):
        if not isinstance(entry, dict):
            continue
        content = clean_text(str(entry.get("content", "")))
        if not content:
            continue
        recent_visible.append(content)
        unresolved = index > completed_autonomy_followup
        if unresolved and "?" in content:
            unresolved_items += 1
            if clean_text(str(entry.get("role", ""))) == "user":
                user_unresolved_items += 1
        if unresolved and answer_promises_search_fn(content):
            search_pending = True

    active_recent_context = bool(latest_user_text) and recent_context_items > 0
    cognitive_refresh_needed = active_recent_context and not router_refresh_inflight and (
        cognitive_stale_sec >= autonomy_cognitive_stale_sec
        and refresh_gap_sec >= autonomy_cognitive_min_interval_sec
    )
    if active_recent_context and cognitive_stale_sec >= autonomy_cognitive_force_refresh_sec:
        cognitive_refresh_needed = (
            not router_refresh_inflight
            and refresh_gap_sec >= autonomy_cognitive_min_interval_sec
        )

    vision = vision_watch if isinstance(vision_watch, dict) else {}
    vision_captured_at = float(vision.get("captured_at", 0.0) or 0.0)
    vision_analyzed_at = float(vision.get("analyzed_at", 0.0) or 0.0)
    vision_unreliable = bool(
        vision
        and (
            vision.get("capture_black")
            or vision.get("scene_unreliable")
            or clean_text(str(vision.get("analysis_error") or ""))
        )
    )
    vision_fingerprint = clean_text(str(vision.get("scene_fingerprint") or vision.get("image_fingerprint") or ""))
    vision_change_recent = bool(
        vision
        and vision.get("changed")
        and not vision_unreliable
        and vision_captured_at > 0
        and (now_time_value - vision_captured_at) <= max(60.0, float(vision_watch_interval_sec) * 3)
    )
    last_input_age_sec = local_mic_state.get("lastInputAgeSec")
    try:
        local_mic_recent = last_input_age_sec is not None and float(last_input_age_sec) <= 5.0
    except Exception:
        local_mic_recent = False

    return {
        "connected": bool(connected),
        "known_followup_channels": int(known_followup_channels),
        "inflight_llm_requests": int(inflight_llm_requests),
        "active_sessions": int(active_sessions),
        "recent_context_items": recent_context_items,
        "last_autonomy_ping_sec": last_ping_gap,
        "observe_channel_ids": list(observe_channel_ids),
        "command_only_channel_ids": list(command_only_channel_ids),
        "observed_channels": [dict(row) for row in observed_channels],
        "quiet_hours": bool(quiet_hours),
        "repeated_blocked_action": repeated_blocked_action,
        "unresolved_items": unresolved_items,
        "user_unresolved_items": user_unresolved_items,
        "search_pending": search_pending,
        "recent_visible": recent_visible[-6:],
        "latest_user_text": latest_user_text,
        "cognitive_stale_sec": cognitive_stale_sec,
        "cognitive_refresh_gap_sec": refresh_gap_sec,
        "cognitive_refresh_needed": cognitive_refresh_needed,
        "router_refresh_inflight": bool(router_refresh_inflight),
        "local_tts_active": bool(local_tts_state.get("active")),
        "local_mic_recent": bool(local_mic_recent),
        "vision_watch": vision,
        "vision_change_recent": vision_change_recent,
        "vision_unreliable": vision_unreliable,
        "vision_fingerprint": vision_fingerprint,
        "vision_analysis_recent": bool(vision_analyzed_at > 0 and (now_time_value - vision_analyzed_at) <= 600),
        "queued_proactive_question_available": bool(queued_proactive_question_available),
    }


__all__ = [
    "build_autonomy_recent_context_payload",
    "build_autonomy_status_payload",
    "build_autonomy_summary_payload",
    "build_default_autonomy_observation",
    "pick_recent_user_text",
]
