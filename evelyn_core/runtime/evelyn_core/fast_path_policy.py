from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any, Callable


@dataclass(frozen=True)
class FastPathPolicyRuntimeDeps:
    clean_text: Callable[[str], str]
    normalize_voice_text: Callable[[str], str]
    should_force_search_query: Callable[[str], bool]
    control_page_source_aliases: tuple[str, ...]
    control_page_light_request_max_chars: int
    fast_path_search_markers: tuple[str, ...]
    fast_path_search_route_markers: tuple[str, ...]
    fast_path_negated_search_markers: tuple[str, ...]
    fast_path_directive_markers: tuple[str, ...]
    fast_path_continue_markers: tuple[str, ...]
    fast_path_deep_route_markers: tuple[str, ...]


def is_control_page_source_from_runtime(source: str, *, deps: FastPathPolicyRuntimeDeps) -> bool:
    return deps.clean_text(source).lower() in set(deps.control_page_source_aliases)


def deep_route_marker_count_from_runtime(
    text: str,
    *,
    ignore_search_markers: bool = False,
    deps: FastPathPolicyRuntimeDeps,
) -> int:
    cleaned = deps.clean_text(text)
    ignored_markers = set(deps.fast_path_search_route_markers) if ignore_search_markers else set()
    return sum(1 for marker in deps.fast_path_deep_route_markers if marker not in ignored_markers and marker in cleaned)


def has_negated_search_marker_from_runtime(text: str, *, deps: FastPathPolicyRuntimeDeps) -> bool:
    cleaned = deps.clean_text(text).lower()
    compact = re.sub(r"\s+", "", cleaned)
    return any(marker in cleaned for marker in deps.fast_path_negated_search_markers) or any(
        marker.replace(" ", "") in compact for marker in deps.fast_path_negated_search_markers
    )


def needs_search_or_deep_routing_from_runtime(
    text: str,
    *,
    source: str = "text",
    deps: FastPathPolicyRuntimeDeps,
) -> bool:
    cleaned = deps.clean_text(text)
    if not cleaned:
        return False
    negated_search = has_negated_search_marker_from_runtime(cleaned, deps=deps)
    if not negated_search and deps.should_force_search_query(cleaned):
        return True
    marker_hits = deep_route_marker_count_from_runtime(cleaned, ignore_search_markers=negated_search, deps=deps)
    if not negated_search and any(marker in cleaned for marker in deps.fast_path_search_markers):
        return True
    if is_control_page_source_from_runtime(source, deps=deps) and marker_hits == 0 and len(cleaned) <= deps.control_page_light_request_max_chars:
        return False
    if marker_hits >= 2:
        return True
    if len(cleaned) >= 72:
        return True
    return False


def is_simple_directive_from_runtime(text: str, *, source: str = "text", deps: FastPathPolicyRuntimeDeps) -> bool:
    cleaned = deps.clean_text(text)
    if not cleaned:
        return False
    if needs_search_or_deep_routing_from_runtime(cleaned, source=source, deps=deps):
        return False
    if any(marker in cleaned for marker in deps.fast_path_directive_markers):
        return True
    return len(cleaned) <= 24 and "?" not in cleaned


def is_obvious_continue_from_runtime(
    text: str,
    source: str,
    *,
    room_state: dict | None = None,
    deps: FastPathPolicyRuntimeDeps,
) -> bool:
    cleaned = deps.normalize_voice_text(text) if source == "voice" else deps.clean_text(text)
    if not cleaned:
        return True
    state = room_state or {}
    if not (state.get("reply_in_progress") or state.get("awaiting_user_reply") or state.get("owner_user_id")):
        return False
    if len(cleaned) > 12 and len(cleaned.split()) > 3:
        return False
    return any(cleaned == marker or cleaned.startswith(marker) for marker in deps.fast_path_continue_markers)


def fast_path_policy_from_runtime(
    text: str,
    source: str,
    room_state: dict | None = None,
    *,
    deps: FastPathPolicyRuntimeDeps,
) -> dict | None:
    cleaned = deps.clean_text(text)
    if not cleaned:
        return {"route": "main_direct", "action": "wait", "reason_brief": "empty_input"}
    if is_control_page_source_from_runtime(source, deps=deps):
        return None
    if is_obvious_continue_from_runtime(cleaned, source, room_state=room_state, deps=deps):
        return {"route": "main_direct", "action": "wait", "reason_brief": "obvious_continue"}
    if deps.should_force_search_query(cleaned):
        return {"route": "search_executor", "action": "search_then_answer", "reason_brief": "search_trigger"}
    if is_simple_directive_from_runtime(cleaned, source=source, deps=deps):
        return {"route": "main_direct", "action": "answer", "reason_brief": "simple_directive"}
    if not needs_search_or_deep_routing_from_runtime(cleaned, source=source, deps=deps):
        return {"route": "main_direct", "action": "answer", "reason_brief": "light_request"}
    return None


def context_policy_for_fast_path_policy_from_runtime(
    policy: dict | None,
    *,
    source: str,
    deps: FastPathPolicyRuntimeDeps,
) -> dict[str, Any]:
    action = deps.clean_text(str((policy or {}).get("action") or "answer"))
    route = deps.clean_text(str((policy or {}).get("route") or "main_direct"))
    needs_search = action == "search_then_answer" or route == "search_executor"
    return {
        "intent": "question" if needs_search else "chat",
        "needs_main_llm": action == "answer",
        "needs_memory": False,
        "needs_runtime_state": False,
        "needs_minecraft_state": False,
        "needs_vision": False,
        "needs_skill_graph": False,
        "needs_long_context": False,
        "needs_search": needs_search,
        "needs_tts": True,
        "priority": "accuracy" if needs_search else "latency",
        "context_focus": [],
        "response_mode": "short" if source == "voice" else "normal",
    }


__all__ = [
    "FastPathPolicyRuntimeDeps",
    "is_control_page_source_from_runtime",
    "deep_route_marker_count_from_runtime",
    "has_negated_search_marker_from_runtime",
    "needs_search_or_deep_routing_from_runtime",
    "is_simple_directive_from_runtime",
    "is_obvious_continue_from_runtime",
    "fast_path_policy_from_runtime",
    "context_policy_for_fast_path_policy_from_runtime",
]
