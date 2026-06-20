from __future__ import annotations

import re

from .text import clean_text


def normalize_route_name(value: str) -> str:
    route = clean_text(value).lower()
    if route in {"subwait", "sub_wait", "wait", "fresh_sub", "fresh-sub"}:
        return "sub_wait"
    if route in {"subhint", "sub_hint", "hint", "cached_sub", "cached-sub"}:
        return "sub_hint"
    if route in {"voice_context", "voice-context", "context", "memory_context"}:
        return "sub_hint"
    return "main_direct"


def should_force_voice_context_route(user_text: str) -> bool:
    text = clean_text(user_text)
    if not text:
        return False
    voice_context_markers = [
        "기억",
        "방금",
        "아까",
        "전에",
        "이전",
        "말했",
        "했던",
        "하던",
        "무슨 얘기",
        "뭐지",
        "이어",
        "계속",
        "정리",
        "요약",
        "우리",
        "우리가",
        "하기로",
        "먹기로",
        "가기로",
        "약속",
        "정했",
    ]
    marker_hits = sum(1 for marker in voice_context_markers if marker in text)
    if marker_hits >= 1:
        return True
    return bool(re.search(r"(우리|우리가).*(하기로|먹기로|가기로)", text))


def classify_llm_route_fallback(user_text: str, *, source: str = "text") -> str:
    text = clean_text(user_text)
    if source == "voice" and not should_force_voice_context_route(text):
        return "main_direct"

    short_text = len(text) <= 18 or len(text.split()) <= 4
    if short_text and source != "voice":
        return "main_direct"

    context_markers = [
        "아까",
        "방금",
        "전에",
        "이전",
        "기억",
        "문맥",
        "계속",
        "이어",
        "요약",
        "정리",
        "판단",
        "비교",
        "설명",
        "의견",
        "생각",
        "왜",
        "어떻게",
    ]
    marker_hits = sum(1 for marker in context_markers if marker in text)

    if len(text) >= 60 or marker_hits >= 2:
        return "sub_wait"
    if len(text) >= 24 or marker_hits >= 1:
        return "sub_hint"
    return "main_direct"


__all__ = [
    "classify_llm_route_fallback",
    "normalize_route_name",
    "should_force_voice_context_route",
]
