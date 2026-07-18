from __future__ import annotations

from typing import Any, Callable, Iterable


class ConsoleOutputFilter:
    def __init__(
        self,
        *,
        enabled: bool,
        output: Callable[..., Any],
        allowed_prefixes: Iterable[str],
    ) -> None:
        self.enabled = bool(enabled)
        self.output = output
        self.allowed_prefixes = tuple(allowed_prefixes)

    def __call__(self, *args: Any, **kwargs: Any) -> Any:
        if not self.enabled:
            return self.output(*args, **kwargs)
        text = " ".join(str(arg) for arg in args).lstrip()
        if text.startswith(self.allowed_prefixes):
            return self.output(*args, **kwargs)
        return None


__all__ = ["ConsoleOutputFilter"]
