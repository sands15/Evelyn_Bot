from __future__ import annotations

import json
import re
from dataclasses import dataclass
from typing import Any

from .text import clean_text
from .voice_barge_in_continuity import VOICE_BARGE_IN_RESET_CONFIRM_KEYWORD


CONTROL_PAGE_COMMANDS: list[dict[str, str]] = [
    {"command": "/help", "template": "/help", "summary": "명령어 목록 보기", "visibility": "always", "group": "기본"},
    {"command": "/status", "template": "/status", "summary": "Evelyn, 음성, 모델, Minecraft 상태 요약", "visibility": "always", "group": "기본"},
    {"command": "/memory", "template": "/memory", "summary": "메모리 패널 열기/숨기기", "visibility": "always", "group": "페이지"},
    {"command": "/obsidian", "template": "/obsidian", "summary": "메모리 패널 열기/숨기기", "visibility": "always", "group": "페이지"},
    {"command": "/voice status", "template": "/voice status", "summary": "음성 입력, STT, TTS 파이프라인 상태 보기", "visibility": "always", "group": "음성"},
    {"command": "/voice continuity", "template": "/voice continuity", "summary": "음성 바리인 연속성 요약", "visibility": "always", "group": "음성"},
    {"command": "/voice continuity reset", "template": "/voice continuity reset", "summary": "음성 바리인 연속성 카운트 리셋", "visibility": "always", "group": "음성"},
    {"command": "/voice continuity reset confirm", "template": "/voice continuity reset confirm", "summary": "바리인 연속성 리셋 확인", "visibility": "always", "group": "음성"},
    {"command": "/voice reconnect", "template": "/voice reconnect", "summary": "최근 저장된 음성 채널에 다시 연결", "visibility": "always", "group": "음성"},
    {"command": "/voice input auto", "template": "/voice input auto", "summary": "로컬 마이크와 Discord 입력 자동 전환", "visibility": "always", "group": "음성"},
    {"command": "/voice input local", "template": "/voice input local", "summary": "로컬 마이크 입력 사용", "visibility": "always", "group": "음성"},
    {"command": "/voice input discord", "template": "/voice input discord", "summary": "Discord 음성 입력 사용", "visibility": "always", "group": "음성"},
    {"command": "/minecraft connect", "template": "/minecraft connect", "summary": "Voyager Minecraft 모드 시작", "visibility": "minecraft-idle", "group": "Minecraft"},
    {"command": "/minecraft status", "template": "/minecraft status", "summary": "Minecraft 연결과 현재 task 상태 보기", "visibility": "minecraft-active", "group": "Minecraft"},
    {"command": "/inventory", "template": "/inventory", "summary": "현재 Minecraft 인벤토리 요약 보기", "visibility": "minecraft-active", "group": "Minecraft"},
    {"command": "/voyager stats", "template": "/voyager stats", "summary": "Voyager 진행 상태와 평가 지표 보기", "visibility": "minecraft-active", "group": "Minecraft"},
    {"command": "/minecraft disconnect", "template": "/minecraft disconnect", "summary": "Voyager Minecraft 모드 중지", "visibility": "minecraft-active", "group": "Minecraft"},
    {"command": "/minecraft goal <goal>", "template": "/minecraft goal ", "summary": "Minecraft 목표 변경", "visibility": "minecraft-active", "group": "Minecraft"},
    {"command": "/autonomy status", "template": "/autonomy status", "summary": "Evelyn 자율 행동 엔진 상태 보기", "visibility": "always", "group": "자율 행동"},
    {"command": "/restart", "template": "/restart", "summary": "Evelyn bot process 재시작", "visibility": "always", "group": "시스템"},
    {"command": "/shutdown", "template": "/shutdown", "summary": "Evelyn runtime 종료 (Shut down Evelyn runtime)", "visibility": "always", "group": "시스템"},
]


@dataclass(frozen=True)
class ControlPageToolSpec:
    name: str
    risk: str
    description: str
    router_enabled: bool = True
    requires_guild: bool = False


CONTROL_PAGE_TOOL_SPECS: dict[str, ControlPageToolSpec] = {
    "control_page.help": ControlPageToolSpec("control_page.help", "low", "Show the control page command list.", router_enabled=False),
    "runtime.status": ControlPageToolSpec("runtime.status", "low", "Show Evelyn runtime status."),
    "control_page.memory_panel": ControlPageToolSpec("control_page.memory_panel", "low", "Open, close, or toggle the memory panel."),
    "memory.open_vault": ControlPageToolSpec("memory.open_vault", "low", "Open the Obsidian memory vault."),
    "voice.status": ControlPageToolSpec("voice.status", "low", "Show voice, STT, and TTS status."),
    "voice.continuity": ControlPageToolSpec("voice.continuity", "low", "Show voice barge-in continuity state."),
    "voice.continuity_reset": ControlPageToolSpec("voice.continuity_reset", "low", "Reset the voice barge-in continuity counters."),
    "voice.input_mode": ControlPageToolSpec("voice.input_mode", "medium", "Set voice input mode.", router_enabled=False),
    "voice.reconnect": ControlPageToolSpec("voice.reconnect", "medium", "Reconnect to the last voice channel.", requires_guild=True),
    "runtime.restart_bot": ControlPageToolSpec("runtime.restart_bot", "medium", "Restart the Evelyn bot process."),
    "runtime.shutdown_stack": ControlPageToolSpec("runtime.shutdown_stack", "high", "Shut down the Evelyn runtime stack.", router_enabled=False),
    "minecraft.inventory": ControlPageToolSpec("minecraft.inventory", "low", "Show Minecraft inventory.", requires_guild=True),
    "minecraft.status": ControlPageToolSpec("minecraft.status", "low", "Show Minecraft/Voyager status.", requires_guild=True),
    "minecraft.connect": ControlPageToolSpec("minecraft.connect", "medium", "Start Voyager Minecraft mode.", requires_guild=True),
    "minecraft.disconnect": ControlPageToolSpec("minecraft.disconnect", "medium", "Stop Voyager Minecraft mode.", requires_guild=True),
    "minecraft.set_goal": ControlPageToolSpec("minecraft.set_goal", "medium", "Set a Minecraft goal.", router_enabled=False, requires_guild=True),
    "autonomy.status": ControlPageToolSpec("autonomy.status", "low", "Show autonomy engine status.", requires_guild=True),
}

CONTROL_PAGE_SLASH_TOOL_ALIASES: dict[str, str] = {
    "/": "control_page.help",
    "/help": "control_page.help",
    "/status": "runtime.status",
    "/memory": "control_page.memory_panel",
    "/obsidian": "memory.open_vault",
    "/voice": "voice.status",
    "/voice status": "voice.status",
    "/voice continuity": "voice.continuity",
    "/voice continuity reset": "voice.continuity_reset",
    "/voice continuity reset confirm": "voice.continuity_reset",
    "/voice reconnect": "voice.reconnect",
    "/voice rejoin": "voice.reconnect",
    "/restart": "runtime.restart_bot",
    "/재시작": "runtime.restart_bot",
    "/shutdown": "runtime.shutdown_stack",
    "/quit": "runtime.shutdown_stack",
    "/exit": "runtime.shutdown_stack",
    "/inventory": "minecraft.inventory",
    "/voyager stats": "minecraft.status",
    "/minecraft status": "minecraft.status",
    "/mc-status": "minecraft.status",
    "/minecraft connect": "minecraft.connect",
    "/mc-connect": "minecraft.connect",
    "/minecraft disconnect": "minecraft.disconnect",
    "/mc-disconnect": "minecraft.disconnect",
    "/autonomy status": "autonomy.status",
}


def build_control_page_commands(*, minecraft_session_active: bool) -> list[dict[str, str]]:
    commands: list[dict[str, str]] = []
    for item in CONTROL_PAGE_COMMANDS:
        visibility = item.get("visibility", "always")
        if visibility == "minecraft-active" and not minecraft_session_active:
            continue
        if visibility == "minecraft-idle" and minecraft_session_active:
            continue
        commands.append({key: value for key, value in item.items() if key != "visibility"})
    return commands


def build_control_page_all_commands() -> list[dict[str, str]]:
    return [
        {key: value for key, value in item.items() if key != "visibility"}
        for item in CONTROL_PAGE_COMMANDS
    ]


def build_control_page_help_reply() -> str:
    lines = ["페이지 명령어"]
    groups = ["기본", "페이지", "음성", "Minecraft", "자율 행동", "시스템"]
    for group in groups:
        items = [item for item in CONTROL_PAGE_COMMANDS if item.get("group") == group]
        if not items:
            continue
        lines.extend(["", group])
        for item in items:
            lines.append(f"- {item['command']} - {item['summary']}")
    return "\n".join(lines)


def control_page_tool_registry_prompt() -> str:
    tools: list[dict[str, Any]] = []
    for spec in CONTROL_PAGE_TOOL_SPECS.values():
        if not spec.router_enabled:
            continue
        tools.append(
            {
                "name": spec.name,
                "risk": spec.risk,
                "description": spec.description,
            }
        )
    return json.dumps(tools, ensure_ascii=False, separators=(",", ":"))


def normalize_control_page_tool_arguments(tool_name: str, arguments: Any) -> dict[str, Any]:
    if isinstance(arguments, str):
        try:
            parsed = json.loads(arguments)
            arguments = parsed if isinstance(parsed, dict) else {}
        except Exception:
            arguments = {}
    if not isinstance(arguments, dict):
        arguments = {}
    cleaned: dict[str, Any] = {}
    if tool_name == "control_page.memory_panel":
        action = clean_text(str(arguments.get("action") or "")).lower()
        cleaned["action"] = action if action in {"open", "close", "toggle"} else "toggle"
    elif tool_name == "voice.input_mode":
        mode = clean_text(str(arguments.get("mode") or "")).lower()
        cleaned["mode"] = mode if mode in {"auto", "local", "discord"} else "auto"
    elif tool_name == "voice.continuity_reset":
        cleaned["reason"] = clean_text(str(arguments.get("reason") or "manual_reset"))
        confirm_requested = clean_text(str(arguments.get("confirm") or ""))
        cleaned["confirm"] = confirm_requested == VOICE_BARGE_IN_RESET_CONFIRM_KEYWORD
    elif tool_name == "minecraft.set_goal":
        cleaned["goal"] = clean_text(str(arguments.get("goal") or ""))
    return cleaned


def control_page_tool_decision(
    tool_name: str,
    *,
    arguments: dict[str, Any] | None = None,
    confidence: float = 1.0,
    source: str = "cheap",
    reply: str = "",
) -> dict[str, Any] | None:
    spec = CONTROL_PAGE_TOOL_SPECS.get(clean_text(tool_name))
    if spec is None:
        return None
    return {
        "tool": spec.name,
        "risk": spec.risk,
        "arguments": normalize_control_page_tool_arguments(spec.name, arguments or {}),
        "confidence": max(0.0, min(1.0, float(confidence or 0.0))),
        "source": source,
        "reply": clean_text(reply),
    }


def control_page_tool_decision_from_llm(decision: dict[str, Any] | None) -> dict[str, Any] | None:
    if not isinstance(decision, dict):
        return None
    tool_call = decision.get("tool_call")
    if tool_call is None and isinstance(decision.get("tool_calls"), list) and decision["tool_calls"]:
        tool_call = decision["tool_calls"][0]
    if not isinstance(tool_call, dict):
        return None
    tool_name = clean_text(str(tool_call.get("name") or tool_call.get("tool") or "")).lower()
    if tool_name not in CONTROL_PAGE_TOOL_SPECS:
        return None
    spec = CONTROL_PAGE_TOOL_SPECS[tool_name]
    if not spec.router_enabled:
        return None
    confidence = decision.get("confidence", tool_call.get("confidence", 0.0))
    return control_page_tool_decision(
        tool_name,
        arguments=normalize_control_page_tool_arguments(tool_name, tool_call.get("arguments")),
        confidence=float(confidence or 0.0),
        source="router",
        reply=clean_text(str(decision.get("reply") or "")),
    )


def control_page_ui_tool_action_from_decision(decision: dict[str, Any] | None) -> str | None:
    tool_decision = control_page_tool_decision_from_llm(decision)
    if not tool_decision or tool_decision.get("tool") != "control_page.memory_panel":
        return None
    action = clean_text(str((tool_decision.get("arguments") or {}).get("action") or "")).lower()
    return action if action in {"open", "close", "toggle"} else None


def control_page_tool_policy_error(decision: dict[str, Any], *, guild_available: bool) -> str | None:
    tool_name = clean_text(str(decision.get("tool") or ""))
    spec = CONTROL_PAGE_TOOL_SPECS.get(tool_name)
    if spec is None:
        return "그 명령은 등록된 도구가 아니라서 실행하지 않을게."
    if spec.requires_guild and not guild_available:
        return "그 명령은 Discord 연결이 필요해."
    source = clean_text(str(decision.get("source") or ""))
    confidence = float(decision.get("confidence") or 0.0)
    if spec.risk == "high" and source != "slash":
        return "그건 위험한 명령이라 명확한 /shutdown 명령으로만 받을게."
    if spec.risk == "medium" and source == "router" and confidence < 0.86:
        return "그 명령은 조금 애매해. 한 번만 더 정확히 말해줘."
    return None


def control_page_tool_reply_from_execution(decision: dict[str, Any], execute_reply: str) -> str:
    tool_name = clean_text(str(decision.get("tool") or ""))
    router_reply = clean_text(str(decision.get("reply") or ""))
    if tool_name == "control_page.memory_panel" and router_reply:
        return router_reply
    return clean_text(execute_reply)


def is_explicit_control_page_restart_request(text: str) -> bool:
    normalized = clean_text(text).lower().strip()
    if not normalized:
        return False
    compact = re.sub(r"\s+", "", normalized)
    if compact in {
        "/restart",
        "/재시작",
        "restart",
        "restartnow",
        "reload",
        "재시작",
        "재시작해",
        "재시작해줘",
        "다시시작",
        "다시시작해",
        "다시시작해줘",
        "다시켜",
        "다시켜줘",
        "재기동",
        "재기동해",
        "재기동해줘",
        "리로드",
        "리로드해",
        "리로드해줘",
    }:
        return True
    question_starts = (
        "왜",
        "어떻게",
        "언제",
        "뭐",
        "무엇",
        "혹시",
        "재시작 왜",
        "재시작하면",
        "재시작 해야",
        "재시작해야",
    )
    if normalized.startswith(question_starts):
        return False
    polite_suffix = r"(?:해|해줘|해라|하자|시켜|시켜줘|부탁해|해둘래|해줄래|해줄수있어|해줄수있니|가능해|ㄱㄱ|now|please|pls)"
    restart_pattern = rf"^(?:이제|지금|그럼|바로|적용하려고|적용되게|please|pls)?\s*(?:이블린|봇|bot|evelyn)?\s*(?:재시작|재기동|restart|reload)\s*{polite_suffix}?[.!?]*$"
    if re.search(restart_pattern, normalized):
        return True
    restart_tail_pattern = rf"(?:이제|지금|그럼|바로|적용하려고|적용되게)\s*(?:이블린|봇|bot|evelyn)?\s*(?:재시작|재기동|restart|reload)\s*{polite_suffix}?[.!?]*$"
    if re.search(restart_tail_pattern, normalized):
        return True
    return bool(re.search(r"^(?:이제|지금|그럼|바로)?\s*(?:다시\s*(?:시작|켜|띄워|올려))\s*(?:줘|해줘|해|라|둘래|줄래|해줄수있어|해줄수있니|부탁해)?[.!?]*$", normalized))


def is_control_page_question_text(text: str) -> bool:
    normalized = clean_text(text).lower().strip()
    if not normalized:
        return False
    if "?" in normalized:
        return True
    return normalized.startswith(("왜", "어떻게", "언제", "뭐", "무엇", "혹시", "설명", "알려줘"))


def is_control_page_runtime_status_request(text: str) -> bool:
    normalized = clean_text(text).lower().strip()
    if not normalized:
        return False
    compact = re.sub(r"\s+", "", normalized)
    if any(key in normalized for key in ("날씨", "비", "weather")):
        return False
    if "status" in normalized:
        return True
    if "상태" in compact:
        return True
    subjectish = any(key in normalized for key in ("이블린", "evelyn", "봇", "bot", "너", "니", "네", "런타임", "runtime", "llm", "모델", "서버"))
    statusish = any(key in compact for key in ("어때", "어떄", "어떠", "괜찮", "문제", "오류", "에러", "로딩", "살아", "떠있", "켜져", "준비"))
    return subjectish and statusish


def control_page_compact_has_any(compact: str, keys: tuple[str, ...]) -> bool:
    return any(key in compact for key in keys)


def cheap_control_page_tool_decision(text: str) -> dict[str, Any] | None:
    normalized = clean_text(text).lower().strip()
    if not normalized:
        return None
    if normalized.startswith("/minecraft goal "):
        return control_page_tool_decision(
            "minecraft.set_goal",
            arguments={"goal": clean_text(text[len("/minecraft goal "):])},
            source="slash",
        )
    if normalized == "/voice continuity":
        return control_page_tool_decision("voice.continuity", source="slash")
    if normalized == "/voice continuity reset confirm":
        return control_page_tool_decision(
            "voice.continuity_reset",
            arguments={"reason": "", "confirm": True},
            source="slash",
        )
    if normalized.startswith("/voice continuity reset confirm"):
        return control_page_tool_decision(
            "voice.continuity_reset",
            arguments={
                "reason": clean_text(text[len("/voice continuity reset confirm"):]),
                "confirm": True,
            },
            source="slash",
        )
    if normalized.startswith("/voice continuity clear confirm"):
        return control_page_tool_decision(
            "voice.continuity_reset",
            arguments={
                "reason": clean_text(text[len("/voice continuity clear confirm"):]),
                "confirm": True,
            },
            source="slash",
        )
    if normalized.startswith("/voice continuity reset"):
        return control_page_tool_decision(
            "voice.continuity_reset",
            arguments={"reason": clean_text(text[len("/voice continuity reset"):])},
            source="slash",
        )
    if normalized.startswith("/voice continuity clear"):
        return control_page_tool_decision(
            "voice.continuity_reset",
            arguments={"reason": clean_text(text[len("/voice continuity clear"):])},
            source="slash",
        )
    if normalized.startswith("/voice input ") or normalized.startswith("/voice source "):
        return control_page_tool_decision(
            "voice.input_mode",
            arguments={"mode": normalized.rsplit(" ", 1)[-1]},
            source="slash",
        )
    slash_tool = CONTROL_PAGE_SLASH_TOOL_ALIASES.get(normalized)
    if slash_tool:
        args = {"action": "toggle"} if slash_tool == "control_page.memory_panel" else {}
        return control_page_tool_decision(slash_tool, arguments=args, source="slash")
    if normalized.startswith("/"):
        return None
    compact = re.sub(r"\s+", "", normalized)
    if is_explicit_control_page_restart_request(normalized):
        return control_page_tool_decision("runtime.restart_bot", source="cheap")
    if any(key in normalized for key in ("메모리", "memory", "패널", "panel")):
        if control_page_compact_has_any(compact, ("열어", "켜", "보여", "open")):
            return control_page_tool_decision("control_page.memory_panel", arguments={"action": "open"}, source="cheap")
        if control_page_compact_has_any(compact, ("닫아", "닫어", "숨겨", "꺼", "close", "hide")):
            return control_page_tool_decision("control_page.memory_panel", arguments={"action": "close"}, source="cheap")
        if control_page_compact_has_any(compact, ("토글", "전환", "toggle")):
            return control_page_tool_decision("control_page.memory_panel", arguments={"action": "toggle"}, source="cheap")
    if ("옵시디언" in normalized or "obsidian" in normalized) and control_page_compact_has_any(compact, ("열어", "켜", "open")):
        return control_page_tool_decision("memory.open_vault", source="cheap")
    if "음성" in normalized or "voice" in normalized:
        if control_page_compact_has_any(compact, ("상태", "status", "봐", "보여")):
            return control_page_tool_decision("voice.status", source="cheap")
        if control_page_compact_has_any(compact, ("재연결", "다시연결", "reconnect", "rejoin")):
            return control_page_tool_decision("voice.reconnect", source="cheap")
    if any(key in normalized for key in ("마크", "minecraft", "voyager", "인벤", "inventory")):
        if control_page_compact_has_any(compact, ("인벤", "inventory")):
            return control_page_tool_decision("minecraft.inventory", source="cheap")
        if control_page_compact_has_any(compact, ("상태", "status", "봐", "보여")):
            return control_page_tool_decision("minecraft.status", source="cheap")
        if control_page_compact_has_any(compact, ("연결해", "시작", "connect")):
            return control_page_tool_decision("minecraft.connect", source="cheap")
        if control_page_compact_has_any(compact, ("끊어", "종료", "중지", "disconnect")):
            return control_page_tool_decision("minecraft.disconnect", source="cheap")
    if ("자율" in normalized or "autonomy" in normalized) and control_page_compact_has_any(compact, ("상태", "status", "봐", "보여")):
        return control_page_tool_decision("autonomy.status", source="cheap")
    if is_control_page_runtime_status_request(normalized):
        return control_page_tool_decision("runtime.status", source="cheap")
    if is_control_page_question_text(normalized):
        return None
    if any(key in normalized for key in ("메모리", "memory", "패널", "panel")):
        if any(key in compact for key in ("열어", "켜", "보여", "open")):
            return control_page_tool_decision("control_page.memory_panel", arguments={"action": "open"}, source="cheap")
        if any(key in compact for key in ("닫아", "닫어", "숨겨", "꺼", "close", "hide")):
            return control_page_tool_decision("control_page.memory_panel", arguments={"action": "close"}, source="cheap")
        if any(key in compact for key in ("토글", "전환", "toggle")):
            return control_page_tool_decision("control_page.memory_panel", arguments={"action": "toggle"}, source="cheap")
    if ("옵시디언" in normalized or "obsidian" in normalized) and any(key in compact for key in ("열어", "켜", "open")):
        return control_page_tool_decision("memory.open_vault", source="cheap")
    if "음성" in normalized or "voice" in normalized:
        if any(key in compact for key in ("상태", "status", "봐", "보여")):
            return control_page_tool_decision("voice.status", source="cheap")
        if any(key in compact for key in ("재연결", "다시연결", "reconnect", "rejoin")):
            return control_page_tool_decision("voice.reconnect", source="cheap")
    if any(key in normalized for key in ("마크", "minecraft", "voyager", "인벤", "inventory")):
        if any(key in compact for key in ("인벤", "inventory")):
            return control_page_tool_decision("minecraft.inventory", source="cheap")
        if any(key in compact for key in ("상태", "status", "봐", "보여")):
            return control_page_tool_decision("minecraft.status", source="cheap")
        if any(key in compact for key in ("연결해", "시작", "connect")):
            return control_page_tool_decision("minecraft.connect", source="cheap")
        if any(key in compact for key in ("끊어", "종료", "중지", "disconnect")):
            return control_page_tool_decision("minecraft.disconnect", source="cheap")
    if ("자율" in normalized or "autonomy" in normalized) and any(key in compact for key in ("상태", "status", "봐", "보여")):
        return control_page_tool_decision("autonomy.status", source="cheap")
    if any(key in compact for key in ("상태보여", "상태봐", "status")) and not any(key in normalized for key in ("날씨", "비", "weather")):
        return control_page_tool_decision("runtime.status", source="cheap")
    return None


def should_route_control_page_tool_candidate(text: str) -> bool:
    normalized = clean_text(text).lower().strip()
    if not normalized or normalized.startswith("/") or len(normalized) > 80:
        return False
    if is_control_page_question_text(normalized):
        return False
    compact = re.sub(r"\s+", "", normalized)
    actionish = any(key in compact for key in ("열어", "닫아", "숨겨", "꺼", "켜", "다시", "적용", "실행", "해줘", "보여줘", "연결"))
    reference = any(key in normalized for key in ("그거", "이거", "아까", "방금", "패널", "창", "상태", "메모리", "음성", "마크", "봇", "이블린"))
    return actionish and reference


__all__ = [
    "CONTROL_PAGE_COMMANDS",
    "CONTROL_PAGE_SLASH_TOOL_ALIASES",
    "CONTROL_PAGE_TOOL_SPECS",
    "ControlPageToolSpec",
    "build_control_page_all_commands",
    "build_control_page_commands",
    "build_control_page_help_reply",
    "cheap_control_page_tool_decision",
    "control_page_tool_decision",
    "control_page_tool_decision_from_llm",
    "control_page_tool_policy_error",
    "control_page_tool_reply_from_execution",
    "control_page_tool_registry_prompt",
    "control_page_ui_tool_action_from_decision",
    "is_control_page_question_text",
    "is_control_page_runtime_status_request",
    "is_explicit_control_page_restart_request",
    "normalize_control_page_tool_arguments",
    "should_route_control_page_tool_candidate",
]
