from __future__ import annotations

import importlib
import importlib.util
from pathlib import Path
from types import ModuleType

from .registry import SkillRegistry, skill_registry


def load_skill_module(module_name: str, *, registry: SkillRegistry = skill_registry, replace: bool = False):
    module = importlib.import_module(module_name)
    return registry.register_module(module, origin=module_name, replace=replace)


def load_skill_file(path: str | Path, *, registry: SkillRegistry = skill_registry, replace: bool = False):
    file_path = Path(path).expanduser().resolve()
    module_name = f"evelyn_external_skill_{file_path.stem}_{abs(hash(str(file_path)))}"
    spec = importlib.util.spec_from_file_location(module_name, file_path)
    if spec is None or spec.loader is None:
        raise ImportError(f"Cannot create import spec for skill file: {file_path}")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    if not isinstance(module, ModuleType):
        raise ImportError(f"Loaded object is not a module: {file_path}")
    return registry.register_module(module, origin=str(file_path), replace=replace)


def load_skills_from_directory(directory: str | Path, *, registry: SkillRegistry = skill_registry, recursive: bool = False, replace: bool = False):
    base = Path(directory).expanduser().resolve()
    pattern = "**/*.py" if recursive else "*.py"
    loaded = []
    for file_path in sorted(base.glob(pattern)):
        if file_path.name.startswith("_") or file_path.name == "__init__.py":
            continue
        loaded.append(load_skill_file(file_path, registry=registry, replace=replace))
    return loaded
