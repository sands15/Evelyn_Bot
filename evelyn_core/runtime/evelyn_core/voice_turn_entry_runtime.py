from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Awaitable, Callable

from .voice_orchestration import (
    VoiceTurnOrchestrator,
    VoiceTurnOrchestratorDeps,
    VoiceTurnRequest,
)


@dataclass(frozen=True)
class VoiceTurnEntryRuntimeDeps:
    attach_current_task: Callable[[Any], Any]
    detach_task: Callable[[Any, Any], None]
    prepare_route_context: Callable[..., Awaitable[Any]]
    maybe_handle_short_circuit_route: Callable[..., Awaitable[Any]]
    maybe_execute_registered_route: Callable[..., Awaitable[Any]]
    run_main_llm_turn: Callable[..., Awaitable[str]]
    emit_delivery_plan_chunks: Callable[..., Awaitable[Any]]
    build_answer_payload_from_text: Callable[[str], Any]
    build_delivery_plan: Callable[..., Any]
    split_tts_sentences: Callable[..., Any]
    record_voice_pipeline_failure: Callable[..., Any]


async def ask_llm_streaming_from_runtime(
    user_text: str,
    *,
    deps: VoiceTurnEntryRuntimeDeps,
    guild_id: int | None = None,
    on_sentence: Callable[[str], Awaitable[None]] | None = None,
    on_first_chunk: Callable[[], None] | None = None,
    session_key: str | None = None,
    room_key: str | None = None,
    person_key: str | None = None,
    session_memory_key: str | None = None,
    source: str = "text",
    debug_text: str | None = None,
    metrics: dict | None = None,
    turn_scope: Any = None,
) -> str:
    task = deps.attach_current_task(turn_scope)
    try:
        request = VoiceTurnRequest(
            user_text=user_text,
            guild_id=guild_id,
            session_key=session_key,
            room_key=room_key,
            person_key=person_key,
            session_memory_key=session_memory_key,
            source=source,
            debug_text=debug_text,
            metrics=metrics,
            turn_scope=turn_scope,
            on_sentence=on_sentence,
            on_first_chunk=on_first_chunk,
        )
        orchestrator = VoiceTurnOrchestrator(
            VoiceTurnOrchestratorDeps(
                prepare_route_context=deps.prepare_route_context,
                maybe_handle_short_circuit_route=deps.maybe_handle_short_circuit_route,
                maybe_execute_registered_route=deps.maybe_execute_registered_route,
                run_main_llm_turn=deps.run_main_llm_turn,
                emit_delivery_plan_chunks=deps.emit_delivery_plan_chunks,
                build_answer_payload_from_text=deps.build_answer_payload_from_text,
                build_delivery_plan=deps.build_delivery_plan,
                split_tts_sentences=deps.split_tts_sentences,
            )
        )
        result = await orchestrator.execute(request)
        return result.answer_text
    except Exception as exc:
        deps.record_voice_pipeline_failure(
            "llm_failed",
            exc,
            metrics,
            stage="ask_llm_streaming",
        )
        raise
    finally:
        deps.detach_task(turn_scope, task)


__all__ = ["VoiceTurnEntryRuntimeDeps", "ask_llm_streaming_from_runtime"]
