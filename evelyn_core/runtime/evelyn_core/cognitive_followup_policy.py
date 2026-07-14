from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Callable


@dataclass(frozen=True)
class ShouldForceSearchFollowupRuntimeDeps:
    read_cached_cognitive_state_fn: Callable[..., dict[str, Any] | None]
    apply_ask_gating_fn: Callable[[dict[str, Any] | None], dict[str, Any]]
    clean_text_fn: Callable[[str], str]


def should_force_search_followup_from_runtime(
    guild_id: int | None,
    *,
    room_key: str | None = None,
    person_key: str | None = None,
    session_memory_key: str | None = None,
    source: str,
    deps: ShouldForceSearchFollowupRuntimeDeps,
) -> bool:
    state = deps.read_cached_cognitive_state_fn(
        guild_id,
        room_key=room_key,
        person_key=person_key,
        session_memory_key=session_memory_key,
    )
    if not state:
        return False
    gated = deps.apply_ask_gating_fn(state, source=source)
    return deps.clean_text_fn(str(gated.get("action") or "")) == "search_then_answer"


__all__ = [
    "ShouldForceSearchFollowupRuntimeDeps",
    "should_force_search_followup_from_runtime",
]
