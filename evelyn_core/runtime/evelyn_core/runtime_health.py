from __future__ import annotations

import asyncio
import contextlib
import json
import math
import os
import re
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Awaitable, Callable

import aiohttp

from .runtime_services import HealthProbeSpec, ServiceManifest, ServiceSpec, load_service_manifest
from .paths import get_runtime_artifacts_root
from .minecraft_autonomy_readiness import (
    MINECRAFT_READINESS_BLOCKERS,
    validate_minecraft_autonomy_readiness,
)
from .runtime_error_observability import (
    collect_runtime_error_observability,
    sanitize_runtime_error_code,
    sanitize_runtime_error_type,
)
from .voice_capabilities import attach_voice_capabilities


ProbeRunner = Callable[[ServiceSpec, HealthProbeSpec], Awaitable[dict[str, Any]]]

LEGACY_SERVICE_READY_KEYS = {
    "bot_api": "botReady",
    "main_llm": "mainReady",
    "router_llm": "routerReady",
    "sub_llm": "subReady",
    "tts": "ttsReady",
    "stt": "sttReady",
    "vision": "visionReady",
    "voyager": "voyagerReady",
    "codex_gateway": "codexReady",
}

PUBLIC_RUNTIME_HEALTH_SCHEMA = "runtime_health.public.v1"
_PUBLIC_CODE_RE = re.compile(r"^[a-z0-9_.:-]{1,120}$")
_PUBLIC_DIAGNOSTIC_RE = re.compile(r"^[A-Z0-9_:-]{1,120}$")
_PUBLIC_CAPABILITY_STATES = {
    "ready",
    "degraded",
    "unavailable",
    "unknown",
}
_PUBLIC_CAPABILITY_IDS = {
    "voiceLocal",
    "voiceDiscord",
    "screenVision",
}
_PUBLIC_ERROR_SOURCES = {
    "hostSupervisor": "Host Supervisor",
    "localBridge": "Local I/O Bridge",
    "discord": "Discord",
    "conversationContinuity": "Conversation Continuity",
    "fastControlContinuity": "Fast Control Continuity",
    "controlPage": "Control Page",
    "botApi": "Bot API",
    "mainLlm": "Main LLM",
    "subLlm": "Sub LLM",
    "routerLlm": "Router LLM",
    "tts": "TTS",
    "stt": "STT",
    "vision": "Vision",
    "mindcraft": "Mindcraft",
    "codexGateway": "Codex Gateway",
}


@dataclass(frozen=True)
class Diagnostic:
    code: str
    severity: str
    message: str
    service_ids: tuple[str, ...]
    details: str = ""
    suggested_actions: tuple[dict[str, Any], ...] = ()

    def to_dict(self) -> dict[str, Any]:
        return {
            "code": self.code,
            "severity": self.severity,
            "message": self.message,
            "details": self.details,
            "serviceIds": list(self.service_ids),
            "suggestedActions": list(self.suggested_actions),
        }


async def _probe_tcp(_: ServiceSpec, check: HealthProbeSpec) -> dict[str, Any]:
    started = time.monotonic()
    target = f"{check.host}:{check.port}"
    try:
        _reader, writer = await asyncio.wait_for(
            asyncio.open_connection(check.host, check.port),
            timeout=max(0.001, check.timeout_ms / 1000.0),
        )
        writer.close()
        with contextlib.suppress(Exception):
            await writer.wait_closed()
        return {
            "kind": "tcp",
            "ok": True,
            "reason": "ok",
            "target": target,
            "elapsedMs": round((time.monotonic() - started) * 1000.0, 1),
        }
    except asyncio.TimeoutError:
        return {
            "kind": "tcp",
            "ok": False,
            "reason": "timeout",
            "target": target,
            "elapsedMs": round((time.monotonic() - started) * 1000.0, 1),
            "error": "TimeoutError",
        }
    except Exception as exc:
        return {
            "kind": "tcp",
            "ok": False,
            "reason": "connection_failed",
            "target": target,
            "elapsedMs": round((time.monotonic() - started) * 1000.0, 1),
            "error": type(exc).__name__,
        }


async def _probe_http(_: ServiceSpec, check: HealthProbeSpec) -> dict[str, Any]:
    started = time.monotonic()
    path = check.path if check.path.startswith("/") else f"/{check.path}" if check.path else ""
    url = f"http://{check.host}:{check.port}{path}"
    timeout = aiohttp.ClientTimeout(total=max(0.001, check.timeout_ms / 1000.0))
    try:
        async with aiohttp.ClientSession(timeout=timeout) as session:
            async with session.request(check.method, url) as response:
                payload: Any = None
                with contextlib.suppress(Exception):
                    payload = await response.json(content_type=None)
                status_ok = response.status == check.expect_status if check.expect_status is not None else 200 <= response.status < 500
                json_ok = True
                if check.expect_json is not None:
                    json_ok = isinstance(payload, dict) and all(payload.get(key) == value for key, value in check.expect_json.items())
                ok = bool(status_ok and json_ok)
                return {
                    "kind": "http",
                    "ok": ok,
                    "reason": "ok" if ok else "unexpected_response",
                    "target": url,
                    "status": response.status,
                    "payload": payload if isinstance(payload, dict) else None,
                    "elapsedMs": round((time.monotonic() - started) * 1000.0, 1),
                }
    except asyncio.TimeoutError:
        return {
            "kind": "http",
            "ok": False,
            "reason": "timeout",
            "target": url,
            "status": None,
            "elapsedMs": round((time.monotonic() - started) * 1000.0, 1),
            "error": "TimeoutError",
        }
    except Exception as exc:
        return {
            "kind": "http",
            "ok": False,
            "reason": "request_failed",
            "target": url,
            "status": None,
            "elapsedMs": round((time.monotonic() - started) * 1000.0, 1),
            "error": type(exc).__name__,
        }


def _artifact_path(check: HealthProbeSpec) -> Path | None:
    root = get_runtime_artifacts_root().resolve()
    raw = str(check.path or "").replace("\\", "/").strip()
    if raw.startswith("runtime_artifacts/"):
        raw = raw.removeprefix("runtime_artifacts/")
    candidate = Path(raw)
    if not candidate.is_absolute():
        candidate = root / candidate
    try:
        resolved = candidate.resolve()
        resolved.relative_to(root)
    except (OSError, ValueError):
        return None
    return resolved


async def _probe_artifact_json(_: ServiceSpec, check: HealthProbeSpec) -> dict[str, Any]:
    started = time.monotonic()
    path = _artifact_path(check)
    if path is None:
        return {
            "kind": "artifact_json",
            "ok": False,
            "reason": "artifact_path_outside_runtime_root",
            "target": str(check.path or ""),
            "elapsedMs": round((time.monotonic() - started) * 1000.0, 1),
        }
    try:
        raw = await asyncio.to_thread(path.read_text, encoding="utf-8")
    except FileNotFoundError:
        return {
            "kind": "artifact_json",
            "ok": False,
            "reason": "artifact_missing",
            "target": str(path),
            "elapsedMs": round((time.monotonic() - started) * 1000.0, 1),
        }
    except OSError as exc:
        return {
            "kind": "artifact_json",
            "ok": False,
            "reason": "artifact_read_failed",
            "target": str(path),
            "error": type(exc).__name__,
            "elapsedMs": round((time.monotonic() - started) * 1000.0, 1),
        }
    try:
        payload = json.loads(raw)
    except (ValueError, TypeError):
        return {
            "kind": "artifact_json",
            "ok": False,
            "reason": "artifact_corrupt",
            "target": str(path),
            "elapsedMs": round((time.monotonic() - started) * 1000.0, 1),
        }
    if not isinstance(payload, dict):
        return {
            "kind": "artifact_json",
            "ok": False,
            "reason": "artifact_not_object",
            "target": str(path),
            "elapsedMs": round((time.monotonic() - started) * 1000.0, 1),
        }
    json_ok = True
    if check.expect_json is not None:
        json_ok = all(payload.get(key) == value for key, value in check.expect_json.items())
    heartbeat_at = next(
        (
            float(payload[key])
            for key in ("heartbeatAt", "updatedAt", "at")
            if isinstance(payload.get(key), (int, float))
        ),
        path.stat().st_mtime,
    )
    age_sec = max(0.0, time.time() - heartbeat_at)
    stale = check.stale_after_sec is not None and age_sec > check.stale_after_sec
    ok = bool(json_ok and not stale)
    return {
        "kind": "artifact_json",
        "ok": ok,
        "reason": "ok" if ok else "artifact_stale" if stale else "unexpected_json",
        "target": str(path),
        "payload": payload,
        "ageSec": round(age_sec, 2),
        "staleAfterSec": check.stale_after_sec,
        "elapsedMs": round((time.monotonic() - started) * 1000.0, 1),
    }


async def default_probe_runner(service: ServiceSpec, check: HealthProbeSpec) -> dict[str, Any]:
    if check.kind == "tcp":
        return await _probe_tcp(service, check)
    if check.kind == "http":
        return await _probe_http(service, check)
    if check.kind == "artifact_json":
        return await _probe_artifact_json(service, check)
    return {"kind": check.kind, "ok": False, "reason": "unknown_probe_kind"}


def _classify_state(results: list[dict[str, Any]]) -> tuple[str, str]:
    if not results:
        return "unknown", "no_checks"
    tcp_results = [result for result in results if result.get("kind") == "tcp"]
    http_results = [result for result in results if result.get("kind") == "http"]
    tcp_ok = any(bool(result.get("ok")) for result in tcp_results)
    http_ok = all(bool(result.get("ok")) for result in http_results) if http_results else True
    if tcp_results and not tcp_ok:
        return "down", str(tcp_results[-1].get("reason") or "port_closed")
    if tcp_ok and http_results and not http_ok:
        failed = next((result for result in http_results if not result.get("ok")), http_results[-1])
        reason = str(failed.get("reason") or "http_failed")
        if reason == "timeout":
            reason = "http_timeout"
        return "partial", reason
    if all(bool(result.get("ok")) for result in results):
        return "up", "ok"
    return "degraded", "check_failed"


def _suggest_start_action(service: ServiceSpec) -> tuple[dict[str, Any], ...]:
    if not service.repair or not service.repair.allowed:
        return ()
    return (
        {
            "id": f"start_{service.id}",
            "label": f"Start {service.label}",
            "risk": "medium",
            "requiresConfirm": bool(service.repair.requires_confirm),
            "strategy": service.repair.strategy,
        },
    )


def service_spec_by_id(manifest: ServiceManifest) -> dict[str, ServiceSpec]:
    return {service.id: service for service in manifest.services}


def _compact_detail(value: Any, *, max_chars: int = 220) -> str:
    text = str(value or "").replace("\r", " ").replace("\n", " ").strip()
    if len(text) <= max_chars:
        return text
    return text[: max_chars - 1].rstrip() + "..."


def _first_http_payload(service: dict[str, Any], *, path_suffix: str | None = None) -> dict[str, Any] | None:
    for check in service.get("checks") or []:
        if not isinstance(check, dict) or check.get("kind") != "http":
            continue
        if path_suffix:
            target = str(check.get("target") or "")
            if not target.endswith(path_suffix):
                continue
        payload = check.get("payload")
        if isinstance(payload, dict):
            return payload
    return None


def _annotate_functional_readiness(services: dict[str, dict[str, Any]]) -> None:
    voyager = services.get("voyager")
    if not isinstance(voyager, dict):
        return

    http_ready = voyager.get("state") == "up"
    runtime_ready = False
    contract_state = "unavailable"
    readiness: dict[str, Any] | None = None
    if http_ready:
        status_payload = _first_http_payload(voyager, path_suffix="/status") or _first_http_payload(voyager)
        recovery_state = status_payload.get("recovery_state") if isinstance(status_payload, dict) else None
        readiness, contract_state = (
            validate_minecraft_autonomy_readiness(
                status_payload
            )
            if isinstance(status_payload, dict)
            else (None, "missing")
        )
        if readiness is not None:
            if (
                isinstance(recovery_state, dict)
                and str(
                    recovery_state.get("scope") or ""
                ).strip().lower()
                == "runtime"
                and recovery_state.get("healthy") is False
            ):
                readiness = None
                contract_state = "invalid"
            else:
                runtime_ready = bool(readiness["ready"])
        elif (
            contract_state == "missing"
            and isinstance(status_payload, dict)
            and str(
                status_payload.get("runtime") or ""
            ).strip().lower()
            != "mindcraft"
            and isinstance(recovery_state, dict)
            and isinstance(
                recovery_state.get("healthy"),
                bool,
            )
        ):
            contract_state = "legacy"
            runtime_ready = not bool(
                str(
                    recovery_state.get("scope") or ""
                ).strip().lower()
                == "runtime"
                and recovery_state.get("healthy") is False
            )

    voyager["httpReady"] = http_ready
    voyager["runtimeReady"] = runtime_ready
    voyager["readinessContractState"] = contract_state
    if readiness is not None:
        voyager["functionalReadiness"] = readiness
        voyager["readinessBlockers"] = list(
            readiness["blockers"]
        )
    elif http_ready and contract_state in {"missing", "invalid"}:
        voyager["readinessBlockers"] = [
            f"readiness_contract_{contract_state}"
        ]
    if http_ready and not runtime_ready:
        voyager["ready"] = False
        voyager["reason"] = (
            "runtime_recovery_required"
            if contract_state == "legacy"
            else f"readiness_contract_{contract_state}"
            if contract_state in {"missing", "invalid"}
            else "functional_not_ready"
        )


def _status_text(value: Any) -> str:
    if isinstance(value, dict):
        for key in ("status", "state", "outcome", "decision", "reason_code", "reason"):
            raw = value.get(key)
            if isinstance(raw, (str, int, float, bool)) and str(raw).strip():
                return str(raw).strip()
    return ""


def _contract_failure_signal(value: Any) -> str:
    if not isinstance(value, dict):
        return ""
    explicit_success = value.get("success")
    if explicit_success is False:
        return "success=false"
    explicit_ok = value.get("ok")
    if explicit_ok is False:
        return "ok=false"
    status = _status_text(value).lower()
    failed_words = (
        "fail",
        "failed",
        "blocked",
        "reject",
        "rejected",
        "invalid",
        "missing",
        "unmet",
        "unsafe",
        "recovery_required",
    )
    if status and any(word in status for word in failed_words):
        return status
    return ""


def _voyager_contract_evidence(status_payload: dict[str, Any]) -> tuple[str, bool]:
    last_step = status_payload.get("status_summary", {}).get("last_step") if isinstance(status_payload.get("status_summary"), dict) else {}
    if not isinstance(last_step, dict):
        last_step = {}
    evidence_sources = {
        "contract": status_payload.get("last_task_contract_decision") or last_step.get("last_task_contract_decision"),
        "bookkeeping": status_payload.get("current_task_bookkeeping")
        or status_payload.get("last_task_bookkeeping")
        or last_step.get("current_task_bookkeeping")
        or last_step.get("last_task_bookkeeping"),
        "effect": status_payload.get("last_world_effect_verification") or last_step.get("last_world_effect_verification"),
        "critic": status_payload.get("last_critic_result") or last_step.get("last_critic_result"),
    }
    parts: list[str] = []
    has_failure = False
    for label, value in evidence_sources.items():
        if not isinstance(value, dict):
            continue
        signal = _contract_failure_signal(value)
        has_failure = has_failure or bool(signal)
        summary = signal or _status_text(value) or "present"
        parts.append(f"{label}={_compact_detail(summary, max_chars=80)}")
    return "; ".join(parts), has_failure


async def check_service(service: ServiceSpec, *, probe_runner: ProbeRunner | None = None) -> dict[str, Any]:
    runner = probe_runner or default_probe_runner
    started = time.monotonic()
    results: list[dict[str, Any]] = []
    for check in service.checks:
        probe_started = time.monotonic()
        try:
            result = await asyncio.wait_for(
                runner(service, check),
                timeout=max(0.001, check.timeout_ms / 1000.0),
            )
        except asyncio.TimeoutError:
            result = {
                "kind": check.kind,
                "ok": False,
                "reason": "timeout",
                "elapsedMs": round((time.monotonic() - probe_started) * 1000.0, 1),
            }
        results.append(dict(result))
        if check.kind == "tcp" and not result.get("ok"):
            break
    state, reason = _classify_state(results)
    ready = state == "up"
    return {
        "id": service.id,
        "label": service.label,
        "required": service.required,
        "host": service.host,
        "port": service.port,
        "defaultHost": service.default_host,
        "hostEnv": service.host_env,
        "defaultPort": service.default_port,
        "portEnv": service.port_env,
        "state": state,
        "ready": ready,
        "reason": reason,
        "checkedAt": time.time(),
        "elapsedMs": round((time.monotonic() - started) * 1000.0, 1),
        "checks": results,
        "suggestedActions": [] if ready else list(_suggest_start_action(service)),
    }


def _diagnose(services: dict[str, dict[str, Any]]) -> list[Diagnostic]:
    diagnostics: list[Diagnostic] = []
    control_page = services.get("control_page") or {}
    bot_api = services.get("bot_api") or {}
    if control_page.get("state") == "up" and bot_api.get("state") == "down":
        diagnostics.append(
            Diagnostic(
                code="CP_UP_BOT_DOWN",
                severity="error",
                message="Control-Page is up, but Bot API is down.",
                details="The page can load, but chat, memory commands, and runtime commands can fail.",
                service_ids=("control_page", "bot_api"),
                suggested_actions=tuple(bot_api.get("suggestedActions") or ()),
            )
        )
        diagnostics.append(
            Diagnostic(
                code="BOT_API_DOWN_WITH_CONTROL_PAGE_UP",
                severity="error",
                message="Control-Page is up, but Bot API is down.",
                details="The page can load, but chat, memory commands, and runtime commands can fail.",
                service_ids=("control_page", "bot_api"),
                suggested_actions=tuple(bot_api.get("suggestedActions") or ()),
            )
        )
    if bot_api.get("state") == "partial":
        diagnostics.append(
            Diagnostic(
                code="BOT_API_PARTIAL",
                severity="warning",
                message="Bot API port is open, but its HTTP health response is not ready.",
                details="The process may still be warming up, or the state payload may be slow.",
                service_ids=("bot_api",),
                suggested_actions=tuple(bot_api.get("suggestedActions") or ()),
            )
        )
    if control_page.get("state") == "down":
        diagnostics.append(
            Diagnostic(
                code="CONTROL_PAGE_DOWN",
                severity="error",
                message="Control-Page is not responding.",
                service_ids=("control_page",),
                suggested_actions=tuple(control_page.get("suggestedActions") or ()),
            )
        )
    for service_id, code, message in (
        ("main_llm", "MAIN_LLM_DOWN", "Main LLM is not responding, so answer generation is limited."),
        ("router_llm", "ROUTER_LLM_DOWN", "Router LLM is not responding, so routing is limited."),
        ("sub_llm", "SUB_LLM_DOWN", "Sub LLM is not responding, so summary and support judgment are limited."),
        ("tts", "TTS_DOWN", "TTS is not responding, so voice output is limited."),
        ("stt", "STT_DOWN", "STT is not responding, so voice input is limited."),
    ):
        service = services.get(service_id) or {}
        if service.get("required") and service.get("state") in {"down", "partial", "unknown"}:
            diagnostics.append(
                Diagnostic(
                    code=code,
                    severity="error",
                    message=message,
                    service_ids=(service_id,),
                    suggested_actions=tuple(service.get("suggestedActions") or ()),
                )
            )
    for service_id, code, message in (
        ("vision", "VISION_DOWN", "Vision is not responding, so screen perception is limited."),
        ("voyager", "VOYAGER_DOWN", "Voyager is not responding, so Minecraft autonomy is limited."),
        ("codex_gateway", "CODEX_GATEWAY_DOWN", "Codex Gateway is not responding, so Voyager code execution is limited."),
    ):
        service = services.get(service_id) or {}
        if service and service.get("state") in {"down", "partial", "unknown"}:
            diagnostics.append(
                Diagnostic(
                    code=code,
                    severity="warning",
                    message=message,
                    service_ids=(service_id,),
                    suggested_actions=tuple(service.get("suggestedActions") or ()),
                )
            )
    voyager = services.get("voyager") or {}
    if voyager.get("state") == "up":
        status_payload = _first_http_payload(voyager, path_suffix="/status") or _first_http_payload(voyager)
        recovery_state = status_payload.get("recovery_state") if isinstance(status_payload, dict) else None
        if (
            voyager.get("httpReady") is True
            and voyager.get("runtimeReady") is False
            and not (
                isinstance(recovery_state, dict)
                and str(
                    recovery_state.get("scope") or ""
                ).strip().lower()
                == "runtime"
                and recovery_state.get("healthy") is False
            )
        ):
            blockers = [
                str(item)
                for item in voyager.get(
                    "readinessBlockers"
                )
                or []
                if str(item) in {
                    *MINECRAFT_READINESS_BLOCKERS.values(),
                    "readiness_contract_missing",
                    "readiness_contract_invalid",
                }
            ]
            diagnostics.append(
                Diagnostic(
                    code="VOYAGER_RUNTIME_RECOVERY_REQUIRED",
                    severity="warning",
                    message=(
                        "Mindcraft HTTP is reachable, but Minecraft "
                        "autonomy is not functionally ready."
                    ),
                    details=(
                        "blockers=" + ",".join(blockers)
                        if blockers
                        else "blockers=readiness_unknown"
                    ),
                    service_ids=("voyager",),
                )
            )
        if isinstance(recovery_state, dict) and recovery_state.get("healthy") is False:
            scope = str(recovery_state.get("scope") or "unknown")
            domain = str(recovery_state.get("domain") or "unknown")
            subdomain = str(recovery_state.get("subdomain") or "")
            reason = _compact_detail(recovery_state.get("reason"))
            action = str(recovery_state.get("recommended_action") or "")
            evidence, contract_failed = _voyager_contract_evidence(status_payload)
            if scope == "runtime":
                code = "VOYAGER_RUNTIME_RECOVERY_REQUIRED"
                message = "Voyager is reachable, but its runtime boundary needs recovery."
            elif domain in {"task_unverified", "task_result_unverified", "task_bookkeeping_unverified"}:
                code = "VOYAGER_TASK_CONTRACT_UNVERIFIED"
                message = "Voyager reported task progress without an explicit verified success contract."
            elif contract_failed:
                code = "VOYAGER_TASK_CONTRACT_FAILED"
                message = "Voyager reported a failed task contract or verification signal."
            else:
                code = "VOYAGER_TASK_RECOVERY_REQUIRED"
                message = "Voyager is reachable, but the current task boundary needs recovery."
            details = "; ".join(
                part
                for part in (
                    f"scope={scope}",
                    f"domain={domain}",
                    f"subdomain={subdomain}" if subdomain else "",
                    f"reason={reason}" if reason else "",
                    f"recommended_action={action}" if action else "",
                    f"evidence={evidence}" if evidence else "",
                )
                if part
            )
            diagnostics.append(
                Diagnostic(
                    code=code,
                    severity="warning",
                    message=message,
                    details=details,
                    service_ids=("voyager",),
                    suggested_actions=tuple(voyager.get("suggestedActions") or ()),
                )
            )
    codex_gateway = services.get("codex_gateway") or {}
    if codex_gateway.get("state") == "up":
        payload = next(
            (
                check.get("payload")
                for check in codex_gateway.get("checks") or []
                if isinstance(check, dict) and isinstance(check.get("payload"), dict)
            ),
            None,
        )
        if isinstance(payload, dict) and payload.get("lastActionReady") is False:
            diagnostics.append(
                Diagnostic(
                    code="CODEX_GATEWAY_ACTION_FAILED",
                    severity="warning",
                    message="Codex Gateway HTTP is up, but the last action execution failed.",
                    details="Check Codex CLI authentication before relying on Voyager action generation.",
                    service_ids=("codex_gateway",),
                )
            )
    return diagnostics


def _summary(overall_state: str, diagnostics: list[Diagnostic]) -> str:
    if diagnostics:
        return diagnostics[0].message
    if overall_state == "up":
        return "All runtime services are ready."
    if overall_state == "degraded":
        return "Some optional runtime services are limited."
    return "One or more required runtime services are not responding."


def apply_runtime_health_overrides(
    health: dict[str, Any],
    overrides: dict[str, dict[str, Any]],
    *,
    manifest: ServiceManifest | None = None,
    now_ts: float | None = None,
) -> dict[str, Any]:
    if not overrides:
        return health
    manifest = manifest or load_service_manifest()
    specs = service_spec_by_id(manifest)
    now_ts = time.time() if now_ts is None else now_ts
    next_health = dict(health)
    services = [dict(service) for service in next_health.get("services") or [] if isinstance(service, dict)]
    diagnostics = [dict(diagnostic) for diagnostic in next_health.get("diagnostics") or [] if isinstance(diagnostic, dict)]
    active_overrides: list[dict[str, Any]] = []

    for override in overrides.values():
        service_id = str(override.get("serviceId") or "").strip()
        if not service_id:
            continue
        expires_at = float(override.get("expiresAt") or 0)
        if expires_at and expires_at <= now_ts:
            continue
        spec = specs.get(service_id)
        if spec is None:
            continue
        state = str(override.get("state") or "down").lower()
        if state not in {"down", "partial", "unknown"}:
            state = "down"
        reason = str(override.get("reason") or "simulated_runtime_health_override")
        message = str(override.get("message") or f"{spec.label} is simulated as {state}.")
        active_overrides.append({"serviceId": service_id, "state": state, "reason": reason, "expiresAt": expires_at})

        row = next((service for service in services if service.get("id") == service_id), None)
        if row is None:
            row = {
                "id": spec.id,
                "label": spec.label,
                "required": spec.required,
                "host": spec.host,
                "port": spec.port,
                "checks": [],
            }
            services.append(row)
        row.update(
            {
                "state": state,
                "ready": False,
                "reason": reason,
                "simulated": True,
                "overrideExpiresAt": expires_at,
                "suggestedActions": list(_suggest_start_action(spec)),
            }
        )
        row.setdefault("checks", []).append({"kind": "override", "ok": False, "reason": reason})
        diagnostics.insert(
            0,
            Diagnostic(
                code=f"{service_id.upper()}_DOWN_SIMULATED",
                severity="error" if spec.required else "warning",
                message=message,
                details="This is a safe Control-Page health override. The real service was not stopped.",
                service_ids=(service_id,),
                suggested_actions=tuple(_suggest_start_action(spec)),
            ).to_dict(),
        )

    if not active_overrides:
        return health

    service_map = {str(service.get("id")): service for service in services}
    _annotate_functional_readiness(service_map)
    required_failed = any(
        bool(service.get("required")) and service.get("state") != "up"
        for service in services
    )
    optional_failed = any(
        not bool(service.get("required")) and service.get("state") != "up"
        for service in services
    )
    if required_failed:
        overall_state = "down"
    elif optional_failed or diagnostics:
        overall_state = "degraded"
    else:
        overall_state = "up"
    next_health["ok"] = not required_failed
    next_health["fullyHealthy"] = overall_state == "up"
    next_health["coreState"] = "down" if required_failed else "up"
    next_health["optionalDegraded"] = overall_state == "degraded"
    next_health["overallState"] = overall_state
    next_health["summary"] = str(diagnostics[0].get("message") if diagnostics else _summary(overall_state, []))
    next_health["services"] = services
    next_health["diagnostics"] = diagnostics
    next_health["legacyServices"] = legacy_services_from_health(service_map)
    next_health["simulatedOverrides"] = active_overrides
    return next_health


def _public_code(value: Any, *, fallback: str) -> str:
    text = str(value or "").strip().lower()
    return text if _PUBLIC_CODE_RE.fullmatch(text) else fallback


def _public_identifier(value: Any) -> str:
    return _public_code(value, fallback="")


def _public_number(value: Any) -> int | float | None:
    if isinstance(value, bool):
        return None
    if isinstance(value, (int, float)):
        return value if math.isfinite(float(value)) else None
    return None


def _public_count(value: Any) -> int:
    number = _public_number(value)
    if number is None:
        return 0
    return max(0, int(number))


def _public_legacy_services(value: Any) -> dict[str, Any]:
    if not isinstance(value, dict):
        return {}
    result: dict[str, Any] = {}
    readiness_keys = {
        *LEGACY_SERVICE_READY_KEYS.values(),
        "voyagerHttpReady",
        "voyagerRuntimeReady",
    }
    for key in readiness_keys:
        if isinstance(value.get(key), bool):
            result[key] = bool(value[key])
    if isinstance(value.get("codexRequired"), bool):
        result["codexRequired"] = bool(value["codexRequired"])
    if value.get("codexBackend") in {"local", "codex-gateway"}:
        result["codexBackend"] = value["codexBackend"]
    required_keys = {
        "botReady",
        "mainReady",
        "routerReady",
        "subReady",
        "ttsReady",
        "sttReady",
    }
    if required_keys.issubset(result):
        if not result["botReady"]:
            result["summary"] = (
                "Control-Page is open; Bot API is not ready."
            )
        elif all(
            result[key]
            for key in (
                "mainReady",
                "routerReady",
                "subReady",
                "ttsReady",
                "sttReady",
            )
        ):
            result["summary"] = (
                "Control-Page and Evelyn runtime are ready."
            )
        else:
            result["summary"] = (
                "Control-Page is open; model or voice services are still starting."
            )
    return result


def _public_commit_metrics(value: Any) -> dict[str, Any] | None:
    if (
        not isinstance(value, dict)
        or value.get("schema")
        != "conversation_continuity.commit-metrics.v1"
    ):
        return None
    result: dict[str, Any] = {
        "schema": "conversation_continuity.commit-metrics.v1",
        "state": _public_code(value.get("state"), fallback="unknown"),
        "attemptCount": _public_count(value.get("attemptCount")),
        "successCount": _public_count(value.get("successCount")),
        "failureCount": _public_count(value.get("failureCount")),
        "sampleCount": _public_count(value.get("sampleCount")),
        "lastSucceeded": (
            value.get("lastSucceeded")
            if isinstance(value.get("lastSucceeded"), bool)
            else None
        ),
        "warningCode": sanitize_runtime_error_code(
            value.get("warningCode"),
            fallback="",
        ),
    }
    for key in (
        "lastMs",
        "p50Ms",
        "p95Ms",
        "maxMs",
        "lastAt",
        "warningThresholdMs",
    ):
        result[key] = _public_number(value.get(key))
    return result


def _public_error_source(
    source_id: str,
    value: Any,
) -> dict[str, Any] | None:
    if source_id not in _PUBLIC_ERROR_SOURCES or not isinstance(value, dict):
        return None
    result: dict[str, Any] = {
        "id": source_id,
        "label": _PUBLIC_ERROR_SOURCES[source_id],
        "state": _public_code(value.get("state"), fallback="unknown"),
        "available": bool(value.get("available")),
        "stale": bool(value.get("stale")),
        "heartbeatAt": _public_number(value.get("heartbeatAt")),
        "errorCount": _public_count(value.get("errorCount")),
        "lastErrorAt": _public_number(value.get("lastErrorAt")),
        "lastErrorCode": sanitize_runtime_error_code(
            value.get("lastErrorCode"),
            fallback="",
        ),
        "lastErrorType": sanitize_runtime_error_type(
            value.get("lastErrorType")
        ),
        "errorCounters": {},
        "hasCurrentError": bool(value.get("hasCurrentError")),
    }
    counters = value.get("errorCounters")
    if isinstance(counters, dict):
        for raw_code, raw_count in list(counters.items())[:64]:
            code = sanitize_runtime_error_code(raw_code, fallback="")
            if code:
                result["errorCounters"][code] = _public_count(raw_count)
    commit_metrics = _public_commit_metrics(
        value.get("completedTurnCommit")
    )
    if commit_metrics is not None:
        result["completedTurnCommit"] = commit_metrics
    return result


def _public_error_observability(value: Any) -> dict[str, Any]:
    if not isinstance(value, dict):
        return {}
    errors = value.get("exceptions")
    if not isinstance(errors, dict):
        return {}
    raw_summary = errors.get("summary")
    summary = {
        key: _public_count(raw_summary.get(key))
        for key in (
            "sourceCount",
            "availableCount",
            "staleCount",
            "currentErrorCount",
            "recentErrorCount",
            "totalCount",
        )
    } if isinstance(raw_summary, dict) else {}
    raw_sources = errors.get("sources")
    sources = {
        source_id: source
        for source_id, item in (
            raw_sources.items()
            if isinstance(raw_sources, dict)
            else ()
        )
        if (
            source := _public_error_source(str(source_id), item)
        ) is not None
    }
    recent_errors: list[dict[str, Any]] = []
    for item in errors.get("recentErrors") or []:
        if not isinstance(item, dict):
            continue
        source_id = str(item.get("source") or "")
        if source_id not in _PUBLIC_ERROR_SOURCES:
            continue
        recent_errors.append(
            {
                "source": source_id,
                "at": _public_number(item.get("at")),
                "code": sanitize_runtime_error_code(
                    item.get("code"),
                    fallback="runtime_error",
                ),
                "type": sanitize_runtime_error_type(item.get("type")),
            }
        )
        if len(recent_errors) >= 10:
            break
    warnings: list[dict[str, str]] = []
    for item in errors.get("warnings") or []:
        if not isinstance(item, dict):
            continue
        source_id = str(item.get("source") or "")
        code = sanitize_runtime_error_code(item.get("code"), fallback="")
        if source_id in _PUBLIC_ERROR_SOURCES and code:
            warnings.append({"source": source_id, "code": code})
    return {
        "exceptions": {
            "schema": "runtime_errors.summary.v1",
            "state": _public_code(
                errors.get("state"),
                fallback="unknown",
            ),
            "generatedAt": _public_number(errors.get("generatedAt")),
            "recentAfterSec": _public_number(
                errors.get("recentAfterSec")
            ),
            "summary": summary,
            "sources": sources,
            "recentErrors": recent_errors,
            "warnings": warnings,
            "privacy": {
                "exceptionMessages": False,
                "stackTraces": False,
                "filesystemPaths": False,
            },
        }
    }


def _public_action(value: Any) -> dict[str, Any] | None:
    if not isinstance(value, dict):
        return None
    action_id = _public_identifier(
        value.get("actionId") or value.get("id")
    )
    if not action_id:
        return None
    result: dict[str, Any] = {
        "id": action_id,
        "label": str(value.get("label") or action_id)[:160],
        "risk": _public_code(
            value.get("risk"),
            fallback="medium",
        ),
        "requiresConfirm": bool(value.get("requiresConfirm", True)),
    }
    service_id = _public_identifier(value.get("serviceId"))
    if service_id:
        result["serviceId"] = service_id
    strategy = _public_identifier(value.get("strategy"))
    if strategy:
        result["strategy"] = strategy
    return result


def _public_probe_check(value: Any) -> dict[str, Any] | None:
    if not isinstance(value, dict):
        return None
    result: dict[str, Any] = {
        "kind": _public_code(
            value.get("kind"),
            fallback="unknown",
        ),
        "ok": bool(value.get("ok")),
        "reason": _public_code(
            value.get("reason"),
            fallback="probe_failed",
        ),
    }
    for source_key, public_key in (
        ("status", "status"),
        ("elapsedMs", "elapsedMs"),
        ("ageSec", "ageSec"),
        ("staleAfterSec", "staleAfterSec"),
    ):
        number = _public_number(value.get(source_key))
        if number is not None:
            result[public_key] = number
    return result


def _public_functional_readiness(value: Any) -> dict[str, Any] | None:
    if not isinstance(value, dict):
        return None
    dependencies = value.get("dependencies")
    task_contract = value.get("taskContract")
    result: dict[str, Any] = {
        "schema": _public_code(
            value.get("schema"),
            fallback="minecraft_autonomy_readiness",
        ),
        "state": _public_code(
            value.get("state"),
            fallback="unknown",
        ),
        "ready": bool(value.get("ready")),
        "blockers": [
            code
            for item in (value.get("blockers") or [])
            if (code := _public_identifier(item))
        ][:24],
        "contentFree": bool(value.get("contentFree")),
    }
    if isinstance(dependencies, dict):
        result["dependencies"] = {
            str(key): bool(item)
            for key, item in dependencies.items()
            if _PUBLIC_CODE_RE.fullmatch(str(key).strip().lower())
            and isinstance(item, bool)
        }
    if isinstance(task_contract, dict):
        result["taskContract"] = {
            str(key): normalized
            for key, item in task_contract.items()
            if _PUBLIC_CODE_RE.fullmatch(str(key).strip().lower())
            and (
                normalized := _public_code(
                    item,
                    fallback="",
                )
            )
        }
    return result


def _public_service(value: Any) -> dict[str, Any] | None:
    if not isinstance(value, dict):
        return None
    service_id = _public_identifier(value.get("id"))
    if not service_id:
        return None
    result: dict[str, Any] = {
        "id": service_id,
        "label": str(value.get("label") or service_id)[:160],
        "required": bool(value.get("required")),
        "state": _public_code(
            value.get("state"),
            fallback="unknown",
        ),
        "ready": bool(value.get("ready")),
        "reason": _public_code(
            value.get("reason"),
            fallback="probe_failed",
        ),
        "checks": [],
        "suggestedActions": [],
    }
    for key in ("port", "defaultPort", "checkedAt", "elapsedMs"):
        number = _public_number(value.get(key))
        if number is not None:
            result[key] = number
    cached_state = _public_identifier(value.get("cachedState"))
    if cached_state:
        result["cachedState"] = cached_state
    for key in ("httpReady", "runtimeReady", "simulated"):
        if isinstance(value.get(key), bool):
            result[key] = bool(value[key])
    contract_state = _public_identifier(
        value.get("readinessContractState")
    )
    if contract_state:
        result["readinessContractState"] = contract_state
    blockers = [
        code
        for item in (value.get("readinessBlockers") or [])
        if (code := _public_identifier(item))
    ][:24]
    if blockers:
        result["readinessBlockers"] = blockers
    functional = _public_functional_readiness(
        value.get("functionalReadiness")
    )
    if functional is not None:
        result["functionalReadiness"] = functional
    expires_at = _public_number(value.get("overrideExpiresAt"))
    if expires_at is not None:
        result["overrideExpiresAt"] = expires_at
    result["checks"] = [
        check
        for item in (value.get("checks") or [])
        if (check := _public_probe_check(item)) is not None
    ]
    result["suggestedActions"] = [
        action
        for item in (value.get("suggestedActions") or [])
        if (action := _public_action(item)) is not None
    ]
    return result


def _public_diagnostic(value: Any) -> dict[str, Any] | None:
    if not isinstance(value, dict):
        return None
    raw_code = str(value.get("code") or "").strip().upper()
    code = (
        raw_code
        if _PUBLIC_DIAGNOSTIC_RE.fullmatch(raw_code)
        else "RUNTIME_HEALTH_DIAGNOSTIC"
    )
    service_ids = [
        service_id
        for item in (value.get("serviceIds") or [])
        if (service_id := _public_identifier(item))
    ][:16]
    message = str(value.get("message") or code).replace(
        "\r",
        " ",
    ).replace("\n", " ").strip()[:300]
    if code.endswith("_DOWN_SIMULATED"):
        label = service_ids[0] if service_ids else "runtime service"
        message = f"{label} is simulated as unavailable by a local override."
    severity = _public_code(
        value.get("severity"),
        fallback="warning",
    )
    if severity not in {"info", "warning", "error"}:
        severity = "warning"
    return {
        "code": code,
        "severity": severity,
        "message": message,
        "details": "",
        "serviceIds": service_ids,
        "suggestedActions": [
            action
            for item in (value.get("suggestedActions") or [])
            if (action := _public_action(item)) is not None
        ],
    }


def _public_capability_item(value: Any) -> dict[str, Any] | None:
    if not isinstance(value, dict):
        return None
    state = _public_code(value.get("state"), fallback="unknown")
    if state not in _PUBLIC_CAPABILITY_STATES:
        state = "unknown"

    def public_issue(item: Any) -> dict[str, Any] | None:
        if not isinstance(item, dict):
            return None
        code = _public_identifier(item.get("code"))
        if not code:
            return None
        service_id = _public_identifier(item.get("serviceId"))
        result = {
            "code": code,
            "message": str(item.get("message") or code).replace(
                "\r",
                " ",
            ).replace("\n", " ").strip()[:240],
        }
        if service_id:
            result["serviceId"] = service_id
        return result

    dependencies: list[dict[str, Any]] = []
    for item in value.get("dependencies") or []:
        if not isinstance(item, dict):
            continue
        service_id = _public_identifier(item.get("id"))
        if not service_id:
            continue
        dependencies.append(
            {
                "id": service_id,
                "label": str(item.get("label") or service_id)[:160],
                "state": _public_code(
                    item.get("state"),
                    fallback="unknown",
                ),
                "ready": bool(item.get("ready")),
                "reason": _public_code(
                    item.get("reason"),
                    fallback="missing",
                ),
                "checkedAt": _public_number(item.get("checkedAt")),
            }
        )
    repairs: list[dict[str, Any]] = []
    for item in value.get("repairActions") or []:
        action = _public_action(item)
        if action is None:
            continue
        action["actionId"] = action.pop("id")
        if (
            item.get("manualCommand")
            == "start_local.bat --background"
        ):
            action["manualCommand"] = (
                "start_local.bat --background"
            )
        repairs.append(action)
    return {
        "state": state,
        "ready": bool(value.get("ready")),
        "blockers": [
            issue
            for item in (value.get("blockers") or [])
            if (issue := public_issue(item)) is not None
        ],
        "warnings": [
            issue
            for item in (value.get("warnings") or [])
            if (issue := public_issue(item)) is not None
        ],
        "dependencies": dependencies,
        "repairActions": repairs,
    }


def public_runtime_health_snapshot(
    health: dict[str, Any] | None,
) -> dict[str, Any]:
    """Return the browser-safe runtime-health projection.

    Probe payloads remain available inside the collector for readiness,
    capability, and repair decisions. They are not part of the public API.
    """

    source = health if isinstance(health, dict) else {}
    legacy_services = _public_legacy_services(
        source.get("legacyServices")
    )
    required_legacy_keys = (
        "botReady",
        "mainReady",
        "routerReady",
        "subReady",
        "ttsReady",
        "sttReady",
    )
    inferred_ok = bool(
        all(key in legacy_services for key in required_legacy_keys)
        and all(
            legacy_services.get(key) is True
            for key in required_legacy_keys
        )
    )
    public_ok = (
        bool(source.get("ok"))
        if isinstance(source.get("ok"), bool)
        else inferred_ok
    )
    optional_degraded = bool(source.get("optionalDegraded"))
    diagnostics = [
        diagnostic
        for item in (source.get("diagnostics") or [])
        if (diagnostic := _public_diagnostic(item)) is not None
    ]
    overall_state = _public_code(
        source.get("overallState"),
        fallback=(
            "degraded"
            if public_ok and optional_degraded
            else "up"
            if public_ok
            else "unknown"
        ),
    )
    result: dict[str, Any] = {
        "schema": PUBLIC_RUNTIME_HEALTH_SCHEMA,
        "ok": public_ok,
        "fullyHealthy": (
            bool(source.get("fullyHealthy"))
            if isinstance(source.get("fullyHealthy"), bool)
            else public_ok and not optional_degraded
        ),
        "coreState": _public_code(
            source.get("coreState"),
            fallback="up" if public_ok else "unknown",
        ),
        "optionalDegraded": optional_degraded,
        "overallState": overall_state,
        "summary": (
            diagnostics[0]["message"]
            if diagnostics
            else _summary(overall_state, [])
        ),
        "manifestVersion": str(source.get("manifestVersion") or "")[:40],
        "runtimeName": str(source.get("runtimeName") or "")[:80],
        "checkedAt": _public_number(source.get("checkedAt")),
        "services": [
            service
            for item in (source.get("services") or [])
            if (service := _public_service(item)) is not None
        ],
        "diagnostics": diagnostics,
        "legacyServices": legacy_services,
        "observability": _public_error_observability(
            source.get("observability")
        ),
        "capabilities": {
            capability_id: capability
            for key, item in (
                source.get("capabilities") or {}
            ).items()
            if (
                capability_id := str(key)
            )
            in _PUBLIC_CAPABILITY_IDS
            and (
                capability := _public_capability_item(item)
            )
            is not None
        },
        "privacy": {
            "rawProbePayloads": False,
            "probeTargets": False,
            "filesystemPaths": False,
            "processIds": False,
            "deviceNames": False,
        },
    }
    revision = _public_number(source.get("revision"))
    if revision is not None:
        result["revision"] = revision
    cache = source.get("cache")
    if isinstance(cache, dict):
        result["cache"] = {
            "schema": str(cache.get("schema") or "")[:80],
            "ageSec": _public_number(cache.get("ageSec")),
            "stale": bool(cache.get("stale")),
            "refreshing": bool(cache.get("refreshing")),
            "refreshAfterSec": _public_number(
                cache.get("refreshAfterSec")
            ),
            "maxStaleSec": _public_number(cache.get("maxStaleSec")),
            "lastRefreshError": _public_code(
                cache.get("lastRefreshError"),
                fallback="",
            ),
        }
    overrides: list[dict[str, Any]] = []
    for item in source.get("simulatedOverrides") or []:
        if not isinstance(item, dict):
            continue
        service_id = _public_identifier(item.get("serviceId"))
        if not service_id:
            continue
        overrides.append(
            {
                "serviceId": service_id,
                "state": _public_code(
                    item.get("state"),
                    fallback="unknown",
                ),
                "reason": _public_code(
                    item.get("reason"),
                    fallback="operator_simulated_down",
                ),
                "expiresAt": _public_number(item.get("expiresAt")),
            }
        )
    if overrides:
        result["simulatedOverrides"] = overrides
    return result


async def collect_runtime_health(
    *,
    manifest: ServiceManifest | None = None,
    probe_runner: ProbeRunner | None = None,
) -> dict[str, Any]:
    manifest = manifest or load_service_manifest()
    active_services = tuple(
        service
        for service in manifest.services
        if service.id != "codex_gateway" or _codex_gateway_required()
    )
    checked = await asyncio.gather(
        *(check_service(service, probe_runner=probe_runner) for service in active_services),
        return_exceptions=True,
    )
    services: dict[str, dict[str, Any]] = {}
    for service, result in zip(active_services, checked):
        if isinstance(result, Exception):
            services[service.id] = {
                "id": service.id,
                "label": service.label,
                "required": service.required,
                "host": service.host,
                "port": service.port,
                "state": "unknown",
                "ready": False,
                "reason": type(result).__name__,
                "checkedAt": time.time(),
                "elapsedMs": 0,
                "checks": [],
                "suggestedActions": list(_suggest_start_action(service)),
            }
        else:
            services[service.id] = dict(result)
    _annotate_functional_readiness(services)
    diagnostics = _diagnose(services)
    required_failed = any(
        bool(service.get("required")) and service.get("state") != "up"
        for service in services.values()
    )
    optional_failed = any(
        not bool(service.get("required")) and service.get("state") != "up"
        for service in services.values()
    )
    if required_failed:
        overall_state = "down"
    elif optional_failed or diagnostics:
        overall_state = "degraded"
    else:
        overall_state = "up"
    runtime_errors = collect_runtime_error_observability(
        service_health=services,
    )
    return attach_voice_capabilities({
        "ok": not required_failed,
        "fullyHealthy": overall_state == "up",
        "coreState": "down" if required_failed else "up",
        "optionalDegraded": overall_state == "degraded",
        "overallState": overall_state,
        "summary": _summary(overall_state, diagnostics),
        "manifestVersion": manifest.schema_version,
        "runtimeName": manifest.runtime_name,
        "checkedAt": time.time(),
        "services": list(services.values()),
        "diagnostics": [diagnostic.to_dict() for diagnostic in diagnostics],
        "legacyServices": legacy_services_from_health(services),
        "observability": {
            "exceptions": runtime_errors,
        },
    })


def _codex_gateway_required() -> bool:
    return (
        str(os.environ.get("VOYAGER_ACTION_BACKEND") or "local").strip().lower() == "codex-gateway"
        or str(os.environ.get("MINDCRAFT_CODEX_ENABLED") or "").strip().lower()
        in {"1", "true", "yes", "on"}
    )


def legacy_services_from_health(services: dict[str, dict[str, Any]]) -> dict[str, Any]:
    legacy: dict[str, Any] = {}
    for service_id, key in LEGACY_SERVICE_READY_KEYS.items():
        if service_id in services:
            service = services[service_id]
            if service_id == "voyager":
                http_ready = service.get("state") == "up"
                runtime_ready = bool(service.get("runtimeReady", http_ready))
                legacy["voyagerHttpReady"] = http_ready
                legacy["voyagerRuntimeReady"] = runtime_ready
                legacy[key] = runtime_ready
            else:
                legacy[key] = service.get("state") == "up"
    legacy["codexRequired"] = _codex_gateway_required()
    legacy["codexBackend"] = "codex-gateway" if legacy["codexRequired"] else "local"
    required_keys = {"botReady", "mainReady", "routerReady", "subReady", "ttsReady", "sttReady"}
    if required_keys.issubset(legacy):
        if not legacy.get("botReady"):
            legacy["summary"] = "Control-Page is open; Bot API is not ready."
        elif (
            legacy.get("mainReady")
            and legacy.get("routerReady")
            and legacy.get("subReady")
            and legacy.get("ttsReady")
            and legacy.get("sttReady")
        ):
            legacy["summary"] = "Control-Page and Evelyn runtime are ready."
        else:
            legacy["summary"] = "Control-Page is open; model or voice services are still starting."
    return legacy
