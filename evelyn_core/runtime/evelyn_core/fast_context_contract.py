from __future__ import annotations

from dataclasses import dataclass, field
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
    build_conversation_state_context,
    build_context_policy_for_turn,
    build_runtime_state_context,
    build_tool_use_decisions,
    has_unanswered_user_turn,
    render_tool_use_context,
)
from .config import MEMORY_ROOT
from .conversation_memory_exposure import (
    capture_combined_memory_exposure,
    memory_exposure_position_from_receipt,
)
from .fast_action_runtime import compact_local_bridge_context
from .fast_tool_planner import render_fast_tool_registry_context
from .host_vision_client import HostVisionResult, request_host_vision
from .memory_deletion_journal import (
    MemoryDeletionJournalBusyError,
    MemoryDeletionJournalIntegrityError,
    MemoryDeletionPosition,
    memory_deletion_ledger_note_id,
    memory_deletion_journal_guard,
    memory_deletion_journal_read_guard,
    memory_deletion_note_id_is_canonical,
)
from .memory_deletion_outbound import (
    capture_memory_deletion_outbound_position,
    current_memory_deletion_outbound_position,
    reset_memory_deletion_outbound_position,
)
from .memory_exposure import (
    MemoryExposurePosition,
    current_memory_exposure_position,
    memory_exposure_guard,
)
from .memory_confirmation_contract import memory_owner_scope
from .memory_prompt_policy import (
    MEMORY_CONTEXT_USE_POLICY,
    memory_deletion_boundary_from_position,
    memory_deletion_boundary_matches_position,
    memory_deletion_boundary_not_required,
    normalize_memory_deletion_boundary,
    normalize_memory_retrieval_mode,
    prepare_memory_context_for_prompt,
    reconcile_memory_receipt_for_prompt,
    validated_memory_grounding_state,
)
from .runtime_health import collect_runtime_health
from .runtime_services import load_service_manifest
from .text import clean_text
from .vision_runtime import VisionEvidence, vision_evidence_from_payload


RuntimeHealthProvider = Callable[[], Awaitable[dict[str, Any]]]
SearchProvider = Callable[[str], Awaitable[tuple[str, list[dict[str, Any]]]]]
MemoryProvider = Callable[[str], Awaitable[str | tuple[str, dict[str, Any]]]]
LogProvider = Callable[[str], Awaitable[str]]
LocalBridgeStatusProvider = Callable[[], Any]
VisionProvider = Callable[..., Awaitable[HostVisionResult]]
FAST_LOCAL_MEMORY_OWNER_SCOPE = memory_owner_scope(
    guild_id=None,
    person_key="control-page:local",
)


@dataclass(slots=True)
class FastControlContext:
    policy: ContextPolicy
    tool_use_decisions: list[ToolUseDecision]
    system_context: str
    search_context: str = ""
    memory_context: str = ""
    memory_receipt: dict[str, Any] = field(default_factory=dict)
    memory_deletion_position: MemoryDeletionPosition | None = None
    memory_exposure_position: MemoryExposurePosition | None = None
    log_context: str = ""
    local_bridge_context: str = ""
    vision_context: str = ""
    vision_evidence: VisionEvidence = field(default_factory=VisionEvidence)
    required_evidence_failure_reply: str = ""
    grounded_evidence_reply: str = ""
    unanswered_user_turn_context: bool = False


@dataclass(slots=True)
class FastMainLlmRequest:
    context: FastControlContext
    messages: list[dict[str, Any]]
    memory_deletion_position: MemoryDeletionPosition | None = None
    memory_exposure_position: MemoryExposurePosition | None = None


def build_required_evidence_failure_reply(
    decisions: list[ToolUseDecision],
    *,
    vision_evidence: VisionEvidence,
) -> str:
    failed_required = {
        decision.tool_name
        for decision in decisions
        if decision.required_before_answer
        and decision.status in {
            "failed",
            "failed_or_unavailable",
            "executed_empty",
        }
    }
    if "vision_ocr" in failed_required:
        if vision_evidence.scene_available:
            return (
                "화면 캡처는 됐지만 이번에는 글자를 읽을 수 있는 근거를 얻지 못했어. "
                "제목이나 버튼 이름은 추측하지 않을게."
            )
        return (
            "이번에는 화면의 글자를 확인할 수 없었어. "
            "제목이나 버튼 이름은 추측하지 않을게."
        )
    if "vision_capture_or_watch" in failed_required:
        return (
            "이번에는 화면을 확인할 수 없었어. "
            "보이는 내용을 추측하지 않을게."
        )
    return ""


def build_grounded_evidence_reply(
    user_text: str,
    *,
    vision_result: HostVisionResult | None,
) -> str:
    if (
        vision_result is None
        or vision_result.evidence.reason_code
        != "live_accessibility_observation"
        or not vision_result.evidence.satisfies_tool("vision_ocr")
    ):
        return ""
    normalized = clean_text(user_text).casefold()
    asks_exact_window_title = bool(
        "창 제목" in normalized
        or "window title" in normalized
    ) and any(
        marker in normalized
        for marker in ("정확", "그대로", "exact", "verbatim")
    )
    if not asks_exact_window_title:
        return ""
    match = re.search(
        r"foreground_window:\s*title=(.*?);\s*class=",
        vision_result.observation,
        flags=re.IGNORECASE,
    )
    if match is None:
        return ""
    title = clean_text(match.group(1))[:240]
    if not title or title == "<empty>":
        return ""
    return title


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
            "supported_inline_tools=vision_capture_or_watch,vision_ocr,runtime_status,runtime_log_read,memory_recall,web_search,datetime",
            "unsupported_inline_tools=host_arbitrary_file_read",
            "unsupported_tool_rule=if a required unsupported tool is needed and no evidence is present, say evidence is missing instead of pretending to have used it.",
            "action_execution_contract=inline tools finish before the answer; background acknowledgements require an active_action_id.",
            "background_followup_contract=registered long-running actions publish completion or failure to chat and /api/control-page/action-events.",
            "pre_llm_commands=/help,/status,/memory,/voice status,/mic status,/mic on,/mic off,/minecraft status,/inventory,/voyager stats,/minecraft disconnect,/autonomy status,/restart,/shutdown,natural memory,microphone,Minecraft status,and scoped runtime controls",
            render_fast_tool_registry_context(),
        )
    )


async def default_search_provider(query: str) -> tuple[str, list[dict[str, Any]]]:
    from .search_tools import normalize_search_query, search_duckduckgo

    cleaned_query = normalize_search_query(query)
    results = await search_duckduckgo(cleaned_query)
    return cleaned_query, [result.to_dict() for result in results]


def _fast_memory_context_receipt(
    value: dict[str, Any] | None,
    *,
    has_context: bool,
    position: MemoryDeletionPosition | None = None,
) -> dict[str, Any]:
    source = value if isinstance(value, dict) else {}
    raw_note_ids = source.get("suppliedNoteIds") or source.get("noteIds") or []
    note_ids: list[str] = []
    if isinstance(raw_note_ids, (list, tuple)):
        normalized_ids: set[str] = set()
        for item in raw_note_ids[:12]:
            raw_id = clean_text(str(item))
            if not raw_id:
                continue
            normalized_ids.add(
                raw_id
                if memory_deletion_note_id_is_canonical(raw_id)
                else memory_deletion_ledger_note_id(raw_id)
            )
        note_ids = sorted(normalized_ids)
    state = clean_text(str(source.get("state") or ("provided" if has_context else "empty")))
    if state not in {"provided", "empty", "unavailable", "withheld"}:
        state = "provided" if has_context else "empty"
    elif has_context:
        state = "provided"
    elif state in {"provided", "withheld"}:
        state = "empty"
    grounding_state = clean_text(str(source.get("groundingState") or ""))
    if grounding_state not in {"attributed", "partial", "unattributed", "empty", "unavailable"}:
        grounding_state = "attributed" if has_context and note_ids else ("unattributed" if has_context else state)
    try:
        memory_version = int(source.get("memoryVersion") or 0)
    except (TypeError, ValueError):
        memory_version = 0
    source_type_counts: dict[str, int] = {}
    raw_source_type_counts = source.get("sourceTypeCounts")
    if isinstance(raw_source_type_counts, dict):
        for source_type in ("system", "legacy", "derived", "conversation", "user", "runtime", "unknown"):
            try:
                count = int(raw_source_type_counts.get(source_type) or 0)
            except (TypeError, ValueError):
                count = 0
            if count > 0:
                source_type_counts[source_type] = count
    try:
        confirm_only_item_count = max(
            0,
            int(source.get("confirmOnlyItemCount") or 0),
        )
    except (TypeError, ValueError):
        confirm_only_item_count = 0
    if has_context and grounding_state in {"partial", "unattributed"}:
        confirm_only_item_count = max(1, confirm_only_item_count)
    elif grounding_state == "attributed":
        confirm_only_item_count = 0
    deletion_boundary = normalize_memory_deletion_boundary(
        source.get("deletionBoundary"),
        require_captured=False,
    )
    if (
        deletion_boundary.get("state") == "captured"
        and not memory_deletion_boundary_matches_position(
            deletion_boundary,
            position,
        )
    ):
        raise MemoryDeletionJournalIntegrityError()
    return {
        "schema": "memory.context-receipt.v1",
        "state": state,
        "groundingState": grounding_state,
        "usePolicy": MEMORY_CONTEXT_USE_POLICY,
        "confirmOnlyItemCount": confirm_only_item_count,
        "promptTruncated": False,
        "promptEvidenceDiscarded": False,
        "promptMemoryWithheld": False,
        "withheldItemCount": 0,
        "withheldNoteCount": 0,
        "withheldLegacyItemCount": 0,
        "preTruncationLegacyItemCount": 0,
        "preTruncationNoteCount": 0,
        "opaqueConfirmOnlyComponentCount": 0,
        "vaultState": state,
        "memoryVersion": memory_version,
        "retrievalMode": normalize_memory_retrieval_mode(
            source.get("retrievalMode")
        ),
        "cacheHit": bool(source.get("cacheHit")),
        "indexFresh": source.get("indexFresh") is True,
        "readOnlyFallback": (
            source.get("readOnlyFallback") is True
        ),
        "hotContextState": "not_requested",
        "suppliedNoteIds": note_ids,
        "suppliedNoteCount": len(note_ids),
        "sourceTypeCounts": source_type_counts,
        "legacyItemCounts": {},
        "legacyItemCount": 0,
        "legacyAttributedItemCount": 0,
        "legacyUnattributedItemCount": 0,
        "legacyConfirmOnlyItemCount": 0,
        "legacyEvidenceIds": [],
        "legacySourceEvidenceIds": [],
        "legacySourceTurnIds": [],
        "deletionBoundary": deletion_boundary,
        "contentFree": True,
    }


async def _default_memory_provider_result(
    user_text: str,
    *,
    owner_scope: str = FAST_LOCAL_MEMORY_OWNER_SCOPE,
) -> tuple[str, dict[str, Any]]:
    from .assistant_contracts import MemoryRecallRequest
    from .memory_vault import build_memory_recall_receipt, recall_memory_vault

    request = MemoryRecallRequest(
        turn_id=f"fast-control-{int(time.time() * 1000)}",
        session_key="control-page:local",
        guild_id=None,
        user_text=user_text,
        topic_id=None,
        source="fast_control_api",
        owner_scope=owner_scope,
        max_items=5,
        metadata={"active_project": "evelyn", "context_focus": ["control_page", "local_runtime"]},
    )
    def recall_at_verified_position() -> tuple[
        str,
        dict[str, Any],
        MemoryDeletionPosition | None,
    ]:
        def recall_at_position(
            position: MemoryDeletionPosition,
        ) -> tuple[
            str,
            dict[str, Any],
            MemoryDeletionPosition | None,
        ]:
            result = recall_memory_vault(request)
            context = clean_text(result.context_text) if result.ok else ""
            receipt = build_memory_recall_receipt(result)
            receipt["deletionBoundary"] = (
                memory_deletion_boundary_from_position(position)
                if context
                else memory_deletion_boundary_not_required()
            )
            return context, receipt, position if context else None

        entered = False
        try:
            with memory_deletion_journal_guard(
                MEMORY_ROOT / "memory_index",
                require_stable=True,
            ) as position:
                entered = True
                return recall_at_position(position)
        except MemoryDeletionJournalBusyError:
            if entered:
                raise
            with memory_deletion_journal_read_guard(
                MEMORY_ROOT / "memory_index",
                require_stable=True,
                allow_repair=False,
            ) as position:
                return recall_at_position(position)

    context, recall_receipt, position = await asyncio.to_thread(
        recall_at_verified_position
    )
    if position is not None:
        capture_memory_deletion_outbound_position(position)
    return context, _fast_memory_context_receipt(
        recall_receipt,
        has_context=bool(context),
        position=position,
    )


async def default_memory_provider(user_text: str) -> str:
    context, _receipt = await _default_memory_provider_result(user_text)
    return context


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
    memory_owner_scope: str = FAST_LOCAL_MEMORY_OWNER_SCOPE,
    tool_user_text: str | None = None,
    runtime_health_provider: RuntimeHealthProvider | None = None,
    search_provider: SearchProvider | None = None,
    memory_provider: MemoryProvider | None = None,
    log_provider: LogProvider | None = None,
    local_bridge_status_provider: LocalBridgeStatusProvider | None = None,
    vision_provider: VisionProvider | None = None,
) -> FastControlContext:
    reset_memory_deletion_outbound_position()
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
    memory_receipt = {
        "schema": "memory.context-receipt.v1",
        "state": "not_requested",
        "groundingState": "not_requested",
        "usePolicy": MEMORY_CONTEXT_USE_POLICY,
        "confirmOnlyItemCount": 0,
        "promptTruncated": False,
        "promptEvidenceDiscarded": False,
        "promptMemoryWithheld": False,
        "withheldItemCount": 0,
        "withheldNoteCount": 0,
        "withheldLegacyItemCount": 0,
        "preTruncationLegacyItemCount": 0,
        "preTruncationNoteCount": 0,
        "opaqueConfirmOnlyComponentCount": 0,
        "deletionBoundary": memory_deletion_boundary_not_required(),
        "contentFree": True,
    }
    log_context = ""
    local_bridge_context = ""
    vision_context = ""
    vision_evidence = VisionEvidence(
        state="unknown",
        reason_code="not_requested",
    )
    vision_result: HostVisionResult | None = None
    vision_decisions = [
        decision
        for decision in decisions
        if decision.tool_name in {"vision_capture_or_watch", "vision_ocr"}
    ]
    if vision_decisions:
        run_ocr = any(decision.tool_name == "vision_ocr" for decision in vision_decisions)
        try:
            with memory_exposure_guard():
                vision_result = await (vision_provider or request_host_vision)(
                    decision_text,
                    run_ocr=run_ocr,
                )
            if not isinstance(vision_result, HostVisionResult):
                raise TypeError("invalid_host_vision_result")
            vision_evidence = vision_evidence_from_payload(
                vision_result.evidence.to_dict(),
            )
            observation = (
                vision_result.observation
                if vision_evidence.state == "observed"
                else (
                    "Local screen observation was discarded because its evidence "
                    "was unavailable, stale, or invalid. Do not infer screen contents."
                )
            )
            vision_result = HostVisionResult(
                observation=observation,
                evidence=vision_evidence,
                error_code=vision_result.error_code,
                latency_ms=vision_result.latency_ms,
                screenshot_deleted=vision_result.screenshot_deleted,
                scene_chars=vision_result.scene_chars,
                ocr_chars=vision_result.ocr_chars,
            )
        except MemoryDeletionJournalIntegrityError:
            raise
        except Exception:
            vision_result = None
            vision_evidence = VisionEvidence(
                state="failed",
                reason_code="host_vision_runtime_error",
            )
            observation = (
                "Local screen vision failed before a usable observation was produced. "
                "Do not claim the screen was analyzed."
            )
        vision_context = "\n\n".join(
            (
                "VISION_ANSWER_RULE: This turn requested live screen evidence. "
                "Only a vision.evidence.v2 result with evidence_available=true, freshness=live, "
                "and an unexpired timestamp counts as an "
                "observation. A request, capture attempt, or failure message is not evidence. "
                "When evidence is unavailable, say the screen could not be observed and do not "
                "infer its contents.",
                clean_text(observation),
                "VISION_EVIDENCE_GATE: " + vision_evidence.provenance_summary(),
            )
        )
        for decision in vision_decisions:
            decision.status = (
                "executed"
                if vision_evidence.satisfies_tool(decision.tool_name)
                else "failed_or_unavailable"
            )
            decision.evidence = vision_evidence.provenance_summary(
                tool_name=decision.tool_name
            )
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

                with memory_exposure_guard():
                    query, results = await (
                        search_provider or default_search_provider
                    )(decision_text)
                search_context = render_search_results_for_llm(query, results)
                decision.status = "executed" if results else "executed_empty"
                decision.auto_allowed = True
                decision.evidence = clean_text(search_context)[:1000]
            except MemoryDeletionJournalIntegrityError:
                raise
            except Exception as exc:
                decision.status = "failed"
                decision.evidence = clean_text(repr(exc))[:240]
        elif decision.tool_name == "memory_recall":
            try:
                if memory_provider is None:
                    memory_context, memory_receipt = (
                        await _default_memory_provider_result(
                            decision_text,
                            owner_scope=memory_owner_scope,
                        )
                    )
                else:
                    provider_result = await memory_provider(decision_text)
                    if (
                        isinstance(provider_result, tuple)
                        and len(provider_result) == 2
                        and isinstance(provider_result[1], dict)
                    ):
                        memory_context = clean_text(str(provider_result[0] or ""))
                        memory_receipt = _fast_memory_context_receipt(
                            provider_result[1],
                            has_context=bool(memory_context),
                            position=(
                                current_memory_deletion_outbound_position()
                            ),
                        )
                    else:
                        memory_context = clean_text(str(provider_result or ""))
                        memory_receipt = _fast_memory_context_receipt(
                            {
                                "state": "provided" if memory_context else "empty",
                                "groundingState": "unattributed" if memory_context else "empty",
                            },
                            has_context=bool(memory_context),
                            position=(
                                current_memory_deletion_outbound_position()
                            ),
                        )
                decision.status = (
                    "failed_or_unavailable"
                    if memory_receipt["state"] == "unavailable"
                    else "executed"
                    if memory_context
                    else "executed_empty"
                )
                decision.evidence = (
                    f"memory_context_chars={len(memory_context)}; "
                    f"receipt_state={memory_receipt['state']}; "
                    f"grounding={memory_receipt['groundingState']}; "
                    f"note_count={memory_receipt['suppliedNoteCount']}"
                )
            except MemoryDeletionJournalIntegrityError:
                reset_memory_deletion_outbound_position()
                raise
            except Exception:
                memory_context = ""
                reset_memory_deletion_outbound_position()
                memory_receipt = {
                    "schema": "memory.context-receipt.v1",
                    "state": "unavailable",
                    "groundingState": "unavailable",
                    "usePolicy": MEMORY_CONTEXT_USE_POLICY,
                    "confirmOnlyItemCount": 0,
                    "promptTruncated": False,
                    "promptEvidenceDiscarded": False,
                    "promptMemoryWithheld": False,
                    "withheldItemCount": 0,
                    "withheldNoteCount": 0,
                    "withheldLegacyItemCount": 0,
                    "preTruncationLegacyItemCount": 0,
                    "preTruncationNoteCount": 0,
                    "opaqueConfirmOnlyComponentCount": 0,
                    "deletionBoundary": (
                        memory_deletion_boundary_not_required()
                    ),
                    "contentFree": True,
                }
                decision.status = "failed"
                decision.evidence = "memory_recall_runtime_error"
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
    memory_grounding_state = validated_memory_grounding_state(
        memory_receipt,
        has_context=bool(clean_text(memory_context)),
    )
    memory_prompt_boundary = prepare_memory_context_for_prompt(
        memory_context,
        grounding_state=memory_grounding_state,
    )
    reconcile_memory_receipt_for_prompt(memory_receipt, memory_prompt_boundary)
    prompt_memory_context = memory_prompt_boundary.context
    if (
        prompt_memory_context
        and not memory_prompt_boundary.evidence_withheld
    ):
        deletion_boundary = normalize_memory_deletion_boundary(
            memory_receipt.get("deletionBoundary"),
            require_captured=True,
        )
        if not memory_deletion_boundary_matches_position(
            deletion_boundary,
            current_memory_deletion_outbound_position(),
        ):
            raise MemoryDeletionJournalIntegrityError()
    current_exposure = memory_exposure_position_from_receipt(
        memory_receipt,
        deletion_position=current_memory_deletion_outbound_position(),
        required=bool(
            prompt_memory_context
            and not memory_prompt_boundary.evidence_withheld
        ),
    )
    combined_exposure = capture_combined_memory_exposure(
        current_memory_exposure_position(),
        current_exposure,
    )
    for decision in decisions:
        if decision.tool_name != "memory_recall" or decision.status == "failed":
            continue
        if memory_receipt.get("state") == "withheld":
            decision.status = "executed_withheld"
        decision.evidence = (
            f"memory_context_chars={len(prompt_memory_context)}; "
            f"receipt_state={memory_receipt['state']}; "
            f"grounding={memory_receipt['groundingState']}; "
            f"note_count={memory_receipt.get('suppliedNoteCount', 0)}; "
            f"confirm_only_count={memory_receipt.get('confirmOnlyItemCount', 0)}; "
            f"prompt_truncated={str(bool(memory_receipt.get('promptTruncated'))).lower()}; "
            f"prompt_withheld={str(bool(memory_receipt.get('promptMemoryWithheld'))).lower()}; "
            f"withheld_count={memory_receipt.get('withheldItemCount', 0)}"
        )
    packet = build_basic_context_packet(
        current_user_input="",
        memory_context=prompt_memory_context,
        runtime_state=runtime_context,
        conversation_state="route: fast_control_api",
        tool_context=render_tool_use_context(decisions),
        skill_context=search_context,
        vision_context=vision_context,
        policy=policy,
    )
    return FastControlContext(
        policy=policy,
        tool_use_decisions=decisions,
        system_context=ContextBuilder().render_system_context(packet),
        search_context=search_context,
        memory_context=prompt_memory_context,
        memory_receipt=memory_receipt,
        memory_deletion_position=(
            current_memory_deletion_outbound_position()
        ),
        memory_exposure_position=combined_exposure,
        log_context=log_context,
        local_bridge_context=local_bridge_context,
        vision_context=vision_context,
        vision_evidence=vision_evidence,
        required_evidence_failure_reply=build_required_evidence_failure_reply(
            decisions,
            vision_evidence=vision_evidence,
        ),
        grounded_evidence_reply=build_grounded_evidence_reply(
            decision_text,
            vision_result=vision_result,
        ),
    )


async def build_fast_main_llm_request(
    *,
    base_system_prompt: str,
    recent_messages: list[dict[str, Any]],
    user_text: str,
    final_user_text: str,
    source: str,
    memory_owner_scope: str = FAST_LOCAL_MEMORY_OWNER_SCOPE,
    tool_user_text: str | None = None,
    runtime_health_provider: RuntimeHealthProvider | None = None,
    search_provider: SearchProvider | None = None,
    memory_provider: MemoryProvider | None = None,
    log_provider: LogProvider | None = None,
    local_bridge_status_provider: LocalBridgeStatusProvider | None = None,
    vision_provider: VisionProvider | None = None,
) -> FastMainLlmRequest:
    context = await build_fast_control_context(
        user_text,
        source=source,
        memory_owner_scope=memory_owner_scope,
        tool_user_text=tool_user_text,
        runtime_health_provider=runtime_health_provider,
        search_provider=search_provider,
        memory_provider=memory_provider,
        log_provider=log_provider,
        local_bridge_status_provider=local_bridge_status_provider,
        vision_provider=vision_provider,
    )
    context.unanswered_user_turn_context = has_unanswered_user_turn(
        recent_messages
    )
    if context.unanswered_user_turn_context:
        continuity_context = build_conversation_state_context(
            unanswered_user_turn=True,
        )
        context.system_context = "\n\n".join(
            part
            for part in (context.system_context, continuity_context)
            if clean_text(part)
        )
    system_content = "\n\n".join(
        part
        for part in (
            clean_text(base_system_prompt),
            context.system_context,
        )
        if clean_text(part)
    )
    return FastMainLlmRequest(
        context=context,
        messages=[
            {"role": "system", "content": system_content},
            *[
                dict(message)
                for message in recent_messages
                if isinstance(message, dict)
            ],
            {"role": "user", "content": final_user_text},
        ],
        memory_deletion_position=context.memory_deletion_position,
        memory_exposure_position=context.memory_exposure_position,
    )


async def build_fast_main_llm_messages(
    *,
    base_system_prompt: str,
    recent_messages: list[dict[str, Any]],
    user_text: str,
    final_user_text: str,
    source: str,
    memory_owner_scope: str = FAST_LOCAL_MEMORY_OWNER_SCOPE,
    tool_user_text: str | None = None,
    runtime_health_provider: RuntimeHealthProvider | None = None,
    search_provider: SearchProvider | None = None,
    memory_provider: MemoryProvider | None = None,
    log_provider: LogProvider | None = None,
    local_bridge_status_provider: LocalBridgeStatusProvider | None = None,
    vision_provider: VisionProvider | None = None,
) -> list[dict[str, Any]]:
    request = await build_fast_main_llm_request(
        base_system_prompt=base_system_prompt,
        recent_messages=recent_messages,
        user_text=user_text,
        final_user_text=final_user_text,
        source=source,
        memory_owner_scope=memory_owner_scope,
        tool_user_text=tool_user_text,
        runtime_health_provider=runtime_health_provider,
        search_provider=search_provider,
        memory_provider=memory_provider,
        log_provider=log_provider,
        local_bridge_status_provider=local_bridge_status_provider,
        vision_provider=vision_provider,
    )
    return request.messages
