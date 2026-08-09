from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path
from typing import Any

from .text import clean_text


class ContextIntent(str, Enum):
    CHAT = "chat"
    QUESTION = "question"
    MINECRAFT_TASK = "minecraft_task"
    VISION_QUESTION = "vision_question"
    MEMORY_UPDATE = "memory_update"
    CONTROL = "control"


class ContextPriority(str, Enum):
    LATENCY = "latency"
    ACCURACY = "accuracy"
    ACTION = "action"


class ResponseMode(str, Enum):
    SHORT = "short"
    NORMAL = "normal"
    DETAILED = "detailed"
    ACTION_ONLY = "action_only"


def clean_block_text(text: str) -> str:
    lines = [clean_text(line) for line in str(text or "").splitlines()]
    cleaned: list[str] = []
    previous_blank = False
    for line in lines:
        if not line:
            if not previous_blank:
                cleaned.append("")
            previous_blank = True
            continue
        cleaned.append(line)
        previous_blank = False
    return "\n".join(cleaned).strip()


def _as_bool(value: Any) -> bool:
    if isinstance(value, bool):
        return value
    if isinstance(value, (int, float)):
        return bool(value)
    if isinstance(value, str):
        return value.strip().lower() in {"1", "true", "yes", "on"}
    return bool(value)


@dataclass(slots=True)
class ContextPolicy:
    intent: str = ContextIntent.CHAT.value
    needs_main_llm: bool = True
    needs_memory: bool = True
    needs_runtime_state: bool = True
    needs_minecraft_state: bool = False
    needs_vision: bool = False
    needs_skill_graph: bool = False
    needs_long_context: bool = False
    needs_search: bool = False
    needs_tts: bool = True
    priority: str = ContextPriority.LATENCY.value
    context_focus: list[str] = field(default_factory=list)
    response_mode: str = ResponseMode.NORMAL.value

    @classmethod
    def from_mapping(cls, value: dict[str, Any] | None) -> "ContextPolicy":
        if not isinstance(value, dict):
            return cls()
        policy = cls()
        bool_keys = {
            "needs_main_llm",
            "needs_memory",
            "needs_runtime_state",
            "needs_minecraft_state",
            "needs_vision",
            "needs_skill_graph",
            "needs_long_context",
            "needs_search",
            "needs_tts",
        }
        for key in (
            "intent",
            "needs_main_llm",
            "needs_memory",
            "needs_runtime_state",
            "needs_minecraft_state",
            "needs_vision",
            "needs_skill_graph",
            "needs_long_context",
            "needs_search",
            "needs_tts",
            "priority",
            "response_mode",
        ):
            if key in value:
                setattr(policy, key, _as_bool(value[key]) if key in bool_keys else clean_text(str(value[key])))
        focus = value.get("context_focus")
        if isinstance(focus, list):
            policy.context_focus = [clean_text(str(item)) for item in focus if clean_text(str(item))]
        return policy

    def to_dict(self) -> dict[str, Any]:
        return {
            "intent": self.intent,
            "needs_main_llm": bool(self.needs_main_llm),
            "needs_memory": bool(self.needs_memory),
            "needs_runtime_state": bool(self.needs_runtime_state),
            "needs_minecraft_state": bool(self.needs_minecraft_state),
            "needs_vision": bool(self.needs_vision),
            "needs_skill_graph": bool(self.needs_skill_graph),
            "needs_long_context": bool(self.needs_long_context),
            "needs_search": bool(self.needs_search),
            "needs_tts": bool(self.needs_tts),
            "priority": self.priority,
            "context_focus": list(self.context_focus),
            "response_mode": self.response_mode,
        }


@dataclass(slots=True)
class ToolUseDecision:
    tool_name: str
    reason: str
    risk: str = "low"
    cost: str = "low"
    auto_allowed: bool = False
    required_before_answer: bool = False
    status: str = "planned"
    evidence: str = ""

    def to_dict(self) -> dict[str, Any]:
        tool_name = clean_text(self.tool_name)
        status = clean_text(self.status)
        evidence = clean_block_text(self.evidence)
        if status == "failed":
            evidence = f"{tool_name or 'tool'}_failed"
        return {
            "tool_name": tool_name,
            "reason": clean_text(self.reason),
            "risk": clean_text(self.risk),
            "cost": clean_text(self.cost),
            "auto_allowed": bool(self.auto_allowed),
            "required_before_answer": bool(self.required_before_answer),
            "status": status,
            "evidence": evidence,
        }


@dataclass(slots=True)
class ContextSection:
    name: str
    content: str
    priority: int = 50
    source: str = ""
    metadata: dict[str, Any] = field(default_factory=dict)

    def cleaned_content(self) -> str:
        return clean_block_text(self.content)

    def is_empty(self) -> bool:
        return not self.cleaned_content()


@dataclass(slots=True)
class ContextPacket:
    policy: ContextPolicy = field(default_factory=ContextPolicy)
    system_rules: list[ContextSection] = field(default_factory=list)
    pinned_memory: list[ContextSection] = field(default_factory=list)
    conversation_state: list[ContextSection] = field(default_factory=list)
    retrieved_memory: list[ContextSection] = field(default_factory=list)
    runtime_state: list[ContextSection] = field(default_factory=list)
    tool_context: list[ContextSection] = field(default_factory=list)
    skill_context: list[ContextSection] = field(default_factory=list)
    vision_context: list[ContextSection] = field(default_factory=list)
    recent_turns: list[dict[str, Any]] = field(default_factory=list)
    current_user_input: str = ""

    def sections(self) -> list[ContextSection]:
        ordered: list[ContextSection] = []
        for group in (
            self.system_rules,
            self.pinned_memory,
            self.conversation_state,
            self.retrieved_memory,
            self.runtime_state,
            self.tool_context,
            self.skill_context,
            self.vision_context,
        ):
            ordered.extend(section for section in group if not section.is_empty())
        return ordered


@dataclass(slots=True)
class ContextBudget:
    max_context_tokens: int = 2048
    system_chars: int = 1800
    pinned_memory_chars: int = 1200
    conversation_state_chars: int = 1600
    retrieved_memory_chars: int = 1800
    runtime_state_chars: int = 1600
    tool_context_chars: int = 1400
    skill_context_chars: int = 1800
    vision_context_chars: int = 1600
    recent_turns_limit: int = 4


@dataclass(slots=True)
class MemoryWriterDecision:
    write_raw_transcript: bool = True
    update_conversation_summary: bool = False
    update_runtime_state: bool = True
    store_long_term_memory: bool = False
    store_open_questions: bool = False
    store_minecraft_failure: bool = False
    reason: str = "default"

    def should_run_summary_llm(self) -> bool:
        return bool(
            self.update_conversation_summary
            or self.store_long_term_memory
            or self.store_open_questions
            or self.store_minecraft_failure
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "write_raw_transcript": bool(self.write_raw_transcript),
            "update_conversation_summary": bool(self.update_conversation_summary),
            "update_runtime_state": bool(self.update_runtime_state),
            "store_long_term_memory": bool(self.store_long_term_memory),
            "store_open_questions": bool(self.store_open_questions),
            "store_minecraft_failure": bool(self.store_minecraft_failure),
            "reason": self.reason,
        }


def _trim_text(value: str, max_chars: int) -> str:
    text = clean_block_text(value)
    if max_chars <= 0 or len(text) <= max_chars:
        return text
    return text[: max(0, max_chars - 3)].rstrip() + "..."


def _render_section_group(title: str, sections: list[ContextSection], max_chars: int) -> str:
    lines: list[str] = []
    for section in sorted(sections, key=lambda item: item.priority):
        content = section.cleaned_content()
        if not content:
            continue
        label = clean_text(section.name) or "context"
        if "\n" in content:
            lines.append(f"[{label}]\n{content}")
        else:
            lines.append(f"- {label}: {content}")
    if not lines:
        return ""
    return f"[{title}]\n" + _trim_text("\n".join(lines), max_chars)


class ContextBuilder:
    def __init__(self, budget: ContextBudget | None = None) -> None:
        self.budget = budget or ContextBudget()

    def render_system_context(self, packet: ContextPacket, base_system: str = "") -> str:
        parts: list[str] = []
        base = clean_text(base_system)
        if base:
            parts.append(base)

        rendered_groups = [
            _render_section_group("Pinned Memory", packet.pinned_memory, self.budget.pinned_memory_chars),
            _render_section_group("Conversation State", packet.conversation_state, self.budget.conversation_state_chars),
            _render_section_group("Retrieved Memory", packet.retrieved_memory, self.budget.retrieved_memory_chars),
            _render_section_group("Runtime State", packet.runtime_state, self.budget.runtime_state_chars),
            _render_section_group("Tool Use Policy", packet.tool_context, self.budget.tool_context_chars),
            _render_section_group("Skill / Capability Context", packet.skill_context, self.budget.skill_context_chars),
            _render_section_group("Vision Context", packet.vision_context, self.budget.vision_context_chars),
        ]
        parts.extend(group for group in rendered_groups if group)
        return "\n\n".join(parts)

    def build_messages(self, packet: ContextPacket, base_messages: list[dict[str, Any]]) -> list[dict[str, Any]]:
        messages = [dict(message) for message in base_messages if isinstance(message, dict)]
        base_system = ""
        if messages and messages[0].get("role") == "system":
            base_system = clean_text(str(messages[0].get("content") or ""))
            messages = messages[1:]

        system_context = self.render_system_context(packet, base_system=base_system)
        final_messages: list[dict[str, Any]] = []
        if system_context:
            final_messages.append({"role": "system", "content": system_context})

        recent_turns = packet.recent_turns[-self.budget.recent_turns_limit :] if packet.recent_turns else []
        final_messages.extend(dict(message) for message in recent_turns if isinstance(message, dict))
        final_messages.extend(messages)

        current_input = clean_text(packet.current_user_input)
        if current_input:
            final_messages.append({"role": "user", "content": current_input})
        return final_messages


def build_basic_context_packet(
    *,
    current_user_input: str,
    memory_context: str = "",
    runtime_state: str = "",
    conversation_state: str = "",
    skill_context: str = "",
    vision_context: str = "",
    tool_context: str = "",
    policy: ContextPolicy | dict[str, Any] | None = None,
) -> ContextPacket:
    if isinstance(policy, ContextPolicy):
        context_policy = policy
    else:
        context_policy = ContextPolicy.from_mapping(policy)

    packet = ContextPacket(policy=context_policy, current_user_input=current_user_input)
    if memory_context:
        packet.retrieved_memory.append(ContextSection(name="legacy_memory_context", content=memory_context, source="memory"))
    if runtime_state:
        packet.runtime_state.append(ContextSection(name="runtime_state", content=runtime_state, source="runtime"))
    if conversation_state:
        packet.conversation_state.append(ContextSection(name="conversation_state", content=conversation_state, source="conversation"))
    if tool_context:
        packet.tool_context.append(ContextSection(name="tool_context", content=tool_context, source="tool_policy", priority=35))
    if skill_context:
        packet.skill_context.append(ContextSection(name="skill_context", content=skill_context, source="skill_graph"))
    if vision_context:
        packet.vision_context.append(ContextSection(name="vision_context", content=vision_context, source="vision"))
    return packet


def build_context_policy_for_turn(
    *,
    user_text: str,
    source: str,
    route: str,
    route_meta: dict[str, Any] | None = None,
    cognitive_state: dict[str, Any] | None = None,
) -> ContextPolicy:
    text = clean_text(user_text).lower()
    route_name = clean_text(route).lower()
    action = clean_text(str((cognitive_state or {}).get("action") or ""))
    meta_policy = (route_meta or {}).get("context_policy")
    policy = ContextPolicy.from_mapping(meta_policy if isinstance(meta_policy, dict) else None)

    minecraft_markers = (
        "minecraft",
        "마인크래프트",
        "마크",
        "voyager",
        "오디세이",
        "odyssey",
        "openha",
        "crossagent",
        "인벤토리",
        "곡괭이",
        "도끼",
        "조합대",
        "화로",
        "원목",
        "조약돌",
        "철광석",
    )
    vision_markers = (
        "image",
        "vision",
        "photo",
        "picture",
        "screenshot",
        "사진",
        "이미지",
        "스크린샷",
        "화면",
        "보이는",
        "뭐가 보여",
        "무엇이 보여",
        "뭐 보여",
        "무엇이 보이",
        "캡처",
        "비전",
        "ocr",
    )
    memory_markers = ("remember", "memory", "기억", "전에", "이전", "문맥", "요약")
    detailed_markers = ("자세히", "길게", "구체", "설계", "문서", "분석", "왜")

    is_minecraft = any(marker in text for marker in minecraft_markers)
    korean_vision_markers = (
        "화면",
        "내 화면",
        "보이는",
        "뭐가 보여",
        "무엇이 보여",
        "뭐 보여",
        "무엇이 보이",
        "사진",
        "이미지",
        "스크린샷",
        "캡처",
        "캡쳐",
        "비전",
        "ocr",
    )
    is_vision = any(marker in text for marker in vision_markers) or any(marker in text for marker in korean_vision_markers)
    asks_memory = any(marker in text for marker in memory_markers)
    wants_detail = any(marker in text for marker in detailed_markers) or len(text) >= 240

    if is_minecraft:
        policy.intent = ContextIntent.MINECRAFT_TASK.value
        policy.needs_minecraft_state = True
        policy.needs_skill_graph = True
        policy.priority = ContextPriority.ACTION.value
        policy.context_focus.extend(["minecraft_state", "capability_graph", "current_goal"])
    elif is_vision:
        policy.intent = ContextIntent.VISION_QUESTION.value
        policy.needs_vision = True
        policy.priority = ContextPriority.ACCURACY.value
        policy.context_focus.extend(["vision_target", "current_question"])
    elif action == "ask":
        policy.intent = ContextIntent.QUESTION.value
        policy.priority = ContextPriority.ACCURACY.value
    elif action == "search_then_answer":
        policy.intent = ContextIntent.QUESTION.value
        policy.needs_search = True
        policy.priority = ContextPriority.ACCURACY.value
    else:
        policy.intent = ContextIntent.CHAT.value

    if asks_memory or route_name in {"sub_wait", "sub_hint"}:
        policy.needs_memory = True
        policy.context_focus.append("relevant_memory")

    if wants_detail or route_name == "sub_wait":
        policy.needs_long_context = True
        policy.response_mode = ResponseMode.DETAILED.value
        policy.priority = ContextPriority.ACCURACY.value
    elif source == "voice":
        policy.response_mode = ResponseMode.SHORT.value

    policy.context_focus = list(dict.fromkeys(clean_text(item) for item in policy.context_focus if clean_text(item)))
    return policy


def _contains_any_marker(text: str, markers: tuple[str, ...]) -> bool:
    return any(marker and marker in text for marker in markers)


def build_tool_use_decisions(user_text: str, policy: ContextPolicy | dict[str, Any] | None = None) -> list[ToolUseDecision]:
    context_policy = policy if isinstance(policy, ContextPolicy) else ContextPolicy.from_mapping(policy)
    text = clean_text(user_text).lower()
    decisions: dict[str, ToolUseDecision] = {}

    def add(decision: ToolUseDecision) -> None:
        existing = decisions.get(decision.tool_name)
        if existing is None:
            decisions[decision.tool_name] = decision
            return
        existing.required_before_answer = existing.required_before_answer or decision.required_before_answer
        existing.auto_allowed = existing.auto_allowed or decision.auto_allowed
        existing.reason = clean_text(existing.reason or decision.reason)

    runtime_markers = (
        "status",
        "health",
        "runtime",
        "server",
        "port",
        "vram",
        "gpu",
        "oom",
        "process",
        "model",
        "tts",
        "stt",
        "mic",
        "상태",
        "서버",
        "포트",
        "브이램",
        "메모리",
        "프로세스",
        "모델",
        "마이크",
        "음성",
        "오류",
        "에러",
        "로딩",
        "언로드",
    )
    screen_markers = (
        "screen",
        "screenshot",
        "vision",
        "capture",
        "보이는",
        "화면",
        "스크린",
        "캡처",
    )
    ocr_markers = (
        "ocr",
        "text on screen",
        "read text",
        "title",
        "button",
        "label",
        "menu",
        "글자",
        "텍스트",
        "읽어",
        "읽고",
        "문자",
        "제목",
        "버튼",
        "메뉴",
        "오류 메시지",
    )
    memory_markers = ("remember", "memory", "previous", "earlier", "기억", "이전", "아까", "방금", "대화")
    explicit_web_markers = (
        "latest",
        "news",
        "weather",
        "forecast",
        "rain",
        "price",
        "search",
        "internet",
        "최신",
        "뉴스",
        "날씨",
        "예보",
        "강수",
        "우산",
        "가격",
        "시세",
        "환율",
        "검색",
        "인터넷",
        "웹에서",
        "외부 검색",
        "찾아봐",
        "찾아 봐",
        "찾아줘",
        "찾아 줘",
        "찾아보",
        "알아봐",
        "알아 봐",
        "알아보",
        "조사해",
        "조사해봐",
        "조사해 봐",
        "법률",
        "법령",
        "규정",
    )
    current_time_markers = (
        "current",
        "today",
        "now",
        "현재",
        "오늘",
        "지금",
    )
    external_subject_markers = (
        "president",
        "prime minister",
        "ceo",
        "stock",
        "schedule",
        "score",
        "대통령",
        "총리",
        "대표",
        "주가",
        "경기",
        "일정",
        "금리",
        "정책",
    )
    local_file_markers = ("log", "file", "test", "diff", "git", "로그", "파일", "테스트", "문서", "코드")
    tool_diagnostic_markers = (
        "tool call",
        "tool-call",
        "function call",
        "function-call",
        "tool use",
        "main llm",
        "main_llm",
        "llm tool",
        "도구 호출",
        "툴 호출",
        "도구콜",
        "툴콜",
        "도구 사용",
        "툴 사용",
        "메인 llm",
        "메인 모델",
        "메인모델",
        "호출이",
        "호출을",
    )
    asks_tool_diagnostic = _contains_any_marker(text, tool_diagnostic_markers) and _contains_any_marker(
        text,
        ("tool", "function", "llm", "도구", "툴", "호출", "메인"),
    )

    if _contains_any_marker(text, runtime_markers):
        add(
            ToolUseDecision(
                tool_name="runtime_status",
                reason="The answer may depend on live Evelyn process, port, model, GPU, or error state.",
                auto_allowed=True,
                required_before_answer=_contains_any_marker(text, runtime_markers),
            )
        )
    asks_screen_evidence = context_policy.needs_vision or _contains_any_marker(
        text,
        screen_markers,
    )
    if asks_screen_evidence:
        add(
            ToolUseDecision(
                tool_name="vision_capture_or_watch",
                reason="The user is asking about visible screen state or the policy requested vision context.",
                auto_allowed=True,
                required_before_answer=True,
                cost="medium",
            )
        )
    if asks_screen_evidence and _contains_any_marker(text, ocr_markers):
        add(
            ToolUseDecision(
                tool_name="vision_ocr",
                reason="The user is asking to read text from the screen.",
                auto_allowed=True,
                required_before_answer=True,
                cost="high",
                evidence="May lazy-load Falcon-OCR; first request can be slow and use extra VRAM.",
            )
        )
    if _contains_any_marker(text, memory_markers):
        add(
            ToolUseDecision(
                tool_name="memory_recall",
                reason="The answer may depend on prior conversation or saved context.",
                auto_allowed=True,
                required_before_answer=_contains_any_marker(text, memory_markers),
            )
        )
    needs_current_external_info = (
        _contains_any_marker(text, explicit_web_markers)
        or (
            _contains_any_marker(text, current_time_markers)
            and _contains_any_marker(text, external_subject_markers)
        )
    )
    if context_policy.needs_search or needs_current_external_info:
        add(
            ToolUseDecision(
                tool_name="web_current_info",
                reason="The user may be asking for current external information.",
                risk="external",
                cost="medium",
                auto_allowed=False,
                required_before_answer=True,
                status="needs_permission_or_external_tool",
                evidence="Do not present current external facts as verified unless a web/search tool result is present.",
            )
        )
    if _contains_any_marker(text, local_file_markers):
        add(
            ToolUseDecision(
                tool_name="local_file_or_log_read",
                reason="The user may be asking about implementation files, logs, tests, or diffs.",
                auto_allowed=False,
                required_before_answer=True,
                status="needs_local_tool",
                evidence="Use local file/log evidence before making code or runtime claims.",
            )
        )
    if asks_tool_diagnostic:
        add(
            ToolUseDecision(
                tool_name="runtime_status",
                reason="The user is reporting weak main-LLM tool behavior; live model/runtime state may affect tool execution.",
                auto_allowed=True,
                required_before_answer=True,
            )
        )
        add(
            ToolUseDecision(
                tool_name="local_file_or_log_read",
                reason="The user is reporting a runtime/tool-calling behavior problem; inspect implementation or logs before claiming a cause.",
                auto_allowed=True,
                required_before_answer=True,
                status="planned",
                evidence="Check the tool policy, route context, prompt assembly, and recent logs before diagnosing.",
            )
        )

    return list(decisions.values())


def render_tool_use_context(decisions: list[ToolUseDecision]) -> str:
    if not decisions:
        return ""
    lines = [
        "Tool-use policy for this answer.",
        "Required tool evidence is a hard gate: do not answer from guesswork when required=true.",
        "If a required tool is unavailable, failed, or not executed, say that clearly and avoid claiming tool-backed evidence.",
        "If status is executed, ground the answer in evidence. If status is planned/needs_local_tool/needs_permission_or_external_tool, either execute the tool in the runtime path first or state that the evidence is still missing.",
        "If status is executed_withheld, the tool ran but its result was deliberately excluded from model input; do not use or infer that result as evidence.",
    ]
    for decision in decisions:
        item = decision.to_dict()
        parts = [
            f"tool={item['tool_name']}",
            f"status={item['status'] or 'planned'}",
            f"required={str(item['required_before_answer']).lower()}",
            f"auto_allowed={str(item['auto_allowed']).lower()}",
            f"risk={item['risk'] or 'low'}",
            f"cost={item['cost'] or 'low'}",
        ]
        reason = item.get("reason") or ""
        evidence = item.get("evidence") or ""
        if reason:
            parts.append(f"reason={reason}")
        if evidence:
            parts.append(f"evidence={evidence}")
        lines.append("- " + "; ".join(parts))
    return "\n".join(lines)


def build_conversation_state_context(
    *,
    cognitive_state: dict[str, Any] | None = None,
    session_state: dict[str, Any] | None = None,
    route: str = "",
    unanswered_user_turn: bool = False,
) -> str:
    lines: list[str] = []
    if route:
        lines.append(f"route: {clean_text(route)}")
    state = cognitive_state or {}
    for label, key in (
        ("action", "action"),
        ("user_intent", "user_intent"),
    ):
        value = clean_text(str(state.get(key) or ""))
        if value:
            lines.append(f"{label}: {value}")
    session = session_state or {}
    if session.get("awaiting_user_reply"):
        lines.append("awaiting_user_reply: true")
    if session.get("topic_id"):
        lines.append(f"topic_id: {clean_text(str(session.get('topic_id')))}")
    if session.get("last_speaker"):
        lines.append(f"last_speaker: {clean_text(str(session.get('last_speaker')))}")
    if unanswered_user_turn:
        lines.extend(
            (
                "continuity_schema: conversation.unanswered-user.v1",
                "unanswered_user_turn: true",
                "continuity_content_free: true",
                (
                    "continuity_rule: The latest accepted user message in "
                    "conversation history has no delivered assistant reply. "
                    "Treat it as unanswered context, address it together with "
                    "the current request when relevant, and never claim it was "
                    "already answered."
                ),
            )
        )
    return "\n".join(lines)


def has_unanswered_user_turn(messages: list[dict[str, Any]]) -> bool:
    """Return whether the latest non-empty conversational row is user-only.

    The result intentionally carries no message content so it is safe to expose
    through metrics and turn summaries.
    """

    for message in reversed(messages):
        if not isinstance(message, dict):
            continue
        role = clean_text(str(message.get("role") or "")).casefold()
        if role not in {"user", "assistant"}:
            continue
        if not clean_text(str(message.get("content") or "")):
            continue
        return role == "user"
    return False


def build_runtime_state_context(
    *,
    source: str,
    route: str,
    guild_id: int | None = None,
    session_key: str | None = None,
    room_key: str | None = None,
    person_key: str | None = None,
    session_memory_key: str | None = None,
    session_state: dict[str, Any] | None = None,
) -> str:
    lines = [
        f"source: {clean_text(source) or 'unknown'}",
        f"route: {clean_text(route) or 'unknown'}",
    ]
    if guild_id is not None:
        lines.append(f"guild_id: {guild_id}")
    for label, value in (
        ("session_key", session_key),
        ("room_key", room_key),
        ("person_key", person_key),
        ("session_memory_key", session_memory_key),
    ):
        clean = clean_text(str(value or ""))
        if clean:
            lines.append(f"{label}: {clean}")
    session = session_state or {}
    if session.get("awaiting_user_reply"):
        lines.append("session.awaiting_user_reply: true")
    if session.get("topic_id"):
        lines.append(f"session.topic_id: {clean_text(str(session.get('topic_id')))}")
    return "\n".join(lines)


def _tokenize_context_query(text: str) -> list[str]:
    lowered = clean_text(text).lower()
    tokens = set(re.findall(r"[a-z0-9_]{3,}", lowered))
    korean_markers = {
        "\uace1\uad2d\uc774": ["pickaxe"],
        "\ub3c4\ub07c": ["axe"],
        "\uc870\ud569": ["craft"],
        "\uc6d0\ubaa9": ["log"],
        "\ub098\ubb34": ["log", "wood"],
        "\uc870\uc57d\ub3cc": ["cobblestone", "stone"],
        "\uc870\ud569\ub300": ["crafting_table"],
        "\ud654\ub85c": ["furnace"],
        "\ud6a8\uc728": ["plan"],
        "\ucca0": ["iron"],
    }
    for marker, mapped in korean_markers.items():
        if marker in lowered:
            tokens.update(mapped)
    return sorted(tokens)


def _compact_mapping_items(mapping: dict[str, Any], *, limit: int = 8) -> str:
    if not mapping:
        return ""
    parts: list[str] = []
    for key in sorted(mapping.keys())[:limit]:
        value = mapping.get(key)
        parts.append(f"{clean_text(str(key))}:{clean_text(str(value))}")
    if len(mapping) > limit:
        parts.append(f"+{len(mapping) - limit} more")
    return ", ".join(parts)


def _minecraft_recipe_snippets(tokens: list[str]) -> list[str]:
    token_text = " ".join(tokens)
    snippets: list[str] = []
    if "pickaxe" in token_text or "stone_pickaxe" in token_text:
        snippets.append(
            "recipe: stone_pickaxe requires 3 cobblestone + 2 sticks + crafting_table; "
            "if missing cobblestone, make/use wooden_pickaxe first."
        )
    if "axe" in token_text or "stone_axe" in token_text:
        snippets.append("recipe: stone_axe requires 3 cobblestone + 2 sticks + crafting_table.")
    if "furnace" in token_text or "smelt" in token_text or "iron" in token_text:
        snippets.append(
            "recipe: furnace requires 8 cobblestone; smelting iron requires raw_iron/iron_ore plus fuel."
        )
    if "torch" in token_text:
        snippets.append("recipe: torches require coal or charcoal plus sticks.")
    return snippets


def _read_matching_voyager_skills(
    *,
    skill_library_path: str | Path | None,
    tokens: list[str],
    limit: int = 5,
) -> list[str]:
    if not skill_library_path or not tokens:
        return []
    path = Path(skill_library_path)
    if not path.exists():
        return []
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return []
    if not isinstance(data, dict):
        return []

    scored: list[tuple[int, str, str]] = []
    for name, payload in data.items():
        if not isinstance(name, str):
            continue
        description = ""
        if isinstance(payload, dict):
            description = clean_text(str(payload.get("description") or ""))
        haystack = f"{name} {description}".lower()
        score = sum(1 for token in tokens if token and token in haystack)
        if score <= 0:
            continue
        compact_description = description.replace("\n", " ")
        if len(compact_description) > 180:
            compact_description = compact_description[:177].rstrip() + "..."
        scored.append((score, name, compact_description))

    scored.sort(key=lambda item: (-item[0], item[1]))
    return [
        f"voyager_skill: {name} - {description or 'available executable skill'}"
        for _score, name, description in scored[: max(1, limit)]
    ]


def _read_json_mapping(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {}
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return {}
    return value if isinstance(value, dict) else {}


def _format_odyssey_recipe(value: Any) -> str:
    if not isinstance(value, list) or len(value) < 1:
        return clean_text(str(value))
    ingredients = value[0]
    output_count = value[1] if len(value) > 1 else None
    needs_table = value[2] if len(value) > 2 else None
    if isinstance(ingredients, list):
        ingredient_text = ", ".join(clean_text(str(item)) for item in ingredients)
    else:
        ingredient_text = clean_text(str(ingredients))
    suffix = []
    if output_count is not None:
        suffix.append(f"output={output_count}")
    if needs_table is not None:
        suffix.append(f"needs_table={bool(needs_table)}")
    return f"{ingredient_text}" + (f" ({', '.join(suffix)})" if suffix else "")


def _read_matching_odyssey_capabilities(
    *,
    capability_data_dir: str | Path | None,
    tokens: list[str],
    limit: int = 6,
) -> list[str]:
    if not capability_data_dir or not tokens:
        return []
    data_dir = Path(capability_data_dir)
    if not data_dir.exists():
        return []

    func_map = _read_json_mapping(data_dir / "func.json")
    pre_item = _read_json_mapping(data_dir / "pre_item.json")
    pre_tool = _read_json_mapping(data_dir / "pre_tool.json")
    pre_smelt = _read_json_mapping(data_dir / "pre_smelt.json")
    pre_collect = _read_json_mapping(data_dir / "pre_collect.json")

    candidate_names = set(func_map) | set(pre_item) | set(pre_tool) | set(pre_smelt) | set(pre_collect)
    scored: list[tuple[int, str]] = []
    for name in candidate_names:
        if not isinstance(name, str):
            continue
        haystack = name.lower()
        score = sum(1 for token in tokens if token and (token in haystack or haystack in token))
        if score > 0:
            scored.append((score, name))
    scored.sort(key=lambda item: (-item[0], item[1]))

    lines: list[str] = []
    for _score, name in scored[: max(1, limit)]:
        bits: list[str] = []
        action = clean_text(str(func_map.get(name) or ""))
        if action:
            bits.append(f"action={action}")
        if name in pre_item:
            bits.append(f"recipe={_format_odyssey_recipe(pre_item.get(name))}")
        if name in pre_tool:
            bits.append(f"tool={clean_text(str(pre_tool.get(name)))}")
        if name in pre_smelt:
            bits.append(f"smelt={clean_text(str(pre_smelt.get(name)))}")
        if name in pre_collect:
            bits.append(f"collect={clean_text(str(pre_collect.get(name)))}")
        lines.append(f"odyssey_capability: {name} - " + "; ".join(bits))
    return lines


def build_minecraft_skill_context(
    policy: ContextPolicy,
    *,
    user_text: str = "",
    minecraft_state: dict[str, Any] | None = None,
    skill_library_path: str | Path | None = None,
    capability_data_dir: str | Path | None = None,
    limit: int = 5,
) -> str:
    if not policy.needs_skill_graph:
        return ""
    tokens = _tokenize_context_query(user_text)
    lines = [
        "Skill graph requested. Use compact capability/recipe snippets only.",
        "Do not dump full Odyssey/OpenHA/Voyager libraries into the main LLM context.",
    ]
    if minecraft_state:
        runtime_snapshot = minecraft_state.get("runtime_snapshot") if isinstance(minecraft_state.get("runtime_snapshot"), dict) else {}
        if runtime_snapshot:
            freshness = clean_text(str(runtime_snapshot.get("freshness") or ""))
            age_sec = runtime_snapshot.get("age_sec")
            source = clean_text(str(runtime_snapshot.get("source") or ""))
            details = []
            if freshness:
                details.append(f"freshness={freshness}")
            if age_sec is not None:
                details.append(f"age_sec={age_sec}")
            if source:
                details.append(f"source={source}")
            if details:
                lines.append("runtime_snapshot: " + "; ".join(details))
            if runtime_snapshot.get("last_error"):
                lines.append(f"runtime_snapshot_error: {clean_text(str(runtime_snapshot.get('last_error')))}")
        inventory = minecraft_state.get("inventory") if isinstance(minecraft_state.get("inventory"), dict) else {}
        inventory_summary = clean_text(str(minecraft_state.get("inventory_summary") or ""))
        if not inventory_summary:
            inventory_summary = _compact_mapping_items(inventory)
        for label, value in (
            ("goal", minecraft_state.get("goal")),
            ("current_task", minecraft_state.get("current_task")),
            ("stage", minecraft_state.get("stage")),
            ("position", minecraft_state.get("position_text")),
            ("health", minecraft_state.get("health")),
            ("hunger", minecraft_state.get("hunger")),
        ):
            clean = clean_text(str(value or ""))
            if clean:
                lines.append(f"{label}: {clean}")
        if inventory_summary:
            lines.append(f"inventory: {inventory_summary}")
        inventory_plan = minecraft_state.get("last_inventory_plan")
        if isinstance(inventory_plan, dict):
            compact_plan = json.dumps(inventory_plan, ensure_ascii=False)
            lines.append(f"inventory_plan: {compact_plan[:500]}")

    for snippet in _minecraft_recipe_snippets(tokens):
        lines.append(snippet)
    for snippet in _read_matching_voyager_skills(
        skill_library_path=skill_library_path,
        tokens=tokens,
        limit=limit,
    ):
        lines.append(snippet)
    for snippet in _read_matching_odyssey_capabilities(
        capability_data_dir=capability_data_dir,
        tokens=tokens,
        limit=limit,
    ):
        lines.append(snippet)
    return "\n".join(lines)


def build_skill_context_hint(policy: ContextPolicy) -> str:
    return build_minecraft_skill_context(policy)


def build_vision_context_hint(policy: ContextPolicy, *, user_text: str = "") -> str:
    if not policy.needs_vision:
        return ""
    lines = [
        "Vision context requested. Use attached image analysis or prepared visual observations when available. "
        "If scene, OCR, or vision-tool failure text is present in this section, answer from that evidence before any generic reply. "
        "Do not ignore this section. If no visual payload is present, state that visual input is missing instead of hallucinating."
    ]
    marker = "[Attached Visual Inputs]"
    if marker in user_text:
        attached = user_text.split(marker, 1)[1].strip()
        if attached:
            lines.append("attached_visual_inputs:\n" + attached[:1200])
    return "\n".join(lines)


def build_memory_writer_decision(
    *,
    user_text: str,
    answer: str,
    source: str,
    should_refresh_memory: bool,
    runtime_mode: str = "normal",
) -> MemoryWriterDecision:
    merged = clean_text(f"{user_text} {answer}").lower()
    decision = MemoryWriterDecision(reason="refresh" if should_refresh_memory else "raw_only")
    if runtime_mode == "realtime":
        decision.reason = "realtime_raw_only"
        return decision
    if not should_refresh_memory:
        return decision

    decision.update_conversation_summary = True
    explicit_memory_markers = ("remember", "memory", "decided", "preference", "\uae30\uc5b5", "\uacb0\uc815", "\uc120\ud638")
    if any(marker in merged for marker in explicit_memory_markers):
        decision.store_long_term_memory = True
        decision.reason = "explicit_memory_or_decision"
    if "?" in user_text or "\uff1f" in user_text:
        decision.store_open_questions = True
    if "minecraft" in merged or "voyager" in merged or "\ub9c8\ud06c" in merged:
        decision.update_runtime_state = True
        if any(marker in merged for marker in ("failed", "error", "exception", "\uc2e4\ud328", "\uc624\ub958")):
            decision.store_minecraft_failure = True
            decision.reason = "minecraft_failure"
    return decision


__all__ = [
    "ContextBudget",
    "ContextBuilder",
    "ContextIntent",
    "ContextPacket",
    "ContextPolicy",
    "ContextPriority",
    "ContextSection",
    "ToolUseDecision",
    "MemoryWriterDecision",
    "ResponseMode",
    "build_context_policy_for_turn",
    "build_basic_context_packet",
    "build_conversation_state_context",
    "build_memory_writer_decision",
    "build_minecraft_skill_context",
    "build_runtime_state_context",
    "build_skill_context_hint",
    "build_tool_use_decisions",
    "build_vision_context_hint",
    "clean_block_text",
    "has_unanswered_user_turn",
    "render_tool_use_context",
]
