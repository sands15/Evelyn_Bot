from __future__ import annotations

import re
import time
import uuid
from typing import Any, Callable

from .memory import (
    append_unique_memory_rows,
    compact_working_summary,
    memory_facts_path,
    memory_questions_path,
    vault_facts_path,
    vault_questions_path,
    write_memory_summary_with_provenance,
)
from .memory_llm_context import (
    build_compact_long_term_memory_messages,
    build_long_term_memory_messages,
    layered_summary_text,
    memory_scope_targets,
    recent_memory_groups,
)
from .proactive_questions import promote_open_questions
from .text import clean_text


_EVIDENCE_ID_RE = re.compile(r"^[A-Za-z0-9._:-]+$")


def _evidence_id(value: object, *, max_chars: int = 120) -> str:
    cleaned = clean_text(str(value or ""))[:max_chars]
    return cleaned if _EVIDENCE_ID_RE.fullmatch(cleaned) else ""


def _evidence_ids(value: object, *, max_items: int = 64) -> list[str]:
    if not isinstance(value, (list, tuple)):
        return []
    return list(
        dict.fromkeys(
            cleaned
            for item in value[:max_items]
            if (cleaned := _evidence_id(item))
        )
    )


def _memory_input_evidence(
    layers: dict[str, dict[str, Any]],
    recent: dict[str, list[dict[str, Any]]],
    *,
    source_turn_id: str | None,
    user_text: str,
    answer: str,
    include_recent: bool,
) -> tuple[list[str], list[str]]:
    evidence_ids: list[str] = []
    source_turn_ids: list[str] = []
    for layer in (
        layers.get("session"),
        layers.get("person"),
        layers.get("room"),
        layers.get("guild"),
    ):
        if not isinstance(layer, dict) or not clean_text(str(layer.get("summary") or "")):
            continue
        provenance = layer.get("summary_provenance")
        if not isinstance(provenance, dict):
            continue
        evidence_id = _evidence_id(provenance.get("evidence_id"))
        source_ids = _evidence_ids(provenance.get("source_evidence_ids"))
        if (
            clean_text(str(provenance.get("evidence_kind") or ""))
            != "derived_summary"
            or not evidence_id
            or not source_ids
        ):
            continue
        evidence_ids.append(evidence_id)
        source_turn_ids.extend(
            _evidence_ids(provenance.get("source_turn_ids"), max_items=32)
        )
    if include_recent:
        for expected_kind, rows in (
            ("conversation_turn", recent.get("raw", [])),
            ("derived_fact", recent.get("facts", [])),
            ("derived_question", recent.get("questions", [])),
        ):
            for row in rows:
                evidence_kind = clean_text(str(row.get("evidence_kind") or ""))
                if evidence_kind != expected_kind:
                    continue
                evidence_id = _evidence_id(row.get("evidence_id"))
                if evidence_kind == "conversation_turn":
                    source_turn_id_value = _evidence_id(
                        row.get("source_turn_id"),
                        max_chars=80,
                    )
                    role = clean_text(str(row.get("role") or "user")).lower()
                    if (
                        role not in {"user", "assistant"}
                        or evidence_id != f"turn:{source_turn_id_value}:{role}"
                    ):
                        continue
                    evidence_ids.append(evidence_id)
                    source_turn_ids.append(source_turn_id_value)
                    continue
                source_ids = _evidence_ids(row.get("source_evidence_ids"))
                if not evidence_id or not source_ids:
                    continue
                evidence_ids.append(evidence_id)
                source_turn_ids.extend(
                    _evidence_ids(row.get("source_turn_ids"), max_items=32)
                )
    normalized_turn_id = _evidence_id(source_turn_id, max_chars=80)
    if normalized_turn_id:
        if clean_text(user_text):
            evidence_ids.append(f"turn:{normalized_turn_id}:user")
        if clean_text(answer):
            evidence_ids.append(f"turn:{normalized_turn_id}:assistant")
        if clean_text(user_text) or clean_text(answer):
            source_turn_ids.append(normalized_turn_id)
    return (
        list(dict.fromkeys(evidence_ids))[:64],
        list(dict.fromkeys(source_turn_ids))[:32],
    )


def _derived_memory_rows(
    rows: list[dict[str, Any]],
    *,
    evidence_kind: str,
    source_evidence_ids: list[str],
    source_turn_ids: list[str],
) -> list[dict[str, Any]]:
    prefix = "fact" if evidence_kind == "derived_fact" else "question"
    return [
        {
            **row,
            "evidence_id": f"memory:{prefix}:{uuid.uuid4().hex[:12]}",
            "evidence_kind": evidence_kind,
            "source_evidence_ids": list(source_evidence_ids),
            "source_turn_ids": list(source_turn_ids),
        }
        for row in rows
    ]


def apply_long_term_memory_result(
    guild_id: int,
    result: dict[str, Any],
    *,
    room_key: str | None = None,
    person_key: str | None = None,
    session_memory_key: str | None = None,
    memory_fact_limit: int,
    memory_loop_limit: int,
    source_evidence_ids: list[str] | tuple[str, ...] = (),
    source_turn_ids: list[str] | tuple[str, ...] = (),
    log: Callable[[str], None] | None = None,
) -> dict[str, Any]:
    normalized_source_evidence_ids = _evidence_ids(source_evidence_ids)
    normalized_source_turn_ids = _evidence_ids(source_turn_ids, max_items=32)
    scope_targets = memory_scope_targets(
        room_key=room_key,
        person_key=person_key,
        session_memory_key=session_memory_key,
    )
    applied = {
        "summary_written": 0,
        "facts_written": 0,
        "questions_written": 0,
        "question_promote_failures": 0,
        "scope_count": len(scope_targets),
        "source_evidence_count": len(normalized_source_evidence_ids),
    }

    summary_update = compact_working_summary(str(result.get("summary_update", "")))
    if summary_update:
        summary_evidence_id = f"memory:summary:{uuid.uuid4().hex[:12]}"
        for scope_type, scope_key in scope_targets:
            write_memory_summary_with_provenance(
                guild_id,
                summary_update,
                evidence_id=summary_evidence_id,
                source_evidence_ids=normalized_source_evidence_ids,
                source_turn_ids=normalized_source_turn_ids,
                scope_type=scope_type,
                scope_key=scope_key,
            )
            applied["summary_written"] += 1

    durable_facts = result.get("durable_facts", [])
    if isinstance(durable_facts, list):
        rows = [row for row in durable_facts if isinstance(row, dict)]
        if rows:
            rows = _derived_memory_rows(
                rows,
                evidence_kind="derived_fact",
                source_evidence_ids=normalized_source_evidence_ids,
                source_turn_ids=normalized_source_turn_ids,
            )
            for scope_type, scope_key in scope_targets:
                append_unique_memory_rows(
                    memory_facts_path(guild_id, scope_type=scope_type, scope_key=scope_key),
                    rows,
                    memory_fact_limit,
                    mirror_path=vault_facts_path(guild_id, scope_type=scope_type, scope_key=scope_key),
                )
                applied["facts_written"] += len(rows)

    open_questions = result.get("open_questions", [])
    if isinstance(open_questions, list):
        rows = [row for row in open_questions if isinstance(row, dict)]
        if rows:
            rows = _derived_memory_rows(
                rows,
                evidence_kind="derived_question",
                source_evidence_ids=normalized_source_evidence_ids,
                source_turn_ids=normalized_source_turn_ids,
            )
            for scope_type, scope_key in scope_targets:
                append_unique_memory_rows(
                    memory_questions_path(guild_id, scope_type=scope_type, scope_key=scope_key),
                    rows,
                    memory_loop_limit,
                    mirror_path=vault_questions_path(guild_id, scope_type=scope_type, scope_key=scope_key),
                )
                applied["questions_written"] += len(rows)
                try:
                    promote_open_questions(guild_id, rows, scope_type=scope_type, scope_key=scope_key)
                except Exception as exc:
                    applied["question_promote_failures"] += 1
                    if log is not None:
                        log(
                            f"[PROACTIVE QUESTIONS] promote failed guild={guild_id} "
                            f"scope={scope_type}:{scope_key or 'default'} err={exc}"
                        )

    return applied


async def run_long_term_memory_update(
    guild_id: int,
    user_text: str,
    answer: str,
    *,
    room_key: str | None = None,
    person_key: str | None = None,
    session_memory_key: str | None = None,
    source_turn_id: str | None = None,
    turn_scope: Any = None,
    collect_layers: Callable[..., dict[str, dict[str, Any]]],
    ask_summary_llm: Callable[..., Any],
    is_context_size_error: Callable[[Exception], bool],
    should_log_latency: Callable[[float], bool],
    memory_fact_limit: int,
    memory_loop_limit: int,
    raw_limit: int,
    log: Callable[[str], None] | None = None,
    now: Callable[[], float] = time.monotonic,
) -> dict[str, Any]:
    started_at = now()
    scope_note = session_memory_key or room_key or "guild"

    def emit(message: str) -> None:
        if log is not None:
            log(message)

    def raise_if_cancelled() -> None:
        if turn_scope is not None:
            turn_scope.raise_if_cancelled()

    raise_if_cancelled()
    layers = collect_layers(
        guild_id,
        room_key=room_key,
        person_key=person_key,
        session_memory_key=session_memory_key,
    )
    current_summary = layered_summary_text(layers)
    recent = recent_memory_groups(
        layers,
        raw_limit=raw_limit,
        facts_limit=6,
        questions_limit=4,
    )
    source_evidence_ids, source_turn_ids = _memory_input_evidence(
        layers,
        recent,
        source_turn_id=source_turn_id,
        user_text=user_text,
        answer=answer,
        include_recent=True,
    )

    messages = build_long_term_memory_messages(
        current_summary=current_summary,
        recent_raw=recent["raw"],
        recent_facts=recent["facts"],
        recent_questions=recent["questions"],
        user_text=user_text,
        answer=answer,
        raw_limit=raw_limit,
    )

    result: dict[str, Any] | None = None
    failure: Exception | None = None
    try:
        raise_if_cancelled()
        maybe_result = await ask_summary_llm(
            messages,
            purpose="memory_summary",
            hot_path=False,
            turn_id=getattr(turn_scope, "turn_id", None),
            session_key=session_memory_key,
            source="memory_writebehind",
            guild_id=guild_id,
        )
        if isinstance(maybe_result, dict):
            result = maybe_result
    except Exception as exc:
        failure = exc
        if is_context_size_error(exc):
            compact_messages = build_compact_long_term_memory_messages(
                current_summary=current_summary,
                user_text=user_text,
                answer=answer,
            )
            try:
                raise_if_cancelled()
                maybe_result = await ask_summary_llm(
                    compact_messages,
                    max_tokens=220,
                    timeout_seconds=20,
                    purpose="memory_summary",
                    hot_path=False,
                    turn_id=getattr(turn_scope, "turn_id", None),
                    session_key=session_memory_key,
                    source="memory_writebehind",
                    guild_id=guild_id,
                )
                if isinstance(maybe_result, dict):
                    result = maybe_result
                    source_evidence_ids, source_turn_ids = _memory_input_evidence(
                        layers,
                        recent,
                        source_turn_id=source_turn_id,
                        user_text=user_text,
                        answer=answer,
                        include_recent=False,
                    )
            except Exception as retry_exc:
                failure = retry_exc
                emit(f"[MEMORY] compact retry 실패: {retry_exc}")
            else:
                emit("[MEMORY] compact retry 성공")

    if not isinstance(result, dict):
        emit(f"[MEMORY] 요약 업데이트 실패: {failure}")
        elapsed_ms = (now() - started_at) * 1000.0
        if should_log_latency(elapsed_ms):
            emit(f"[MEMORY LATENCY] guild={guild_id} scope={scope_note} failed_after_ms={elapsed_ms:.0f}")
        return {"ok": False, "elapsed_ms": elapsed_ms, "scope": scope_note}

    raise_if_cancelled()
    applied = apply_long_term_memory_result(
        guild_id,
        result,
        room_key=room_key,
        person_key=person_key,
        session_memory_key=session_memory_key,
        memory_fact_limit=memory_fact_limit,
        memory_loop_limit=memory_loop_limit,
        source_evidence_ids=source_evidence_ids,
        source_turn_ids=source_turn_ids,
        log=log,
    )

    elapsed_ms = (now() - started_at) * 1000.0
    if should_log_latency(elapsed_ms):
        emit(f"[MEMORY LATENCY] guild={guild_id} scope={scope_note} ms={elapsed_ms:.0f}")
    return {"ok": True, "elapsed_ms": elapsed_ms, "scope": scope_note, "applied": applied}


__all__ = ["apply_long_term_memory_result", "run_long_term_memory_update"]
