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
        {"command": "/help", "template": "/help", "summary": "사용 가능한 명령 보기", "visibility": "always", "group": "기본"},
        {"command": "/status", "template": "/status", "summary": "이블린 핵심 서비스 상태 보기", "visibility": "always", "group": "기본"},
        {"command": "/remember <fact>", "template": "/remember ", "summary": "현재 요청을 근거로 새 기억 저장", "visibility": "always", "group": "기억"},
        {"command": "/memory", "template": "/memory", "summary": "메모리 패널 열기/숨기기", "visibility": "always", "group": "페이지"},
        {"command": "/obsidian", "template": "/obsidian", "summary": "Obsidian 메모리 저장소 열기", "visibility": "always", "group": "페이지"},
        {"command": "/voice status", "template": "/voice status", "summary": "Windows 음성 브리지 상태 보기", "visibility": "always", "group": "음성"},
        {"command": "/mic status", "template": "/mic status", "summary": "로컬 마이크 상태 보기", "visibility": "always", "group": "음성"},
        {"command": "/mic on", "template": "/mic on", "summary": "로컬 마이크 입력 켜기", "visibility": "always", "group": "음성"},
        {"command": "/mic off", "template": "/mic off", "summary": "로컬 마이크 입력 끄기", "visibility": "always", "group": "음성"},
        {"command": "/minecraft connect", "template": "/minecraft connect", "summary": "Minecraft 서비스 지연 기동 및 접속", "visibility": "always", "group": "Minecraft"},
        {"command": "/minecraft status", "template": "/minecraft status", "summary": "Minecraft 서비스와 접속 상태 보기", "visibility": "always", "group": "Minecraft"},
        {"command": "/inventory", "template": "/inventory", "summary": "현재 Minecraft 인벤토리 보기", "visibility": "always", "group": "Minecraft"},
        {"command": "/voyager stats", "template": "/voyager stats", "summary": "Minecraft 자율 작업 진행 상태 보기", "visibility": "always", "group": "Minecraft"},
        {"command": "/minecraft disconnect", "template": "/minecraft disconnect", "summary": "Minecraft 에이전트 연결 중지", "visibility": "always", "group": "Minecraft"},
        {"command": "/minecraft goal <goal>", "template": "/minecraft goal ", "summary": "Minecraft 목표 설정 또는 변경", "visibility": "always", "group": "Minecraft"},
        {"command": "/autonomy status", "template": "/autonomy status", "summary": "Minecraft 자율 작업 상태 보기", "visibility": "always", "group": "Minecraft"},
        {"command": "/repair preview", "template": "/repair preview", "summary": "런타임 복구 계획을 실행 없이 확인", "visibility": "always", "group": "시스템"},
        {"command": "/repair start", "template": "/repair start", "summary": "확인된 런타임 복구 계획 실행", "visibility": "always", "group": "시스템"},
        {"command": "/restart", "template": "/restart", "summary": "로컬 이블린 재시작 요청", "visibility": "always", "group": "시스템"},
        {"command": "/shutdown", "template": "/shutdown", "summary": "로컬 이블린 종료 요청 (Shut down Evelyn runtime)", "visibility": "always", "group": "시스템"},
    ]


def build_fast_control_help_reply() -> str:
    commands = build_fast_control_default_commands()
    lines = ["사용 가능한 명령"]
    current_group = ""
    for item in commands:
        group = item["group"]
        if group != current_group:
            current_group = group
            lines.extend(("", group))
        lines.append(f"- {item['command']} — {item['summary']}")
    lines.extend(("", "자연어로 시간 확인, 웹 검색, 기억 검색, 문제 조사도 요청할 수 있어."))
    return "\n".join(lines)


def local_restart_requested_reply() -> str:
    return "로컬 이블린 재시작 요청을 보냈어. Windows bridge가 정리 후 다시 시작할 거야."


def local_shutdown_requested_reply() -> str:
    return "응, 로컬 이블린 종료 요청을 보냈어. Windows bridge가 정리 스크립트를 실행할 거야."
