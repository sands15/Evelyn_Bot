from __future__ import annotations

from typing import Any

from .query_intents import extract_korean_location_hint, is_weather_query, resolve_recent_weather_location
from .search_followup_policy import is_generic_search_followup_text, is_underspecified_weather_query
from .text import clean_text, strip_omnivoice_tags


def recent_user_text_candidates(
    messages: list[dict[str, Any]] | None,
    *,
    exclude_text: str = "",
    limit: int = 8,
) -> list[str]:
    excluded = clean_text(exclude_text).lower()
    candidates: list[str] = []
    for item in reversed(list(messages or [])):
        if not isinstance(item, dict) or item.get("role") != "user":
            continue
        content = clean_text(str(item.get("content") or ""))
        if not content or content.lower() == excluded:
            continue
        candidates.append(content)
        if len(candidates) >= limit:
            break
    return candidates


def resolve_contextual_search_query(
    user_text: str,
    *,
    messages: list[dict[str, Any]] | None = None,
) -> str:
    text = clean_text(strip_omnivoice_tags(user_text))
    recent_users = recent_user_text_candidates(messages, exclude_text=text)
    if is_generic_search_followup_text(text):
        for candidate in recent_users:
            if is_generic_search_followup_text(candidate):
                continue
            return candidate
    if is_underspecified_weather_query(text):
        for candidate in recent_users:
            if "날씨" in candidate and not is_underspecified_weather_query(candidate):
                return candidate
    return text


def enrich_weather_search_query_from_context(
    user_text: str,
    *,
    messages: list[dict[str, Any]] | None = None,
    memory_summary: str = "",
) -> str:
    text = clean_text(strip_omnivoice_tags(user_text))
    if not is_weather_query(text) or extract_korean_location_hint(text):
        return text
    recent_users = recent_user_text_candidates(messages, exclude_text=text, limit=10)
    location = resolve_recent_weather_location(recent_users)
    if not location and memory_summary:
        location = extract_korean_location_hint(memory_summary)
    if not location:
        return text
    return clean_text(f"{location} {text}")


def build_search_query_from_context(
    user_text: str,
    *,
    messages: list[dict[str, Any]] | None = None,
    memory_summary: str = "",
    has_memory_scope: bool = False,
) -> str:
    text = clean_text(strip_omnivoice_tags(user_text))
    context_messages = list(messages or [])
    contextual = resolve_contextual_search_query(text, messages=context_messages)
    if contextual and contextual != text:
        return contextual
    weather_contextual = enrich_weather_search_query_from_context(
        text,
        messages=context_messages,
        memory_summary=memory_summary,
    )
    if weather_contextual and weather_contextual != text:
        return weather_contextual
    if len(text) >= 8:
        return text
    if not has_memory_scope:
        return text
    if memory_summary:
        return clean_text(f"{text} {memory_summary}")
    return text


__all__ = [
    "build_search_query_from_context",
    "enrich_weather_search_query_from_context",
    "recent_user_text_candidates",
    "resolve_contextual_search_query",
]
