from __future__ import annotations

import argparse
import asyncio
import ctypes
import json
import os
import re
import secrets
import select
import signal
import subprocess
import threading
import time
from collections import OrderedDict
from copy import deepcopy
from pathlib import Path
from typing import Any

from aiohttp import web

from .minecraft_owner_lock import (
    MinecraftOwnerLock,
    MinecraftOwnerLockBusy,
    MinecraftOwnerLockUnavailable,
)
from .paths import get_runtime_artifacts_root
from .minecraft_world_lease_contract import (
    load_guarded_world_lease,
    validate_world_lease_request,
)
from .minecraft_autonomy_readiness import (
    MINECRAFT_AUTONOMY_READINESS_SCHEMA,
    MINECRAFT_READINESS_BLOCKERS,
    MINECRAFT_READINESS_DEPENDENCIES,
    MINDCRAFT_TASK_CONTRACT_SCHEMA,
    expected_readiness_state,
    validate_minecraft_autonomy_readiness,
)
from .minecraft_action_contract import (
    MINECRAFT_ACTION_DISPATCH_SCHEMA,
    MINECRAFT_ACTION_REQUEST_SCHEMA,
    MINECRAFT_ACTION_RESULT_SCHEMA,
    MINECRAFT_ACTION_SPECS,
    MinecraftActionContractError,
    validate_minecraft_action_request,
)
from .mindcraft_world_effect import (
    ArchiveReadiness,
    ArchiveSink,
    MINDCRAFT_WORLD_EFFECT_BINDING_SCHEMA,
    MINDCRAFT_WORLD_EFFECT_EVENT_SCHEMA,
    MindcraftWorldEffectProjector,
)
from .mindcraft_conversation_archive_runtime import (
    MindcraftConversationArchiveClient,
)
from .runtime_config_schema import (
    MINDCRAFT_SERVICE_SETTINGS,
    load_runtime_settings,
)
from .runtime_error_observability import RuntimeErrorCounter
from .runtime_artifact_io import atomic_json_write


DEFAULT_GOAL = (
    "Defeat the Ender Dragon as a normal non-operator survival player. Progress safely through "
    "food, shelter, basic tools, iron or diamond gear, Nether access, blaze rods, Ender Pearls, "
    "Eyes of Ender, the stronghold, End preparation, and the dragon fight. Verify each milestone "
    "from actual inventory and world outcomes, preserve life, and recover lost prerequisites after "
    "death. Never use slash commands, cheats, creative mode, teleports, item grants, or operator "
    "privileges; when blocked, observe and take a normal-player detour instead of repeating."
)
RUNTIME_ARTIFACTS_ROOT = get_runtime_artifacts_root()
WORLD_LEASE_STATUS_PATH = (
    RUNTIME_ARTIFACTS_ROOT
    / "minecraft_world_lease"
    / "status.json"
)
WORLD_LEASE_OWNER_CLAIM_PATH = (
    RUNTIME_ARTIFACTS_ROOT
    / "minecraft_world_lease"
    / "owner_claim.json"
)
WORLD_ACTION_LOCK_PATH = (
    RUNTIME_ARTIFACTS_ROOT
    / "minecraft_world_lease"
    / "world_action.lock"
)
WORLD_LEASE_SECRET_PATH = (
    RUNTIME_ARTIFACTS_ROOT
    / "secrets"
    / "minecraft_world_lease.json"
)
WORLD_LEASE_GUARD_INTERVAL_SEC = 5.0
ACTION_GUARD_INTERVAL_SEC = 0.25
ACTION_TIMEOUT_SEC = 180.0
ACTION_RECORD_LIMIT = 20
WORLD_EFFECT_STATUS_PATH = (
    RUNTIME_ARTIFACTS_ROOT
    / "mindcraft_world_effect"
    / "status.json"
)
WORLD_EFFECT_EVENTS_DIR = (
    RUNTIME_ARTIFACTS_ROOT
    / "mindcraft_world_effect"
    / "events"
)
ACTION_GATEWAY_STATUS_PATH = (
    RUNTIME_ARTIFACTS_ROOT
    / "mindcraft_action_gateway"
    / "status.json"
)
ACTION_GATEWAY_STATUS_SCHEMA = (
    "mindcraft_action_gateway.status.v1"
)
MINDCRAFT_PROCESS_IDENTITY_PATH = (
    RUNTIME_ARTIFACTS_ROOT
    / "mindcraft"
    / "process_identity.json"
)
MINDCRAFT_PROCESS_IDENTITY_SCHEMA = (
    "mindcraft_runtime.process-identity.v1"
)
MINDCRAFT_CHILD_RUNTIME_PATH = (
    RUNTIME_ARTIFACTS_ROOT
    / "mindcraft"
    / "child_runtime.json"
)
MINDCRAFT_CHILD_RUNTIME_SCHEMA = "mindcraft_runtime.child-events.v1"
_MINDCRAFT_CHILD_EVENT_LIMIT = 12
_MINDCRAFT_CHILD_OUTPUT_READ_LIMIT = 4096
_MINDCRAFT_AGENT_STABILITY_SEC = 3.0
MICROSOFT_DEVICE_LOGIN_URL = "https://www.microsoft.com/link"
_MINDCRAFT_MICROSOFT_DEVICE_CODE_MIN_TTL_SEC = 60
_MINDCRAFT_MICROSOFT_DEVICE_CODE_MAX_TTL_SEC = 1800
_MINDCRAFT_MICROSOFT_DEVICE_CODE_LINE_PATTERN = re.compile(
    rb"\[EVELYN_MSA_DEVICE_CODE\] ([A-Z0-9]{8}) ([0-9]{1,4})\r?\n?"
)
_MINDCRAFT_CHILD_EVENT_CATEGORIES = frozenset(
    {
        "action_stop_timeout",
        "child_exit",
        "interrupt_plugin_not_ready",
        "mineflayer_end",
        "mineflayer_kicked",
        "rapid_loop",
        "spawn_event_failed",
        "uncaught_exception",
        "unhandled_rejection",
    }
)
_MINDCRAFT_CHILD_SIGNAL_CODES = frozenset(
    {
        "other",
        "SIGABRT",
        "SIGBREAK",
        "SIGHUP",
        "SIGINT",
        "SIGKILL",
        "SIGPIPE",
        "SIGQUIT",
        "SIGSEGV",
        "SIGTERM",
        "SIGUSR1",
        "SIGUSR2",
    }
)
_MINDCRAFT_CHILD_FATAL_CATEGORIES = frozenset(
    {
        "child_exit",
        "interrupt_plugin_not_ready",
        "spawn_event_failed",
        "uncaught_exception",
        "unhandled_rejection",
    }
)
_MINDCRAFT_CHILD_EXIT_PATTERN = re.compile(
    rb"Agent process exited with code (-?\d+|null) and signal ([A-Z0-9]+|null)",
    re.IGNORECASE,
)
_MINDCRAFT_CHILD_CATEGORY_MARKERS = (
    ("spawn_event_failed", (b"error in spawn event",)),
    ("interrupt_plugin_not_ready", (b"agent.requestinterrupt",)),
    (
        "unhandled_rejection",
        (
            b"unhandled rejection",
            b"unhandledpromiserejection",
            b"unhandledrejection",
            b"node:internal/process/promises",
        ),
    ),
    (
        "uncaught_exception",
        (b"uncaught exception", b"uncaughtexception"),
    ),
    (
        "action_stop_timeout",
        (b"code execution refused stop after 10 seconds",),
    ),
    (
        "rapid_loop",
        (b"infinite action loop detected", b"rapid action loop detected"),
    ),
    ("mineflayer_kicked", (b"[loginguard] kicked:",)),
    ("mineflayer_end", (b"[loginguard] disconnected:",)),
)
_PROCESS_STOP_TIMEOUT_SEC = 5.0
_FOOD_RECOVERY_GOAL = (
    "Restore and verify a safe reserve of at least three food items, then pause."
)
_MINDCRAFT_CONFIG = load_runtime_settings(
    "mindcraft",
    MINDCRAFT_SERVICE_SETTINGS,
)
STATUS_PATH = Path(
    _MINDCRAFT_CONFIG["MINDCRAFT_STATUS_PATH"]
    or RUNTIME_ARTIFACTS_ROOT / "mindcraft" / "status.json"
)
GOAL_STATE_PATH = RUNTIME_ARTIFACTS_ROOT / "voyager" / "voyager_goal_state.json"
MINDCRAFT_ROOT = Path(_MINDCRAFT_CONFIG["MINDCRAFT_ROOT"])
PROFILE_PATH = Path(
    _MINDCRAFT_CONFIG["MINDCRAFT_AGENT_PROFILE"]
    or MINDCRAFT_ROOT / "profiles" / "evelyn.json"
)


def _read_json(path: Path) -> dict[str, Any]:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (FileNotFoundError, json.JSONDecodeError, OSError):
        return {}
    return payload if isinstance(payload, dict) else {}


def _write_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + f".{os.getpid()}.tmp")
    temporary.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    temporary.replace(path)


_MINDCRAFT_LAST_ERROR_CODES = frozenset(
    {
        "minecraft_disconnected",
        "minecraft_kicked",
        "minecraft_runtime_error",
        "mindcraft_auto_restart_failed",
    }
)
_MINDCRAFT_BLOCKED_COMMAND_CODES = frozenset(
    {
        "outbound_chat_disabled",
        "outbound_whisper_disabled",
        "slash_command_blocked",
    }
)
_MINDCRAFT_SURVIVAL_DECISION_CODES = frozenset(
    {
        "acquire_food",
        "bootstrap_tools",
        "eat_inventory_food",
        "escape_to_surface",
        "handle_hostile",
        "planner_control",
        "reassess",
        "shelter_until_safe_dawn",
    }
)
_MINDCRAFT_SURVIVAL_WAKE_CODES = frozenset(
    {
        "breath",
        "fallback",
        "health",
        "hostile_band",
        "hostile_gone",
        "hostile_spawn",
        "projectile",
    }
)
_MINDCRAFT_SURVIVAL_REFLEX_CODES = frozenset({"hostile", "projectile"})
_MINDCRAFT_SURVIVAL_BOOTSTRAP_PHASE_CODES = frozenset(
    {
        "candidate_search",
        "candidate_reached",
        "candidate_unreached",
        "collect_started",
        "collect_finished",
        "no_candidates",
        "interrupted",
        "complete",
    }
)


def _bounded_survival_latency_ms(value: Any) -> int | None:
    if (
        isinstance(value, (int, float))
        and not isinstance(value, bool)
        and 0 <= value <= 600_000
    ):
        return round(value)
    return None


def _bounded_survival_count(value: Any, maximum: int) -> int | None:
    if isinstance(value, int) and not isinstance(value, bool) and 0 <= value <= maximum:
        return value
    return None


def _classify_mindcraft_child_output(
    value: bytes | str,
) -> tuple[str, int | None, str | None] | None:
    """Classify one bounded output fragment without retaining its content."""

    raw = (
        value.encode("utf-8", errors="ignore")
        if isinstance(value, str)
        else value
    )
    match = _MINDCRAFT_CHILD_EXIT_PATTERN.search(raw)
    if match is not None:
        raw_exit_code = match.group(1).lower()
        exit_code = None
        if raw_exit_code != b"null":
            candidate = int(raw_exit_code)
            if -255 <= candidate <= 255:
                exit_code = candidate
        raw_signal = match.group(2).decode("ascii").upper()
        signal_code = None
        if raw_signal != "NULL":
            signal_code = (
                raw_signal
                if raw_signal in _MINDCRAFT_CHILD_SIGNAL_CODES
                else "other"
            )
        return "child_exit", exit_code, signal_code

    lowered = raw.lower()
    for category, markers in _MINDCRAFT_CHILD_CATEGORY_MARKERS:
        if any(marker in lowered for marker in markers):
            return category, None, None
    return None


def _parse_mindcraft_microsoft_device_code(
    value: bytes | str,
) -> tuple[str, int] | None:
    if isinstance(value, str):
        try:
            raw = value.encode("ascii")
        except UnicodeEncodeError:
            return None
    else:
        raw = value
    match = _MINDCRAFT_MICROSOFT_DEVICE_CODE_LINE_PATTERN.fullmatch(raw)
    if match is None:
        return None
    ttl_sec = int(match.group(2))
    if not (
        _MINDCRAFT_MICROSOFT_DEVICE_CODE_MIN_TTL_SEC
        <= ttl_sec
        <= _MINDCRAFT_MICROSOFT_DEVICE_CODE_MAX_TTL_SEC
    ):
        return None
    return match.group(1).decode("ascii"), ttl_sec


def _classify_mindcraft_child_returncode(
    value: Any,
) -> tuple[str, int | None, str | None] | None:
    if isinstance(value, bool) or not isinstance(value, int):
        return None
    if value >= 0:
        return "child_exit", value if value <= 255 else None, None
    try:
        signal_code = signal.Signals(-value).name
    except ValueError:
        signal_code = "other"
    if signal_code not in _MINDCRAFT_CHILD_SIGNAL_CODES:
        signal_code = "other"
    return "child_exit", None, signal_code


def _project_mindcraft_child_runtime(payload: Any) -> dict[str, Any]:
    events: list[dict[str, Any]] = []
    raw_events = payload.get("events") if isinstance(payload, dict) else None
    if isinstance(raw_events, list):
        for raw_event in raw_events[-_MINDCRAFT_CHILD_EVENT_LIMIT:]:
            if not isinstance(raw_event, dict):
                continue
            category = raw_event.get("category")
            observed_at = raw_event.get("observed_at")
            if (
                not isinstance(category, str)
                or category not in _MINDCRAFT_CHILD_EVENT_CATEGORIES
                or isinstance(observed_at, bool)
                or not isinstance(observed_at, (int, float))
                or not 0 <= observed_at <= 100_000_000_000
            ):
                continue
            exit_code: int | None = None
            signal_code: str | None = None
            if category == "child_exit":
                candidate_exit_code = raw_event.get("exit_code")
                if (
                    isinstance(candidate_exit_code, int)
                    and not isinstance(candidate_exit_code, bool)
                    and -255 <= candidate_exit_code <= 255
                ):
                    exit_code = candidate_exit_code
                candidate_signal = raw_event.get("signal")
                if (
                    isinstance(candidate_signal, str)
                    and candidate_signal in _MINDCRAFT_CHILD_SIGNAL_CODES
                ):
                    signal_code = candidate_signal
            events.append(
                {
                    "category": category,
                    "observed_at": observed_at,
                    "exit_code": exit_code,
                    "signal": signal_code,
                }
            )
    updated_at = payload.get("updated_at") if isinstance(payload, dict) else None
    if (
        isinstance(updated_at, bool)
        or not isinstance(updated_at, (int, float))
        or not 0 <= updated_at <= 100_000_000_000
    ):
        updated_at = events[-1]["observed_at"] if events else None
    return {
        "schema": MINDCRAFT_CHILD_RUNTIME_SCHEMA,
        "events": events,
        "updated_at": updated_at,
        "content_free": True,
    }


def _project_mindcraft_telemetry(payload: dict[str, Any]) -> dict[str, Any]:
    """Return the small public/status projection, never legacy free text."""

    projected: dict[str, Any] = {}
    for key in (
        "position",
        "health",
        "hunger",
        "food_saturation",
        "inventory",
        "hostiles_nearby",
        "last_death_event",
    ):
        if key in payload:
            projected[key] = deepcopy(payload[key])
    projected["runtime"] = "mindcraft"
    for key in ("running", "connected"):
        if isinstance(payload.get(key), bool):
            projected[key] = payload[key]
    for key in ("agent_name", "connection_state", "phase"):
        value = _safe_action_code(payload.get(key), "")
        if value:
            projected[key] = value
    for key in ("updated_at", "connected_at", "last_blocked_command_at"):
        value = payload.get(key)
        if isinstance(value, (int, float)) and not isinstance(value, bool):
            projected[key] = value
    blocked_count = payload.get("blocked_command_count")
    if (
        isinstance(blocked_count, int)
        and not isinstance(blocked_count, bool)
        and blocked_count >= 0
    ):
        projected["blocked_command_count"] = blocked_count

    blocked_code = payload.get("last_blocked_command")
    projected["last_blocked_command"] = (
        blocked_code
        if isinstance(blocked_code, str)
        and blocked_code in _MINDCRAFT_BLOCKED_COMMAND_CODES
        else None
    )
    error_code = payload.get("last_error")
    projected["last_error"] = (
        error_code
        if isinstance(error_code, str)
        and error_code in _MINDCRAFT_LAST_ERROR_CODES
        else None
    )

    task_contract = payload.get("task_contract")
    if isinstance(task_contract, dict):
        mode = task_contract.get("goal_manager_mode")
        projected["task_contract"] = {
            "schema": (
                MINDCRAFT_TASK_CONTRACT_SCHEMA
                if task_contract.get("schema") == MINDCRAFT_TASK_CONTRACT_SCHEMA
                else ""
            ),
            "ready": task_contract.get("ready") is True,
            "goal_manager_mode": (
                mode
                if isinstance(mode, str) and mode in {"off", "shadow", "gated"}
                else ""
            ),
            "command_gate": (
                "evelyn_goal_manager"
                if task_contract.get("command_gate") == "evelyn_goal_manager"
                else ""
            ),
            "effect_verification": (
                "explicit_postcondition"
                if task_contract.get("effect_verification")
                == "explicit_postcondition"
                else ""
            ),
        }

    goal_manager = payload.get("goal_manager")
    if isinstance(goal_manager, dict):
        mode = goal_manager.get("mode")
        autonomy_state = goal_manager.get("autonomy_state")
        pause_reason = goal_manager.get("manual_pause_reason")
        subgoal = goal_manager.get("current_subgoal")
        subgoal_id = (
            _safe_action_code(subgoal.get("id"), "")
            if isinstance(subgoal, dict)
            else ""
        )
        projected["goal_manager"] = {
            "mode": (
                mode
                if isinstance(mode, str) and mode in {"off", "shadow", "gated"}
                else ""
            ),
            "autonomy_state": (
                autonomy_state
                if isinstance(autonomy_state, str)
                and autonomy_state in {"active", "manual_pause", "completed"}
                else ""
            ),
            "manual_pause_reason": (
                pause_reason
                if isinstance(pause_reason, str)
                and pause_reason
                in {"user_end_goal_command", "world_effect_candidate_published"}
                else None
            ),
            "current_subgoal": {"id": subgoal_id} if subgoal_id else None,
        }

    survival = payload.get("survival_controller")
    if isinstance(survival, dict) and survival.get("content_free") is True:
        phase = survival.get("phase")
        last_decision = survival.get("last_decision")
        bootstrap_phase = survival.get("bootstrap_phase")
        handoff_until = survival.get("recovery_handoff_until")
        last_reflex_at = survival.get("last_reflex_at")
        updated_at = survival.get("updated_at")
        projected["survival_controller"] = {
            "phase": phase if phase in _MINDCRAFT_SURVIVAL_DECISION_CODES else None,
            "last_decision": (
                last_decision
                if last_decision in _MINDCRAFT_SURVIVAL_DECISION_CODES
                else None
            ),
            "last_success": (
                survival.get("last_success")
                if isinstance(survival.get("last_success"), bool)
                else None
            ),
            "shelter_success_count": _bounded_survival_count(
                survival.get("shelter_success_count"), 9_007_199_254_740_991
            ),
            "last_error": (
                "survival_action_failed"
                if survival.get("last_error") == "survival_action_failed"
                else None
            ),
            "recovery_progress": survival.get("recovery_progress") is True,
            "recovery_handoff_until": (
                handoff_until
                if isinstance(handoff_until, (int, float))
                and not isinstance(handoff_until, bool)
                else 0
            ),
            "wake_reason": (
                survival.get("wake_reason")
                if survival.get("wake_reason") in _MINDCRAFT_SURVIVAL_WAKE_CODES
                else None
            ),
            "wake_to_decision_ms": _bounded_survival_latency_ms(
                survival.get("wake_to_decision_ms")
            ),
            "decision_to_action_ms": _bounded_survival_latency_ms(
                survival.get("decision_to_action_ms")
            ),
            "reflex_reason": (
                survival.get("reflex_reason")
                if survival.get("reflex_reason")
                in _MINDCRAFT_SURVIVAL_REFLEX_CODES
                else None
            ),
            "reflex_to_action_ms": _bounded_survival_latency_ms(
                survival.get("reflex_to_action_ms")
            ),
            "bootstrap_phase": (
                bootstrap_phase
                if bootstrap_phase in _MINDCRAFT_SURVIVAL_BOOTSTRAP_PHASE_CODES
                else None
            ),
            "bootstrap_candidate_count": _bounded_survival_count(
                survival.get("bootstrap_candidate_count"), 4
            ),
            "bootstrap_logs_before": _bounded_survival_count(
                survival.get("bootstrap_logs_before"), 64
            ),
            "bootstrap_logs_after": _bounded_survival_count(
                survival.get("bootstrap_logs_after"), 64
            ),
            "last_reflex_at": (
                last_reflex_at
                if isinstance(last_reflex_at, (int, float))
                and not isinstance(last_reflex_at, bool)
                and last_reflex_at >= 0
                else None
            ),
            "updated_at": (
                updated_at
                if isinstance(updated_at, (int, float))
                and not isinstance(updated_at, bool)
                else None
            ),
            "content_free": True,
        }
    return projected


def _read_mindcraft_status() -> dict[str, Any]:
    return _project_mindcraft_telemetry(_read_json(STATUS_PATH))


def _write_mindcraft_status(payload: dict[str, Any]) -> None:
    _write_json(STATUS_PATH, _project_mindcraft_telemetry(payload))


def _process_birth_identity(pid: int) -> str | None:
    """Return an OS creation identity for ``pid``, never command content.

    PID alone is unsafe across service restarts because it may be reused.  The
    Linux start-time tick and Windows creation FILETIME are stable for the
    lifetime of one process and contain no argv, path, goal, or transcript.
    Unsupported or unreadable process tables raise so callers fail closed.
    """

    if isinstance(pid, bool) or not isinstance(pid, int) or pid <= 0:
        raise ValueError("mindcraft_process_identity_invalid")
    if os.name == "nt":
        from ctypes import wintypes

        process_query_limited_information = 0x1000
        error_invalid_parameter = 87
        kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
        kernel32.OpenProcess.argtypes = [
            wintypes.DWORD,
            wintypes.BOOL,
            wintypes.DWORD,
        ]
        kernel32.OpenProcess.restype = wintypes.HANDLE
        kernel32.GetProcessTimes.argtypes = [
            wintypes.HANDLE,
            ctypes.POINTER(wintypes.FILETIME),
            ctypes.POINTER(wintypes.FILETIME),
            ctypes.POINTER(wintypes.FILETIME),
            ctypes.POINTER(wintypes.FILETIME),
        ]
        kernel32.GetProcessTimes.restype = wintypes.BOOL
        kernel32.CloseHandle.argtypes = [wintypes.HANDLE]
        kernel32.CloseHandle.restype = wintypes.BOOL
        handle = kernel32.OpenProcess(
            process_query_limited_information,
            False,
            pid,
        )
        if not handle:
            error = ctypes.get_last_error()
            if error == error_invalid_parameter:
                return None
            raise OSError(error, "process identity unavailable")
        try:
            creation = wintypes.FILETIME()
            exit_time = wintypes.FILETIME()
            kernel_time = wintypes.FILETIME()
            user_time = wintypes.FILETIME()
            if not kernel32.GetProcessTimes(
                handle,
                ctypes.byref(creation),
                ctypes.byref(exit_time),
                ctypes.byref(kernel_time),
                ctypes.byref(user_time),
            ):
                error = ctypes.get_last_error()
                raise OSError(error, "process identity unavailable")
            value = (int(creation.dwHighDateTime) << 32) | int(
                creation.dwLowDateTime
            )
            return f"windows:{value}"
        finally:
            kernel32.CloseHandle(handle)

    stat_path = Path("/proc") / str(pid) / "stat"
    try:
        raw = stat_path.read_text(encoding="utf-8")
    except FileNotFoundError:
        return None
    except (OSError, UnicodeError) as exc:
        raise OSError("process identity unavailable") from exc
    closing = raw.rfind(")")
    if closing < 0:
        raise OSError("process identity unavailable")
    fields_after_name = raw[closing + 1 :].strip().split()
    # /proc/<pid>/stat field 22 is starttime.  The suffix begins at field 3.
    if len(fields_after_name) <= 19 or not fields_after_name[19].isdigit():
        raise OSError("process identity unavailable")
    return f"linux:{fields_after_name[19]}"


def _terminate_process_identity(
    pid: int,
    expected_birth_identity: str,
    *,
    timeout_sec: float = _PROCESS_STOP_TIMEOUT_SEC,
) -> bool:
    """Terminate only the exact PID/birth pair and prove it is gone."""

    if os.name == "nt":
        from ctypes import wintypes

        process_terminate = 0x0001
        process_query_limited_information = 0x1000
        synchronize = 0x00100000
        error_invalid_parameter = 87
        wait_object_0 = 0
        kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
        kernel32.OpenProcess.argtypes = [
            wintypes.DWORD,
            wintypes.BOOL,
            wintypes.DWORD,
        ]
        kernel32.OpenProcess.restype = wintypes.HANDLE
        kernel32.GetProcessTimes.argtypes = [
            wintypes.HANDLE,
            ctypes.POINTER(wintypes.FILETIME),
            ctypes.POINTER(wintypes.FILETIME),
            ctypes.POINTER(wintypes.FILETIME),
            ctypes.POINTER(wintypes.FILETIME),
        ]
        kernel32.GetProcessTimes.restype = wintypes.BOOL
        kernel32.TerminateProcess.argtypes = [wintypes.HANDLE, wintypes.UINT]
        kernel32.TerminateProcess.restype = wintypes.BOOL
        kernel32.WaitForSingleObject.argtypes = [wintypes.HANDLE, wintypes.DWORD]
        kernel32.WaitForSingleObject.restype = wintypes.DWORD
        kernel32.CloseHandle.argtypes = [wintypes.HANDLE]
        kernel32.CloseHandle.restype = wintypes.BOOL
        handle = kernel32.OpenProcess(
            process_terminate
            | process_query_limited_information
            | synchronize,
            False,
            pid,
        )
        if not handle:
            return ctypes.get_last_error() == error_invalid_parameter
        try:
            creation = wintypes.FILETIME()
            exit_time = wintypes.FILETIME()
            kernel_time = wintypes.FILETIME()
            user_time = wintypes.FILETIME()
            if not kernel32.GetProcessTimes(
                handle,
                ctypes.byref(creation),
                ctypes.byref(exit_time),
                ctypes.byref(kernel_time),
                ctypes.byref(user_time),
            ):
                return False
            current = (
                int(creation.dwHighDateTime) << 32
            ) | int(creation.dwLowDateTime)
            if f"windows:{current}" != expected_birth_identity:
                return True
            if not kernel32.TerminateProcess(handle, 1):
                return False
            wait_ms = max(1, int(max(0.1, timeout_sec) * 1000))
            return kernel32.WaitForSingleObject(handle, wait_ms) == wait_object_0
        finally:
            kernel32.CloseHandle(handle)

    pidfd_open = getattr(os, "pidfd_open", None)
    pidfd_send_signal = getattr(signal, "pidfd_send_signal", None)
    if not callable(pidfd_open) or not callable(pidfd_send_signal):
        return False
    try:
        pidfd = pidfd_open(pid, 0)
    except ProcessLookupError:
        return True
    except OSError:
        return False
    try:
        try:
            current = _process_birth_identity(pid)
        except (OSError, ValueError):
            return False
        if current is None or current != expected_birth_identity:
            return True
        try:
            pidfd_send_signal(pidfd, signal.SIGTERM, None, 0)
        except ProcessLookupError:
            return True
        except OSError:
            return False
        readable, _, _ = select.select(
            [pidfd],
            [],
            [],
            max(0.1, float(timeout_sec)),
        )
        if readable:
            return True
        try:
            pidfd_send_signal(pidfd, signal.SIGKILL, None, 0)
        except ProcessLookupError:
            return True
        except OSError:
            return False
        readable, _, _ = select.select(
            [pidfd],
            [],
            [],
            max(0.1, float(timeout_sec)),
        )
        return bool(readable)
    finally:
        os.close(pidfd)


def _clean_goal(value: Any) -> str:
    return str(value or "").strip() or DEFAULT_GOAL


def _allowed_players() -> list[str]:
    return [
        item.strip()
        for item in str(_MINDCRAFT_CONFIG["MINDCRAFT_ALLOWED_PLAYERS"]).split(",")
        if item.strip()
    ]


def _effect_observer_ready() -> bool:
    projector = globals().get("WORLD_EFFECT_PROJECTOR")
    gateway = globals().get("ACTION_GATEWAY")
    if projector is None:
        return False
    try:
        status = projector.status()
    except Exception:
        return False
    safe_state = status.get("state") in {
        "idle",
        "armed",
        "verified",
        "rejected",
    }
    gateway_ready = True
    if gateway is not None:
        try:
            gateway_ready = gateway.available()
        except Exception:
            gateway_ready = False
    return bool(
        status.get("auditReady") is True
        and status.get("statusReady") is True
        and safe_state
        and gateway_ready
    )


def _functional_readiness(
    *,
    world_lease_authorized: bool,
    running: bool,
    telemetry_fresh: bool,
    connected: bool,
    telemetry: dict[str, Any],
    effect_observer_ready: bool,
) -> dict[str, Any]:
    task_contract = (
        telemetry.get("task_contract")
        if isinstance(telemetry.get("task_contract"), dict)
        else {}
    )
    goal_manager = (
        telemetry.get("goal_manager")
        if isinstance(telemetry.get("goal_manager"), dict)
        else {}
    )
    goal_manager_mode = str(
        goal_manager.get("mode") or ""
    ).strip().lower()
    autonomy_state = str(
        goal_manager.get("autonomy_state") or ""
    ).strip().lower()
    task_contract_ready = bool(
        task_contract.get("schema")
        == MINDCRAFT_TASK_CONTRACT_SCHEMA
        and task_contract.get("ready") is True
        and str(
            task_contract.get("goal_manager_mode") or ""
        ).strip().lower()
        == "gated"
        and task_contract.get("command_gate")
        == "evelyn_goal_manager"
        and task_contract.get("effect_verification")
        == "explicit_postcondition"
        and goal_manager_mode == "gated"
    )
    autonomy_active = autonomy_state == "active"
    dependencies = {
        "worldLeaseAuthorized": bool(
            world_lease_authorized
        ),
        "runnerAlive": bool(running),
        "telemetryFresh": bool(telemetry_fresh),
        "minecraftConnected": bool(connected),
        "taskContractReady": task_contract_ready,
        "effectObserverReady": bool(effect_observer_ready),
        "autonomyActive": autonomy_active,
    }
    blockers = [
        MINECRAFT_READINESS_BLOCKERS[name]
        for name in MINECRAFT_READINESS_DEPENDENCIES
        if not dependencies[name]
    ]
    state = expected_readiness_state(dependencies)
    return {
        "schema": MINECRAFT_AUTONOMY_READINESS_SCHEMA,
        "state": state,
        "ready": not blockers,
        "blockers": blockers,
        "dependencies": dependencies,
        "taskContract": {
            "schema": (
                MINDCRAFT_TASK_CONTRACT_SCHEMA
                if task_contract_ready
                else ""
            ),
            "goalManagerMode": goal_manager_mode,
            "autonomyState": autonomy_state,
            "commandGate": str(
                task_contract.get("command_gate") or ""
            ),
            "effectVerification": str(
                task_contract.get("effect_verification") or ""
            ),
        },
        "contentFree": True,
    }


def _stable_minecraft_connection(
    *,
    running: bool,
    telemetry_fresh: bool,
    telemetry: dict[str, Any],
    child_runtime: dict[str, Any],
    now: float | None = None,
) -> bool:
    connected_at = telemetry.get("connected_at")
    observed_at = time.time() if now is None else now
    fatal_child_event = any(
        isinstance(event, dict)
        and event.get("category") in _MINDCRAFT_CHILD_FATAL_CATEGORIES
        for event in child_runtime.get("events", [])
    )
    return bool(
        running
        and telemetry_fresh
        and telemetry.get("connected") is True
        and isinstance(connected_at, (int, float))
        and not isinstance(connected_at, bool)
        and 0 <= observed_at - float(connected_at)
        and observed_at - float(connected_at) >= _MINDCRAFT_AGENT_STABILITY_SEC
        and not fatal_child_event
    )


class MindcraftRuntime:
    def __init__(
        self,
        *,
        process_identity_path: Path | None = None,
        child_runtime_path: Path | None = None,
    ) -> None:
        self._process: subprocess.Popen[bytes] | None = None
        self._lock = threading.RLock()
        self.process_identity_path = Path(
            process_identity_path or MINDCRAFT_PROCESS_IDENTITY_PATH
        )
        self.child_runtime_path = Path(
            child_runtime_path or MINDCRAFT_CHILD_RUNTIME_PATH
        )
        child_runtime = _project_mindcraft_child_runtime(
            _read_json(self.child_runtime_path)
        )
        self._child_runtime_lock = threading.Lock()
        self._child_runtime_events = list(child_runtime["events"])
        self._child_runtime_updated_at = child_runtime["updated_at"]
        self._child_runtime_write_lock = threading.Lock()
        self._child_runtime_write_event = threading.Event()
        self._child_runtime_writer_thread: threading.Thread | None = None
        self._child_output_generation = 0
        self._child_output_thread: threading.Thread | None = None
        self._microsoft_auth_challenge: dict[str, Any] | None = None
        self._microsoft_auth_challenge_deadline = 0.0
        self._process_birth_identity = ""
        self._started_at: float | None = None
        self._last_exit_code: int | None = None
        self._manual_stop = True
        self._last_world_lease_error_code = (
            "minecraft_world_authorization_required"
        )
        self.runtime_errors = RuntimeErrorCounter()
        self._auto_restart = bool(_MINDCRAFT_CONFIG["MINDCRAFT_AUTO_RESTART"])
        self._restart_backoff_until = 0.0
        self._restart_cooldown_sec = float(
            _MINDCRAFT_CONFIG["MINDCRAFT_AUTO_RESTART_COOLDOWN_SEC"]
        )
        self._world_effect_binding: dict[str, Any] | None = None

    def _child_runtime_payload_unlocked(self) -> dict[str, Any]:
        return {
            "schema": MINDCRAFT_CHILD_RUNTIME_SCHEMA,
            "events": deepcopy(self._child_runtime_events),
            "updated_at": self._child_runtime_updated_at,
            "content_free": True,
        }

    def _flush_child_runtime(self) -> None:
        with self._child_runtime_lock:
            payload = self._child_runtime_payload_unlocked()
        try:
            with self._child_runtime_write_lock:
                atomic_json_write(
                    self.child_runtime_path,
                    payload,
                    attempts=1,
                )
        except OSError as exc:
            self.runtime_errors.record("status_write_failed", exc)

    def _write_child_runtime_events(self) -> None:
        while True:
            self._child_runtime_write_event.wait()
            self._child_runtime_write_event.clear()
            self._flush_child_runtime()

    def _ensure_child_runtime_writer(self) -> None:
        thread = self._child_runtime_writer_thread
        if thread is not None and thread.is_alive():
            return
        thread = threading.Thread(
            target=self._write_child_runtime_events,
            name="mindcraft-child-runtime-writer",
            daemon=True,
        )
        self._child_runtime_writer_thread = thread
        thread.start()

    def _reset_child_runtime(self) -> int:
        with self._child_runtime_lock:
            self._child_output_generation += 1
            self._child_runtime_events = []
            self._child_runtime_updated_at = time.time()
            self._microsoft_auth_challenge = None
            self._microsoft_auth_challenge_deadline = 0.0
            generation = self._child_output_generation
        self._flush_child_runtime()
        return generation

    def _record_child_runtime_event(
        self,
        generation: int,
        category: str,
        exit_code: int | None,
        signal_code: str | None,
    ) -> None:
        observed_at = time.time()
        event = _project_mindcraft_child_runtime(
            {
                "events": [
                    {
                        "category": category,
                        "observed_at": observed_at,
                        "exit_code": exit_code,
                        "signal": signal_code,
                    }
                ]
            }
        )["events"]
        if not event:
            return
        with self._child_runtime_lock:
            if generation != self._child_output_generation:
                return
            self._child_runtime_events = (
                self._child_runtime_events + event
            )[-_MINDCRAFT_CHILD_EVENT_LIMIT:]
            self._child_runtime_updated_at = observed_at
        self._child_runtime_write_event.set()

    def _child_runtime_snapshot(self) -> dict[str, Any]:
        with self._child_runtime_lock:
            return self._child_runtime_payload_unlocked()

    def _clear_microsoft_auth_challenge(self) -> None:
        with self._child_runtime_lock:
            self._microsoft_auth_challenge = None
            self._microsoft_auth_challenge_deadline = 0.0

    def _record_microsoft_auth_challenge(
        self,
        generation: int,
        user_code: str,
        ttl_sec: int,
    ) -> None:
        observed_at = time.time()
        deadline = time.monotonic() + ttl_sec
        with self._child_runtime_lock:
            if generation != self._child_output_generation:
                return
            self._microsoft_auth_challenge = {
                "user_code": user_code,
                "expires_at": observed_at + ttl_sec,
            }
            self._microsoft_auth_challenge_deadline = deadline

    def _microsoft_auth_challenge_snapshot(
        self,
        *,
        running: bool,
        connected: bool,
    ) -> dict[str, Any] | None:
        with self._child_runtime_lock:
            if (
                not running
                or connected
                or self._microsoft_auth_challenge is None
                or time.monotonic()
                >= self._microsoft_auth_challenge_deadline
            ):
                self._microsoft_auth_challenge = None
                self._microsoft_auth_challenge_deadline = 0.0
                return None
            return {
                "state": "device_code_pending",
                "user_code": self._microsoft_auth_challenge["user_code"],
                "verification_url": MICROSOFT_DEVICE_LOGIN_URL,
                "expires_at": self._microsoft_auth_challenge["expires_at"],
            }

    def _drain_child_output(
        self,
        process: subprocess.Popen[bytes],
        generation: int,
    ) -> None:
        stream = process.stdout
        if stream is None:
            return
        saw_child_exit = False
        try:
            while True:
                fragment = stream.readline(_MINDCRAFT_CHILD_OUTPUT_READ_LIMIT)
                if not fragment:
                    break
                auth_challenge = _parse_mindcraft_microsoft_device_code(
                    fragment
                )
                if auth_challenge is not None:
                    self._record_microsoft_auth_challenge(
                        generation,
                        *auth_challenge,
                    )
                classified = _classify_mindcraft_child_output(fragment)
                if classified is not None:
                    saw_child_exit = saw_child_exit or classified[0] == "child_exit"
                    self._record_child_runtime_event(generation, *classified)
        except (OSError, ValueError) as exc:
            self.runtime_errors.record("mindcraft_log_close_failed", exc)
        finally:
            try:
                stream.close()
            except OSError as exc:
                self.runtime_errors.record("mindcraft_log_close_failed", exc)
            if not saw_child_exit:
                return_code = process.poll()
                if return_code is None:
                    try:
                        return_code = process.wait(timeout=0.1)
                    except subprocess.TimeoutExpired:
                        return_code = None
                classified_exit = _classify_mindcraft_child_returncode(
                    return_code
                )
                if classified_exit is not None:
                    self._record_child_runtime_event(
                        generation,
                        *classified_exit,
                    )
            self._flush_child_runtime()
            if self._child_output_thread is threading.current_thread():
                self._child_output_thread = None

    def _start_child_output_drain(
        self,
        process: subprocess.Popen[bytes],
        generation: int,
    ) -> None:
        self._ensure_child_runtime_writer()
        thread = threading.Thread(
            target=self._drain_child_output,
            args=(process, generation),
            name="mindcraft-child-output-drain",
            daemon=True,
        )
        self._child_output_thread = thread
        try:
            thread.start()
        except Exception:
            self._child_output_thread = None
            if process.poll() is None:
                process.kill()
                process.wait(timeout=5)
            if process.stdout is not None:
                process.stdout.close()
            raise

    def _retire_child_output_drain(self) -> None:
        thread = self._child_output_thread
        if thread is not None and thread is not threading.current_thread():
            thread.join(timeout=0.25)
        with self._child_runtime_lock:
            self._child_output_generation += 1
        if thread is not None and not thread.is_alive():
            self._child_output_thread = None

    @staticmethod
    def _process_identity_payload(
        *,
        state: str,
        pid: int = 0,
        birth_identity: str = "",
    ) -> dict[str, Any]:
        return {
            "schema": MINDCRAFT_PROCESS_IDENTITY_SCHEMA,
            "state": state,
            "pid": int(pid),
            "birthIdentity": str(birth_identity),
            "updatedAt": time.time(),
            "contentFree": True,
        }

    @staticmethod
    def _valid_process_identity(payload: Any) -> bool:
        if not isinstance(payload, dict) or set(payload) != {
            "schema",
            "state",
            "pid",
            "birthIdentity",
            "updatedAt",
            "contentFree",
        }:
            return False
        if (
            payload.get("schema") != MINDCRAFT_PROCESS_IDENTITY_SCHEMA
            or payload.get("state") not in {"starting", "active", "stopped"}
            or isinstance(payload.get("pid"), bool)
            or not isinstance(payload.get("pid"), int)
            or isinstance(payload.get("updatedAt"), bool)
            or not isinstance(payload.get("updatedAt"), (int, float))
            or payload.get("contentFree") is not True
        ):
            return False
        birth_identity = payload.get("birthIdentity")
        if not isinstance(birth_identity, str):
            return False
        if payload["state"] in {"starting", "stopped"}:
            return payload["pid"] == 0 and birth_identity == ""
        prefix, separator, value = birth_identity.partition(":")
        return bool(
            payload["pid"] > 0
            and separator
            and prefix in {"linux", "windows"}
            and value.isdigit()
            and len(value) <= 32
        )

    def _write_process_identity(
        self,
        *,
        state: str,
        pid: int = 0,
        birth_identity: str = "",
    ) -> None:
        payload = self._process_identity_payload(
            state=state,
            pid=pid,
            birth_identity=birth_identity,
        )
        if not self._valid_process_identity(payload):
            raise ValueError("mindcraft_process_identity_invalid")
        atomic_json_write(
            self.process_identity_path,
            payload,
            durable=True,
        )

    def _load_process_identity(self) -> tuple[dict[str, Any] | None, str]:
        try:
            payload = json.loads(
                self.process_identity_path.read_text(encoding="utf-8")
            )
        except FileNotFoundError:
            return None, "minecraft_prior_process_identity_missing"
        except (OSError, UnicodeError, json.JSONDecodeError):
            return None, "minecraft_prior_process_identity_invalid"
        if not self._valid_process_identity(payload):
            return None, "minecraft_prior_process_identity_invalid"
        return payload, ""

    def _record_live_process_identity(
        self,
        process: subprocess.Popen[bytes],
    ) -> None:
        pid = int(process.pid)
        birth_identity: str | None = None
        last_error: Exception | None = None
        for _ in range(20):
            try:
                birth_identity = _process_birth_identity(pid)
            except (OSError, ValueError) as exc:
                last_error = exc
                break
            if birth_identity:
                break
            if process.poll() is not None:
                break
            time.sleep(0.01)
        if not birth_identity:
            raise RuntimeError(
                "mindcraft_process_identity_unavailable"
            ) from last_error
        self._write_process_identity(
            state="active",
            pid=pid,
            birth_identity=birth_identity,
        )
        self._process_birth_identity = birth_identity

    def reconcile_inflight_restart(self) -> tuple[bool, str]:
        """Prove a pre-restart action process is dead before handoff.

        The gateway calls this only when its durable status contains an
        accepted/running action.  Missing or ambiguous identity is therefore
        not a clean first start: it is an unrecoverable authority ambiguity.
        """

        with self._lock:
            if self.process_alive():
                try:
                    self.stop()
                except Exception:
                    return False, "minecraft_prior_process_stop_failed"
                if self.process_alive():
                    return False, "minecraft_prior_process_stop_unverified"
                return True, ""
            payload, error = self._load_process_identity()
            if payload is None:
                return False, error
            if payload["state"] == "starting":
                # Popen may or may not have returned before the previous
                # service died.  With no durable PID/birth pair there is no
                # safe process to signal and no proof that no child exists.
                return False, "minecraft_prior_process_start_ambiguous"
            if payload["state"] == "stopped":
                return True, ""
            pid = int(payload["pid"])
            birth_identity = str(payload["birthIdentity"])
            platform_prefix = "windows:" if os.name == "nt" else "linux:"
            if not birth_identity.startswith(platform_prefix):
                # A PID namespace from another OS authority domain cannot
                # prove anything about a process that may still be alive on
                # the host sharing this artifact directory.
                return False, "minecraft_prior_process_identity_unverified"
            try:
                observed = _process_birth_identity(pid)
            except (OSError, ValueError):
                return False, "minecraft_prior_process_identity_unverified"
            if observed is not None and observed == birth_identity:
                if not _terminate_process_identity(pid, birth_identity):
                    return False, "minecraft_prior_process_stop_unverified"
            try:
                remaining = _process_birth_identity(pid)
            except (OSError, ValueError):
                return False, "minecraft_prior_process_identity_unverified"
            if remaining == birth_identity:
                return False, "minecraft_prior_process_stop_unverified"
            try:
                self._write_process_identity(state="stopped")
            except (OSError, TypeError, ValueError):
                return False, "minecraft_prior_process_identity_write_failed"
            self._process_birth_identity = ""
            return True, ""

    def process_alive(self) -> bool:
        process = self._process
        return process is not None and process.poll() is None

    def get_goal(self) -> str:
        payload = _read_json(GOAL_STATE_PATH)
        return _clean_goal(payload.get("goal_override") or payload.get("goal"))

    def persist_goal(self, goal: str) -> None:
        goal = _clean_goal(goal)
        _write_json(
            GOAL_STATE_PATH,
            {"goal_override": goal, "goal": goal, "runtime": "mindcraft", "updated_at": time.time()},
        )

    def _settings(self, goal: str) -> dict[str, Any]:
        return {
            "minecraft_version": str(_MINDCRAFT_CONFIG["MINECRAFT_VERSION"]),
            "host": str(_MINDCRAFT_CONFIG["MINEFLAYER_HOST"]),
            "port": int(_MINDCRAFT_CONFIG["MINEFLAYER_PORT"]),
            "auth": str(_MINDCRAFT_CONFIG["MINEFLAYER_AUTH"]),
            "mindserver_port": int(_MINDCRAFT_CONFIG["MINDSERVER_PORT"]),
            "auto_open_ui": False,
            "base_profile": "survival",
            "profiles": [str(PROFILE_PATH)],
            "load_memory": False,
            "init_message": f"Set and pursue this survival goal: {goal}",
            "only_chat_with": _allowed_players(),
            "speak": False,
            "chat_ingame": False,
            "language": "ko",
            "render_bot_view": False,
            "allow_insecure_coding": False,
            "allow_vision": False,
            "blocked_actions": [
                "!newAction",
                "!setMode",
                "!attackPlayer",
                "!digDown",
                "!checkBlueprint",
                "!checkBlueprintLevel",
                "!getBlueprint",
                "!getBlueprintLevel",
                "!searchWiki",
            ],
            "code_timeout_mins": 1,
            "relevant_docs_count": 8,
            "max_messages": 8,
            "num_examples": 1,
            "max_commands": -1,
            "show_command_syntax": "full",
            "narrate_behavior": False,
            "chat_bot_messages": False,
            "spawn_timeout": 60,
            "block_place_delay": 100,
            "log_all_prompts": False,
        }

    def start(
        self,
        goal: str | None = None,
        *,
        world_effect_binding: dict[str, Any] | None = None,
        persist_goal_state: bool = True,
    ) -> None:
        with self._lock:
            requested_goal = _clean_goal(goal or self.get_goal())
            if self.process_alive():
                return
            self._manual_stop = False
            self._world_effect_binding = (
                deepcopy(world_effect_binding)
                if isinstance(world_effect_binding, dict)
                else None
            )
            if persist_goal_state:
                self.persist_goal(requested_goal)
            if not (MINDCRAFT_ROOT / "main.js").exists():
                self.runtime_errors.record(
                    "mindcraft_start_failed",
                    FileNotFoundError,
                )
                raise RuntimeError(f"Mindcraft main.js is missing under {MINDCRAFT_ROOT}")
            if not PROFILE_PATH.exists():
                self.runtime_errors.record(
                    "mindcraft_start_failed",
                    FileNotFoundError,
                )
                raise RuntimeError(f"Mindcraft Evelyn profile is missing: {PROFILE_PATH}")

            env = os.environ.copy()
            env["SETTINGS_JSON"] = json.dumps(self._settings(requested_goal), ensure_ascii=False)
            env["PROFILES"] = json.dumps([str(PROFILE_PATH)])
            env["MINDCRAFT_GOAL"] = requested_goal
            env["MINDCRAFT_STATUS_PATH"] = str(STATUS_PATH)
            env["MINDCRAFT_GOAL_MANAGER_MODE"] = str(
                _MINDCRAFT_CONFIG[
                    "MINDCRAFT_GOAL_MANAGER_MODE"
                ]
            )
            binding = self._world_effect_binding
            if binding is not None:
                environment_keys = {
                    "goalRunId": "MINDCRAFT_WORLD_EFFECT_GOAL_RUN_ID",
                    "actionRunId": "MINDCRAFT_WORLD_EFFECT_ACTION_RUN_ID",
                    "actionKey": "MINDCRAFT_WORLD_EFFECT_ACTION_KEY",
                    "contractCode": "MINDCRAFT_WORLD_EFFECT_CONTRACT_CODE",
                    "leaseId": "MINDCRAFT_WORLD_EFFECT_LEASE_ID",
                    "leaseProcessNonce": (
                        "MINDCRAFT_WORLD_EFFECT_LEASE_PROCESS_NONCE"
                    ),
                    "producerNonce": (
                        "MINDCRAFT_WORLD_EFFECT_PRODUCER_NONCE"
                    ),
                }
                for key, environment_key in environment_keys.items():
                    env[environment_key] = str(binding[key])
            env.setdefault(
                "MINECRAFT_USERNAME",
                str(_MINDCRAFT_CONFIG["MINEFLAYER_USERNAME"]),
            )
            env.setdefault(
                "MINEFLAYER_PROFILES_FOLDER",
                str(_MINDCRAFT_CONFIG["MINEFLAYER_PROFILES_FOLDER"]),
            )
            env["MINDCRAFT_ENABLE_SKIN_COMMANDS"] = "false"
            env["INSECURE_CODING"] = ""

            # Fence the Popen/write crash window.  A restart that sees this
            # marker cannot assume the child was never created and therefore
            # quarantines instead of admitting another action.
            try:
                self._write_process_identity(state="starting")
            except Exception as exc:
                self.runtime_errors.record(
                    "mindcraft_process_identity_failed",
                    exc,
                )
                raise
            try:
                child_output_generation = self._reset_child_runtime()
                self._process = subprocess.Popen(
                    ["node", "main.js"],
                    cwd=str(MINDCRAFT_ROOT),
                    env=env,
                    stdout=subprocess.PIPE,
                    stderr=subprocess.STDOUT,
                )
                self._start_child_output_drain(
                    self._process,
                    child_output_generation,
                )
            except Exception as exc:
                self.runtime_errors.record("mindcraft_start_failed", exc)
                raise
            try:
                self._record_live_process_identity(self._process)
            except Exception as exc:
                self.runtime_errors.record(
                    "mindcraft_process_identity_failed",
                    exc,
                )
                # A child without durable PID/birth identity must never be
                # allowed to outlive this service as an invisible authority.
                # Keep the Popen handle intact if cleanup fails so the action
                # gateway quarantines and a later exact cancel can retry.
                self.stop()
                raise
            self._started_at = time.time()
            self._last_exit_code = None
            self._restart_backoff_until = 0.0

    def _cleanup_process_state(self) -> None:
        process = self._process
        if process is not None and process.poll() is not None:
            self._last_exit_code = process.returncode
            self._process = None
            self._clear_microsoft_auth_challenge()

    def _ensure_process_running(self) -> None:
        if (
            self._manual_stop
            or not self._auto_restart
            or self._world_effect_binding is not None
        ):
            return
        if time.time() < self._restart_backoff_until:
            return
        with self._lock:
            self._cleanup_process_state()
            if self.process_alive():
                return
            if self._manual_stop:
                return
            self._restart_backoff_until = time.time() + self._restart_cooldown_sec
            try:
                self.start(self.get_goal())
            except Exception as exc:
                self.runtime_errors.record("mindcraft_auto_restart_failed", exc)
                telemetry = _read_mindcraft_status()
                telemetry["last_error"] = "mindcraft_auto_restart_failed"
                _write_mindcraft_status(telemetry)

    def reconcile_world_lease(
        self,
        *,
        world_action_lock: MinecraftOwnerLock | None = None,
    ) -> bool:
        action_lock = world_action_lock
        release_action_lock = False
        if action_lock is None:
            gateway = globals().get("ACTION_GATEWAY")
            if gateway is not None:
                try:
                    action_lock = gateway.admitted_world_action_lock()
                except Exception:
                    action_lock = None
        if action_lock is not None:
            if (
                not action_lock.acquired
                or action_lock.path != WORLD_ACTION_LOCK_PATH
            ):
                self._last_world_lease_error_code = (
                    "minecraft_world_action_lock_unavailable"
                )
                if self.process_alive() or not self._manual_stop:
                    self.stop()
                return False
        else:
            action_lock = MinecraftOwnerLock(WORLD_ACTION_LOCK_PATH)
            try:
                action_lock.acquire()
                release_action_lock = True
            except MinecraftOwnerLockBusy:
                self._last_world_lease_error_code = (
                    "minecraft_world_action_lock_busy"
                )
                return False
            except (MinecraftOwnerLockUnavailable, OSError):
                self._last_world_lease_error_code = (
                    "minecraft_world_action_lock_unavailable"
                )
                if self.process_alive() or not self._manual_stop:
                    self.stop()
                return False

        try:
            lease_status, error = load_guarded_world_lease(
                WORLD_LEASE_STATUS_PATH,
                WORLD_LEASE_SECRET_PATH,
                owner_claim_path=WORLD_LEASE_OWNER_CLAIM_PATH,
            )
            authorized = bool(lease_status)
            self._last_world_lease_error_code = error
            if not authorized:
                if self.process_alive() or not self._manual_stop:
                    self.stop()
                return False
            self._ensure_process_running()
            return True
        finally:
            if release_action_lock:
                action_lock.release()

    def stop(self) -> None:
        with self._lock:
            try:
                self._manual_stop = True
                process = self._process
                if process is not None and process.poll() is None:
                    process.terminate()
                    try:
                        process.wait(timeout=10)
                    except subprocess.TimeoutExpired:
                        process.kill()
                        process.wait(timeout=5)
                if process is not None:
                    self._last_exit_code = process.poll()
                    if process.poll() is None:
                        raise RuntimeError("mindcraft_stop_unverified")
                self._retire_child_output_drain()
                self._clear_microsoft_auth_challenge()
                # Publish the stopped identity before dropping the only local
                # Popen handle.  Failure leaves that handle available for a
                # cancellation retry and causes the gateway to retain its lock.
                self._write_process_identity(state="stopped")
                self._process_birth_identity = ""
                self._process = None
                self._world_effect_binding = None
                telemetry = _read_mindcraft_status()
                telemetry.update(
                    {
                        "runtime": "mindcraft",
                        "running": False,
                        "connected": False,
                        "connection_state": "stopped",
                        "phase": "stopped",
                        "updated_at": time.time(),
                    }
                )
                _write_mindcraft_status(telemetry)
            except Exception as exc:
                self.runtime_errors.record("mindcraft_stop_failed", exc)
                raise

    def restart_for_goal(self, goal: str) -> None:
        with self._lock:
            was_running = self.process_alive()
            if was_running:
                self.stop()
            self.persist_goal(goal)
            if was_running:
                self.start(goal)

    def restart_for_action(
        self,
        *,
        goal: str,
        world_effect_binding: dict[str, Any],
    ) -> None:
        """Restart Mindcraft with one immutable effect binding.

        The prior telemetry projection is fenced before the new process is
        launched so an old candidate can never be interpreted as belonging
        to the new action.
        """

        with self._lock:
            if self.process_alive() or not self._manual_stop:
                self.stop()
            telemetry = _read_mindcraft_status()
            telemetry.pop("goal_manager", None)
            telemetry.pop("task_contract", None)
            telemetry.update(
                {
                    "runtime": "mindcraft",
                    "running": False,
                    "connected": False,
                    "connection_state": "action_starting",
                    "phase": "action_starting",
                    "updated_at": time.time(),
                }
            )
            _write_mindcraft_status(telemetry)
            self.start(
                goal,
                world_effect_binding=world_effect_binding,
                persist_goal_state=False,
            )

    def build_status(
        self,
        *,
        world_action_lock: MinecraftOwnerLock | None = None,
    ) -> dict[str, Any]:
        world_lease_authorized = self.reconcile_world_lease(
            world_action_lock=world_action_lock,
        )
        process = self._process
        if process is not None and process.poll() is not None:
            self._last_exit_code = process.returncode
        self._cleanup_process_state()
        running = self.process_alive()
        telemetry = _read_mindcraft_status()
        updated_at = telemetry.get("updated_at")
        telemetry_fresh = isinstance(updated_at, (int, float)) and time.time() - float(updated_at) <= 10
        child_runtime = self._child_runtime_snapshot()
        microsoft_auth = self._microsoft_auth_challenge_snapshot(
            running=running,
            connected=telemetry.get("connected") is True,
        )
        connected = _stable_minecraft_connection(
            running=running,
            telemetry_fresh=telemetry_fresh,
            telemetry=telemetry,
            child_runtime=child_runtime,
        )
        goal = self.get_goal()
        observation = {
            "runtime": "mindcraft",
            "connected": connected,
            "active": running,
            "connection_state": telemetry.get("connection_state") or ("starting" if running else "stopped"),
            "position": telemetry.get("position"),
            "health": telemetry.get("health"),
            "hunger": telemetry.get("hunger"),
            "food_saturation": telemetry.get("food_saturation"),
            "inventory": telemetry.get("inventory") if isinstance(telemetry.get("inventory"), dict) else {},
            "hostiles_nearby": telemetry.get("hostiles_nearby") if isinstance(telemetry.get("hostiles_nearby"), list) else [],
            "last_death_event": telemetry.get("last_death_event"),
            "survival_controller": telemetry.get("survival_controller") if isinstance(telemetry.get("survival_controller"), dict) else None,
            "goal_manager": telemetry.get("goal_manager") if isinstance(telemetry.get("goal_manager"), dict) else None,
            "updated_at": updated_at,
        }
        goal_manager = observation["goal_manager"] or {}
        current_subgoal = (
            goal_manager.get("current_subgoal")
            if isinstance(goal_manager.get("current_subgoal"), dict)
            else None
        )
        functional_readiness = _functional_readiness(
            world_lease_authorized=world_lease_authorized,
            running=running,
            telemetry_fresh=telemetry_fresh,
            connected=connected,
            telemetry=telemetry,
            effect_observer_ready=_effect_observer_ready(),
        )
        gateway = globals().get("ACTION_GATEWAY")
        action_gateway = (
            gateway.readiness_projection()
            if gateway is not None
            else {
                "schema": "mindcraft_action_gateway.readiness.v1",
                "state": "unavailable",
                "ready": False,
                "acceptsNewAction": False,
                "active": False,
                "terminalStatus": "",
                "repeatActionReady": False,
                "contentFree": True,
            }
        )
        return {
            "service": "mindcraft_minecraft",
            "runtime": "mindcraft",
            "mode": "survival_non_op",
            "running": running,
            "loop_running": running,
            "connected": connected,
            "minecraft_connected": connected,
            "connection_state": observation["connection_state"],
            "sidecar_process_running": running,
            "goal": goal,
            "goal_override": goal,
            "stage": telemetry.get("phase") or ("starting" if running else "stopped"),
            "current_task": goal if running else None,
            "current_task_stage": current_subgoal.get("id") if current_subgoal else telemetry.get("phase") or None,
            "display_stage": current_subgoal.get("id") if current_subgoal else telemetry.get("phase") or None,
            "current_subgoal": current_subgoal,
            "goal_manager": observation["goal_manager"],
            "last_error": telemetry.get("last_error"),
            "observation": observation,
            "position": observation["position"],
            "health": observation["health"],
            "hunger": observation["hunger"],
            "hostiles_nearby": observation["hostiles_nearby"],
            "survival_controller": observation["survival_controller"],
            "agent_models": {
                "planner": str(_MINDCRAFT_CONFIG["MINDCRAFT_LOCAL_MODEL"]),
                "router": str(_MINDCRAFT_CONFIG["MINDCRAFT_ROUTER_MODEL"]),
                "escalation": (
                    str(_MINDCRAFT_CONFIG["MINDCRAFT_CODEX_MODEL"])
                    if _MINDCRAFT_CONFIG["MINDCRAFT_CODEX_ENABLED"]
                    else str(_MINDCRAFT_CONFIG["MINDCRAFT_LOCAL_MODEL"])
                ),
            },
            "codex_gateway": {
                "enabled": bool(_MINDCRAFT_CONFIG["MINDCRAFT_CODEX_ENABLED"]),
                "url": str(_MINDCRAFT_CONFIG["MINDCRAFT_CODEX_GATEWAY_URL"]),
                "model": str(_MINDCRAFT_CONFIG["MINDCRAFT_CODEX_MODEL"]),
            },
            "command_policy": "outbound_chat_disabled_by_default",
            "blocked_command_count": int(telemetry.get("blocked_command_count") or 0),
            "last_blocked_command": telemetry.get("last_blocked_command"),
            "telemetry_fresh": telemetry_fresh,
            "updated_at": updated_at or self._started_at or time.time(),
            "runner_exit_code": self._last_exit_code,
            "child_runtime": child_runtime,
            **(
                {"microsoft_auth": microsoft_auth}
                if microsoft_auth is not None
                else {}
            ),
            "world_lease_authorized": world_lease_authorized,
            "world_lease_error_code": (
                "" if world_lease_authorized
                else self._last_world_lease_error_code
            ),
            "functional_readiness": functional_readiness,
            "action_gateway_ready": bool(action_gateway["ready"]),
            "action_gateway": action_gateway,
            "configuration": _MINDCRAFT_CONFIG.public_summary(),
            **self.runtime_errors.snapshot(),
            "note": "Evelyn Mindcraft v0.1.4 runtime with non-operator survival policy.",
        }


def _safe_action_code(value: Any, fallback: str) -> str:
    text = str(value or "").strip()
    allowed = (
        "abcdefghijklmnopqrstuvwxyz"
        "ABCDEFGHIJKLMNOPQRSTUVWXYZ"
        "0123456789:_-."
    )
    if not text or len(text) > 128 or any(char not in allowed for char in text):
        return fallback
    return text


def _archive_parent_record_id(value: Any) -> str:
    text = str(value or "").strip()
    if (
        not text
        or len(text) > 64
        or not text[0].isalnum()
        or not text[0].isascii()
        or _safe_action_code(text, "") != text
    ):
        return ""
    return text


def _archive_parent_record_ids(value: Any) -> tuple[str, ...]:
    if value in (None, (), []):
        return ()
    if not isinstance(value, (list, tuple)) or len(value) != 1:
        raise ValueError("minecraft_archive_lineage_invalid")
    parent = _archive_parent_record_id(value[0])
    if not parent:
        raise ValueError("minecraft_archive_lineage_invalid")
    return (parent,)


def _request_from_action_projection(value: dict[str, Any]) -> dict[str, Any]:
    return validate_minecraft_action_request(
        {
            "schema": MINECRAFT_ACTION_REQUEST_SCHEMA,
            "guildId": value.get("guildId"),
            "actionKey": value.get("actionKey"),
            "actionRunId": value.get("actionRunId"),
            "authorizationGrantId": value.get("authorizationGrantId"),
            "contractCode": value.get("contractCode"),
            "parameters": {},
            "goalRunId": value.get("goalRunId"),
            "leaseId": value.get("leaseId"),
            "leaseProcessNonce": value.get("leaseProcessNonce"),
        },
        bound=True,
    )


def _action_ack(
    request: dict[str, Any],
    *,
    status: str,
    error_code: str = "",
) -> dict[str, Any]:
    accepted = status in {"accepted", "running"}
    result = {
        "schema": MINECRAFT_ACTION_DISPATCH_SCHEMA,
        "status": status,
        "accepted": accepted,
        **{
            key: request[key]
            for key in (
                "guildId",
                "actionKey",
                "actionRunId",
                "authorizationGrantId",
                "goalRunId",
                "leaseId",
                "leaseProcessNonce",
                "contractCode",
            )
        },
        "errorCode": "",
        "contentFree": True,
    }
    if status in {"failed", "cancelled"}:
        result["errorCode"] = _safe_action_code(
            error_code,
            "minecraft_action_failed",
        )
    return result


def _action_result(request: dict[str, Any]) -> dict[str, Any]:
    spec = MINECRAFT_ACTION_SPECS[request["actionKey"]]
    return {
        "schema": MINECRAFT_ACTION_RESULT_SCHEMA,
        "status": "completed",
        **{
            key: request[key]
            for key in (
                "guildId",
                "actionKey",
                "actionRunId",
                "authorizationGrantId",
                "goalRunId",
                "leaseId",
                "leaseProcessNonce",
                "contractCode",
            )
        },
        "postconditionCode": spec.postcondition_code,
        "evidenceCode": spec.evidence_code,
        "verified": True,
        "contentFree": True,
    }


class MindcraftActionGateway:
    """Own one admitted action from dispatch through a verified terminal edge."""

    def __init__(
        self,
        *,
        runtime: MindcraftRuntime,
        projector: MindcraftWorldEffectProjector,
        status_path: Path = ACTION_GATEWAY_STATUS_PATH,
        timeout_sec: float = ACTION_TIMEOUT_SEC,
    ) -> None:
        self.runtime = runtime
        self.projector = projector
        self.status_path = Path(status_path)
        self.timeout_sec = max(1.0, float(timeout_sec))
        self._lock = threading.RLock()
        self._available = True
        self._last_error_code = ""
        self._active_request: dict[str, Any] | None = None
        self._active_archive_parent_record_ids: tuple[str, ...] = ()
        self._active_binding: dict[str, Any] | None = None
        self._active_deadline = 0.0
        self._active_readiness_observed = False
        self._action_lock: MinecraftOwnerLock | None = None
        self._terminal_ready_current_process = False
        self._repeat_arm_admission = False
        self._records: OrderedDict[str, dict[str, Any]] = OrderedDict()
        self._seen_goal_run_ids: set[str] = set()
        self._seen_action_run_ids: set[str] = set()
        self._load_replay_fence()
        self._load_projector_replay_fence()

    def available(self) -> bool:
        with self._lock:
            if not self._available:
                return False
            try:
                observer = self.projector.status()
                archive_ready = getattr(
                    self.projector,
                    "archive_ready",
                    None,
                )
                archive_operational = bool(
                    not callable(archive_ready)
                    or archive_ready() is True
                )
            except Exception:
                return False
            return bool(
                archive_operational
                and observer.get("auditReady") is True
                and observer.get("statusReady") is True
                and observer.get("state")
                in {"idle", "armed", "verified", "rejected"}
            )

    def admitted_world_action_lock(self) -> MinecraftOwnerLock | None:
        with self._lock:
            if (
                self._active_request is not None
                and self._action_lock is not None
                and self._action_lock.acquired
                and self._action_lock.path == WORLD_ACTION_LOCK_PATH
            ):
                return self._action_lock
            return None

    def request_for_binding(
        self,
        binding: dict[str, Any],
    ) -> dict[str, Any] | None:
        with self._lock:
            request = self._active_request
            if request is None:
                return None
            for key in (
                "goalRunId",
                "actionRunId",
                "actionKey",
                "contractCode",
                "leaseId",
                "leaseProcessNonce",
            ):
                if binding.get(key) != request.get(key):
                    return None
            return deepcopy(request)

    def repeat_arm_admitted(
        self,
        binding: dict[str, Any],
    ) -> bool:
        with self._lock:
            if (
                not self._repeat_arm_admission
                or self._active_request is None
                or self._action_lock is None
                or not self._action_lock.acquired
                or self._action_lock.path != WORLD_ACTION_LOCK_PATH
                or self.runtime.process_alive()
            ):
                return False
            return self.request_for_binding(binding) is not None

    def readiness_projection(self) -> dict[str, Any]:
        with self._lock:
            operational = self.available()
            terminal_status = ""
            if self._records:
                latest = next(reversed(self._records.values()))
                terminal_status = str(latest.get("status") or "")
                if terminal_status in {"accepted", "running"}:
                    terminal_status = ""
            state = (
                "unavailable"
                if not operational
                else "running"
                if self._active_request is not None
                else "terminal"
                if terminal_status
                else "idle"
            )
            return {
                "schema": "mindcraft_action_gateway.readiness.v1",
                "state": state,
                "ready": operational,
                "acceptsNewAction": bool(
                    operational and self._active_request is None
                ),
                "active": self._active_request is not None,
                "terminalStatus": terminal_status,
                "repeatActionReady": bool(
                    operational
                    and self._active_request is None
                    and self._terminal_ready_current_process
                ),
                "contentFree": True,
            }

    @staticmethod
    def _record_valid(record: Any) -> bool:
        if not isinstance(record, dict) or record.get("contentFree") is not True:
            return False
        try:
            request = _request_from_action_projection(record)
        except MinecraftActionContractError:
            return False
        if record.get("schema") == MINECRAFT_ACTION_RESULT_SCHEMA:
            return record == _action_result(request)
        if record.get("schema") != MINECRAFT_ACTION_DISPATCH_SCHEMA:
            return False
        status = record.get("status")
        if status not in {"accepted", "running", "failed", "cancelled"}:
            return False
        expected = _action_ack(
            request,
            status=str(status),
            error_code=str(record.get("errorCode") or ""),
        )
        return record == expected

    def _status_payload(self) -> dict[str, Any]:
        return {
            "schema": ACTION_GATEWAY_STATUS_SCHEMA,
            "updatedAt": time.time(),
            "available": self._available,
            "lastErrorCode": self._last_error_code,
            "activeGoalRunId": (
                self._active_request["goalRunId"]
                if self._active_request is not None
                else ""
            ),
            "records": [deepcopy(item) for item in self._records.values()],
            "contentFree": True,
        }

    def _persist(self) -> bool:
        try:
            atomic_json_write(
                self.status_path,
                self._status_payload(),
                durable=True,
            )
            return True
        except (OSError, TypeError, ValueError):
            self._available = False
            self._last_error_code = "minecraft_action_status_write_failed"
            return False

    def _remember(self, record: dict[str, Any]) -> None:
        goal_run_id = str(record["goalRunId"])
        self._records.pop(goal_run_id, None)
        self._records[goal_run_id] = deepcopy(record)
        while len(self._records) > ACTION_RECORD_LIMIT:
            self._records.popitem(last=False)
        self._seen_goal_run_ids.add(goal_run_id)
        self._seen_action_run_ids.add(str(record["actionRunId"]))

    def _load_replay_fence(self) -> None:
        try:
            payload = json.loads(self.status_path.read_text(encoding="utf-8"))
        except FileNotFoundError:
            self._persist()
            return
        except (json.JSONDecodeError, OSError, UnicodeError):
            self._available = False
            self._last_error_code = "minecraft_action_status_invalid"
            return
        expected_keys = {
            "schema",
            "updatedAt",
            "available",
            "lastErrorCode",
            "activeGoalRunId",
            "records",
            "contentFree",
        }
        if (
            not isinstance(payload, dict)
            or set(payload) != expected_keys
            or payload.get("schema") != ACTION_GATEWAY_STATUS_SCHEMA
            or type(payload.get("available")) is not bool
            or not isinstance(payload.get("records"), list)
            or payload.get("contentFree") is not True
            or any(not self._record_valid(row) for row in payload["records"])
            or isinstance(payload.get("updatedAt"), bool)
            or not isinstance(payload.get("updatedAt"), (int, float))
            or (
                str(payload.get("lastErrorCode") or "")
                and not _safe_action_code(payload.get("lastErrorCode"), "")
            )
            or (
                str(payload.get("activeGoalRunId") or "")
                and not _safe_action_code(payload.get("activeGoalRunId"), "")
            )
        ):
            self._available = False
            self._last_error_code = "minecraft_action_status_invalid"
            return
        self._available = bool(payload["available"])
        self._last_error_code = _safe_action_code(
            payload.get("lastErrorCode"),
            "",
        )
        goal_ids = [str(row["goalRunId"]) for row in payload["records"]]
        action_ids = [str(row["actionRunId"]) for row in payload["records"]]
        if len(set(goal_ids)) != len(goal_ids) or len(set(action_ids)) != len(action_ids):
            self._available = False
            self._last_error_code = "minecraft_action_status_invalid"
            return
        retained_records = payload["records"][-ACTION_RECORD_LIMIT:]
        inflight_restart = any(
            record.get("status") in {"accepted", "running"}
            for record in retained_records
        )
        restart_reconciled = True
        restart_error = ""
        if inflight_restart:
            reconciler = getattr(
                self.runtime,
                "reconcile_inflight_restart",
                None,
            )
            if not callable(reconciler):
                restart_reconciled = False
                restart_error = (
                    "minecraft_prior_process_identity_unavailable"
                )
            else:
                try:
                    outcome = reconciler()
                except Exception:
                    outcome = (
                        False,
                        "minecraft_prior_process_identity_unverified",
                    )
                if (
                    not isinstance(outcome, tuple)
                    or len(outcome) != 2
                    or type(outcome[0]) is not bool
                    or not isinstance(outcome[1], str)
                ):
                    restart_reconciled = False
                    restart_error = (
                        "minecraft_prior_process_identity_unverified"
                    )
                else:
                    restart_reconciled = outcome[0]
                    restart_error = _safe_action_code(
                        outcome[1],
                        "minecraft_prior_process_identity_unverified",
                    ) if not restart_reconciled else ""
        for record in retained_records:
            request = _request_from_action_projection(record)
            if (
                restart_reconciled
                and record.get("status") in {"accepted", "running"}
            ):
                record = _action_ack(
                    request,
                    status="failed",
                    error_code="minecraft_action_authority_lost_on_restart",
                )
            self._remember(record)
        if not restart_reconciled:
            self._available = False
            self._last_error_code = restart_error
            self._terminal_ready_current_process = False
            self._persist()
            return
        if self._available and not self._persist():
            self._available = False

    def _load_projector_replay_fence(self) -> None:
        if not self._available:
            return
        try:
            event_paths = sorted(WORLD_EFFECT_EVENTS_DIR.glob("*.jsonl"))
            for event_path in event_paths:
                for line in event_path.read_text(encoding="utf-8").splitlines():
                    if not line.strip():
                        continue
                    event = json.loads(line)
                    if (
                        not isinstance(event, dict)
                        or event.get("schema")
                        != MINDCRAFT_WORLD_EFFECT_EVENT_SCHEMA
                        or event.get("contentFree") is not True
                    ):
                        raise ValueError("invalid effect event")
                    for key, target in (
                        ("goalRunId", self._seen_goal_run_ids),
                        ("actionRunId", self._seen_action_run_ids),
                    ):
                        value = str(event.get(key) or "").strip()
                        if value:
                            safe = _safe_action_code(value, "")
                            if not safe:
                                raise ValueError("invalid effect identity")
                            target.add(safe)
        except (OSError, UnicodeError, json.JSONDecodeError, ValueError):
            self._available = False
            self._last_error_code = "minecraft_action_replay_fence_invalid"
            self._persist()

    def _release_action_lock(self) -> None:
        action_lock = self._action_lock
        self._action_lock = None
        if action_lock is not None:
            action_lock.release()

    def _clear_active(self) -> None:
        self._active_request = None
        self._active_archive_parent_record_ids = ()
        self._active_binding = None
        self._active_deadline = 0.0
        self._active_readiness_observed = False
        self._repeat_arm_admission = False

    def _quarantine_unverified_stop(self, code: str) -> None:
        """Retain the admission fence until the child is proven stopped.

        A terminal action record is authority-bearing: publishing it and
        releasing ``world_action.lock`` would allow another action to start.
        If stopping the Mindcraft child raises, or the child still reports
        alive afterwards, keep the active binding and OS lock owned by this
        gateway and require manual intervention.  A later exact cancellation
        or guard pass may retry the stop, but no new dispatch is admitted.
        """

        self._available = False
        self._last_error_code = _safe_action_code(
            code,
            "minecraft_action_stop_failed",
        )
        self._terminal_ready_current_process = False
        self._repeat_arm_admission = False
        self._persist()

    def _stop_runtime_verified(self) -> None:
        try:
            self.runtime.stop()
        except Exception as exc:
            self._quarantine_unverified_stop(
                "minecraft_action_stop_failed"
            )
            raise RuntimeError(
                self._last_error_code
                or "minecraft_action_stop_failed"
            ) from exc
        try:
            still_alive = self.runtime.process_alive()
        except Exception as exc:
            self._quarantine_unverified_stop(
                "minecraft_action_stop_unverified"
            )
            raise RuntimeError(
                self._last_error_code
                or "minecraft_action_stop_unverified"
            ) from exc
        if still_alive:
            self._quarantine_unverified_stop(
                "minecraft_action_stop_unverified"
            )
            raise RuntimeError(
                self._last_error_code
                or "minecraft_action_stop_unverified"
            )

    def _terminal_failure(self, code: str) -> dict[str, Any]:
        request = self._active_request
        if request is None:
            raise RuntimeError("minecraft_action_not_active")
        safe_code = _safe_action_code(code, "minecraft_action_failed")
        quarantine_error = (
            self._last_error_code if not self._available else ""
        )
        try:
            self.projector.disarm(safe_code)
        except Exception:
            pass
        self._stop_runtime_verified()
        try:
            record = _action_ack(
                request,
                status="failed",
                error_code=safe_code,
            )
            self._remember(record)
            self._last_error_code = quarantine_error or safe_code
            self._terminal_ready_current_process = self._available
            self._clear_active()
            if not self._persist():
                record = _action_ack(
                    request,
                    status="failed",
                    error_code="minecraft_action_status_write_failed",
                )
                self._remember(record)
            return deepcopy(record)
        finally:
            self._release_action_lock()

    def _terminal_verified(self) -> dict[str, Any]:
        request = self._active_request
        if request is None:
            raise RuntimeError("minecraft_action_not_active")
        quarantine_error = (
            self._last_error_code if not self._available else ""
        )
        self._stop_runtime_verified()
        try:
            record = _action_result(request)
            self._remember(record)
            self._last_error_code = quarantine_error
            self._terminal_ready_current_process = self._available
            self._clear_active()
            if not self._persist():
                try:
                    self.runtime.stop()
                except Exception:
                    pass
                failed = _action_ack(
                    request,
                    status="failed",
                    error_code="minecraft_action_status_write_failed",
                )
                self._remember(failed)
                return deepcopy(failed)
            return deepcopy(record)
        finally:
            self._release_action_lock()

    def dispatch(
        self,
        request: dict[str, Any],
        *,
        action_lock: MinecraftOwnerLock,
        preflight_status: dict[str, Any],
        archive_parent_record_ids: tuple[str, ...] = (),
    ) -> dict[str, Any]:
        with self._lock:
            normalized = validate_minecraft_action_request(
                request,
                bound=True,
            )
            if len(archive_parent_record_ids) > 1 or any(
                not _archive_parent_record_id(parent)
                for parent in archive_parent_record_ids
            ):
                raise RuntimeError("minecraft_archive_lineage_invalid")
            if not self.available():
                raise RuntimeError(
                    self._last_error_code
                    or "minecraft_action_gateway_unavailable"
                )
            if self._active_request is not None:
                raise RuntimeError("minecraft_action_already_running")
            if (
                normalized["goalRunId"] in self._seen_goal_run_ids
                or normalized["actionRunId"] in self._seen_action_run_ids
            ):
                raise RuntimeError("minecraft_action_replay_rejected")
            if (
                not action_lock.acquired
                or action_lock.path != WORLD_ACTION_LOCK_PATH
            ):
                raise RuntimeError("minecraft_world_action_lock_unavailable")
            readiness, readiness_state = validate_minecraft_autonomy_readiness(
                preflight_status
            )
            if (
                not self._terminal_ready_current_process
                and (
                    readiness_state != "valid"
                    or readiness is None
                    or readiness.get("ready") is not True
                )
            ):
                raise RuntimeError("minecraft_runtime_not_ready")
            binding = {
                "schema": MINDCRAFT_WORLD_EFFECT_BINDING_SCHEMA,
                **{
                    key: normalized[key]
                    for key in (
                        "goalRunId",
                        "actionRunId",
                        "actionKey",
                        "contractCode",
                        "leaseId",
                        "leaseProcessNonce",
                    )
                },
                "producerNonce": secrets.token_hex(16),
                "candidateSequence": 1,
                "contentFree": True,
            }
            self._active_request = normalized
            self._active_archive_parent_record_ids = tuple(
                archive_parent_record_ids
            )
            self._active_binding = binding
            self._active_deadline = time.monotonic() + self.timeout_sec
            self._active_readiness_observed = False
            repeat_admission = self._terminal_ready_current_process
            self._terminal_ready_current_process = False
            self._repeat_arm_admission = repeat_admission
            self._action_lock = action_lock
            accepted = _action_ack(normalized, status="accepted")
            self._remember(accepted)
            if not self._persist():
                self._clear_active()
                self._release_action_lock()
                raise RuntimeError("minecraft_action_status_write_failed")
            try:
                armed = self.projector.arm(binding)
            except Exception:
                self._repeat_arm_admission = False
                return self._terminal_failure(
                    "minecraft_action_arm_failed"
                )
            self._repeat_arm_admission = False
            if not isinstance(armed, dict) or armed.get("accepted") is not True:
                return self._terminal_failure(
                    str((armed or {}).get("code") or "minecraft_action_arm_failed")
                )
            try:
                self.runtime.restart_for_action(
                    goal=_FOOD_RECOVERY_GOAL,
                    world_effect_binding=binding,
                )
            except Exception:
                return self._terminal_failure("minecraft_action_start_failed")
            running = _action_ack(normalized, status="running")
            self._remember(running)
            if not self._persist():
                return self._terminal_failure(
                    "minecraft_action_status_write_failed"
                )
            return deepcopy(accepted)

    def poll(self) -> dict[str, Any] | None:
        with self._lock:
            request = self._active_request
            if request is None:
                return None
            lease, lease_error = _load_exact_action_lease(request)
            if lease is None:
                return self._terminal_failure(lease_error)
            if time.monotonic() >= self._active_deadline:
                return self._terminal_failure("minecraft_action_timeout")
            if not self.runtime.process_alive():
                return self._terminal_failure(
                    "minecraft_action_runtime_stopped"
                )
            telemetry = _read_json(STATUS_PATH)
            goal_manager = telemetry.get("goal_manager")
            candidate: Any = None
            if isinstance(goal_manager, dict):
                candidate = goal_manager.get("postcondition_candidate")
            if candidate is not None:
                observed = self.projector.observe(
                    candidate,
                    archive_context={
                        **request,
                        **(
                            {
                                "parentRecordIds": list(
                                    self._active_archive_parent_record_ids
                                )
                            }
                            if self._active_archive_parent_record_ids
                            else {}
                        ),
                    },
                )
                if isinstance(observed, dict) and observed.get("verified") is True:
                    return self._terminal_verified()
                return self._terminal_failure(
                    str((observed or {}).get("code") or "minecraft_action_effect_rejected")
                )
            guard_error = self.projector.active_guard_error()
            if not guard_error:
                self._active_readiness_observed = True
            elif (
                self._active_readiness_observed
                or guard_error != "minecraft_runtime_not_ready"
            ):
                return self._terminal_failure(guard_error)
            return deepcopy(self._records[request["goalRunId"]])

    def get_status(self, goal_run_id: str) -> dict[str, Any] | None:
        with self._lock:
            self.poll()
            record = self._records.get(str(goal_run_id or ""))
            return deepcopy(record) if record is not None else None

    def cancel(self, request: dict[str, Any]) -> dict[str, Any]:
        with self._lock:
            normalized = validate_minecraft_action_request(
                request,
                bound=True,
            )
            active = self._active_request
            if active is None:
                if not self.available():
                    raise RuntimeError(
                        self._last_error_code
                        or "minecraft_action_gateway_unavailable"
                    )
                existing = self._records.get(normalized["goalRunId"])
                if existing is not None:
                    return deepcopy(existing)
                raise RuntimeError("minecraft_action_not_active")
            if normalized != active:
                raise RuntimeError("minecraft_action_cancel_mismatch")
            quarantine_error = (
                self._last_error_code if not self._available else ""
            )
            try:
                self.projector.disarm("minecraft_action_cancelled")
            except Exception:
                pass
            self._stop_runtime_verified()
            record = _action_ack(
                active,
                status="cancelled",
                error_code="minecraft_action_cancelled",
            )
            self._remember(record)
            self._last_error_code = (
                quarantine_error or "minecraft_action_cancelled"
            )
            self._terminal_ready_current_process = self._available
            self._clear_active()
            try:
                if not self._persist():
                    record = _action_ack(
                        active,
                        status="failed",
                        error_code="minecraft_action_status_write_failed",
                    )
                    self._remember(record)
                return deepcopy(record)
            finally:
                self._release_action_lock()

    def fail_closed(self, code: str) -> dict[str, Any] | None:
        with self._lock:
            request = self._active_request
            if request is None:
                return None
            if not self._available:
                record = self._records.get(request["goalRunId"])
                return deepcopy(record) if record is not None else None
            return self._terminal_failure(code)

    def shutdown(self) -> None:
        with self._lock:
            if self._active_request is not None:
                self._terminal_failure(
                    "minecraft_action_authority_lost_on_restart"
                )
            else:
                self._release_action_lock()


STATE = MindcraftRuntime()


def _load_exact_action_lease(
    request: dict[str, Any],
) -> tuple[dict[str, Any] | None, str]:
    lease_status, error = load_guarded_world_lease(
        WORLD_LEASE_STATUS_PATH,
        WORLD_LEASE_SECRET_PATH,
        owner_claim_path=WORLD_LEASE_OWNER_CLAIM_PATH,
    )
    if error or not isinstance(lease_status, dict):
        return None, error or "minecraft_world_authorization_required"
    lease = lease_status.get("lease")
    if not isinstance(lease, dict):
        return None, "minecraft_world_authorization_required"
    if lease.get("guildId") != request.get("guildId"):
        return None, "minecraft_world_lease_owner_mismatch"
    if (
        lease.get("leaseId") != request.get("leaseId")
        or lease_status.get("processNonce")
        != request.get("leaseProcessNonce")
    ):
        return None, "minecraft_world_lease_changed"
    return lease_status, ""


def _effect_guarded_lease(
    binding: dict[str, Any],
) -> tuple[bool, str]:
    gateway = globals().get("ACTION_GATEWAY")
    request = (
        gateway.request_for_binding(binding)
        if gateway is not None
        else None
    )
    if request is None:
        return False, "minecraft_action_binding_unowned"
    lease, error = _load_exact_action_lease(request)
    return bool(lease is not None), error


def _effect_guarded_readiness(
    binding: dict[str, Any],
) -> tuple[bool, str]:
    gateway = globals().get("ACTION_GATEWAY")
    request = (
        gateway.request_for_binding(binding)
        if gateway is not None
        else None
    )
    if request is None:
        return False, "minecraft_action_binding_unowned"
    lease_status, lease_error = _load_exact_action_lease(request)
    if lease_status is None:
        return False, lease_error
    telemetry = _read_json(STATUS_PATH)
    updated_at = telemetry.get("updated_at")
    telemetry_fresh = bool(
        isinstance(updated_at, (int, float))
        and time.time() - float(updated_at) <= 10.0
    )
    running = STATE.process_alive()
    connected = _stable_minecraft_connection(
        running=running,
        telemetry_fresh=telemetry_fresh,
        telemetry=telemetry,
        child_runtime=STATE._child_runtime_snapshot(),
    )
    readiness = _functional_readiness(
        world_lease_authorized=True,
        running=running,
        telemetry_fresh=telemetry_fresh,
        connected=connected,
        telemetry=telemetry,
        effect_observer_ready=True,
    )
    if readiness.get("ready") is True:
        return True, ""
    dependencies = readiness.get("dependencies")
    goal_manager = telemetry.get("goal_manager")
    candidate = (
        goal_manager.get("postcondition_candidate")
        if isinstance(goal_manager, dict)
        else None
    )
    base_dependencies_ready = bool(
        isinstance(dependencies, dict)
        and all(
            dependencies.get(name) is True
            for name in MINECRAFT_READINESS_DEPENDENCIES
            if name != "autonomyActive"
        )
    )
    identity_matches = bool(
        isinstance(candidate, dict)
        and candidate.get("schema")
        == "mindcraft.postcondition-candidate.v1"
        and all(
            candidate.get(key) == binding.get(key)
            for key in (
                "goalRunId",
                "actionRunId",
                "actionKey",
                "contractCode",
                "leaseId",
                "leaseProcessNonce",
                "producerNonce",
            )
        )
    )
    exact_candidate_pause = bool(
        isinstance(goal_manager, dict)
        and goal_manager.get("autonomy_state") == "manual_pause"
        and goal_manager.get("manual_pause_reason")
        == "world_effect_candidate_published"
        and identity_matches
    )
    if base_dependencies_ready and exact_candidate_pause:
        return True, ""
    if gateway is not None and gateway.repeat_arm_admitted(binding):
        return True, ""
    return False, "minecraft_runtime_not_ready"


WORLD_EFFECT_PROJECTOR = MindcraftWorldEffectProjector(
    status_path=WORLD_EFFECT_STATUS_PATH,
    events_dir=WORLD_EFFECT_EVENTS_DIR,
    validate_guarded_lease=_effect_guarded_lease,
    validate_readiness=_effect_guarded_readiness,
)
ACTION_GATEWAY = MindcraftActionGateway(
    runtime=STATE,
    projector=WORLD_EFFECT_PROJECTOR,
)


async def health(_: web.Request) -> web.Response:
    runtime_status = STATE.build_status()
    return web.json_response(
        {
            "ok": True,
            "service": "mindcraft_minecraft",
            "runtime": "mindcraft",
            "runner_alive": bool(
                runtime_status.get("running")
            ),
            "functional_readiness": runtime_status.get(
                "functional_readiness"
            ),
            "configuration": _MINDCRAFT_CONFIG.public_summary(),
            **STATE.runtime_errors.snapshot(),
        }
    )


async def status(_: web.Request) -> web.Response:
    return web.json_response(STATE.build_status())


async def observe(_: web.Request) -> web.Response:
    return web.json_response(STATE.build_status().get("observation") or {})


def _acquire_world_action_lock() -> MinecraftOwnerLock:
    action_lock = MinecraftOwnerLock(WORLD_ACTION_LOCK_PATH)
    try:
        action_lock.acquire()
    except MinecraftOwnerLockBusy:
        raise web.HTTPServiceUnavailable(
            text=json.dumps(
                {"error": "minecraft_world_action_lock_busy"}
            ),
            content_type="application/json",
        ) from None
    except (MinecraftOwnerLockUnavailable, OSError):
        raise web.HTTPServiceUnavailable(
            text=json.dumps(
                {
                    "error": (
                        "minecraft_world_action_lock_unavailable"
                    )
                }
            ),
            content_type="application/json",
        ) from None
    return action_lock


async def start(request: web.Request) -> web.Response:
    payload = await request.json() if request.can_read_body else {}
    action_lock = _acquire_world_action_lock()
    try:
        valid, error = validate_world_lease_request(
            payload,
            status_path=WORLD_LEASE_STATUS_PATH,
            secret_path=WORLD_LEASE_SECRET_PATH,
            owner_claim_path=WORLD_LEASE_OWNER_CLAIM_PATH,
        )
        if not valid:
            raise web.HTTPForbidden(
                text=json.dumps({"error": error}),
                content_type="application/json",
            )
        STATE.start(
            _clean_goal((payload or {}).get("goal") or STATE.get_goal())
        )
        return web.json_response(
            STATE.build_status(world_action_lock=action_lock)
        )
    finally:
        action_lock.release()


async def stop(_: web.Request) -> web.Response:
    if ACTION_GATEWAY.admitted_world_action_lock() is not None:
        raise _http_json_error(
            web.HTTPServiceUnavailable,
            "minecraft_world_action_lock_busy",
        )
    STATE.stop()
    return web.json_response(STATE.build_status())


async def set_goal(request: web.Request) -> web.Response:
    payload = await request.json() if request.can_read_body else {}
    action_lock = _acquire_world_action_lock()
    try:
        valid, error = validate_world_lease_request(
            payload,
            status_path=WORLD_LEASE_STATUS_PATH,
            secret_path=WORLD_LEASE_SECRET_PATH,
            owner_claim_path=WORLD_LEASE_OWNER_CLAIM_PATH,
        )
        if not valid:
            raise web.HTTPForbidden(
                text=json.dumps({"error": error}),
                content_type="application/json",
            )
        goal = str((payload or {}).get("goal") or "").strip()
        if not goal:
            raise web.HTTPBadRequest(
                text=json.dumps({"error": "goal text is empty"}),
                content_type="application/json",
            )
        STATE.restart_for_goal(goal)
        return web.json_response(
            STATE.build_status(world_action_lock=action_lock)
        )
    finally:
        action_lock.release()


async def archive_lifecycle_result(request: web.Request) -> web.Response:
    try:
        payload = await request.json()
    except (json.JSONDecodeError, UnicodeError, ValueError):
        raise _http_json_error(
            web.HTTPBadRequest,
            "minecraft_archive_result_payload_invalid",
        ) from None
    if not isinstance(payload, dict) or set(payload) != {
        "guildId",
        "parentRecordIds",
        "operation",
        "outcomeCode",
    }:
        raise _http_json_error(
            web.HTTPBadRequest,
            "minecraft_archive_result_payload_invalid",
        )
    guild_id = payload.get("guildId")
    if isinstance(guild_id, bool) or not isinstance(guild_id, int) or guild_id <= 0:
        raise _http_json_error(
            web.HTTPBadRequest,
            "minecraft_archive_result_payload_invalid",
        )
    try:
        parent_record_ids = _archive_parent_record_ids(
            payload.get("parentRecordIds")
        )
    except ValueError:
        raise _http_json_error(
            web.HTTPBadRequest,
            "minecraft_archive_lineage_invalid",
        ) from None
    archived, error = WORLD_EFFECT_PROJECTOR.archive_verified_lifecycle(
        guild_id=guild_id,
        parent_record_ids=parent_record_ids,
        operation=str(payload.get("operation") or ""),
        outcome_code=str(payload.get("outcomeCode") or ""),
    )
    if not archived:
        raise _http_json_error(
            web.HTTPServiceUnavailable,
            error or "mindcraft_world_effect_archive_unavailable",
        )
    return web.json_response(
        {"archived": True, "contentFree": True}
    )


def _http_json_error(
    status_type: type[web.HTTPException],
    code: str,
) -> web.HTTPException:
    return status_type(
        text=json.dumps({"error": code}),
        content_type="application/json",
    )


def _validate_action_payload(
    payload: Any,
) -> dict[str, Any]:
    if not isinstance(payload, dict) or set(payload) != {
        "request",
        "worldLease",
    }:
        raise _http_json_error(
            web.HTTPBadRequest,
            "minecraft_action_payload_invalid",
        )
    try:
        action_request = validate_minecraft_action_request(
            payload.get("request"),
            bound=True,
        )
    except MinecraftActionContractError as exc:
        raise _http_json_error(web.HTTPBadRequest, exc.code) from None
    valid, error = validate_world_lease_request(
        payload,
        status_path=WORLD_LEASE_STATUS_PATH,
        secret_path=WORLD_LEASE_SECRET_PATH,
        owner_claim_path=WORLD_LEASE_OWNER_CLAIM_PATH,
    )
    if not valid:
        raise _http_json_error(web.HTTPForbidden, error)
    lease, lease_error = _load_exact_action_lease(action_request)
    if lease is None:
        raise _http_json_error(web.HTTPForbidden, lease_error)
    return action_request


async def dispatch_action(request: web.Request) -> web.Response:
    try:
        payload = await request.json()
    except (json.JSONDecodeError, UnicodeError, ValueError):
        raise _http_json_error(
            web.HTTPBadRequest,
            "minecraft_action_payload_invalid",
        ) from None
    action_lock = _acquire_world_action_lock()
    try:
        action_request = _validate_action_payload(payload)
        archive_parent_record_ids = _archive_parent_record_ids(
            (payload.get("worldLease") or {}).get("parentRecordIds")
        )
        preflight = STATE.build_status(world_action_lock=action_lock)
        try:
            result = ACTION_GATEWAY.dispatch(
                action_request,
                action_lock=action_lock,
                preflight_status=preflight,
                archive_parent_record_ids=archive_parent_record_ids,
            )
        except MinecraftActionContractError as exc:
            raise _http_json_error(web.HTTPBadRequest, exc.code) from None
        except RuntimeError as exc:
            code = _safe_action_code(
                exc,
                "minecraft_action_gateway_unavailable",
            )
            status_type = (
                web.HTTPConflict
                if code
                in {
                    "minecraft_action_already_running",
                    "minecraft_action_replay_rejected",
                }
                else web.HTTPServiceUnavailable
            )
            raise _http_json_error(status_type, code) from None
        return web.json_response(result)
    finally:
        admitted = ACTION_GATEWAY.admitted_world_action_lock()
        if action_lock.acquired and admitted is not action_lock:
            action_lock.release()


async def action_status(request: web.Request) -> web.Response:
    goal_run_id = _safe_action_code(
        request.match_info.get("goal_run_id"),
        "",
    )
    if not goal_run_id:
        raise _http_json_error(
            web.HTTPBadRequest,
            "minecraft_goal_run_id_invalid",
        )
    try:
        result = ACTION_GATEWAY.get_status(goal_run_id)
    except Exception:
        ACTION_GATEWAY.fail_closed("minecraft_action_guard_failed")
        raise _http_json_error(
            web.HTTPServiceUnavailable,
            "minecraft_action_guard_failed",
        ) from None
    if result is None:
        raise _http_json_error(
            web.HTTPNotFound,
            "minecraft_action_not_found",
        )
    return web.json_response(result)


async def cancel_action(request: web.Request) -> web.Response:
    try:
        payload = await request.json()
    except (json.JSONDecodeError, UnicodeError, ValueError):
        raise _http_json_error(
            web.HTTPBadRequest,
            "minecraft_action_payload_invalid",
        ) from None
    action_request = _validate_action_payload(payload)
    try:
        result = ACTION_GATEWAY.cancel(action_request)
    except MinecraftActionContractError as exc:
        raise _http_json_error(web.HTTPBadRequest, exc.code) from None
    except RuntimeError as exc:
        code = _safe_action_code(exc, "minecraft_action_cancel_failed")
        raise _http_json_error(web.HTTPConflict, code) from None
    return web.json_response(result)


async def _cleanup(_: web.Application) -> None:
    ACTION_GATEWAY.shutdown()
    STATE.stop()


async def _world_lease_guard_context(_: web.Application):
    async def guard_loop() -> None:
        while True:
            await asyncio.sleep(WORLD_LEASE_GUARD_INTERVAL_SEC)
            try:
                STATE.reconcile_world_lease()
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                STATE.runtime_errors.record(
                    "mindcraft_world_lease_guard_failed",
                    exc,
                )

    task = asyncio.create_task(guard_loop())
    try:
        yield
    finally:
        task.cancel()
        try:
            await task
        except asyncio.CancelledError:
            pass


async def _action_guard_context(_: web.Application):
    async def guard_loop() -> None:
        while True:
            await asyncio.sleep(ACTION_GUARD_INTERVAL_SEC)
            try:
                ACTION_GATEWAY.poll()
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                STATE.runtime_errors.record(
                    "mindcraft_action_guard_failed",
                    exc,
                )
                ACTION_GATEWAY.fail_closed(
                    "minecraft_action_guard_failed"
                )

    task = asyncio.create_task(guard_loop())
    try:
        yield
    finally:
        task.cancel()
        try:
            await task
        except asyncio.CancelledError:
            pass
        ACTION_GATEWAY.shutdown()


def build_app(
    *,
    conversation_archive_enabled: bool = False,
    archive_verified_world_effect: ArchiveSink | None = None,
    validate_conversation_archive_ready: ArchiveReadiness | None = None,
) -> web.Application:
    if type(conversation_archive_enabled) is not bool:
        raise TypeError("conversation_archive_enabled must be bool")
    WORLD_EFFECT_PROJECTOR.configure_archive(
        (
            archive_verified_world_effect
            if conversation_archive_enabled
            else None
        ),
        validate_ready=(
            validate_conversation_archive_ready
            if conversation_archive_enabled
            else None
        ),
        required=conversation_archive_enabled,
    )
    app = web.Application()
    app.router.add_get("/health", health)
    app.router.add_get("/status", status)
    app.router.add_get("/observe", observe)
    app.router.add_post("/start", start)
    app.router.add_post("/stop", stop)
    app.router.add_post("/goal", set_goal)
    app.router.add_post("/archive-result", archive_lifecycle_result)
    app.router.add_post("/action", dispatch_action)
    app.router.add_post("/action/cancel", cancel_action)
    app.router.add_get("/action/{goal_run_id}", action_status)
    app.cleanup_ctx.append(_world_lease_guard_context)
    app.cleanup_ctx.append(_action_guard_context)
    app.on_cleanup.append(_cleanup)
    return app


def _conversation_archive_callbacks_from_environment(
) -> tuple[bool, ArchiveSink | None, ArchiveReadiness | None]:
    enabled = (
        os.getenv("EVELYN_CONVERSATION_ARCHIVE_ENABLED", "false").strip()
        == "true"
    )
    if not enabled:
        return False, None, None
    base_url = os.getenv(
        "EVELYN_CONVERSATION_ARCHIVE_BOT_API_URL", ""
    ).strip()
    key_file = os.getenv(
        "EVELYN_CONVERSATION_ARCHIVE_MINECRAFT_KEY_FILE", ""
    ).strip()
    if not base_url:
        raise RuntimeError("conversation_archive_bot_api_url_required")
    if not key_file:
        raise RuntimeError("conversation_archive_minecraft_key_path_required")
    client = MindcraftConversationArchiveClient.from_key_file(
        base_url=base_url,
        key_file=key_file,
    )
    return True, client.archive_verified_effect, client.validate_ready


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8765)
    args = parser.parse_args()
    archive_enabled, archive_sink, archive_readiness = (
        _conversation_archive_callbacks_from_environment()
    )
    web.run_app(
        build_app(
            conversation_archive_enabled=archive_enabled,
            archive_verified_world_effect=archive_sink,
            validate_conversation_archive_ready=archive_readiness,
        ),
        host=args.host,
        port=args.port,
        handle_signals=True,
        print=None,
    )


if __name__ == "__main__":
    main()
