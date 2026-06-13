from __future__ import annotations

import json
import os
import subprocess
import time
from datetime import datetime, timezone
from hashlib import sha256
from pathlib import Path
from typing import Any, Callable

from .paths import get_runtime_artifacts_root
from .runtime_services import RUNTIME_ROOT, ServiceManifest, ServiceSpec, get_service, load_service_manifest


PROJECT_ROOT = RUNTIME_ROOT.parents[1]
CORE_ROOT = RUNTIME_ROOT.parent
ALLOWED_LAUNCHER_ROOTS = (PROJECT_ROOT, CORE_ROOT, RUNTIME_ROOT)
DEFAULT_REPAIR_LOG_PATH = get_runtime_artifacts_root() / "runtime_health" / "repair_log.jsonl"
RepairRunner = Callable[[list[str], str], dict[str, Any]]

REPAIR_PRIORITY_SERVICE_IDS = ("main_llm", "router_llm", "sub_llm", "tts", "bot_api", "control_page")
BLOCKING_SERVICE_STATES = {"down", "partial", "unknown"}


def service_id_from_action(action_id: str) -> str:
    action = str(action_id or "").strip()
    if action.startswith("start_"):
        return action.removeprefix("start_")
    return action


def resolve_launcher_path(service: ServiceSpec) -> Path | None:
    if not service.launcher:
        return None
    launcher = Path(service.launcher)
    if not launcher.is_absolute():
        launcher = RUNTIME_ROOT / launcher
    return launcher.resolve()


def launcher_is_inside_allowed_root(path: Path) -> bool:
    resolved = path.resolve()
    for root in ALLOWED_LAUNCHER_ROOTS:
        try:
            resolved.relative_to(root.resolve())
            return True
        except ValueError:
            continue
    return False


def command_preview_for_launcher(path: Path) -> list[str]:
    suffix = path.suffix.lower()
    if suffix == ".ps1":
        return [
            "powershell.exe",
            "-NoLogo",
            "-NoProfile",
            "-ExecutionPolicy",
            "Bypass",
            "-File",
            str(path),
        ]
    if suffix in {".bat", ".cmd"}:
        return ["cmd.exe", "/c", str(path)]
    if suffix == ".sh":
        return ["bash", str(path)]
    return [str(path)]


def confirm_token_for_plan(plan: dict[str, Any]) -> str:
    material = "|".join(
        [
            str(plan.get("serviceId") or ""),
            str(plan.get("actionId") or ""),
            str(plan.get("launcherPath") or ""),
            str(plan.get("workingDirectory") or ""),
        ]
    )
    digest = sha256(material.encode("utf-8")).hexdigest()[:16]
    return f"confirm-{digest}"


def service_health_from_summary(health: dict[str, Any] | None, service_id: str) -> dict[str, Any] | None:
    if not isinstance(health, dict):
        return None
    services = health.get("services")
    if not isinstance(services, list):
        return None
    for item in services:
        if isinstance(item, dict) and item.get("id") == service_id:
            return item
    return None


def _service_health_rows(health: dict[str, Any] | None) -> list[dict[str, Any]]:
    services = health.get("services") if isinstance(health, dict) else None
    if not isinstance(services, list):
        return []
    return [item for item in services if isinstance(item, dict)]


def _service_health_map(health: dict[str, Any] | None) -> dict[str, dict[str, Any]]:
    rows = _service_health_rows(health)
    return {str(item.get("id")).strip(): item for item in rows if str(item.get("id") or "").strip()}


def _suggested_action_id(service_row: dict[str, Any] | None) -> str:
    service_id = str((service_row or {}).get("id") or "").strip()
    if not service_id:
        return ""
    actions = (service_row or {}).get("suggestedActions")
    if isinstance(actions, list):
        for action in actions:
            if not isinstance(action, dict):
                continue
            action_id = str(action.get("id") or "").strip()
            if action_id:
                return action_id
    return f"start_{service_id}"


def _repair_context_from_health(health: dict[str, Any] | None) -> dict[str, Any]:
    rows = _service_health_rows(health)
    row_by_id = {str(row.get("id")).strip(): row for row in rows if str(row.get("id") or "").strip()}
    seen = set()

    blocking_services: list[dict[str, Any]] = []
    recommended_order: list[dict[str, Any]] = []

    def add_service(service_id: str) -> None:
        service = row_by_id.get(service_id)
        if not service or service_id in seen:
            return
        seen.add(service_id)
        if str(service.get("state") or "").lower() not in BLOCKING_SERVICE_STATES:
            return
        if not bool(service.get("required")):
            return
        action_id = _suggested_action_id(service)
        if not action_id:
            return
        item = {
            "serviceId": service_id,
            "label": str(service.get("label") or service_id),
            "state": str(service.get("state") or "").lower(),
            "required": True,
            "actionId": action_id,
        }
        blocking_services.append(item)
        recommended_order.append(item)

    for service_id in REPAIR_PRIORITY_SERVICE_IDS:
        add_service(service_id)

    for service in rows:
        service_id = str(service.get("id") or "").strip()
        if service_id:
            add_service(service_id)

    return {
        "runtimeHealthSummary": str((health or {}).get("summary") or ""),
        "blockingServices": blocking_services,
        "recommendedOrder": recommended_order,
    }


def append_repair_event(event: dict[str, Any], *, log_path: Path | None = None) -> dict[str, Any]:
    path = log_path or DEFAULT_REPAIR_LOG_PATH
    row = {
        "at": datetime.now(timezone.utc).isoformat(),
        **dict(event or {}),
    }
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8", newline="\n") as handle:
        handle.write(json.dumps(row, ensure_ascii=False, sort_keys=True) + "\n")
    return {"ok": True, "logPath": str(path), "event": row}


def read_recent_repair_events(*, log_path: Path | None = None, limit: int = 200) -> list[dict[str, Any]]:
    path = log_path or DEFAULT_REPAIR_LOG_PATH
    if not path.exists():
        return []
    rows: list[dict[str, Any]] = []
    for line in path.read_text(encoding="utf-8").splitlines()[-max(1, limit) :]:
        try:
            item = json.loads(line)
        except Exception:
            continue
        if isinstance(item, dict):
            rows.append(item)
    return rows


def parse_event_timestamp(value: Any) -> float | None:
    try:
        parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except Exception:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.timestamp()


def cooldown_block_reason(plan: dict[str, Any], *, now_ts: float | None = None, log_path: Path | None = None) -> dict[str, Any] | None:
    cooldown_sec = max(0, int(plan.get("cooldownSec") or 0))
    if cooldown_sec <= 0:
        return None
    now_ts = time.time() if now_ts is None else now_ts
    for event in reversed(read_recent_repair_events(log_path=log_path)):
        if event.get("serviceId") != plan.get("serviceId") or event.get("actionId") != plan.get("actionId"):
            continue
        if event.get("event") not in {"apply_started", "apply_succeeded"}:
            continue
        event_ts = parse_event_timestamp(event.get("at"))
        if event_ts is None:
            continue
        elapsed = now_ts - event_ts
        if elapsed < cooldown_sec:
            return {
                "error": "repair_cooldown_active",
                "cooldownSec": cooldown_sec,
                "retryAfterSec": max(1, int(round(cooldown_sec - elapsed))),
                "lastEventAt": event.get("at"),
            }
    return None


def runtime_repair_capabilities(
    *,
    manifest: ServiceManifest | None = None,
    health: dict[str, Any] | None = None,
) -> dict[str, Any]:
    manifest = manifest or load_service_manifest()
    services = []
    for service in manifest.services:
        repair = service.repair
        if not repair or not repair.allowed:
            continue
        service_health = service_health_from_summary(health, service.id)
        services.append(
            {
                "id": service.id,
                "label": service.label,
                "required": service.required,
                "state": str((service_health or {}).get("state") or "unknown"),
                "actionId": f"start_{service.id}",
                "strategy": repair.strategy,
                "requiresConfirm": repair.requires_confirm,
                "cooldownSec": repair.cooldown_sec,
                "dryRunOnly": False,
                "executionSupported": True,
            }
        )
    return {
        "ok": True,
        "dryRunOnly": False,
        "executionSupported": True,
        "runtimeName": manifest.runtime_name,
        "manifestVersion": manifest.schema_version,
        "repairableServices": services,
    }


def build_runtime_repair_plan(
    *,
    service_id: str | None = None,
    action_id: str | None = None,
    manifest: ServiceManifest | None = None,
    health: dict[str, Any] | None = None,
) -> dict[str, Any]:
    manifest = manifest or load_service_manifest()
    normalized_service_id = str(service_id or "").strip() or service_id_from_action(str(action_id or ""))
    action = str(action_id or f"start_{normalized_service_id}").strip()
    if not normalized_service_id:
        return {
            "ok": False,
            "dryRun": True,
            "error": "service_id_required",
            "message": "serviceId or actionId is required.",
        }

    service = get_service(manifest, normalized_service_id)
    if service is None:
        return {
            "ok": False,
            "dryRun": True,
            "error": "unknown_service",
            "serviceId": normalized_service_id,
            "actionId": action,
            "message": f"Unknown runtime service: {normalized_service_id}",
        }

    if action not in {"start", f"start_{service.id}"}:
        return {
            "ok": False,
            "dryRun": True,
            "eligible": False,
            "error": "unsupported_repair_action",
            "serviceId": service.id,
            "label": service.label,
            "actionId": action,
            "message": f"Unsupported repair action for {service.label}: {action}",
        }

    repair = service.repair
    repair_context = _repair_context_from_health(health)
    service_health = service_health_from_summary(health, service.id)
    current_state = str((service_health or {}).get("state") or "unknown")
    base = {
        "dryRun": True,
        "dryRunOnly": True,
        "serviceId": service.id,
        "label": service.label,
        "actionId": action,
        "currentState": current_state,
        "required": service.required,
        "risk": "medium",
        "cooldownOk": True,
        "strategy": repair.strategy if repair else "none",
        "requiresConfirm": repair.requires_confirm if repair else True,
        "cooldownSec": repair.cooldown_sec if repair else 60,
        "runtimeHealthSummary": repair_context["runtimeHealthSummary"],
        "blockingServices": repair_context["blockingServices"],
        "recommendedOrder": repair_context["recommendedOrder"],
    }

    if not repair or not repair.allowed:
        return {
            **base,
            "ok": False,
            "eligible": False,
            "error": "repair_not_allowed",
            "message": f"Repair is not allowed for {service.label}.",
        }
    if repair.strategy != "start_if_down":
        return {
            **base,
            "ok": False,
            "eligible": False,
            "error": "unsupported_repair_strategy",
            "message": f"Unsupported repair strategy: {repair.strategy}",
        }
    if bool((service_health or {}).get("simulated")):
        return {
            **base,
            "ok": True,
            "eligible": False,
            "planStatus": "simulated_only",
            "message": f"{service.label} is only simulated as down. No repair launcher will be started.",
            "safety": {
                "willExecute": False,
                "simulated": True,
                "reason": "safe health override does not mutate real services",
            },
        }
    if current_state == "up":
        return {
            **base,
            "ok": True,
            "eligible": False,
            "planStatus": "not_needed",
            "message": f"{service.label} is already up.",
        }

    launcher_path = resolve_launcher_path(service)
    if launcher_path is None:
        return {
            **base,
            "ok": False,
            "eligible": False,
            "error": "launcher_not_configured",
            "message": f"No launcher is configured for {service.label}.",
        }
    if not launcher_is_inside_allowed_root(launcher_path):
        return {
            **base,
            "ok": False,
            "eligible": False,
            "error": "launcher_outside_allowed_roots",
            "launcherPath": str(launcher_path),
            "message": "Launcher path is outside the Evelyn project boundary.",
        }
    if not launcher_path.exists():
        return {
            **base,
            "ok": False,
            "eligible": False,
            "error": "launcher_not_found",
            "launcherPath": str(launcher_path),
            "message": f"Launcher does not exist: {launcher_path}",
        }

    command_preview = command_preview_for_launcher(launcher_path)
    plan = {
        **base,
        "ok": True,
        "eligible": True,
        "planStatus": "ready",
        "launcherPath": str(launcher_path),
        "workingDirectory": str(PROJECT_ROOT),
        "commandPreview": command_preview,
        "commandText": " ".join(command_preview),
        "preconditions": [
            "repair is allowed by service_manifest.json",
            "launcher path is inside the Evelyn project boundary",
            "launcher file exists",
            "service is not currently up",
        ],
        "riskChecks": [
            {"id": "preview_only", "ok": True, "message": "The preview endpoint does not execute commands."},
            {"id": "manual_confirm_required", "ok": bool(repair.requires_confirm), "message": "Execution phase will require manual confirmation."},
            {"id": "launcher_boundary", "ok": True, "message": "Launcher path resolved inside an allowed Evelyn root."},
        ],
        "inferredSideEffects": [
            f"would start {service.label}",
            f"would bind or reuse port {service.port}",
        ],
        "message": f"Dry-run only: {service.label} would be started with its configured launcher.",
        "safety": {
            "willExecute": False,
            "requiresManualConfirm": bool(repair.requires_confirm),
            "boundary": "launcher path must stay inside Evelyn project roots",
        },
    }
    plan["confirmToken"] = confirm_token_for_plan(plan)
    plan["confirmInstruction"] = "Send this confirmToken to /api/control-page/runtime-repair/apply to start the service."
    return plan


def start_visible_process(command: list[str], cwd: str) -> dict[str, Any]:
    creationflags = 0
    if os.name == "nt" and hasattr(subprocess, "CREATE_NEW_CONSOLE"):
        creationflags = subprocess.CREATE_NEW_CONSOLE
    process = subprocess.Popen(
        command,
        cwd=cwd,
        close_fds=True,
        creationflags=creationflags,
    )
    return {"pid": process.pid}


def execute_runtime_repair_plan(
    *,
    plan: dict[str, Any],
    confirm_token: str | None,
    reason: str | None = None,
    runner: RepairRunner | None = None,
    log_path: Path | None = None,
    now_ts: float | None = None,
) -> dict[str, Any]:
    if not plan.get("ok") or not plan.get("eligible") or plan.get("planStatus") != "ready":
        response = {
            "ok": False,
            "error": "repair_plan_not_executable",
            "serviceId": plan.get("serviceId"),
            "actionId": plan.get("actionId"),
            "planStatus": plan.get("planStatus"),
            "message": "Repair plan is not executable.",
            "safety": {"willExecute": False},
        }
        append_repair_event({"event": "apply_rejected", **response, "reason": str(reason or "")}, log_path=log_path)
        return response

    expected_token = str(plan.get("confirmToken") or confirm_token_for_plan(plan))
    if str(confirm_token or "") != expected_token:
        response = {
            "ok": False,
            "error": "confirm_token_required",
            "serviceId": plan.get("serviceId"),
            "actionId": plan.get("actionId"),
            "message": "A matching confirmToken from the preview response is required.",
            "safety": {"willExecute": False},
        }
        append_repair_event({"event": "apply_rejected", **response, "reason": str(reason or "")}, log_path=log_path)
        return response

    cooldown = cooldown_block_reason(plan, now_ts=now_ts, log_path=log_path)
    if cooldown is not None:
        response = {
            "ok": False,
            "serviceId": plan.get("serviceId"),
            "actionId": plan.get("actionId"),
            "message": "Repair action is still in cooldown.",
            "safety": {"willExecute": False},
            **cooldown,
        }
        append_repair_event({"event": "apply_rejected", **response, "reason": str(reason or "")}, log_path=log_path)
        return response

    command = list(plan.get("commandPreview") or [])
    cwd = str(plan.get("workingDirectory") or PROJECT_ROOT)
    if not command:
        response = {
            "ok": False,
            "error": "repair_command_missing",
            "serviceId": plan.get("serviceId"),
            "actionId": plan.get("actionId"),
            "message": "Repair command is missing from the plan.",
            "safety": {"willExecute": False},
        }
        append_repair_event({"event": "apply_rejected", **response, "reason": str(reason or "")}, log_path=log_path)
        return response

    append_repair_event(
        {
            "event": "apply_started",
            "serviceId": plan.get("serviceId"),
            "actionId": plan.get("actionId"),
            "reason": str(reason or ""),
            "commandPreview": command,
            "workingDirectory": cwd,
        },
        log_path=log_path,
    )
    try:
        runner_result = (runner or start_visible_process)(command, cwd)
    except Exception as exc:
        response = {
            "ok": False,
            "error": "repair_launch_failed",
            "serviceId": plan.get("serviceId"),
            "actionId": plan.get("actionId"),
            "message": f"Repair launcher failed: {exc}",
            "safety": {"willExecute": True, "launchAttempted": True},
        }
        append_repair_event({"event": "apply_failed", **response, "reason": str(reason or "")}, log_path=log_path)
        return response

    response = {
        "ok": True,
        "status": "started",
        "serviceId": plan.get("serviceId"),
        "label": plan.get("label"),
        "actionId": plan.get("actionId"),
        "message": f"Started repair launcher for {plan.get('label') or plan.get('serviceId')}.",
        "launcherPath": plan.get("launcherPath"),
        "workingDirectory": cwd,
        "commandPreview": command,
        "runner": runner_result,
        "safety": {
            "willExecute": True,
            "visibleLaunch": os.name == "nt",
            "mode": "start_if_down",
        },
    }
    append_repair_event({"event": "apply_succeeded", **response, "reason": str(reason or "")}, log_path=log_path)
    return response
