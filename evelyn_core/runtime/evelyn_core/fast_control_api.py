from __future__ import annotations

import asyncio
from contextvars import ContextVar
import json
import os
import random
import re
import time
from typing import Any, AsyncIterator, Awaitable, Callable

from aiohttp import ClientSession, ClientTimeout, web

from .control_page_http import reject_browser_origin_middleware
from .assistant_prompt_contract import (
    FAST_MAIN_LLM_USER_PREFIX,
    build_evelyn_system_prompt,
    build_fast_main_llm_user_text,
)
from .control_page_contracts import (
    build_control_page_panel_state_payload,
    build_fast_control_default_commands,
    build_fast_control_help_reply,
    detect_memory_panel_action,
    local_restart_requested_reply,
    local_shutdown_requested_reply,
    memory_panel_reply,
)
from .context_pipeline import build_context_policy_for_turn, build_tool_use_decisions
from .continuity_commit_contract import (
    require_durable_continuity_receipt,
)
from .fast_context_contract import build_fast_main_llm_request
from .fast_action_runtime import (
    FastActionCoordinator,
    FastActionExecutionError,
    FastActionTask,
    SafeIncrementalSpeechFilter,
    detect_local_mic_command,
    detect_local_runtime_command,
    detect_minecraft_control_command,
    detect_minecraft_runtime_command,
    enforce_action_reply_contract,
    has_unbacked_progress_claim,
    is_local_mic_status_request,
    render_local_mic_status,
)
from .fast_action_recovery import (
    FAST_ACTION_RECOVERY_NOTICE,
    FAST_ACTION_RECOVERY_SCHEMA,
    FastActionRecoveryJournal,
)
from .fast_tool_planner import (
    FastToolPlan,
    answer_fast_tool_capability_question,
    enforce_registered_tool_capability_truth,
    plan_fast_tool_request,
)
from .fast_control_continuity import (
    FastControlContinuityOwner,
)
from .explicit_memory_confirmation import (
    execute_explicit_memory_confirmation,
)
from .cross_surface_continuity import (
    CrossSurfaceContinuityBridge,
    CrossSurfaceContinuityConfig,
)
from .paths import get_runtime_artifacts_root
from .public_error_contract import (
    public_error_code,
    public_failure_message,
)
from .minecraft_mode_composition import (
    MinecraftModeComposition,
    MinecraftModeCompositionDeps,
)
from .host_ui_action_client import (
    apply_host_ui_action,
    discover_host_ui_action,
    preview_host_ui_action,
)
from .minecraft_world_lease import MinecraftWorldLeaseOwner
from .minecraft_world_lease_delegation import (
    MINECRAFT_WORLD_LEASE_DELEGATION_TOKEN_HEADER,
    execute_minecraft_world_lease_delegation,
    minecraft_world_lease_delegation_authorized,
    minecraft_world_lease_delegation_error_code,
)
from .minecraft_world_lease_http_runtime import (
    MinecraftWorldLeaseHttpRuntime,
)
from .memory_prompt_policy import MEMORY_CONTEXT_USE_POLICY
from .query_intents import answer_current_datetime_query
from .runtime_health import (
    collect_runtime_health,
    default_probe_runner,
    public_runtime_health_snapshot,
)
from .runtime_health_snapshot_cache import (
    RuntimeHealthSnapshotCache,
)
from .runtime_services import HealthProbeSpec, ServiceSpec, load_service_manifest
from .text import (
    ModelStreamPrefixFilter,
    should_suppress_tts_for_command,
    visible_text as shared_visible_text,
)


HOST = os.getenv("CONTROL_PAGE_HOST", "0.0.0.0")
PORT = int(os.getenv("CONTROL_PAGE_PORT", os.getenv("CONTROL_PAGE_BOT_API_PORT", "8798")))
PUBLIC_CONTROL_PORT = int(os.getenv("CONTROL_PAGE_PUBLIC_PORT", "8799"))
MINECRAFT_WORLD_LEASE_OWNER_ENABLED = (
    os.getenv("MINECRAFT_WORLD_LEASE_OWNER_ENABLED", "").strip().lower()
    in {"1", "true", "yes", "on"}
    or os.getenv("EVELYN_FAST_BOOT", "").strip() == "1"
)
LLM_SERVER_URL = os.getenv("LLM_SERVER_URL", "http://127.0.0.1:9820/v1/chat/completions")
MODEL_NAME = os.getenv("LLM_MODEL_NAME", "google-gemma-4-12B-it-IQ4_XS.gguf")
MAIN_LLM_STOP_TOKENS = tuple(
    token.strip()
    for token in os.getenv("MAIN_LLM_STOP_TOKENS", "<|eot_id|>,<|end_of_text|>").split(",")
    if token.strip()
)
MEMORY_RECALL_PROGRESS_TEXTS = (
    "잠깐만.",
    "잠시만.",
    "음… 기다려봐.",
    "어디 보자…",
    "잠깐 기다려봐.",
)
MEMORY_RECALL_PROGRESS_SOURCES = frozenset({"local_bridge", "local_mic", "voice"})
MEMORY_RECALL_PROGRESS_LAST_TEXT: str | None = None
FAST_MEMORY_CONTEXT_RECEIPT: ContextVar[dict[str, Any] | None] = ContextVar(
    "fast_memory_context_receipt",
    default=None,
)
RESEARCH_PROGRESS_TEXTS = (
    "잠깐, 관련 자료를 찾아볼게.",
    "음… 제대로 비교해볼게.",
    "잠시만, 필요한 자료부터 모아볼게.",
)
INVESTIGATION_PROGRESS_TEXTS = (
    "잠깐, 상태와 로그를 확인해볼게.",
    "음… 문제 원인을 좀 찾아볼게.",
    "잠시만, 실제 상태부터 점검해볼게.",
)
MINECRAFT_LAZY_START_TIMEOUT_SEC = max(
    30.0,
    float(os.getenv("MINECRAFT_LAZY_START_TIMEOUT_SEC", "300")),
)
MINECRAFT_AUTONOMY_SERVICE_HOST = os.getenv("MINECRAFT_AUTONOMY_SERVICE_HOST", "voyager")
MINECRAFT_AUTONOMY_SERVICE_PORT = int(os.getenv("MINECRAFT_AUTONOMY_SERVICE_PORT", "8765"))
MINECRAFT_AUTONOMY_SERVICE_BASE = (
    f"http://{MINECRAFT_AUTONOMY_SERVICE_HOST}:{MINECRAFT_AUTONOMY_SERVICE_PORT}"
)
MINECRAFT_CONTROL_TIMEOUT_SEC = max(
    0.5,
    float(os.getenv("MINECRAFT_CONTROL_TIMEOUT_SEC", "2.5")),
)
FAST_RUNTIME_HEALTH_REFRESH_SEC = max(
    0.5,
    float(
        os.getenv(
            "FAST_RUNTIME_HEALTH_REFRESH_SEC",
            "2.0",
        )
    ),
)
FAST_RUNTIME_HEALTH_MAX_STALE_SEC = max(
    FAST_RUNTIME_HEALTH_REFRESH_SEC,
    float(
        os.getenv(
            "FAST_RUNTIME_HEALTH_MAX_STALE_SEC",
            "6.0",
        )
    ),
)
CHAT_LOG_LIMIT = max(4, int(os.getenv("FAST_CONTROL_CHAT_LOG_LIMIT", "40")))
FAST_CONTROL_CONTINUITY_ENABLED = (
    os.getenv(
        "FAST_CONTROL_CONTINUITY_ENABLED",
        "",
    ).strip().lower()
    in {"1", "true", "yes", "on"}
)
LOCAL_BRIDGE_STALE_AFTER_SEC = max(3.0, float(os.getenv("LOCAL_BRIDGE_STALE_AFTER_SEC", "8.0")))
FAST_MAIN_LLM_SYSTEM_PROMPT = "\n".join(
    (
        build_evelyn_system_prompt(),
        FAST_MAIN_LLM_USER_PREFIX,
    )
)


BOOT_STEPS = (
    ("control_page", "Control-Page"),
    ("bot_api", "Bot API"),
    ("main_llm", "Main LLM"),
    ("router_llm", "Router LLM"),
    ("sub_llm", "Sub LLM"),
    ("tts", "TTS"),
    ("stt", "STT"),
)

FAST_CONTROL_CONTINUITY_OWNER = FastControlContinuityOwner(
    artifacts_root=get_runtime_artifacts_root(),
    enabled=FAST_CONTROL_CONTINUITY_ENABLED,
)
FAST_ACTION_RECOVERY_JOURNAL = FastActionRecoveryJournal(
    path=(
        get_runtime_artifacts_root()
        / "fast_control_actions"
        / "recovery.json"
    ),
    enabled=FAST_CONTROL_CONTINUITY_ENABLED,
)
CROSS_SURFACE_CONTINUITY_BRIDGE = CrossSurfaceContinuityBridge(
    artifacts_root=get_runtime_artifacts_root(),
    config=CrossSurfaceContinuityConfig.from_env(),
)
CHAT_MESSAGES: list[dict[str, Any]] = (
    FAST_CONTROL_CONTINUITY_OWNER.restored_chat_messages()[
        -CHAT_LOG_LIMIT:
    ]
)
ACTION_COORDINATOR = FastActionCoordinator(history_limit=CHAT_LOG_LIMIT)
BACKGROUND_ACTION_HANDLERS: list[dict[str, Any]] = []
BACKGROUND_ACTION_TASKS: set[asyncio.Task[Any]] = set()
CONTROL_PAGE_UI_COMMANDS: list[dict[str, Any]] = []
CONTROL_PAGE_UI_COMMAND_SEQ = 0
LOCAL_BRIDGE_STATUS: dict[str, Any] = {
    "enabled": False,
    "ready": False,
    "mode": "windows_io_bridge",
}
LOCAL_BRIDGE_SPEAK_QUEUE: list[dict[str, Any]] = []
LOCAL_BRIDGE_SPEAK_SEQ = 0
LOCAL_AUDIO_DEVICE_STATE_PATH = get_runtime_artifacts_root() / "state" / "local_audio_devices.json"
SHUTDOWN_REQUEST: dict[str, Any] = {
    "requested": False,
    "requestedAt": None,
    "source": "",
    "reason": "",
}
RESTART_REQUEST: dict[str, Any] = {
    "requested": False,
    "requestedAt": None,
    "source": "",
    "reason": "",
}


def load_local_audio_device_state() -> dict[str, Any]:
    try:
        with LOCAL_AUDIO_DEVICE_STATE_PATH.open("r", encoding="utf-8") as handle:
            data = json.load(handle)
    except Exception:
        return {"outputDevice": "", "requestedAt": None, "source": "", "revision": 0}
    if not isinstance(data, dict):
        return {"outputDevice": "", "requestedAt": None, "source": "", "revision": 0}
    try:
        revision = int(data.get("revision") or 0)
    except Exception:
        revision = 0
    return {
        "outputDevice": re.sub(r"\s+", " ", str(data.get("outputDevice") or "").strip()),
        "requestedAt": data.get("requestedAt") if isinstance(data.get("requestedAt"), (int, float)) else None,
        "source": re.sub(r"\s+", " ", str(data.get("source") or "").strip()),
        "revision": max(0, revision),
    }


def save_local_audio_device_state(state: dict[str, Any]) -> None:
    LOCAL_AUDIO_DEVICE_STATE_PATH.parent.mkdir(parents=True, exist_ok=True)
    with LOCAL_AUDIO_DEVICE_STATE_PATH.open("w", encoding="utf-8") as handle:
        json.dump(state, handle, ensure_ascii=False, indent=2)


LOCAL_BRIDGE_OUTPUT_DEVICE_REQUEST: dict[str, Any] = load_local_audio_device_state()
LOCAL_BRIDGE_MIC_CONTROL_REQUEST: dict[str, Any] = {
    "revision": 0,
    "enabled": None,
    "requestedAt": None,
    "source": "",
}
LOCAL_BRIDGE_MINECRAFT_COMMAND_REQUEST: dict[str, Any] = {
    "revision": 0,
    "command": "",
    "action": "",
    "requestedAt": None,
    "source": "",
}


def json_response(payload: dict[str, Any], *, status: int = 200) -> web.Response:
    return web.json_response(payload, status=status)


def clean_text(value: Any) -> str:
    return re.sub(r"\s+", " ", str(value or "").strip())


def should_emit_memory_recall_progress(text: str, *, source: str) -> bool:
    if clean_text(source).lower() not in MEMORY_RECALL_PROGRESS_SOURCES:
        return False
    policy = build_context_policy_for_turn(
        user_text=text,
        source=source,
        route="fast_control_api",
    )
    return any(
        decision.tool_name == "memory_recall"
        and decision.auto_allowed
        and decision.required_before_answer
        for decision in build_tool_use_decisions(text, policy)
    )


def next_memory_recall_progress_text() -> str:
    global MEMORY_RECALL_PROGRESS_LAST_TEXT
    candidates = tuple(
        text
        for text in MEMORY_RECALL_PROGRESS_TEXTS
        if text != MEMORY_RECALL_PROGRESS_LAST_TEXT
    )
    text = random.choice(candidates or MEMORY_RECALL_PROGRESS_TEXTS)
    MEMORY_RECALL_PROGRESS_LAST_TEXT = text
    return text


def visible_text(value: Any) -> str:
    text = str(value or "")
    text = re.sub(
        r"<\|channel\>\s*(?:thought|analysis|reasoning)\b.*?<channel\|>\s*",
        " ",
        text,
        flags=re.IGNORECASE | re.DOTALL,
    )
    text = re.sub(r"<\|channel\>\s*(?:final|model|answer|content)\s*<channel\|>", " ", text, flags=re.IGNORECASE)
    text = re.sub(r"<\|channel\>|<channel\|>|</?think>", " ", text, flags=re.IGNORECASE)
    text = re.sub(r"^\s*\[(?:찾기|질문|대기|응답)\]\s*", "", text, flags=re.IGNORECASE)
    return clean_text(shared_visible_text(text))


def reset_fast_memory_context_receipt() -> dict[str, Any]:
    receipt = {
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
        "contentFree": True,
    }
    FAST_MEMORY_CONTEXT_RECEIPT.set(receipt)
    return dict(receipt)


def current_fast_memory_context_receipt() -> dict[str, Any]:
    receipt = FAST_MEMORY_CONTEXT_RECEIPT.get()
    return dict(receipt) if isinstance(receipt, dict) else reset_fast_memory_context_receipt()


def append_chat_message(
    role: str,
    author: str,
    text: str,
    *,
    source: str | None = None,
    task_id: str | None = None,
    task_status: str | None = None,
    memory_receipt: dict[str, Any] | None = None,
    memory_write_receipt: dict[str, Any] | None = None,
) -> None:
    message = {
        "role": role,
        "author": author,
        "text": text,
        "at": time.time(),
    }
    if source:
        message["source"] = source
    if clean_text(task_id):
        message["taskId"] = clean_text(task_id)
    if clean_text(task_status):
        message["taskStatus"] = clean_text(task_status)
    if isinstance(memory_receipt, dict):
        message["memoryReceipt"] = dict(memory_receipt)
    if isinstance(memory_write_receipt, dict):
        message["memoryWriteReceipt"] = dict(
            memory_write_receipt
        )
    CHAT_MESSAGES.append(message)
    if len(CHAT_MESSAGES) > CHAT_LOG_LIMIT:
        del CHAT_MESSAGES[:-CHAT_LOG_LIMIT]


def _fast_control_continuity_result(
    *,
    raw_status: object | None = None,
    enabled: bool,
) -> dict[str, Any]:
    result: dict[str, Any] = {
        "schema": "fast_control.delivery-continuity.v1",
        "enabled": bool(enabled),
        "durable": False,
        "generation": 0,
        "persistedSessionCount": 0,
        "error": "",
    }
    if not enabled:
        return result
    try:
        receipt = require_durable_continuity_receipt(
            raw_status
        )
    except Exception as exc:
        result["error"] = (
            "conversation_continuity_commit_failed"
        )
        print(
            "[FAST CONTROL] continuity_commit_failed "
            f"errorType={type(exc).__name__}",
            flush=True,
        )
        return result
    result.update(
        {
            "durable": True,
            "generation": int(receipt["generation"]),
            "persistedSessionCount": int(
                receipt["persistedSessionCount"]
            ),
        }
    )
    return result


def commit_fast_control_turn(
    user_text: str,
    assistant_text: str,
) -> dict[str, Any]:
    owner = FAST_CONTROL_CONTINUITY_OWNER
    if not owner.enabled:
        return _fast_control_continuity_result(
            enabled=False,
        )
    if not (
        FAST_ACTION_RECOVERY_JOURNAL
        .continuity_commit_allowed()
    ):
        print(
            "[FAST CONTROL] continuity_blocked_by_action_recovery",
            flush=True,
        )
        return _fast_control_continuity_result(
            raw_status=None,
            enabled=True,
        )
    try:
        raw_status = owner.record_completed_turn(
            user_text,
            assistant_text,
        )
    except Exception as exc:
        print(
            "[FAST CONTROL] continuity_record_failed "
            f"errorType={type(exc).__name__}",
            flush=True,
        )
        raw_status = None
    return _fast_control_continuity_result(
        raw_status=raw_status,
        enabled=True,
    )


def commit_fast_control_followup(
    assistant_text: str,
) -> dict[str, Any]:
    owner = FAST_CONTROL_CONTINUITY_OWNER
    if not owner.enabled:
        return _fast_control_continuity_result(
            enabled=False,
        )
    try:
        raw_status = owner.record_assistant_followup(
            assistant_text
        )
    except Exception as exc:
        print(
            "[FAST CONTROL] followup_continuity_record_failed "
            f"errorType={type(exc).__name__}",
            flush=True,
        )
        raw_status = None
    return _fast_control_continuity_result(
        raw_status=raw_status,
        enabled=True,
    )


def begin_fast_action_recovery(
    task: FastActionTask,
) -> None:
    try:
        owner_status = FAST_CONTROL_CONTINUITY_OWNER.status()
        FAST_ACTION_RECOVERY_JOURNAL.begin(
            task.task_id,
            continuity_generation=max(
                0,
                int(owner_status.get("generation") or 0),
            ),
        )
    except Exception as exc:
        ACTION_COORDINATOR.fail(
            task.task_id,
            "fast_action_recovery_unavailable",
            reply=(
                "작업 복구 상태를 기록하지 못해서 "
                "장시간 작업을 시작하지 않았어."
            ),
        )
        print(
            "[FAST CONTROL] action_recovery_begin_failed "
            f"errorType={type(exc).__name__}",
            flush=True,
        )
        raise RuntimeError(
            "fast_action_recovery_unavailable"
        ) from exc


def commit_fast_control_action_followup(
    task_id: str,
    assistant_text: str,
) -> dict[str, Any]:
    owner = FAST_CONTROL_CONTINUITY_OWNER
    journal = FAST_ACTION_RECOVERY_JOURNAL
    if not owner.enabled:
        return _fast_control_continuity_result(
            enabled=False,
        )

    def prepare_terminal(
        expected_generation: int,
    ) -> None:
        journal.prepare_terminal(
            task_id,
            expected_generation=expected_generation,
        )

    try:
        raw_status = owner.record_assistant_followup(
            assistant_text,
            before_commit=prepare_terminal,
        )
    except Exception as exc:
        print(
            "[FAST CONTROL] action_followup_commit_failed "
            f"errorType={type(exc).__name__}",
            flush=True,
        )
        raw_status = None
    result = _fast_control_continuity_result(
        raw_status=raw_status,
        enabled=True,
    )
    if result.get("durable") is True:
        try:
            journal.finish(task_id)
        except Exception as exc:
            print(
                "[FAST CONTROL] action_recovery_finish_failed "
                f"errorType={type(exc).__name__}",
                flush=True,
            )
    else:
        try:
            journal.mark_interrupted(task_id)
        except Exception as exc:
            print(
                "[FAST CONTROL] action_recovery_interrupt_failed "
                f"errorType={type(exc).__name__}",
                flush=True,
            )
    return result


def commit_fast_control_terminal_turn(
    task_id: str,
    user_text: str,
    assistant_text: str,
) -> dict[str, Any]:
    owner = FAST_CONTROL_CONTINUITY_OWNER
    journal = FAST_ACTION_RECOVERY_JOURNAL
    if not owner.enabled:
        return _fast_control_continuity_result(
            enabled=False,
        )

    def prepare_terminal(
        expected_generation: int,
    ) -> None:
        journal.prepare_terminal(
            task_id,
            expected_generation=expected_generation,
        )

    try:
        raw_status = owner.record_completed_turn(
            user_text,
            assistant_text,
            before_commit=prepare_terminal,
        )
    except Exception as exc:
        print(
            "[FAST CONTROL] action_terminal_turn_commit_failed "
            f"errorType={type(exc).__name__}",
            flush=True,
        )
        raw_status = None
    result = _fast_control_continuity_result(
        raw_status=raw_status,
        enabled=True,
    )
    if result.get("durable") is True:
        try:
            journal.finish(task_id)
        except Exception as exc:
            print(
                "[FAST CONTROL] action_recovery_finish_failed "
                f"errorType={type(exc).__name__}",
                flush=True,
            )
    else:
        try:
            journal.mark_interrupted(task_id)
        except Exception as exc:
            print(
                "[FAST CONTROL] action_recovery_interrupt_failed "
                f"errorType={type(exc).__name__}",
                flush=True,
            )
    return result


def recover_fast_control_actions_after_restart(
) -> dict[str, Any]:
    journal = FAST_ACTION_RECOVERY_JOURNAL
    owner = FAST_CONTROL_CONTINUITY_OWNER
    owner_status = owner.status()
    generation = max(
        0,
        int(owner_status.get("generation") or 0),
    )
    decision = journal.recovery_decision(
        continuity_generation=generation,
        continuity_ready=(
            owner_status.get("durableReady") is True
        ),
    )
    state = clean_text(decision.get("state"))
    pending_count = max(
        0,
        int(decision.get("pendingCount") or 0),
    )
    if state in {"disabled", "idle", "unavailable"}:
        return journal.public_status()
    if state == "delivery_verified":
        try:
            return journal.acknowledge_recovery(
                recovered_count=pending_count,
            )
        except Exception as exc:
            print(
                "[FAST CONTROL] action_recovery_ack_failed "
                f"errorType={type(exc).__name__}",
                flush=True,
            )
            return journal.public_status()
    restored_notice = bool(
        CHAT_MESSAGES
        and clean_text(CHAT_MESSAGES[-1].get("role"))
        == "assistant"
        and clean_text(CHAT_MESSAGES[-1].get("text"))
        == FAST_ACTION_RECOVERY_NOTICE
        and clean_text(CHAT_MESSAGES[-1].get("source"))
        == "fast_control_continuity_restore"
        and owner_status.get("durableReady") is True
        and journal.restored_notice_matches(
            continuity_generation=generation,
        )
    )
    if not restored_notice:
        append_chat_message(
            "assistant",
            "Evelyn",
            FAST_ACTION_RECOVERY_NOTICE,
            source="fast_control_action_recovery",
        )
        continuity = commit_fast_control_followup(
            FAST_ACTION_RECOVERY_NOTICE
        )
        if continuity.get("durable") is not True:
            return journal.public_status()
    try:
        return journal.acknowledge_recovery(
            recovered_count=pending_count,
            error_code=clean_text(
                decision.get("reasonCode")
            ),
        )
    except Exception as exc:
        print(
            "[FAST CONTROL] action_recovery_ack_failed "
            f"errorType={type(exc).__name__}",
            flush=True,
        )
        return journal.public_status()


def recent_chat_messages_for_planner(text: str, *, limit: int = 8) -> list[dict[str, str]]:
    messages = [
        {
            "role": clean_text(message.get("role")) or "user",
            "content": clean_text(message.get("text")),
        }
        for message in CHAT_MESSAGES[-max(1, limit + 1) :]
        if clean_text(message.get("role")) in {"user", "assistant"}
        and clean_text(message.get("text"))
    ]
    if (
        messages
        and messages[-1]["role"] == "user"
        and messages[-1]["content"] == clean_text(text)
    ):
        messages.pop()
    merged = CROSS_SURFACE_CONTINUITY_BRIDGE.merge_for_fast(
        messages,
        current_user_text=text,
    )
    return [
        {
            "role": clean_text(message.get("role")),
            "content": clean_text(message.get("content")),
        }
        for message in merged[-limit:]
        if clean_text(message.get("role"))
        in {"user", "assistant"}
        and clean_text(message.get("content"))
    ]


def local_bridge_status_snapshot(*, now: float | None = None) -> dict[str, Any]:
    snapshot = dict(LOCAL_BRIDGE_STATUS)
    raw_error = clean_text(snapshot.get("lastError"))
    error_fallback = (
        "mic_control_failed"
        if raw_error.lower().startswith("mic_control_failed")
        else "local_bridge_failed"
    )
    snapshot["lastError"] = (
        public_error_code(raw_error, fallback=error_fallback)
        if raw_error
        else ""
    )
    mic = dict(snapshot.get("mic") or {})
    raw_mic_error = clean_text(mic.get("lastError"))
    if raw_mic_error:
        mic["lastError"] = public_error_code(
            raw_mic_error,
            fallback="mic_control_failed",
        )
        snapshot["mic"] = mic
    snapshot["micControlRequest"] = dict(LOCAL_BRIDGE_MIC_CONTROL_REQUEST)
    snapshot["minecraftCommandRequest"] = dict(LOCAL_BRIDGE_MINECRAFT_COMMAND_REQUEST)
    if LOCAL_BRIDGE_OUTPUT_DEVICE_REQUEST.get("outputDevice"):
        snapshot["outputDeviceSelection"] = dict(LOCAL_BRIDGE_OUTPUT_DEVICE_REQUEST)
    updated_at = snapshot.get("updatedAt")
    if not snapshot.get("enabled") or not isinstance(updated_at, (int, float)):
        return snapshot
    age_sec = float((time.time() if now is None else now) - updated_at)
    snapshot["ageSec"] = round(max(0.0, age_sec), 1)
    if age_sec <= LOCAL_BRIDGE_STALE_AFTER_SEC:
        snapshot["stale"] = False
        return snapshot
    snapshot["ready"] = False
    snapshot["stale"] = True
    snapshot["lastError"] = (
        clean_text(snapshot.get("lastError")) or "local_bridge_stale"
    )
    return snapshot


def queue_local_bridge_speech(text: str, *, source: str = "control_page") -> dict[str, Any] | None:
    global LOCAL_BRIDGE_SPEAK_SEQ
    speech_text = clean_text(text)
    if not speech_text:
        return None
    bridge = local_bridge_status_snapshot()
    if not bridge.get("ready") or bridge.get("stale"):
        return None
    LOCAL_BRIDGE_SPEAK_SEQ += 1
    request = {
        "id": f"page-tts-{LOCAL_BRIDGE_SPEAK_SEQ}",
        "text": speech_text,
        "source": source,
        "createdAt": time.time(),
    }
    LOCAL_BRIDGE_SPEAK_QUEUE.append(request)
    del LOCAL_BRIDGE_SPEAK_QUEUE[:-8]
    return request


def drain_local_bridge_speak_requests() -> list[dict[str, Any]]:
    requests = list(LOCAL_BRIDGE_SPEAK_QUEUE)
    LOCAL_BRIDGE_SPEAK_QUEUE.clear()
    return requests


def set_local_bridge_output_device(output_device: str, *, source: str = "control_page") -> dict[str, Any]:
    current_revision = int(LOCAL_BRIDGE_OUTPUT_DEVICE_REQUEST.get("revision") or 0)
    requested_device = clean_text(output_device) or "default"
    LOCAL_BRIDGE_OUTPUT_DEVICE_REQUEST.update(
        {
            "outputDevice": requested_device,
            "requestedAt": time.time(),
            "source": clean_text(source) or "control_page",
            "revision": current_revision + 1,
        }
    )
    save_local_audio_device_state(LOCAL_BRIDGE_OUTPUT_DEVICE_REQUEST)
    return dict(LOCAL_BRIDGE_OUTPUT_DEVICE_REQUEST)


def request_local_bridge_mic_control(enabled: bool, *, source: str = "control_page") -> dict[str, Any]:
    current_revision = int(LOCAL_BRIDGE_MIC_CONTROL_REQUEST.get("revision") or 0)
    revision = max(current_revision + 1, int(time.time() * 1000))
    LOCAL_BRIDGE_MIC_CONTROL_REQUEST.update(
        {
            "revision": revision,
            "enabled": bool(enabled),
            "requestedAt": time.time(),
            "source": clean_text(source) or "control_page",
        }
    )
    return dict(LOCAL_BRIDGE_MIC_CONTROL_REQUEST)


async def wait_for_local_bridge_mic_control(
    request: dict[str, Any],
    *,
    timeout_sec: float = 4.0,
) -> dict[str, Any]:
    revision = int(request.get("revision") or 0)
    desired_enabled = bool(request.get("enabled"))
    deadline = time.monotonic() + max(0.1, float(timeout_sec))
    while time.monotonic() < deadline:
        snapshot = local_bridge_status_snapshot()
        applied_revision = int(snapshot.get("micControlRevision") or 0)
        if applied_revision >= revision and bool(snapshot.get("micEnabled")) == desired_enabled:
            return {"applied": True, "request": dict(request), "localBridge": snapshot}
        await asyncio.sleep(0.05)
    return {
        "applied": False,
        "request": dict(request),
        "localBridge": local_bridge_status_snapshot(),
        "error": "mic_control_ack_timeout",
    }


async def execute_local_bridge_mic_control(enabled: bool, *, source: str) -> str:
    request = request_local_bridge_mic_control(enabled, source=source)
    result = await wait_for_local_bridge_mic_control(request)
    snapshot = dict(result.get("localBridge") or {})
    if not result.get("applied"):
        action = "켜기" if enabled else "끄기"
        return f"마이크 입력 {action} 요청은 보냈지만 브리지 적용 확인을 받지 못했어."
    if enabled:
        mic = dict(snapshot.get("mic") or {})
        if snapshot.get("ready") and mic.get("captureReady"):
            return "마이크 입력을 켰어."
        error = clean_text(snapshot.get("lastError")) or clean_text(mic.get("lastError"))
        detail = f" 오류: {error}" if error else ""
        return clean_text(f"마이크 입력을 켜려고 했지만 캡처가 준비되지 않았어.{detail}")
    error = clean_text(snapshot.get("lastError"))
    if error.startswith("mic_control_failed"):
        return f"마이크 입력을 끄려고 했지만 캡처 종료 중 오류가 났어. 오류: {error}"
    return "마이크 입력을 껐어."


def request_local_bridge_minecraft_command(command: str, *, source: str) -> dict[str, Any]:
    action = detect_minecraft_runtime_command(command)
    if action not in {"start", "goal"}:
        raise ValueError("not_a_minecraft_runtime_command")
    if clean_text(LOCAL_BRIDGE_MINECRAFT_COMMAND_REQUEST.get("command")):
        raise RuntimeError("minecraft_command_already_pending")
    current_revision = int(LOCAL_BRIDGE_MINECRAFT_COMMAND_REQUEST.get("revision") or 0)
    revision = max(current_revision + 1, int(time.time() * 1000))
    LOCAL_BRIDGE_MINECRAFT_COMMAND_REQUEST.update(
        {
            "revision": revision,
            "command": clean_text(command),
            "action": action,
            "requestedAt": time.time(),
            "source": clean_text(source) or "control_page",
        }
    )
    return dict(LOCAL_BRIDGE_MINECRAFT_COMMAND_REQUEST)


def clear_local_bridge_minecraft_command_request(revision: int) -> None:
    if int(LOCAL_BRIDGE_MINECRAFT_COMMAND_REQUEST.get("revision") or 0) != int(revision):
        return
    LOCAL_BRIDGE_MINECRAFT_COMMAND_REQUEST.update(
        {
            "command": "",
            "action": "",
            "requestedAt": None,
            "source": "",
        }
    )


async def wait_for_local_bridge_minecraft_command(
    request: dict[str, Any],
    *,
    timeout_sec: float = MINECRAFT_LAZY_START_TIMEOUT_SEC,
) -> dict[str, Any]:
    revision = int(request.get("revision") or 0)
    deadline = time.monotonic() + max(0.1, float(timeout_sec))
    while time.monotonic() < deadline:
        snapshot = local_bridge_status_snapshot()
        applied_revision = int(snapshot.get("minecraftCommandRevision") or 0)
        state = clean_text(snapshot.get("minecraftCommandState")).lower()
        if applied_revision >= revision and state in {"ready", "failed"}:
            return {
                "applied": state == "ready",
                "request": dict(request),
                "localBridge": snapshot,
                "state": state,
                "result": dict(snapshot.get("minecraftCommandResult") or {}),
                "error": clean_text(snapshot.get("minecraftCommandError")),
            }
        await asyncio.sleep(0.1)
    return {
        "applied": False,
        "request": dict(request),
        "localBridge": local_bridge_status_snapshot(),
        "state": "timeout",
        "result": {},
        "error": "minecraft_command_ack_timeout",
    }


async def request_minecraft_control_service(
    method: str,
    path: str,
    payload: dict[str, Any] | None = None,
    *,
    log_failure: bool = True,
) -> tuple[dict[str, Any] | None, str]:
    url = f"{MINECRAFT_AUTONOMY_SERVICE_BASE}{path}"
    timeout = ClientTimeout(total=MINECRAFT_CONTROL_TIMEOUT_SEC)
    try:
        async with ClientSession(timeout=timeout) as session:
            request_kwargs: dict[str, Any] = {}
            if payload is not None:
                request_kwargs["json"] = payload
            async with session.request(
                method.upper(),
                url,
                **request_kwargs,
            ) as response:
                raw = await response.text()
                try:
                    payload = json.loads(raw or "{}")
                except json.JSONDecodeError:
                    payload = {}
                if response.status >= 400:
                    return None, public_error_code(
                        (payload or {}).get("error"),
                        fallback="minecraft_request_failed",
                    )
                if not isinstance(payload, dict):
                    return None, "invalid_minecraft_response"
                return payload, ""
    except Exception as exc:
        if log_failure:
            print(
                "[FAST CONTROL] minecraft_request_failed "
                f"method={method} path={path} "
                f"errorType={type(exc).__name__}"
            )
        return None, "minecraft_service_unavailable"


def minecraft_service_is_offline(error: str) -> bool:
    normalized = clean_text(error).lower()
    if any(
        marker in normalized
        for marker in (
            "clientconnectorerror",
            "clientconnectordnserror",
            "connectionrefusederror",
            "connect call failed",
            "offline",
            "minecraft_service_unavailable",
            "name or service not known",
            "nodename nor servname",
            "temporary failure in name resolution",
        )
    ):
        return True
    if "timeouterror" not in normalized:
        return False
    bridge = local_bridge_status_snapshot()
    command_revision = int(bridge.get("minecraftCommandRevision") or 0)
    command_state = clean_text(bridge.get("minecraftCommandState")).lower()
    return command_revision <= 0 or command_state in {"", "idle", "failed"}


async def _request_minecraft_world_runtime(
    method: str,
    path: str,
    payload: dict[str, Any] | None,
) -> tuple[dict[str, Any] | None, str]:
    is_internal_status_probe = (
        method.upper() == "GET"
        and path == "/status"
        and payload is None
    )
    return await request_minecraft_control_service(
        method,
        path,
        payload,
        log_failure=not is_internal_status_probe,
    )


def _merge_minecraft_world_status(
    status: Any,
    observed: Any,
) -> dict[str, Any]:
    merged = dict(status) if isinstance(status, dict) else {}
    if isinstance(observed, dict):
        merged.update(observed)
    return merged


MINECRAFT_WORLD_HTTP_RUNTIME = MinecraftWorldLeaseHttpRuntime(
    request=_request_minecraft_world_runtime,
    is_offline_error=minecraft_service_is_offline,
)
MINECRAFT_WORLD_MODE = MinecraftModeComposition(
    MinecraftModeCompositionDeps(
        get_client=lambda: MINECRAFT_WORLD_HTTP_RUNTIME,
        merge_status=_merge_minecraft_world_status,
        clean_text=clean_text,
        monotonic=time.monotonic,
        sleep=asyncio.sleep,
    )
)
MINECRAFT_WORLD_LEASE_OWNER = MinecraftWorldLeaseOwner(
    status_path=(
        get_runtime_artifacts_root()
        / "minecraft_world_lease"
        / "status.json"
    ),
    events_dir=(
        get_runtime_artifacts_root()
        / "minecraft_world_lease"
        / "events"
    ),
    get_runtime_status=MINECRAFT_WORLD_HTTP_RUNTIME.status,
    enable_mode=MINECRAFT_WORLD_MODE.enable_minecraft_mode,
    disable_mode=MINECRAFT_WORLD_MODE.disable_minecraft_mode,
    set_goal=MINECRAFT_WORLD_HTTP_RUNTIME.set_goal,
    create_task=asyncio.create_task,
    log=print,
)


def minecraft_control_error_reply(subject: str, error: str) -> str:
    if minecraft_service_is_offline(error):
        return minecraft_standby_reply(subject)
    detail = clean_text(error) or "unknown_error"
    return f"마인크래프트 {subject} 확인에 실패했어. 오류: {detail}"


def minecraft_standby_reply(subject: str = "상태") -> str:
    if subject == "inventory":
        return "마인크래프트 서비스가 대기 중이라 현재 인벤토리는 확인할 수 없어."
    if subject == "disconnect":
        return "마인크래프트 서비스는 이미 종료돼 있어."
    return "마인크래프트 서비스는 지금 대기 중이야. 실행 명령을 받기 전에는 전용 모델을 로드하지 않아."


def render_minecraft_status(payload: dict[str, Any], *, detailed: bool = False) -> str:
    running = bool(payload.get("running") or payload.get("loop_running"))
    connected = bool(payload.get("connected") or payload.get("minecraft_connected"))
    lease_status = (
        payload.get("world_lease")
        if isinstance(payload.get("world_lease"), dict)
        else {}
    )
    if not running:
        reply = minecraft_standby_reply()
        if lease_status.get("active"):
            reply += " 세계 행동 lease는 승인됐지만 runner는 실행 중이 아니야."
        return reply
    state = clean_text(payload.get("connection_state")) or ("connected" if connected else "starting")
    goal = clean_text(payload.get("goal") or payload.get("current_task"))
    stage = clean_text(payload.get("display_stage") or payload.get("stage"))
    raw_last_error = clean_text(payload.get("last_error"))
    last_error = (
        public_error_code(
            raw_last_error,
            fallback="minecraft_snapshot_unavailable",
        )
        if raw_last_error
        else ""
    )
    parts = [
        f"마인크래프트 에이전트는 실행 중이고 게임 접속은 {'확인됐어' if connected else '아직 준비 중이야'}.",
        f"현재 상태는 {state}야.",
    ]
    if goal:
        parts.append(f"목표는 “{goal}”이야.")
    if detailed and stage:
        parts.append(f"진행 단계는 {stage}야.")
    if detailed:
        blocked_count = int(payload.get("blocked_command_count") or 0)
        parts.append(f"차단된 명령은 {blocked_count}건이야.")
    if last_error:
        parts.append(f"최근 오류: {last_error}")
    if lease_status:
        parts.append(
            "세계 행동 lease는 "
            f"{lease_status.get('state') or 'unknown'} 상태야."
        )
    return clean_text(" ".join(parts))


def render_minecraft_inventory(payload: dict[str, Any]) -> str:
    inventory = payload.get("inventory")
    if not isinstance(inventory, dict) or not inventory:
        return "현재 확인되는 마인크래프트 인벤토리가 비어 있어."
    items: list[str] = []
    for name, raw_count in sorted(inventory.items(), key=lambda item: str(item[0]).lower()):
        try:
            count = int(raw_count)
        except (TypeError, ValueError):
            count = 0
        if count > 0:
            items.append(f"{clean_text(name)} {count}개")
        if len(items) >= 12:
            break
    if not items:
        return "현재 확인되는 마인크래프트 인벤토리가 비어 있어."
    return "현재 인벤토리: " + ", ".join(items) + "."


async def execute_minecraft_control_command(
    action: str,
    *,
    guild_id: int = 0,
) -> str:
    if action == "disconnect":
        try:
            payload = await MINECRAFT_WORLD_LEASE_OWNER.disconnect(
                guild_id
            )
        except RuntimeError as exc:
            code = minecraft_world_lease_delegation_error_code(
                exc
            )
            if code == "minecraft_world_lease_owner_mismatch":
                return (
                    "다른 대화 공간이 현재 Minecraft lease를 소유하고 "
                    "있어서 여기서는 연결을 종료할 수 없어."
                )
            return (
                "마인크래프트 연결 종료 검증에 실패했어. "
                f"오류 코드: {code}"
            )
        if payload.get("running") or payload.get("loop_running"):
            return (
                "마인크래프트 중지 요청 뒤에도 runner가 실행 중이라 "
                "성공으로 처리하지 않았어."
            )
        return "마인크래프트 에이전트 연결을 중지했어."

    if action == "inventory":
        payload, error = await request_minecraft_control_service("GET", "/observe")
        if payload is None:
            return minecraft_control_error_reply("inventory", error)
        return render_minecraft_inventory(payload)

    payload, error = await request_minecraft_control_service("GET", "/status")
    if payload is None:
        return minecraft_control_error_reply("상태", error)
    payload["world_lease"] = MINECRAFT_WORLD_LEASE_OWNER.status()
    return render_minecraft_status(payload, detailed=action in {"stats", "autonomy_status"})


def minecraft_goal_from_command(text: str) -> str:
    value = clean_text(text)
    match = re.match(
        r"^/(?:minecraft|mc)\s+goal\s+(.+)$",
        value,
        flags=re.IGNORECASE,
    )
    if match:
        return clean_text(match.group(1))
    return value


def fast_control_minecraft_issuer(source: str) -> str:
    safe_source = re.sub(
        r"[^A-Za-z0-9_.-]+",
        "_",
        clean_text(source),
    ).strip("_")
    return f"fast_control:{safe_source or 'local'}"


async def execute_fast_control_minecraft_runtime_command(
    text: str,
    *,
    source: str,
    guild_id: int = 0,
) -> str:
    action = detect_minecraft_runtime_command(text)
    if action not in {"start", "goal"}:
        raise RuntimeError("not_a_minecraft_runtime_command")
    try:
        if action == "goal":
            goal = minecraft_goal_from_command(text)
            status = MINECRAFT_WORLD_LEASE_OWNER.status()
            lease = (
                status.get("lease")
                if isinstance(status.get("lease"), dict)
                else {}
            )
            if (
                status.get("active")
                and lease.get("guildId") == guild_id
            ):
                result = await MINECRAFT_WORLD_LEASE_OWNER.set_goal(
                    guild_id,
                    goal,
                )
                if result.get("outcome_verified") is not True:
                    raise RuntimeError("minecraft_goal_unverified")
                return "Minecraft 목표 변경을 실제 runtime 응답으로 확인했어."
            result = await MINECRAFT_WORLD_LEASE_OWNER.connect(
                guild_id,
                issuer_ref=fast_control_minecraft_issuer(
                    source
                ),
                source="control_page",
                goal=goal,
            )
        else:
            result = await MINECRAFT_WORLD_LEASE_OWNER.connect(
                guild_id,
                issuer_ref=fast_control_minecraft_issuer(
                    source
                ),
                source="control_page",
            )
    except RuntimeError as exc:
        code = minecraft_world_lease_delegation_error_code(exc)
        if code == "minecraft_service_unavailable":
            return (
                "Minecraft 실행 서비스가 아직 올라오지 않았어. "
                "Voyager 프로필을 시작한 뒤 다시 승인해줘."
            )
        if code == "minecraft_world_lease_owner_mismatch":
            return (
                "다른 대화 공간이 현재 Minecraft lease를 소유하고 있어. "
                "그 공간에서 먼저 연결을 종료해야 해."
            )
        return (
            "Minecraft 세계 행동을 시작하지 못했어. "
            f"오류 코드: {code}"
        )
    if (
        result.get("outcome_verified") is not True
        or not (
            result.get("connected")
            or result.get("minecraft_connected")
        )
    ):
        return (
            "Minecraft 연결 결과를 검증하지 못해서 시작 성공으로 "
            "처리하지 않았어."
        )
    return (
        "Minecraft world-action lease를 발급했고 게임 연결까지 "
        "확인했어."
    )


async def execute_local_bridge_minecraft_command(command: str, source: str) -> str:
    _ = command, source
    raise FastActionExecutionError(
        "minecraft_world_authorization_required",
        reply=(
            "마인크래프트 세계 행동은 승인된 Control Page 도구나 "
            "Discord /minecraft connect 명령으로 먼저 연결해야 해."
        ),
    )


async def synthesize_tool_evidence_reply(
    *,
    user_text: str,
    task_kind: str,
    evidence: str,
) -> str:
    system_prompt = "\n\n".join(
        (
            FAST_MAIN_LLM_SYSTEM_PROMPT,
            (
                "A registered Evelyn tool already completed a background task. "
                "Answer only from the supplied evidence. Do not say you will search, inspect, or work later. "
                "Do not claim a registered tool is unavailable. "
                "For comparison research, give a concise Korean comparison and a practical next choice. "
                "For runtime investigation, state the observed cause or uncertainty and the most useful next action. "
                "Do not recommend network, server, permissions, or restarts unless the evidence explicitly shows that failure. "
                "If the requested service is healthy, say no current failure is confirmed before discussing log observations. "
                "Use 2 to 5 short Korean sentences."
            ),
            f"completed_task_kind={task_kind}\n{clean_text(evidence)[:7000]}",
        )
    )
    payload: dict[str, Any] = {
        "model": MODEL_NAME,
        "messages": [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": clean_text(user_text)},
        ],
        "temperature": 0.2,
        "max_tokens": 650,
        "stream": False,
        "cache_prompt": True,
    }
    if MAIN_LLM_STOP_TOKENS:
        payload["stop"] = list(MAIN_LLM_STOP_TOKENS)
    timeout = ClientTimeout(total=120)
    async with ClientSession(timeout=timeout) as session:
        async with session.post(LLM_SERVER_URL, json=payload) as response:
            if response.status != 200:
                detail = await response.text()
                raise RuntimeError(f"main_llm_tool_synthesis_error {response.status}: {detail[:300]}")
            data = await response.json(content_type=None)
    choices = data.get("choices") or []
    content = ((choices[0].get("message") or {}).get("content")) if choices else ""
    reply = enforce_registered_tool_capability_truth(visible_text(content))
    return enforce_action_reply_contract(reply)


async def execute_web_research_plan(plan: FastToolPlan, user_text: str, source: str) -> str:
    from .fast_context_contract import default_search_provider
    from .search_tools import normalize_search_query, render_search_results_for_llm

    query = normalize_search_query(plan.query or user_text)
    if not query:
        raise FastActionExecutionError(
            "research_query_empty",
            reply="무엇을 조사해야 하는지 주제를 잡지 못했어. 대상을 한 번만 더 말해줘.",
        )
    executed_query, results = await default_search_provider(query)
    if not results:
        retry_query = normalize_search_query(
            re.sub(
                r"(?:아니|그거|그걸|좀|제대로|찾아보라고|알아보라고|조사해보라고)",
                " ",
                query,
                flags=re.IGNORECASE,
            )
        )
        if retry_query and retry_query != executed_query:
            executed_query, results = await default_search_provider(retry_query)
    if not results:
        raise FastActionExecutionError(
            "web_research_empty",
            reply=f"`{executed_query or query}`로 검색했지만 비교할 만한 결과를 찾지 못했어.",
        )
    evidence = render_search_results_for_llm(executed_query, results)
    try:
        reply = await synthesize_tool_evidence_reply(
            user_text=user_text,
            task_kind="research_compare",
            evidence=evidence,
        )
    except Exception:
        titles = [
            clean_text((item if isinstance(item, dict) else item.to_dict()).get("title"))
            for item in results[:3]
        ]
        reply = clean_text(
            f"`{executed_query}` 검색은 완료했어. "
            f"우선 확인된 후보는 {', '.join(title for title in titles if title)}야."
        )
    if not reply:
        raise FastActionExecutionError(
            "web_research_synthesis_empty",
            reply="검색은 끝났지만 결과 요약이 비어 있었어.",
        )
    return reply


async def execute_runtime_investigation_plan(plan: FastToolPlan, user_text: str, source: str) -> str:
    from .fast_context_contract import build_fast_log_context, compact_runtime_health_for_llm

    health = await collect_runtime_health(
        manifest=load_service_manifest(),
        probe_runner=fast_control_probe_runner,
    )
    lowered_query = clean_text(plan.query or user_text).lower()
    target_markers = {
        "tts": ("tts", "음성 합성", "목소리"),
        "stt": ("stt", "음성 인식"),
        "main_llm": ("main llm", "main_llm", "메인 llm", "메인 모델"),
        "router_llm": ("router", "라우터"),
        "sub_llm": ("sub llm", "sub_llm", "서브 llm", "서브 모델"),
        "bot_api": ("bot api", "bot_api", "봇 api"),
        "control_page": ("control page", "control_page", "제어 페이지", "컨트롤 페이지"),
        "vision": ("vision", "비전", "화면 인식"),
    }
    target_ids = {
        service_id
        for service_id, markers in target_markers.items()
        if any(marker in lowered_query for marker in markers)
    }
    log_query = " ".join(sorted(target_ids)) if target_ids else (plan.query or user_text)
    log_context = await asyncio.to_thread(
        build_fast_log_context,
        log_query,
        max_files=10,
        max_chars=4500,
        require_match=True,
    )
    health_evidence = compact_runtime_health_for_llm(health)
    if target_ids:
        services = [
            dict(item)
            for item in health.get("services") or []
            if isinstance(item, dict) and clean_text(item.get("id")) in target_ids
        ]
        diagnostics = [
            dict(item)
            for item in health.get("diagnostics") or []
            if isinstance(item, dict)
            and target_ids.intersection(
                {
                    clean_text(service_id)
                    for service_id in (
                        item.get("serviceIds")
                        or item.get("service_ids")
                        or [item.get("serviceId") or item.get("service_id")]
                    )
                    if clean_text(service_id)
                }
            )
        ]
        targeted_health = {
            "overallState": (
                "up"
                if services and all(item.get("state") == "up" or item.get("ready") for item in services)
                else "down"
            ),
            "summary": (
                "Requested services are responding; no current health failure is confirmed."
                if services and all(item.get("state") == "up" or item.get("ready") for item in services)
                else "One or more requested services are not responding."
            ),
            "services": services,
            "diagnostics": diagnostics,
        }
        health_evidence = compact_runtime_health_for_llm(targeted_health)
    evidence = "\n\n".join(
        part
        for part in (
            "[Runtime Health]\n" + health_evidence,
            "[Mounted Evelyn Logs]\n" + (clean_text(log_context) or "No matching recent log evidence."),
        )
        if clean_text(part)
    )
    try:
        reply = await synthesize_tool_evidence_reply(
            user_text=user_text,
            task_kind="runtime_investigation",
            evidence=evidence,
        )
    except Exception:
        reply = clean_text(
            f"실제 런타임 상태와 로그를 확인했어. "
            f"{health.get('summary') or health.get('overallState') or '상태 요약은 비어 있어.'}"
        )
    if not reply:
        raise FastActionExecutionError(
            "runtime_investigation_synthesis_empty",
            reply="상태와 로그 확인은 끝났지만 전달할 결론이 비어 있었어.",
        )
    return reply


def prepare_tool_plan_background_action(
    plan: FastToolPlan | None,
    text: str,
    *,
    source: str,
) -> tuple[FastActionTask, Callable[[str, str], Awaitable[str]]] | None:
    if plan is None or not plan.is_background:
        return None
    if plan.tool_name == "research_compare":
        start_reply = random.choice(RESEARCH_PROGRESS_TEXTS)

        async def runner(user_text: str, runner_source: str) -> str:
            return await execute_web_research_plan(plan, user_text, runner_source)

    elif plan.tool_name == "runtime_investigation":
        start_reply = random.choice(INVESTIGATION_PROGRESS_TEXTS)

        async def runner(user_text: str, runner_source: str) -> str:
            return await execute_runtime_investigation_plan(plan, user_text, runner_source)

    else:
        return None
    task = ACTION_COORDINATOR.start(
        kind=plan.tool_name,
        source=source,
        user_text=text,
        start_reply=start_reply,
    )
    begin_fast_action_recovery(task)
    return task, runner


def should_queue_local_bridge_speech(source: str) -> bool:
    return clean_text(source) not in {"local_bridge", "local_mic", "voice"}


def register_background_action_handler(
    *,
    kind: str,
    matcher: Callable[[str], bool],
    runner: Callable[[str, str], Awaitable[str]],
    start_reply: str | Callable[[str], str],
) -> None:
    BACKGROUND_ACTION_HANDLERS.append(
        {
            "kind": clean_text(kind) or "background",
            "matcher": matcher,
            "runner": runner,
            "startReply": start_reply,
        }
    )


def clear_background_action_handlers() -> None:
    BACKGROUND_ACTION_HANDLERS.clear()


def register_builtin_background_action_handlers() -> None:
    # Minecraft start/goal is intentionally excluded. The local bridge does not
    # own the process-local world lease and therefore cannot authorize actions.
    return


def prepare_registered_background_action(
    text: str,
    *,
    source: str,
) -> tuple[FastActionTask, Callable[[str, str], Awaitable[str]]] | None:
    for handler in BACKGROUND_ACTION_HANDLERS:
        matcher = handler.get("matcher")
        if not callable(matcher) or not matcher(text):
            continue
        start_reply_value = handler.get("startReply")
        start_reply = start_reply_value(text) if callable(start_reply_value) else start_reply_value
        task = ACTION_COORDINATOR.start(
            kind=clean_text(handler.get("kind")) or "background",
            source=source,
            user_text=text,
            start_reply=clean_text(start_reply) or "작업을 시작했어.",
        )
        runner = handler.get("runner")
        if not callable(runner):
            ACTION_COORDINATOR.fail(
                task.task_id,
                "background_action_runner_missing",
                reply="작업 실행기가 연결되지 않아 시작하지 못했어.",
            )
            return None
        begin_fast_action_recovery(task)
        return task, runner
    return None


def launch_background_action(
    task: FastActionTask,
    runner: Callable[[str, str], Awaitable[str]],
) -> asyncio.Task[Any]:
    async def execute() -> None:
        try:
            raw_reply = await runner(task.user_text, task.source)
            final_reply = enforce_action_reply_contract(clean_text(raw_reply))
            if not final_reply:
                final_reply = "작업은 완료됐지만 전달할 결과가 비어 있어."
            completed = ACTION_COORDINATOR.complete(task.task_id, final_reply)
            append_chat_message(
                "assistant",
                "Evelyn",
                completed.final_reply,
                source="fast_control_action_followup",
                task_id=completed.task_id,
                task_status=completed.status,
            )
            commit_fast_control_action_followup(
                completed.task_id,
                completed.final_reply
            )
            queue_local_bridge_speech(completed.final_reply, source="fast_control_action_followup")
        except Exception as exc:
            print(
                "[FAST CONTROL] background_action_failed "
                f"task={task.task_id} errorType={type(exc).__name__}",
                flush=True,
            )
            if isinstance(exc, FastActionExecutionError):
                error = public_error_code(
                    str(exc),
                    fallback="background_action_failed",
                )
                failed_reply = (
                    clean_text(exc.reply)
                    or public_failure_message(
                        "background_action_failed"
                    )
                )
            else:
                error = "background_action_failed"
                failed_reply = public_failure_message(error)
            failed = ACTION_COORDINATOR.fail(task.task_id, error, reply=failed_reply)
            append_chat_message(
                "assistant",
                "Evelyn",
                failed.final_reply,
                source="fast_control_action_followup",
                task_id=failed.task_id,
                task_status=failed.status,
            )
            commit_fast_control_action_followup(
                failed.task_id,
                failed.final_reply
            )
            queue_local_bridge_speech(failed.final_reply, source="fast_control_action_followup")

    background_task = asyncio.create_task(execute(), name=task.task_id)
    BACKGROUND_ACTION_TASKS.add(background_task)
    background_task.add_done_callback(BACKGROUND_ACTION_TASKS.discard)
    return background_task


def enqueue_control_page_ui_command(action: str, *, panel_id: str) -> dict[str, Any]:
    global CONTROL_PAGE_UI_COMMAND_SEQ
    cleaned_action = clean_text(action).lower()
    if cleaned_action not in {"open", "close", "toggle"}:
        cleaned_action = "toggle"
    CONTROL_PAGE_UI_COMMAND_SEQ += 1
    command = {
        "id": CONTROL_PAGE_UI_COMMAND_SEQ,
        "action": cleaned_action,
        "panel": panel_id,
        "at": time.time(),
    }
    CONTROL_PAGE_UI_COMMANDS.append(command)
    if len(CONTROL_PAGE_UI_COMMANDS) > 40:
        del CONTROL_PAGE_UI_COMMANDS[:-40]
    return dict(command)


def build_control_page_panel_state() -> dict[str, Any]:
    return build_control_page_panel_state_payload(
        CONTROL_PAGE_UI_COMMANDS,
        revision=CONTROL_PAGE_UI_COMMAND_SEQ,
    )


def execute_memory_panel_action(action: str) -> str:
    cleaned_action = action if action in {"open", "close", "toggle"} else "toggle"
    enqueue_control_page_ui_command(cleaned_action, panel_id="memory")
    return memory_panel_reply(cleaned_action)


def request_local_shutdown(*, source: str, reason: str = "") -> dict[str, Any]:
    SHUTDOWN_REQUEST.update(
        {
            "requested": True,
            "requestedAt": time.time(),
            "source": clean_text(source) or "control_page",
            "reason": clean_text(reason) or "operator_request",
        }
    )
    return {
        "ok": True,
        "message": "Local Evelyn shutdown requested. Windows local I/O bridge will run the stop script.",
        "shutdown": dict(SHUTDOWN_REQUEST),
    }


def request_local_restart(*, source: str, reason: str = "") -> dict[str, Any]:
    RESTART_REQUEST.update(
        {
            "requested": True,
            "requestedAt": time.time(),
            "source": clean_text(source) or "control_page",
            "reason": clean_text(reason) or "operator_request",
        }
    )
    return {
        "ok": True,
        "message": "Local Evelyn restart requested. Windows local I/O bridge will restart the local runtime.",
        "restart": dict(RESTART_REQUEST),
    }


def build_control_plane_state(
    *,
    bot_ready: bool,
    health_cache: dict[str, Any] | None = None,
) -> dict[str, Any]:
    cache = dict(health_cache or {})
    return {
        "controlPage": {
            "ready": True,
            "host": "127.0.0.1",
            "port": PUBLIC_CONTROL_PORT,
            "role": "Control-Page",
        },
        "botApi": {
            "ready": bool(bot_ready),
            "portOpen": bool(bot_ready),
            "host": HOST,
            "port": PORT,
            "role": "Bot API",
            "state": "ready" if bot_ready else "down",
        },
        "lastProxyFailure": {},
        "healthCache": {
            "schema": str(
                cache.get("schema") or "runtime_health.cache.v1"
            ),
            "ageSec": max(
                0.0,
                float(cache.get("ageSec") or 0.0),
            ),
            "stale": bool(cache.get("stale")),
            "refreshing": bool(cache.get("refreshing")),
            "refreshAfterSec": float(
                cache.get("refreshAfterSec") or 0.0
            ),
            "maxStaleSec": float(
                cache.get("maxStaleSec") or 0.0
            ),
            "lastRefreshError": str(
                cache.get("lastRefreshError") or ""
            ),
        },
        "statusText": (
            "Control-Page and Bot API are both responding."
            if bot_ready
            else f"Control-Page is live on {PUBLIC_CONTROL_PORT}; Bot API is not ready on {PORT}."
        ),
    }


def default_chat_messages() -> list[dict[str, Any]]:
    if CHAT_MESSAGES:
        return list(CHAT_MESSAGES)
    return [
        {
            "role": "assistant",
            "author": "Control",
            "text": "Docker core is ready. Windows local I/O bridge can attach microphone and speaker output.",
            "at": time.time(),
        }
    ]


def parse_stream_line(raw_line: bytes) -> dict[str, Any] | None:
    line = raw_line.decode("utf-8", errors="ignore").strip()
    if not line or line.startswith(":"):
        return None
    if line.startswith("data:"):
        line = line[5:].strip()
    if not line:
        return None
    if line == "[DONE]":
        return {"done": True, "delta": ""}
    try:
        data = json.loads(line)
    except json.JSONDecodeError:
        return None
    choices = data.get("choices") or []
    if not choices:
        return {"done": False, "delta": ""}
    choice = choices[0] or {}
    delta = choice.get("delta") or {}
    content = delta.get("content")
    if isinstance(content, list):
        content = "".join(str(item.get("text") or "") for item in content if isinstance(item, dict))
    if content is None:
        content = (choice.get("message") or {}).get("content") or choice.get("text") or ""
    return {"done": False, "delta": str(content or "")}


def pop_speakable_chunks(buffer: str, *, force: bool = False, max_chars: int = 110) -> tuple[list[str], str]:
    text = buffer or ""
    chunks: list[str] = []
    while text:
        match = re.search(r"(.+?[.!?\u3002\uff01\uff1f]+)(?:\s+|$)", text, flags=re.DOTALL)
        if match:
            chunk = clean_text(match.group(1))
            if chunk:
                chunks.append(chunk)
            text = text[match.end() :]
            continue
        if force:
            chunk = clean_text(text)
            if chunk:
                chunks.append(chunk)
            return chunks, ""
        if len(text) >= max_chars:
            split_at = max(text.rfind(" ", 0, max_chars), text.rfind(",", 0, max_chars), text.rfind("，", 0, max_chars))
            if split_at < max_chars // 2:
                split_at = max_chars
            chunk = clean_text(text[:split_at])
            if chunk:
                chunks.append(chunk)
            text = text[split_at:]
            continue
        break
    return chunks, text


async def build_main_llm_request_payload(
    text: str,
    *,
    source: str,
    tool_plan: FastToolPlan | None = None,
) -> tuple[dict[str, Any], str]:
    recent_messages = recent_chat_messages_for_planner(
        text,
        limit=8,
    )
    final_user_text = build_fast_main_llm_user_text(text)
    llm_request = await build_fast_main_llm_request(
        base_system_prompt=FAST_MAIN_LLM_SYSTEM_PROMPT,
        recent_messages=recent_messages,
        user_text=text,
        final_user_text=final_user_text,
        source=source,
        tool_user_text=tool_plan.query if tool_plan is not None else None,
        local_bridge_status_provider=local_bridge_status_snapshot,
    )
    payload = {
        "model": MODEL_NAME,
        "messages": llm_request.messages,
        "temperature": 0.3 if source in {"voice", "local_bridge", "local_mic"} else 0.2,
        "max_tokens": 700,
        "stream": True,
        "cache_prompt": True,
    }
    if MAIN_LLM_STOP_TOKENS:
        payload["stop"] = list(MAIN_LLM_STOP_TOKENS)
    deterministic_reply = (
        llm_request.context.required_evidence_failure_reply
        or llm_request.context.grounded_evidence_reply
    )
    memory_receipt = getattr(llm_request.context, "memory_receipt", None)
    FAST_MEMORY_CONTEXT_RECEIPT.set(
        dict(memory_receipt)
        if isinstance(memory_receipt, dict)
        else reset_fast_memory_context_receipt()
    )
    return payload, deterministic_reply


async def build_main_llm_payload(
    text: str,
    *,
    source: str,
    tool_plan: FastToolPlan | None = None,
) -> dict[str, Any]:
    payload, _failure_reply = await build_main_llm_request_payload(
        text,
        source=source,
        tool_plan=tool_plan,
    )
    return payload


async def iter_main_llm_deltas(
    text: str,
    *,
    source: str,
    tool_plan: FastToolPlan | None = None,
) -> AsyncIterator[str]:
    payload, failure_reply = await build_main_llm_request_payload(
        text,
        source=source,
        tool_plan=tool_plan,
    )
    if failure_reply:
        yield failure_reply
        return
    timeout = ClientTimeout(total=120)
    prefix_filter = ModelStreamPrefixFilter()
    async with ClientSession(timeout=timeout) as session:
        async with session.post(LLM_SERVER_URL, json=payload) as resp:
            if resp.status != 200:
                detail = await resp.text()
                raise RuntimeError(f"main_llm_error {resp.status}: {detail[:300]}")
            content_type = resp.headers.get("Content-Type", "")
            if "application/json" in content_type.lower():
                data = await resp.json()
                choices = data.get("choices") or []
                if choices:
                    filtered = prefix_filter.push(str((choices[0].get("message") or {}).get("content") or ""))
                    if filtered:
                        yield filtered
                    tail = prefix_filter.finish()
                    if tail:
                        yield tail
                return
            async for raw_line in resp.content:
                event = parse_stream_line(raw_line)
                if not event:
                    continue
                if event.get("done"):
                    break
                delta = str(event.get("delta") or "")
                if delta:
                    filtered = prefix_filter.push(delta)
                    if filtered:
                        yield filtered
            tail = prefix_filter.finish()
            if tail:
                yield tail


async def ask_main_llm(
    text: str,
    *,
    source: str,
    tool_plan: FastToolPlan | None = None,
) -> str:
    stream = (
        iter_main_llm_deltas(text, source=source)
        if tool_plan is None
        else iter_main_llm_deltas(text, source=source, tool_plan=tool_plan)
    )
    parts = [
        delta
        async for delta in stream
    ]
    return enforce_registered_tool_capability_truth(visible_text("".join(parts)))


async def ask_main_llm_and_queue_speech(
    text: str,
    *,
    source: str,
    tool_plan: FastToolPlan | None = None,
) -> tuple[str, int]:
    raw_parts: list[str] = []
    clean_seen_len = 0
    sentence_buffer = ""
    emitted_chunks: list[str] = []
    queued_count = 0
    stream = (
        iter_main_llm_deltas(text, source=source)
        if tool_plan is None
        else iter_main_llm_deltas(text, source=source, tool_plan=tool_plan)
    )
    async for delta in stream:
        raw_parts.append(delta)
        cleaned = visible_text("".join(raw_parts))
        new_text = cleaned[clean_seen_len:]
        clean_seen_len = len(cleaned)
        if not new_text:
            continue
        sentence_buffer += new_text
        chunks, sentence_buffer = pop_speakable_chunks(sentence_buffer)
        for chunk in chunks:
            if has_unbacked_progress_claim(chunk):
                continue
            emitted_chunks.append(chunk)
            if queue_local_bridge_speech(chunk, source=source):
                queued_count += 1
    tail_chunks, sentence_buffer = pop_speakable_chunks(sentence_buffer, force=True)
    for chunk in tail_chunks:
        if has_unbacked_progress_claim(chunk):
            continue
        emitted_chunks.append(chunk)
        if queue_local_bridge_speech(chunk, source=source):
            queued_count += 1

    reply = enforce_action_reply_contract(
        enforce_registered_tool_capability_truth(visible_text("".join(raw_parts)))
    )
    emitted_text = clean_text(" ".join(emitted_chunks))
    if not emitted_text:
        reply_chunks, _remainder = pop_speakable_chunks(reply, force=True)
        for chunk in reply_chunks:
            if queue_local_bridge_speech(chunk, source=source):
                queued_count += 1
    elif reply.startswith(emitted_text):
        remainder_chunks, _remainder = pop_speakable_chunks(reply[len(emitted_text) :], force=True)
        for chunk in remainder_chunks:
            if queue_local_bridge_speech(chunk, source=source):
                queued_count += 1
    elif reply != emitted_text:
        reply = enforce_action_reply_contract(emitted_text)
    return reply, queued_count


def render_fast_runtime_status(health: dict[str, Any]) -> str:
    legacy = dict(health.get("legacyServices") or {})
    required_keys = ("botReady", "mainReady", "routerReady", "subReady", "ttsReady", "sttReady")
    core_ready = all(bool(legacy.get(key)) for key in required_keys)
    if not core_ready:
        return clean_text(
            str(health.get("summary") or health.get("overallState") or "runtime status unavailable")
        )

    bridge = local_bridge_status_snapshot()
    bridge_ready = bool(bridge.get("ready")) and not bool(bridge.get("stale"))
    mic = dict(bridge.get("mic") or {})
    mic_enabled = bool(bridge.get("micEnabled", mic.get("enabled", False)))
    minecraft_ready = bool(
        legacy.get("voyagerReady")
        or legacy.get("voyagerHttpReady")
        or legacy.get("voyagerRuntimeReady")
    )
    parts = [
        "이블린 핵심 서비스는 모두 정상 작동 중이야.",
        (
            f"Windows 음성 브리지는 정상이고 마이크는 {'켜져 있어' if mic_enabled else '꺼져 있어'}."
            if bridge_ready
            else "Windows 음성 브리지는 현재 준비되지 않았어."
        ),
        (
            "마인크래프트 서비스도 실행 중이야."
            if minecraft_ready
            else "마인크래프트 서비스는 명령을 받기 전까지 대기 중이야."
        ),
    ]
    return clean_text(" ".join(parts))


async def resolve_pre_llm_reply(text: str, *, source: str) -> str | None:
    normalized = clean_text(text).lower().strip()
    capability_reply = answer_fast_tool_capability_question(text)
    if capability_reply is not None:
        return capability_reply
    datetime_reply = answer_current_datetime_query(text)
    if datetime_reply is not None:
        return datetime_reply
    if normalized in {"/help", "help"}:
        return build_fast_control_help_reply()
    if normalized in {"/status", "status"}:
        manifest = load_service_manifest()
        health = await collect_runtime_health(manifest=manifest, probe_runner=fast_control_probe_runner)
        return render_fast_runtime_status(health)
    if (memory_action := detect_memory_panel_action(text)) is not None:
        return execute_memory_panel_action(memory_action)
    mic_command = detect_local_mic_command(text)
    if mic_command == "on":
        return await execute_local_bridge_mic_control(True, source=source)
    if mic_command == "off":
        return await execute_local_bridge_mic_control(False, source=source)
    if mic_command == "status" or is_local_mic_status_request(text):
        return render_local_mic_status(local_bridge_status_snapshot())
    if normalized in {"/voice", "/voice status", "voice status"}:
        bridge_status = local_bridge_status_snapshot()
        ready = bool(bridge_status.get("ready"))
        error = (
            public_error_code(
                bridge_status.get("lastError"),
                fallback="local_bridge_failed",
            )
            if bridge_status.get("lastError")
            else ""
        )
        mic = dict(bridge_status.get("mic") or {})
        mic_enabled = bool(bridge_status.get("micEnabled", mic.get("enabled", False)))
        reply = (
            f"Windows local I/O bridge는 {'준비됐어' if ready else '준비되지 않았어'}. "
            f"마이크 입력은 {'켜져 있어' if mic_enabled else '꺼져 있어'}."
        )
        if bridge_status.get("stale"):
            reply += f" 마지막 상태는 {bridge_status.get('ageSec')}초 전이야."
        if error:
            reply += f" 오류: {error}"
        return clean_text(reply)

    minecraft_control = detect_minecraft_control_command(text)
    if minecraft_control is not None:
        return await execute_minecraft_control_command(minecraft_control)

    runtime_command = detect_local_runtime_command(text)
    if runtime_command == "restart":
        request_local_restart(source=source, reason="chat_command")
        return local_restart_requested_reply()
    if runtime_command == "shutdown":
        request_local_shutdown(source=source, reason="chat_command")
        return local_shutdown_requested_reply()

    if normalized == "/obsidian":
        return "Obsidian 열기는 이블린 제어 페이지에서 실행할 수 있어."
    if normalized.startswith("/repair"):
        return "런타임 복구 명령은 이블린 제어 페이지의 복구 카드에서 미리보기 후 실행할 수 있어."
    if normalized.startswith(("/voice continuity", "/voice input ", "/voice reconnect", "/voice rejoin")):
        return "그 음성 명령은 현재 로컬 Fast Control에서 지원하지 않아. /voice status와 /mic 명령을 사용해줘."

    if detect_minecraft_runtime_command(text) in {"start", "goal"}:
        return await execute_fast_control_minecraft_runtime_command(
            text,
            source=source,
        )
    if normalized.startswith("/"):
        return f"지원하지 않는 명령이야: {normalized}. /help에서 현재 사용 가능한 명령을 확인해줘."
    return None


def should_skip_fast_tool_planner(text: str) -> bool:
    normalized = clean_text(text).lower().strip()
    if not normalized:
        return True
    if normalized.startswith("/"):
        return True
    if detect_local_runtime_command(text) is not None:
        return True
    if detect_local_mic_command(text) is not None or is_local_mic_status_request(text):
        return True
    if detect_minecraft_control_command(text) is not None:
        return True
    if detect_minecraft_runtime_command(text) is not None:
        return True
    if detect_memory_panel_action(text) is not None:
        return True
    if answer_current_datetime_query(text) is not None:
        return True
    return answer_fast_tool_capability_question(text) is not None


async def plan_fast_tool_request_for_turn(text: str) -> FastToolPlan | None:
    if should_skip_fast_tool_planner(text):
        return None
    return await plan_fast_tool_request(
        text,
        recent_messages=recent_chat_messages_for_planner(text),
    )


def _service_by_id(health: dict[str, Any]) -> dict[str, dict[str, Any]]:
    return {str(service.get("id") or ""): dict(service) for service in health.get("services") or [] if isinstance(service, dict)}


def build_boot_progress(health: dict[str, Any]) -> dict[str, Any]:
    services = _service_by_id(health)
    steps: list[dict[str, Any]] = []
    for service_id, label in BOOT_STEPS:
        service = services.get(service_id) or {}
        ready = bool(service.get("ready") or service.get("state") == "up")
        steps.append(
            {
                "key": service_id,
                "label": label,
                "done": ready,
                "status": "done" if ready else str(service.get("state") or "pending"),
                "detail": str(service.get("reason") or ""),
            }
        )
    done_count = sum(1 for step in steps if step["done"])
    percent = round((done_count / max(1, len(steps))) * 100)
    current = next((step for step in steps if not step["done"]), steps[-1])
    return {
        "percent": percent,
        "phase": "core services ready" if percent >= 100 else f"waiting for {current['label']}",
        "ready": percent >= 100,
        "componentsReady": percent >= 100,
        "done": done_count,
        "total": len(steps),
        "source": "fast_control_api",
        "steps": steps,
    }


def build_default_commands() -> list[dict[str, str]]:
    return build_fast_control_default_commands()


def build_control_state(health: dict[str, Any]) -> dict[str, Any]:
    legacy = dict(health.get("legacyServices") or {})
    services_by_id = _service_by_id(health)
    boot_progress = build_boot_progress(health)
    control_ready = (services_by_id.get("control_page") or {}).get("state") == "up"
    bot_ready = bool(legacy.get("botReady"))
    chat_ready = bool(legacy.get("mainReady") and legacy.get("routerReady"))
    voice_ready = bool(legacy.get("ttsReady") and legacy.get("sttReady"))
    core_ready = bool(
        health.get(
            "ok",
            legacy.get("botReady")
            and legacy.get("mainReady")
            and legacy.get("routerReady")
            and legacy.get("subReady")
            and legacy.get("ttsReady")
            and legacy.get("sttReady"),
        )
    )
    fully_healthy = bool(health.get("fullyHealthy", str(health.get("overallState") or "up") == "up"))
    commands = build_default_commands()
    summary = str(health.get("summary") or health.get("overallState") or "unknown")
    bridge_status = local_bridge_status_snapshot()
    bridge_mic = dict(bridge_status.get("mic") or {})
    bridge_speaking = bool(bridge_status.get("speaking"))
    bridge_listening = bool(bridge_mic.get("captureActive"))
    control_plane = build_control_plane_state(
        bot_ready=bot_ready,
        health_cache=(
            health.get("cache")
            if isinstance(health.get("cache"), dict)
            else None
        ),
    )
    return {
        "ok": core_ready,
        "generatedAt": time.time(),
        "mode": "docker_fast_control",
        "localUrl": f"http://127.0.0.1:{PUBLIC_CONTROL_PORT}/",
        "bootProgress": boot_progress,
        "ui": {
            "mode": "default",
            "submode": (
                "voice-speaking"
                if bridge_speaking
                else "voice-listening"
                if bridge_listening
                else "idle"
                if bot_ready
                else "booting"
            ),
            "reason": "docker_fast_control",
        },
        "commands": commands,
        "allCommands": commands,
        "controlPagePanels": build_control_page_panel_state(),
        "chat": {
            "messages": default_chat_messages(),
            "inputEnabled": chat_ready,
        },
        "actions": {
            **ACTION_COORDINATOR.snapshot(),
            "recovery": (
                FAST_ACTION_RECOVERY_JOURNAL.public_status()
            ),
        },
        "voice": {
            "outputMode": "windows_local_bridge" if bridge_status.get("enabled") else "docker_service",
            "channelName": "로컬 마이크" if bridge_listening else "없음",
            "listening": bridge_listening,
            "speaking": bridge_speaking,
            "ttsTargetName": "로컬 스피커" if bridge_speaking else "없음",
            "localBridge": bridge_status,
        },
        "restart": dict(RESTART_REQUEST),
        "shutdown": dict(SHUTDOWN_REQUEST),
        "runtime": {
            "summary": summary,
            "services": {
                "controlReady": control_ready,
                "botReady": bot_ready,
                "mainReady": bool(legacy.get("mainReady")),
                "routerReady": bool(legacy.get("routerReady")),
                "subReady": bool(legacy.get("subReady")),
                "ttsReady": bool(legacy.get("ttsReady")),
                "sttReady": bool(legacy.get("sttReady")),
                "visionReady": bool(legacy.get("visionReady")),
                "chatReady": chat_ready,
                "voiceReady": voice_ready,
                "coreReady": core_ready,
                "fullReady": fully_healthy,
                "optionalDegraded": bool(health.get("optionalDegraded", not fully_healthy and core_ready)),
                "voyagerHttpReady": bool(legacy.get("voyagerHttpReady")),
                "voyagerRuntimeReady": bool(legacy.get("voyagerRuntimeReady")),
            },
            "controlPlane": control_plane,
            "continuity": (
                FAST_CONTROL_CONTINUITY_OWNER.status()
            ),
            "crossSurfaceContinuity": (
                CROSS_SURFACE_CONTINUITY_BRIDGE.public_status()
            ),
            "bootProgress": boot_progress,
            "capabilities": dict(health.get("capabilities") or {}),
            "serviceHealth": health,
        },
        "statusText": control_plane["statusText"],
    }


async def fast_control_probe_runner(service: ServiceSpec, check: HealthProbeSpec) -> dict[str, Any]:
    if service.id == "bot_api":
        target = f"{check.host}:{check.port}{check.path}"
        return {
            "kind": check.kind,
            "ok": True,
            "reason": "fast_control_self",
            "target": target,
            "status": 200 if check.kind == "http" else None,
            "elapsedMs": 0.0,
        }
    return await default_probe_runner(service, check)


async def collect_fast_runtime_health() -> dict[str, Any]:
    return await collect_runtime_health(
        manifest=load_service_manifest(),
        probe_runner=fast_control_probe_runner,
    )


FAST_RUNTIME_HEALTH_CACHE = RuntimeHealthSnapshotCache(
    collector=collect_fast_runtime_health,
    refresh_after_sec=FAST_RUNTIME_HEALTH_REFRESH_SEC,
    max_stale_sec=FAST_RUNTIME_HEALTH_MAX_STALE_SEC,
)


async def cached_fast_runtime_health(
    *,
    force: bool = False,
) -> dict[str, Any]:
    return public_runtime_health_snapshot(
        await FAST_RUNTIME_HEALTH_CACHE.get(force=force)
    )


async def minecraft_world_lease_owner_context(
    _: web.Application,
):
    MINECRAFT_WORLD_LEASE_OWNER.initialize()
    await MINECRAFT_WORLD_LEASE_OWNER.ensure_started()
    try:
        yield
    finally:
        await MINECRAFT_WORLD_LEASE_OWNER.shutdown(
            reason="shutdown"
        )


async def health_handler(_: web.Request) -> web.StreamResponse:
    return json_response(
        {
            "ok": True,
            "role": "fast-control-bot-api",
            "port": PORT,
            "minecraftWorldLease": (
                MINECRAFT_WORLD_LEASE_OWNER.status()
            ),
        }
    )


async def minecraft_world_lease_status_handler(
    _: web.Request,
) -> web.StreamResponse:
    return json_response(
        {
            "ok": True,
            "leaseStatus": MINECRAFT_WORLD_LEASE_OWNER.status(),
        }
    )


async def minecraft_world_lease_mutation_handler(
    request: web.Request,
) -> web.StreamResponse:
    presented_token = request.headers.get(
        MINECRAFT_WORLD_LEASE_DELEGATION_TOKEN_HEADER,
        "",
    )
    expected_token = (
        MINECRAFT_WORLD_LEASE_OWNER.delegation_token()
    )
    if not minecraft_world_lease_delegation_authorized(
        expected_token=expected_token,
        presented_token=presented_token,
    ):
        return json_response(
            {
                "ok": False,
                "error": (
                    "minecraft_world_lease_delegation_unauthorized"
                ),
            },
            status=401,
        )
    try:
        payload = await request.json()
    except Exception:
        return json_response(
            {
                "ok": False,
                "error": "minecraft_world_payload_invalid",
            },
            status=400,
        )
    action = clean_text(
        request.match_info.get("action")
    ).lower()
    try:
        response = (
            await execute_minecraft_world_lease_delegation(
                MINECRAFT_WORLD_LEASE_OWNER,
                action=action,
                payload=payload,
            )
        )
    except Exception as exc:
        error = minecraft_world_lease_delegation_error_code(
            exc
        )
        return json_response(
            {"ok": False, "error": error},
            status=(
                503
                if error == "minecraft_service_unavailable"
                else 409
            ),
        )
    return json_response(response)


async def state_handler(_: web.Request) -> web.StreamResponse:
    health = await cached_fast_runtime_health()
    return json_response(build_control_state(health))


async def chat_handler(request: web.Request) -> web.StreamResponse:
    try:
        payload = await request.json()
    except Exception:
        return json_response({"ok": False, "error": "invalid_json"}, status=400)
    text = clean_text((payload or {}).get("text"))
    if not text:
        return json_response({"ok": False, "error": "empty_text"}, status=400)
    source = clean_text((payload or {}).get("source")) or "control_page"
    action_id = (
        (payload or {}).get("turnId")
        or (payload or {}).get("requestId")
        or ""
    )
    reset_fast_memory_context_receipt()
    suppress_tts = should_suppress_tts_for_command(text)
    append_chat_message("user", "정훈", text, source=source)
    tool_plan: FastToolPlan | None = None
    queued_speech_count = 0
    error_code = ""
    task_record: FastActionTask | None = None
    task_runner: Callable[[str, str], Awaitable[str]] | None = None
    memory_write_receipt: dict[str, Any] | None = None
    try:
        (
            memory_command_matched,
            memory_command_reply,
            memory_write_receipt,
            memory_command_error,
        ) = execute_explicit_memory_confirmation(
            text,
            action_id=action_id,
        )
        if memory_command_matched:
            reply = memory_command_reply
            error_code = memory_command_error
        else:
            tool_plan = await plan_fast_tool_request_for_turn(
                text
            )
            pre_llm_reply = await resolve_pre_llm_reply(text, source=source)
            if pre_llm_reply is not None:
                reply = pre_llm_reply
            else:
                prepared_action = prepare_tool_plan_background_action(
                    tool_plan,
                    text,
                    source=source,
                ) or prepare_registered_background_action(text, source=source)
                if prepared_action is not None:
                    task_record, task_runner = prepared_action
                    reply = task_record.start_reply
                else:
                    if should_queue_local_bridge_speech(source):
                        if tool_plan is None:
                            reply, queued_speech_count = await ask_main_llm_and_queue_speech(text, source=source)
                        else:
                            reply, queued_speech_count = await ask_main_llm_and_queue_speech(
                                text,
                                source=source,
                                tool_plan=tool_plan,
                            )
                    else:
                        if tool_plan is None:
                            reply = await ask_main_llm(text, source=source)
                        else:
                            reply = await ask_main_llm(text, source=source, tool_plan=tool_plan)
            if not reply:
                reply = "응답이 비어 있었어. 다시 한 번 말해줘."
        reply = enforce_registered_tool_capability_truth(
            enforce_action_reply_contract(
                reply,
                active_task_id=task_record.task_id if task_record is not None else None,
            )
        )
    except Exception as exc:
        error_code = "fast_control_chat_failed"
        print(
            "[FAST CONTROL] chat_failed "
            f"errorType={type(exc).__name__}",
            flush=True,
        )
        if task_record is not None and task_record.status == "running":
            ACTION_COORDINATOR.fail(
                task_record.task_id,
                error_code,
                reply=public_failure_message(error_code),
            )
        reply = public_failure_message(error_code)
        task_runner = None
    append_chat_message(
        "assistant",
        "Evelyn",
        reply,
        source="fast_control_api",
        task_id=task_record.task_id if task_record is not None else None,
        task_status=task_record.status if task_record is not None else None,
        memory_receipt=current_fast_memory_context_receipt(),
        memory_write_receipt=memory_write_receipt,
    )
    continuity = (
        commit_fast_control_terminal_turn(
            task_record.task_id,
            text,
            reply,
        )
        if (
            task_record is not None
            and task_record.status != "running"
        )
        else commit_fast_control_turn(text, reply)
    )
    if not suppress_tts and should_queue_local_bridge_speech(source) and queued_speech_count <= 0:
        queue_local_bridge_speech(reply, source=source)
    if task_record is not None and task_runner is not None and task_record.status == "running":
        launch_background_action(task_record, task_runner)
    health = await cached_fast_runtime_health()
    result: dict[str, Any] = {
        "ok": not bool(error_code),
        "reply": reply,
        "suppressTts": suppress_tts,
        "state": build_control_state(health),
        "continuity": continuity,
        "memoryReceipt": current_fast_memory_context_receipt(),
    }
    if memory_write_receipt is not None:
        result["memoryWriteReceipt"] = memory_write_receipt
    if error_code:
        result["error"] = error_code
    if task_record is not None:
        result["task"] = task_record.to_dict()
    return json_response(result)


async def write_stream_event(response: web.StreamResponse, payload: dict[str, Any]) -> None:
    await response.write((json.dumps(payload, ensure_ascii=False) + "\n").encode("utf-8"))


async def chat_stream_handler(request: web.Request) -> web.StreamResponse:
    try:
        payload = await request.json()
    except Exception:
        return json_response({"ok": False, "error": "invalid_json"}, status=400)
    text = clean_text((payload or {}).get("text"))
    if not text:
        return json_response({"ok": False, "error": "empty_text"}, status=400)
    source = clean_text((payload or {}).get("source")) or "local_bridge"
    action_id = (
        (payload or {}).get("turnId")
        or (payload or {}).get("requestId")
        or ""
    )
    reset_fast_memory_context_receipt()
    suppress_tts = should_suppress_tts_for_command(text)
    append_chat_message("user", "정훈", text, source=source)
    tool_plan: FastToolPlan | None = None

    response = web.StreamResponse(
        status=200,
        headers={
            "Content-Type": "application/x-ndjson; charset=utf-8",
            "Cache-Control": "no-cache",
            "X-Accel-Buffering": "no",
        },
    )
    await response.prepare(request)

    started_at = time.perf_counter()
    first_sentence_ms: float | None = None
    first_delta_ms: float | None = None
    first_progress_ms: float | None = None
    raw_parts: list[str] = []
    clean_seen_len = 0
    sentence_buffer = ""
    reply = ""
    emitted_chunks: list[str] = []
    task_record: FastActionTask | None = None
    task_runner: Callable[[str, str], Awaitable[str]] | None = None
    speech_filter = SafeIncrementalSpeechFilter()
    memory_write_receipt: dict[str, Any] | None = None
    memory_command_error = ""

    async def emit_delta(fragment: str) -> None:
        nonlocal first_delta_ms
        if not fragment:
            return
        elapsed_ms = (time.perf_counter() - started_at) * 1000.0
        if first_delta_ms is None:
            first_delta_ms = elapsed_ms
        await write_stream_event(
            response,
            {
                "type": "delta",
                "text": fragment,
                "suppressTts": suppress_tts,
                "elapsedMs": round(elapsed_ms, 1),
            },
        )

    async def emit_progress(text: str, *, stage: str) -> None:
        nonlocal first_progress_ms
        progress_text = clean_text(text)
        if not progress_text:
            return
        elapsed_ms = (time.perf_counter() - started_at) * 1000.0
        if first_progress_ms is None:
            first_progress_ms = elapsed_ms
        await write_stream_event(
            response,
            {
                "type": "progress",
                "text": progress_text,
                "stage": clean_text(stage),
                "requiresContinuation": True,
                "terminal": False,
                "suppressTts": suppress_tts,
                "elapsedMs": round(elapsed_ms, 1),
            },
        )

    async def emit_sentence(sentence: str) -> None:
        nonlocal first_sentence_ms
        chunk = clean_text(sentence)
        if not chunk:
            return
        elapsed_ms = (time.perf_counter() - started_at) * 1000.0
        if first_sentence_ms is None:
            first_sentence_ms = elapsed_ms
        emitted_chunks.append(chunk)
        await write_stream_event(
            response,
            {
                "type": "sentence",
                "text": chunk,
                "suppressTts": suppress_tts,
                "elapsedMs": round(elapsed_ms, 1),
            },
        )

    try:
        (
            memory_command_matched,
            memory_command_reply,
            memory_write_receipt,
            memory_command_error,
        ) = execute_explicit_memory_confirmation(
            text,
            action_id=action_id,
        )
        if memory_command_matched:
            reply = enforce_action_reply_contract(
                memory_command_reply
            )
            await emit_sentence(reply)
        else:
            tool_plan = await plan_fast_tool_request_for_turn(
                text
            )
            pre_llm_reply = await resolve_pre_llm_reply(text, source=source)
            if pre_llm_reply is not None:
                reply = enforce_action_reply_contract(pre_llm_reply)
                await emit_sentence(reply)
            else:
                prepared_action = prepare_tool_plan_background_action(
                    tool_plan,
                    text,
                    source=source,
                ) or prepare_registered_background_action(text, source=source)
                if prepared_action is not None:
                    task_record, task_runner = prepared_action
                    reply = enforce_action_reply_contract(
                        task_record.start_reply,
                        active_task_id=task_record.task_id,
                    )
                    await emit_sentence(reply)
                else:
                    if should_emit_memory_recall_progress(text, source=source):
                        await emit_progress(
                            next_memory_recall_progress_text(),
                            stage="memory_recall",
                        )
                    llm_stream = (
                        iter_main_llm_deltas(text, source=source)
                        if tool_plan is None
                        else iter_main_llm_deltas(text, source=source, tool_plan=tool_plan)
                    )
                    async for delta in llm_stream:
                        raw_parts.append(delta)
                        cleaned = visible_text("".join(raw_parts))
                        new_text = cleaned[clean_seen_len:]
                        clean_seen_len = len(cleaned)
                        if not new_text:
                            continue
                        for safe_fragment in speech_filter.push(new_text):
                            await emit_delta(safe_fragment)
                        sentence_buffer += new_text
                        chunks, sentence_buffer = pop_speakable_chunks(sentence_buffer)
                        for chunk in chunks:
                            if has_unbacked_progress_claim(chunk):
                                continue
                            await emit_sentence(chunk)
                    for safe_fragment in speech_filter.finish():
                        await emit_delta(safe_fragment)
                    tail_chunks, sentence_buffer = pop_speakable_chunks(sentence_buffer, force=True)
                    for chunk in tail_chunks:
                        if has_unbacked_progress_claim(chunk):
                            continue
                        await emit_sentence(chunk)
                    reply = enforce_registered_tool_capability_truth(
                        enforce_action_reply_contract(visible_text("".join(raw_parts)))
                    )
                    if not reply:
                        reply = "답변이 비어 있었어. 다시 한 번 말해줘."
                    emitted_text = clean_text(" ".join(emitted_chunks))
                    if not emitted_text:
                        await emit_sentence(reply)
                    elif reply.startswith(emitted_text):
                        await emit_sentence(reply[len(emitted_text) :])
                    elif reply != emitted_text:
                        reply = enforce_action_reply_contract(emitted_text)
        append_chat_message(
            "assistant",
            "Evelyn",
            reply,
            source="fast_control_api_stream",
            task_id=task_record.task_id if task_record is not None else None,
            task_status=task_record.status if task_record is not None else None,
            memory_receipt=current_fast_memory_context_receipt(),
            memory_write_receipt=memory_write_receipt,
        )
        continuity = commit_fast_control_turn(text, reply)
        await write_stream_event(
            response,
            {
                "type": "done",
                "ok": not bool(memory_command_error),
                "error": memory_command_error,
                "reply": reply,
                "suppressTts": suppress_tts,
                "taskId": task_record.task_id if task_record is not None else None,
                "taskStatus": task_record.status if task_record is not None else None,
                "continuity": continuity,
                "memoryReceipt": current_fast_memory_context_receipt(),
                "memoryWriteReceipt": memory_write_receipt,
                "firstSentenceMs": round(first_sentence_ms, 1) if first_sentence_ms is not None else None,
                "firstDeltaMs": round(first_delta_ms, 1) if first_delta_ms is not None else None,
                "firstProgressMs": round(first_progress_ms, 1) if first_progress_ms is not None else None,
                "elapsedMs": round((time.perf_counter() - started_at) * 1000.0, 1),
            },
        )
        if task_record is not None and task_runner is not None and task_record.status == "running":
            launch_background_action(task_record, task_runner)
    except Exception as exc:
        error_code = "fast_control_stream_failed"
        failure_reply = public_failure_message(error_code)
        print(
            "[FAST CONTROL] chat_stream_failed "
            f"errorType={type(exc).__name__}",
            flush=True,
        )
        if task_record is not None and task_record.status == "running":
            failed = ACTION_COORDINATOR.fail(
                task_record.task_id,
                error_code,
                reply=failure_reply,
            )
            failure_reply = failed.final_reply
            append_chat_message(
                "assistant",
                "Evelyn",
                failed.final_reply,
                source="fast_control_action_followup",
                task_id=failed.task_id,
                task_status=failed.status,
                memory_receipt=current_fast_memory_context_receipt(),
            )
        else:
            append_chat_message(
                "assistant",
                "Evelyn",
                failure_reply,
                source="fast_control_api_stream",
                memory_receipt=current_fast_memory_context_receipt(),
            )
        continuity = (
            commit_fast_control_terminal_turn(
                task_record.task_id,
                text,
                failure_reply,
            )
            if task_record is not None
            else commit_fast_control_turn(
                text,
                failure_reply,
            )
        )
        await write_stream_event(
            response,
            {
                "type": "error",
                "ok": False,
                "error": error_code,
                "message": failure_reply,
                "memoryReceipt": current_fast_memory_context_receipt(),
                "continuity": continuity,
            },
        )
    await response.write_eof()
    return response


async def local_bridge_status_handler(request: web.Request) -> web.StreamResponse:
    speak_requests: list[dict[str, Any]] = []
    if request.method == "POST":
        try:
            payload = await request.json()
        except Exception:
            payload = {}
        if isinstance(payload, dict):
            LOCAL_BRIDGE_STATUS.update(payload)
            try:
                minecraft_revision = int(payload.get("minecraftCommandRevision") or 0)
            except (TypeError, ValueError):
                minecraft_revision = 0
            minecraft_state = clean_text(payload.get("minecraftCommandState")).lower()
            if minecraft_revision and minecraft_state in {"ready", "failed"}:
                clear_local_bridge_minecraft_command_request(minecraft_revision)
        LOCAL_BRIDGE_STATUS["enabled"] = True
        LOCAL_BRIDGE_STATUS["updatedAt"] = time.time()
        speak_requests = drain_local_bridge_speak_requests()
    return json_response(
        {
            "ok": True,
            "localBridge": local_bridge_status_snapshot(),
            "speakRequests": speak_requests,
            "outputDeviceRequest": dict(LOCAL_BRIDGE_OUTPUT_DEVICE_REQUEST)
            if LOCAL_BRIDGE_OUTPUT_DEVICE_REQUEST.get("outputDevice")
            else {},
            "micControlRequest": dict(LOCAL_BRIDGE_MIC_CONTROL_REQUEST),
            "minecraftCommandRequest": dict(LOCAL_BRIDGE_MINECRAFT_COMMAND_REQUEST),
            "restart": dict(RESTART_REQUEST),
            "shutdown": dict(SHUTDOWN_REQUEST),
        }
    )


async def local_bridge_mic_handler(request: web.Request) -> web.StreamResponse:
    if request.method == "GET":
        return json_response(
            {
                "ok": True,
                "request": dict(LOCAL_BRIDGE_MIC_CONTROL_REQUEST),
                "localBridge": local_bridge_status_snapshot(),
            }
        )
    try:
        payload = await request.json()
    except Exception:
        return json_response({"ok": False, "error": "invalid_json"}, status=400)
    value = (payload or {}).get("enabled")
    if not isinstance(value, bool):
        action = clean_text((payload or {}).get("action")).lower()
        if action in {"on", "enable", "start"}:
            value = True
        elif action in {"off", "disable", "stop"}:
            value = False
        else:
            return json_response({"ok": False, "error": "missing_mic_enabled"}, status=400)
    request_state = request_local_bridge_mic_control(
        value,
        source=clean_text((payload or {}).get("source")) or "control_page",
    )
    result = await wait_for_local_bridge_mic_control(request_state)
    return json_response(
        {"ok": bool(result.get("applied")), **result},
        status=200 if result.get("applied") else 202,
    )


async def local_bridge_output_device_handler(request: web.Request) -> web.StreamResponse:
    if request.method == "GET":
        return json_response(
            {
                "ok": True,
                "selection": dict(LOCAL_BRIDGE_OUTPUT_DEVICE_REQUEST),
                "localBridge": local_bridge_status_snapshot(),
            }
        )
    try:
        payload = await request.json()
    except Exception:
        return json_response({"ok": False, "error": "invalid_json"}, status=400)
    output_device = clean_text((payload or {}).get("outputDevice"))
    if not output_device:
        return json_response({"ok": False, "error": "missing_output_device"}, status=400)
    selection = set_local_bridge_output_device(
        output_device,
        source=clean_text((payload or {}).get("source")) or "control_page",
    )
    return json_response(
        {
            "ok": True,
            "selection": selection,
            "localBridge": local_bridge_status_snapshot(),
        }
    )


async def local_bridge_test_tts_handler(request: web.Request) -> web.StreamResponse:
    try:
        payload = await request.json()
    except Exception:
        payload = {}
    text = clean_text((payload or {}).get("text")) or "이블린 오디오 테스트입니다."
    queued = queue_local_bridge_speech(text, source="control_page_audio_test")
    if queued is None:
        return json_response({"ok": False, "error": "local_bridge_not_ready"}, status=409)
    return json_response({"ok": True, "request": queued, "localBridge": local_bridge_status_snapshot()})


async def ui_action_status_handler(_: web.Request) -> web.StreamResponse:
    local_bridge = local_bridge_status_snapshot()
    status = (
        local_bridge.get("hostUiAction")
        if isinstance(local_bridge, dict)
        else None
    )
    return json_response(
        {
            "ok": bool(
                isinstance(status, dict)
                and status.get("state") == "running"
                and status.get("auditReady") is True
            ),
            "schema": "ui_action.control-status.v1",
            "status": dict(status or {}),
            "policy": {
                "requiresExplicitConfirmation": True,
                "automaticRetry": False,
                "arbitraryCoordinates": False,
                "allowedActions": ["invoke"],
                "allowedControlTypes": ["Button"],
            },
        }
    )


async def ui_action_preview_handler(
    request: web.Request,
) -> web.StreamResponse:
    try:
        payload = await request.json()
    except Exception:
        return json_response(
            {"ok": False, "error": "invalid_json"},
            status=400,
        )
    if not isinstance(payload, dict) or set(payload) != {
        "elementId",
        "action",
        "postcondition",
    }:
        return json_response(
            {"ok": False, "error": "ui_action_invalid_preview_request"},
            status=400,
        )
    result = await preview_host_ui_action(
        element_id=str(payload.get("elementId") or ""),
        action=str(payload.get("action") or ""),
        postcondition=str(payload.get("postcondition") or ""),
    )
    return json_response(
        result,
        status=200 if result.get("ok") else 409,
    )


async def ui_action_targets_handler(
    request: web.Request,
) -> web.StreamResponse:
    try:
        payload = await request.json()
    except Exception:
        return json_response(
            {"ok": False, "error": "invalid_json"},
            status=400,
        )
    if not isinstance(payload, dict) or payload:
        return json_response(
            {"ok": False, "error": "ui_action_invalid_discover_request"},
            status=400,
        )
    result = await discover_host_ui_action()
    return json_response(
        result,
        status=200 if result.get("ok") else 409,
    )


async def ui_action_apply_handler(
    request: web.Request,
) -> web.StreamResponse:
    try:
        payload = await request.json()
    except Exception:
        return json_response(
            {"ok": False, "error": "invalid_json"},
            status=400,
        )
    if (
        not isinstance(payload, dict)
        or set(payload) != {"confirmToken", "userConfirmed"}
        or payload.get("userConfirmed") is not True
    ):
        return json_response(
            {"ok": False, "error": "ui_action_explicit_confirmation_required"},
            status=400,
        )
    result = await apply_host_ui_action(
        confirm_token=str(payload.get("confirmToken") or ""),
    )
    return json_response(
        result,
        status=200 if result.get("ok") else 409,
    )


async def action_events_handler(request: web.Request) -> web.StreamResponse:
    try:
        after = int(clean_text(request.query.get("after")) or "0")
    except (TypeError, ValueError):
        return json_response({"ok": False, "error": "invalid_after_cursor"}, status=400)
    snapshot = ACTION_COORDINATOR.snapshot()
    return json_response(
        {
            "ok": True,
            "after": max(0, after),
            "lastEventId": snapshot["lastEventId"],
            "activeCount": snapshot["activeCount"],
            "events": ACTION_COORDINATOR.events_after(after),
            "tasks": snapshot["tasks"],
        }
    )


async def shutdown_handler(request: web.Request) -> web.StreamResponse:
    try:
        payload = await request.json()
    except Exception:
        payload = {}
    source = clean_text((payload or {}).get("source")) or "control_page"
    reason = clean_text((payload or {}).get("reason")) or "shutdown_endpoint"
    return json_response(request_local_shutdown(source=source, reason=reason))


def create_app(
    *,
    enable_minecraft_world_lease_owner: bool | None = None,
) -> web.Application:
    register_builtin_background_action_handlers()
    recover_fast_control_actions_after_restart()
    app = web.Application(middlewares=[reject_browser_origin_middleware])
    owner_enabled = (
        MINECRAFT_WORLD_LEASE_OWNER_ENABLED
        if enable_minecraft_world_lease_owner is None
        else bool(enable_minecraft_world_lease_owner)
    )
    if owner_enabled:
        app.cleanup_ctx.append(
            minecraft_world_lease_owner_context
        )
    app.router.add_get("/health", health_handler)
    app.router.add_get(
        "/internal/minecraft-world-lease",
        minecraft_world_lease_status_handler,
    )
    app.router.add_post(
        "/internal/minecraft-world-lease/{action}",
        minecraft_world_lease_mutation_handler,
    )
    app.router.add_get("/api/control-page/state", state_handler)
    app.router.add_post("/api/control-page/chat", chat_handler)
    app.router.add_post("/api/control-page/chat-stream", chat_stream_handler)
    app.router.add_get("/api/control-page/action-events", action_events_handler)
    app.router.add_post("/api/control-page/shutdown", shutdown_handler)
    app.router.add_get("/api/local-bridge/status", local_bridge_status_handler)
    app.router.add_post("/api/local-bridge/status", local_bridge_status_handler)
    app.router.add_get("/api/local-bridge/mic", local_bridge_mic_handler)
    app.router.add_post("/api/local-bridge/mic", local_bridge_mic_handler)
    app.router.add_get("/api/local-bridge/output-device", local_bridge_output_device_handler)
    app.router.add_post("/api/local-bridge/output-device", local_bridge_output_device_handler)
    app.router.add_post("/api/local-bridge/test-tts", local_bridge_test_tts_handler)
    app.router.add_get(
        "/api/control-page/ui-action",
        ui_action_status_handler,
    )
    app.router.add_post(
        "/api/control-page/ui-action/targets",
        ui_action_targets_handler,
    )
    app.router.add_post(
        "/api/control-page/ui-action/preview",
        ui_action_preview_handler,
    )
    app.router.add_post(
        "/api/control-page/ui-action/apply",
        ui_action_apply_handler,
    )
    return app


def main() -> None:
    web.run_app(create_app(), host=HOST, port=PORT)


if __name__ == "__main__":
    main()
