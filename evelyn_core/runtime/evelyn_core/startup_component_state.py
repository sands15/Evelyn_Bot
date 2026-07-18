from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Callable, MutableMapping

from .text import clean_text


STARTUP_BOOT_STEPS: tuple[tuple[str, str], ...] = (
    ("main_service", "Main LLM"),
    ("router_service", "Router LLM"),
    ("sub_service", "Sub LLM"),
    ("tts_service", "TTS service"),
    ("discord_gateway", "Discord gateway"),
    ("control_api", "Bot API"),
    ("opus", "Opus"),
    ("stt", "STT"),
    ("main_warmup", "Main LLM warmup"),
    ("tts_warmup", "TTS warmup"),
)


@dataclass(frozen=True)
class StartupComponentRuntimeDeps:
    startup_component_state: MutableMapping[str, dict[str, Any]]
    now: Callable[[], float]


def mark_startup_component_state(
    startup_component_state: MutableMapping[str, dict[str, Any]],
    key: str,
    status: str,
    detail: str = "",
    *,
    now: Callable[[], float],
) -> None:
    startup_component_state[key] = {
        "status": clean_text(status) or "pending",
        "detail": clean_text(detail),
        "updatedAt": now(),
    }


def startup_component_done_from_state(
    startup_component_state: MutableMapping[str, dict[str, Any]],
    key: str,
) -> bool:
    return (startup_component_state.get(key) or {}).get("status") == "done"


def mark_startup_component_from_runtime(
    key: str,
    status: str,
    detail: str = "",
    *,
    deps: StartupComponentRuntimeDeps,
) -> None:
    mark_startup_component_state(
        deps.startup_component_state,
        key,
        status,
        detail,
        now=deps.now,
    )


def startup_component_done_from_runtime(
    key: str,
    *,
    deps: StartupComponentRuntimeDeps,
) -> bool:
    return startup_component_done_from_state(deps.startup_component_state, key)


__all__ = [
    "StartupComponentRuntimeDeps",
    "STARTUP_BOOT_STEPS",
    "mark_startup_component_from_runtime",
    "mark_startup_component_state",
    "startup_component_done_from_runtime",
    "startup_component_done_from_state",
]
