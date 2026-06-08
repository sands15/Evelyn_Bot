from __future__ import annotations

import sys
from typing import Any

from ..base import SkillContext, SkillResult, require_callback
from ..registry import skill_registry

name = "conversation"
routes = ("main_direct", "policy_short_circuit")
sources = ("text", "voice")
description = "General direct-answer conversation flow and follow-up generation."


async def execute(context: SkillContext) -> SkillResult:
    extras = context.extras or {}
    route = str(extras.get("route") or "main_direct")
    user_text = str(extras.get("user_text") or "")
    prompt_text = str(extras.get("prompt_text") or user_text)
    preface = str(extras.get("user_visible_preface") or "")

    if preface:
        return SkillResult(
            skill=name,
            route=route,
            display_text=preface,
            answer_text=preface,
            metadata={"kind": "policy_short_circuit"},
            dedupe_key=f"conversation|{route}|{user_text}",
        )

    build_main_response_guidance = require_callback(extras, "build_main_response_guidance_fn")
    build_main_llm_payload = require_callback(extras, "build_main_llm_payload_fn")
    execute_main_llm_once = require_callback(extras, "execute_main_llm_once_fn")

    cognitive_state = extras.get("cognitive_state") if isinstance(extras.get("cognitive_state"), dict) else None
    messages = list(extras.get("messages") or [])
    model_name = str(extras.get("model_name") or "")
    stop_tokens = extras.get("main_llm_stop_tokens")
    if not isinstance(stop_tokens, (list, tuple)):
        stop_tokens = None
    max_tokens = int(extras.get("voice_llm_max_tokens") or 0)

    final_user_text = f"{prompt_text}\n\n{build_main_response_guidance(cognitive_state, source=context.source)}"
    payload = build_main_llm_payload(
        model_name=model_name,
        messages=messages,
        final_user_text=final_user_text,
        source=context.source,
        stream=False,
        max_tokens=max_tokens,
        stop_tokens=stop_tokens,
    )
    answer, answer_source = await execute_main_llm_once(
        payload=payload,
        user_text=user_text,
    )
    answer_text = str(answer or "")
    return SkillResult(
        skill=name,
        route=route,
        display_text=answer_text,
        answer_text=answer_text,
        metadata={"answer_source": answer_source},
        dedupe_key=f"conversation|{route}|{user_text}",
    )


CONVERSATION_SKILL = skill_registry.register_module(sys.modules[__name__])

__all__ = ["CONVERSATION_SKILL", "name", "routes", "sources", "description", "execute"]
