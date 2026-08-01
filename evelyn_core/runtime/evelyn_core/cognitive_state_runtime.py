from __future__ import annotations

import asyncio
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Awaitable, Callable

from . import config as runtime_config
from .memory_deletion_journal import (
    MemoryDeletionJournalIntegrityError,
    memory_deletion_journal_guard,
)


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
    task = deps.attach_current_task(turn_scope)
    lock = deps.cognitive_locks.setdefault(guild_id, asyncio.Lock())
    scope_type = "session" if session_memory_key else "person" if person_key else "room" if room_key else "guild"
    scope_key = session_memory_key if session_memory_key else person_key if person_key else room_key
    try:
        async with lock:
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
                current_state = deps.normalize_cognitive_state(
                    deps.read_layered_cognitive_state(
                        guild_id,
                        room_key=room_key,
                        person_key=person_key,
                        session_memory_key=session_memory_key,
                    ) or {}
                )
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
                        state = deps.build_fast_cognitive_state(
                            user_text,
                            action=str(fast_policy.get("action", "answer")),
                            current_state=current_state,
                            reason_brief=str(fast_policy.get("reason_brief", "fast_path")),
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
                        deps.log(f"[COGNITIVE] compact retry 실패: {e2}")
                    else:
                        deps.log("[COGNITIVE] compact retry 성공")
                if "result" not in locals() or not isinstance(result, dict):
                    deps.log(f"[COGNITIVE] 상태 업데이트 실패 또는 timeout: {e}")
                    elapsed_ms = (time.monotonic() - started_at) * 1000.0
                    if deps.should_log_voice_timing(elapsed_ms):
                        deps.log(f"[COGNITIVE LATENCY] guild={guild_id} scope={scope_type}:{scope_key or 'default'} failed_after_ms={elapsed_ms:.0f}")
                    with memory_deletion_journal_guard(
                        deletion_index_dir,
                        expected_position=memory_deletion_position,
                        require_stable=True,
                    ):
                        fallback = deps.build_cognitive_fallback_state(
                            current_state=current_state,
                            user_text=user_text,
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
                state = deps.finalize_cognitive_state(
                    result,
                    current_state=current_state,
                    user_text=user_text,
                )
                deps.write_json_file(deps.cognitive_state_path(guild_id, scope_type=scope_type, scope_key=scope_key), state)
            elapsed_ms = (time.monotonic() - started_at) * 1000.0
            if deps.should_log_voice_timing(elapsed_ms):
                deps.log(f"[COGNITIVE LATENCY] guild={guild_id} scope={scope_type}:{scope_key or 'default'} action={state.get('action')} ms={elapsed_ms:.0f}")

            if state.get("action") == "ask" and state.get("question_for_user"):
                deps.log(
                    f"[COGNITIVE ASK] guild={guild_id} scope={scope_type}:{scope_key or 'default'} question={state['question_for_user']!r} reason={state.get('reason_brief', '')!r} confidence={state.get('confidence', 0.0):.2f}"
                )
            elif state.get("action") == "search_then_answer":
                deps.log(
                    f"[COGNITIVE SEARCH] guild={guild_id} scope={scope_type}:{scope_key or 'default'} intent={state.get('user_intent', '')!r} reason={state.get('reason_brief', '')!r}"
                )

            return state
    finally:
        deps.detach_task(turn_scope, task)
