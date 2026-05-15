from __future__ import annotations

import inspect
from collections.abc import Iterable
from types import ModuleType
from typing import Any

from .base import RegisteredSkill, SkillContext, SkillExecute, SkillSpec


class SkillRegistry:
    def __init__(self) -> None:
        self._skills: dict[str, RegisteredSkill] = {}

    def register(self, spec: SkillSpec, execute: SkillExecute, *, origin: str = "internal", replace: bool = False) -> RegisteredSkill:
        existing = self._skills.get(spec.name)
        if existing is not None and not replace:
            raise ValueError(f"Skill name already registered: {spec.name} (origin={existing.origin})")
        registered = RegisteredSkill(spec=spec, execute=execute, origin=origin)
        self._skills[spec.name] = registered
        return registered

    def register_module(self, module: ModuleType, *, origin: str | None = None, replace: bool = False) -> RegisteredSkill:
        name = str(getattr(module, "name", "") or "").strip()
        if not name:
            raise ValueError(f"Skill module {getattr(module, '__name__', '<unknown>')} is missing 'name'.")

        execute = getattr(module, "execute", None)
        if not callable(execute):
            raise ValueError(f"Skill module {module.__name__} must define callable execute(context).")

        routes = tuple(str(item) for item in (getattr(module, "routes", ()) or ()))
        sources = tuple(str(item) for item in (getattr(module, "sources", ()) or ()))
        description = str(getattr(module, "description", "") or "")
        spec = SkillSpec(
            name=name,
            routes=routes,
            sources=sources,
            description=description,
        )
        return self.register(spec, execute, origin=origin or module.__name__, replace=replace)

    def get(self, name: str) -> RegisteredSkill | None:
        return self._skills.get(name)

    def list(self) -> list[RegisteredSkill]:
        return list(self._skills.values())

    def find_by_route(self, route: str, *, source: str | None = None) -> list[RegisteredSkill]:
        return [skill for skill in self._skills.values() if skill.supports(route=route, source=source)]

    def extend(self, skills: Iterable[RegisteredSkill]) -> None:
        for skill in skills:
            self._skills[skill.name] = skill

    async def execute(self, name: str, context: SkillContext) -> Any:
        skill = self.get(name)
        if skill is None:
            raise KeyError(f"Unknown skill: {name}")
        result = skill.execute(context)
        if inspect.isawaitable(result):
            return await result
        return result


skill_registry = SkillRegistry()
