from __future__ import annotations

import re
from typing import Any, Callable

from .text import clean_text

RouteAvailable = Callable[..., bool]


def _route_available(route_available: RouteAvailable | None, route_name: str, *, source: str) -> bool:
    if route_available is None:
        return False
    try:
        return bool(route_available(route_name, source=source))
    except Exception:
        return False


def _text_has_any_marker(text: str, markers: tuple[str, ...]) -> bool:
    lowered = clean_text(text).lower()
    compact = re.sub(r"\s+", "", lowered)
    return any(marker in lowered or marker.replace(" ", "") in compact for marker in markers)


def build_tool_awareness_context(
    user_text: str,
    *,
    source: str = "text",
    route_decision: Any = None,
    route_available: RouteAvailable | None = None,
) -> str:
    text = clean_text(user_text)
    tools: list[str] = []
    search_markers = (
        "search",
        "look up",
        "find",
        "check",
        "weather",
        "news",
        "price",
        "검색",
        "찾아",
        "찾아봐",
        "확인",
        "날씨",
        "뉴스",
        "가격",
        "시세",
        "환율",
        "주가",
        "최신",
    )
    runtime_markers = (
        "runtime",
        "status",
        "voice",
        "model",
        "llm",
        "evelyn",
        "이블린",
        "상태",
        "음성",
        "모델",
        "서버",
        "켜져",
        "오류",
        "에러",
    )
    minecraft_markers = (
        "minecraft",
        "voyager",
        "inventory",
        "마크",
        "마인크래프트",
        "보이저",
        "인벤",
    )

    needs_search = bool(
        route_decision
        and (
            bool(getattr(route_decision, "needs_search", False))
            or clean_text(str(getattr(route_decision, "action", ""))) == "search_then_answer"
        )
    )
    needs_search = needs_search or _text_has_any_marker(text, search_markers)
    if needs_search and _route_available(route_available, "search_executor", source=source):
        tools.append("- search: use for current info, weather, prices, news, web lookup, and explicit find/check/search requests.")

    if _text_has_any_marker(text, minecraft_markers):
        tools.append("- minecraft.status: use runtime Minecraft/Voyager state when a Minecraft status or inventory question is asked.")

    if _text_has_any_marker(text, runtime_markers):
        tools.append("- runtime.status: use live Evelyn runtime/service status when the user asks about Evelyn, voice, model, server, or errors.")

    if not tools and _route_available(route_available, "search_executor", source=source):
        tools.append("- search: available if the user asks for current info, weather, prices, news, or explicit web lookup.")

    if not tools:
        return ""

    return clean_text(
        "TOOL_AWARENESS: Runtime, not memory, is the source of truth for tools. "
        "Available tool shortlist for this turn: "
        + " ".join(tools[:4])
        + " Contract: if a tool is needed, do not give only a promise such as 'I'll look it up' as the final answer. "
        "Route to the tool executor or produce a short preface that the runtime can escalate. "
        "After a tool result exists, answer in the final phase grounded in that result."
    )


__all__ = ["build_tool_awareness_context"]
