from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Awaitable, Callable

import aiohttp

from .text import clean_text


@dataclass(frozen=True)
class MainLlmRuntimeDeps:
    model_name: str
    llm_server_url: str
    main_llm_chat_content_format: str
    main_llm_stop_tokens: tuple[str, ...] | list[str]
    voice_llm_max_tokens: int
    get_http_session: Callable[..., Awaitable[Any]]
    fallback_answer_for: Callable[[str], str]
    extract_main_llm_answer_from_choice: Callable[..., tuple[str, str, str]]
    sanitize_model_output: Callable[[str], str]
    parse_response_action_tag: Callable[[str], Any]
    extract_answer_from_reasoning: Callable[[str, str], str]
    compact_memory_text: Callable[..., str]
    build_main_response_guidance: Callable[..., str]
    build_main_llm_payload: Callable[..., dict[str, Any]]
    strip_search_answer_sources: Callable[[str], str]
    enforce_question_limits: Callable[..., tuple[str, dict[str, Any]]]
    record_question_trace: Callable[..., Any]
    answer_promises_search: Callable[[str], bool]
    has_negated_search_marker: Callable[[str], bool]
    execute_search_then_answer_action: Callable[..., Awaitable[Any]]
    log: Callable[..., Any] = print


@dataclass(frozen=True)
class AskLlmOnceRuntimeDeps:
    log_voice_stage: Callable[..., None]
    clean_text: Callable[[str], str]
    prepare_route_context: Callable[..., Awaitable[tuple[Any, Any, Any, Any, bool]]]
    maybe_execute_registered_route: Callable[..., Awaitable[str | None]]
    is_user_echo_answer: Callable[[str, str], bool]
    update_session_state: Callable[..., None]
    build_answer_payload_from_text: Callable[[str], Any]
    session_is_casual_call_or_status_question: Callable[[str], bool]
    observe_live_minecraft_state: Callable[[int | None], Awaitable[Any]]
    build_runtime_status_context: Callable[..., Awaitable[Any]]
    build_main_response_guidance: Callable[..., str]
    build_main_llm_payload: Callable[..., dict[str, Any]]
    execute_main_llm_once: Callable[..., Awaitable[tuple[str, str]]]
    sanitize_unrequested_minecraft_leak: Callable[[str, str], str]
    resolve_promised_search_final_answer: Callable[..., Awaitable[str]]
    enforce_question_limits: Callable[[str, Any], tuple[str, dict[str, Any]]]
    record_question_trace: Callable[..., None]
    model_name: str
    main_llm_chat_content_format: str
    voice_llm_max_tokens: int
    main_llm_stop_tokens: tuple[str, ...] | list[str]


async def ask_llm_once_from_runtime(
    user_text: str,
    *,
    deps: AskLlmOnceRuntimeDeps,
    guild_id: int | None = None,
    session_key: str | None = None,
    room_key: str | None = None,
    person_key: str | None = None,
    session_memory_key: str | None = None,
    source: str = "text",
    debug_text: str | None = None,
    metrics: dict | None = None,
    record_question_trace_enabled: bool = True,
) -> str:
    deps.log_voice_stage(
        metrics,
        "LLM 2단계 요청 시작",
        extra=f"source={source} user_text_len={len(deps.clean_text(user_text))}",
    )
    messages, cognitive_state, route_decision, _gated_state, awaiting_user_reply = await deps.prepare_route_context(
        user_text,
        guild_id=guild_id,
        session_key=session_key,
        room_key=room_key,
        person_key=person_key,
        session_memory_key=session_memory_key,
        source=source,
        debug_text=debug_text,
        metrics=metrics,
    )
    skill_route_answer = await deps.maybe_execute_registered_route(
        route_decision=route_decision,
        user_text=user_text,
        source=source,
        guild_id=guild_id,
        session_key=session_key,
        room_key=room_key,
        person_key=person_key,
        session_memory_key=session_memory_key,
        debug_text=debug_text,
        metrics=metrics,
        cognitive_state=cognitive_state,
        messages=messages,
        allow_internal_routes={"main_direct", "policy_short_circuit", "search_executor"},
    )
    if skill_route_answer and not deps.is_user_echo_answer(user_text, skill_route_answer):
        if session_key is not None:
            deps.update_session_state(
                session_key,
                speaker="assistant",
                awaiting_user_reply=awaiting_user_reply,
                answer_text=skill_route_answer,
                user_text=user_text,
            )
        deps.log_voice_stage(
            metrics,
            "LLM 2단계 요청 끝남",
            extra=f"skill_route={route_decision.route} answer_len={len(skill_route_answer)}",
        )
        return deps.build_answer_payload_from_text(skill_route_answer).display_text

    if route_decision.user_visible_preface and not deps.is_user_echo_answer(
        user_text,
        route_decision.user_visible_preface,
    ):
        if session_key is not None:
            deps.update_session_state(
                session_key,
                speaker="assistant",
                awaiting_user_reply=awaiting_user_reply,
                answer_text=route_decision.user_visible_preface,
                user_text=user_text,
            )
        deps.log_voice_stage(
            metrics,
            "LLM 2단계 요청 끝남",
            extra=f"policy_len={len(route_decision.user_visible_preface)}",
        )
        return deps.build_answer_payload_from_text(route_decision.user_visible_preface).display_text

    guided_user_text = route_decision.prompt_text or user_text
    lightweight_persona_turn = deps.session_is_casual_call_or_status_question(guided_user_text)
    live_minecraft_state = None if lightweight_persona_turn else await deps.observe_live_minecraft_state(guild_id)
    runtime_status_context = await deps.build_runtime_status_context(force=bool(route_decision.needs_runtime_state))
    final_user_text = (
        f"{guided_user_text}\n\n"
        f"{deps.build_main_response_guidance(cognitive_state, source=source, user_text=guided_user_text, session_key=session_key, guild_id=guild_id, minecraft_state=live_minecraft_state, runtime_status_context=runtime_status_context, route_decision=route_decision)}"
    )
    payload = deps.build_main_llm_payload(
        model_name=deps.model_name,
        messages=messages,
        final_user_text=final_user_text,
        source=source,
        stream=False,
        content_format=deps.main_llm_chat_content_format,
        max_tokens=deps.voice_llm_max_tokens,
        stop_tokens=deps.main_llm_stop_tokens,
    )
    answer, answer_source = await deps.execute_main_llm_once(payload=payload, user_text=user_text)
    answer = deps.sanitize_unrequested_minecraft_leak(guided_user_text, answer)
    answer = await deps.resolve_promised_search_final_answer(
        user_text=user_text,
        answer_text=answer,
        guild_id=guild_id,
        session_key=session_key,
        source=source,
        messages=messages,
        cognitive_state=cognitive_state,
        route_decision=route_decision,
        metrics=metrics,
    )
    answer, question_shape_meta = deps.enforce_question_limits(answer, route_decision)
    if record_question_trace_enabled:
        deps.record_question_trace(
            route_decision=route_decision,
            answer=answer,
            shape_meta=question_shape_meta,
            metrics=metrics,
            cooldown_hit=bool((metrics or {}).get("meta", {}).get("question_cooldown_hit"))
            if isinstance(metrics, dict)
            else False,
        )
    if answer_source == "reasoning":
        deps.log_voice_stage(metrics, "LLM 2단계 요청 끝남", extra=f"reasoning_len={len(answer)}")
    elif answer_source.startswith("fallback"):
        deps.log_voice_stage(metrics, "LLM canned reply 사용", extra=f"reason={answer_source} fallback_len={len(answer)}")
    else:
        deps.log_voice_stage(metrics, "LLM 2단계 요청 끝남", extra=f"answer_len={len(answer)}")
    return deps.build_answer_payload_from_text(answer).display_text


async def execute_main_llm_once_from_runtime(
    *,
    deps: MainLlmRuntimeDeps,
    payload: dict[str, Any],
    user_text: str,
) -> tuple[str, str]:
    timeout = aiohttp.ClientTimeout(total=120)
    session = await deps.get_http_session()
    async with session.post(deps.llm_server_url, json=payload, timeout=timeout) as resp:
        if resp.status != 200:
            error_text = await resp.text()
            raise RuntimeError(f"LLM 서버 오류: {resp.status} / {error_text[:300]}")
        data = await resp.json()
    choices = data.get("choices", [])
    if not choices:
        return deps.fallback_answer_for(user_text), "fallback_empty_choices"
    answer, answer_source, finish_reason = deps.extract_main_llm_answer_from_choice(
        choices[0],
        user_text,
        sanitize_output=deps.sanitize_model_output,
        parse_response_action_tag=deps.parse_response_action_tag,
        extract_answer_from_reasoning=deps.extract_answer_from_reasoning,
    )
    if answer:
        return answer, answer_source
    deps.log(f"LLM 응답 본문이 비어 있어서 fallback 사용, finish_reason={finish_reason}")
    return deps.fallback_answer_for(user_text), "fallback_empty_body"


def render_tool_synthesis_recent_context(
    messages: list[dict[str, Any]] | None,
    *,
    deps: MainLlmRuntimeDeps,
    user_text: str,
    max_items: int = 6,
    max_chars: int = 900,
) -> str:
    current = clean_text(user_text).lower()
    rendered: list[str] = []
    for item in list(messages or [])[-max_items:]:
        if not isinstance(item, dict):
            continue
        role = clean_text(str(item.get("role") or ""))
        if role not in {"user", "assistant"}:
            continue
        content = clean_text(str(item.get("content") or ""))
        if not content or content.lower() == current:
            continue
        label = "user" if role == "user" else "assistant"
        rendered.append(f"{label}: {deps.compact_memory_text(content, max_chars=180)}")
    context = "\n".join(rendered)
    return deps.compact_memory_text(context, max_chars=max_chars)


def tool_synthesis_answer_drifted(answer: str, *, user_text: str, tool_result_text: str) -> bool:
    cleaned_answer = clean_text(answer)
    if not cleaned_answer:
        return False
    anchor = f"{clean_text(user_text)}\n{clean_text(tool_result_text)}"
    suspicious_terms = ("동물", "버튼", "좌표", "클릭")
    if any(term in cleaned_answer and term not in anchor for term in suspicious_terms):
        return True
    if any(phrase in cleaned_answer for phrase in ("질문했을 때", "요청했습니다", "요청했어")):
        if "날씨" in anchor and "날씨" in cleaned_answer:
            return True
    return False


async def synthesize_tool_result_with_main_llm_from_runtime(
    *,
    deps: MainLlmRuntimeDeps,
    user_text: str,
    tool_name: str,
    tool_result_text: str,
    guild_id: int | None = None,
    session_key: str | None = None,
    source: str = "text",
    messages: list[dict[str, Any]] | None = None,
    cognitive_state: dict | None = None,
    route_decision: Any = None,
    metrics: dict | None = None,
) -> str:
    cleaned_user = clean_text(user_text)
    cleaned_result = clean_text(tool_result_text)
    if not cleaned_user or not cleaned_result:
        return cleaned_result
    if metrics is not None:
        metrics.setdefault("meta", {})["main_synthesis_requested"] = {
            "tool_name": clean_text(tool_name) or "tool",
            "tool_result_chars": len(cleaned_result),
        }
    recent_context = render_tool_synthesis_recent_context(messages, deps=deps, user_text=cleaned_user)
    synthesis_prompt = (
        "A tool result is now available. Produce the final answer to the user in Korean.\n"
        "This is the final answer phase, not a preface. Do not say that you will look it up now.\n"
        "Use Evelyn's normal conversational tone. If the tool result is weak or incomplete, say so plainly and give the best next step.\n"
        "Treat recent context only as a way to resolve short follow-ups like 'search it' or 'tell me the weather'.\n"
        "Do not introduce unrelated objects, buttons, coordinates, animals, or old topics unless they appear in the original request or tool result.\n"
        "Ground the final answer in the tool result below.\n\n"
        f"Original user request:\n{cleaned_user}\n\n"
        f"Recent conversation context for ellipsis resolution only:\n{recent_context or '(none)'}\n\n"
        f"Tool name:\n{clean_text(tool_name) or 'tool'}\n\n"
        f"Tool result:\n{cleaned_result}"
    )
    final_user_text = (
        f"{synthesis_prompt}\n\n"
        f"{deps.build_main_response_guidance(cognitive_state, source=source, user_text=cleaned_user, session_key=session_key, guild_id=guild_id, route_decision=route_decision)}"
    )
    payload = deps.build_main_llm_payload(
        model_name=deps.model_name,
        messages=[],
        final_user_text=final_user_text,
        source=source,
        stream=False,
        content_format=deps.main_llm_chat_content_format,
        max_tokens=deps.voice_llm_max_tokens,
        stop_tokens=deps.main_llm_stop_tokens,
    )
    answer, answer_source = await execute_main_llm_once_from_runtime(
        deps=deps,
        payload=payload,
        user_text=cleaned_user,
    )
    answer = deps.strip_search_answer_sources(deps.sanitize_model_output(answer))
    if tool_synthesis_answer_drifted(answer, user_text=cleaned_user, tool_result_text=cleaned_result):
        if metrics is not None:
            metrics.setdefault("meta", {})["main_synthesis_drift_guard"] = True
        answer = cleaned_result
    if route_decision is not None:
        answer, question_shape_meta = deps.enforce_question_limits(answer, route_decision)
        deps.record_question_trace(
            route_decision=route_decision,
            answer=answer,
            shape_meta=question_shape_meta,
            metrics=metrics,
            cooldown_hit=bool((metrics or {}).get("meta", {}).get("question_cooldown_hit")) if isinstance(metrics, dict) else False,
        )
    if metrics is not None:
        metrics.setdefault("meta", {})["main_synthesis_answer_source"] = answer_source
    return clean_text(answer) or cleaned_result


async def resolve_promised_search_final_answer_from_runtime(
    *,
    deps: MainLlmRuntimeDeps,
    user_text: str,
    answer_text: str,
    guild_id: int | None = None,
    session_key: str | None = None,
    source: str = "text",
    messages: list[dict[str, Any]] | None = None,
    cognitive_state: dict | None = None,
    route_decision: Any = None,
    metrics: dict | None = None,
) -> str:
    answer = clean_text(answer_text)
    if not answer or not deps.answer_promises_search(answer):
        return answer
    if deps.has_negated_search_marker(user_text):
        if metrics is not None:
            metrics.setdefault("meta", {})["promised_search_escalation_skipped"] = "negated_search"
        return answer
    if metrics is not None:
        metrics.setdefault("meta", {})["promised_search_escalated"] = True

    action_result = await deps.execute_search_then_answer_action(
        guild_id=guild_id,
        user_text=user_text,
        session_key=session_key,
        messages=messages,
    )
    final_answer = await synthesize_tool_result_with_main_llm_from_runtime(
        deps=deps,
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
    if final_answer and not deps.answer_promises_search(final_answer):
        return final_answer
    return clean_text(action_result.answer_text) or answer
