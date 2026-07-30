from __future__ import annotations

import ctypes
import os
import re
from typing import Any


WINDOWS_FOREGROUND_SCHEMA = "windows_foreground.observation.v1"


def _bounded_window_text(value: Any, *, max_chars: int) -> str:
    return re.sub(r"[\x00-\x1f\x7f]+", " ", str(value or "")).strip()[
        :max_chars
    ]


def read_windows_foreground_window(
    *,
    user32: Any | None = None,
) -> dict[str, Any]:
    """Read only the active window title/class; never inspect process paths or argv."""
    if user32 is None:
        if os.name != "nt":
            return {
                "schema": WINDOWS_FOREGROUND_SCHEMA,
                "available": False,
                "title": "",
                "className": "",
            }
        user32 = ctypes.windll.user32
    handle = user32.GetForegroundWindow()
    if not handle:
        return {
            "schema": WINDOWS_FOREGROUND_SCHEMA,
            "available": False,
            "title": "",
            "className": "",
        }
    title_length = max(0, min(2048, int(user32.GetWindowTextLengthW(handle))))
    title_buffer = ctypes.create_unicode_buffer(title_length + 1)
    user32.GetWindowTextW(handle, title_buffer, title_length + 1)
    class_buffer = ctypes.create_unicode_buffer(256)
    user32.GetClassNameW(handle, class_buffer, len(class_buffer))
    title = _bounded_window_text(title_buffer.value, max_chars=240)
    class_name = _bounded_window_text(class_buffer.value, max_chars=80)
    return {
        "schema": WINDOWS_FOREGROUND_SCHEMA,
        "available": bool(title or class_name),
        "title": title,
        "className": class_name,
    }


__all__ = [
    "WINDOWS_FOREGROUND_SCHEMA",
    "read_windows_foreground_window",
]
