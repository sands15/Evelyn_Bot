from __future__ import annotations

import os
from pathlib import Path


def _candidate_roots(start: Path):
    yield start
    yield from start.parents


def get_repo_root() -> Path:
    env_root = os.getenv("EVELYN_PROJECT_ROOT")
    if env_root:
        try:
            return Path(env_root).resolve()
        except Exception:
            pass

    current = Path(__file__).resolve()
    for candidate in _candidate_roots(current.parent):
        if (candidate / "main.py").exists() and (candidate / "evelyn_core").exists():
            return candidate
    return current.parents[3] if len(current.parents) >= 4 else current.parent


def get_evelyn_core_root() -> Path:
    env_root = os.getenv("EVELYN_CORE_ROOT")
    if env_root:
        try:
            return Path(env_root).resolve()
        except Exception:
            pass

    repo_root = get_repo_root()
    return repo_root / "evelyn_core"


def get_runtime_root() -> Path:
    env_root = os.getenv("EVELYN_CORE_RUNTIME")
    if env_root:
        try:
            return Path(env_root).resolve()
        except Exception:
            pass

    current = Path(__file__).resolve()
    if current.parent.name == "evelyn_core" and current.parent.parent.name == "runtime":
        return current.parent.parent
    return get_evelyn_core_root() / "runtime"


def get_runtime_artifacts_root() -> Path:
    env_root = os.getenv("EVELYN_RUNTIME_ARTIFACTS_DIR") or os.getenv("RUNTIME_ARTIFACTS_DIR")
    if env_root:
        try:
            return Path(env_root).resolve()
        except Exception:
            pass
    return get_repo_root() / "runtime_artifacts"
