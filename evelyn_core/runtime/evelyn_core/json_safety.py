from __future__ import annotations

import json
import math
from pathlib import Path
from typing import Any


def safe_json_string(value: Any) -> str:
    text = str(value)
    return text.encode("utf-8", errors="replace").decode("utf-8", errors="replace")


def safe_json_value(value: Any) -> Any:
    if value is None or isinstance(value, bool):
        return value
    if isinstance(value, int):
        return value
    if isinstance(value, float):
        return value if math.isfinite(value) else None
    if isinstance(value, str):
        return safe_json_string(value)
    if isinstance(value, bytes):
        return value.decode("utf-8", errors="replace")
    if isinstance(value, Path):
        return str(value)
    if hasattr(value, "item"):
        try:
            return safe_json_value(value.item())
        except Exception:
            pass
    if isinstance(value, dict):
        return {safe_json_string(key): safe_json_value(item) for key, item in value.items()}
    if isinstance(value, (list, tuple, set)):
        return [safe_json_value(item) for item in value]
    return safe_json_string(value)


def safe_json_dumps(value: Any, **kwargs: Any) -> str:
    return json.dumps(safe_json_value(value), allow_nan=False, **kwargs)
