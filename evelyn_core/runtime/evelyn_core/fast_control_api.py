from __future__ import annotations

import asyncio
import contextlib
from contextvars import ContextVar
import hmac
import hashlib
import json
import math
import os
import random
import re
import secrets
import time
from pathlib import Path
from typing import Any, AsyncIterator, Awaitable, Callable

from aiohttp import ClientSession, ClientTimeout, web

from .control_page_http import reject_browser_origin_middleware
from .control_page_memory_http import control_page_memory_handoff_headers
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
from .continuity_authenticity import (
    load_continuity_authenticity,
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
    bind_fast_tool_plan_memory_exposure,
    enforce_registered_tool_capability_truth,
    plan_fast_tool_request,
)
from .fast_control_continuity import (
    FAST_CONTROL_EPHEMERAL_VALIDATION_DELIVERY_REF,
    FAST_CONTROL_INGRESS_SURFACE,
    FAST_CONTROL_SESSION_KEY,
    FastControlContinuityOwner,
)
from .conversation_ingress_recovery import (
    CONVERSATION_INGRESS_RESERVATION_REVOCATION_RECEIPT_SCHEMA,
    ConversationIngressBindingMismatch,
    ConversationIngressRecoveryError,
    conversation_ingress_entry_id,
    final_text_sha256,
)
from .explicit_memory_confirmation import (
    execute_explicit_memory_confirmation,
)
from .cross_surface_continuity import (
    CrossSurfaceContinuityBridge,
    CrossSurfaceContinuityConfig,
)
from .paths import get_repo_root, get_runtime_artifacts_root
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
from .local_voice_admission import (
    LocalVoiceAdmissionManager,
    LocalVoiceAdmissionTransactionError,
    LocalVoiceDurableReservationRevocation,
    LocalVoiceDurableIssuanceReservation,
    LocalVoiceDurableIngressClaim,
    LocalVoiceReservationRevocationRequest,
    LocalVoiceIssuanceReservationRequest,
    LocalVoiceIngressClaimRequest,
    normalize_validation_binding,
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
from .memory_deletion_journal import (
    MEMORY_DELETION_JOURNAL_INTEGRITY_ERROR,
    MemoryDeletionJournalIntegrityError,
    memory_deletion_journal_guard,
)
from .memory_deletion_outbound import (
    capture_memory_deletion_outbound_position,
    reset_memory_deletion_outbound_position,
)
from .conversation_memory_exposure import (
    capture_combined_memory_exposure,
    filter_conversation_history_for_memory_exposure,
    memory_exposure_position_from_receipt,
    memory_receipt_ref_from_exposure,
)
from .conversation_memory_receipt import (
    memory_receipt_ref_from_receipt,
    merge_memory_receipt_refs,
    not_used_memory_receipt_ref,
    sanitize_memory_receipt_ref,
    unattributed_memory_receipt_ref,
)
from .config import MEMORY_ROOT
from .memory_exposure import (
    MemoryExposurePosition,
    current_memory_exposure_position,
    memory_exposure_guard,
    memory_exposure_position_from_dict,
    memory_exposure_position_to_dict,
    memory_exposure_request,
    reset_memory_exposure_position,
)
from .memory_prompt_policy import (
    MEMORY_CONTEXT_USE_POLICY,
    memory_deletion_boundary_not_required,
)
from .query_intents import answer_current_datetime_query
from .runtime_health import (
    collect_runtime_health,
    default_probe_runner,
    public_runtime_health_snapshot,
)
from .runtime_health_snapshot_cache import (
    RuntimeHealthSnapshotCache,
)
from .runtime_source_identity import runtime_source_identity
from .runtime_services import HealthProbeSpec, ServiceSpec, load_service_manifest
from .text import (
    ModelStreamPrefixFilter,
    should_suppress_tts_for_command,
    visible_text as shared_visible_text,
)
from .voice_validation import (
    emit_voice_validation_event,
    validation_attempt_binding_is_current,
    validation_transcript_admission_status,
)
from .voice_validation_attempt_lease import (
    VoiceValidationAttemptLeaseBusy,
    VoiceValidationAttemptLeaseSet,
    VoiceValidationAttemptLeaseUnavailable,
    acquire_attempt_lease,
)
from .voice_capture_consent import (
    HOST_LEASE_STALE_SEC,
    WATCHDOG_STATUS_SCHEMA,
    voice_capture_consent_fence_matches,
)
from .voice_capture_consent_claim_lease import (
    VoiceCaptureConsentClaimLeaseBusy,
    VoiceCaptureConsentClaimLeaseUnavailable,
    acquire_voice_capture_consent_claim_lease,
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
FAST_MEMORY_DELETION_POSITION: ContextVar[Any | None] = ContextVar(
    "fast_memory_deletion_position",
    default=None,
)
FAST_MEMORY_EXPOSURE_POSITION: ContextVar[
    MemoryExposurePosition | None
] = ContextVar(
    "fast_memory_exposure_position",
    default=None,
)
FAST_HISTORY_MEMORY_RECEIPT_REF: ContextVar[
    dict[str, Any] | None
] = ContextVar(
    "fast_history_memory_receipt_ref",
    default=None,
)
FAST_VALIDATION_ATTEMPT_LEASE: ContextVar[
    VoiceValidationAttemptLeaseSet | None
] = ContextVar(
    "fast_validation_attempt_lease",
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
FAST_CONTROL_HTTP_DELIVERY_REF = "fast-control:http-json"
FAST_CONTROL_STREAM_DELIVERY_REF = "fast-control:http-ndjson"
FAST_CONTROL_INGRESS_PENDING_ERROR = (
    "conversation_ingress_request_pending"
)
FAST_CONTROL_INGRESS_REPLAY_ERROR = (
    "conversation_ingress_cached_response_unavailable"
)
FAST_CONTROL_INGRESS_REDELIVERY_SUPPRESSED_ERROR = (
    "conversation_ingress_completed_redelivery_suppressed"
)
FAST_CONTROL_CONTINUITY_ENABLED = (
    os.getenv(
        "FAST_CONTROL_CONTINUITY_ENABLED",
        "",
    ).strip().lower()
    in {"1", "true", "yes", "on"}
)
LOCAL_BRIDGE_STALE_AFTER_SEC = max(3.0, float(os.getenv("LOCAL_BRIDGE_STALE_AFTER_SEC", "8.0")))
LOCAL_BRIDGE_STATUS_AUTH_HEADER = "X-Evelyn-Local-Bridge-Token"
LOCAL_BRIDGE_STATUS_AUTH_TOKEN = os.getenv(
    "LOCAL_BRIDGE_STATUS_AUTH_TOKEN",
    "",
).strip()
EVELYN_INTERNAL_CONTROL_HEADER = "X-Evelyn-Internal-Control-Token"
EVELYN_INTERNAL_CONTROL_TOKEN = os.getenv(
    "EVELYN_INTERNAL_CONTROL_TOKEN",
    "",
).strip()
LOCAL_BRIDGE_AUTH_TOKEN_MIN_LENGTH = 32
LOCAL_BRIDGE_HEARTBEAT_MAX_SKEW_SEC = max(
    5.0,
    float(os.getenv("LOCAL_BRIDGE_HEARTBEAT_MAX_SKEW_SEC", "30")),
)
LOCAL_BRIDGE_STATUS_MAX_BYTES = max(
    4096,
    int(os.getenv("LOCAL_BRIDGE_STATUS_MAX_BYTES", "131072")),
)
LOCAL_BRIDGE_MIC_ENABLE_FENCE_SCHEMA = (
    "local_io_bridge.mic-enable-fence.v1"
)
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

CONTINUITY_ARTIFACTS_ROOT = get_runtime_artifacts_root()
VOICE_CAPTURE_HOST_LEASE_PATH = (
    CONTINUITY_ARTIFACTS_ROOT
    / "voice_capture_consent"
    / "owner_heartbeat.json"
)
VOICE_CAPTURE_CONSENT_STATE_PATH = (
    CONTINUITY_ARTIFACTS_ROOT
    / "voice_capture_consent"
    / "state.json"
)
CONTINUITY_AUTHENTICITY = load_continuity_authenticity(
    protected_root=get_repo_root(),
    additional_protected_roots=(CONTINUITY_ARTIFACTS_ROOT,),
)
FAST_CONTROL_CONTINUITY_OWNER = FastControlContinuityOwner(
    artifacts_root=CONTINUITY_ARTIFACTS_ROOT,
    enabled=FAST_CONTROL_CONTINUITY_ENABLED,
    authenticity=CONTINUITY_AUTHENTICITY,
)
FAST_ACTION_RECOVERY_JOURNAL = FastActionRecoveryJournal(
    path=(
        get_runtime_artifacts_root()
        / "fast_control_actions"
        / "recovery.json"
    ),
    enabled=FAST_CONTROL_CONTINUITY_ENABLED,
    authenticity=CONTINUITY_AUTHENTICITY,
)
CROSS_SURFACE_CONTINUITY_BRIDGE = CrossSurfaceContinuityBridge(
    artifacts_root=CONTINUITY_ARTIFACTS_ROOT,
    config=CrossSurfaceContinuityConfig.from_env(),
    authenticity=CONTINUITY_AUTHENTICITY,
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
LOCAL_VOICE_ADMISSION = LocalVoiceAdmissionManager()
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
    "actionId": "",
    "enabled": None,
    "requestedAt": None,
    "source": "",
    "purpose": "",
    "bridgeInstanceDigest": "",
}
LOCAL_BRIDGE_MIC_CONTROL_ACK_SCHEMA = "local_io_bridge.mic-control-ack.v1"
LOCAL_BRIDGE_MIC_ENABLE_FENCE: dict[str, Any] = {
    "schema": LOCAL_BRIDGE_MIC_ENABLE_FENCE_SCHEMA,
    "epoch": secrets.token_hex(16),
    "disableGeneration": 0,
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


class MemoryGuardedJsonResponse(web.Response):
    """Keep the verified memory boundary through the actual HTTP write."""

    def __init__(
        self,
        payload: dict[str, Any],
        *,
        expected_position: MemoryExposurePosition | None,
        status: int = 200,
        after_write: Callable[[], None] | None = None,
        before_write: Callable[[], None] | None = None,
        after_write_failure: Callable[[str], None] | None = None,
        after_terminal: Callable[[], None] | None = None,
    ) -> None:
        super().__init__(
            text=json.dumps(payload, ensure_ascii=False),
            status=status,
            content_type="application/json",
            charset="utf-8",
        )
        self._memory_expected_position = expected_position
        self._memory_guard: Any | None = None
        self._memory_guard_disabled = False
        self._before_write = before_write
        self._before_write_called = False
        self._after_write_success = after_write
        self._after_write_success_called = False
        self._after_write_failure = after_write_failure
        self._after_write_failure_called = False
        self._after_terminal = after_terminal
        self._after_terminal_called = False
        self.headers["Cache-Control"] = "no-store"
        self.headers.update(
            control_page_memory_handoff_headers(expected_position)
        )

    def _replace_with_integrity_failure(self) -> None:
        """Discard the original body before any response headers are sent."""

        self._memory_expected_position = None
        self._memory_guard_disabled = True
        self._before_write = None
        self._after_write_success = None
        self._after_write_failure = None
        self.set_status(503)
        self.text = json.dumps(
            {
                "ok": False,
                "error": MEMORY_DELETION_JOURNAL_INTEGRITY_ERROR,
            },
            ensure_ascii=False,
        )
        self.headers.update(
            {
                "Cache-Control": "no-store",
                "Pragma": "no-cache",
                "Expires": "0",
            }
        )
        self.headers.update(control_page_memory_handoff_headers(None))

    def adopt_after_terminal(self, callback: Callable[[], None]) -> None:
        """Run one additional callback after EOF or terminal write failure."""

        if self._after_terminal_called:
            callback()
            return
        previous = self._after_terminal
        if previous is None:
            self._after_terminal = callback
            return

        def combined() -> None:
            try:
                previous()
            finally:
                callback()

        self._after_terminal = combined

    def _enter_memory_guard(self) -> None:
        if self._memory_guard_disabled or self._memory_guard is not None:
            return
        guard = memory_exposure_guard(
            expected_position=self._memory_expected_position,
            required=self._memory_expected_position is not None,
            index_dir=Path(MEMORY_ROOT) / "memory_index",
        )
        guard.__enter__()
        self._memory_guard = guard

    def _exit_memory_guard(self, exc: BaseException | None = None) -> None:
        guard = self._memory_guard
        self._memory_guard = None
        if guard is not None:
            guard.__exit__(
                type(exc) if exc is not None else None,
                exc,
                exc.__traceback__ if exc is not None else None,
            )

    def _run_before_write(self) -> None:
        if self._before_write_called:
            return
        self._before_write_called = True
        callback = self._before_write
        self._before_write = None
        if callback is not None:
            callback()

    def _run_after_write(self) -> None:
        """Run success-only work after EOF; retained for focused tests."""

        if self._after_write_success_called:
            return
        self._after_write_success_called = True
        callback = self._after_write_success
        self._after_write_success = None
        self._after_write_failure = None
        try:
            if callback is not None:
                callback()
        except Exception as exc:
            print(
                "[FAST CONTROL] post_write_callback_failed "
                f"errorType={type(exc).__name__}",
                flush=True,
            )
        finally:
            self._run_after_terminal()

    def _run_after_write_failure(self, error_code: str) -> None:
        if self._after_write_failure_called:
            return
        self._after_write_failure_called = True
        callback = self._after_write_failure
        self._after_write_failure = None
        self._after_write_success = None
        try:
            if callback is not None:
                callback(error_code)
        except Exception as exc:
            print(
                "[FAST CONTROL] post_write_failure_callback_failed "
                f"errorType={type(exc).__name__}",
                flush=True,
            )
        finally:
            self._run_after_terminal()

    def _run_after_terminal(self) -> None:
        if self._after_terminal_called:
            return
        self._after_terminal_called = True
        callback = self._after_terminal
        self._after_terminal = None
        if callback is None:
            return
        try:
            callback()
        except Exception as exc:
            print(
                "[FAST CONTROL] terminal_callback_failed "
                f"errorType={type(exc).__name__}",
                flush=True,
            )

    async def prepare(self, request: web.BaseRequest) -> Any:
        try:
            self._enter_memory_guard()
            self._run_before_write()
            return await super().prepare(request)
        except MemoryDeletionJournalIntegrityError:
            self._exit_memory_guard()
            if self.prepared:
                self._run_after_terminal()
                raise
            self._replace_with_integrity_failure()
            try:
                return await super().prepare(request)
            except BaseException:
                self._run_after_terminal()
                raise
        except BaseException as exc:
            self._exit_memory_guard(exc)
            if self._before_write_called:
                self._run_after_write_failure(
                    "conversation_ingress_delivery_failed"
                )
            else:
                self._run_after_terminal()
            raise

    async def write_eof(self, data: bytes = b"") -> None:
        try:
            self._enter_memory_guard()
            await super().write_eof(data)
        except BaseException as exc:
            self._exit_memory_guard(exc)
            self._run_after_write_failure(
                "conversation_ingress_delivery_disconnected"
            )
            raise
        else:
            self._exit_memory_guard()
            self._run_after_write()


def memory_guarded_json_response(
    payload: dict[str, Any],
    *,
    expected_position: MemoryExposurePosition | None,
    status: int = 200,
    after_write: Callable[[], None] | None = None,
    before_write: Callable[[], None] | None = None,
    after_write_failure: Callable[[str], None] | None = None,
    after_terminal: Callable[[], None] | None = None,
) -> MemoryGuardedJsonResponse:
    return MemoryGuardedJsonResponse(
        payload,
        expected_position=expected_position,
        status=status,
        after_write=after_write,
        before_write=before_write,
        after_write_failure=after_write_failure,
        after_terminal=after_terminal,
    )


def clean_text(value: Any) -> str:
    return re.sub(r"\s+", " ", str(value or "").strip())


_INVALID_LOCAL_VOICE_VALIDATION_BINDING = object()
_LOCAL_VOICE_VALIDATION_LEASE_KEY = "_validationAttemptLease"


def local_voice_no_store_response(
    payload: dict[str, Any],
    *,
    status: int,
    after_terminal: Callable[[], None] | None = None,
) -> web.Response:
    if after_terminal is not None:
        return memory_guarded_json_response(
            payload,
            expected_position=None,
            status=status,
            after_terminal=after_terminal,
        )
    response = json_response(payload, status=status)
    response.headers["Cache-Control"] = "no-store"
    return response


def local_voice_fixed_failure(
    error_code: str,
    *,
    status: int,
    after_terminal: Callable[[], None] | None = None,
) -> web.Response:
    """Return a content-free failure without mutating admission state."""

    return local_voice_no_store_response(
        {
            "ok": False,
            "admitted": False,
            "reason": error_code,
            "error": error_code,
        },
        status=status,
        after_terminal=after_terminal,
    )


def _durable_local_voice_reservation_revocation(
    requests: tuple[LocalVoiceReservationRevocationRequest, ...],
) -> LocalVoiceDurableReservationRevocation:
    """Revoke an exact content-free reservation set in one journal write."""

    if not requests or not FAST_CONTROL_CONTINUITY_OWNER.enabled:
        raise LocalVoiceAdmissionTransactionError(
            "local_voice_reservation_revocation_failed"
        )
    raw_requests: list[dict[str, Any]] = []
    expected_bindings: list[tuple[str, str, str, str]] = []
    for request in requests:
        request_id = json.dumps(
            [request.bridge_instance_id, request.turn_id],
            ensure_ascii=False,
            separators=(",", ":"),
        )
        entry_id = conversation_ingress_entry_id(
            surface=FAST_CONTROL_INGRESS_SURFACE,
            scope=FAST_CONTROL_SESSION_KEY,
            source_delivery_id=request_id,
        )
        raw_requests.append(
            {
                "request_id": request_id,
                "text_hash": request.forward_text_digest,
                "turn_id": request.ingress_turn_id,
                "reservation_ref": request.reservation_ref,
            }
        )
        expected_bindings.append(
            (
                entry_id,
                request.ingress_turn_id,
                request.forward_text_digest,
                request.reservation_ref,
            )
        )
    try:
        receipt = dict(
            FAST_CONTROL_CONTINUITY_OWNER.revoke_reserved_ingress_batch(
                raw_requests
            )
        )
        raw_bindings = receipt.get("bindings")
        if not isinstance(raw_bindings, list):
            raise ValueError("bindings")
        bindings = tuple(
            sorted(
                (
                    str(binding["entryId"]),
                    str(binding["turnId"]),
                    str(binding["textHash"]),
                    str(binding["reservationRef"]),
                )
                for binding in raw_bindings
                if isinstance(binding, dict)
                and set(binding)
                == {"entryId", "turnId", "textHash", "reservationRef"}
            )
        )
        generation = receipt.get("journalGeneration")
        revoked_count = receipt.get("revokedCount")
        if (
            receipt.get("schema")
            != CONVERSATION_INGRESS_RESERVATION_REVOCATION_RECEIPT_SCHEMA
            or receipt.get("durable") is not True
            or isinstance(revoked_count, bool)
            or revoked_count != len(requests)
            or isinstance(generation, bool)
            or not isinstance(generation, int)
            or generation <= 0
            or bindings != tuple(sorted(expected_bindings))
        ):
            raise ValueError("receipt")
    except LocalVoiceAdmissionTransactionError:
        raise
    except Exception as exc:
        raise LocalVoiceAdmissionTransactionError(
            "local_voice_reservation_revocation_failed"
        ) from exc
    return LocalVoiceDurableReservationRevocation(
        schema=str(receipt["schema"]),
        durable=True,
        bindings=bindings,
        revoked_count=revoked_count,
        journal_generation=generation,
    )


def _durable_local_voice_scope_revocation() -> dict[str, Any]:
    """Revoke restart-orphaned local-voice reservations content-free."""

    if not FAST_CONTROL_CONTINUITY_OWNER.enabled:
        raise LocalVoiceAdmissionTransactionError(
            "local_voice_reservation_revocation_failed"
        )
    try:
        receipt = dict(
            FAST_CONTROL_CONTINUITY_OWNER.revoke_reserved_local_voice_ingress()
        )
        raw_bindings = receipt.get("bindings")
        revoked_count = receipt.get("revokedCount")
        generation = receipt.get("journalGeneration")
        if (
            receipt.get("schema")
            != CONVERSATION_INGRESS_RESERVATION_REVOCATION_RECEIPT_SCHEMA
            or receipt.get("durable") is not True
            or type(revoked_count) is not int
            or revoked_count < 0
            or not isinstance(raw_bindings, list)
            or len(raw_bindings) != revoked_count
            or type(generation) is not int
            or generation < 0
        ):
            raise ValueError("receipt")
        normalized = []
        for binding in raw_bindings:
            if not isinstance(binding, dict) or set(binding) != {
                "entryId",
                "turnId",
                "textHash",
                "reservationRef",
            }:
                raise ValueError("binding")
            values = tuple(clean_text(binding[key]) for key in (
                "entryId",
                "turnId",
                "textHash",
                "reservationRef",
            ))
            if (
                re.fullmatch(r"ingress-[0-9a-f]{64}", values[0]) is None
                or re.fullmatch(r"lva-[0-9a-f]{64}", values[1]) is None
                or re.fullmatch(r"[0-9a-f]{64}", values[2]) is None
                or re.fullmatch(r"[0-9a-f]{64}", values[3]) is None
            ):
                raise ValueError("binding")
            normalized.append(values)
        if len(set(normalized)) != len(normalized):
            raise ValueError("binding")
        return receipt
    except LocalVoiceAdmissionTransactionError:
        raise
    except Exception as exc:
        raise LocalVoiceAdmissionTransactionError(
            "local_voice_reservation_revocation_failed"
        ) from exc


def _reset_local_voice_admission(
    reason: str,
    *,
    revoke_scope: bool = False,
) -> dict[str, Any]:
    status = LOCAL_VOICE_ADMISSION.reset(
        reason,
        durable_revocation=_durable_local_voice_reservation_revocation,
    )
    if revoke_scope and FAST_CONTROL_CONTINUITY_OWNER.enabled:
        try:
            _durable_local_voice_scope_revocation()
        except Exception:
            LOCAL_VOICE_ADMISSION.require_durable_revocation()
            raise
    return status


def _private_local_voice_capture_fence_digest() -> str:
    digest = clean_text(
        LOCAL_BRIDGE_STATUS.get("voiceCaptureFenceDigest")
    ).lower()
    return digest if re.fullmatch(r"[0-9a-f]{64}", digest) else ""


def _acquire_local_voice_capture_claim_lease():
    try:
        return acquire_voice_capture_consent_claim_lease(
            root=CONTINUITY_ARTIFACTS_ROOT,
        )
    except VoiceCaptureConsentClaimLeaseBusy:
        raise LocalVoiceAdmissionTransactionError(
            "local_voice_capture_claim_inflight"
        ) from None
    except VoiceCaptureConsentClaimLeaseUnavailable:
        raise LocalVoiceAdmissionTransactionError(
            "local_voice_capture_claim_lease_unavailable"
        ) from None


def _revoke_local_voice_for_capture_fence() -> tuple[str, int]:
    """Close every live capability when host capture consent is not current."""

    try:
        with _acquire_local_voice_capture_claim_lease():
            _reset_local_voice_admission(
                "voice_capture_consent_not_current",
                revoke_scope=True,
            )
    except LocalVoiceAdmissionTransactionError as exc:
        if exc.code in {
            "local_voice_capture_claim_inflight",
            "local_voice_capture_claim_lease_unavailable",
        }:
            if exc.code.endswith("_unavailable"):
                try:
                    LOCAL_VOICE_ADMISSION.require_durable_revocation()
                except Exception:
                    pass
            return exc.code, 503
        try:
            LOCAL_VOICE_ADMISSION.require_durable_revocation()
        except Exception:
            pass
        return "local_voice_reservation_revocation_failed", 503
    except Exception:
        try:
            LOCAL_VOICE_ADMISSION.require_durable_revocation()
        except Exception:
            pass
        return "local_voice_reservation_revocation_failed", 503
    return "voice_capture_consent_not_current", 409


def _acquire_local_voice_validation_lease(
    binding: Any,
) -> tuple[VoiceValidationAttemptLeaseSet | None, web.Response | None]:
    normalized = normalize_validation_binding(binding)
    if not normalized:
        return None, None
    try:
        return acquire_attempt_lease(normalized), None
    except VoiceValidationAttemptLeaseBusy:
        return None, local_voice_fixed_failure(
            "validation_attempt_inflight",
            status=409,
        )
    except VoiceValidationAttemptLeaseUnavailable:
        return None, local_voice_fixed_failure(
            "validation_attempt_lease_unavailable",
            status=503,
        )


def _release_local_voice_validation_lease(
    payload: dict[str, Any],
) -> None:
    lease = payload.pop(_LOCAL_VOICE_VALIDATION_LEASE_KEY, None)
    if isinstance(lease, VoiceValidationAttemptLeaseSet):
        lease.release()


def local_voice_validation_binding(payload: dict[str, Any]) -> Any:
    if "validation" in payload and "validationBinding" in payload:
        if payload.get("validation") != payload.get("validationBinding"):
            return _INVALID_LOCAL_VOICE_VALIDATION_BINDING
    if "validation" in payload:
        return payload.get("validation")
    return payload.get("validationBinding")


def local_voice_validation_binding_is_current(binding: dict[str, Any]) -> bool:
    normalized = normalize_validation_binding(binding)
    if normalized is None:
        return False
    return validation_attempt_binding_is_current(
        normalized,
        surface="local",
        reject_unbound_when_active=True,
    )


def _emit_local_voice_turn_accepted(
    binding: Any,
    turn_id: Any,
) -> dict[str, Any] | None:
    normalized = normalize_validation_binding(binding)
    accepted_turn_id = clean_text(turn_id)
    if not normalized or not accepted_turn_id:
        return None
    material = json.dumps(
        {
            "attemptId": normalized["attemptId"],
            "sessionId": normalized["sessionId"],
            "stepId": normalized["stepId"],
            "turnId": accepted_turn_id,
        },
        ensure_ascii=True,
        sort_keys=True,
        separators=(",", ":"),
    )
    try:
        return emit_voice_validation_event(
            "local",
            "turn_accepted",
            session_id=normalized["sessionId"],
            step_id=normalized["stepId"],
            attempt_id=normalized["attemptId"],
            eventId=(
                "local-accepted-"
                + hashlib.sha256(material.encode("utf-8")).hexdigest()[:32]
            ),
            turnId=accepted_turn_id,
        )
    except Exception as exc:
        # The ingress claim is already durable. Observability must not leave
        # its one-shot capability live or turn the committed claim into 503.
        print(
            "[FAST CONTROL] local_voice_validation_event_write_failed "
            f"errorType={type(exc).__name__}",
            flush=True,
        )
        return None


def consume_local_voice_admission(
    payload: dict[str, Any],
    *,
    text: str,
    source: str,
) -> tuple[
    str,
    LocalVoiceDurableIngressClaim | None,
    web.Response | None,
]:
    if clean_text(source).lower() != "local_bridge":
        return text, None, None
    binding = local_voice_validation_binding(payload)
    validation_lease, lease_rejection = (
        _acquire_local_voice_validation_lease(binding)
    )
    if lease_rejection is not None:
        return "", None, lease_rejection
    lease_holder = [validation_lease]
    try:
        return _consume_local_voice_admission_with_lease(
            payload,
            text=text,
            binding=binding,
            lease_holder=lease_holder,
        )
    finally:
        unreleased = lease_holder[0]
        if unreleased is not None:
            lease_holder[0] = None
            unreleased.release()


def _consume_local_voice_admission_with_lease(
    payload: dict[str, Any],
    *,
    text: str,
    binding: Any,
    lease_holder: list[VoiceValidationAttemptLeaseSet | None],
) -> tuple[
    str,
    LocalVoiceDurableIngressClaim | None,
    web.Response | None,
]:
    validation_lease = lease_holder[0]

    def take_validation_terminal_callback() -> Callable[[], None] | None:
        nonlocal validation_lease
        if validation_lease is None:
            return None
        callback = validation_lease.release
        validation_lease = None
        lease_holder[0] = None
        return callback

    owner = FAST_CONTROL_CONTINUITY_OWNER
    unsafe_test_bypass = bool(
        not owner.enabled
        and getattr(
            owner,
            "_test_only_allow_unsafe_ingress",
            False,
        )
        is True
    )
    transaction = None
    if owner.enabled and not local_voice_capture_fence_is_current(
        payload.get("bridgeInstanceId")
    ):
        error_code, status = _revoke_local_voice_for_capture_fence()
        return (
            "",
            None,
            local_voice_fixed_failure(
                error_code,
                status=status,
                after_terminal=take_validation_terminal_callback(),
            ),
        )
    capture_fence_digest = (
        _private_local_voice_capture_fence_digest()
        if owner.enabled
        else ""
    )
    if owner.enabled and not capture_fence_digest:
        error_code, status = _revoke_local_voice_for_capture_fence()
        return (
            "",
            None,
            local_voice_fixed_failure(
                error_code,
                status=status,
                after_terminal=take_validation_terminal_callback(),
            ),
        )
    if unsafe_test_bypass:
        result = LOCAL_VOICE_ADMISSION.consume(
            payload.get("admissionToken"),
            payload.get("bridgeInstanceId"),
            payload.get("turnId"),
            text,
            admission_mode=payload.get("admissionMode"),
            validation_binding=binding,
            validation_is_current=(
                local_voice_validation_binding_is_current
            ),
            durable_revocation=(
                _durable_local_voice_reservation_revocation
            ),
        )
    else:
        bridge_instance_id = clean_text(
            payload.get("bridgeInstanceId")
        )
        bridge_turn_id = clean_text(payload.get("turnId"))
        request_id = json.dumps(
            [bridge_instance_id, bridge_turn_id],
            ensure_ascii=False,
            separators=(",", ":"),
        )
        try:
            def durable_claim(
                claim_request: LocalVoiceIngressClaimRequest,
            ) -> LocalVoiceDurableIngressClaim:
                with _acquire_local_voice_capture_claim_lease():
                    if (
                        not local_voice_capture_fence_is_current(
                            claim_request.bridge_instance_id
                        )
                        or not hmac.compare_digest(
                            claim_request.capture_fence_digest,
                            _private_local_voice_capture_fence_digest(),
                        )
                    ):
                        raise LocalVoiceAdmissionTransactionError(
                            "local_voice_capture_fence_not_current"
                        )
                    receipt = dict(
                        owner.claim_reserved_ingress(
                            request_id=request_id,
                            accepted_text=claim_request.forward_text,
                            turn_id=claim_request.ingress_turn_id,
                            reservation_ref=claim_request.reservation_ref,
                        )
                    )
                    expected_entry_id = conversation_ingress_entry_id(
                        surface=FAST_CONTROL_INGRESS_SURFACE,
                        scope=FAST_CONTROL_SESSION_KEY,
                        source_delivery_id=request_id,
                    )
                    if (
                        claim_request.bridge_instance_id
                        != bridge_instance_id
                        or claim_request.turn_id != bridge_turn_id
                        or receipt.get("entryId") != expected_entry_id
                        or receipt.get("turnId")
                        != claim_request.ingress_turn_id
                        or receipt.get("textHash")
                        != claim_request.forward_text_digest
                        or type(receipt.get("shouldProcess")) is not bool
                        or type(receipt.get("journalGeneration")) is not int
                    ):
                        raise LocalVoiceAdmissionTransactionError(
                            "local_voice_ingress_claim_binding_mismatch"
                        )
                    claim = LocalVoiceDurableIngressClaim(
                        schema=str(receipt.get("schema") or ""),
                        durable=receipt.get("durable") is True,
                        bridge_instance_id=(
                            claim_request.bridge_instance_id
                        ),
                        local_turn_id=claim_request.turn_id,
                        forward_text_digest=(
                            claim_request.forward_text_digest
                        ),
                        entry_id=str(receipt.get("entryId") or ""),
                        ingress_turn_id=str(
                            receipt.get("turnId") or ""
                        ),
                        phase=str(receipt.get("phase") or ""),
                        disposition=str(
                            receipt.get("disposition") or ""
                        ),
                        should_process=receipt["shouldProcess"],
                        text_hash=str(receipt.get("textHash") or ""),
                        journal_generation=receipt["journalGeneration"],
                        reservation_ref=claim_request.reservation_ref,
                        reservation_verified=True,
                        _validation_lease_held=(
                            validation_lease is not None
                        ),
                    )
                    LOCAL_VOICE_ADMISSION._durable_ingress_claim_receipt(
                        claim,
                        claim_request,
                    )
                    _emit_local_voice_turn_accepted(binding, bridge_turn_id)
                    return claim

            transaction = (
                LOCAL_VOICE_ADMISSION.consume_with_durable_claim(
                    payload.get("admissionToken"),
                    payload.get("bridgeInstanceId"),
                    payload.get("turnId"),
                    text,
                    durable_claim=durable_claim,
                    durable_revocation=(
                        _durable_local_voice_reservation_revocation
                    ),
                    admission_mode=payload.get("admissionMode"),
                    validation_binding=binding,
                    validation_is_current=(
                        local_voice_validation_binding_is_current
                    ),
                    durable_recovery_is_current=(
                        lambda: local_voice_recovery_context_is_current(
                            bridge_instance_id
                        )
                    ),
                    capture_fence_digest=capture_fence_digest,
                )
            )
        except ConversationIngressBindingMismatch:
            return (
                "",
                None,
                local_voice_fixed_failure(
                    "local_voice_turn_binding_mismatch",
                    status=409,
                    after_terminal=take_validation_terminal_callback(),
                ),
            )
        except ConversationIngressRecoveryError as exc:
            invalid_request_codes = {
                "conversation_ingress_source_delivery_id_invalid",
                "conversation_ingress_scope_invalid",
                "conversation_ingress_surface_invalid",
                "conversation_ingress_accepted_text_invalid",
            }
            return (
                "",
                None,
                _ingress_error_response(
                    (
                        "conversation_ingress_request_invalid"
                        if exc.code in invalid_request_codes
                        else "conversation_ingress_recovery_unavailable"
                    ),
                    status=(
                        400 if exc.code in invalid_request_codes else 503
                    ),
                    after_terminal=take_validation_terminal_callback(),
                ),
            )
        except LocalVoiceAdmissionTransactionError as exc:
            if exc.code == "local_voice_capture_fence_not_current":
                error_code, status = _revoke_local_voice_for_capture_fence()
                return (
                    "",
                    None,
                    local_voice_fixed_failure(
                        error_code,
                        status=status,
                        after_terminal=(
                            take_validation_terminal_callback()
                        ),
                    ),
                )
            if exc.code in {
                "local_voice_capture_claim_inflight",
                "local_voice_capture_claim_lease_unavailable",
            }:
                return (
                    "",
                    None,
                    local_voice_fixed_failure(
                        exc.code,
                        status=503,
                        after_terminal=(
                            take_validation_terminal_callback()
                        ),
                    ),
                )
            return (
                "",
                None,
                _ingress_error_response(
                    (
                        "local_voice_reservation_revocation_failed"
                        if "revocation" in exc.code
                        else "conversation_ingress_recovery_unavailable"
                    ),
                    status=503,
                    after_terminal=take_validation_terminal_callback(),
                ),
            )
        except (OSError, RuntimeError):
            # Durable journal writers may surface their underlying I/O error.
            # Keep the public failure content-free; the manager transaction
            # has not consumed the capability when the claim raises.
            return (
                "",
                None,
                _ingress_error_response(
                    "conversation_ingress_recovery_unavailable",
                    status=503,
                    after_terminal=take_validation_terminal_callback(),
                ),
            )
        result = transaction.admission
    if transaction is None and result.get("admitted") is True:
        _emit_local_voice_turn_accepted(
            binding,
            payload.get("turnId"),
        )
    durable_duplicate = bool(
        transaction is not None
        and transaction.ingress_claim is not None
        and transaction.ingress_claim.should_process is False
        and result.get("suppressed") is True
        and result.get("reason") == "admission_ingress_duplicate"
    )
    if result.get("admitted") is not True and not durable_duplicate:
        return (
            "",
            None,
            local_voice_no_store_response(
                result,
                status=409,
                after_terminal=take_validation_terminal_callback(),
            ),
        )
    admitted_text = clean_text(result.get("forwardText"))
    if not admitted_text:
        failed = LOCAL_VOICE_ADMISSION.reject("admission_forward_text_invalid")
        return (
            "",
            None,
            local_voice_no_store_response(
                failed,
                status=409,
                after_terminal=take_validation_terminal_callback(),
            ),
        )
    if validation_lease is not None:
        payload[_LOCAL_VOICE_VALIDATION_LEASE_KEY] = validation_lease
        validation_lease = None
        lease_holder[0] = None
    return (
        admitted_text,
        transaction.ingress_claim if transaction is not None else None,
        None,
    )


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
    FAST_MEMORY_DELETION_POSITION.set(None)
    FAST_MEMORY_EXPOSURE_POSITION.set(None)
    reset_memory_deletion_outbound_position()
    reset_memory_exposure_position()
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
        "deletionBoundary": memory_deletion_boundary_not_required(),
        "contentFree": True,
    }
    FAST_MEMORY_CONTEXT_RECEIPT.set(receipt)
    FAST_HISTORY_MEMORY_RECEIPT_REF.set(
        memory_receipt_ref_from_receipt(None)
    )
    return dict(receipt)


def current_fast_memory_context_receipt() -> dict[str, Any]:
    receipt = FAST_MEMORY_CONTEXT_RECEIPT.get()
    return dict(receipt) if isinstance(receipt, dict) else reset_fast_memory_context_receipt()


def current_fast_response_memory_receipt_ref() -> dict[str, Any]:
    current_ref = memory_receipt_ref_from_receipt(
        current_fast_memory_context_receipt()
    )
    history_ref = FAST_HISTORY_MEMORY_RECEIPT_REF.get()
    merged = merge_memory_receipt_refs(history_ref, current_ref)
    return merged or memory_receipt_ref_from_receipt(None)


def _ingress_error_response(
    error_code: str,
    *,
    status: int,
    after_terminal: Callable[[], None] | None = None,
) -> MemoryGuardedJsonResponse:
    return memory_guarded_json_response(
        {"ok": False, "error": error_code},
        expected_position=None,
        status=status,
        after_terminal=after_terminal,
    )


def _prepare_fast_control_ingress(
    payload: dict[str, Any],
    *,
    accepted_text: str,
    source: str,
    preclaimed: LocalVoiceDurableIngressClaim | None = None,
) -> tuple[
    dict[str, Any] | None,
    tuple[dict[str, Any], MemoryExposurePosition | None] | None,
    web.StreamResponse | None,
]:
    """Claim once and project only safe completed retries."""

    owner = FAST_CONTROL_CONTINUITY_OWNER
    if not owner.enabled:
        if getattr(
            owner,
            "_test_only_allow_unsafe_ingress",
            False,
        ) is True:
            return None, None, None
        return (
            None,
            None,
            _ingress_error_response(
                "conversation_ingress_recovery_unavailable",
                status=503,
            ),
        )
    normalized_source = clean_text(source).lower() or "control_page"
    request_id = clean_text(
        payload.get("requestId") or payload.get("turnId")
    )
    if normalized_source == "local_bridge":
        bridge_instance_id = clean_text(
            payload.get("bridgeInstanceId")
        )
        bridge_turn_id = clean_text(payload.get("turnId"))
        if not bridge_instance_id or not bridge_turn_id:
            return (
                None,
                None,
                _ingress_error_response(
                    "conversation_ingress_request_id_required",
                    status=400,
                ),
            )
        request_id = json.dumps(
            [bridge_instance_id, bridge_turn_id],
            ensure_ascii=False,
            separators=(",", ":"),
        )
    if not request_id:
        return (
            None,
            None,
            _ingress_error_response(
                "conversation_ingress_request_id_required",
                status=400,
            ),
        )
    if preclaimed is not None:
        if (
            normalized_source != "local_bridge"
            or preclaimed.bridge_instance_id != bridge_instance_id
            or preclaimed.local_turn_id != bridge_turn_id
            or preclaimed.forward_text_digest
            != final_text_sha256(accepted_text)
            or preclaimed.durable is not True
            or preclaimed.entry_id
            != conversation_ingress_entry_id(
                surface=FAST_CONTROL_INGRESS_SURFACE,
                scope=FAST_CONTROL_SESSION_KEY,
                source_delivery_id=request_id,
            )
            or preclaimed.text_hash != final_text_sha256(accepted_text)
        ):
            return (
                None,
                None,
                _ingress_error_response(
                    "conversation_ingress_recovery_unavailable",
                    status=503,
                ),
            )
        claim = {
            "entryId": preclaimed.entry_id,
            "turnId": preclaimed.ingress_turn_id,
            "phase": preclaimed.phase,
            "shouldProcess": preclaimed.should_process,
        }
    else:
        try:
            claim = owner.claim_ingress(
                request_id=request_id,
                accepted_text=accepted_text,
            )
            claim = dict(claim)
        except ConversationIngressBindingMismatch:
            return (
                None,
                None,
                _ingress_error_response(
                    "conversation_ingress_binding_mismatch",
                    status=409,
                ),
            )
        except ConversationIngressRecoveryError as exc:
            invalid_request_codes = {
                "conversation_ingress_source_delivery_id_invalid",
                "conversation_ingress_scope_invalid",
                "conversation_ingress_surface_invalid",
                "conversation_ingress_accepted_text_invalid",
            }
            return (
                None,
                None,
                _ingress_error_response(
                    (
                        "conversation_ingress_request_invalid"
                        if exc.code in invalid_request_codes
                        else "conversation_ingress_recovery_unavailable"
                    ),
                    status=(
                        400 if exc.code in invalid_request_codes else 503
                    ),
                ),
            )
    claim["_effectId"] = request_id
    if claim.get("shouldProcess") is True:
        return claim, None, None
    if claim.get("phase") != "completed":
        return (
            claim,
            None,
            _ingress_error_response(
                FAST_CONTROL_INGRESS_PENDING_ERROR,
                status=409,
            ),
        )
    if normalized_source != "control_page":
        return (
            claim,
            None,
            _ingress_error_response(
                FAST_CONTROL_INGRESS_REDELIVERY_SUPPRESSED_ERROR,
                status=409,
            ),
        )

    try:
        record = owner.ingress_record(
            claim["entryId"],
            replay=True,
        )
        if record is None:
            raise ConversationIngressRecoveryError(
                "conversation_ingress_replay_not_terminal"
            )
        receipt_ref = sanitize_memory_receipt_ref(
            record.get("memoryReceiptRef")
        )
        if (
            receipt_ref is None
            or receipt_ref.get("state") not in {"bound", "not_used"}
        ):
            raise ConversationIngressRecoveryError(
                "conversation_ingress_replay_unattributed"
            )
        memory_index_dir = Path(MEMORY_ROOT) / "memory_index"
        with memory_deletion_journal_guard(
            memory_index_dir,
            require_stable=True,
        ) as deletion_position:
            capture_memory_deletion_outbound_position(
                deletion_position
            )
            exposure = memory_exposure_position_from_receipt(
                receipt_ref,
                deletion_position=deletion_position,
                required=receipt_ref["state"] == "bound",
            )
        with memory_exposure_guard(
            expected_position=exposure,
            required=receipt_ref["state"] == "bound",
            index_dir=memory_index_dir,
        ):
            pass
        return claim, (record, exposure), None
    except (
        ConversationIngressRecoveryError,
        MemoryDeletionJournalIntegrityError,
        OSError,
        RuntimeError,
        TypeError,
        ValueError,
    ):
        return (
            claim,
            None,
            _ingress_error_response(
                FAST_CONTROL_INGRESS_REPLAY_ERROR,
                status=409,
            ),
        )


def _pending_fast_control_continuity_result() -> dict[str, Any]:
    return {
        "schema": "fast_control.delivery-continuity.v1",
        "enabled": True,
        "durable": False,
        "generation": 0,
        "persistedSessionCount": 0,
        "pendingDelivery": True,
        "error": "",
    }


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
    if clean_text(role).lower() == "assistant":
        message["memoryReceiptRef"] = (
            unattributed_memory_receipt_ref()
            if memory_receipt is None
            else memory_receipt_ref_from_receipt(memory_receipt)
        )
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
    *,
    memory_receipt: Any = None,
    ingress_entry_id: str = "",
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
            memory_receipt=memory_receipt,
            ingress_entry_id=ingress_entry_id,
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
    *,
    memory_receipt: Any = None,
) -> dict[str, Any]:
    owner = FAST_CONTROL_CONTINUITY_OWNER
    if not owner.enabled:
        return _fast_control_continuity_result(
            enabled=False,
        )
    try:
        raw_status = owner.record_assistant_followup(
            assistant_text,
            memory_receipt=memory_receipt,
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
            memory_receipt=not_used_memory_receipt_ref(),
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
    *,
    memory_receipt: Any = None,
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
            memory_receipt=memory_receipt,
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
    *,
    memory_receipt: Any = None,
    ingress_entry_id: str = "",
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
            memory_receipt=memory_receipt,
            ingress_entry_id=ingress_entry_id,
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
            memory_receipt=not_used_memory_receipt_ref(),
        )
        continuity = commit_fast_control_followup(
            FAST_ACTION_RECOVERY_NOTICE,
            memory_receipt=not_used_memory_receipt_ref(),
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
    messages: list[dict[str, Any]] = []
    for raw_message in CHAT_MESSAGES[-max(1, limit + 1) :]:
        role = clean_text(raw_message.get("role"))
        content = clean_text(raw_message.get("text"))
        if role not in {"user", "assistant"} or not content:
            continue
        message: dict[str, Any] = {
            "role": role,
            "content": content,
        }
        if role == "assistant":
            if "memoryReceiptRef" in raw_message:
                message["memoryReceiptRef"] = raw_message.get(
                    "memoryReceiptRef"
                )
            elif "memoryReceipt" in raw_message:
                message["memoryReceipt"] = raw_message.get(
                    "memoryReceipt"
                )
        messages.append(message)
    if (
        messages
        and messages[-1]["role"] == "user"
        and messages[-1]["content"] == clean_text(text)
    ):
        messages.pop()
    recovered_loader = getattr(
        FAST_CONTROL_CONTINUITY_OWNER,
        "recovered_ingress_context_messages",
        None,
    )
    if callable(recovered_loader):
        current_text = clean_text(text)
        seen_messages = {
            (
                clean_text(message.get("role")),
                clean_text(message.get("content")),
            )
            for message in messages
        }
        seen_recovery_entries: set[str] = set()
        for recovered in recovered_loader(limit=min(4, limit)):
            if not isinstance(recovered, dict):
                continue
            entry_id = clean_text(
                recovered.get("_ingressRecoveryEntryId")
            )
            role = clean_text(recovered.get("role")).lower()
            content = clean_text(recovered.get("content"))
            key = (role, content)
            if (
                role != "user"
                or not entry_id
                or not content
                or entry_id in seen_recovery_entries
                or content == current_text
                or key in seen_messages
            ):
                continue
            seen_recovery_entries.add(entry_id)
            seen_messages.add(key)
            messages.append(
                {
                    "role": "user",
                    "content": content,
                    "_ingressRecoveryEntryId": entry_id,
                    "_ingressRecoveryUnanswered": True,
                }
            )
    merged = CROSS_SURFACE_CONTINUITY_BRIDGE.merge_for_fast(
        messages,
        current_user_text=text,
    )
    reset_memory_exposure_position()
    outcome = filter_conversation_history_for_memory_exposure(
        merged[-limit:],
        memory_index_dir=Path(MEMORY_ROOT) / "memory_index",
    )
    exposure_position = capture_combined_memory_exposure(
        outcome.memory_exposure_position,
    )
    FAST_MEMORY_EXPOSURE_POSITION.set(exposure_position)
    FAST_HISTORY_MEMORY_RECEIPT_REF.set(
        dict(outcome.memory_receipt_ref)
    )
    return [
        {
            "role": clean_text(message.get("role")),
            "content": clean_text(message.get("content")),
        }
        for message in outcome.messages[-limit:]
        if clean_text(message.get("role"))
        in {"user", "assistant"}
        and clean_text(message.get("content"))
    ]


def local_bridge_status_snapshot(*, now: float | None = None) -> dict[str, Any]:
    snapshot = dict(LOCAL_BRIDGE_STATUS)
    snapshot.pop("bridgeInstanceId", None)
    snapshot.pop("voiceCaptureFenceDigest", None)
    snapshot["voiceAdmission"] = LOCAL_VOICE_ADMISSION.public_status()
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
    raw_mic_control_error = clean_text(snapshot.get("micControlError"))
    snapshot["micControlError"] = (
        public_error_code(
            raw_mic_control_error,
            fallback="mic_control_failed",
        )
        if raw_mic_control_error
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


def local_voice_recovery_context_is_current(
    bridge_instance_id: Any,
    *,
    now: float | None = None,
) -> bool:
    """Authorize restart recovery only while capture consent is current."""

    return local_voice_capture_fence_is_current(
        bridge_instance_id,
        now=now,
    )


def local_voice_capture_fence_digest_if_current(
    bridge_instance_id: Any,
    *,
    now: float | None = None,
) -> str:
    """Return the private, current consent generation digest or ``""``."""

    if not _configured_control_token(LOCAL_BRIDGE_STATUS_AUTH_TOKEN):
        return ""
    if LOCAL_BRIDGE_MIC_CONTROL_REQUEST.get("enabled") is False:
        return ""
    checked_at = _finite_number(time.time() if now is None else now)
    bridge_id = clean_text(bridge_instance_id)
    if (
        checked_at is None
        or checked_at <= 0
        or not bridge_id
        or bridge_id
        != clean_text(LOCAL_BRIDGE_STATUS.get("bridgeInstanceId"))
    ):
        return ""
    snapshot = local_bridge_status_snapshot(now=checked_at)
    mic = snapshot.get("mic")
    watchdog = LOCAL_BRIDGE_STATUS.get("voiceCaptureWatchdog")
    fence_digest = LOCAL_BRIDGE_STATUS.get("voiceCaptureFenceDigest")
    if not isinstance(watchdog, dict):
        return ""
    watchdog_checked_at = _finite_number(watchdog.get("checkedAt"))
    heartbeat_at = _finite_number(LOCAL_BRIDGE_STATUS.get("heartbeatAt"))
    watchdog_age = (
        None
        if watchdog_checked_at is None
        else checked_at - watchdog_checked_at
    )
    if not (
        watchdog.get("schema") == WATCHDOG_STATUS_SCHEMA
        and watchdog.get("state") == "authorized"
        and not clean_text(watchdog.get("reason"))
        and watchdog.get("captureStopped") is False
        and watchdog.get("stoppedAt") is None
        and watchdog.get("contentFree") is True
        and isinstance(fence_digest, str)
        and re.fullmatch(r"[0-9a-f]{64}", fence_digest) is not None
        and watchdog_age is not None
        and 0.0 <= watchdog_age <= HOST_LEASE_STALE_SEC
        and heartbeat_at is not None
        and watchdog_checked_at is not None
        and watchdog_checked_at <= heartbeat_at
        and snapshot.get("enabled") is True
        and snapshot.get("ready") is True
        and snapshot.get("stale") is False
        and snapshot.get("micEnabled") is True
        and snapshot.get("micControlDesiredEnabled") is True
        and int(snapshot.get("micControlPendingRevision") or 0) == 0
        and snapshot.get("micControlState") in {"idle", "applied"}
        and not clean_text(snapshot.get("micControlError"))
        and snapshot.get("micCaptureStopped") is False
        and snapshot.get("restartStarted") is False
        and snapshot.get("shutdownStarted") is False
        and isinstance(mic, dict)
        and mic.get("enabled") is True
        and mic.get("captureReady") is True
        and mic.get("captureActive") is True
        and mic.get("captureStopped") is False
        and RESTART_REQUEST.get("requested") is not True
        and SHUTDOWN_REQUEST.get("requested") is not True
    ):
        return ""
    try:
        if not voice_capture_consent_fence_matches(
            VOICE_CAPTURE_HOST_LEASE_PATH,
            VOICE_CAPTURE_CONSENT_STATE_PATH,
            expected_digest=fence_digest,
            now=lambda: checked_at,
        ):
            return ""
        # Do not bind a grant to a digest swapped concurrently with the
        # authenticated state/lease check above.
        return (
            fence_digest
            if hmac.compare_digest(
                fence_digest,
                clean_text(
                    LOCAL_BRIDGE_STATUS.get("voiceCaptureFenceDigest")
                ),
            )
            else ""
        )
    except Exception:
        return ""


def local_voice_capture_fence_is_current(
    bridge_instance_id: Any,
    *,
    now: float | None = None,
) -> bool:
    """Match the live authenticated Bridge to the durable host consent."""

    return bool(
        local_voice_capture_fence_digest_if_current(
            bridge_instance_id,
            now=now,
        )
    )


def _configured_control_token(value: Any) -> str:
    token = str(value or "").strip()
    if len(token) < LOCAL_BRIDGE_AUTH_TOKEN_MIN_LENGTH:
        return ""
    return token


def _request_has_control_token(
    request: web.Request,
    *,
    header: str,
    expected: Any,
    unauthorized_error: str = "local_bridge_status_unauthorized",
) -> tuple[bool, str, int]:
    configured = _configured_control_token(expected)
    if not configured:
        return False, "local_bridge_auth_unconfigured", 503
    provided = str(request.headers.get(header) or "")
    if not hmac.compare_digest(provided, configured):
        return False, unauthorized_error, 403
    return True, "", 200


def _strict_nonnegative_int(value: Any) -> int | None:
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        return None
    return value


def _strict_positive_int(value: Any) -> int | None:
    parsed = _strict_nonnegative_int(value)
    return parsed if parsed is not None and parsed > 0 else None


def _finite_number(value: Any) -> float | None:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    parsed = float(value)
    return parsed if math.isfinite(parsed) else None


def _bounded_status_text(value: Any, *, limit: int = 512) -> str | None:
    if not isinstance(value, str) or len(value) > limit:
        return None
    return value


def _mic_action_id(value: Any, *, allow_empty: bool = False) -> str | None:
    if value == "" and allow_empty:
        return ""
    if not isinstance(value, str) or re.fullmatch(r"[a-f0-9]{32}", value) is None:
        return None
    return value


_LOCAL_BRIDGE_STATUS_PASSTHROUGH_FIELDS = frozenset(
    {
        "activePlaybackOwner",
        "botApiBase",
        "device",
        "errorCount",
        "errorCounters",
        "hostUiAction",
        "hostVision",
        "inputStreamingTts",
        "lastError",
        "lastErrorAt",
        "lastErrorCode",
        "lastErrorType",
        "lastLatency",
        "lastTtsPlayback",
        "minecraftCommandError",
        "minecraftCommandResult",
        "minecraftCommandRevision",
        "minecraftCommandState",
        "mode",
        "outputDevice",
        "outputDevices",
        "outputErrorCode",
        "outputFormat",
        "outputReady",
        "playCount",
        "playbackCancelRequested",
        "segmentCount",
        "speaking",
        "sttUrl",
        "streamingTts",
        "transcriptCount",
        "ttsUrl",
        "ttsWarmup",
        "voiceAdmission",
    }
)


def _normalize_local_bridge_status(
    payload: Any,
    *,
    now: float,
) -> dict[str, Any] | None:
    """Validate the authoritative heartbeat and return an allowlisted copy."""
    if not isinstance(payload, dict):
        return None
    required = {
        "schema",
        "statusSeq",
        "heartbeatAt",
        "pid",
        "bridgeInstanceId",
        "startedAt",
        "enabled",
        "ready",
        "micEnabled",
        "micControlRevision",
        "micControlActionId",
        "micControlPendingRevision",
        "micControlPendingActionId",
        "micControlState",
        "micControlDesiredEnabled",
        "micControlError",
        "micCaptureStopped",
        "mic",
    }
    if not required.issubset(payload):
        return None
    if payload.get("schema") != "local_io_bridge.status.v1":
        return None
    status_seq = _strict_positive_int(payload.get("statusSeq"))
    heartbeat_at = _finite_number(payload.get("heartbeatAt"))
    started_at = _finite_number(payload.get("startedAt"))
    pid = _strict_positive_int(payload.get("pid"))
    bridge_instance_id = _mic_action_id(payload.get("bridgeInstanceId"))
    if (
        status_seq is None
        or heartbeat_at is None
        or started_at is None
        or started_at <= 0
        or pid is None
        or bridge_instance_id is None
        or abs(now - heartbeat_at) > LOCAL_BRIDGE_HEARTBEAT_MAX_SKEW_SEC
        or started_at > heartbeat_at + LOCAL_BRIDGE_HEARTBEAT_MAX_SKEW_SEC
    ):
        return None
    boolean_fields = (
        "enabled",
        "ready",
        "micEnabled",
        "micControlDesiredEnabled",
        "micCaptureStopped",
    )
    if any(type(payload.get(field)) is not bool for field in boolean_fields):
        return None
    lifecycle_fields = ("restartStarted", "shutdownStarted")
    if any(
        field in payload and type(payload.get(field)) is not bool
        for field in lifecycle_fields
    ):
        return None
    if payload.get("enabled") is not True:
        return None
    revision = _strict_nonnegative_int(payload.get("micControlRevision"))
    pending_revision = _strict_nonnegative_int(
        payload.get("micControlPendingRevision")
    )
    if revision is None or pending_revision is None:
        return None
    action_id = _mic_action_id(
        payload.get("micControlActionId"),
        allow_empty=revision == 0,
    )
    pending_action_id = _mic_action_id(
        payload.get("micControlPendingActionId"),
        allow_empty=pending_revision == 0,
    )
    if (
        action_id is None
        or pending_action_id is None
        or (revision == 0 and action_id != "")
        or (pending_revision == 0 and pending_action_id != "")
        or (pending_revision > 0 and pending_revision <= revision)
    ):
        return None
    control_state = _bounded_status_text(
        payload.get("micControlState"),
        limit=16,
    )
    if control_state not in {"idle", "applying", "applied", "failed"}:
        return None
    control_error = _bounded_status_text(
        payload.get("micControlError"),
        limit=80,
    )
    if control_error is None or (
        control_error
        and re.fullmatch(r"[a-z0-9_.-]{1,80}", control_error) is None
    ):
        return None
    mic = payload.get("mic")
    if not isinstance(mic, dict):
        return None
    mic_required = {
        "enabled",
        "captureReady",
        "captureActive",
        "captureStopped",
    }
    if not mic_required.issubset(mic) or any(
        type(mic.get(field)) is not bool for field in mic_required
    ):
        return None
    if (
        mic.get("enabled") is not payload.get("micEnabled")
        or mic.get("captureStopped") is not payload.get("micCaptureStopped")
        or (
            payload.get("micEnabled") is False
            and (
                mic.get("captureReady") is not False
                or mic.get("captureActive") is not False
                or mic.get("captureStopped") is not True
            )
        )
        or (
            mic.get("captureStopped") is True
            and (
                mic.get("captureReady") is not False
                or mic.get("captureActive") is not False
            )
        )
    ):
        return None
    watchdog = payload.get("voiceCaptureWatchdog")
    fence_digest = payload.get("voiceCaptureFenceDigest")
    normalized_watchdog: dict[str, Any] | None = None
    if watchdog is not None or fence_digest is not None:
        watchdog_keys = {
            "schema",
            "state",
            "reason",
            "checkedAt",
            "captureStopped",
            "stoppedAt",
            "contentFree",
        }
        if not isinstance(watchdog, dict) or set(watchdog) != watchdog_keys:
            return None
        watchdog_state = _bounded_status_text(
            watchdog.get("state"),
            limit=32,
        )
        watchdog_reason = _bounded_status_text(
            watchdog.get("reason"),
            limit=120,
        )
        watchdog_checked_at = _finite_number(watchdog.get("checkedAt"))
        stopped_at = watchdog.get("stoppedAt")
        normalized_stopped_at = (
            None if stopped_at is None else _finite_number(stopped_at)
        )
        valid_fence_digest = bool(
            isinstance(fence_digest, str)
            and re.fullmatch(r"[0-9a-f]{64}", fence_digest)
        )
        if (
            watchdog.get("schema") != WATCHDOG_STATUS_SCHEMA
            or watchdog_state not in {"authorized", "blocked", "stop_failed"}
            or watchdog_reason is None
            or watchdog_checked_at is None
            or watchdog_checked_at <= 0
            or watchdog_checked_at > heartbeat_at
            or abs(now - watchdog_checked_at)
            > LOCAL_BRIDGE_HEARTBEAT_MAX_SKEW_SEC
            or type(watchdog.get("captureStopped")) is not bool
            or watchdog.get("captureStopped")
            is not payload.get("micCaptureStopped")
            or (
                stopped_at is not None
                and (
                    normalized_stopped_at is None
                    or normalized_stopped_at <= 0
                )
            )
            or watchdog.get("contentFree") is not True
            or (
                watchdog_state == "authorized"
                and (
                    watchdog_reason
                    or not valid_fence_digest
                    or stopped_at is not None
                )
            )
            or (
                watchdog_state != "authorized"
                and (not watchdog_reason or fence_digest != "")
            )
        ):
            return None
        normalized_watchdog = {
            "schema": WATCHDOG_STATUS_SCHEMA,
            "state": watchdog_state,
            "reason": watchdog_reason,
            "checkedAt": watchdog_checked_at,
            "captureStopped": watchdog["captureStopped"],
            "stoppedAt": normalized_stopped_at,
            "contentFree": True,
        }

    # Preserve known operational evidence only. Unknown fields never become
    # authoritative merely because a reporter included them.
    normalized = {
        key: value
        for key, value in payload.items()
        if key in _LOCAL_BRIDGE_STATUS_PASSTHROUGH_FIELDS
    }
    mic_allowlist = {
        "captureActive",
        "captureReady",
        "captureStopped",
        "continueThreshold",
        "discardedPendingSegmentCount",
        "enabled",
        "envNoiseFilterEnabled",
        "inputBlockCount",
        "lastInputAgeSec",
        "lastInputLevel",
        "lastInputStatus",
        "lastRejectedReason",
        "lastSegmentFilter",
        "maxInputLevel",
        "minVoicedMs",
        "rejectedSegmentCount",
        "startThreshold",
        "suppressedSegmentCount",
        "ttsInputSuppressRemainingMs",
        "ttsInputSuppressed",
        "vadFilterEnabled",
        "waveformFilterEnabled",
    }
    normalized_mic = {
        key: value for key, value in mic.items() if key in mic_allowlist
    }
    normalized.update(
        {
            "schema": "local_io_bridge.status.v1",
            "statusSeq": status_seq,
            "heartbeatAt": heartbeat_at,
            "pid": pid,
            "bridgeInstanceId": bridge_instance_id,
            "startedAt": started_at,
            "enabled": True,
            "ready": payload["ready"],
            "micEnabled": payload["micEnabled"],
            "micControlRevision": revision,
            "micControlActionId": action_id,
            "micControlPendingRevision": pending_revision,
            "micControlPendingActionId": pending_action_id,
            "micControlState": control_state,
            "micControlDesiredEnabled": payload[
                "micControlDesiredEnabled"
            ],
            "micControlError": control_error,
            "micCaptureStopped": payload["micCaptureStopped"],
            "restartStarted": bool(payload.get("restartStarted", False)),
            "shutdownStarted": bool(payload.get("shutdownStarted", False)),
            "mic": normalized_mic,
        }
    )
    if normalized_watchdog is not None:
        normalized["voiceCaptureWatchdog"] = normalized_watchdog
        normalized["voiceCaptureFenceDigest"] = str(fence_digest)
    return normalized


def _local_bridge_status_order_is_valid(
    candidate: dict[str, Any],
) -> bool:
    current_instance = clean_text(LOCAL_BRIDGE_STATUS.get("bridgeInstanceId"))
    if not current_instance:
        return True
    candidate_instance = clean_text(candidate.get("bridgeInstanceId"))
    if candidate_instance == current_instance:
        return bool(
            candidate.get("pid") == LOCAL_BRIDGE_STATUS.get("pid")
            and candidate.get("startedAt") == LOCAL_BRIDGE_STATUS.get("startedAt")
            and int(candidate.get("statusSeq") or 0)
            > int(LOCAL_BRIDGE_STATUS.get("statusSeq") or 0)
        )
    # A new process generation must be provably newer. Staleness alone is not
    # sufficient because a delayed heartbeat from a retired process can arrive.
    current_started_at = _finite_number(LOCAL_BRIDGE_STATUS.get("startedAt"))
    candidate_started_at = _finite_number(candidate.get("startedAt"))
    return bool(
        current_started_at is not None
        and candidate_started_at is not None
        and candidate_started_at > current_started_at
    )


def local_bridge_mic_enable_fence_snapshot() -> dict[str, Any]:
    return dict(LOCAL_BRIDGE_MIC_ENABLE_FENCE)


def _mic_enable_fence_is_well_formed(value: Any) -> bool:
    if not isinstance(value, dict) or set(value) != {
        "schema",
        "epoch",
        "disableGeneration",
    }:
        return False
    generation = _strict_nonnegative_int(value.get("disableGeneration"))
    return bool(
        value.get("schema") == LOCAL_BRIDGE_MIC_ENABLE_FENCE_SCHEMA
        and _mic_action_id(value.get("epoch")) is not None
        and generation is not None
    )


def _mic_enable_fence_matches(value: Any) -> bool:
    return bool(
        _mic_enable_fence_is_well_formed(value)
        and value == LOCAL_BRIDGE_MIC_ENABLE_FENCE
    )


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
    memory_exposure = current_memory_exposure_position()
    if memory_exposure is not None:
        request["memoryBoundary"] = (
            memory_exposure_position_to_dict(memory_exposure)
        )
    LOCAL_BRIDGE_SPEAK_QUEUE.append(request)
    del LOCAL_BRIDGE_SPEAK_QUEUE[:-8]
    return request


def drain_local_bridge_speak_requests() -> list[dict[str, Any]]:
    requests: list[dict[str, Any]] = []
    for raw_request in LOCAL_BRIDGE_SPEAK_QUEUE:
        request = dict(raw_request)
        raw_boundary = request.get("memoryBoundary")
        if raw_boundary is None:
            requests.append(request)
            continue
        try:
            position = memory_exposure_position_from_dict(
                raw_boundary
            )
            with memory_exposure_guard(
                expected_position=position,
                required=True,
                index_dir=Path(MEMORY_ROOT) / "memory_index",
            ):
                requests.append(request)
        except MemoryDeletionJournalIntegrityError:
            continue
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


def _local_bridge_instance_digest(value: Any) -> str:
    bridge_instance_id = clean_text(value)
    if not bridge_instance_id:
        return ""
    return hashlib.sha256(bridge_instance_id.encode("utf-8")).hexdigest()


def request_local_bridge_mic_control(
    enabled: bool,
    *,
    source: str = "control_page",
    purpose: str = "",
    enable_fence: dict[str, Any] | None = None,
) -> dict[str, Any]:
    if enabled:
        if clean_text(purpose) != "voice_capture_consent":
            raise PermissionError("mic_enable_not_authorized")
        if not _mic_enable_fence_is_well_formed(enable_fence):
            raise PermissionError("mic_enable_not_authorized")
        if not _mic_enable_fence_matches(enable_fence):
            raise PermissionError("mic_enable_fence_stale")
    else:
        LOCAL_BRIDGE_MIC_ENABLE_FENCE["disableGeneration"] = (
            int(LOCAL_BRIDGE_MIC_ENABLE_FENCE["disableGeneration"]) + 1
        )
    revocation_error: LocalVoiceAdmissionTransactionError | None = None
    if not enabled:
        try:
            _reset_local_voice_admission(
                "mic_disabled",
                revoke_scope=True,
            )
        except LocalVoiceAdmissionTransactionError as exc:
            # Publish physical OFF even while the admission fence stays shut.
            revocation_error = exc
    current_revision = int(LOCAL_BRIDGE_MIC_CONTROL_REQUEST.get("revision") or 0)
    observed_revision = _strict_nonnegative_int(
        LOCAL_BRIDGE_STATUS.get("micControlRevision")
    )
    revision = max(current_revision, observed_revision or 0) + 1
    bridge_instance_digest = _local_bridge_instance_digest(
        LOCAL_BRIDGE_STATUS.get("bridgeInstanceId")
    )
    LOCAL_BRIDGE_MIC_CONTROL_REQUEST.update(
        {
            "revision": revision,
            "actionId": secrets.token_hex(16),
            "enabled": bool(enabled),
            "requestedAt": time.time(),
            "source": clean_text(source) or "control_page",
            "purpose": clean_text(purpose) if enabled else "",
            "bridgeInstanceDigest": bridge_instance_digest,
        }
    )
    if revocation_error is not None:
        raise revocation_error
    return dict(LOCAL_BRIDGE_MIC_CONTROL_REQUEST)


def _local_bridge_mic_control_observation(
    request: dict[str, Any],
) -> tuple[str, dict[str, Any], dict[str, Any] | None]:
    """Return pending/failed/applied with an exact, content-free receipt."""
    snapshot = local_bridge_status_snapshot()
    revision = request.get("revision")
    action_id = request.get("actionId")
    observed_revision = snapshot.get("micControlRevision")
    if (
        not isinstance(revision, int)
        or isinstance(revision, bool)
        or not isinstance(observed_revision, int)
        or isinstance(observed_revision, bool)
    ):
        return "failed", snapshot, None
    desired_enabled = request.get("enabled")
    target_digest = clean_text(request.get("bridgeInstanceDigest"))
    current_digest = _local_bridge_instance_digest(
        LOCAL_BRIDGE_STATUS.get("bridgeInstanceId")
    )
    if (
        revision <= 0
        or _mic_action_id(action_id) is None
        or not isinstance(desired_enabled, bool)
        or len(target_digest) != 64
        or any(char not in "0123456789abcdef" for char in target_digest)
        or current_digest != target_digest
    ):
        return "failed", snapshot, None
    current_request = LOCAL_BRIDGE_MIC_CONTROL_REQUEST
    if any(
        (
            current_request.get("revision") != revision,
            current_request.get("actionId") != action_id,
            current_request.get("enabled") is not desired_enabled,
            clean_text(current_request.get("bridgeInstanceDigest"))
            != target_digest,
        )
    ):
        return "failed", snapshot, None
    if observed_revision > revision:
        return "failed", snapshot, None
    if observed_revision < revision:
        return "pending", snapshot, None
    if snapshot.get("micControlActionId") != action_id:
        return "failed", snapshot, None

    control_state = clean_text(snapshot.get("micControlState")).lower()
    control_error = clean_text(snapshot.get("micControlError"))
    if control_state == "failed":
        return "failed", snapshot, None
    if control_state != "applied":
        return "pending", snapshot, None
    if (
        snapshot.get("micControlDesiredEnabled") is not desired_enabled
        or control_error
        or snapshot.get("stale") is not False
        or snapshot.get("enabled") is not True
    ):
        return "failed", snapshot, None

    mic = snapshot.get("mic")
    if not isinstance(mic, dict):
        return "failed", snapshot, None
    if desired_enabled:
        capture_stopped = False
        verified = (
            snapshot.get("micEnabled") is True
            and snapshot.get("ready") is True
            and snapshot.get("micCaptureStopped") is False
            and mic.get("enabled") is True
            and mic.get("captureReady") is True
            and mic.get("captureStopped") is False
        )
    else:
        capture_stopped = True
        verified = (
            snapshot.get("micEnabled") is False
            and snapshot.get("micCaptureStopped") is True
            and mic.get("enabled") is False
            and mic.get("captureReady") is False
            and mic.get("captureActive") is False
            and mic.get("captureStopped") is True
        )
    if not verified:
        return "failed", snapshot, None

    return (
        "applied",
        snapshot,
        {
            "schema": LOCAL_BRIDGE_MIC_CONTROL_ACK_SCHEMA,
            "actionId": action_id,
            "requestRevision": revision,
            "observedRevision": observed_revision,
            "enabled": desired_enabled,
            "bridgeInstanceDigest": target_digest,
            "state": "applied",
            "captureStopped": capture_stopped,
        },
    )


async def wait_for_local_bridge_mic_control(
    request: dict[str, Any],
    *,
    timeout_sec: float = 4.0,
) -> dict[str, Any]:
    if not clean_text(request.get("bridgeInstanceDigest")):
        return {
            "applied": False,
            "request": dict(request),
            "localBridge": local_bridge_status_snapshot(),
            "error": "mic_control_bridge_unavailable",
        }
    deadline = time.monotonic() + max(0.1, float(timeout_sec))
    while time.monotonic() < deadline:
        state, snapshot, ack = _local_bridge_mic_control_observation(request)
        if state == "applied" and ack is not None:
            return {
                "applied": True,
                "request": dict(request),
                "ack": ack,
                "localBridge": snapshot,
            }
        if state == "failed":
            current = LOCAL_BRIDGE_MIC_CONTROL_REQUEST
            superseded = any(
                (
                    current.get("revision") != request.get("revision"),
                    current.get("actionId") != request.get("actionId"),
                    current.get("enabled") is not request.get("enabled"),
                    snapshot.get("micControlRevision", 0)
                    > request.get("revision", 0),
                )
            )
            return {
                "applied": False,
                "request": dict(request),
                "localBridge": snapshot,
                "error": (
                    "mic_control_superseded"
                    if superseded
                    else clean_text(snapshot.get("micControlError"))
                    or "mic_control_ack_invalid"
                ),
            }
        await asyncio.sleep(0.05)
    return {
        "applied": False,
        "request": dict(request),
        "localBridge": local_bridge_status_snapshot(),
        "error": "mic_control_ack_timeout",
    }


async def execute_local_bridge_mic_control(enabled: bool, *, source: str) -> str:
    if enabled:
        return (
            "마이크 입력은 음성 검증 화면에서 청취 동의를 확인한 뒤에만 "
            "켤 수 있어."
        )
    try:
        request = request_local_bridge_mic_control(False, source=source)
    except LocalVoiceAdmissionTransactionError:
        return (
            "마이크 중지 요청은 보냈지만 음성 예약 철회를 확인하지 못했어. "
            "새 음성 입력은 차단된 상태야."
        )
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
VOICE_VALIDATION_LLM_SYSTEM_PROMPT = "\n\n".join(
    (
        FAST_MAIN_LLM_SYSTEM_PROMPT,
        (
            "This is an isolated voice transport validation turn. Answer only "
            "the current user message directly. Do not use or claim memory, "
            "conversation history, tools, runtime state, vision, search, or "
            "external facts. Do not initiate actions. Keep the answer concise."
        ),
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
    dispatch_action=MINECRAFT_WORLD_HTTP_RUNTIME.dispatch_action,
    get_action_status=MINECRAFT_WORLD_HTTP_RUNTIME.action_status,
    cancel_action=MINECRAFT_WORLD_HTTP_RUNTIME.cancel_action,
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
    memory_exposure_position: MemoryExposurePosition | None = None,
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
    exposure_position = (
        memory_exposure_position
        if memory_exposure_position is not None
        else current_memory_exposure_position()
    )
    async with ClientSession(timeout=timeout) as session:
        async with memory_exposure_request(
            session.post,
            LLM_SERVER_URL,
            json=payload,
            expected_position=exposure_position,
            memory_boundary_required=(exposure_position is not None),
        ) as response:
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
    with memory_exposure_guard(
        expected_position=plan.memory_exposure_position,
        required=(plan.memory_exposure_position is not None),
    ):
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
            with memory_exposure_guard(
                expected_position=plan.memory_exposure_position,
                required=(plan.memory_exposure_position is not None),
            ):
                executed_query, results = await default_search_provider(
                    retry_query
                )
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
            memory_exposure_position=(
                plan.memory_exposure_position
            ),
        )
    except MemoryDeletionJournalIntegrityError:
        raise
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
            memory_exposure_position=(
                plan.memory_exposure_position
            ),
        )
    except MemoryDeletionJournalIntegrityError:
        raise
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
                memory_receipt=not_used_memory_receipt_ref(),
            )
            return None
        begin_fast_action_recovery(task)
        return task, runner
    return None


def launch_background_action(
    task: FastActionTask,
    runner: Callable[[str, str], Awaitable[str]],
) -> asyncio.Task[Any]:
    exposure_position = current_memory_exposure_position()
    memory_receipt_ref = (
        memory_receipt_ref_from_exposure(exposure_position)
        if exposure_position is not None
        else not_used_memory_receipt_ref()
    )

    async def execute() -> None:
        terminal_reply_recorded = False
        try:
            raw_reply = await runner(task.user_text, task.source)
            final_reply = enforce_action_reply_contract(clean_text(raw_reply))
            if not final_reply:
                final_reply = "작업은 완료됐지만 전달할 결과가 비어 있어."
            with memory_exposure_guard(
                expected_position=exposure_position,
                required=(exposure_position is not None),
            ):
                completed = ACTION_COORDINATOR.complete(
                    task.task_id,
                    final_reply,
                    memory_receipt=memory_receipt_ref,
                )
                terminal_reply_recorded = True
                append_chat_message(
                    "assistant",
                    "Evelyn",
                    completed.final_reply,
                    source="fast_control_action_followup",
                    task_id=completed.task_id,
                    task_status=completed.status,
                    memory_receipt=memory_receipt_ref,
                )
                commit_fast_control_action_followup(
                    completed.task_id,
                    completed.final_reply,
                    memory_receipt=memory_receipt_ref,
                )
                queue_local_bridge_speech(
                    completed.final_reply,
                    source="fast_control_action_followup",
                )
        except Exception as exc:
            print(
                "[FAST CONTROL] background_action_failed "
                f"task={task.task_id} errorType={type(exc).__name__}",
                flush=True,
            )
            if terminal_reply_recorded:
                return
            custom_failure = isinstance(
                exc,
                FastActionExecutionError,
            )
            if custom_failure:
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
            failure_receipt = (
                memory_receipt_ref
                if custom_failure
                else not_used_memory_receipt_ref()
            )

            def persist_failure() -> None:
                nonlocal terminal_reply_recorded
                failed = ACTION_COORDINATOR.fail(
                    task.task_id,
                    error,
                    reply=failed_reply,
                    memory_receipt=failure_receipt,
                )
                terminal_reply_recorded = True
                append_chat_message(
                    "assistant",
                    "Evelyn",
                    failed.final_reply,
                    source="fast_control_action_followup",
                    task_id=failed.task_id,
                    task_status=failed.status,
                    memory_receipt=failure_receipt,
                )
                commit_fast_control_action_followup(
                    failed.task_id,
                    failed.final_reply,
                    memory_receipt=failure_receipt,
                )
                queue_local_bridge_speech(
                    failed.final_reply,
                    source="fast_control_action_followup",
                )

            if custom_failure:
                try:
                    with memory_exposure_guard(
                        expected_position=exposure_position,
                        required=exposure_position is not None,
                    ):
                        persist_failure()
                except MemoryDeletionJournalIntegrityError:
                    if terminal_reply_recorded:
                        return
                    error = "background_action_failed"
                    failed_reply = public_failure_message(error)
                    failure_receipt = not_used_memory_receipt_ref()
                    persist_failure()
            else:
                persist_failure()

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
    revocation_error: LocalVoiceAdmissionTransactionError | None = None
    try:
        _reset_local_voice_admission(
            "shutdown_requested",
            revoke_scope=True,
        )
    except LocalVoiceAdmissionTransactionError as exc:
        revocation_error = exc
    SHUTDOWN_REQUEST.update(
        {
            "requested": True,
            "requestedAt": time.time(),
            "source": clean_text(source) or "control_page",
            "reason": clean_text(reason) or "operator_request",
        }
    )
    if revocation_error is not None:
        raise revocation_error
    return {
        "ok": True,
        "message": "Local Evelyn shutdown requested. Windows local I/O bridge will run the stop script.",
        "shutdown": dict(SHUTDOWN_REQUEST),
    }


def request_local_restart(*, source: str, reason: str = "") -> dict[str, Any]:
    _reset_local_voice_admission(
        "restart_requested",
        revoke_scope=True,
    )
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


_MEMORY_CHANGED_REPLY_REDACTION = (
    "메모리가 변경되어 이전 결과를 더 이상 표시하지 않아."
)


def _memory_index_path(
    memory_index_dir: Path | None,
) -> Path:
    return (
        Path(memory_index_dir)
        if memory_index_dir is not None
        else Path(MEMORY_ROOT) / "memory_index"
    )


def _memory_safe_public_rows(
    rows: list[dict[str, Any]],
    *,
    memory_index_dir: Path,
) -> list[dict[str, Any]]:
    """Filter memory-derived text before a public state projection."""

    try:
        outcome = filter_conversation_history_for_memory_exposure(
            rows,
            memory_index_dir=Path(memory_index_dir),
        )
        capture_combined_memory_exposure(
            current_memory_exposure_position(),
            outcome.memory_exposure_position,
        )
        return [dict(row) for row in outcome.messages]
    except MemoryDeletionJournalIntegrityError:
        # A broken or unavailable index must not make a bound reply public.
        # Preserve user rows and assistant rows explicitly proven independent
        # of memory so the surrounding status endpoint remains useful.
        safe_rows: list[dict[str, Any]] = []
        for raw_row in rows:
            if not isinstance(raw_row, dict):
                continue
            row = dict(raw_row)
            role = clean_text(row.get("role")).lower()
            if role != "assistant":
                row.pop("memoryReceipt", None)
                row.pop("memoryReceiptRef", None)
                safe_rows.append(row)
                continue
            if "memoryReceiptRef" in row:
                receipt = memory_receipt_ref_from_receipt(
                    row.get("memoryReceiptRef")
                )
            elif "memoryReceipt" in row:
                receipt = memory_receipt_ref_from_receipt(
                    row.get("memoryReceipt")
                )
            else:
                continue
            if receipt.get("state") != "not_used":
                continue
            row.pop("memoryReceipt", None)
            row.pop("memoryReceiptRef", None)
            safe_rows.append(row)
        return safe_rows


def default_chat_messages(
    *,
    memory_index_dir: Path | None = None,
) -> list[dict[str, Any]]:
    if CHAT_MESSAGES:
        safe_messages = _memory_safe_public_rows(
            CHAT_MESSAGES,
            memory_index_dir=_memory_index_path(
                memory_index_dir
            ),
        )
        public_keys = (
            "role",
            "author",
            "text",
            "at",
            "source",
            "taskId",
            "taskStatus",
        )
        return [
            {
                key: message[key]
                for key in public_keys
                if key in message
            }
            for message in safe_messages
            if isinstance(message, dict)
        ]
    return [
        {
            "role": "assistant",
            "author": "Control",
            "text": "Docker core is ready. Windows local I/O bridge can attach microphone and speaker output.",
            "at": time.time(),
        }
    ]


def _public_fast_action_snapshot(
    *,
    memory_index_dir: Path | None = None,
) -> dict[str, Any]:
    internal = ACTION_COORDINATOR.internal_snapshot()
    candidates: list[dict[str, Any]] = []

    for task in internal.get("tasks") or []:
        if (
            not isinstance(task, dict)
            or task.get("status") not in {"completed", "failed"}
            or not clean_text(task.get("finalReply"))
        ):
            continue
        row: dict[str, Any] = {
            "role": "assistant",
            "content": clean_text(task.get("finalReply")),
            "projectionId": f"task:{task.get('id')}",
        }
        if "_memoryReceiptRef" in task:
            row["memoryReceiptRef"] = task.get(
                "_memoryReceiptRef"
            )
        candidates.append(row)

    for event in internal.get("events") or []:
        if (
            not isinstance(event, dict)
            or event.get("type") not in {"completed", "failed"}
            or not clean_text(event.get("reply"))
        ):
            continue
        row = {
            "role": "assistant",
            "content": clean_text(event.get("reply")),
            "projectionId": f"event:{event.get('id')}",
        }
        if "_memoryReceiptRef" in event:
            row["memoryReceiptRef"] = event.get(
                "_memoryReceiptRef"
            )
        candidates.append(row)

    kept_projection_ids = {
        clean_text(row.get("projectionId"))
        for row in _memory_safe_public_rows(
            candidates,
            memory_index_dir=_memory_index_path(
                memory_index_dir
            ),
        )
        if clean_text(row.get("projectionId"))
    }

    tasks: list[dict[str, Any]] = []
    for raw_task in internal.get("tasks") or []:
        if not isinstance(raw_task, dict):
            continue
        task = {
            key: value
            for key, value in raw_task.items()
            if not key.startswith("_")
        }
        projection_id = f"task:{task.get('id')}"
        if (
            task.get("status") in {"completed", "failed"}
            and clean_text(task.get("finalReply"))
            and projection_id not in kept_projection_ids
        ):
            task["finalReply"] = (
                _MEMORY_CHANGED_REPLY_REDACTION
            )
            task["replyRedacted"] = True
        tasks.append(task)

    events: list[dict[str, Any]] = []
    for raw_event in internal.get("events") or []:
        if not isinstance(raw_event, dict):
            continue
        event = {
            key: value
            for key, value in raw_event.items()
            if not key.startswith("_")
        }
        projection_id = f"event:{event.get('id')}"
        if (
            event.get("type") in {"completed", "failed"}
            and clean_text(event.get("reply"))
            and projection_id not in kept_projection_ids
        ):
            event["reply"] = (
                _MEMORY_CHANGED_REPLY_REDACTION
            )
            event["replyRedacted"] = True
        events.append(event)

    return {
        "activeCount": int(internal.get("activeCount") or 0),
        "lastEventId": int(internal.get("lastEventId") or 0),
        "tasks": tasks,
        "events": events,
    }


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
    plan_exposure_position = (
        tool_plan.memory_exposure_position
        if tool_plan is not None
        else None
    )
    combined_prebuild_exposure = capture_combined_memory_exposure(
        plan_exposure_position,
        current_memory_exposure_position(),
    )
    if plan_exposure_position is not None:
        merged_history_ref = merge_memory_receipt_refs(
            FAST_HISTORY_MEMORY_RECEIPT_REF.get(),
            memory_receipt_ref_from_exposure(
                plan_exposure_position
            ),
        )
        FAST_HISTORY_MEMORY_RECEIPT_REF.set(
            merged_history_ref
            or memory_receipt_ref_from_receipt(None)
        )
    FAST_MEMORY_EXPOSURE_POSITION.set(
        combined_prebuild_exposure
    )
    final_user_text = build_fast_main_llm_user_text(text)
    with memory_exposure_guard(
        expected_position=combined_prebuild_exposure,
        required=(combined_prebuild_exposure is not None),
    ):
        llm_request = await build_fast_main_llm_request(
            base_system_prompt=FAST_MAIN_LLM_SYSTEM_PROMPT,
            recent_messages=recent_messages,
            user_text=text,
            final_user_text=final_user_text,
            source=source,
            tool_user_text=(
                tool_plan.query
                if tool_plan is not None
                else None
            ),
            local_bridge_status_provider=(
                local_bridge_status_snapshot
            ),
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
    FAST_MEMORY_DELETION_POSITION.set(
        getattr(llm_request, "memory_deletion_position", None)
    )
    FAST_MEMORY_EXPOSURE_POSITION.set(
        getattr(llm_request, "memory_exposure_position", None)
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


def build_isolated_voice_validation_llm_payload(text: str) -> dict[str, Any]:
    """Build a voice-validation request without context or provider access."""

    reset_fast_memory_context_receipt()
    payload: dict[str, Any] = {
        "model": MODEL_NAME,
        "messages": [
            {
                "role": "system",
                "content": VOICE_VALIDATION_LLM_SYSTEM_PROMPT,
            },
            {"role": "user", "content": clean_text(text)},
        ],
        "temperature": 0.2,
        "max_tokens": 700,
        "stream": True,
        "cache_prompt": True,
    }
    if MAIN_LLM_STOP_TOKENS:
        payload["stop"] = list(MAIN_LLM_STOP_TOKENS)
    return payload


async def iter_main_llm_deltas(
    text: str,
    *,
    source: str,
    tool_plan: FastToolPlan | None = None,
    isolated_validation: bool = False,
) -> AsyncIterator[str]:
    if isolated_validation:
        if tool_plan is not None:
            raise RuntimeError("validation_tool_plan_forbidden")
        payload = build_isolated_voice_validation_llm_payload(text)
        failure_reply = ""
    else:
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
    exposure_position = FAST_MEMORY_EXPOSURE_POSITION.get()
    async with ClientSession(timeout=timeout) as session:
        async with memory_exposure_request(
            session.post,
            LLM_SERVER_URL,
            json=payload,
            expected_position=exposure_position,
            memory_boundary_required=(
                exposure_position is not None
            ),
        ) as resp:
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
    isolated_validation: bool = False,
) -> str:
    if isolated_validation and tool_plan is not None:
        raise RuntimeError("validation_tool_plan_forbidden")
    stream = (
        iter_main_llm_deltas(
            text,
            source=source,
            isolated_validation=True,
        )
        if isolated_validation
        else (
            iter_main_llm_deltas(text, source=source)
            if tool_plan is None
            else iter_main_llm_deltas(
                text,
                source=source,
                tool_plan=tool_plan,
            )
        )
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
    isolated_validation: bool = False,
) -> tuple[str, int]:
    if isolated_validation and tool_plan is not None:
        raise RuntimeError("validation_tool_plan_forbidden")
    raw_parts: list[str] = []
    clean_seen_len = 0
    sentence_buffer = ""
    emitted_chunks: list[str] = []
    queued_count = 0
    stream = (
        iter_main_llm_deltas(
            text,
            source=source,
            isolated_validation=True,
        )
        if isolated_validation
        else (
            iter_main_llm_deltas(text, source=source)
            if tool_plan is None
            else iter_main_llm_deltas(
                text,
                source=source,
                tool_plan=tool_plan,
            )
        )
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
    plan = await plan_fast_tool_request(
        text,
        recent_messages=recent_chat_messages_for_planner(text),
        memory_index_dir=Path(MEMORY_ROOT) / "memory_index",
    )
    if plan is None:
        return None
    return bind_fast_tool_plan_memory_exposure(
        plan,
        current_memory_exposure_position(),
    )


def _service_by_id(health: dict[str, Any]) -> dict[str, dict[str, Any]]:
    return {str(service.get("id") or ""): dict(service) for service in health.get("services") or [] if isinstance(service, dict)}


def build_boot_progress(
    health: dict[str, Any],
    *,
    source_identity: dict[str, Any] | None = None,
) -> dict[str, Any]:
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
    identity = (
        dict(source_identity)
        if isinstance(source_identity, dict)
        else runtime_source_identity()
    )
    source_ready = identity.get("ready") is True
    steps.append(
        {
            "key": "source_identity",
            "label": "Runtime source",
            "done": source_ready,
            "status": (
                "done"
                if source_ready
                else str(identity.get("state") or "unverified")
            ),
            "detail": (
                ""
                if source_ready
                else str(
                    identity.get("reasonCode")
                    or "source_identity_unverified"
                )
            ),
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


def build_control_state(
    health: dict[str, Any],
    *,
    memory_index_dir: Path | None = None,
) -> dict[str, Any]:
    legacy = dict(health.get("legacyServices") or {})
    services_by_id = _service_by_id(health)
    source_identity = runtime_source_identity()
    source_ready = source_identity.get("ready") is True
    boot_progress = build_boot_progress(
        health,
        source_identity=source_identity,
    )
    control_ready = bool(
        (services_by_id.get("control_page") or {}).get("state") == "up"
        and source_ready
    )
    bot_ready = bool(legacy.get("botReady") and source_ready)
    chat_ready = bool(
        legacy.get("mainReady")
        and legacy.get("routerReady")
        and source_ready
    )
    voice_ready = bool(
        legacy.get("ttsReady")
        and legacy.get("sttReady")
        and source_ready
    )
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
        and source_ready
    )
    fully_healthy = bool(
        health.get(
            "fullyHealthy",
            str(health.get("overallState") or "up") == "up",
        )
        and source_ready
    )
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
            "messages": default_chat_messages(
                memory_index_dir=memory_index_dir
            ),
            "inputEnabled": chat_ready,
        },
        "actions": {
            **_public_fast_action_snapshot(
                memory_index_dir=memory_index_dir
            ),
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
                "sourceAligned": source_ready,
                "fullReady": fully_healthy,
                "optionalDegraded": bool(health.get("optionalDegraded", not fully_healthy and core_ready)),
                "voyagerHttpReady": bool(legacy.get("voyagerHttpReady")),
                "voyagerRuntimeReady": bool(legacy.get("voyagerRuntimeReady")),
            },
            "controlPlane": control_plane,
            "sourceIdentity": source_identity,
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
        source_identity = runtime_source_identity()
        source_ready = source_identity.get("ready") is True
        return {
            "kind": check.kind,
            "ok": source_ready,
            "reason": (
                "fast_control_self"
                if source_ready
                else str(
                    source_identity.get("reasonCode")
                    or "source_identity_unverified"
                )
            ),
            "target": target,
            "status": (
                200
                if check.kind == "http" and source_ready
                else 503
                if check.kind == "http"
                else None
            ),
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


async def _shielded_minecraft_world_lease_owner_shutdown(
    owner: Any,
) -> None:
    shutdown_task = asyncio.create_task(
        owner.shutdown(reason="shutdown")
    )
    cancellation_requested = False
    while not shutdown_task.done():
        try:
            await asyncio.shield(shutdown_task)
        except asyncio.CancelledError:
            cancellation_requested = True
            continue
    shutdown_task.result()
    if cancellation_requested:
        raise asyncio.CancelledError()


async def minecraft_world_lease_owner_context(
    _: web.Application,
):
    owner = MINECRAFT_WORLD_LEASE_OWNER
    try:
        owner.initialize()
        await owner.ensure_started()
        yield
    finally:
        await _shielded_minecraft_world_lease_owner_shutdown(owner)


async def health_handler(_: web.Request) -> web.StreamResponse:
    source_identity = runtime_source_identity()
    source_ready = source_identity.get("ready") is True
    return json_response(
        {
            "ok": source_ready,
            "role": "fast-control-bot-api",
            "port": PORT,
            "sourceIdentity": source_identity,
            "minecraftWorldLease": (
                MINECRAFT_WORLD_LEASE_OWNER.status()
            ),
        },
        status=200 if source_ready else 503,
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


def minecraft_world_lease_error_payload(
    error: BaseException | str,
) -> dict[str, Any]:
    payload: dict[str, Any] = {
        "ok": False,
        "error": minecraft_world_lease_delegation_error_code(
            error
        ),
    }
    try:
        lease_status = MINECRAFT_WORLD_LEASE_OWNER.status()
    except Exception:
        lease_status = None
    if isinstance(lease_status, dict):
        payload["leaseStatus"] = dict(lease_status)
    return payload


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
            minecraft_world_lease_error_payload(
                "minecraft_world_payload_invalid"
            ),
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
            minecraft_world_lease_error_payload(error),
            status=(
                503
                if error in {
                    "minecraft_service_unavailable",
                    "minecraft_world_action_lock_busy",
                    "minecraft_world_action_lock_unavailable",
                    "minecraft_world_lease_audit_unavailable",
                    "minecraft_world_lease_owner_claim_failed",
                    "minecraft_world_lease_owner_claim_write_failed",
                    "minecraft_world_lease_owner_lock_unavailable",
                    "minecraft_world_lease_status_write_failed",
                }
                else 409
            ),
        )
    return json_response(response)


async def state_handler(_: web.Request) -> web.StreamResponse:
    reset_memory_exposure_position()
    health = await cached_fast_runtime_health()
    state = build_control_state(health)
    return memory_guarded_json_response(
        state,
        expected_position=current_memory_exposure_position(),
    )


async def local_voice_admission_handler(
    request: web.Request,
) -> web.StreamResponse:
    try:
        payload = await request.json()
    except Exception:
        return local_voice_no_store_response(
            {"ok": False, "error": "invalid_json"},
            status=400,
        )
    if not isinstance(payload, dict):
        return local_voice_no_store_response(
            {"ok": False, "error": "invalid_json"},
            status=400,
        )
    if (
        "validation" in payload
        and "validationBinding" in payload
        and payload.get("validation") != payload.get("validationBinding")
    ):
        return local_voice_no_store_response(
            LOCAL_VOICE_ADMISSION.reject("validation_binding_invalid"),
            status=409,
        )
    text = clean_text(payload.get("text"))
    binding = local_voice_validation_binding(payload)
    normalized_binding = normalize_validation_binding(binding)
    validation_lease, lease_rejection = (
        _acquire_local_voice_validation_lease(binding)
    )
    if lease_rejection is not None:
        return lease_rejection

    def respond(
        response_payload: dict[str, Any],
        *,
        status: int,
    ) -> web.Response:
        nonlocal validation_lease
        after_terminal = (
            validation_lease.release
            if validation_lease is not None
            else None
        )
        validation_lease = None
        return local_voice_no_store_response(
            response_payload,
            status=status,
            after_terminal=after_terminal,
        )

    try:
        if normalized_binding:
            transcript_admission = validation_transcript_admission_status(
                "local",
                text,
                normalized_binding,
            )
            if transcript_admission.get("current") is not True:
                return respond(
                    LOCAL_VOICE_ADMISSION.reject(
                        "validation_attempt_stale"
                    ),
                    status=409,
                )
            if transcript_admission.get("matched") is not True:
                return respond(
                    LOCAL_VOICE_ADMISSION.reject(
                        clean_text(transcript_admission.get("reason"))
                        or "validation_transcript_mismatch"
                    ),
                    status=409,
                )

        owner = FAST_CONTROL_CONTINUITY_OWNER
        unsafe_test_bypass = bool(
            not owner.enabled
            and getattr(
                owner,
                "_test_only_allow_unsafe_ingress",
                False,
            )
            is True
        )
        if unsafe_test_bypass:
            result = LOCAL_VOICE_ADMISSION.issue(
                payload.get("bridgeInstanceId"),
                payload.get("turnId"),
                text,
                validation_binding=binding,
                validation_is_current=(
                    local_voice_validation_binding_is_current
                ),
                durable_revocation=(
                    _durable_local_voice_reservation_revocation
                ),
            )
        elif not owner.enabled:
            return respond(
                {
                    "ok": False,
                    "admitted": False,
                    "reason": "conversation_ingress_recovery_unavailable",
                    "error": "conversation_ingress_recovery_unavailable",
                },
                status=503,
            )
        else:
            bridge_instance_id = clean_text(
                payload.get("bridgeInstanceId")
            )
            bridge_turn_id = clean_text(payload.get("turnId"))
            if not local_voice_capture_fence_is_current(
                bridge_instance_id
            ):
                error_code, status = _revoke_local_voice_for_capture_fence()
                return respond(
                    {
                        "ok": False,
                        "admitted": False,
                        "reason": error_code,
                        "error": error_code,
                    },
                    status=status,
                )
            capture_fence_digest = (
                _private_local_voice_capture_fence_digest()
            )
            if not capture_fence_digest:
                error_code, status = _revoke_local_voice_for_capture_fence()
                return respond(
                    {
                        "ok": False,
                        "admitted": False,
                        "reason": error_code,
                        "error": error_code,
                    },
                    status=status,
                )
            request_id = json.dumps(
                [bridge_instance_id, bridge_turn_id],
                ensure_ascii=False,
                separators=(",", ":"),
            )

            def durable_reservation(
                reservation_request: LocalVoiceIssuanceReservationRequest,
            ) -> LocalVoiceDurableIssuanceReservation:
                with _acquire_local_voice_capture_claim_lease():
                    if (
                        not local_voice_capture_fence_is_current(
                            reservation_request.bridge_instance_id
                        )
                        or not hmac.compare_digest(
                            reservation_request.capture_fence_digest,
                            _private_local_voice_capture_fence_digest(),
                        )
                    ):
                        raise LocalVoiceAdmissionTransactionError(
                            "local_voice_capture_fence_not_current"
                        )
                    receipt = dict(
                        owner.reserve_ingress(
                            request_id=request_id,
                            text_hash=(
                                reservation_request.forward_text_digest
                            ),
                            turn_id=reservation_request.ingress_turn_id,
                            reservation_ref=(
                                reservation_request.reservation_ref
                            ),
                            ttl_sec=reservation_request.ttl_sec,
                        )
                    )
                    expected_entry_id = conversation_ingress_entry_id(
                        surface=FAST_CONTROL_INGRESS_SURFACE,
                        scope=FAST_CONTROL_SESSION_KEY,
                        source_delivery_id=request_id,
                    )
                    if (
                        reservation_request.bridge_instance_id
                        != bridge_instance_id
                        or reservation_request.turn_id != bridge_turn_id
                        or receipt.get("entryId") != expected_entry_id
                        or receipt.get("turnId")
                        != reservation_request.ingress_turn_id
                        or receipt.get("textHash")
                        != reservation_request.forward_text_digest
                        or receipt.get("phase") != "reserved"
                        or receipt.get("disposition") != "reserved"
                        or receipt.get("shouldProcess") is not False
                        or type(receipt.get("journalGeneration")) is not int
                    ):
                        raise LocalVoiceAdmissionTransactionError(
                            "local_voice_issuance_reservation_binding_mismatch"
                        )
                    reservation = LocalVoiceDurableIssuanceReservation(
                        schema=str(receipt.get("schema") or ""),
                        durable=receipt.get("durable") is True,
                        bridge_instance_id=(
                            reservation_request.bridge_instance_id
                        ),
                        local_turn_id=reservation_request.turn_id,
                        forward_text_digest=(
                            reservation_request.forward_text_digest
                        ),
                        reservation_ref=(
                            reservation_request.reservation_ref
                        ),
                        entry_id=str(receipt.get("entryId") or ""),
                        ingress_turn_id=str(receipt.get("turnId") or ""),
                        phase=str(receipt.get("phase") or ""),
                        disposition=str(receipt.get("disposition") or ""),
                        should_process=receipt["shouldProcess"],
                        text_hash=str(receipt.get("textHash") or ""),
                        journal_generation=receipt["journalGeneration"],
                    )
                    if (
                        not local_voice_capture_fence_is_current(
                            reservation_request.bridge_instance_id
                        )
                        or not hmac.compare_digest(
                            reservation_request.capture_fence_digest,
                            _private_local_voice_capture_fence_digest(),
                        )
                    ):
                        _durable_local_voice_reservation_revocation(
                            (
                                LocalVoiceReservationRevocationRequest(
                                    bridge_instance_id=(
                                        reservation_request.bridge_instance_id
                                    ),
                                    turn_id=reservation_request.turn_id,
                                    forward_text_digest=(
                                        reservation_request.forward_text_digest
                                    ),
                                    validation_binding_digest=(
                                        reservation_request.validation_binding_digest
                                    ),
                                    mode=reservation_request.mode,
                                    token_digest=(
                                        reservation_request.token_digest
                                    ),
                                    ingress_turn_id=(
                                        reservation_request.ingress_turn_id
                                    ),
                                    reservation_ref=(
                                        reservation_request.reservation_ref
                                    ),
                                    capture_fence_digest=(
                                        reservation_request.capture_fence_digest
                                    ),
                                ),
                            )
                        )
                        raise LocalVoiceAdmissionTransactionError(
                            "local_voice_capture_fence_not_current"
                        )
                    return reservation

            transaction = (
                LOCAL_VOICE_ADMISSION.issue_with_durable_reservation(
                    payload.get("bridgeInstanceId"),
                    payload.get("turnId"),
                    text,
                    durable_reservation=durable_reservation,
                    durable_revocation=(
                        _durable_local_voice_reservation_revocation
                    ),
                    capture_fence_digest=capture_fence_digest,
                    validation_binding=binding,
                    validation_is_current=(
                        local_voice_validation_binding_is_current
                    ),
                )
            )
            result = transaction.admission
        response = respond(
            result,
            status=200 if result.get("admitted") is True else 409,
        )
    except ConversationIngressBindingMismatch:
        return respond(
            {
                "ok": False,
                "admitted": False,
                "reason": "local_voice_turn_binding_mismatch",
                "error": "local_voice_turn_binding_mismatch",
            },
            status=409,
        )
    except ConversationIngressRecoveryError:
        return respond(
            {
                "ok": False,
                "admitted": False,
                "reason": "conversation_ingress_recovery_unavailable",
                "error": "conversation_ingress_recovery_unavailable",
            },
            status=503,
        )
    except LocalVoiceAdmissionTransactionError as exc:
        if exc.code == "local_voice_capture_fence_not_current":
            error_code, status = _revoke_local_voice_for_capture_fence()
            return respond(
                {
                    "ok": False,
                    "admitted": False,
                    "reason": error_code,
                    "error": error_code,
                },
                status=status,
            )
        if exc.code in {
            "local_voice_capture_claim_inflight",
            "local_voice_capture_claim_lease_unavailable",
        }:
            return respond(
                {
                    "ok": False,
                    "admitted": False,
                    "reason": exc.code,
                    "error": exc.code,
                },
                status=503,
            )
        error_code = (
            "local_voice_reservation_revocation_failed"
            if "revocation" in exc.code
            else "conversation_ingress_recovery_unavailable"
        )
        return respond(
            {
                "ok": False,
                "admitted": False,
                "reason": error_code,
                "error": error_code,
            },
            status=503,
        )
    except (OSError, RuntimeError):
        return respond(
            {
                "ok": False,
                "admitted": False,
                "reason": "conversation_ingress_recovery_unavailable",
                "error": "conversation_ingress_recovery_unavailable",
            },
            status=503,
        )
    finally:
        if validation_lease is not None:
            validation_lease.release()
    return response


def _cached_fast_control_json_response(
    record: dict[str, Any],
    *,
    exposure: MemoryExposurePosition | None,
    source: str,
) -> MemoryGuardedJsonResponse:
    receipt_ref = sanitize_memory_receipt_ref(
        record.get("memoryReceiptRef")
    )
    if receipt_ref is None:
        return _ingress_error_response(
            FAST_CONTROL_INGRESS_REPLAY_ERROR,
            status=409,
        )
    payload: dict[str, Any] = {
        "ok": True,
        "reply": str(record["assistantText"]),
        "cached": True,
        "suppressTts": should_suppress_tts_for_command(
            str(record["acceptedText"])
        ),
        "memoryReceiptRef": receipt_ref,
        "ingress": {
            "state": "completed",
            "cached": True,
            "automaticReplay": False,
        },
    }
    if clean_text(source) in {"local_bridge", "local_mic", "voice"}:
        payload.update(
            {
                "memoryState": (
                    "bound" if exposure is not None else "not_used"
                ),
                "memoryBoundary": (
                    memory_exposure_position_to_dict(exposure)
                    if exposure is not None
                    else None
                ),
            }
        )
    return memory_guarded_json_response(
        payload,
        expected_position=exposure,
    )


async def _cached_fast_control_stream_response(
    request: web.Request,
    record: dict[str, Any],
    *,
    exposure: MemoryExposurePosition | None,
    source: str,
) -> web.StreamResponse:
    receipt_ref = sanitize_memory_receipt_ref(
        record.get("memoryReceiptRef")
    )
    if receipt_ref is None:
        return _ingress_error_response(
            FAST_CONTROL_INGRESS_REPLAY_ERROR,
            status=409,
        )
    response = web.StreamResponse(
        status=200,
        headers={
            "Content-Type": "application/x-ndjson; charset=utf-8",
            "Cache-Control": "no-store",
            "X-Accel-Buffering": "no",
        },
    )
    local_handoff = clean_text(source) in {
        "local_bridge",
        "local_mic",
        "voice",
    }
    with memory_exposure_guard(
        expected_position=exposure,
        required=receipt_ref["state"] == "bound",
        index_dir=Path(MEMORY_ROOT) / "memory_index",
    ):
        await response.prepare(request)
        if local_handoff:
            await write_stream_event(
                response,
                {
                    "type": "memory_boundary",
                    "memoryState": (
                        "bound" if exposure is not None else "not_used"
                    ),
                    "memoryBoundary": (
                        memory_exposure_position_to_dict(exposure)
                        if exposure is not None
                        else None
                    ),
                },
            )
        reply = str(record["assistantText"])
        await write_stream_event(
            response,
            {
                "type": "sentence",
                "text": reply,
                "cached": True,
                "suppressTts": should_suppress_tts_for_command(
                    str(record["acceptedText"])
                ),
            },
        )
        await write_stream_event(
            response,
            {
                "type": "done",
                "ok": True,
                "reply": reply,
                "cached": True,
                "memoryReceiptRef": receipt_ref,
                "ingress": {
                    "state": "completed",
                    "cached": True,
                    "automaticReplay": False,
                },
            },
        )
        await response.write_eof()
    return response


async def _finalize_fast_chat_response(
    *,
    text: str,
    reply: str,
    source: str,
    suppress_tts: bool,
    queued_speech_count: int,
    task_record: FastActionTask | None,
    task_runner: Callable[[str, str], Awaitable[str]] | None,
    memory_write_receipt: dict[str, Any] | None,
    error_code: str,
    ingress_claim: dict[str, Any] | None = None,
    validation_lease: VoiceValidationAttemptLeaseSet | None = None,
) -> web.StreamResponse:
    isolated_validation = validation_lease is not None
    response_exposure = capture_combined_memory_exposure(
        FAST_MEMORY_EXPOSURE_POSITION.get(),
        current_memory_exposure_position(),
    )
    FAST_MEMORY_EXPOSURE_POSITION.set(response_exposure)
    with memory_exposure_guard(
        expected_position=response_exposure,
        required=response_exposure is not None,
        index_dir=Path(MEMORY_ROOT) / "memory_index",
    ):
        response_memory_receipt_ref = (
            current_fast_response_memory_receipt_ref()
        )
        ingress_entry_id = clean_text(
            (ingress_claim or {}).get("entryId")
        )
        if ingress_entry_id and not isolated_validation:
            try:
                FAST_CONTROL_CONTINUITY_OWNER.bind_ingress_response(
                    ingress_entry_id,
                    assistant_text=reply,
                    memory_receipt_ref=response_memory_receipt_ref,
                )
            except ConversationIngressRecoveryError:
                return _ingress_error_response(
                    "conversation_ingress_recovery_unavailable",
                    status=503,
                    after_terminal=(
                        validation_lease.release
                        if validation_lease is not None
                        else None
                    ),
                )
        if not isolated_validation:
            append_chat_message(
                "assistant",
                "Evelyn",
                reply,
                source="fast_control_api",
                task_id=(
                    task_record.task_id
                    if task_record is not None
                    else None
                ),
                task_status=(
                    task_record.status
                    if task_record is not None
                    else None
                ),
                memory_receipt=response_memory_receipt_ref,
                memory_write_receipt=memory_write_receipt,
            )
        continuity = (
            _pending_fast_control_continuity_result()
            if ingress_entry_id or isolated_validation
            else (
                commit_fast_control_terminal_turn(
                    task_record.task_id,
                    text,
                    reply,
                    memory_receipt=response_memory_receipt_ref,
                )
                if (
                    task_record is not None
                    and task_record.status != "running"
                )
                else commit_fast_control_turn(
                    text,
                    reply,
                    memory_receipt=response_memory_receipt_ref,
                )
            )
        )
        if (
            not suppress_tts
            and should_queue_local_bridge_speech(source)
            and queued_speech_count <= 0
        ):
            queue_local_bridge_speech(reply, source=source)
        state = {}
        if validation_lease is None:
            health = await cached_fast_runtime_health()
            state = build_control_state(health)
        final_response_exposure = current_memory_exposure_position()
        result: dict[str, Any] = {
            "ok": not bool(error_code),
            "reply": reply,
            "suppressTts": suppress_tts,
            "state": state,
            "continuity": continuity,
            "memoryReceipt": current_fast_memory_context_receipt(),
        }
        if clean_text(source) in {"local_bridge", "local_mic", "voice"}:
            result["memoryState"] = (
                "bound" if response_exposure is not None else "not_used"
            )
            result["memoryBoundary"] = (
                memory_exposure_position_to_dict(response_exposure)
                if response_exposure is not None
                else None
            )
        if memory_write_receipt is not None:
            result["memoryWriteReceipt"] = memory_write_receipt
        if error_code:
            result["error"] = error_code
        if task_record is not None:
            result["task"] = task_record.to_dict()
        if ingress_entry_id:
            result["ingress"] = {
                "state": "delivery_pending",
                "cached": False,
                "automaticReplay": False,
            }
        after_write: Callable[[], None] | None = None
        before_write: Callable[[], None] | None = None
        after_write_failure: Callable[[str], None] | None = None
        if ingress_entry_id:
            def begin_ingress_delivery() -> None:
                FAST_CONTROL_CONTINUITY_OWNER.mark_ingress_delivery_inflight(
                    ingress_entry_id,
                    delivery_ref=(
                        FAST_CONTROL_EPHEMERAL_VALIDATION_DELIVERY_REF
                        if isolated_validation
                        else FAST_CONTROL_HTTP_DELIVERY_REF
                    ),
                    streaming=isolated_validation,
                )

            def fail_ingress_delivery(failure_code: str) -> None:
                if isolated_validation:
                    FAST_CONTROL_CONTINUITY_OWNER.complete_ephemeral_ingress(
                        ingress_entry_id,
                        assistant_text=reply,
                        memory_receipt_ref=response_memory_receipt_ref,
                    )
                    return
                FAST_CONTROL_CONTINUITY_OWNER.mark_ingress_delivery_ambiguous(
                    ingress_entry_id,
                    error_code=failure_code,
                )

            def complete_ingress_delivery() -> None:
                if isolated_validation:
                    FAST_CONTROL_CONTINUITY_OWNER.complete_ephemeral_ingress(
                        ingress_entry_id,
                        assistant_text=reply,
                        memory_receipt_ref=response_memory_receipt_ref,
                    )
                    return
                FAST_CONTROL_CONTINUITY_OWNER.mark_ingress_delivery_succeeded(
                    ingress_entry_id,
                    delivery_ref=FAST_CONTROL_HTTP_DELIVERY_REF,
                )
                committed = (
                    commit_fast_control_terminal_turn(
                        task_record.task_id,
                        text,
                        reply,
                        memory_receipt=response_memory_receipt_ref,
                        ingress_entry_id=ingress_entry_id,
                    )
                    if (
                        task_record is not None
                        and task_record.status != "running"
                    )
                    else commit_fast_control_turn(
                        text,
                        reply,
                        memory_receipt=response_memory_receipt_ref,
                        ingress_entry_id=ingress_entry_id,
                    )
                )
                if committed.get("durable") is not True:
                    return
                if (
                    task_record is not None
                    and task_runner is not None
                    and task_record.status == "running"
                ):
                    launch_background_action(
                        task_record,
                        task_runner,
                    )

            before_write = begin_ingress_delivery
            after_write_failure = fail_ingress_delivery
            after_write = complete_ingress_delivery
        elif (
            task_record is not None
            and task_runner is not None
            and task_record.status == "running"
        ):
            def launch_after_response_write() -> None:
                if task_record.status == "running":
                    launch_background_action(
                        task_record,
                        task_runner,
                    )

            after_write = launch_after_response_write
        return memory_guarded_json_response(
            result,
            expected_position=final_response_exposure,
            after_write=after_write,
            before_write=before_write,
            after_write_failure=after_write_failure,
            after_terminal=(
                validation_lease.release
                if validation_lease is not None
                else None
            ),
        )


async def chat_handler(request: web.Request) -> web.StreamResponse:
    try:
        payload = await request.json()
    except Exception:
        return memory_guarded_json_response(
            {"ok": False, "error": "invalid_json"},
            expected_position=None,
            status=400,
        )
    if not isinstance(payload, dict):
        return memory_guarded_json_response(
            {"ok": False, "error": "invalid_json"},
            expected_position=None,
            status=400,
        )
    text = clean_text(payload.get("text"))
    if not text:
        return memory_guarded_json_response(
            {"ok": False, "error": "empty_text"},
            expected_position=None,
            status=400,
        )
    source = clean_text(payload.get("source")).lower() or "control_page"
    action_id = (
        payload.get("turnId")
        or payload.get("requestId")
        or ""
    )
    text, preclaimed_ingress, admission_rejection = (
        consume_local_voice_admission(
            payload,
            text=text,
            source=source,
        )
    )
    if admission_rejection is not None:
        return admission_rejection
    validation_lease = payload.pop(
        _LOCAL_VOICE_VALIDATION_LEASE_KEY,
        None,
    )
    isolated_validation = validation_lease is not None
    reset_fast_memory_context_receipt()
    try:
        ingress_claim, cached_replay, ingress_rejection = (
            _prepare_fast_control_ingress(
                payload,
                accepted_text=text,
                source=source,
                preclaimed=preclaimed_ingress,
            )
        )
    except BaseException:
        if validation_lease is not None:
            validation_lease.release()
        raise
    if ingress_rejection is not None:
        if validation_lease is not None:
            if isinstance(
                ingress_rejection,
                MemoryGuardedJsonResponse,
            ):
                ingress_rejection.adopt_after_terminal(
                    validation_lease.release
                )
            else:
                validation_lease.release()
        return ingress_rejection
    if cached_replay is not None:
        cached_record, cached_exposure = cached_replay
        response = _cached_fast_control_json_response(
            cached_record,
            exposure=cached_exposure,
            source=source,
        )
        if validation_lease is not None:
            response.adopt_after_terminal(validation_lease.release)
        return response
    if ingress_claim is not None:
        action_id = str(ingress_claim["_effectId"])
    suppress_tts = should_suppress_tts_for_command(text)
    if validation_lease is None:
        append_chat_message("user", "정훈", text, source=source)
    tool_plan: FastToolPlan | None = None
    queued_speech_count = 0
    error_code = ""
    task_record: FastActionTask | None = None
    task_runner: Callable[[str, str], Awaitable[str]] | None = None
    memory_write_receipt: dict[str, Any] | None = None
    try:
        if validation_lease is not None:
            memory_command_matched = False
            memory_command_reply = ""
            memory_command_error = ""
        else:
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
            tool_plan = (
                None
                if validation_lease is not None
                else await plan_fast_tool_request_for_turn(text)
            )
            pre_llm_reply = (
                None
                if validation_lease is not None
                else await resolve_pre_llm_reply(text, source=source)
            )
            if pre_llm_reply is not None:
                reply = pre_llm_reply
            else:
                prepared_action = (
                    None
                    if validation_lease is not None
                    else prepare_tool_plan_background_action(
                        tool_plan,
                        text,
                        source=source,
                    )
                    or prepare_registered_background_action(
                        text,
                        source=source,
                    )
                )
                if prepared_action is not None:
                    task_record, task_runner = prepared_action
                    reply = task_record.start_reply
                else:
                    if should_queue_local_bridge_speech(source):
                        if tool_plan is None:
                            reply, queued_speech_count = await ask_main_llm_and_queue_speech(
                                text,
                                source=source,
                                isolated_validation=(
                                    validation_lease is not None
                                ),
                            )
                        else:
                            reply, queued_speech_count = await ask_main_llm_and_queue_speech(
                                text,
                                source=source,
                                tool_plan=tool_plan,
                            )
                    else:
                        if tool_plan is None:
                            reply = await ask_main_llm(
                                text,
                                source=source,
                                isolated_validation=(
                                    validation_lease is not None
                                ),
                            )
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
    except MemoryDeletionJournalIntegrityError:
        if validation_lease is not None:
            validation_lease.release()
        raise
    except Exception as exc:
        error_code = "fast_control_chat_failed"
        print(
            "[FAST CONTROL] chat_failed "
            f"errorType={type(exc).__name__}",
            flush=True,
        )
        if (
            not isolated_validation
            and task_record is not None
            and task_record.status == "running"
        ):
            ACTION_COORDINATOR.fail(
                task_record.task_id,
                error_code,
                reply=public_failure_message(error_code),
                memory_receipt=not_used_memory_receipt_ref(),
            )
        reply = public_failure_message(error_code)
        task_runner = None
    except BaseException:
        if validation_lease is not None:
            validation_lease.release()
        raise
    try:
        return await _finalize_fast_chat_response(
            text=text,
            reply=reply,
            source=source,
            suppress_tts=suppress_tts,
            queued_speech_count=queued_speech_count,
            task_record=task_record,
            task_runner=task_runner,
            memory_write_receipt=memory_write_receipt,
            error_code=error_code,
            ingress_claim=ingress_claim,
            validation_lease=validation_lease,
        )
    except BaseException:
        if validation_lease is not None:
            validation_lease.release()
        raise


async def write_stream_event(response: web.StreamResponse, payload: dict[str, Any]) -> None:
    await response.write((json.dumps(payload, ensure_ascii=False) + "\n").encode("utf-8"))


async def _chat_stream_handler(request: web.Request) -> web.StreamResponse:
    try:
        payload = await request.json()
    except Exception:
        return json_response({"ok": False, "error": "invalid_json"}, status=400)
    if not isinstance(payload, dict):
        return json_response({"ok": False, "error": "invalid_json"}, status=400)
    text = clean_text(payload.get("text"))
    if not text:
        return json_response({"ok": False, "error": "empty_text"}, status=400)
    source = clean_text(payload.get("source")).lower() or "control_page"
    action_id = (
        payload.get("turnId")
        or payload.get("requestId")
        or ""
    )
    text, preclaimed_ingress, admission_rejection = (
        consume_local_voice_admission(
            payload,
            text=text,
            source=source,
        )
    )
    if admission_rejection is not None:
        return admission_rejection
    validation_lease = payload.pop(
        _LOCAL_VOICE_VALIDATION_LEASE_KEY,
        None,
    )
    isolated_validation = validation_lease is not None
    FAST_VALIDATION_ATTEMPT_LEASE.set(validation_lease)
    reset_fast_memory_context_receipt()
    ingress_claim, cached_replay, ingress_rejection = (
        _prepare_fast_control_ingress(
            payload,
            accepted_text=text,
            source=source,
            preclaimed=preclaimed_ingress,
        )
    )
    if ingress_rejection is not None:
        return ingress_rejection
    if cached_replay is not None:
        cached_record, cached_exposure = cached_replay
        return await _cached_fast_control_stream_response(
            request,
            cached_record,
            exposure=cached_exposure,
            source=source,
        )
    if ingress_claim is not None:
        action_id = str(ingress_claim["_effectId"])
    suppress_tts = should_suppress_tts_for_command(text)
    if validation_lease is None:
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
    llm_stream: AsyncIterator[str] | None = None
    response_finished = False
    terminal_reply_recorded = False
    local_memory_handoff_required = clean_text(source) in {
        "local_bridge",
        "local_mic",
        "voice",
    }
    stream_memory_boundary_emitted = False
    stream_memory_exposure: MemoryExposurePosition | None = None
    ingress_entry_id = clean_text(
        (ingress_claim or {}).get("entryId")
    )
    ingress_delivery_started = False
    ingress_delivery_failed = False

    def mark_stream_delivery_ambiguous(error_code: str) -> None:
        nonlocal ingress_delivery_failed
        if (
            not ingress_entry_id
            or not ingress_delivery_started
            or ingress_delivery_failed
        ):
            return
        ingress_delivery_failed = True
        try:
            FAST_CONTROL_CONTINUITY_OWNER.mark_ingress_delivery_ambiguous(
                ingress_entry_id,
                error_code=error_code,
            )
        except ConversationIngressRecoveryError as exc:
            print(
                "[FAST CONTROL] stream_ingress_ambiguous_failed "
                f"errorCode={exc.code}",
                flush=True,
            )

    async def ensure_response_prepared() -> None:
        nonlocal ingress_delivery_started
        if not response.prepared:
            if ingress_entry_id and not ingress_delivery_started:
                FAST_CONTROL_CONTINUITY_OWNER.mark_ingress_delivery_inflight(
                    ingress_entry_id,
                    delivery_ref=(
                        FAST_CONTROL_EPHEMERAL_VALIDATION_DELIVERY_REF
                        if isolated_validation
                        else FAST_CONTROL_STREAM_DELIVERY_REF
                    ),
                    streaming=True,
                )
                ingress_delivery_started = True
            try:
                await response.prepare(request)
            except BaseException:
                mark_stream_delivery_ambiguous(
                    "conversation_ingress_delivery_failed"
                )
                raise

    def active_stream_memory_exposure() -> MemoryExposurePosition | None:
        position = capture_combined_memory_exposure(
            FAST_MEMORY_EXPOSURE_POSITION.get(),
            current_memory_exposure_position(),
        )
        FAST_MEMORY_EXPOSURE_POSITION.set(position)
        return position

    async def write_event_at_memory_exposure(
        event_payload: dict[str, Any],
        position: MemoryExposurePosition | None,
    ) -> None:
        with memory_exposure_guard(
            expected_position=position,
            required=position is not None,
            index_dir=Path(MEMORY_ROOT) / "memory_index",
        ):
            try:
                await ensure_response_prepared()
                await write_stream_event(response, event_payload)
            except BaseException:
                mark_stream_delivery_ambiguous(
                    "conversation_ingress_delivery_disconnected"
                )
                raise

    async def ensure_local_memory_boundary() -> None:
        nonlocal stream_memory_boundary_emitted, stream_memory_exposure
        if not local_memory_handoff_required:
            return
        position = active_stream_memory_exposure()
        if stream_memory_boundary_emitted:
            if position != stream_memory_exposure:
                raise MemoryDeletionJournalIntegrityError()
            return
        stream_memory_exposure = position
        stream_memory_boundary_emitted = True
        await write_event_at_memory_exposure(
            {
                "type": "memory_boundary",
                "memoryState": (
                    "bound" if position is not None else "not_used"
                ),
                "memoryBoundary": (
                    memory_exposure_position_to_dict(position)
                    if position is not None
                    else None
                ),
            },
            position,
        )

    async def write_response_event(event_payload: dict[str, Any]) -> None:
        if local_memory_handoff_required:
            await ensure_local_memory_boundary()
        position = active_stream_memory_exposure()
        if (
            local_memory_handoff_required
            and position != stream_memory_exposure
        ):
            raise MemoryDeletionJournalIntegrityError()
        await write_event_at_memory_exposure(event_payload, position)

    def local_memory_handoff_fields() -> dict[str, Any]:
        if not local_memory_handoff_required:
            return {}
        if not stream_memory_boundary_emitted:
            raise MemoryDeletionJournalIntegrityError()
        return {
            "memoryState": (
                "bound"
                if stream_memory_exposure is not None
                else "not_used"
            ),
            "memoryBoundary": (
                memory_exposure_position_to_dict(
                    stream_memory_exposure
                )
                if stream_memory_exposure is not None
                else None
            ),
        }

    async def emit_delta(fragment: str) -> None:
        nonlocal first_delta_ms
        if not fragment:
            return
        elapsed_ms = (time.perf_counter() - started_at) * 1000.0
        if first_delta_ms is None:
            first_delta_ms = elapsed_ms
        await write_response_event(
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
        await write_response_event(
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
        await write_response_event(
            {
                "type": "sentence",
                "text": chunk,
                "suppressTts": suppress_tts,
                "elapsedMs": round(elapsed_ms, 1),
            },
        )

    async def consume_llm_delta(delta: str) -> None:
        nonlocal clean_seen_len, sentence_buffer
        raw_parts.append(delta)
        cleaned = visible_text("".join(raw_parts))
        new_text = cleaned[clean_seen_len:]
        clean_seen_len = len(cleaned)
        if not new_text:
            return
        for safe_fragment in speech_filter.push(new_text):
            await emit_delta(safe_fragment)
        sentence_buffer += new_text
        chunks, sentence_buffer = pop_speakable_chunks(
            sentence_buffer
        )
        for chunk in chunks:
            if has_unbacked_progress_claim(chunk):
                continue
            await emit_sentence(chunk)

    async def finalize_success_delivery() -> None:
        nonlocal response_finished, terminal_reply_recorded
        response_exposure = active_stream_memory_exposure()
        with memory_exposure_guard(
            expected_position=response_exposure,
            required=response_exposure is not None,
            index_dir=Path(MEMORY_ROOT) / "memory_index",
        ):
            await ensure_local_memory_boundary()
            response_memory_receipt_ref = (
                current_fast_response_memory_receipt_ref()
            )
            if ingress_entry_id and not isolated_validation:
                FAST_CONTROL_CONTINUITY_OWNER.bind_ingress_response(
                    ingress_entry_id,
                    assistant_text=reply,
                    memory_receipt_ref=response_memory_receipt_ref,
                )
            if not isolated_validation:
                append_chat_message(
                    "assistant",
                    "Evelyn",
                    reply,
                    source="fast_control_api_stream",
                    task_id=(
                        task_record.task_id
                        if task_record is not None
                        else None
                    ),
                    task_status=(
                        task_record.status
                        if task_record is not None
                        else None
                    ),
                    memory_receipt=response_memory_receipt_ref,
                    memory_write_receipt=memory_write_receipt,
                )
            terminal_reply_recorded = True
            continuity = (
                _pending_fast_control_continuity_result()
                if ingress_entry_id or isolated_validation
                else commit_fast_control_turn(
                    text,
                    reply,
                    memory_receipt=response_memory_receipt_ref,
                )
            )
            await write_response_event(
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
                    **local_memory_handoff_fields(),
                    **(
                        {
                            "ingress": {
                                "state": "delivery_pending",
                                "cached": False,
                                "automaticReplay": False,
                            }
                        }
                        if ingress_entry_id
                        else {}
                    ),
                },
            )
            try:
                await response.write_eof()
            except BaseException:
                mark_stream_delivery_ambiguous(
                    "conversation_ingress_delivery_disconnected"
                )
                if isolated_validation and ingress_entry_id:
                    FAST_CONTROL_CONTINUITY_OWNER.complete_ephemeral_ingress(
                        ingress_entry_id,
                        assistant_text=reply,
                        memory_receipt_ref=response_memory_receipt_ref,
                    )
                raise
            response_finished = True

        delivery_committed = True
        if ingress_entry_id:
            if isolated_validation:
                FAST_CONTROL_CONTINUITY_OWNER.complete_ephemeral_ingress(
                    ingress_entry_id,
                    assistant_text=reply,
                    memory_receipt_ref=response_memory_receipt_ref,
                )
            else:
                FAST_CONTROL_CONTINUITY_OWNER.mark_ingress_delivery_succeeded(
                    ingress_entry_id,
                    delivery_ref=FAST_CONTROL_STREAM_DELIVERY_REF,
                )
                delivery_continuity = commit_fast_control_turn(
                    text,
                    reply,
                    memory_receipt=response_memory_receipt_ref,
                    ingress_entry_id=ingress_entry_id,
                )
                delivery_committed = bool(
                    delivery_continuity.get("durable") is True
                )
        if (
            delivery_committed
            and task_record is not None
            and task_runner is not None
            and task_record.status == "running"
        ):
            launch_background_action(task_record, task_runner)

    try:
        if validation_lease is not None:
            memory_command_matched = False
            memory_command_reply = ""
            memory_command_error = ""
        else:
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
            tool_plan = (
                None
                if validation_lease is not None
                else await plan_fast_tool_request_for_turn(text)
            )
            pre_llm_reply = (
                None
                if validation_lease is not None
                else await resolve_pre_llm_reply(text, source=source)
            )
            if pre_llm_reply is not None:
                reply = enforce_action_reply_contract(pre_llm_reply)
                await emit_sentence(reply)
            else:
                prepared_action = (
                    None
                    if validation_lease is not None
                    else prepare_tool_plan_background_action(
                        tool_plan,
                        text,
                        source=source,
                    )
                    or prepare_registered_background_action(
                        text,
                        source=source,
                    )
                )
                if prepared_action is not None:
                    task_record, task_runner = prepared_action
                    reply = enforce_action_reply_contract(
                        task_record.start_reply,
                        active_task_id=task_record.task_id,
                    )
                    await emit_sentence(reply)
                else:
                    llm_stream = (
                        iter_main_llm_deltas(
                            text,
                            source=source,
                            isolated_validation=True,
                        )
                        if isolated_validation
                        else (
                            iter_main_llm_deltas(text, source=source)
                            if tool_plan is None
                            else iter_main_llm_deltas(
                                text,
                                source=source,
                                tool_plan=tool_plan,
                            )
                        )
                    )
                    try:
                        first_delta = await anext(llm_stream)
                        has_first_delta = True
                    except StopAsyncIteration:
                        first_delta = ""
                        has_first_delta = False
                    if validation_lease is None and should_emit_memory_recall_progress(
                        text,
                        source=source,
                    ):
                        await emit_progress(
                            next_memory_recall_progress_text(),
                            stage="memory_recall",
                        )
                    if has_first_delta:
                        await consume_llm_delta(first_delta)
                    async for delta in llm_stream:
                        await consume_llm_delta(delta)
                    tail_exposure = FAST_MEMORY_EXPOSURE_POSITION.get()
                    with memory_exposure_guard(
                        expected_position=tail_exposure,
                        required=tail_exposure is not None,
                        index_dir=Path(MEMORY_ROOT) / "memory_index",
                    ):
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
        await finalize_success_delivery()
    except MemoryDeletionJournalIntegrityError:
        if ingress_delivery_started:
            mark_stream_delivery_ambiguous(
                "conversation_ingress_delivery_failed"
            )
            if isolated_validation and ingress_entry_id:
                partial_reply = (
                    visible_text("".join(raw_parts))
                    or clean_text(" ".join(emitted_chunks))
                    or public_failure_message("fast_control_stream_failed")
                )
                if not bool(getattr(response, "_eof_sent", False)):
                    with contextlib.suppress(Exception):
                        await response.write_eof()
                FAST_CONTROL_CONTINUITY_OWNER.complete_ephemeral_ingress(
                    ingress_entry_id,
                    assistant_text=partial_reply,
                    memory_receipt_ref=not_used_memory_receipt_ref(),
                )
            response_finished = True
            return response
        raise
    except Exception as exc:
        error_code = "fast_control_stream_failed"
        failure_reply = public_failure_message(error_code)
        print(
            "[FAST CONTROL] chat_stream_failed "
            f"errorType={type(exc).__name__}",
            flush=True,
        )
        if ingress_delivery_started:
            if isolated_validation and ingress_entry_id:
                partial_reply = (
                    visible_text("".join(raw_parts))
                    or clean_text(" ".join(emitted_chunks))
                    or failure_reply
                )
                if not bool(getattr(response, "_eof_sent", False)):
                    with contextlib.suppress(Exception):
                        await response.write_eof()
                FAST_CONTROL_CONTINUITY_OWNER.complete_ephemeral_ingress(
                    ingress_entry_id,
                    assistant_text=partial_reply,
                    memory_receipt_ref=not_used_memory_receipt_ref(),
                )
                response_finished = True
                return response
            # Once any stream event crossed the HTTP boundary, a later
            # generation failure cannot be represented by a second fixed
            # assistant payload. Preserve the observed prefix as ambiguous
            # and leave it non-replayable/non-terminal.
            mark_stream_delivery_ambiguous(
                "conversation_ingress_delivery_ambiguous"
            )
            response_finished = True
            return response
        if ingress_delivery_failed or terminal_reply_recorded:
            response_finished = True
            return response
        if (
            not isolated_validation
            and task_record is not None
            and task_record.status == "running"
        ):
            failed = ACTION_COORDINATOR.fail(
                task_record.task_id,
                error_code,
                reply=failure_reply,
                memory_receipt=not_used_memory_receipt_ref(),
            )
            failure_reply = failed.final_reply
            append_chat_message(
                "assistant",
                "Evelyn",
                failed.final_reply,
                source="fast_control_action_followup",
                task_id=failed.task_id,
                task_status=failed.status,
                memory_receipt=not_used_memory_receipt_ref(),
            )
        elif not isolated_validation:
            append_chat_message(
                "assistant",
                "Evelyn",
                failure_reply,
                source="fast_control_api_stream",
                memory_receipt=not_used_memory_receipt_ref(),
            )
        failure_receipt = not_used_memory_receipt_ref()
        if ingress_entry_id and not isolated_validation:
            try:
                FAST_CONTROL_CONTINUITY_OWNER.bind_ingress_response(
                    ingress_entry_id,
                    assistant_text=failure_reply,
                    memory_receipt_ref=failure_receipt,
                )
            except ConversationIngressRecoveryError:
                if ingress_delivery_started:
                    mark_stream_delivery_ambiguous(
                        "conversation_ingress_delivery_failed"
                    )
                    response_finished = True
                    return response
                return _ingress_error_response(
                    "conversation_ingress_recovery_unavailable",
                    status=503,
                )
        continuity = (
            _pending_fast_control_continuity_result()
            if ingress_entry_id or isolated_validation
            else (
                commit_fast_control_terminal_turn(
                    task_record.task_id,
                    text,
                    failure_reply,
                    memory_receipt=failure_receipt,
                )
                if task_record is not None
                else commit_fast_control_turn(
                    text,
                    failure_reply,
                    memory_receipt=failure_receipt,
                )
            )
        )
        try:
            await write_response_event(
                {
                    "type": "error",
                    "ok": False,
                    "error": error_code,
                    "message": failure_reply,
                    "memoryReceipt": current_fast_memory_context_receipt(),
                    "continuity": continuity,
                    **(
                        {
                            "ingress": {
                                "state": "delivery_pending",
                                "cached": False,
                                "automaticReplay": False,
                            }
                        }
                        if ingress_entry_id
                        else {}
                    ),
                },
            )
            await response.write_eof()
            response_finished = True
        except BaseException:
            mark_stream_delivery_ambiguous(
                "conversation_ingress_delivery_disconnected"
            )
            if isolated_validation and ingress_entry_id:
                FAST_CONTROL_CONTINUITY_OWNER.complete_ephemeral_ingress(
                    ingress_entry_id,
                    assistant_text=failure_reply,
                    memory_receipt_ref=failure_receipt,
                )
            response_finished = True
            return response
        if ingress_entry_id:
            if isolated_validation:
                FAST_CONTROL_CONTINUITY_OWNER.complete_ephemeral_ingress(
                    ingress_entry_id,
                    assistant_text=failure_reply,
                    memory_receipt_ref=failure_receipt,
                )
            else:
                FAST_CONTROL_CONTINUITY_OWNER.mark_ingress_delivery_succeeded(
                    ingress_entry_id,
                    delivery_ref=FAST_CONTROL_STREAM_DELIVERY_REF,
                )
                (
                    commit_fast_control_terminal_turn(
                        task_record.task_id,
                        text,
                        failure_reply,
                        memory_receipt=failure_receipt,
                        ingress_entry_id=ingress_entry_id,
                    )
                    if task_record is not None
                    else commit_fast_control_turn(
                        text,
                        failure_reply,
                        memory_receipt=failure_receipt,
                        ingress_entry_id=ingress_entry_id,
                    )
                )
    finally:
        if llm_stream is not None:
            close_stream = getattr(
                llm_stream,
                "aclose",
                None,
            )
            if callable(close_stream):
                with contextlib.suppress(Exception):
                    await close_stream()
    if not response_finished:
        response_exposure = active_stream_memory_exposure()
        with memory_exposure_guard(
            expected_position=response_exposure,
            required=response_exposure is not None,
            index_dir=Path(MEMORY_ROOT) / "memory_index",
        ):
            await ensure_response_prepared()
            try:
                await response.write_eof()
            except BaseException:
                mark_stream_delivery_ambiguous(
                    "conversation_ingress_delivery_disconnected"
                )
                raise
    return response


async def chat_stream_handler(request: web.Request) -> web.StreamResponse:
    context_token = FAST_VALIDATION_ATTEMPT_LEASE.set(None)
    try:
        response = await _chat_stream_handler(request)
        validation_lease = FAST_VALIDATION_ATTEMPT_LEASE.get()
        if (
            validation_lease is not None
            and isinstance(response, MemoryGuardedJsonResponse)
        ):
            response.adopt_after_terminal(validation_lease.release)
            FAST_VALIDATION_ATTEMPT_LEASE.set(None)
        elif validation_lease is not None:
            if not response.prepared:
                await response.prepare(request)
            if not bool(getattr(response, "_eof_sent", False)):
                await response.write_eof()
        return response
    finally:
        validation_lease = FAST_VALIDATION_ATTEMPT_LEASE.get()
        if validation_lease is not None:
            validation_lease.release()
        FAST_VALIDATION_ATTEMPT_LEASE.reset(context_token)


async def local_bridge_status_handler(request: web.Request) -> web.StreamResponse:
    speak_requests: list[dict[str, Any]] = []
    status_ack: dict[str, Any] | None = None
    if request.method == "POST":
        authorized, error, status = _request_has_control_token(
            request,
            header=LOCAL_BRIDGE_STATUS_AUTH_HEADER,
            expected=LOCAL_BRIDGE_STATUS_AUTH_TOKEN,
        )
        if not authorized:
            return json_response({"ok": False, "error": error}, status=status)
        if (
            request.content_length is not None
            and request.content_length > LOCAL_BRIDGE_STATUS_MAX_BYTES
        ):
            return json_response(
                {"ok": False, "error": "invalid_local_bridge_status"},
                status=413,
            )
        try:
            raw_payload = await request.read()
            if len(raw_payload) > LOCAL_BRIDGE_STATUS_MAX_BYTES:
                raise ValueError("status_payload_too_large")
            payload = json.loads(raw_payload.decode("utf-8"))
        except (UnicodeError, ValueError, TypeError, RecursionError):
            return json_response(
                {"ok": False, "error": "invalid_local_bridge_status"},
                status=400,
            )
        accepted_at = time.time()
        normalized = _normalize_local_bridge_status(
            payload,
            now=accepted_at,
        )
        if normalized is None:
            return json_response(
                {"ok": False, "error": "invalid_local_bridge_status"},
                status=400,
            )
        if not _local_bridge_status_order_is_valid(normalized):
            return json_response(
                {
                    "ok": False,
                    "error": "local_bridge_status_out_of_order",
                },
                status=409,
            )
        # Replace the complete authoritative snapshot. A partial or delayed
        # report can never inherit fields/freshness from a previous heartbeat.
        LOCAL_BRIDGE_STATUS.clear()
        LOCAL_BRIDGE_STATUS.update(normalized)
        LOCAL_BRIDGE_STATUS["updatedAt"] = accepted_at
        bridge_instance_id = normalized["bridgeInstanceId"]
        status_ack = local_bridge_status_snapshot()
        status_ack["bridgeInstanceDigest"] = hashlib.sha256(
            bridge_instance_id.encode("utf-8")
        ).hexdigest()
        try:
            LOCAL_VOICE_ADMISSION.observe_bridge_instance(
                bridge_instance_id,
                durable_revocation=(
                    _durable_local_voice_reservation_revocation
                ),
            )
        except LocalVoiceAdmissionTransactionError:
            # Keep status/control delivery alive so the Bridge can physically
            # stop capture. The manager's durable revocation fence stays shut.
            pass
        if normalized["micEnabled"] is False:
            try:
                _reset_local_voice_admission(
                    "mic_disabled",
                    revoke_scope=True,
                )
            except LocalVoiceAdmissionTransactionError:
                pass
        minecraft_revision = _strict_nonnegative_int(
            normalized.get("minecraftCommandRevision")
        ) or 0
        minecraft_state = clean_text(
            normalized.get("minecraftCommandState")
        ).lower()
        if minecraft_revision and minecraft_state in {"ready", "failed"}:
            clear_local_bridge_minecraft_command_request(minecraft_revision)
        speak_requests = drain_local_bridge_speak_requests()
    else:
        authorized, error, status = _request_has_control_token(
            request,
            header=EVELYN_INTERNAL_CONTROL_HEADER,
            expected=EVELYN_INTERNAL_CONTROL_TOKEN,
        )
        if not authorized:
            return json_response({"ok": False, "error": error}, status=status)
    return json_response(
        {
            "ok": True,
            "localBridge": status_ack or local_bridge_status_snapshot(),
            "speakRequests": speak_requests,
            "outputDeviceRequest": dict(LOCAL_BRIDGE_OUTPUT_DEVICE_REQUEST)
            if LOCAL_BRIDGE_OUTPUT_DEVICE_REQUEST.get("outputDevice")
            else {},
            "micControlRequest": dict(LOCAL_BRIDGE_MIC_CONTROL_REQUEST),
            "minecraftCommandRequest": dict(LOCAL_BRIDGE_MINECRAFT_COMMAND_REQUEST),
            "restart": dict(RESTART_REQUEST),
            "shutdown": dict(SHUTDOWN_REQUEST),
            "voiceAdmission": LOCAL_VOICE_ADMISSION.public_status(),
        }
    )


async def local_bridge_mic_handler(request: web.Request) -> web.StreamResponse:
    authorized, auth_error, auth_status = _request_has_control_token(
        request,
        header=EVELYN_INTERNAL_CONTROL_HEADER,
        expected=EVELYN_INTERNAL_CONTROL_TOKEN,
        unauthorized_error="mic_control_unauthorized",
    )
    if not authorized:
        return json_response(
            {"ok": False, "error": auth_error},
            status=auth_status,
        )
    if request.method == "GET":
        return json_response(
            {
                "ok": True,
                "request": dict(LOCAL_BRIDGE_MIC_CONTROL_REQUEST),
                "localBridge": local_bridge_status_snapshot(),
                "enableFence": local_bridge_mic_enable_fence_snapshot(),
            }
        )
    try:
        payload = await request.json()
    except Exception:
        return json_response({"ok": False, "error": "invalid_json"}, status=400)
    if not isinstance(payload, dict):
        return json_response(
            {"ok": False, "error": "json_object_required"},
            status=400,
        )
    if not set(payload).issubset(
        {"enabled", "source", "purpose", "enableFence"}
    ):
        return json_response(
            {"ok": False, "error": "invalid_mic_control_fields"},
            status=400,
        )
    value = payload.get("enabled")
    if not isinstance(value, bool):
        return json_response(
            {"ok": False, "error": "missing_mic_enabled"},
            status=400,
        )
    purpose = clean_text(payload.get("purpose"))
    source = clean_text(payload.get("source")) or "control_page"
    if len(source) > 128:
        return json_response(
            {"ok": False, "error": "invalid_mic_control_source"},
            status=400,
        )
    try:
        request_state = request_local_bridge_mic_control(
            value,
            source=source,
            purpose=purpose,
            enable_fence=(
                payload.get("enableFence")
                if isinstance(payload.get("enableFence"), dict)
                else None
            ),
        )
    except PermissionError as exc:
        error = clean_text(exc) or "mic_enable_not_authorized"
        return json_response(
            {"ok": False, "applied": False, "error": error},
            status=(
                409 if error == "mic_enable_fence_stale" else 403
            ),
        )
    except LocalVoiceAdmissionTransactionError:
        return json_response(
            {
                "ok": False,
                "applied": False,
                "error": "local_voice_reservation_revocation_failed",
            },
            status=503,
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
        return memory_guarded_json_response(
            {"ok": False, "error": "invalid_after_cursor"},
            expected_position=None,
            status=400,
        )
    reset_memory_exposure_position()
    snapshot = _public_fast_action_snapshot()
    response = memory_guarded_json_response(
        {
            "ok": True,
            "after": max(0, after),
            "lastEventId": snapshot["lastEventId"],
            "activeCount": snapshot["activeCount"],
            "events": [
                event
                for event in snapshot["events"]
                if int(event.get("id") or 0) > max(0, after)
            ],
            "tasks": snapshot["tasks"],
        },
        expected_position=current_memory_exposure_position(),
    )
    return response


async def shutdown_handler(request: web.Request) -> web.StreamResponse:
    try:
        payload = await request.json()
    except Exception:
        payload = {}
    source = clean_text((payload or {}).get("source")) or "control_page"
    reason = clean_text((payload or {}).get("reason")) or "shutdown_endpoint"
    try:
        result = request_local_shutdown(source=source, reason=reason)
    except LocalVoiceAdmissionTransactionError:
        return memory_guarded_json_response(
            {
                "ok": False,
                "error": "local_voice_reservation_revocation_failed",
            },
            expected_position=None,
            status=503,
        )
    return memory_guarded_json_response(result, expected_position=None)


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
    app.router.add_post(
        "/api/local-voice/admission",
        local_voice_admission_handler,
    )
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
