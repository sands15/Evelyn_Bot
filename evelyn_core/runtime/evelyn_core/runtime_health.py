from __future__ import annotations

import asyncio
import contextlib
import time
from dataclasses import dataclass
from typing import Any, Awaitable, Callable

import aiohttp

from .runtime_services import HealthProbeSpec, ServiceManifest, ServiceSpec, load_service_manifest


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


async def default_probe_runner(service: ServiceSpec, check: HealthProbeSpec) -> dict[str, Any]:
    if check.kind == "tcp":
        return await _probe_tcp(service, check)
    if check.kind == "http":
        return await _probe_http(service, check)
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
    runtime_ready = http_ready
    if http_ready:
        status_payload = _first_http_payload(voyager, path_suffix="/status") or _first_http_payload(voyager)
        recovery_state = status_payload.get("recovery_state") if isinstance(status_payload, dict) else None
        if (
            isinstance(recovery_state, dict)
            and str(recovery_state.get("scope") or "").strip().lower() == "runtime"
            and recovery_state.get("healthy") is False
        ):
            runtime_ready = False

    voyager["httpReady"] = http_ready
    voyager["runtimeReady"] = runtime_ready
    if http_ready and not runtime_ready:
        voyager["ready"] = False
        voyager["reason"] = "runtime_recovery_required"


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
        result = await runner(service, check)
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


async def collect_runtime_health(
    *,
    manifest: ServiceManifest | None = None,
    probe_runner: ProbeRunner | None = None,
) -> dict[str, Any]:
    manifest = manifest or load_service_manifest()
    checked = await asyncio.gather(
        *(check_service(service, probe_runner=probe_runner) for service in manifest.services),
        return_exceptions=True,
    )
    services: dict[str, dict[str, Any]] = {}
    for service, result in zip(manifest.services, checked):
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
    return {
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
    }


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
    legacy["codexRequired"] = "codex_gateway" in services
    legacy["codexBackend"] = "codex-gateway"
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
