"""Public Voyager package API.

Keep the runtime entry point lazy so dependency-light submodules can be used
without importing the Minecraft bridge and its optional runtime dependencies.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any


__all__ = ["Voyager"]

if TYPE_CHECKING:
    from .voyager import Voyager


def __getattr__(name: str) -> Any:
    if name != "Voyager":
        raise AttributeError(f"module {__name__!r} has no attribute {name!r}")

    from .voyager import Voyager

    globals()[name] = Voyager
    return Voyager


def __dir__() -> list[str]:
    return sorted(set(globals()) | set(__all__))
