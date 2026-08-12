from __future__ import annotations

import json
import math
import re
import threading
import time
from pathlib import Path
from typing import Any, Callable, Mapping

from .paths import get_runtime_artifacts_root


RUNTIME_ERROR_SUMMARY_SCHEMA = "runtime_errors.summary.v1"
DEFAULT_RECENT_AFTER_SEC = 60 * 60
_SAFE_CODE_PATTERN = re.compile(r"[^a-z0-9_.-]+")
_SAFE_TYPE_PATTERN = re.compile(r"[^A-Za-z0-9_.-]+")
_SAFE_EXCEPTION_TYPE_PATTERN = re.compile(
    r"^[A-Za-z][A-Za-z0-9_.]{0,79}(?:Error|Exception)$"
)
_KNOWN_ERROR_CODES = frozenset(
    {
        "automatic_restart_budget_exhausted",
        "autonomy_followup_finalize_failed",
        "autonomy_start_failed",
        "autonomy_stop_failed",
        "chat_stream_failed",
        "codex_backend_failed",
        "codex_credentials_failed",
        "codex_handler_failed",
        "codex_prompt_cleanup_failed",
        "control_page_background_tasks_failed",
        "control_page_start_failed",
        "control_tts_failed",
        "conversation_continuity_checkpoint_rejected",
        "conversation_continuity_commit_failed",
        "conversation_continuity_commit_latency_high",
        "continuity_auth_bootstrap_required",
        "continuity_auth_failed",
        "continuity_auth_key_required",
        "continuity_anchor_auth_failed",
        "continuity_anchor_bootstrap_required",
        "continuity_anchor_record_rejected",
        "continuity_anchor_replay_detected",
        "continuity_anchor_unavailable",
        "conversation_continuity_flush_failed",
        "conversation_continuity_guild_reset_failed",
        "conversation_continuity_guild_reset_finalize_failed",
        "conversation_continuity_guild_reset_revoke_failed",
        "conversation_continuity_restore_failed",
        "docker_compose_failed",
        "discord_voice_text_delivery_failed",
        "gateway_readiness_probe_failed",
        "heartbeat_write_failed",
        "host_action_launch_failed",
        "local_bridge_unexpected_exit",
        "mindcraft_auto_restart_failed",
        "mindcraft_log_close_failed",
        "mindcraft_start_failed",
        "mindcraft_stop_failed",
        "mindcraft_world_lease_guard_failed",
        "minecraft_lazy_start_failed",
        "output_device_probe_failed",
        "restart_start_failed",
        "retention_reporter_stop_failed",
        "runtime_error",
        "shutdown_start_failed",
        "speaker_verification_failed",
        "speaker_verifier_unavailable",
        "stt_import_failed",
        "stt_model_load_failed",
        "stt_timeout",
        "stt_transcribe_failed",
        "startup_initialization_failed",
        "status_write_failed",
        "tts_warmup_attempt_failed",
        "tts_playback_failed",
        "tts_producer_cancelled",
        "tts_request_failed",
        "turn_pipeline_failed",
        "voice_connection_probe_failed",
        "voice_connection_unavailable",
        "voice_delivery_empty",
        "voice_delivery_failed",
        "voice_listening_probe_failed",
        "voice_rearm_failed",
        "voice_state_rearm_failed",
        "wake_probe_failed",
        "vision_analyze_failed",
        "vision_describe_failed",
        "vision_model_load_failed",
        "vision_ocr_generation_failed",
        "vision_ocr_load_failed",
        "vision_reaper_failed",
    }
)
_SOURCE_SPECS: tuple[dict[str, Any], ...] = (
    {
        "id": "hostSupervisor",
        "label": "Host Supervisor",
        "path": Path("host_supervisor") / "status.json",
        "schema": "host_supervisor.status.v1",
        "staleAfterSec": 4.0,
    },
    {
        "id": "localBridge",
        "label": "Local I/O Bridge",
        "path": Path("local_bridge") / "status.json",
        "schema": "local_io_bridge.status.v1",
        "staleAfterSec": 8.0,
    },
    {
        "id": "discord",
        "label": "Discord",
        "path": Path("discord") / "status.json",
        "schema": "discord_runtime.status.v1",
        "staleAfterSec": 8.0,
    },
    {
        "id": "conversationContinuity",
        "label": "Conversation Continuity",
        "path": Path("conversation_continuity") / "status.json",
        "schema": "conversation_continuity.status.v1",
        "staleAfterSec": 5.0,
    },
    {
        "id": "fastControlContinuity",
        "label": "Fast Control Continuity",
        "path": Path("fast_control_continuity") / "status.json",
        "schema": "conversation_continuity.status.v1",
        "staleAfterSec": DEFAULT_RECENT_AFTER_SEC,
    },
)
_HTTP_SOURCE_SPECS: tuple[dict[str, str], ...] = (
    {
        "id": "stt",
        "label": "STT",
        "serviceId": "stt",
    },
    {
        "id": "vision",
        "label": "Vision",
        "serviceId": "vision",
    },
    {
        "id": "mindcraft",
        "label": "Mindcraft",
        "serviceId": "voyager",
    },
    {
        "id": "codexGateway",
        "label": "Codex Gateway",
        "serviceId": "codex_gateway",
    },
)
_REQUIRED_HEALTH_SOURCE_SPECS: tuple[dict[str, str], ...] = (
    {"id": "controlPage", "label": "Control Page", "serviceId": "control_page"},
    {"id": "botApi", "label": "Bot API", "serviceId": "bot_api"},
    {"id": "mainLlm", "label": "Main LLM", "serviceId": "main_llm"},
    {"id": "subLlm", "label": "Sub LLM", "serviceId": "sub_llm"},
    {"id": "routerLlm", "label": "Router LLM", "serviceId": "router_llm"},
    {"id": "tts", "label": "TTS", "serviceId": "tts"},
)


def sanitize_runtime_error_code(value: Any, *, fallback: str = "runtime_error") -> str:
    text = _SAFE_CODE_PATTERN.sub("_", str(value or "").strip().lower())
    text = text.strip("._-")
    code = text[:80]
    return code if code in _KNOWN_ERROR_CODES else fallback


def sanitize_runtime_error_type(value: Any) -> str:
    text = _SAFE_TYPE_PATTERN.sub("_", str(value or "").strip())
    candidate = text.strip("._-")[:80]
    return (
        candidate
        if _SAFE_EXCEPTION_TYPE_PATTERN.fullmatch(candidate)
        else ""
    )


class RuntimeErrorCounter:
    def __init__(self, *, now: Callable[[], float] = time.time) -> None:
        self.now = now
        self.total_count = 0
        self.last_error_at: float | None = None
        self.last_error_code = ""
        self.last_error_type = ""
        self.counters: dict[str, int] = {}
        self._lock = threading.Lock()

    def record(
        self,
        code: str,
        error: BaseException | type[BaseException] | None = None,
    ) -> dict[str, Any]:
        safe_code = sanitize_runtime_error_code(code)
        if isinstance(error, BaseException):
            error_type = type(error).__name__
        elif isinstance(error, type) and issubclass(error, BaseException):
            error_type = error.__name__
        else:
            error_type = ""
        safe_type = sanitize_runtime_error_type(error_type)
        with self._lock:
            self.total_count += 1
            self.last_error_at = float(self.now())
            self.last_error_code = safe_code
            self.last_error_type = safe_type
            self.counters[safe_code] = self.counters.get(safe_code, 0) + 1
            return self._snapshot_unlocked()

    def _snapshot_unlocked(self) -> dict[str, Any]:
        return {
            "errorCount": self.total_count,
            "lastErrorAt": self.last_error_at,
            "lastErrorCode": self.last_error_code,
            "lastErrorType": self.last_error_type,
            "errorCounters": dict(sorted(self.counters.items())),
        }

    def snapshot(self) -> dict[str, Any]:
        with self._lock:
            return self._snapshot_unlocked()


def _safe_number(value: Any) -> float | None:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return number if math.isfinite(number) else None


def _safe_count(value: Any) -> int:
    try:
        return max(0, int(value or 0))
    except (OverflowError, TypeError, ValueError):
        return 0


def _safe_counters(value: Any) -> dict[str, int]:
    if not isinstance(value, dict):
        return {}
    counters: dict[str, int] = {}
    for raw_code, raw_count in value.items():
        code = sanitize_runtime_error_code(raw_code)
        count = _safe_count(raw_count)
        if count:
            counters[code] = counters.get(code, 0) + count
        if len(counters) >= 64:
            break
    return dict(sorted(counters.items()))


def _unavailable_source(spec: dict[str, Any], *, state: str) -> dict[str, Any]:
    return {
        "id": spec["id"],
        "label": spec["label"],
        "state": state,
        "available": False,
        "stale": False,
        "heartbeatAt": None,
        "errorCount": 0,
        "lastErrorAt": None,
        "lastErrorCode": "",
        "lastErrorType": "",
        "errorCounters": {},
        "hasCurrentError": False,
    }


def _safe_continuity_commit_metrics(
    value: Any,
) -> dict[str, Any] | None:
    if (
        not isinstance(value, Mapping)
        or value.get("schema")
        != "conversation_continuity.commit-metrics.v1"
    ):
        return None
    state = str(value.get("state") or "").strip().lower()
    if state not in {"idle", "warming", "ready", "warning", "error"}:
        return None
    warning_code = sanitize_runtime_error_code(
        value.get("warningCode"),
        fallback="",
    )
    return {
        "schema": "conversation_continuity.commit-metrics.v1",
        "state": state,
        "attemptCount": _safe_count(value.get("attemptCount")),
        "successCount": _safe_count(value.get("successCount")),
        "failureCount": _safe_count(value.get("failureCount")),
        "sampleCount": _safe_count(value.get("sampleCount")),
        "lastMs": _safe_number(value.get("lastMs")),
        "p50Ms": _safe_number(value.get("p50Ms")),
        "p95Ms": _safe_number(value.get("p95Ms")),
        "maxMs": _safe_number(value.get("maxMs")),
        "lastAt": _safe_number(value.get("lastAt")),
        "lastSucceeded": (
            value.get("lastSucceeded")
            if isinstance(value.get("lastSucceeded"), bool)
            else None
        ),
        "lastTargetVerified": (
            value.get("lastTargetVerified")
            if isinstance(value.get("lastTargetVerified"), bool)
            else None
        ),
        "warningThresholdMs": _safe_number(
            value.get("warningThresholdMs")
        ),
        "warningCode": warning_code,
    }


def _read_source(
    root: Path,
    spec: dict[str, Any],
    *,
    now: float,
) -> tuple[dict[str, Any], str | None]:
    try:
        path = (root / spec["path"]).resolve()
        path.relative_to(root)
    except (OSError, ValueError):
        return _unavailable_source(spec, state="invalid"), "artifact_path_invalid"
    if not path.exists():
        return _unavailable_source(spec, state="missing"), None
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
        if not isinstance(payload, dict) or payload.get("schema") != spec["schema"]:
            raise ValueError("invalid_schema")
        heartbeat_at = _safe_number(payload.get("heartbeatAt"))
        if heartbeat_at is None:
            heartbeat_at = path.stat().st_mtime
        age_sec = max(0.0, now - heartbeat_at)
        stale = age_sec > float(spec["staleAfterSec"])
        last_error_at = _safe_number(payload.get("lastErrorAt"))
        last_error_code = sanitize_runtime_error_code(
            payload.get("lastErrorCode"),
            fallback="",
        )
        last_error_type = sanitize_runtime_error_type(payload.get("lastErrorType"))
        error_count = _safe_count(payload.get("errorCount"))
        counters = _safe_counters(payload.get("errorCounters"))
        has_current_error = bool(
            last_error_code
            and (
                str(payload.get("lastError") or "").strip()
                or str(payload.get("state") or "").strip().lower()
                in {"error", "down", "degraded"}
            )
        )
        commit_metrics = (
            _safe_continuity_commit_metrics(
                payload.get("completedTurnCommit")
            )
            if spec["id"] in {
                "conversationContinuity",
                "fastControlContinuity",
            }
            else None
        )
        commit_warning = bool(
            commit_metrics
            and commit_metrics["state"] == "warning"
            and commit_metrics["warningCode"]
            == "conversation_continuity_commit_latency_high"
            and not stale
        )
        source_state = "stale" if stale else (
            "degraded" if commit_warning else "ready"
        )
        source = {
            "id": spec["id"],
            "label": spec["label"],
            "state": source_state,
            "available": True,
            "stale": stale,
            "heartbeatAt": heartbeat_at,
            "errorCount": error_count,
            "lastErrorAt": last_error_at,
            "lastErrorCode": last_error_code,
            "lastErrorType": last_error_type,
            "errorCounters": counters,
            "hasCurrentError": has_current_error,
        }
        if commit_metrics is not None:
            source["completedTurnCommit"] = commit_metrics
        return (
            source,
            (
                "conversation_continuity_commit_latency_high"
                if commit_warning
                else None
            ),
        )
    except (OSError, OverflowError, TypeError, ValueError) as exc:
        return (
            _unavailable_source(spec, state="invalid"),
            f"artifact_invalid:{type(exc).__name__}",
        )


def _service_rows(
    service_health: Mapping[str, Any] | None,
) -> Mapping[str, Any]:
    if not isinstance(service_health, Mapping):
        return {}
    raw_services = service_health.get("services")
    if isinstance(raw_services, list):
        return {
            str(item.get("id")): item
            for item in raw_services
            if isinstance(item, Mapping) and item.get("id")
        }
    return service_health


def _service_error_source(
    service_rows: Mapping[str, Any],
    spec: dict[str, str],
) -> dict[str, Any]:
    base_spec: dict[str, Any] = {
        "id": spec["id"],
        "label": spec["label"],
    }
    service = service_rows.get(spec["serviceId"])
    if not isinstance(service, Mapping):
        return _unavailable_source(base_spec, state="missing")
    state = str(service.get("state") or "unknown").strip().lower()
    payload: Mapping[str, Any] | None = None
    for check in service.get("checks") or []:
        if not isinstance(check, Mapping):
            continue
        candidate = check.get("payload")
        if isinstance(candidate, Mapping) and (
            "errorCount" in candidate
            or "lastErrorCode" in candidate
            or "errorCounters" in candidate
        ):
            payload = candidate
            break
    if payload is None:
        required_failure = _required_service_failure_source(service, spec)
        if required_failure is not None:
            return required_failure
        return _unavailable_source(base_spec, state="unavailable")

    last_error_at = _safe_number(payload.get("lastErrorAt"))
    last_error_code = sanitize_runtime_error_code(
        payload.get("lastErrorCode"),
        fallback="",
    )
    current_failure = (
        payload.get("ok") is False
        or payload.get("ready") is False
        or payload.get("lastActionReady") is False
        or state in {"down", "partial", "degraded"}
    )
    return {
        "id": spec["id"],
        "label": spec["label"],
        "state": "ready" if state == "up" else state,
        "available": True,
        "stale": False,
        "heartbeatAt": _safe_number(service.get("checkedAt")),
        "errorCount": _safe_count(payload.get("errorCount")),
        "lastErrorAt": last_error_at,
        "lastErrorCode": last_error_code,
        "lastErrorType": sanitize_runtime_error_type(
            payload.get("lastErrorType")
        ),
        "errorCounters": _safe_counters(payload.get("errorCounters")),
        "hasCurrentError": bool(last_error_code and current_failure),
    }


def _required_service_failure_source(
    service: Any,
    spec: dict[str, str],
) -> dict[str, Any] | None:
    if not isinstance(service, Mapping) or not bool(service.get("required")):
        return None
    state = str(service.get("state") or "unknown").strip().lower()
    if state == "up":
        return None
    if state not in {"down", "partial", "degraded", "unknown"}:
        state = "unknown"
    return {
        **_unavailable_source(spec, state=state),
        "available": True,
        "heartbeatAt": _safe_number(service.get("checkedAt")),
        "hasCurrentError": True,
    }


def collect_runtime_error_observability(
    *,
    artifacts_root: Path | None = None,
    now: float | None = None,
    recent_after_sec: float = DEFAULT_RECENT_AFTER_SEC,
    service_health: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    root = Path(artifacts_root or get_runtime_artifacts_root()).resolve()
    current_time = time.time() if now is None else float(now)
    recent_window = max(60.0, float(recent_after_sec))
    sources: dict[str, dict[str, Any]] = {}
    warnings: list[dict[str, str]] = []
    for spec in _SOURCE_SPECS:
        source, warning = _read_source(root, spec, now=current_time)
        sources[source["id"]] = source
        if warning:
            warnings.append(
                {
                    "source": source["id"],
                    "code": warning,
                }
            )
    service_rows = _service_rows(service_health)
    for spec in _HTTP_SOURCE_SPECS:
        source = _service_error_source(service_rows, spec)
        sources[source["id"]] = source
    for spec in _REQUIRED_HEALTH_SOURCE_SPECS:
        source = _required_service_failure_source(
            service_rows.get(spec["serviceId"]),
            spec,
        )
        if source is not None:
            sources[source["id"]] = source

    available_count = sum(1 for source in sources.values() if source["available"])
    stale_count = sum(1 for source in sources.values() if source["stale"])
    total_count = sum(source["errorCount"] for source in sources.values())
    recent_errors = sorted(
        (
            {
                "source": source["id"],
                "at": source["lastErrorAt"],
                "code": source["lastErrorCode"],
                "type": source["lastErrorType"],
            }
            for source in sources.values()
            if source["lastErrorAt"] is not None
            and current_time - float(source["lastErrorAt"]) <= recent_window
        ),
        key=lambda item: float(item["at"] or 0.0),
        reverse=True,
    )
    current_error_count = sum(
        1
        for source in sources.values()
        if source["available"] and not source["stale"] and source["hasCurrentError"]
    )
    if current_error_count:
        state = "error"
    elif recent_errors or any(
        warning["code"]
        == "conversation_continuity_commit_latency_high"
        for warning in warnings
    ):
        state = "attention"
    elif available_count and stale_count < available_count:
        state = "clear"
    else:
        state = "unknown"
    return {
        "schema": RUNTIME_ERROR_SUMMARY_SCHEMA,
        "state": state,
        "generatedAt": current_time,
        "recentAfterSec": recent_window,
        "summary": {
            "sourceCount": len(sources),
            "availableCount": available_count,
            "staleCount": stale_count,
            "currentErrorCount": current_error_count,
            "recentErrorCount": len(recent_errors),
            "totalCount": total_count,
        },
        "sources": sources,
        "recentErrors": recent_errors[:10],
        "warnings": warnings,
        "privacy": {
            "exceptionMessages": False,
            "stackTraces": False,
            "filesystemPaths": False,
        },
    }


__all__ = [
    "DEFAULT_RECENT_AFTER_SEC",
    "RUNTIME_ERROR_SUMMARY_SCHEMA",
    "RuntimeErrorCounter",
    "collect_runtime_error_observability",
    "sanitize_runtime_error_code",
    "sanitize_runtime_error_type",
]
