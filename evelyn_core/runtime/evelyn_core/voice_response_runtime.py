from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Awaitable, Callable

import aiohttp

from .text import clean_text, clean_tts_text
from .voice_pipeline import AnswerPayload


@dataclass(frozen=True)
class VoiceResponseRuntimeDeps:
    model_name: str
    llm_server_url: str
    main_llm_chat_content_format: str
    main_llm_stop_tokens: tuple[str, ...] | list[str]
    voice_llm_max_tokens: int
    get_http_session: Callable[..., Awaitable[Any]]
    build_chat_messages: Callable[..., Any]
    fallback_answer_for: Callable[[str], str]
    split_tts_sentences: Callable[..., tuple[list[str], str]]
    build_answer_payload_from_text: Callable[..., AnswerPayload]
    log_voice_stage: Callable[..., Any]
    prepare_route_context: Callable[..., Awaitable[tuple[Any, Any, Any, Any, Any]]]
    prepare_llm_messages: Callable[..., Awaitable[tuple[Any, Any, Any, Any]]]
    is_user_echo_answer: Callable[[str, str], bool]
    is_casual_call_or_status_question: Callable[[str], bool]
    observe_live_minecraft_state: Callable[[int | None], Awaitable[dict[str, Any] | None]]
    build_runtime_status_context: Callable[..., Awaitable[str]]
    build_main_response_guidance: Callable[..., str]
    sanitize_model_output: Callable[[str], str]
    parse_response_action_tag: Callable[[str], tuple[Any, str]]
    extract_answer_from_reasoning: Callable[[str, str], str]
    sanitize_unrequested_minecraft_leak: Callable[[str, str], str]
    enforce_question_limits: Callable[..., tuple[str, dict[str, Any]]]
    record_question_trace: Callable[..., Any]
    format_minecraft_state_summary: Callable[[dict[str, Any] | None], str]
    log: Callable[..., Any] = print


def split_first_response_and_followup(answer: str, *, deps: VoiceResponseRuntimeDeps) -> tuple[str, str]:
    cleaned = clean_text(answer)
    if not cleaned:
        return "", ""
    sentences, _tail = deps.split_tts_sentences(cleaned, force=True)
    sentences = [clean_tts_text(sentence) for sentence in sentences if clean_tts_text(sentence)]
    if not sentences:
        return cleaned, ""
    first = sentences[0]
    followup = clean_text(" ".join(sentences[1:])) if len(sentences) > 1 else ""
    return first, followup


def normalize_compare_text(text: str) -> str:
    cleaned = clean_text(text).lower()
    return "".join(ch for ch in cleaned if ch.isalnum() or ch.isspace()).strip()


def is_duplicate_followup(first_response: str, followup_text: str) -> bool:
    first_norm = normalize_compare_text(first_response)
    follow_norm = normalize_compare_text(followup_text)
    if not first_norm or not follow_norm:
        return False
    if follow_norm == first_norm:
        return True
    if follow_norm.startswith(first_norm):
        remainder = follow_norm[len(first_norm):].strip()
        return len(remainder) <= 8
    return False


async def build_first_response_from_runtime(
    user_text: str,
    *,
    deps: VoiceResponseRuntimeDeps,
    guild_id: int | None = None,
    session_key: str | None = None,
    room_key: str | None = None,
    person_key: str | None = None,
    session_memory_key: str | None = None,
    source: str = "text",
    debug_text: str | None = None,
    metrics: dict | None = None,
) -> tuple[AnswerPayload, str, dict | None]:
    deps.log_voice_stage(metrics, "1단계 first response 생성 시작", extra=f"source={source} user_text_len={len(clean_text(user_text))}")
    messages, cognitive_state, route_decision, gated_state, _awaiting_user_reply = await deps.prepare_route_context(
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
    if route_decision.user_visible_preface and not deps.is_user_echo_answer(user_text, route_decision.user_visible_preface):
        return deps.build_answer_payload_from_text(route_decision.user_visible_preface), "", gated_state

    guided_user_text = user_text
    lightweight_persona_turn = deps.is_casual_call_or_status_question(guided_user_text)
    live_minecraft_state = None if lightweight_persona_turn else await deps.observe_live_minecraft_state(guild_id)
    runtime_status_context = await deps.build_runtime_status_context(force=bool(route_decision.needs_runtime_state))
    final_user_text = (
        f"{guided_user_text}\n\n"
        f"{deps.build_main_response_guidance(cognitive_state, source=source, user_text=guided_user_text, session_key=session_key, guild_id=guild_id, minecraft_state=live_minecraft_state, runtime_status_context=runtime_status_context, route_decision=route_decision)}"
    )

    payload = {
        "model": deps.model_name,
        "messages": deps.build_chat_messages(
            messages + [{"role": "user", "content": final_user_text}],
            content_format=deps.main_llm_chat_content_format,
        ),
        "temperature": 0.0,
        "max_tokens": min(40, deps.voice_llm_max_tokens),
        "stream": False,
        "cache_prompt": True,
        "stop": list(deps.main_llm_stop_tokens),
    }

    session = await deps.get_http_session()
    async with session.post(deps.llm_server_url, json=payload, timeout=aiohttp.ClientTimeout(total=120)) as resp:
        if resp.status != 200:
            error_text = await resp.text()
            raise RuntimeError(f"LLM 서버 오류: {resp.status} / {error_text[:300]}")

        data = await resp.json()
        choices = data.get("choices", [])
        if not choices:
            answer = deps.fallback_answer_for(user_text)
            return deps.build_answer_payload_from_text(answer), "", gated_state

        choice = choices[0]
        msg = choice.get("message", {})
        raw_answer = msg.get("content", "")
        _response_action, answer = deps.parse_response_action_tag(deps.sanitize_model_output(raw_answer))
        reasoning = msg.get("reasoning_content", "")
        finish_reason = choice.get("finish_reason", "")

        if not answer:
            answer = deps.extract_answer_from_reasoning(reasoning, user_text)
        if not answer:
            deps.log(f"LLM 1단계 응답 본문이 비어 있어서 fallback 사용, finish_reason={finish_reason}")
            answer = deps.fallback_answer_for(user_text)

        answer = deps.sanitize_unrequested_minecraft_leak(guided_user_text, answer)
        answer, question_shape_meta = deps.enforce_question_limits(answer, route_decision)
        deps.record_question_trace(
            route_decision=route_decision,
            answer=answer,
            shape_meta=question_shape_meta,
            metrics=metrics,
            cooldown_hit=bool((metrics or {}).get("meta", {}).get("question_cooldown_hit")) if isinstance(metrics, dict) else False,
        )
        first_response, followup_seed = split_first_response_and_followup(answer, deps=deps)
        return deps.build_answer_payload_from_text(first_response or answer), followup_seed, gated_state


async def build_followup_response_from_runtime(
    user_text: str,
    first_response: str,
    *,
    deps: VoiceResponseRuntimeDeps,
    guild_id: int | None = None,
    session_key: str | None = None,
    room_key: str | None = None,
    person_key: str | None = None,
    session_memory_key: str | None = None,
    source: str = "text",
    debug_text: str | None = None,
    metrics: dict | None = None,
) -> AnswerPayload:
    deps.log_voice_stage(metrics, "2단계 followup 생성 시작", extra=f"source={source} first_len={len(clean_text(first_response))}")
    messages, cognitive_state, _route, _context_policy = await deps.prepare_llm_messages(
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
    live_minecraft_state = None if deps.is_casual_call_or_status_question(user_text) else await deps.observe_live_minecraft_state(guild_id)
    minecraft_summary = deps.format_minecraft_state_summary(live_minecraft_state)
    followup_prompt = (
        f"사용자가 방금 한 말: {clean_text(user_text)}\n"
        f"이미 먼저 말한 첫 응답: {clean_text(first_response)}\n"
        + (f"현재 마인크래프트 상태: {minecraft_summary}\n" if minecraft_summary else "")
        + "\n"
        + "할 일: 첫 응답과 겹치지 않는 보충 설명만 1~2문장으로 이어서 말해. "
        + "첫 문장을 반복하거나 비슷하게 다시 시작하지 마. 새 정보가 없으면 빈 응답을 반환해."
    )
    payload = {
        "model": deps.model_name,
        "messages": deps.build_chat_messages(
            messages + [{"role": "user", "content": followup_prompt}],
            content_format=deps.main_llm_chat_content_format,
        ),
        "temperature": 0.0,
        "max_tokens": min(64, deps.voice_llm_max_tokens),
        "stream": False,
        "cache_prompt": True,
        "stop": list(deps.main_llm_stop_tokens),
    }
    session = await deps.get_http_session()
    async with session.post(deps.llm_server_url, json=payload, timeout=aiohttp.ClientTimeout(total=120)) as resp:
        if resp.status != 200:
            error_text = await resp.text()
            raise RuntimeError(f"LLM 서버 오류: {resp.status} / {error_text[:300]}")
        data = await resp.json()
        choices = data.get("choices", [])
        if not choices:
            return deps.build_answer_payload_from_text("")
        msg = choices[0].get("message", {})
        raw_answer = msg.get("content", "")
        _response_action, answer = deps.parse_response_action_tag(deps.sanitize_model_output(raw_answer))
    first, followup = split_first_response_and_followup(answer, deps=deps)
    if clean_text(first) == clean_text(first_response):
        return deps.build_answer_payload_from_text(followup)
    cleaned_answer = clean_text(answer)
    if is_duplicate_followup(first_response, cleaned_answer):
        return deps.build_answer_payload_from_text("")
    return deps.build_answer_payload_from_text(cleaned_answer)

