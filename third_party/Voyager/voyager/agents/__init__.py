"""Public agent API with lazy runtime imports."""

from __future__ import annotations

from importlib import import_module
from typing import TYPE_CHECKING, Any


__all__ = ["ActionAgent", "CriticAgent", "CurriculumAgent", "SkillManager"]

_EXPORT_MODULES = {
    "ActionAgent": ".action",
    "CriticAgent": ".critic",
    "CurriculumAgent": ".curriculum",
    "SkillManager": ".skill",
}

if TYPE_CHECKING:
    from .action import ActionAgent
    from .critic import CriticAgent
    from .curriculum import CurriculumAgent
    from .skill import SkillManager


def __getattr__(name: str) -> Any:
    module_name = _EXPORT_MODULES.get(name)
    if module_name is None:
        raise AttributeError(f"module {__name__!r} has no attribute {name!r}")

    value = getattr(import_module(module_name, __name__), name)
    globals()[name] = value
    return value


def __dir__() -> list[str]:
    return sorted(set(globals()) | set(__all__))
