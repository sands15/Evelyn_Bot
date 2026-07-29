from __future__ import annotations

from dataclasses import dataclass
import json
import os
import re
from typing import Any, Awaitable, Callable

from aiohttp import ClientSession, ClientTimeout

from .text import clean_text


@dataclass(frozen=True, slots=True)
class FastToolCapability:
    name: str
    mode: str
    risk: str
    description: str


FAST_TOOL_CAPABILITIES = (
    FastToolCapability("web_search", "inline", "read_only_external", "Search current external information."),
    FastToolCapability("research_compare", "background", "read_only_external", "Research and compare external candidates."),
    FastToolCapability("runtime_investigation", "background", "read_only_local", "Inspect Evelyn health and mounted logs."),
    FastToolCapability("memory_recall", "inline", "read_only_local", "Recall saved Evelyn conversation memory."),
    FastToolCapability("runtime_status", "inline", "read_only_local", "Inspect service, model, port, GPU, and error state."),
    FastToolCapability("runtime_log_read", "inline", "read_only_local", "Read bounded mounted Evelyn logs."),
    FastToolCapability("datetime", "inline", "read_only_local", "Read the current Asia/Seoul date and time."),
    FastToolCapability("control_page_panel", "command", "local_ui", "Open, close, or toggle a Control-Page panel."),
    FastToolCapability("microphone_control", "command", "local_io", "Read or change local microphone state."),
    FastToolCapability("audio_output_control", "command", "local_io", "Read or change local audio output."),
    FastToolCapability("runtime_restart", "command", "local_process", "Request the scoped Evelyn runtime restart."),
    FastToolCapability("runtime_shutdown", "command", "local_process", "Request the scoped Evelyn runtime shutdown."),
    FastToolCapability("minecraft_start", "background", "local_process", "Lazy-start Minecraft services."),
    FastToolCapability("minecraft_goal", "background", "game_action", "Send a goal to the Minecraft agent."),
)
FAST_TOOL_CAPABILITY_BY_NAME = {item.name: item for item in FAST_TOOL_CAPABILITIES}
ROUTER_ALLOWED_TOOLS = frozenset(
    {
        "web_search",
        "research_compare",
        "runtime_investigation",
        "memory_recall",
        "runtime_status",
        "runtime_log_read",
        "ask_clarification",
        "none",
    }
)

ROUTER_LLM_URL = os.getenv("ROUTER_LLM_URL", "http://router_llm:9822/v1/chat/completions")
ROUTER_LLM_MODEL = os.getenv("ROUTER_LLM_MODEL", "gemma-4-E2B-it-Q4_K_M.gguf")

RouterProvider = Callable[[str, list[dict[str, Any]]], Awaitable[dict[str, Any] | None]]

_SEARCH_MARKERS = (
    "검색",
    "웹에서",
    "인터넷",
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
    "search",
    "look up",
    "research",
    "최신",
    "뉴스",
    "날씨",
    "가격",
    "시세",
    "환율",
    "latest",
    "news",
    "weather",
    "price",
)
_RESEARCH_MARKERS = (
    "비교",
    "후보",
    "교체",
    "추천",
    "장단점",
    "벤치마크",
    "모델들",
    "자료",
    "논문",
    "조사",
    "research",
    "compare",
    "candidate",
)
_INVESTIGATION_MARKERS = (
    "문제 찾아",
    "원인 찾아",
    "문제 조사",
    "원인 조사",
    "오류 조사",
    "에러 조사",
    "진단해",
    "점검해봐",
    "점검해 봐",
    "왜 안 돼",
    "왜 안돼",
    "고장",
    "investigate",
    "diagnose",
    "root cause",
)
_LOCAL_RUNTIME_MARKERS = (
    "이블린",
    "런타임",
    "도커",
    "컨테이너",
    "서버",
    "서비스",
    "로그",
    "오류",
    "에러",
    "oom",
    "gpu",
    "tts",
    "stt",
    "llm",
    "마이크",
    "음성",
)
_TOPIC_MARKERS = (
    "모델",
    "stt",
    "tts",
    "llm",
    "voxcpm",
    "qwen",
    "whisper",
    "음성",
    "검색",
    "비교",
    "후보",
    "교체",
    "오류",
    "에러",
    "문제",
    "서버",
    "서비스",
    "이블린",
)
_FOLLOWUP_MARKERS = (
    "아니",
    "그거",
    "그걸",
    "그게",
    "찾아",
    "검색",
    "알아봐",
    "조사",
    "외부 검색",
    "외부 검사",
    "말고",
    "해보라고",
    "해 보라고",
    "하라고",
)
_FALSE_WEB_UNAVAILABILITY_RE = re.compile(
    r"(?:웹\s*검색|외부\s*(?:검색|데이터\s*조회)|인터넷\s*검색).{0,40}"
    r"(?:권한.{0,12}없|지원되지\s*않|사용할\s*수\s*없|불가능)",
    re.IGNORECASE,
)
_CAPABILITY_STATUS_MARKERS = (
    "권한",
    "지원",
    "가능",
    "할 수",
    "쓸 수",
    "사용할 수",
    "돼",
    "되나",
    "있어",
    "없어",
    "연결",
)


@dataclass(slots=True)
class FastToolPlan:
    intent: str
    tool_name: str
    mode: str
    query: str
    confidence: float
    source: str
    reason: str = ""

    @property
    def is_background(self) -> bool:
        return self.mode == "background"

    def to_dict(self) -> dict[str, Any]:
        return {
            "intent": self.intent,
            "tool": self.tool_name,
            "mode": self.mode,
            "query": self.query,
            "confidence": self.confidence,
            "source": self.source,
            "reason": self.reason,
        }


def render_fast_tool_registry_context() -> str:
    lines = [
        "registered_fast_tools:",
        *(
            f"- {item.name}: mode={item.mode}; risk={item.risk}; available=true; {item.description}"
            for item in FAST_TOOL_CAPABILITIES
        ),
        "capability_truth_rule=Never claim an available registered tool is unsupported or lacks permission.",
        "dangerous_tool_rule=Router LLM cannot select restart, shutdown, microphone, audio, or game actions; those require deterministic commands.",
    ]
    return "\n".join(lines)


def answer_fast_tool_capability_question(text: str) -> str | None:
    normalized = clean_text(text).lower()
    if not normalized:
        return None
    asks_status = _has_any(normalized, _CAPABILITY_STATUS_MARKERS) or normalized.rstrip(" ?") in {
        "도구",
        "툴",
        "tools",
    }
    if not asks_status:
        return None
    if _has_any(normalized, ("웹 검색", "웹검색", "외부 검색", "인터넷 검색")):
        return "웹 검색 도구는 연결돼 있고 읽기 전용 외부 검색을 실행할 수 있어."
    if _has_any(normalized, ("무슨 도구", "어떤 도구", "도구 뭐", "툴 뭐", "tools")):
        available = ", ".join(item.name for item in FAST_TOOL_CAPABILITIES)
        return f"현재 등록된 도구는 {available}야."
    return None


def _recent_user_texts(recent_messages: list[dict[str, Any]]) -> list[str]:
    return [
        clean_text(item.get("content") or item.get("text"))
        for item in recent_messages
        if clean_text(item.get("role")).lower() == "user"
        and clean_text(item.get("content") or item.get("text"))
    ]


def _has_any(text: str, markers: tuple[str, ...]) -> bool:
    return any(marker in text for marker in markers)


def _has_research_context(text: str, recent_messages: list[dict[str, Any]]) -> bool:
    combined = " ".join([text, *_recent_user_texts(recent_messages)[-4:]]).lower()
    return _has_any(combined, _SEARCH_MARKERS + _RESEARCH_MARKERS + _TOPIC_MARKERS)


def normalize_stt_tool_text(text: str, *, recent_messages: list[dict[str, Any]] | None = None) -> str:
    normalized = clean_text(text)
    if not normalized:
        return ""
    normalized = re.sub(r"(?i)(?<![A-Za-z])s[\s._-]*t[\s._-]*t(?![A-Za-z])", "STT", normalized)
    normalized = re.sub(r"(?i)(?<![A-Za-z])t[\s._-]*t[\s._-]*s(?![A-Za-z])", "TTS", normalized)
    normalized = re.sub(r"(?i)\bvox\s*cpm\b", "VoxCPM", normalized)
    normalized = re.sub(r"(?i)\bqwen\s*3?\s*asr\b", "Qwen3-ASR", normalized)
    if _has_research_context(normalized, list(recent_messages or [])):
        normalized = re.sub(r"외부\s*(?:검사|검수)", "외부 검색", normalized)
    return clean_text(normalized)


def _best_recent_topic(recent_messages: list[dict[str, Any]]) -> str:
    for text in reversed(_recent_user_texts(recent_messages)[-6:]):
        lowered = text.lower()
        if _has_any(lowered, _TOPIC_MARKERS):
            return text
    return ""


def contextual_tool_query(text: str, recent_messages: list[dict[str, Any]]) -> str:
    normalized = normalize_stt_tool_text(text, recent_messages=recent_messages)
    lowered = normalized.lower()
    recent_topic = _best_recent_topic(recent_messages)
    is_short_followup = len(normalized) <= 28 and _has_any(lowered, _FOLLOWUP_MARKERS)
    if recent_topic and is_short_followup and clean_text(recent_topic) != normalized:
        return clean_text(f"{recent_topic} {normalized}")
    return normalized


def _plan_from_rules(text: str, recent_messages: list[dict[str, Any]]) -> FastToolPlan | None:
    normalized = normalize_stt_tool_text(text, recent_messages=recent_messages)
    lowered = normalized.lower()
    if answer_fast_tool_capability_question(normalized) is not None:
        return None
    contextual = contextual_tool_query(normalized, recent_messages)
    contextual_lower = contextual.lower()
    recent_topic = _best_recent_topic(recent_messages)

    local_investigation = _has_any(contextual_lower, _LOCAL_RUNTIME_MARKERS) and (
        _has_any(contextual_lower, _INVESTIGATION_MARKERS)
        or (
            _has_any(contextual_lower, ("문제", "원인", "오류", "에러", "고장"))
            and _has_any(contextual_lower, ("찾아", "조사", "진단", "점검", "확인"))
        )
    )
    if local_investigation:
        return FastToolPlan(
            intent="runtime_investigation",
            tool_name="runtime_investigation",
            mode="background",
            query=contextual,
            confidence=0.98,
            source="rule",
            reason="explicit local runtime investigation request",
        )

    explicit_search = _has_any(lowered, _SEARCH_MARKERS)
    contextual_search = bool(
        recent_topic
        and _has_any(lowered, _FOLLOWUP_MARKERS)
        and _has_any(contextual_lower, _SEARCH_MARKERS + _RESEARCH_MARKERS)
    )
    if not explicit_search and not contextual_search:
        return None

    research = _has_any(contextual_lower, _RESEARCH_MARKERS) or bool(
        "모델" in contextual_lower
        and _has_any(contextual_lower, ("알아봐", "알아 봐", "알아보", "찾아봐", "조사"))
    )
    if research:
        return FastToolPlan(
            intent="research_compare",
            tool_name="research_compare",
            mode="background",
            query=contextual,
            confidence=0.96,
            source="rule",
            reason="research or comparison request",
        )
    return FastToolPlan(
        intent="web_search",
        tool_name="web_search",
        mode="inline",
        query=contextual,
        confidence=0.94,
        source="rule",
        reason="explicit web search request",
    )


def _looks_ambiguous_tool_followup(text: str, recent_messages: list[dict[str, Any]]) -> bool:
    normalized = clean_text(text).lower()
    return bool(
        _best_recent_topic(recent_messages)
        and len(normalized) <= 36
        and _has_any(normalized, _FOLLOWUP_MARKERS + ("해줘", "해 줘", "봐줘", "봐 줘", "확인해"))
    )


def _parse_router_json(text: str) -> dict[str, Any] | None:
    raw = str(text or "").strip()
    if not raw:
        return None
    raw = re.sub(r"^```(?:json)?\s*|\s*```$", "", raw, flags=re.IGNORECASE)
    match = re.search(r"\{.*\}", raw, flags=re.DOTALL)
    if not match:
        return None
    try:
        value = json.loads(match.group(0))
    except Exception:
        return None
    return value if isinstance(value, dict) else None


async def default_router_provider(text: str, recent_messages: list[dict[str, Any]]) -> dict[str, Any] | None:
    recent = [
        {
            "role": clean_text(item.get("role")) or "user",
            "content": clean_text(item.get("content") or item.get("text")),
        }
        for item in recent_messages[-6:]
        if clean_text(item.get("content") or item.get("text"))
    ]
    system = (
        "Classify an ambiguous Evelyn tool request. Return one JSON object only. "
        "Allowed tool values: web_search, research_compare, runtime_investigation, "
        "runtime_status, runtime_log_read, memory_recall, ask_clarification, none. "
        "Never choose restart, shutdown, microphone, audio, filesystem, browser, or game actions. "
        'Schema: {"intent":"...", "tool":"...", "query":"...", "confidence":0.0, "reason":"..."}.'
    )
    payload = {
        "model": ROUTER_LLM_MODEL,
        "messages": [
            {"role": "system", "content": system},
            *recent,
            {"role": "user", "content": text},
        ],
        "temperature": 0.0,
        "max_tokens": 180,
        "stream": False,
        "response_format": {"type": "json_object"},
    }
    try:
        async with ClientSession(timeout=ClientTimeout(total=5.0)) as session:
            async with session.post(ROUTER_LLM_URL, json=payload) as response:
                if response.status != 200:
                    return None
                data = await response.json(content_type=None)
    except Exception:
        return None
    choices = data.get("choices") or []
    content = clean_text(((choices[0].get("message") or {}).get("content")) if choices else "")
    return _parse_router_json(content)


async def plan_fast_tool_request(
    text: str,
    *,
    recent_messages: list[dict[str, Any]] | None = None,
    router_provider: RouterProvider | None = None,
) -> FastToolPlan | None:
    recent = [dict(item) for item in list(recent_messages or []) if isinstance(item, dict)]
    rule_plan = _plan_from_rules(text, recent)
    if rule_plan is not None:
        return rule_plan
    if not _looks_ambiguous_tool_followup(text, recent):
        return None

    normalized = normalize_stt_tool_text(text, recent_messages=recent)
    routed = await (router_provider or default_router_provider)(normalized, recent)
    if not isinstance(routed, dict):
        return None
    tool_name = clean_text(routed.get("tool")).lower()
    if tool_name not in ROUTER_ALLOWED_TOOLS or tool_name in {"none", "ask_clarification"}:
        return None
    capability = FAST_TOOL_CAPABILITY_BY_NAME.get(tool_name)
    if capability is None or capability.risk not in {"read_only_external", "read_only_local"}:
        return None
    try:
        confidence = float(routed.get("confidence") or 0.0)
    except (TypeError, ValueError):
        confidence = 0.0
    if confidence < 0.72:
        return None
    query = clean_text(routed.get("query")) or contextual_tool_query(normalized, recent)
    return FastToolPlan(
        intent=clean_text(routed.get("intent")) or tool_name,
        tool_name=tool_name,
        mode=capability.mode,
        query=query,
        confidence=min(1.0, max(0.0, confidence)),
        source="router_llm",
        reason=clean_text(routed.get("reason")),
    )


def enforce_registered_tool_capability_truth(reply: str) -> str:
    normalized = clean_text(reply)
    if normalized and _FALSE_WEB_UNAVAILABILITY_RE.search(normalized):
        return (
            "웹 검색 도구는 연결돼 있어. 방금 요청이 검색으로 인식되지 않았거나 "
            "검색 실행 결과가 전달되지 않은 거야."
        )
    return normalized
