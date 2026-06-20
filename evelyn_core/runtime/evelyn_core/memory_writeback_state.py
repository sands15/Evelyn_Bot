from __future__ import annotations

import time
from typing import Any, Callable

from .memory import (
    append_unique_memory_rows,
    compact_working_summary,
    memory_facts_path,
    memory_questions_path,
    memory_summary_path,
    vault_facts_path,
    vault_questions_path,
    write_text_file,
)
from .memory_llm_context import (
    build_compact_long_term_memory_messages,
    build_long_term_memory_messages,
    layered_summary_text,
    memory_scope_targets,
    recent_memory_groups,
)
from .proactive_questions import promote_open_questions


def apply_long_term_memory_result(
    guild_id: int,
    result: dict[str, Any],
    *,
    room_key: str | None = None,
    person_key: str | None = None,
    session_memory_key: str | None = None,
    memory_fact_limit: int,
    memory_loop_limit: int,
    log: Callable[[str], None] | None = None,
) -> dict[str, Any]:
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
    }

    summary_update = compact_working_summary(str(result.get("summary_update", "")))
    if summary_update:
        for scope_type, scope_key in scope_targets:
            write_text_file(
                memory_summary_path(guild_id, scope_type=scope_type, scope_key=scope_key),
                summary_update,
            )
            applied["summary_written"] += 1

    durable_facts = result.get("durable_facts", [])
    if isinstance(durable_facts, list):
        rows = [row for row in durable_facts if isinstance(row, dict)]
        if rows:
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
        log=log,
    )

    elapsed_ms = (now() - started_at) * 1000.0
    if should_log_latency(elapsed_ms):
        emit(f"[MEMORY LATENCY] guild={guild_id} scope={scope_note} ms={elapsed_ms:.0f}")
    return {"ok": True, "elapsed_ms": elapsed_ms, "scope": scope_note, "applied": applied}


__all__ = ["apply_long_term_memory_result", "run_long_term_memory_update"]
