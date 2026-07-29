from __future__ import annotations

from dataclasses import dataclass
import asyncio
import inspect
import os
from pathlib import Path
import re
import time
from typing import Any, Awaitable, Callable

from .context_pipeline import (
    ContextBuilder,
    ContextPolicy,
    ToolUseDecision,
    build_basic_context_packet,
    build_context_policy_for_turn,
    build_runtime_state_context,
    build_tool_use_decisions,
    render_tool_use_context,
)
from .fast_action_runtime import compact_local_bridge_context
from .fast_tool_planner import render_fast_tool_registry_context
from .runtime_health import collect_runtime_health
from .runtime_services import load_service_manifest
from .text import clean_text


RuntimeHealthProvider = Callable[[], Awaitable[dict[str, Any]]]
SearchProvider = Callable[[str], Awaitable[tuple[str, list[dict[str, Any]]]]]
MemoryProvider = Callable[[str], Awaitable[str]]
LogProvider = Callable[[str], Awaitable[str]]
LocalBridgeStatusProvider = Callable[[], Any]


@dataclass(slots=True)
class FastControlContext:
    policy: ContextPolicy
    tool_use_decisions: list[ToolUseDecision]
    system_context: str
    search_context: str = ""
    memory_context: str = ""
    log_context: str = ""
    local_bridge_context: str = ""


def compact_runtime_health_for_llm(health: dict[str, Any]) -> str:
    services = [dict(item) for item in health.get("services") or [] if isinstance(item, dict)]
    down_services = [
        f"{service.get('id')}={service.get('state')}:{service.get('reason')}"
        for service in services
        if service.get("state") != "up"
    ]
    ready_services = [str(service.get("id")) for service in services if service.get("state") == "up"]
    diagnostics = [
        clean_text(str(item.get("message") or item.get("code") or ""))
        for item in health.get("diagnostics") or []
        if isinstance(item, dict)
    ]
    lines = [
        f"runtime_overall={clean_text(str(health.get('overallState') or 'unknown'))}",
        f"runtime_summary={clean_text(str(health.get('summary') or ''))}",
    ]
    if ready_services:
        lines.append("ready_services=" + ",".join(ready_services[:12]))
    if down_services:
        lines.append("limited_services=" + "; ".join(down_services[:8]))
    if diagnostics:
        lines.append("diagnostics=" + " | ".join(diagnostics[:4]))
    return "\n".join(line for line in lines if clean_text(line))


async def default_runtime_health_provider() -> dict[str, Any]:
    return await collect_runtime_health(manifest=load_service_manifest())


def build_fast_route_capability_context() -> str:
    return "\n".join(
        (
            "fast_control_route=python -m evelyn_core.fast_control_api",
            "full_main_route=main.py prepare_llm_messages context pipeline is mirrored here through shared context_pipeline policy decisions.",
            render_fast_tool_registry_context(),
            "pre_llm_commands=/help,/status,/memory,/voice status,/mic status,/mic on,/mic off,/minecraft status,/inventory,/voyager stats,/minecraft disconnect,/autonomy status,/restart,/shutdown,natural memory,microphone,Minecraft status,and scoped runtime controls",
            "action_execution_contract=inline tools finish before the answer; background acknowledgements require an active_action_id.",
            "background_followup_contract=registered long-running actions publish completion or failure to chat and /api/control-page/action-events.",
            "unsupported_inline_tools=vision_capture_or_watch,vision_ocr,host_arbitrary_file_read",
            "unsupported_tool_rule=if a required unsupported tool is needed and no evidence is present, say evidence is missing instead of pretending to have used it.",
        )
    )


async def default_search_provider(query: str) -> tuple[str, list[dict[str, Any]]]:
    from .search_tools import normalize_search_query, search_duckduckgo

    cleaned_query = normalize_search_query(query)
    results = await search_duckduckgo(cleaned_query)
    return cleaned_query, [result.to_dict() for result in results]


async def default_memory_provider(user_text: str) -> str:
    from .assistant_contracts import MemoryRecallRequest
    from .memory_vault import recall_memory_vault

    request = MemoryRecallRequest(
        turn_id=f"fast-control-{int(time.time() * 1000)}",
        session_key="control-page:local",
        guild_id=None,
        user_text=user_text,
        topic_id=None,
        source="fast_control_api",
        max_items=5,
        metadata={"active_project": "evelyn", "context_focus": ["control_page", "local_runtime"]},
    )
    result = await asyncio.to_thread(recall_memory_vault, request)
    if not result.ok:
        return f"Memory recall failed: {clean_text(result.error_text or 'unknown')}"
    return clean_text(result.context_text)


def default_log_roots() -> list[Path]:
    configured = clean_text(os.environ.get("EVELYN_FAST_LOG_ROOTS") or "")
    if configured:
        return [Path(item.strip()) for item in configured.split(os.pathsep) if item.strip()]
    return [
        Path("/app/runtime_artifacts/logs"),
        Path("/app/logs"),
        Path("runtime_artifacts/logs"),
        Path("logs"),
    ]


_LOG_SENSITIVE_PATTERN = re.compile(
    r"(?i)\b(token|api[_-]?key|secret|password|authorization)\b\s*[:=]\s*([^\s,;}]+)"
)
_LOG_INTEREST_PATTERN = re.compile(
    r"(?i)(error|exception|traceback|failed|failure|warn|warning|api_error|500|shutdown|"
    r"llm|tool|memory|search|bridge|tts|stt|vision|control|bot_api)"
)
_LOG_SUFFIXES = {".log", ".jsonl", ".txt"}
_LOG_REQUEST_PATTERN = re.compile(
    r"(?i)(log|logs|error|exception|traceback|failed|failure|api_error|500|"
    r"로그|에러|오류|실패|확인|진단|종료|shutdown)"
)
_LOG_EVENT_BOUNDARY_PATTERN = re.compile(r"\s+(?=\[[A-Z][A-Z0-9 _-]{2,}\])")
_LOG_CONVERSATION_LINE_PATTERN = re.compile(r"(?i)\b(transcript|reply)\s*=")
_VOICE_LOG_REQUEST_PATTERN = re.compile(r"(?i)(voice|stt|tts|mic|speaker|transcript|reply|음성|마이크|스피커|발화)")


def _redact_log_line(line: str) -> str:
    return _LOG_SENSITIVE_PATTERN.sub(r"\1=<redacted>", line)


def _decode_log_bytes(data: bytes) -> str:
    encodings = ["utf-8-sig", "utf-16", "utf-16-le", "cp949"]
    decoded_options: list[tuple[int, str]] = []
    for encoding in encodings:
        try:
            decoded = data.decode(encoding, errors="replace")
        except LookupError:
            continue
        score = decoded.count("\ufffd") * 4 + decoded.count("\x00")
        decoded_options.append((score, decoded))
    if not decoded_options:
        return data.decode("utf-8", errors="replace")
    return min(decoded_options, key=lambda item: item[0])[1]


def _split_decoded_log_lines(text: str) -> list[str]:
    text = _LOG_EVENT_BOUNDARY_PATTERN.sub("\n", text)
    return text.splitlines()


def _query_log_terms(user_text: str) -> list[str]:
    terms = []
    for token in re.findall(r"[A-Za-z0-9_\-/]{3,}|[가-힣]{2,}", user_text):
        lowered = token.lower().strip("-_/")
        if lowered in {
            "log",
            "logs",
            "로그",
            "확인",
            "봐줘",
            "파일",
            "오류",
            "에러",
            "문제",
            "원인",
            "원인을",
            "조사",
            "조사해봐",
            "찾아봐",
        }:
            continue
        terms.append(lowered)
    return terms[:8]


def is_mounted_log_read_request(user_text: str) -> bool:
    return bool(_LOG_REQUEST_PATTERN.search(clean_text(user_text)))


def _tail_log_file(path: Path, *, max_bytes: int = 65536, tail_lines: int = 80) -> list[str]:
    with path.open("rb") as handle:
        handle.seek(0, os.SEEK_END)
        size = handle.tell()
        start = max(0, size - max_bytes)
        if start % 2:
            start -= 1
        handle.seek(start, os.SEEK_SET)
        data = handle.read()
    text = _decode_log_bytes(data)
    return _split_decoded_log_lines(text)[-tail_lines:]


def build_fast_log_context(
    user_text: str,
    *,
    roots: list[Path] | None = None,
    max_files: int = 6,
    max_chars: int = 4000,
    require_match: bool = False,
) -> str:
    candidates_by_path: dict[Path, tuple[float, Path, Path]] = {}
    for root in roots or default_log_roots():
        root_path = Path(root)
        if not root_path.exists() or not root_path.is_dir():
            continue
        try:
            resolved_root = root_path.resolve()
        except OSError:
            continue
        for path in root_path.rglob("*"):
            if not path.is_file() or path.suffix.lower() not in _LOG_SUFFIXES:
                continue
            try:
                resolved_path = path.resolve()
                resolved_path.relative_to(resolved_root)
                stat = path.stat()
            except OSError:
                continue
            if stat.st_size <= 0:
                continue
            current = candidates_by_path.get(resolved_path)
            if current is None or stat.st_mtime > current[0]:
                candidates_by_path[resolved_path] = (stat.st_mtime, resolved_root, resolved_path)

    candidates = list(candidates_by_path.values())
    if not candidates:
        return ""

    query_terms = _query_log_terms(user_text)
    allow_conversation_lines = bool(_VOICE_LOG_REQUEST_PATTERN.search(user_text))
    sections: list[str] = []
    for mtime, root, path in sorted(candidates, reverse=True)[: max(1, max_files)]:
        try:
            lines = _tail_log_file(path)
        except OSError:
            continue
        nonempty = [clean_text(_redact_log_line(line)) for line in lines if clean_text(line)]
        if not allow_conversation_lines:
            nonempty = [line for line in nonempty if not _LOG_CONVERSATION_LINE_PATTERN.search(line)]
        if not nonempty:
            continue
        interesting = [
            line
            for line in nonempty
            if (
                any(term and term in line.lower() for term in query_terms)
                if require_match
                else (
                    _LOG_INTEREST_PATTERN.search(line)
                    or any(term and term in line.lower() for term in query_terms)
                )
            )
        ]
        if require_match and not interesting:
            continue
        selected = interesting[-16:] if interesting else nonempty[-10:]
        try:
            label = str(path.relative_to(root))
        except ValueError:
            label = str(path)
        section = "\n".join(
            [
                f"Recent Evelyn log evidence: {label} (mtime={time.strftime('%Y-%m-%d %H:%M:%S', time.localtime(mtime))})",
                *selected,
            ]
        )
        sections.append(section)
        if len("\n\n".join(sections)) >= max_chars:
            break
    return clean_text("\n\n".join(sections))[:max_chars]


async def default_log_provider(user_text: str) -> str:
    return await asyncio.to_thread(build_fast_log_context, user_text)


async def build_fast_control_context(
    user_text: str,
    *,
    source: str,
    tool_user_text: str | None = None,
    runtime_health_provider: RuntimeHealthProvider | None = None,
    search_provider: SearchProvider | None = None,
    memory_provider: MemoryProvider | None = None,
    log_provider: LogProvider | None = None,
    local_bridge_status_provider: LocalBridgeStatusProvider | None = None,
) -> FastControlContext:
    decision_text = clean_text(tool_user_text) or user_text
    policy = build_context_policy_for_turn(
        user_text=decision_text,
        source=source,
        route="fast_control_api",
    )
    decisions = build_tool_use_decisions(decision_text, policy)
    if any(decision.tool_name in {"vision_capture_or_watch", "vision_ocr"} for decision in decisions):
        policy.needs_vision = True
        policy.priority = "accuracy"
        if "tool_vision" not in policy.context_focus:
            policy.context_focus.append("tool_vision")
    if any(decision.tool_name == "web_current_info" for decision in decisions):
        policy.needs_search = True

    provider = runtime_health_provider or default_runtime_health_provider
    search_context = ""
    memory_context = ""
    log_context = ""
    local_bridge_context = ""
    for decision in decisions:
        if decision.tool_name == "runtime_status" and decision.auto_allowed:
            try:
                runtime_health = await provider()
                evidence = compact_runtime_health_for_llm(runtime_health)
                if local_bridge_status_provider is not None:
                    bridge_snapshot = local_bridge_status_provider()
                    if inspect.isawaitable(bridge_snapshot):
                        bridge_snapshot = await bridge_snapshot
                    local_bridge_context = compact_local_bridge_context(
                        bridge_snapshot if isinstance(bridge_snapshot, dict) else {}
                    )
                    evidence = "\n".join(
                        part for part in (evidence, local_bridge_context) if clean_text(part)
                    )
                decision.status = "executed" if clean_text(evidence) else "executed_empty"
                decision.evidence = clean_text(evidence)[:1200]
            except Exception as exc:
                decision.status = "failed"
                decision.evidence = clean_text(repr(exc))[:240]
        elif decision.tool_name == "local_file_or_log_read":
            if not decision.auto_allowed and not is_mounted_log_read_request(decision_text):
                decision.status = "needs_local_tool"
                decision.evidence = (
                    "Fast Control-Page chat can inspect mounted Evelyn logs only; "
                    "arbitrary host file/code reads still require the local Codex/OpenClaw repair path."
                )
                continue
            try:
                log_context = await (log_provider or default_log_provider)(decision_text)
                decision.status = "executed" if clean_text(log_context) else "executed_empty"
                decision.auto_allowed = True
                decision.evidence = (
                    clean_text(log_context)[:1000]
                    if log_context
                    else "No recent Evelyn log evidence was found in mounted log roots."
                )
            except Exception as exc:
                decision.status = "failed"
                decision.evidence = clean_text(repr(exc))[:240]
        elif decision.tool_name == "web_current_info":
            try:
                from .search_tools import render_search_results_for_llm

                query, results = await (search_provider or default_search_provider)(decision_text)
                search_context = render_search_results_for_llm(query, results)
                decision.status = "executed" if results else "executed_empty"
                decision.auto_allowed = True
                decision.evidence = clean_text(search_context)[:1000]
            except Exception as exc:
                decision.status = "failed"
                decision.evidence = clean_text(repr(exc))[:240]
        elif decision.tool_name == "memory_recall":
            try:
                memory_context = await (memory_provider or default_memory_provider)(decision_text)
                decision.status = "executed" if clean_text(memory_context) else "executed_empty"
                decision.evidence = clean_text(memory_context)[:1000] if memory_context else "No relevant memory was found."
            except Exception as exc:
                decision.status = "failed"
                decision.evidence = clean_text(repr(exc))[:240]
        elif decision.required_before_answer and not decision.auto_allowed:
            decision.evidence = decision.evidence or (
                "This tool is required before a verified answer, but it is not auto-executed in the fast control route."
            )

    runtime_context = "\n".join(
        part
        for part in (
            build_runtime_state_context(source=source, route="fast_control_api"),
            build_fast_route_capability_context(),
            local_bridge_context,
            log_context,
        )
        if clean_text(part)
    )
    packet = build_basic_context_packet(
        current_user_input="",
        memory_context=memory_context,
        runtime_state=runtime_context,
        conversation_state="route: fast_control_api",
        tool_context=render_tool_use_context(decisions),
        skill_context=search_context,
        policy=policy,
    )
    return FastControlContext(
        policy=policy,
        tool_use_decisions=decisions,
        system_context=ContextBuilder().render_system_context(packet),
        search_context=search_context,
        memory_context=memory_context,
        log_context=log_context,
        local_bridge_context=local_bridge_context,
    )


async def build_fast_main_llm_messages(
    *,
    base_system_prompt: str,
    recent_messages: list[dict[str, Any]],
    user_text: str,
    final_user_text: str,
    source: str,
    tool_user_text: str | None = None,
    runtime_health_provider: RuntimeHealthProvider | None = None,
    search_provider: SearchProvider | None = None,
    memory_provider: MemoryProvider | None = None,
    log_provider: LogProvider | None = None,
    local_bridge_status_provider: LocalBridgeStatusProvider | None = None,
) -> list[dict[str, Any]]:
    context = await build_fast_control_context(
        user_text,
        source=source,
        tool_user_text=tool_user_text,
        runtime_health_provider=runtime_health_provider,
        search_provider=search_provider,
        memory_provider=memory_provider,
        log_provider=log_provider,
        local_bridge_status_provider=local_bridge_status_provider,
    )
    system_content = "\n\n".join(
        part
        for part in (
            clean_text(base_system_prompt),
            context.system_context,
        )
        if clean_text(part)
    )
    return [
        {"role": "system", "content": system_content},
        *[dict(message) for message in recent_messages if isinstance(message, dict)],
        {"role": "user", "content": final_user_text},
    ]
