from __future__ import annotations

import sys
from typing import Any

from ...text import clean_text
from ..base import SkillContext, SkillResult, require_callback
from ..registry import skill_registry

name = "delivery"
routes = ("delivery",)
sources = ("text", "voice", "control_page")
description = "Text delivery, TTS preparation, and playback-oriented response emission."


async def execute(context: SkillContext) -> SkillResult:
    extras = context.extras or {}
    answer_text = clean_text(str(extras.get("answer_text") or ""))
    if not answer_text:
        return SkillResult(
            skill=name,
            route="delivery",
            handled=False,
            status="empty_answer",
            should_emit=False,
            metadata={"reason": "missing_answer_text"},
        )
    build_answer_payload_from_text = require_callback(extras, "build_answer_payload_from_text_fn")
    build_delivery_plan = require_callback(extras, "build_delivery_plan_fn")
    split_tts_sentences = require_callback(extras, "split_tts_sentences_fn")
    answer_payload = build_answer_payload_from_text(answer_text)
    delivery_plan = build_delivery_plan(
        answer_payload,
        include_voice=context.source == "voice",
        split_chunks=split_tts_sentences,
    )
    return SkillResult(
        skill=name,
        route="delivery",
        display_text=str(getattr(answer_payload, "display_text", answer_text) or answer_text),
        answer_text=str(getattr(answer_payload, "spoken_text", answer_text) or answer_text),
        metadata={
            "tts_chunk_count": len(getattr(delivery_plan, "tts_chunks", []) or []),
            "should_play_voice": bool(getattr(delivery_plan, "should_play_voice", False)),
        },
        payload={
            "text_message": getattr(delivery_plan, "text_message", None),
            "tts_chunks": list(getattr(delivery_plan, "tts_chunks", []) or []),
            "should_play_voice": bool(getattr(delivery_plan, "should_play_voice", False)),
        },
        dedupe_key=f"delivery|{answer_text}",
    )


DELIVERY_SKILL = skill_registry.register_module(sys.modules[__name__])

__all__ = ["DELIVERY_SKILL", "name", "routes", "sources", "description", "execute"]
