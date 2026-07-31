from __future__ import annotations

import json
import time
from collections.abc import Mapping
from pathlib import Path
from typing import Any

from .text import clean_text


TURN_SUMMARY_EVENTS = frozenset(
    {
        "text_turn_summary",
        "voice_turn_summary",
        "voice_drop_summary",
    }
)

TURN_SUMMARY_KEYS: tuple[str, ...] = (
    "summary_schema",
    "event_name",
    "label",
    "turn_id",
    "source",
    "session_key",
    "room_session_key",
    "guild_id",
    "user_id",
    "owner_user_id",
    "segment_id",
    "chunk_index",
    "topic_id",
    "turn_type",
    "selected_path",
    "reply_source",
    "route",
    "drop_reason",
    "reply_gate_passed_by",
    "reply_gate_blocked_by",
    "needs_main_llm",
    "needs_memory",
    "needs_runtime_state",
    "needs_minecraft_state",
    "needs_vision",
    "needs_skill_graph",
    "needs_search",
    "needs_tts",
    "route_priority",
    "response_mode",
    "context_tokens_estimate",
    "context_message_count",
    "context_sections",
    "context_section_chars",
    "memory_context_state",
    "memory_grounding_state",
    "memory_use_policy",
    "memory_confirm_only_item_count",
    "memory_prompt_truncated",
    "memory_prompt_evidence_discarded",
    "memory_pretruncation_legacy_item_count",
    "memory_pretruncation_note_count",
    "memory_opaque_confirm_only_component_count",
    "memory_supplied_note_ids",
    "memory_supplied_note_count",
    "memory_legacy_item_count",
    "memory_legacy_attributed_item_count",
    "memory_legacy_unattributed_item_count",
    "memory_legacy_confirm_only_item_count",
    "memory_legacy_evidence_ids",
    "memory_legacy_source_evidence_ids",
    "memory_legacy_source_turn_ids",
    "memory_hot_context_state",
    "memory_version",
    "memory_receipt_content_free",
    "memory_writer_decision",
    "minecraft_snapshot_age_ms",
    "minecraft_snapshot_freshness",
    "playback_started",
    "playback_completed",
    "playback_cancelled",
    "error_layer",
    "error",
    "t_ingress",
    "t_policy",
    "t_context_build",
    "cognitive_hotpath_ms",
    "t_stt_done",
    "llm_first_token_ms",
    "llm_ms",
    "tts_first_audio_ms",
    "playback_first_packet_ms",
    "t_main_first_token",
    "t_main_done",
    "t_tts_first_audio",
    "t_playback_first_packet",
    "total_ms",
    "stt_ms_p95",
    "router_ms_p95",
    "main_first_token_ms_p95",
    "tts_first_audio_ms_p95",
    "search_followup_queued_count",
    "cancelled_stale_turn_count",
    "validation_session_id",
    "validation_step_id",
    "validation_transcript_match",
    "extra",
)


def write_turn_trace_event(
    event: str,
    payload: Mapping[str, Any],
    *,
    turn_trace_json_log: bool,
    bottleneck_events: set[str] | frozenset[str],
    summary_events: set[str] | frozenset[str] = TURN_SUMMARY_EVENTS,
    console_only_stt_and_reply: bool,
    voice_bottleneck_logs: bool,
    voice_trace_all_events: bool,
    log_dir: Path,
    file_lock: Any,
    original_print: Any,
    trace_print: Any,
) -> dict[str, Any] | None:
    if not turn_trace_json_log:
        return None
    is_bottleneck_event = event in bottleneck_events
    preserve_null_fields = event in summary_events
    if console_only_stt_and_reply:
        if not is_bottleneck_event:
            return None
    elif voice_bottleneck_logs and not voice_trace_all_events and not is_bottleneck_event:
        return None

    record: dict[str, Any] = {"event": event, "ts": round(time.time(), 3)}
    for key, value in dict(payload).items():
        if value is None and not preserve_null_fields:
            continue
        record[key] = value

    try:
        log_dir.mkdir(parents=True, exist_ok=True)
        trace_path = log_dir / f"{time.strftime('%Y%m%d')}.jsonl"
        with file_lock:
            with trace_path.open("a", encoding="utf-8") as f:
                f.write(json.dumps(record, ensure_ascii=False, sort_keys=True) + "\n")
    except Exception as exc:
        original_print(f"[TURN TRACE FILE ERROR] {exc!r}")

    try:
        trace_print("[TURN TRACE]\n" + json.dumps(record, ensure_ascii=False, sort_keys=True, indent=2))
    except Exception as exc:
        safe_record = {"event": event, "ts": record.get("ts"), "trace_error": repr(exc)}
        for key in ("turn_id", "chunk_index", "session_key", "source_type", "stage", "error"):
            value = record.get(key)
            if value is not None:
                safe_record[key] = value
        trace_print("[TURN TRACE]\n" + json.dumps(safe_record, ensure_ascii=False, sort_keys=True, indent=2))
    return record


def _as_mapping(value: Any) -> Mapping[str, Any]:
    return value if isinstance(value, Mapping) else {}


def _clean_optional(value: Any) -> str | None:
    if value is None:
        return None
    cleaned = clean_text(str(value))
    return cleaned or None


def _round_ms(value: Any) -> float | None:
    if value is None:
        return None
    try:
        return round(float(value), 1)
    except (TypeError, ValueError):
        return None


def _int_or_none(value: Any) -> int | None:
    if value is None:
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _memory_note_ids(value: Any) -> list[str] | None:
    if not isinstance(value, (list, tuple)):
        return None
    note_ids = list(
        dict.fromkeys(
            cleaned
            for item in value[:12]
            if (cleaned := clean_text(str(item)))
        )
    )
    return note_ids or []


def _first_ms(*values: Any) -> float | None:
    for value in values:
        rounded = _round_ms(value)
        if rounded is not None:
            return rounded
    return None


def _bool_or_none(value: Any) -> bool | None:
    if value is None:
        return None
    return bool(value)


def _estimate_context_tokens(section_chars: Any, memory_context_chars: Any) -> int | None:
    total_chars = 0
    if isinstance(section_chars, Mapping):
        for value in section_chars.values():
            try:
                total_chars += int(value or 0)
            except (TypeError, ValueError):
                continue
    else:
        try:
            total_chars += int(memory_context_chars or 0)
        except (TypeError, ValueError):
            pass
    if total_chars <= 0:
        return None
    return max(1, round(total_chars / 4))


def _policy_bool(policy: Mapping[str, Any], key: str) -> bool | None:
    if key not in policy:
        return None
    value = policy.get(key)
    if isinstance(value, bool):
        return value
    if isinstance(value, (int, float)):
        return bool(value)
    if isinstance(value, str):
        return value.strip().lower() in {"1", "true", "yes", "on"}
    return bool(value)


def build_turn_summary_payload(
    metrics: Mapping[str, Any] | None,
    *,
    label: str,
    event_name: str,
    total_ms: float | None,
    p95_summary: Mapping[str, Any] | None = None,
    extra: str | None = None,
    error_layer: str | None = None,
    error: BaseException | str | None = None,
) -> dict[str, Any]:
    metrics_map = _as_mapping(metrics)
    meta = _as_mapping(metrics_map.get("meta"))
    marks = _as_mapping(metrics_map.get("marks"))
    context_meta = _as_mapping(meta.get("context_pipeline"))
    policy = _as_mapping(context_meta.get("policy"))
    memory_receipt = _as_mapping(context_meta.get("memory_receipt"))
    p95 = _as_mapping(p95_summary)
    section_chars = context_meta.get("section_chars")

    playback_started = bool(
        marks.get("t_playback_first_packet") is not None
        or marks.get("first_packet_sent_logged") is not None
        or meta.get("playback_started")
    )
    playback_cancelled = _bool_or_none(meta.get("playback_cancelled"))
    playback_completed = _bool_or_none(meta.get("playback_completed"))
    if playback_completed is None and playback_started and playback_cancelled is False:
        playback_completed = True

    payload: dict[str, Any] = {
        "summary_schema": "turn_summary.v1",
        "event_name": event_name,
        "label": _clean_optional(label),
        "turn_id": meta.get("turn_id"),
        "source": meta.get("source"),
        "session_key": meta.get("session_key"),
        "room_session_key": meta.get("room_session_key"),
        "guild_id": meta.get("guild_id"),
        "user_id": meta.get("user_id"),
        "owner_user_id": meta.get("owner_user_id"),
        "segment_id": meta.get("segment_id"),
        "chunk_index": meta.get("chunk_index"),
        "topic_id": meta.get("topic_id"),
        "turn_type": meta.get("turn_type"),
        "selected_path": meta.get("selected_path"),
        "reply_source": meta.get("reply_source"),
        "route": context_meta.get("route") or meta.get("route"),
        "drop_reason": meta.get("drop_reason"),
        "reply_gate_passed_by": meta.get("reply_gate_passed_by"),
        "reply_gate_blocked_by": meta.get("reply_gate_blocked_by"),
        "needs_main_llm": _policy_bool(policy, "needs_main_llm"),
        "needs_memory": _policy_bool(policy, "needs_memory"),
        "needs_runtime_state": _policy_bool(policy, "needs_runtime_state"),
        "needs_minecraft_state": _policy_bool(policy, "needs_minecraft_state"),
        "needs_vision": _policy_bool(policy, "needs_vision"),
        "needs_skill_graph": _policy_bool(policy, "needs_skill_graph"),
        "needs_search": _policy_bool(policy, "needs_search"),
        "needs_tts": _bool_or_none(meta.get("needs_tts")) if meta.get("needs_tts") is not None else _policy_bool(policy, "needs_tts"),
        "route_priority": _clean_optional(policy.get("priority")),
        "response_mode": _clean_optional(policy.get("response_mode")),
        "context_tokens_estimate": _estimate_context_tokens(section_chars, context_meta.get("memory_context_chars")),
        "context_message_count": context_meta.get("message_count"),
        "context_sections": context_meta.get("sections"),
        "context_section_chars": section_chars,
        "memory_context_state": _clean_optional(memory_receipt.get("state")),
        "memory_grounding_state": _clean_optional(memory_receipt.get("groundingState")),
        "memory_use_policy": _clean_optional(memory_receipt.get("usePolicy")),
        "memory_confirm_only_item_count": _int_or_none(memory_receipt.get("confirmOnlyItemCount")),
        "memory_prompt_truncated": _bool_or_none(memory_receipt.get("promptTruncated")),
        "memory_prompt_evidence_discarded": _bool_or_none(memory_receipt.get("promptEvidenceDiscarded")),
        "memory_pretruncation_legacy_item_count": _int_or_none(memory_receipt.get("preTruncationLegacyItemCount")),
        "memory_pretruncation_note_count": _int_or_none(memory_receipt.get("preTruncationNoteCount")),
        "memory_opaque_confirm_only_component_count": _int_or_none(memory_receipt.get("opaqueConfirmOnlyComponentCount")),
        "memory_supplied_note_ids": _memory_note_ids(memory_receipt.get("suppliedNoteIds")),
        "memory_supplied_note_count": _int_or_none(memory_receipt.get("suppliedNoteCount")),
        "memory_legacy_item_count": _int_or_none(memory_receipt.get("legacyItemCount")),
        "memory_legacy_attributed_item_count": _int_or_none(memory_receipt.get("legacyAttributedItemCount")),
        "memory_legacy_unattributed_item_count": _int_or_none(memory_receipt.get("legacyUnattributedItemCount")),
        "memory_legacy_confirm_only_item_count": _int_or_none(memory_receipt.get("legacyConfirmOnlyItemCount")),
        "memory_legacy_evidence_ids": _memory_note_ids(memory_receipt.get("legacyEvidenceIds")),
        "memory_legacy_source_evidence_ids": _memory_note_ids(memory_receipt.get("legacySourceEvidenceIds")),
        "memory_legacy_source_turn_ids": _memory_note_ids(memory_receipt.get("legacySourceTurnIds")),
        "memory_hot_context_state": _clean_optional(memory_receipt.get("hotContextState")),
        "memory_version": _int_or_none(memory_receipt.get("memoryVersion")),
        "memory_receipt_content_free": _bool_or_none(memory_receipt.get("contentFree")),
        "memory_writer_decision": meta.get("memory_writer_decision"),
        "minecraft_snapshot_age_ms": _round_ms(meta.get("minecraft_snapshot_age_ms")),
        "minecraft_snapshot_freshness": _clean_optional(meta.get("minecraft_snapshot_freshness")),
        "playback_started": playback_started,
        "playback_completed": playback_completed,
        "playback_cancelled": playback_cancelled,
        "error_layer": error_layer or meta.get("error_layer"),
        "error": repr(error) if isinstance(error, BaseException) else _clean_optional(error or meta.get("error")),
        "t_ingress": _round_ms(marks.get("t_ingress")),
        "t_policy": _round_ms(marks.get("t_policy")),
        "t_context_build": _round_ms(marks.get("t_context_build")),
        "cognitive_hotpath_ms": _round_ms(marks.get("cognitive_hotpath_ms")),
        "t_stt_done": _round_ms(marks.get("t_stt_done")),
        "llm_first_token_ms": _first_ms(marks.get("t_main_first_token"), marks.get("llm_first_chunk_logged")),
        "llm_ms": _first_ms(marks.get("t_main_done"), marks.get("llm_done")),
        "tts_first_audio_ms": _first_ms(
            marks.get("t_tts_first_audio"),
            marks.get("tts_first_byte_logged"),
            marks.get("tts_first_frame_logged"),
        ),
        "playback_first_packet_ms": _first_ms(
            marks.get("t_playback_first_packet"),
            marks.get("first_packet_sent_logged"),
        ),
        "t_main_first_token": _round_ms(marks.get("t_main_first_token")),
        "t_main_done": _round_ms(marks.get("t_main_done")),
        "t_tts_first_audio": _round_ms(marks.get("t_tts_first_audio")),
        "t_playback_first_packet": _round_ms(marks.get("t_playback_first_packet")),
        "total_ms": _round_ms(total_ms),
        "stt_ms_p95": _round_ms(p95.get("stt_ms_p95")),
        "router_ms_p95": _round_ms(p95.get("router_ms_p95")),
        "main_first_token_ms_p95": _round_ms(p95.get("main_first_token_ms_p95")),
        "tts_first_audio_ms_p95": _round_ms(p95.get("tts_first_audio_ms_p95")),
        "search_followup_queued_count": p95.get("search_followup_queued_count"),
        "cancelled_stale_turn_count": p95.get("cancelled_stale_turn_count"),
        "validation_session_id": meta.get("validation_session_id"),
        "validation_step_id": meta.get("validation_step_id"),
        "validation_transcript_match": _bool_or_none(meta.get("validation_transcript_match")),
        "extra": extra or None,
    }

    if payload["needs_tts"] is None:
        payload["needs_tts"] = bool(
            event_name == "voice_turn_summary"
            or payload["tts_first_audio_ms"] is not None
            or payload["playback_first_packet_ms"] is not None
        )

    return {key: payload.get(key) for key in TURN_SUMMARY_KEYS}
