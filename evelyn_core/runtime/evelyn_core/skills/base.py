from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Awaitable, Callable, Protocol, runtime_checkable


@dataclass(slots=True)
class SkillContext:
    source: str
    guild_id: int | None = None
    session_key: str | None = None
    room_key: str | None = None
    person_key: str | None = None
    session_memory_key: str | None = None
    debug_text: str | None = None
    metrics: dict[str, Any] | None = None
    extras: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True, slots=True)
class SkillSpec:
    name: str
    routes: tuple[str, ...] = ()
    sources: tuple[str, ...] = ()
    description: str = ""


@dataclass(slots=True)
class SkillResult:
    skill: str
    route: str
    handled: bool = True
    status: str = "ok"
    display_text: str = ""
    answer_text: str = ""
    should_emit: bool = True
    dedupe_key: str | None = None
    executor_used: str | None = None
    followup_route: str | None = None
    followup_payload: dict[str, Any] = field(default_factory=dict)
    followup_delay_ms: int | None = None
    metadata: dict[str, Any] = field(default_factory=dict)
    payload: dict[str, Any] = field(default_factory=dict)


@runtime_checkable
class SkillExecute(Protocol):
    def __call__(self, context: SkillContext) -> Any | Awaitable[Any]: ...


@dataclass(frozen=True, slots=True)
class RegisteredSkill:
    spec: SkillSpec
    execute: SkillExecute
    origin: str = "internal"

    @property
    def name(self) -> str:
        return self.spec.name

    def supports(self, *, route: str | None = None, source: str | None = None) -> bool:
        route_ok = True if route is None else route in self.spec.routes
        source_ok = True if source is None else source in self.spec.sources
        return route_ok and source_ok


def require_callback(extras: dict[str, Any], key: str) -> Callable[..., Any]:
    fn = extras.get(key)
    if not callable(fn):
        raise RuntimeError(f"Missing required callback: {key}")
    return fn


SkillModuleExecute = Callable[[SkillContext], Any | Awaitable[Any]]
