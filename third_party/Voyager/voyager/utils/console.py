from __future__ import annotations

import builtins
from typing import Any


def safe_text(value: Any) -> str:
    try:
        text = str(value)
    except Exception:
        text = repr(value)
    return text.encode("utf-8", errors="replace").decode(
        "utf-8", errors="replace"
    )


def safe_print(*args: Any, **kwargs: Any) -> None:
    sep = kwargs.pop("sep", " ")
    end = kwargs.pop("end", "\n")
    text = sep.join(safe_text(arg) for arg in args)
    builtins.print(text, end=end, **kwargs)
