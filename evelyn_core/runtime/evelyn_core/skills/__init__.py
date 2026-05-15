from __future__ import annotations

import os
from pathlib import Path

from .base import RegisteredSkill, SkillContext, SkillResult, SkillSpec
from .loader import load_skill_file, load_skill_module, load_skills_from_directory
from .registry import SkillRegistry, skill_registry
from . import conversation, delivery, routing, search


def _autoload_external_skills() -> None:
    module_list = [item for item in os.getenv("EVELYN_SKILL_MODULES", "").split(os.pathsep) if item]
    for module_name in module_list:
        try:
            load_skill_module(module_name)
        except Exception:
            continue

    path_list = [item for item in os.getenv("EVELYN_SKILL_PATHS", "").split(os.pathsep) if item]
    for raw_path in path_list:
        path = Path(raw_path).expanduser()
        try:
            if path.is_dir():
                load_skills_from_directory(path, recursive=True)
            elif path.is_file():
                load_skill_file(path)
        except Exception:
            continue


_autoload_external_skills()

__all__ = [
    "RegisteredSkill",
    "SkillContext",
    "SkillResult",
    "SkillSpec",
    "SkillRegistry",
    "skill_registry",
    "load_skill_file",
    "load_skill_module",
    "load_skills_from_directory",
    "conversation",
    "delivery",
    "routing",
    "search",
]
