from __future__ import annotations

import asyncio
import hashlib
import json
import os
from pathlib import Path
import re
import subprocess
import time
from typing import Any, Callable

from .paths import get_runtime_root


WINDOWS_ACCESSIBILITY_RESULT_SCHEMA = "windows_accessibility.result.v1"
WINDOWS_ACCESSIBILITY_OBSERVATION_SCHEMA = (
    "windows_accessibility.observation.v1"
)
WINDOWS_ACCESSIBILITY_MAX_RESPONSE_BYTES = 131072
WINDOWS_ACCESSIBILITY_MAX_ELEMENTS = 120
WINDOWS_ACCESSIBILITY_MAX_AGE_SEC = 5.0
WINDOWS_ACCESSIBILITY_RESULT_KEYS = frozenset(
    {
        "schema",
        "ok",
        "errorCode",
        "capturedAt",
        "available",
        "windowTitle",
        "windowClass",
        "truncated",
        "elements",
    }
)
WINDOWS_ACCESSIBILITY_ELEMENT_KEYS = frozenset(
    {
        "runtimeId",
        "name",
        "automationId",
        "controlType",
        "isEnabled",
        "bounds",
    }
)
WINDOWS_ACCESSIBILITY_BOUND_KEYS = frozenset(
    {"x", "y", "width", "height"}
)
WINDOWS_ACCESSIBILITY_ALLOWED_CONTROL_TYPES = frozenset(
    {
        "Window",
        "TitleBar",
        "Button",
        "MenuBar",
        "Menu",
        "MenuItem",
        "ToolBar",
        "Tab",
        "TabItem",
        "Text",
        "Hyperlink",
        "CheckBox",
        "RadioButton",
        "ComboBox",
        "List",
        "ListItem",
        "Tree",
        "TreeItem",
        "DataGrid",
        "DataItem",
        "Header",
        "HeaderItem",
        "StatusBar",
    }
)


def _clean(value: Any, *, max_chars: int) -> str:
    return re.sub(r"[\x00-\x1f\x7f]+", " ", str(value or "")).strip()[
        :max_chars
    ]


def _normalize_window_value(value: Any) -> str:
    return re.sub(r"\s+", " ", _clean(value, max_chars=240)).casefold()


def accessibility_window_matches_foreground(
    observation: dict[str, Any],
    foreground: dict[str, Any],
) -> bool:
    compared = False
    for observation_key, foreground_key in (
        ("windowTitle", "title"),
        ("windowClass", "className"),
    ):
        observed = _normalize_window_value(observation.get(observation_key))
        expected = _normalize_window_value(foreground.get(foreground_key))
        if not observed or not expected:
            continue
        compared = True
        if observed != expected:
            return False
    return compared


def accessibility_supports_request(
    user_text: str,
    observation: dict[str, Any],
) -> bool:
    elements = [
        item
        for item in observation.get("elements") or []
        if isinstance(item, dict) and _clean(item.get("name"), max_chars=180)
    ]
    named_types = {
        _clean(item.get("controlType"), max_chars=40)
        for item in elements
    }
    text = _normalize_window_value(user_text)
    required_groups: list[set[str]] = []
    if any(marker in text for marker in ("button", "버튼")):
        required_groups.append({"Button"})
    if any(marker in text for marker in ("menu", "메뉴")):
        required_groups.append({"Menu", "MenuBar", "MenuItem"})
    if any(marker in text for marker in ("tab", "탭")):
        required_groups.append({"Tab", "TabItem"})
    if any(marker in text for marker in ("checkbox", "체크박스")):
        required_groups.append({"CheckBox"})
    if any(marker in text for marker in ("radio", "라디오")):
        required_groups.append({"RadioButton"})
    if any(marker in text for marker in ("title", "heading", "제목", "헤더")):
        has_title = bool(
            _clean(observation.get("windowTitle"), max_chars=240)
        ) or bool(named_types & {"TitleBar", "Header", "HeaderItem", "Text"})
        if not has_title:
            return False
    if any(not (named_types & group) for group in required_groups):
        return False
    return bool(
        _clean(observation.get("windowTitle"), max_chars=240)
        or elements
    )


def _element_id(
    *,
    window_title: str,
    window_class: str,
    runtime_id: str,
    control_type: str,
    automation_id: str,
    name: str,
) -> str:
    material = "\x1f".join(
        (
            window_title,
            window_class,
            runtime_id,
            control_type,
            automation_id,
            name,
        )
    )
    return hashlib.sha256(material.encode("utf-8")).hexdigest()[:20]


def _format_accessibility_text(
    *,
    window_title: str,
    elements: list[dict[str, Any]],
    max_chars: int = 4000,
) -> str:
    lines: list[str] = []
    if window_title:
        lines.append(f"Window: {window_title}")
    current_chars = len(lines[0]) if lines else 0
    for item in elements:
        name = item["name"]
        if not name:
            continue
        line = f"{item['controlType']}: {name}"
        if item["automationId"]:
            line += f" [automationId={item['automationId']}]"
        line += f" [elementId={item['elementId']}]"
        bounds = item["bounds"]
        if bounds["width"] > 0 and bounds["height"] > 0:
            line += (
                " [bounds="
                f"{bounds['x']},{bounds['y']},"
                f"{bounds['width']},{bounds['height']}]"
            )
        projected = current_chars + (1 if lines else 0) + len(line)
        if projected > max_chars:
            break
        lines.append(line)
        current_chars = projected
    return "\n".join(lines)


class WindowsAccessibility:
    """Read a bounded foreground UIA tree through one fixed PowerShell script."""

    def __init__(
        self,
        *,
        script_path: Path | None = None,
        powershell_path: Path | None = None,
        max_elements: int = WINDOWS_ACCESSIBILITY_MAX_ELEMENTS,
        timeout_sec: float = 8.0,
        now: Callable[[], float] = time.time,
        run_process: Callable[..., Any] = subprocess.run,
    ) -> None:
        self.script_path = Path(
            script_path
            or get_runtime_root()
            / "launchers"
            / "invoke_windows_accessibility.ps1"
        )
        self.powershell_path = Path(
            powershell_path
            or Path(
                os.environ.get("SystemRoot", r"C:\Windows"),
                "System32",
                "WindowsPowerShell",
                "v1.0",
                "powershell.exe",
            )
        )
        self.max_elements = min(
            WINDOWS_ACCESSIBILITY_MAX_ELEMENTS,
            max(1, int(max_elements)),
        )
        self.timeout_sec = max(1.0, float(timeout_sec))
        self.now = now
        self.run_process = run_process

    def read_sync(self) -> dict[str, Any]:
        if os.name != "nt":
            raise RuntimeError("windows_accessibility_requires_windows")
        if not self.script_path.is_file() or not self.powershell_path.is_file():
            raise RuntimeError("windows_accessibility_runtime_unavailable")
        completed = self.run_process(
            [
                str(self.powershell_path),
                "-NoLogo",
                "-NoProfile",
                "-NonInteractive",
                "-ExecutionPolicy",
                "Bypass",
                "-File",
                str(self.script_path),
                "-MaxElements",
                str(self.max_elements),
            ],
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=self.timeout_sec,
            check=False,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
        )
        stdout = str(completed.stdout or "")
        if len(stdout.encode("utf-8", errors="replace")) > (
            WINDOWS_ACCESSIBILITY_MAX_RESPONSE_BYTES
        ):
            raise RuntimeError("windows_accessibility_response_too_large")
        lines = [line.strip() for line in stdout.splitlines() if line.strip()]
        if not lines:
            raise RuntimeError("windows_accessibility_empty_response")
        try:
            payload = json.loads(lines[-1])
        except json.JSONDecodeError as exc:
            raise RuntimeError(
                "windows_accessibility_invalid_response"
            ) from exc
        if (
            int(completed.returncode or 0) != 0
            or not isinstance(payload, dict)
            or set(payload) != WINDOWS_ACCESSIBILITY_RESULT_KEYS
            or payload.get("schema") != WINDOWS_ACCESSIBILITY_RESULT_SCHEMA
            or payload.get("ok") is not True
        ):
            raise RuntimeError("windows_accessibility_failed")
        captured_at = payload.get("capturedAt")
        now_value = self.now()
        if (
            isinstance(captured_at, bool)
            or not isinstance(captured_at, (int, float))
            or float(captured_at) > now_value + 2.0
            or now_value - float(captured_at)
            > WINDOWS_ACCESSIBILITY_MAX_AGE_SEC
        ):
            raise RuntimeError("windows_accessibility_stale")
        if (
            type(payload.get("available")) is not bool
            or type(payload.get("truncated")) is not bool
            or not isinstance(payload.get("elements"), list)
        ):
            raise RuntimeError("windows_accessibility_invalid_response")

        window_title = _clean(payload.get("windowTitle"), max_chars=240)
        window_class = _clean(payload.get("windowClass"), max_chars=80)
        elements: list[dict[str, Any]] = []
        for raw in payload["elements"][: self.max_elements]:
            if (
                not isinstance(raw, dict)
                or set(raw) != WINDOWS_ACCESSIBILITY_ELEMENT_KEYS
                or not isinstance(raw.get("bounds"), dict)
                or set(raw["bounds"]) != WINDOWS_ACCESSIBILITY_BOUND_KEYS
                or type(raw.get("isEnabled")) is not bool
            ):
                raise RuntimeError(
                    "windows_accessibility_invalid_element"
                )
            control_type = _clean(raw.get("controlType"), max_chars=40)
            if control_type not in WINDOWS_ACCESSIBILITY_ALLOWED_CONTROL_TYPES:
                raise RuntimeError(
                    "windows_accessibility_control_type_not_allowed"
                )
            bounds: dict[str, float] = {}
            for key in ("x", "y", "width", "height"):
                value = raw["bounds"].get(key)
                if isinstance(value, bool) or not isinstance(
                    value,
                    (int, float),
                ):
                    raise RuntimeError(
                        "windows_accessibility_invalid_bounds"
                    )
                bounds[key] = round(float(value), 1)
            runtime_id = _clean(raw.get("runtimeId"), max_chars=160)
            name = _clean(raw.get("name"), max_chars=180)
            automation_id = _clean(
                raw.get("automationId"),
                max_chars=120,
            )
            elements.append(
                {
                    "elementId": _element_id(
                        window_title=window_title,
                        window_class=window_class,
                        runtime_id=runtime_id,
                        control_type=control_type,
                        automation_id=automation_id,
                        name=name,
                    ),
                    "name": name,
                    "automationId": automation_id,
                    "controlType": control_type,
                    "isEnabled": raw["isEnabled"],
                    "bounds": bounds,
                }
            )
        available = bool(payload["available"])
        if not available and (window_title or window_class or elements):
            raise RuntimeError(
                "windows_accessibility_contradictory_response"
            )
        text = _format_accessibility_text(
            window_title=window_title,
            elements=elements,
        )
        return {
            "schema": WINDOWS_ACCESSIBILITY_OBSERVATION_SCHEMA,
            "attempted": True,
            "available": available,
            "capturedAt": float(captured_at),
            "windowTitle": window_title,
            "windowClass": window_class,
            "truncated": bool(payload["truncated"]),
            "elements": elements,
            "text": text,
        }

    async def read(self) -> dict[str, Any]:
        return await asyncio.to_thread(self.read_sync)


__all__ = [
    "WINDOWS_ACCESSIBILITY_ALLOWED_CONTROL_TYPES",
    "WINDOWS_ACCESSIBILITY_MAX_ELEMENTS",
    "WINDOWS_ACCESSIBILITY_OBSERVATION_SCHEMA",
    "WINDOWS_ACCESSIBILITY_RESULT_SCHEMA",
    "WindowsAccessibility",
    "accessibility_supports_request",
    "accessibility_window_matches_foreground",
]
