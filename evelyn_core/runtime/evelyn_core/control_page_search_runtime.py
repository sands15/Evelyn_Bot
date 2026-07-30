from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Awaitable, Callable


@dataclass(frozen=True)
class ControlPageSearchRuntimeDeps:
    control_page_effective_guild_id: Callable[[Any], int]
    control_page_session_key: Callable[[int | None], str]
    get_conversation_history: Callable[..., list[dict[str, Any]]]
    build_route_decision: Callable[..., Any]
    monotonic: Callable[[], float]
    execute_search_then_answer_action: Callable[..., Awaitable[Any]]
    synthesize_tool_result_with_main_llm: Callable[..., Awaitable[str]]
    clean_text: Callable[[str], str]
    get_session_lock: Callable[[str], Any]
    append_history: Callable[..., None]
    mark_session_active: Callable[..., None]
    commit_session_continuity: Callable[[], Awaitable[dict[str, Any]]]
    active_conversation_text_sec: float
    build_topic_id: Callable[..., str]
    schedule_local_control_tts: Callable[..., None]
    current_turn_id: Callable[[str | None], str | None]
    format_display_text: Callable[..., str]
    fallback_answer_for: Callable[[str], str]
    log: Callable[..., Any]


async def answer_control_page_search_text_from_runtime(
    guild: Any | None,
    user_text: str,
    *,
    deps: ControlPageSearchRuntimeDeps,
) -> str:
    guild_id = deps.control_page_effective_guild_id(guild)
    session_key = deps.control_page_session_key(guild_id)
    messages = list(deps.get_conversation_history(session_key=session_key, guild_id=guild_id))
    route_decision = deps.build_route_decision(
        action="search_then_answer",
        route="search_executor",
        source="control_page",
        prompt_text=user_text,
        needs_main_llm=False,
        needs_search=True,
        needs_tts=False,
        priority="accuracy",
    )
    metrics: dict[str, Any] = {
        "started_at": deps.monotonic(),
        "meta": {
            "source": "control_page",
            "session_key": session_key,
            "guild_id": guild_id,
            "selected_path": "control_page_search_direct",
        },
        "marks": {},
    }
    action_result = await deps.execute_search_then_answer_action(
        guild_id=guild_id,
        user_text=user_text,
        session_key=session_key,
        messages=messages,
    )
    final_answer = await deps.synthesize_tool_result_with_main_llm(
        user_text=user_text,
        tool_name="search",
        tool_result_text=action_result.answer_text,
        guild_id=guild_id,
        session_key=session_key,
        source="control_page",
        messages=messages,
        cognitive_state={"action": "search_then_answer", "user_intent": user_text},
        route_decision=route_decision,
        metrics=metrics,
    )
    reply = (
        deps.clean_text(final_answer)
        or deps.clean_text(action_result.answer_text)
        or "지금 검색 결과를 정리하지 못했어. 잠깐 뒤에 다시 시도해줘."
    )
    async with deps.get_session_lock(session_key):
        deps.append_history(session_key, user_text, reply, guild_id=guild_id)
        deps.mark_session_active(
            session_key,
            ttl_sec=deps.active_conversation_text_sec,
            speaker="assistant",
            awaiting_user_reply=False,
            topic_id=deps.build_topic_id(user_text, "search_executor", reply),
            answer_text=reply,
            user_text=user_text,
        )
        try:
            continuity_status = await deps.commit_session_continuity()
            metrics["meta"]["continuity_commit"] = "durable"
            metrics["meta"]["continuity_generation"] = int(
                continuity_status.get("generation") or 0
            )
        except Exception as exc:
            metrics["meta"]["continuity_commit"] = "failed"
            metrics["meta"]["continuity_error"] = (
                "conversation_continuity_commit_failed"
            )
            deps.log(
                "[CONTROL PAGE] search_continuity_commit_failed "
                f"session={session_key} errorType={type(exc).__name__}"
            )
    deps.schedule_local_control_tts(
        reply,
        turn_id=deps.current_turn_id(session_key),
        session_key=session_key,
    )
    return deps.format_display_text(reply, session_key=session_key).strip() or deps.fallback_answer_for(user_text)


__all__ = [
    "ControlPageSearchRuntimeDeps",
    "answer_control_page_search_text_from_runtime",
]
