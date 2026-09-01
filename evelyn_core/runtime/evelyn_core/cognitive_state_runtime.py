from __future__ import annotations

import asyncio
import re
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Awaitable, Callable

from . import config as runtime_config
from .memory_deletion_journal import (
    MemoryDeletionJournalIntegrityError,
    memory_deletion_journal_guard,
)


_TURN_ID_RE = re.compile(r"^[A-Za-z0-9._:-]{1,80}$")


def _cognitive_lineage_turn_ids(*values: Any) -> list[str]:
    found: set[str] = set()

    def visit(value: Any) -> None:
        if isinstance(value, dict):
            direct = value.get("source_turn_id")
            if isinstance(direct, str) and _TURN_ID_RE.fullmatch(direct):
                found.add(direct)
            many = value.get("source_turn_ids")
            if isinstance(many, (list, tuple)):
                for item in many:
                    if isinstance(item, str) and _TURN_ID_RE.fullmatch(item):
                        found.add(item)
            evidence_id = value.get("evidence_id")
            if isinstance(evidence_id, str):
                match = re.fullmatch(
                    r"turn:([A-Za-z0-9._:-]{1,80}):(user|assistant)",
                    evidence_id,
                )
                if match is not None:
                    found.add(match.group(1))
            for child in value.values():
                if isinstance(child, (dict, list, tuple)):
                    visit(child)
        elif isinstance(value, (list, tuple)):
            for child in value:
                visit(child)

    for value in values:
        visit(value)
    return sorted(found)


def _with_cognitive_lineage(
    state: dict[str, Any],
    *sources: Any,
) -> dict[str, Any]:
    result = dict(state)
    turn_ids = _cognitive_lineage_turn_ids(result, *sources)
    if turn_ids:
        result["source_turn_ids"] = turn_ids
    return result


@dataclass(frozen=True)
class CognitiveStateRuntimeDeps:
    attach_current_task: Callable[[Any], Any]
    detach_task: Callable[[Any, Any], None]
    cognitive_locks: dict[int, asyncio.Lock]
    collect_memory_layers: Callable[..., Any]
    layered_summary_text: Callable[[Any], str]
    normalize_cognitive_state: Callable[[dict], dict]
    read_layered_cognitive_state: Callable[..., dict | None]
    get_matching_speculative_policy: Callable[..., dict | None]
    fast_path_policy: Callable[..., dict | None]
    session_state_snapshot: Callable[[str | None], dict[str, Any]]
    build_fast_cognitive_state: Callable[..., dict[str, Any]]
    write_json_file: Callable[..., Any]
    cognitive_state_path: Callable[..., Any]
    recent_memory_groups: Callable[..., dict[str, Any]]
    memory_cognitive_raw_limit: int
    build_cognitive_state_messages: Callable[..., list[dict[str, Any]]]
    ask_router_llm: Callable[..., Awaitable[dict]]
    cognitive_max_tokens: int
    cognitive_timeout_sec: float
    current_turn_id: Callable[[str | None], str | None]
    is_context_size_error: Callable[[BaseException], bool]
    build_compact_cognitive_state_messages: Callable[..., list[dict[str, Any]]]
    should_log_voice_timing: Callable[[float], bool]
    build_cognitive_fallback_state: Callable[..., dict[str, Any]]
    finalize_cognitive_state: Callable[..., dict[str, Any]]
    log: Callable[..., Any] = print
    archive_target_is_current: Callable[..., bool] | None = None


async def update_cognitive_state_from_runtime(
    guild_id: int,
    user_text: str,
    *,
    deps: CognitiveStateRuntimeDeps,
    session_key: str | None = None,
    room_key: str | None = None,
    person_key: str | None = None,
    session_memory_key: str | None = None,
    source: str = "text",
    turn_scope: Any = None,
    memory_index_dir: Path | None = None,
) -> dict:
    started_at = time.monotonic()
    archive_target = {
        "guild_id": int(guild_id),
        "turn_id": (
            getattr(turn_scope, "turn_id", None)
            or deps.current_turn_id(session_key)
        ),
        "session_key": session_key,
        "session_memory_key": session_memory_key,
        "person_key": person_key,
    }

    def raise_if_archive_retired() -> None:
        callback = getattr(deps, "archive_target_is_current", None)
        if callback is None:
            return
        try:
            current = callback(**archive_target) is True
        except Exception:
            current = False
        if not current:
            raise MemoryDeletionJournalIntegrityError()

    raise_if_archive_retired()
    task = deps.attach_current_task(turn_scope)
    lock = deps.cognitive_locks.setdefault(guild_id, asyncio.Lock())
    scope_type = "session" if session_memory_key else "person" if person_key else "room" if room_key else "guild"
    scope_key = session_memory_key if session_memory_key else person_key if person_key else room_key
    try:
        async with lock:
            raise_if_archive_retired()
            if turn_scope is not None:
                turn_scope.raise_if_cancelled()
            deletion_index_dir = (
                Path(memory_index_dir)
                if memory_index_dir is not None
                else Path(runtime_config.MEMORY_ROOT) / "memory_index"
            )
            with memory_deletion_journal_guard(
                deletion_index_dir,
                require_stable=True,
            ) as memory_deletion_position:
                layers = deps.collect_memory_layers(
                    guild_id,
                    room_key=room_key,
                    person_key=person_key,
                    session_memory_key=session_memory_key,
                )
                current_summary = deps.layered_summary_text(layers)
                current_state_payload = (
                    deps.read_layered_cognitive_state(
                        guild_id,
                        room_key=room_key,
                        person_key=person_key,
                        session_memory_key=session_memory_key,
                    )
                    or {}
                )
                current_state = deps.normalize_cognitive_state(
                    current_state_payload
                )
                current_source_turn_id = deps.current_turn_id(session_key)
                speculative = deps.get_matching_speculative_policy(session_key, user_text) if source == "voice" else None
                fast_policy = (speculative or {}).get("policy") or deps.fast_path_policy(
                    user_text,
                    source,
                    deps.session_state_snapshot(session_key),
                )
                if fast_policy is not None:
                    with memory_deletion_journal_guard(
                        deletion_index_dir,
                        expected_position=memory_deletion_position,
                        require_stable=True,
                    ):
                        raise_if_archive_retired()
                        state = deps.build_fast_cognitive_state(
                            user_text,
                            action=str(fast_policy.get("action", "answer")),
                            current_state=current_state,
                            reason_brief=str(fast_policy.get("reason_brief", "fast_path")),
                        )
                        state = _with_cognitive_lineage(
                            state,
                            current_state_payload,
                            layers,
                            {"source_turn_id": current_source_turn_id},
                        )
                        deps.write_json_file(deps.cognitive_state_path(guild_id, scope_type=scope_type, scope_key=scope_key), state)
                    return state
                recent = deps.recent_memory_groups(
                    layers,
                    raw_limit=deps.memory_cognitive_raw_limit,
                    facts_limit=4,
                    questions_limit=4,
                )

                messages = deps.build_cognitive_state_messages(
                    current_state=current_state,
                    current_summary=current_summary,
                    recent_raw=recent["raw"],
                    recent_facts=recent["facts"],
                    recent_questions=recent["questions"],
                    user_text=user_text,
                    raw_limit=deps.memory_cognitive_raw_limit,
                )
                compact_messages = deps.build_compact_cognitive_state_messages(
                    current_summary=current_summary,
                    user_text=user_text,
                )

            try:
                if turn_scope is not None:
                    turn_scope.raise_if_cancelled()
                result = await deps.ask_router_llm(
                    messages,
                    max_tokens=deps.cognitive_max_tokens,
                    timeout_seconds=deps.cognitive_timeout_sec,
                    purpose="cognitive",
                    hot_path=True,
                    turn_id=deps.current_turn_id(session_key),
                    session_key=session_key,
                    source=source,
                    guild_id=guild_id,
                    memory_deletion_position=memory_deletion_position,
                    memory_boundary_required=True,
                    memory_deletion_index_dir=deletion_index_dir,
                )
            except MemoryDeletionJournalIntegrityError:
                raise
            except Exception as e:
                if deps.is_context_size_error(e):
                    try:
                        if turn_scope is not None:
                            turn_scope.raise_if_cancelled()
                        result = await deps.ask_router_llm(
                            compact_messages,
                            max_tokens=deps.cognitive_max_tokens,
                            timeout_seconds=max(3.0, deps.cognitive_timeout_sec - 2.0),
                            purpose="cognitive",
                            hot_path=True,
                            turn_id=deps.current_turn_id(session_key),
                            session_key=session_key,
                            source=source,
                            guild_id=guild_id,
                            memory_deletion_position=memory_deletion_position,
                            memory_boundary_required=True,
                            memory_deletion_index_dir=deletion_index_dir,
                        )
                    except MemoryDeletionJournalIntegrityError:
                        raise
                    except Exception as e2:
                        e = e2
                        deps.log(
                            "[COGNITIVE] compact_retry_failed "
                            f"errorType={type(e2).__name__}"
                        )
                    else:
                        deps.log("[COGNITIVE] compact retry 성공")
                if "result" not in locals() or not isinstance(result, dict):
                    deps.log(
                        "[COGNITIVE] update_failed "
                        f"errorType={type(e).__name__}"
                    )
                    elapsed_ms = (time.monotonic() - started_at) * 1000.0
                    if deps.should_log_voice_timing(elapsed_ms):
                        deps.log(
                            f"[COGNITIVE LATENCY] guild={guild_id} "
                            f"scope={scope_type} failed_after_ms={elapsed_ms:.0f}"
                        )
                    with memory_deletion_journal_guard(
                        deletion_index_dir,
                        expected_position=memory_deletion_position,
                        require_stable=True,
                    ):
                        raise_if_archive_retired()
                        fallback = deps.build_cognitive_fallback_state(
                            current_state=current_state,
                            user_text=user_text,
                        )
                        fallback = _with_cognitive_lineage(
                            fallback,
                            current_state_payload,
                            layers,
                            {"source_turn_id": current_source_turn_id},
                        )
                        deps.write_json_file(deps.cognitive_state_path(guild_id, scope_type=scope_type, scope_key=scope_key), fallback)
                    return fallback

            if turn_scope is not None:
                turn_scope.raise_if_cancelled()
            with memory_deletion_journal_guard(
                deletion_index_dir,
                expected_position=memory_deletion_position,
                require_stable=True,
            ):
                raise_if_archive_retired()
                state = deps.finalize_cognitive_state(
                    result,
                    current_state=current_state,
                    user_text=user_text,
                )
                state = _with_cognitive_lineage(
                    state,
                    current_state_payload,
                    layers,
                    {"source_turn_id": current_source_turn_id},
                )
                deps.write_json_file(deps.cognitive_state_path(guild_id, scope_type=scope_type, scope_key=scope_key), state)
            elapsed_ms = (time.monotonic() - started_at) * 1000.0
            if deps.should_log_voice_timing(elapsed_ms):
                deps.log(
                    f"[COGNITIVE LATENCY] guild={guild_id} scope={scope_type} "
                    f"action={state.get('action')} ms={elapsed_ms:.0f}"
                )

            if state.get("action") == "ask" and state.get("question_for_user"):
                deps.log(
                    f"[COGNITIVE ASK] guild={guild_id} scope={scope_type} "
                    f"questionChars={len(str(state['question_for_user']))} "
                    f"reasonChars={len(str(state.get('reason_brief', '') or ''))} "
                    f"confidence={state.get('confidence', 0.0):.2f}"
                )
            elif state.get("action") == "search_then_answer":
                deps.log(
                    f"[COGNITIVE SEARCH] guild={guild_id} scope={scope_type} "
                    f"intentChars={len(str(state.get('user_intent', '') or ''))} "
                    f"reasonChars={len(str(state.get('reason_brief', '') or ''))}"
                )

            return state
    finally:
        deps.detach_task(turn_scope, task)
