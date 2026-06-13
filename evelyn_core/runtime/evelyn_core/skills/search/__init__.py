from __future__ import annotations

import sys
from typing import Any

from ..base import SkillContext, SkillResult, require_callback
from ..registry import skill_registry

name = "search"
routes = ("search_executor",)
sources = ("text", "voice", "control_page")
description = "External search and search-then-answer flow."


async def execute(context: SkillContext) -> SkillResult:
    extras = context.extras or {}
    user_text = str(extras.get("user_text") or "")
    route = str(extras.get("route") or "search_executor")
    execute_search_then_answer_action = require_callback(extras, "execute_search_then_answer_action_fn")
    action_result = await execute_search_then_answer_action(
        guild_id=context.guild_id,
        user_text=user_text,
        session_key=context.session_key,
        messages=list(extras.get("messages") or []),
    )
    answer_text = str(getattr(action_result, "answer_text", "") or "")
    metadata = getattr(action_result, "metadata", {})
    return SkillResult(
        skill=name,
        route=route,
        display_text=answer_text,
        answer_text=answer_text,
        metadata=dict(metadata) if isinstance(metadata, dict) else {},
        dedupe_key=f"search|{user_text}",
        followup_route="main_synthesis",
        followup_payload={
            "answer_text": answer_text,
            "tool_name": "search",
            "tool_result_text": answer_text,
            "source": context.source,
            "search_metadata": dict(metadata) if isinstance(metadata, dict) else {},
        },
    )


SEARCH_SKILL = skill_registry.register_module(sys.modules[__name__])

__all__ = ["SEARCH_SKILL", "name", "routes", "sources", "description", "execute"]
