from __future__ import annotations

from dataclasses import dataclass
import re
import time
from typing import Any, Callable

from .text import clean_text


VISION_MEMORY_LINE_RE = re.compile(
    r"(?im)^\s*(?:captured_image|image_size|scene|ocr_text|ocr_error|analysis_error)\s*:\s?.*$"
    r"|^\s*(?:captured_image|image_size)\s*=.*$"
)


@dataclass(frozen=True)
class MemoryTurnWriteResult:
    memory_user_text: str
    memory_answer: str
    rows: list[dict[str, str]]
    vault_mirrored: bool
    identity_record_decision: dict[str, Any]


@dataclass(frozen=True)
class MemoryRefreshInputs:
    turn_index: int
    idle_gap_sec: float
    deep_routing_needed: bool


@dataclass(frozen=True)
class MemoryWritebehindSchedulePlan:
    action: str
    status: str
    writebehind_reason: str = ""
    writebehind_mode: str = ""
    task_key: str | None = None
    replace_existing: bool = False

    @property
    def should_queue(self) -> bool:
        return self.action in {"batch", "normal"}


def redact_vision_text_for_memory(text: str, *, vision_memory_write_enabled: bool = False) -> str:
    if vision_memory_write_enabled:
        return text
    cleaned = clean_text(text)
    if not cleaned:
        return ""
    markers = (
        "Local screen vision observation is available.",
        "Background local screen observation:",
        "ocr_text:",
        "captured_image=",
    )
    if not any(marker in cleaned for marker in markers):
        return cleaned
    redacted = VISION_MEMORY_LINE_RE.sub("[vision context redacted]", str(text or ""))
    redacted = clean_text(redacted)
    redacted = redacted.replace("Local screen vision observation is available.", "[vision context redacted]")
    redacted = redacted.replace("Background local screen observation:", "[vision context redacted]")
    return clean_text(redacted)


def build_memory_turn_rows(
    *,
    user_text: str,
    answer: str,
    source: str,
    user_speaker: str = "user",
    assistant_speaker: str = "Evelyn",
    turn_id: str | None = None,
) -> list[dict[str, str]]:
    rows = [
        {
            "role": "user",
            "speaker": clean_text(user_speaker) or "user",
            "source": clean_text(source) or "unknown",
            "text": clean_text(user_text),
        },
        {
            "role": "assistant",
            "speaker": clean_text(assistant_speaker) or "Evelyn",
            "source": clean_text(source) or "unknown",
            "text": clean_text(answer),
        },
    ]
    normalized_turn_id = clean_text(str(turn_id or ""))[:80]
    if re.fullmatch(r"[A-Za-z0-9._:-]+", normalized_turn_id):
        for row in rows:
            role = clean_text(str(row.get("role") or "memory"))
            row["evidence_id"] = f"turn:{normalized_turn_id}:{role}"
            row["source_turn_id"] = normalized_turn_id
            row["evidence_kind"] = "conversation_turn"
    return rows


def memory_scope_labels(
    *,
    room_key: str | None = None,
    person_key: str | None = None,
    session_memory_key: str | None = None,
) -> list[str]:
    labels = ["guild"]
    if room_key:
        labels.append(f"room:{room_key}")
    if person_key:
        labels.append(f"person:{person_key}")
    if session_memory_key:
        labels.append(f"session:{session_memory_key}")
    return labels


def write_memory_turn_records(
    guild_id: int,
    user_text: str,
    answer: str,
    *,
    room_key: str | None = None,
    person_key: str | None = None,
    session_memory_key: str | None = None,
    source: str = "chat",
    user_speaker: str = "user",
    assistant_speaker: str = "Evelyn",
    turn_id: str | None = None,
    vision_memory_write_enabled: bool = False,
    record_identity_turn: Callable[..., dict[str, Any]],
    append_raw_rows: Callable[..., Any],
    append_vault_rows: Callable[..., Any],
    log: Callable[[str], None] | None = None,
) -> MemoryTurnWriteResult:
    memory_user_text = redact_vision_text_for_memory(
        user_text,
        vision_memory_write_enabled=vision_memory_write_enabled,
    )
    memory_answer = redact_vision_text_for_memory(
        answer,
        vision_memory_write_enabled=vision_memory_write_enabled,
    )
    rows = build_memory_turn_rows(
        user_text=memory_user_text,
        answer=memory_answer,
        source=source,
        user_speaker=user_speaker,
        assistant_speaker=assistant_speaker,
        turn_id=turn_id,
    )
    identity_record_decision = record_identity_turn(
        memory_user_text,
        memory_answer,
        source=source,
    )
    append_raw_rows(guild_id, rows, mirror_daily=False)
    if room_key:
        append_raw_rows(guild_id, rows, scope_type="room", scope_key=room_key, mirror_daily=False)
    if person_key:
        append_raw_rows(guild_id, rows, scope_type="person", scope_key=person_key, mirror_daily=False)
    if session_memory_key:
        append_raw_rows(guild_id, rows, scope_type="session", scope_key=session_memory_key, mirror_daily=False)

    vault_mirrored = True
    try:
        append_vault_rows(
            guild_id,
            rows,
            scope_labels=memory_scope_labels(
                room_key=room_key,
                person_key=person_key,
                session_memory_key=session_memory_key,
            ),
        )
    except Exception as exc:
        vault_mirrored = False
        if log is not None:
            log(f"[MEMORY VAULT] daily mirror failed: {exc!r}")

    return MemoryTurnWriteResult(
        memory_user_text=memory_user_text,
        memory_answer=memory_answer,
        rows=rows,
        vault_mirrored=vault_mirrored,
        identity_record_decision=identity_record_decision,
    )


def build_memory_writer_decision_payload(
    memory_writer_decision: Any,
    *,
    source: str,
    session_key: str | None,
    raw_transcript_written: bool,
    vault_mirrored: bool,
    identity_record_decision: dict[str, Any],
) -> dict[str, Any]:
    payload = dict(memory_writer_decision.to_dict())
    payload["source"] = source
    payload["session_key"] = session_key
    payload["raw_transcript_written"] = raw_transcript_written
    payload["vault_mirrored"] = vault_mirrored
    payload["identity_review_candidate"] = identity_record_decision
    return payload


def memory_refresh_inputs_for_turn(
    *,
    user_text: str,
    source: str,
    session_key: str | None,
    guild_id: int,
    history_reader: Callable[..., list[Any]],
    last_active_at: dict[str, float],
    deep_routing_needed: Callable[..., bool],
    now: Callable[[], float] = time.monotonic,
) -> MemoryRefreshInputs:
    turn_index = 1
    idle_gap_sec = 0.0
    if session_key:
        history_len = len(history_reader(session_key=session_key, guild_id=guild_id))
        turn_index = max(1, (history_len + 1) // 2)
        idle_gap_sec = max(0.0, now() - float(last_active_at.get(session_key, 0.0) or 0.0))
    return MemoryRefreshInputs(
        turn_index=turn_index,
        idle_gap_sec=idle_gap_sec,
        deep_routing_needed=bool(deep_routing_needed(user_text, source=source)),
    )


def build_memory_writer_decision_for_turn(
    *,
    user_text: str,
    answer: str,
    source: str,
    runtime_mode: str,
    refresh_inputs: MemoryRefreshInputs,
    decision_builder: Callable[..., Any],
) -> Any:
    return decision_builder(
        user_text=user_text,
        answer=answer,
        source=source,
        should_refresh_memory=should_run_memory_update(
            user_text=user_text,
            answer=answer,
            source=source,
            turn_index=refresh_inputs.turn_index,
            idle_gap_sec=refresh_inputs.idle_gap_sec,
            deep_routing_needed=refresh_inputs.deep_routing_needed,
        ),
        runtime_mode=runtime_mode,
    )


def plan_memory_writebehind_schedule(
    memory_writer_decision: Any,
    *,
    mode: str,
    guild_id: int,
    session_memory_key: str | None,
    room_key: str | None,
    session_key: str | None,
    decision_payload: dict[str, Any],
    runtime_session_key: Callable[..., str],
    task_key_builder: Callable[[str, dict[str, Any]], str],
    should_replace_task: Callable[[dict[str, Any]], bool],
) -> MemoryWritebehindSchedulePlan:
    if not memory_writer_decision.should_run_summary_llm():
        return MemoryWritebehindSchedulePlan(
            action="skip",
            status="skipped",
            writebehind_reason="summary_llm_not_needed",
        )
    if mode == "realtime":
        return MemoryWritebehindSchedulePlan(
            action="defer",
            status="deferred",
            writebehind_reason="runtime_mode_realtime",
        )

    base_task_key = session_memory_key or room_key or session_key or runtime_session_key(guild_id=guild_id)
    if mode == "batch" and base_task_key is not None:
        task_key = task_key_builder(base_task_key, decision_payload)
        return MemoryWritebehindSchedulePlan(
            action="batch",
            status="queued",
            writebehind_mode="batch",
            task_key=task_key,
            replace_existing=should_replace_task(decision_payload),
        )
    return MemoryWritebehindSchedulePlan(
        action="normal",
        status="queued",
        writebehind_mode="normal",
    )


def should_run_memory_update(
    *,
    user_text: str,
    answer: str,
    source: str,
    turn_index: int = 1,
    idle_gap_sec: float = 0.0,
    deep_routing_needed: bool = False,
) -> bool:
    cleaned_user = clean_text(user_text)
    cleaned_answer = clean_text(answer)
    merged = clean_text(f"{cleaned_user} {cleaned_answer}")
    text_len = len(cleaned_user)
    has_open_question = ("?" in cleaned_user) or ("?" in cleaned_answer)
    explicit_fact_markers = ("나는 ", "내가 ", "우리는", "설정", "결정", "기억", "기억해줘", "해야", "하기로")
    has_explicit_fact = any(marker in merged for marker in explicit_fact_markers)
    is_smalltalk = (
        not deep_routing_needed
        and len(cleaned_user) <= 14
        and len(cleaned_answer) <= 32
    )

    if has_explicit_fact:
        return True
    if has_open_question:
        return True
    if max(1, int(turn_index)) % 4 == 0:
        return True
    if source == "voice" and text_len < 12:
        return False
    if is_smalltalk:
        return False
    return max(0.0, float(idle_gap_sec)) >= 20.0


__all__ = [
    "MemoryRefreshInputs",
    "MemoryTurnWriteResult",
    "MemoryWritebehindSchedulePlan",
    "build_memory_writer_decision_for_turn",
    "build_memory_writer_decision_payload",
    "build_memory_turn_rows",
    "memory_scope_labels",
    "memory_refresh_inputs_for_turn",
    "plan_memory_writebehind_schedule",
    "redact_vision_text_for_memory",
    "should_run_memory_update",
    "write_memory_turn_records",
]
