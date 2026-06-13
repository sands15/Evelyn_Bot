from __future__ import annotations

import re
from typing import Any

from .text import clean_text


CONTROL_PAGE_UI_PANELS: dict[str, str] = {
    "runtime": "Runtime",
    "diagnostics": "Diagnostics",
    "avatar": "Avatar",
    "chat": "Chat",
    "memory": "Memory",
}

CONTROL_PAGE_UI_PANEL_ALIASES: dict[str, str] = {
    "status": "runtime",
    "state": "runtime",
    "diag": "diagnostics",
    "diagnostic": "diagnostics",
    "logs": "diagnostics",
    "model": "avatar",
    "center": "avatar",
    "main": "avatar",
    "evelyn": "avatar",
    "control": "chat",
    "commands": "chat",
    "command": "chat",
    "memory": "memory",
    "mem": "memory",
    "obsidian": "memory",
}


def compact_text(value: str) -> str:
    return re.sub(r"\s+", "", clean_text(value).lower())


def compact_has_any(compact: str, keys: tuple[str, ...]) -> bool:
    return any(key in compact for key in keys)


def normalize_control_page_ui_panel(value: str | None) -> str | None:
    key = clean_text(str(value or "")).lower().strip()
    if not key:
        return None
    if key in CONTROL_PAGE_UI_PANELS:
        return key
    return CONTROL_PAGE_UI_PANEL_ALIASES.get(key)


def memory_panel_reply(action: str) -> str:
    if action == "open":
        return "메모리 패널을 열어둘게."
    if action == "close":
        return "메모리 패널은 숨겨둘게."
    return "메모리 패널을 열거나 숨길게."


def detect_memory_panel_action(text: str) -> str | None:
    normalized = clean_text(text).lower().strip()
    if not normalized:
        return None
    if normalized in {"/memory", "/mem", "memory panel"}:
        return "toggle"
    compact = compact_text(normalized)
    references_memory_panel = (
        "메모리" in normalized
        or "memory" in normalized
        or ("패널" in normalized and compact_has_any(compact, ("열어", "닫아", "보여", "띄워", "open", "close", "show")))
    )
    if not references_memory_panel:
        return None
    if compact_has_any(compact, ("열어", "띄워", "보여", "켜", "open", "show")):
        return "open"
    if compact_has_any(compact, ("닫아", "닫어", "숨겨", "꺼", "close", "hide")):
        return "close"
    if compact_has_any(compact, ("토글", "전환", "toggle")):
        return "toggle"
    return None


def build_control_page_panel_state_payload(
    commands: list[dict[str, Any]],
    *,
    revision: int,
) -> dict[str, Any]:
    return {
        "revision": revision,
        "commands": [dict(command) for command in commands[-40:]],
        "panels": [
            {"id": panel_id, "label": label}
            for panel_id, label in CONTROL_PAGE_UI_PANELS.items()
        ],
    }


def build_fast_control_default_commands() -> list[dict[str, str]]:
    return [
        {"command": "/help", "template": "/help", "summary": "Show available commands", "visibility": "always", "group": "base"},
        {"command": "/status", "template": "/status", "summary": "Show Evelyn runtime status", "visibility": "always", "group": "base"},
        {"command": "/memory", "template": "/memory", "summary": "Open or toggle the Control-Page memory panel", "visibility": "always", "group": "page"},
        {"command": "/voice status", "template": "/voice status", "summary": "Show Windows local I/O bridge status", "visibility": "always", "group": "voice"},
        {"command": "/restart", "template": "/restart", "summary": "Request local Evelyn runtime restart", "visibility": "always", "group": "system"},
        {"command": "/shutdown", "template": "/shutdown", "summary": "Request local Evelyn runtime shutdown", "visibility": "always", "group": "system"},
    ]


def local_restart_requested_reply() -> str:
    return "로컬 이블린 재시작 요청을 보냈어. Windows bridge가 정리 후 다시 시작할 거야."


def local_shutdown_requested_reply() -> str:
    return "응, 로컬 이블린 종료 요청을 보냈어. Windows bridge가 정리 스크립트를 실행할 거야."
