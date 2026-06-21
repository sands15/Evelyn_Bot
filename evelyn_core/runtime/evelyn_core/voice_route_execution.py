from __future__ import annotations

import time
from dataclasses import dataclass, replace
from typing import Any, Awaitable, Callable, MutableMapping

from .skills import SkillContext, SkillResult
from .text import clean_text, is_user_echo_answer
from .turn_budget import build_turn_execution_budget
from .turn_lifecycle import TurnState
from .voice_orchestration import VoiceTurnRequest, VoiceTurnRouteContext
from .voice_pipeline import (
    ActionResult,
    AnswerPayload,
    RouteDecision,
    action_result_to_answer_payload,
    build_action_result,
    route_decision_policy_dict,
)


@dataclass(frozen=True)
class VoiceRouteExecutionDeps:
    update_session_state: Callable[..., Any]
    emit_delivery_plan_chunks: Callable[..., Awaitable[Any]]
    build_delivery_plan: Callable[..., Any]
    split_tts_sentences: Callable[..., Any]
    build_search_query: Callable[..., str]
    search_duckduckgo: Callable[..., Awaitable[list[dict[str, Any]]]]
    answer_from_search_results: Callable[..., Awaitable[str]]
    prepare_llm_messages: Callable[..., Awaitable[tuple[list[dict[str, Any]], dict | None, str, Any]]]
    policy_response_for_state: Callable[..., Any]
    build_route_decision_from_state: Callable[..., RouteDecision]
    apply_ask_gating: Callable[..., dict[str, Any]]
    build_route_decision: Callable[..., RouteDecision]
    apply_fast_path_question_policy: Callable[..., tuple[RouteDecision, bool]]
    should_await_user_reply_for_route: Callable[..., bool]
    answer_simple_local_chat_query: Callable[[str], str | None]
    answer_current_datetime_query: Callable[[str], str | None]
    answer_gpu_runtime_status_query: Callable[[str], str | None]
    synthesize_tool_result_with_main_llm: Callable[..., Awaitable[str]]
    observe_live_minecraft_state: Callable[..., Awaitable[dict[str, Any] | None]]
    skill_registry: Any
    recent_skill_dispatches: MutableMapping[str, float]
    build_main_response_guidance: Callable[..., str]
    build_main_llm_payload: Callable[..., Any]
    execute_main_llm_once: Callable[..., Awaitable[tuple[str, str] | Any]]
    build_answer_payload_from_text: Callable[[str], Any]
    resolve_route_executor: Callable[..., Any]
    model_name: str
    main_llm_stop_tokens: tuple[str, ...] | list[str]
    voice_llm_max_tokens: int
    default_internal_routes: set[str]
    disabled_main_app_skill_routes: set[str]
    skill_dispatch_cache_ttl_sec: float
    skill_dispatch_repeat_window_sec: float
    skill_dispatch_cache_max: int
    router_route_timeout_sec: float
    cognitive_timeout_sec: float
    router_llm_enabled: bool
    log: Callable[..., Any] = print


def build_skill_context(
    *,
    deps: VoiceRouteExecutionDeps,
    user_text: str,
    source: str,
    guild_id: int | None,
    session_key: str | None,
    room_key: str | None,
    person_key: str | None,
    session_memory_key: str | None,
    debug_text: str | None,
    metrics: dict | None,
    route_decision: RouteDecision,
    cognitive_state: dict | None,
    messages: list[dict[str, Any]] | None = None,
    minecraft_state: dict[str, Any] | None = None,
) -> SkillContext:
    return SkillContext(
        source=source,
        guild_id=guild_id,
        session_key=session_key,
        room_key=room_key,
        person_key=person_key,
        session_memory_key=session_memory_key,
        debug_text=debug_text,
        metrics=metrics,
        extras={
            "user_text": user_text,
            "route": route_decision.route,
            "action": route_decision.action,
            "prompt_text": route_decision.prompt_text,
            "user_visible_preface": route_decision.user_visible_preface,
            "needs_search": route_decision.needs_search,
            "route_policy": route_decision_policy_dict(route_decision),
            "should_interrupt_delivery": route_decision.should_interrupt_delivery,
            "cognitive_state": cognitive_state,
            "messages": list(messages or []),
            "minecraft_state": dict(minecraft_state or {}),
            "model_name": deps.model_name,
            "main_llm_stop_tokens": list(deps.main_llm_stop_tokens),
            "voice_llm_max_tokens": deps.voice_llm_max_tokens,
            "build_main_response_guidance_fn": deps.build_main_response_guidance,
            "build_main_llm_payload_fn": deps.build_main_llm_payload,
            "execute_main_llm_once_fn": deps.execute_main_llm_once,
            "synthesize_tool_result_with_main_llm_fn": deps.synthesize_tool_result_with_main_llm,
            "execute_search_then_answer_action_fn": lambda **kwargs: execute_search_then_answer_action(deps=deps, **kwargs),
            "build_answer_payload_from_text_fn": deps.build_answer_payload_from_text,
            "build_delivery_plan_fn": deps.build_delivery_plan,
            "split_tts_sentences_fn": deps.split_tts_sentences,
            "executor": deps.resolve_route_executor(guild_id=guild_id, route_name=str(route_decision.route or "")),
        },
    )


def skill_result_to_text(result: Any) -> str:
    if result is None:
        return ""
    if isinstance(result, SkillResult):
        return clean_text(result.display_text or result.answer_text or "")
    if isinstance(result, str):
        return clean_text(result)
    if isinstance(result, dict):
        for key in ("display_text", "answer_text", "message", "summary", "rationale"):
            value = clean_text(str(result.get(key) or ""))
            if value:
                return value
        goal = result.get("goal") or {}
        goal_name = clean_text(str(goal.get("name") or ""))
        goal_desc = clean_text(str(goal.get("description") or ""))
        steps = [clean_text(str(step)) for step in (result.get("proposed_steps") or []) if clean_text(str(step))]
        pieces = []
        if goal_name or goal_desc:
            pieces.append(f"[Minecraft] {goal_name}: {goal_desc}".strip())
        if steps:
            pieces.append(" / ".join(steps[:3]))
        return clean_text(" ".join(piece for piece in pieces if piece))
    return clean_text(str(result))


def make_skill_dispatch_key(*, route_name: str, source: str, session_key: str | None, user_text: str) -> str:
    base = clean_text(user_text).lower()
    return f"{route_name}|{source}|{session_key or '-'}|{base}"


def cleanup_recent_skill_dispatches(*, deps: VoiceRouteExecutionDeps, now: float | None = None) -> None:
    current = time.monotonic() if now is None else now
    stale_before = current - deps.skill_dispatch_cache_ttl_sec
    stale_keys = [key for key, ts in deps.recent_skill_dispatches.items() if float(ts or 0.0) < stale_before]
    for key in stale_keys:
        deps.recent_skill_dispatches.pop(key, None)
    if len(deps.recent_skill_dispatches) <= deps.skill_dispatch_cache_max:
        return
    overflow = len(deps.recent_skill_dispatches) - deps.skill_dispatch_cache_max
    for key, _ts in sorted(deps.recent_skill_dispatches.items(), key=lambda item: item[1])[:overflow]:
        deps.recent_skill_dispatches.pop(key, None)


async def maybe_execute_registered_route(
    *,
    deps: VoiceRouteExecutionDeps,
    route_decision: RouteDecision,
    user_text: str,
    source: str,
    guild_id: int | None,
    session_key: str | None,
    room_key: str | None,
    person_key: str | None,
    session_memory_key: str | None,
    debug_text: str | None,
    metrics: dict | None,
    cognitive_state: dict | None,
    messages: list[dict[str, Any]] | None = None,
    allow_internal_routes: set[str] | None = None,
) -> str | None:
    route_name = clean_text(route_decision.route)
    if not route_name:
        return None
    if route_name in deps.default_internal_routes and route_name not in (allow_internal_routes or set()):
        return None
    if route_name in deps.disabled_main_app_skill_routes:
        return None
    dispatch_key = make_skill_dispatch_key(
        route_name=route_name,
        source=source,
        session_key=session_key,
        user_text=user_text,
    )
    now = time.monotonic()
    cleanup_recent_skill_dispatches(deps=deps, now=now)
    last_dispatch = float(deps.recent_skill_dispatches.get(dispatch_key, 0.0) or 0.0)
    if last_dispatch > 0 and (now - last_dispatch) < deps.skill_dispatch_repeat_window_sec:
        return None
    skills = deps.skill_registry.find_by_route(route_name, source=source)
    if not skills:
        return None
    live_minecraft_state = await deps.observe_live_minecraft_state(guild_id)
    context = build_skill_context(
        deps=deps,
        user_text=user_text,
        source=source,
        guild_id=guild_id,
        session_key=session_key,
        room_key=room_key,
        person_key=person_key,
        session_memory_key=session_memory_key,
        debug_text=debug_text,
        metrics=metrics,
        route_decision=route_decision,
        cognitive_state=cognitive_state,
        messages=messages,
        minecraft_state=live_minecraft_state,
    )
    deps.recent_skill_dispatches[dispatch_key] = now
    result = await deps.skill_registry.execute(skills[0].name, context)
    if isinstance(result, SkillResult):
        if not result.handled or not result.should_emit:
            return None
        if result.dedupe_key:
            deps.recent_skill_dispatches[result.dedupe_key] = now
        if result.followup_route:
            if result.followup_delay_ms and int(result.followup_delay_ms) > 0:
                deps.recent_skill_dispatches[dispatch_key] = now + (int(result.followup_delay_ms) / 1000.0)
                return skill_result_to_text(result)
            followup_context = SkillContext(
                source=source,
                guild_id=guild_id,
                session_key=session_key,
                room_key=room_key,
                person_key=person_key,
                session_memory_key=session_memory_key,
                debug_text=debug_text,
                metrics=metrics,
                extras={
                    "user_text": user_text,
                    "answer_text": result.answer_text or result.display_text,
                    **dict(result.followup_payload or {}),
                    "route": result.followup_route,
                    "messages": list(messages or []),
                    "cognitive_state": cognitive_state,
                    "synthesize_tool_result_with_main_llm_fn": deps.synthesize_tool_result_with_main_llm,
                    "build_main_response_guidance_fn": deps.build_main_response_guidance,
                    "build_main_llm_payload_fn": deps.build_main_llm_payload,
                    "execute_main_llm_once_fn": deps.execute_main_llm_once,
                    "build_answer_payload_from_text_fn": deps.build_answer_payload_from_text,
                    "build_delivery_plan_fn": deps.build_delivery_plan,
                    "split_tts_sentences_fn": deps.split_tts_sentences,
                },
            )
            followup_skills = deps.skill_registry.find_by_route(result.followup_route, source=source)
            if followup_skills:
                followup_result = await deps.skill_registry.execute(followup_skills[0].name, followup_context)
                return skill_result_to_text(followup_result)
    return skill_result_to_text(result)


async def emit_action_result_delivery(
    action_result: ActionResult,
    *,
    deps: VoiceRouteExecutionDeps,
    on_sentence: Callable[[str], Awaitable[None]] | None,
    session_key: str | None,
    user_text: str,
    awaiting_user_reply: bool,
) -> AnswerPayload:
    answer_payload = action_result_to_answer_payload(action_result)
    if session_key is not None:
        deps.update_session_state(
            session_key,
            speaker="assistant",
            awaiting_user_reply=awaiting_user_reply,
            answer_text=answer_payload.display_text,
            user_text=user_text,
        )
    await deps.emit_delivery_plan_chunks(
        deps.build_delivery_plan(answer_payload, include_voice=on_sentence is not None, split_chunks=deps.split_tts_sentences),
        on_sentence=on_sentence,
    )
    return answer_payload


async def execute_search_then_answer_action(
    *,
    deps: VoiceRouteExecutionDeps,
    guild_id: int | None,
    user_text: str,
    session_key: str | None = None,
    messages: list[dict[str, Any]] | None = None,
) -> ActionResult:
    search_query = deps.build_search_query(
        guild_id,
        user_text,
        session_key=session_key,
        messages=messages,
    )
    try:
        results = await deps.search_duckduckgo(search_query)
        answer = await deps.answer_from_search_results(search_query, results)
        return build_action_result(
            action="search_then_answer",
            answer_text=clean_text(answer) or "지금 검색 결과를 정리하지 못했어. 잠깐 뒤에 다시 시도해줘.",
            metadata={"query": search_query, "result_count": len(results)},
        )
    except Exception:
        return build_action_result(
            action="search_then_answer",
            answer_text="지금 검색 결과를 바로 가져오지 못했어. 잠깐 뒤에 다시 시도해줘.",
            metadata={"query": search_query, "error": "search_failed"},
        )


async def prepare_route_context(
    user_text: str,
    guild_id: int | None = None,
    *,
    deps: VoiceRouteExecutionDeps,
    session_key: str | None = None,
    room_key: str | None = None,
    person_key: str | None = None,
    session_memory_key: str | None = None,
    source: str = "text",
    debug_text: str | None = None,
    metrics: dict | None = None,
    turn_scope: Any = None,
) -> tuple[list[dict[str, Any]], dict | None, RouteDecision, dict | None, bool]:
    if turn_scope is not None:
        turn_scope.transition(TurnState.ROUTING, reason="prepare_route_context")
    messages, cognitive_state, _route, context_policy = await deps.prepare_llm_messages(
        user_text,
        guild_id=guild_id,
        session_key=session_key,
        room_key=room_key,
        person_key=person_key,
        session_memory_key=session_memory_key,
        source=source,
        debug_text=debug_text,
        metrics=metrics,
        turn_scope=turn_scope,
    )
    policy_response = deps.policy_response_for_state(cognitive_state, source=source, user_text=user_text)
    route_decision = deps.build_route_decision_from_state(
        cognitive_state=cognitive_state,
        source=source,
        user_text=user_text,
        policy_response=policy_response,
        apply_ask_gating=deps.apply_ask_gating,
        build_route_decision=deps.build_route_decision,
    )
    search_needed = bool(route_decision.needs_search or context_policy.needs_search)
    if search_needed:
        route_decision = replace(
            route_decision,
            action="search_then_answer",
            route="search_executor",
            needs_main_llm=False,
            needs_memory=bool(context_policy.needs_memory),
            needs_runtime_state=bool(context_policy.needs_runtime_state),
            needs_minecraft_state=bool(context_policy.needs_minecraft_state),
            needs_vision=bool(context_policy.needs_vision),
            needs_skill_graph=bool(context_policy.needs_skill_graph),
            needs_long_context=bool(context_policy.needs_long_context),
            needs_search=True,
            needs_tts=bool(context_policy.needs_tts),
            response_mode=clean_text(context_policy.response_mode) or route_decision.response_mode,
            priority="accuracy",
        )
    else:
        route_decision = replace(
            route_decision,
            needs_main_llm=bool(route_decision.needs_main_llm and context_policy.needs_main_llm),
            needs_memory=bool(context_policy.needs_memory),
            needs_runtime_state=bool(context_policy.needs_runtime_state),
            needs_minecraft_state=bool(context_policy.needs_minecraft_state),
            needs_vision=bool(context_policy.needs_vision),
            needs_skill_graph=bool(context_policy.needs_skill_graph),
            needs_long_context=bool(context_policy.needs_long_context),
            needs_search=False,
            needs_tts=bool(context_policy.needs_tts),
            response_mode=clean_text(context_policy.response_mode) or route_decision.response_mode,
            priority=clean_text(context_policy.priority) or route_decision.priority,
        )
    route_question_policy = None
    if metrics is not None:
        maybe_policy = metrics.setdefault("meta", {}).get("route_question_policy")
        route_question_policy = maybe_policy if isinstance(maybe_policy, dict) else None
    route_decision, question_cooldown_hit = deps.apply_fast_path_question_policy(
        route_decision,
        user_text=user_text,
        session_key=session_key,
        route_meta_question_policy=route_question_policy,
    )
    if metrics is not None:
        metrics.setdefault("meta", {})["question_cooldown_hit"] = bool(question_cooldown_hit)
        metrics.setdefault("meta", {})["route_policy"] = route_decision_policy_dict(route_decision)
        metrics.setdefault("meta", {})["execution_budget"] = build_turn_execution_budget(
            router_timeout_sec=deps.router_route_timeout_sec,
            context_timeout_sec=deps.cognitive_timeout_sec,
            memory_timeout_sec=deps.cognitive_timeout_sec,
            fallback_route=route_decision.route,
            router_enabled=deps.router_llm_enabled,
            context_policy=context_policy,
            route_decision=route_decision,
        ).to_dict()
    gated_state = deps.apply_ask_gating(cognitive_state, source=source) if cognitive_state is not None else None
    awaiting_user_reply = deps.should_await_user_reply_for_route(
        gated_state=gated_state,
        route_action=route_decision.action,
    )
    return messages, cognitive_state, route_decision, gated_state, awaiting_user_reply


async def maybe_handle_short_circuit_route(
    *,
    deps: VoiceRouteExecutionDeps,
    route_decision: RouteDecision,
    source: str,
    guild_id: int | None,
    user_text: str,
    session_key: str | None,
    room_key: str | None = None,
    person_key: str | None = None,
    session_memory_key: str | None = None,
    debug_text: str | None = None,
    on_sentence: Callable[[str], Awaitable[None]] | None = None,
    on_first_chunk: Callable[[], None] | None = None,
    awaiting_user_reply: bool = False,
    metrics: dict | None = None,
    messages: list[dict[str, Any]] | None = None,
    cognitive_state: dict | None = None,
) -> tuple[str | None, Callable[[], None] | None]:
    _ = room_key, person_key, session_memory_key, debug_text
    delivery_on_sentence = on_sentence if route_decision.needs_tts else None
    if metrics is not None:
        metrics.setdefault("meta", {})["needs_tts"] = bool(route_decision.needs_tts and on_sentence is not None)

    simple_local_answer = deps.answer_simple_local_chat_query(user_text)
    if simple_local_answer:
        if on_first_chunk is not None:
            on_first_chunk()
            on_first_chunk = None
        answer_payload = await emit_action_result_delivery(
            build_action_result(
                action="answer",
                answer_text=simple_local_answer,
                metadata={"route": "simple_local_chat_fast_path"},
            ),
            deps=deps,
            on_sentence=delivery_on_sentence,
            session_key=session_key,
            user_text=user_text,
            awaiting_user_reply=False,
        )
        if metrics is not None:
            elapsed_ms = (time.monotonic() - float(metrics.get("started_at", time.monotonic()))) * 1000.0
            metrics.setdefault("marks", {})["policy_short_circuit"] = elapsed_ms
            metrics.setdefault("marks", {})["llm_done"] = elapsed_ms
            metrics.setdefault("marks", {})["t_main_done"] = elapsed_ms
            metrics.setdefault("meta", {})["deterministic_fast_path"] = "simple_local_chat"
        return answer_payload.display_text, on_first_chunk

    datetime_answer = deps.answer_current_datetime_query(user_text)
    if datetime_answer:
        if on_first_chunk is not None:
            on_first_chunk()
            on_first_chunk = None
        answer_payload = await emit_action_result_delivery(
            build_action_result(
                action="answer",
                answer_text=datetime_answer,
                metadata={"route": "datetime_fast_path"},
            ),
            deps=deps,
            on_sentence=delivery_on_sentence,
            session_key=session_key,
            user_text=user_text,
            awaiting_user_reply=False,
        )
        if metrics is not None:
            elapsed_ms = (time.monotonic() - float(metrics.get("started_at", time.monotonic()))) * 1000.0
            metrics.setdefault("marks", {})["policy_short_circuit"] = elapsed_ms
            metrics.setdefault("marks", {})["llm_done"] = elapsed_ms
            metrics.setdefault("marks", {})["t_main_done"] = elapsed_ms
            metrics.setdefault("meta", {})["deterministic_fast_path"] = "datetime"
        return answer_payload.display_text, on_first_chunk

    gpu_runtime_answer = deps.answer_gpu_runtime_status_query(user_text)
    if gpu_runtime_answer:
        if on_first_chunk is not None:
            on_first_chunk()
            on_first_chunk = None
        answer_payload = await emit_action_result_delivery(
            build_action_result(
                action="answer",
                answer_text=gpu_runtime_answer,
                metadata={"route": "gpu_runtime_status_fast_path"},
            ),
            deps=deps,
            on_sentence=delivery_on_sentence,
            session_key=session_key,
            user_text=user_text,
            awaiting_user_reply=False,
        )
        if metrics is not None:
            elapsed_ms = (time.monotonic() - float(metrics.get("started_at", time.monotonic()))) * 1000.0
            metrics.setdefault("marks", {})["policy_short_circuit"] = elapsed_ms
            metrics.setdefault("marks", {})["llm_done"] = elapsed_ms
            metrics.setdefault("marks", {})["t_main_done"] = elapsed_ms
            metrics.setdefault("meta", {})["deterministic_fast_path"] = "gpu_runtime_status"
        return answer_payload.display_text, on_first_chunk

    if route_decision.action == "search_then_answer":
        if on_first_chunk is not None:
            on_first_chunk()
            on_first_chunk = None
        preface_text = route_decision.user_visible_preface
        if not preface_text or is_user_echo_answer(user_text, preface_text):
            preface_text = "잠깐 찾아보고 바로 말해줄게."
        await emit_action_result_delivery(
            build_action_result(
                action=route_decision.action,
                answer_text=preface_text,
                metadata={"route": route_decision.route, "phase": "preface"},
            ),
            deps=deps,
            on_sentence=delivery_on_sentence,
            session_key=session_key,
            user_text=user_text,
            awaiting_user_reply=awaiting_user_reply,
        )
        action_result = await execute_search_then_answer_action(
            deps=deps,
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
            source=source,
            messages=messages,
            cognitive_state=cognitive_state,
            route_decision=route_decision,
            metrics=metrics,
        )
        answer_payload = await emit_action_result_delivery(
            build_action_result(
                action=route_decision.action,
                answer_text=final_answer,
                metadata={
                    **(dict(action_result.metadata) if isinstance(action_result.metadata, dict) else {}),
                    "route": route_decision.route,
                    "phase": "main_synthesis",
                },
            ),
            deps=deps,
            on_sentence=delivery_on_sentence,
            session_key=session_key,
            user_text=user_text,
            awaiting_user_reply=False,
        )
        if metrics is not None:
            elapsed_ms = (time.monotonic() - float(metrics.get("started_at", time.monotonic()))) * 1000.0
            metrics.setdefault("marks", {})["policy_short_circuit"] = elapsed_ms
            metrics.setdefault("marks", {})["llm_done"] = elapsed_ms
            metrics.setdefault("marks", {})["t_main_done"] = elapsed_ms
        return answer_payload.display_text, on_first_chunk

    if route_decision.user_visible_preface and not is_user_echo_answer(user_text, route_decision.user_visible_preface):
        if on_first_chunk is not None:
            on_first_chunk()
            on_first_chunk = None
        policy_payload = await emit_action_result_delivery(
            build_action_result(
                action=route_decision.action,
                answer_text=route_decision.user_visible_preface,
                metadata={"route": route_decision.route},
            ),
            deps=deps,
            on_sentence=delivery_on_sentence,
            session_key=session_key,
            user_text=user_text,
            awaiting_user_reply=awaiting_user_reply,
        )
        if metrics is not None:
            metrics.setdefault("marks", {})["policy_short_circuit"] = (time.monotonic() - float(metrics.get("started_at", time.monotonic()))) * 1000.0
            metrics.setdefault("marks", {})["llm_done"] = (time.monotonic() - float(metrics.get("started_at", time.monotonic()))) * 1000.0
            metrics.setdefault("marks", {})["t_main_done"] = metrics.setdefault("marks", {}).get("llm_done")
        return policy_payload.display_text, on_first_chunk

    return None, on_first_chunk

