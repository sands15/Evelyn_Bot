from __future__ import annotations

import sys

from ...text import clean_text
from ..base import SkillContext, SkillResult, require_callback
from ..registry import skill_registry

name = "main_synthesis"
routes = ("main_synthesis",)
sources = ("text", "voice", "control_page")
description = "Final main-LLM synthesis after a tool or search result is available."


async def execute(context: SkillContext) -> SkillResult:
    extras = context.extras or {}
    user_text = clean_text(str(extras.get("user_text") or ""))
    tool_name = clean_text(str(extras.get("tool_name") or "tool")) or "tool"
    tool_result_text = clean_text(str(extras.get("tool_result_text") or extras.get("answer_text") or ""))
    if not user_text or not tool_result_text:
        return SkillResult(
            skill=name,
            route="main_synthesis",
            handled=False,
            status="missing_input",
            should_emit=False,
            metadata={"reason": "missing_user_text_or_tool_result"},
        )

    synthesize_tool_result = require_callback(extras, "synthesize_tool_result_with_main_llm_fn")
    tool_result_metadata = extras.get("search_metadata")
    final_text = await synthesize_tool_result(
        user_text=user_text,
        tool_name=tool_name,
        tool_result_text=tool_result_text,
        tool_result_metadata=(
            dict(tool_result_metadata)
            if isinstance(tool_result_metadata, dict)
            else None
        ),
        guild_id=context.guild_id,
        session_key=context.session_key,
        source=context.source,
        messages=list(extras.get("messages") or []),
        cognitive_state=extras.get("cognitive_state") if isinstance(extras.get("cognitive_state"), dict) else None,
        metrics=context.metrics,
    )
    final_text = clean_text(str(final_text or "")) or tool_result_text
    return SkillResult(
        skill=name,
        route="main_synthesis",
        display_text=final_text,
        answer_text=final_text,
        metadata={"tool_name": tool_name, "synthesized": True},
        dedupe_key=f"main_synthesis|{tool_name}|{user_text}",
    )


MAIN_SYNTHESIS_SKILL = skill_registry.register_module(sys.modules[__name__])

__all__ = ["MAIN_SYNTHESIS_SKILL", "name", "routes", "sources", "description", "execute"]
