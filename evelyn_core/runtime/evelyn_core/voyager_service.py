from __future__ import annotations

import argparse
import ctypes
import json
import os
import socket
import subprocess
import sys
import threading
import time
from pathlib import Path
from typing import Any
from urllib import error as urllib_error
from urllib import request as urllib_request

if os.name == "nt":
    import msvcrt

from aiohttp import web

from evelyn_core.paths import get_repo_root, get_runtime_artifacts_root

DEFAULT_VOYAGER_GOAL = "discovering as many diverse things as possible"
REPO_ROOT = get_repo_root()
RUNTIME_ARTIFACTS_ROOT = get_runtime_artifacts_root()
GOAL_STATE_PATH = RUNTIME_ARTIFACTS_ROOT / "voyager" / "voyager_goal_state.json"
RUNNER_STATUS_PATH = RUNTIME_ARTIFACTS_ROOT / "voyager" / "upstream_bridge_status.json"
RUNNER_LOG_PATH = RUNTIME_ARTIFACTS_ROOT / "logs" / "upstream_bridge_runner.log"
SERVICE_ERROR_LOG_PATH = RUNTIME_ARTIFACTS_ROOT / "logs" / "voyager_service_errors.log"
VOYAGER_REPO = REPO_ROOT / "third_party" / "Voyager"
VOYAGER_CURRICULUM_PATH = VOYAGER_REPO / "voyager" / "agents" / "curriculum.py"
VOYAGER_VENV_PYTHON = REPO_ROOT / ".venv-voyager" / "Scripts" / "python.exe"
BRIDGE_HTTP_HOST = os.environ.get("VOYAGER_BRIDGE_HOST", "127.0.0.1")
BRIDGE_HTTP_PORT = int(os.environ.get("VOYAGER_BRIDGE_PORT", "3000"))
_STATUS_LINE_LOCK = threading.Lock()
_STATUS_LINE_LENGTH = 0
_VT_MODE_ENABLED: bool | None = None
_ALT_SCREEN_ENABLED = False
_SERVICE_NOTICE = "Waiting for Voyager /start request"
_SERVICE_LOCK_HANDLE: Any | None = None
_SERVICE_MUTEX_HANDLE: Any | None = None


def _set_console_title(text: str) -> None:
    if os.name != "nt":
        return
    try:
        ctypes.windll.kernel32.SetConsoleTitleW(str(text)[:240])
    except Exception:
        pass


def _append_error_log(path: Path, source: str, message: str, details: str | None = None) -> None:
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("a", encoding="utf-8") as handle:
            stamp = time.strftime("%Y-%m-%d %H:%M:%S")
            handle.write(f"[{stamp}] {source}: {message}\n")
            if details:
                handle.write(f"{details}\n")
            handle.write("\n")
    except Exception:
        pass


def _enable_vt_mode() -> bool:
    global _VT_MODE_ENABLED
    if _VT_MODE_ENABLED is not None:
        return _VT_MODE_ENABLED
    if os.name != "nt":
        _VT_MODE_ENABLED = True
        return True
    try:
        kernel32 = ctypes.windll.kernel32
        handle = kernel32.GetStdHandle(-11)
        mode = ctypes.c_uint32()
        if not kernel32.GetConsoleMode(handle, ctypes.byref(mode)):
            _VT_MODE_ENABLED = False
            return False
        enable_vt = 0x0004
        if mode.value & enable_vt:
            _VT_MODE_ENABLED = True
            return True
        _VT_MODE_ENABLED = bool(kernel32.SetConsoleMode(handle, mode.value | enable_vt))
        return _VT_MODE_ENABLED
    except Exception:
        _VT_MODE_ENABLED = False
        return False


def _enter_alternate_screen() -> None:
    global _ALT_SCREEN_ENABLED
    if _ALT_SCREEN_ENABLED:
        return
    if _enable_vt_mode():
        sys.stdout.write("\033[?1049h\033[?25l")
        sys.stdout.flush()
        _ALT_SCREEN_ENABLED = True


def _leave_alternate_screen() -> None:
    global _ALT_SCREEN_ENABLED
    if not _ALT_SCREEN_ENABLED:
        return
    sys.stdout.write("\033[?25h\033[?1049l")
    sys.stdout.flush()
    _ALT_SCREEN_ENABLED = False


def _inventory_summary(inventory: dict[str, Any], limit: int = 8) -> str:
    if not inventory:
        return "empty"
    parts: list[str] = []
    for name in sorted(inventory.keys())[:limit]:
        parts.append(f"{name}:{inventory.get(name)}")
    if len(inventory) > limit:
        parts.append(f"+{len(inventory) - limit} more")
    return " | ".join(parts)


def _stability_signals(*, display_stage: Any, last_phase_at: Any, execution_session: Any, reset_audit_log: Any, active_plan_state: Any = None, now_ts: float | None = None) -> dict[str, Any]:
    now = float(now_ts if now_ts is not None else time.time())
    session = execution_session if isinstance(execution_session, dict) else {}
    audit_log = reset_audit_log if isinstance(reset_audit_log, list) else []
    plan_state = active_plan_state if isinstance(active_plan_state, dict) else {}
    last_reset = audit_log[-1] if audit_log else None
    phase_age_seconds = None
    if isinstance(last_phase_at, (int, float)):
        phase_age_seconds = max(0.0, now - float(last_phase_at))
    unexpected_reset_count = int(session.get("unexpected_reset_count") or 0)
    recovery_reset_count = int(session.get("recovery_reset_count") or 0)
    total_reset_count = int(session.get("reset_count") or 0)
    transition_history = plan_state.get("transition_history") if isinstance(plan_state.get("transition_history"), list) else []
    recent_plan_transitions = [
        entry for entry in transition_history
        if isinstance(entry, dict)
        and isinstance(entry.get("recorded_at"), (int, float))
        and (now - float(entry.get("recorded_at"))) <= 120.0
        and str(entry.get("transition") or "") in {"selected", "advanced_to_next_node", "current_node_failed"}
    ]
    alerts: list[str] = []
    if unexpected_reset_count > 0:
        alerts.append("unexpected_reset_detected")
    if str(display_stage or "") == "between_tasks" and phase_age_seconds is not None and phase_age_seconds >= 30:
        alerts.append("between_tasks_stalled")
    if recovery_reset_count >= 3:
        alerts.append("recovery_reset_churn")
    if len(recent_plan_transitions) >= 6:
        alerts.append("plan_churn_detected")
    return {
        "healthy": not alerts,
        "alerts": alerts,
        "display_stage": display_stage,
        "phase_age_seconds": phase_age_seconds,
        "reset_count": total_reset_count,
        "recovery_reset_count": recovery_reset_count,
        "unexpected_reset_count": unexpected_reset_count,
        "recent_plan_transition_count": len(recent_plan_transitions),
        "last_reset": last_reset,
    }


def _task_recovery_boundary(
    *,
    last_recovery_boundary: Any,
    last_completion_reason: Any,
    last_success: Any,
    last_task_result: Any,
    current_task_bookkeeping: Any,
    last_task_bookkeeping: Any,
    last_critic_result: Any,
) -> dict[str, Any]:
    if isinstance(last_recovery_boundary, dict):
        return last_recovery_boundary

    completion_reason = str(last_completion_reason).strip() if isinstance(last_completion_reason, str) else None
    success = last_success if isinstance(last_success, bool) else None
    task_result = last_task_result if isinstance(last_task_result, dict) else None
    current_bookkeeping = current_task_bookkeeping if isinstance(current_task_bookkeeping, dict) else {}
    previous_bookkeeping = last_task_bookkeeping if isinstance(last_task_bookkeeping, dict) else {}
    critic_result = last_critic_result if isinstance(last_critic_result, dict) else None
    bookkeeping_status = str(
        current_bookkeeping.get("status")
        or previous_bookkeeping.get("status")
        or ""
    ).strip().lower()
    metadata = {
        "last_completion_reason": completion_reason,
        "last_success": success,
        "bookkeeping_status": bookkeeping_status or None,
        "has_last_task_result": task_result is not None,
        "has_last_critic_result": critic_result is not None,
    }

    if success is True:
        return {
            "scope": "task",
            "domain": "task_completed",
            "reason": completion_reason or "explicit success",
            "healthy": True,
            **metadata,
        }
    if success is False:
        return {
            "scope": "task",
            "domain": "task_failed",
            "reason": completion_reason or "explicit failure",
            "healthy": False,
            **metadata,
        }
    if completion_reason:
        return {
            "scope": "task",
            "domain": "task_unverified",
            "reason": f"completion reason without explicit success: {completion_reason}",
            "healthy": False,
            **metadata,
        }
    if task_result is not None:
        return {
            "scope": "task",
            "domain": "task_result_unverified",
            "reason": "last_task_result exists without explicit success",
            "healthy": False,
            **metadata,
        }
    if bookkeeping_status in {"completed", "effect_verified", "critic_passed"}:
        return {
            "scope": "task",
            "domain": "task_bookkeeping_unverified",
            "reason": f"bookkeeping status {bookkeeping_status!r} has no explicit success flag",
            "healthy": False,
            **metadata,
        }
    return {
        "scope": "task",
        "domain": "healthy",
        "reason": None,
        "healthy": True,
        **metadata,
    }


def _format_position(position: dict[str, Any] | None) -> str:
    if not isinstance(position, dict):
        return "-"
    x = position.get("x")
    y = position.get("y")
    z = position.get("z")
    if any(value is None for value in (x, y, z)):
        return "-"
    try:
        return f"({round(float(x), 1)}, {round(float(y), 1)}, {round(float(z), 1)})"
    except Exception:
        return f"({x}, {y}, {z})"


def _fetch_bridge_telemetry(timeout_sec: float = 0.6) -> dict[str, Any]:
    url = f"http://{BRIDGE_HTTP_HOST}:{BRIDGE_HTTP_PORT}/telemetry"
    req = urllib_request.Request(
        url,
        data=b"{}",
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    try:
        with urllib_request.urlopen(req, timeout=max(0.1, float(timeout_sec))) as resp:
            payload = json.loads(resp.read().decode("utf-8", errors="replace"))
    except (urllib_error.URLError, TimeoutError, json.JSONDecodeError, OSError, ValueError):
        return {}
    if not isinstance(payload, dict):
        return {}
    status = payload.get("status") if isinstance(payload.get("status"), dict) else {}
    result: dict[str, Any] = {
        "inventory": payload.get("inventory") if isinstance(payload.get("inventory"), dict) else {},
        "inventory_slots": payload.get("inventorySlots") if isinstance(payload.get("inventorySlots"), list) else [],
        "position": status.get("position") if isinstance(status.get("position"), dict) else None,
        "health": status.get("health"),
        "hunger": status.get("food"),
        "inventory_used": status.get("inventoryUsed"),
        "equipment": status.get("equipment") if isinstance(status.get("equipment"), list) else [],
        "connection_state": payload.get("connectionState") if isinstance(payload.get("connectionState"), str) else None,
        "connection_note": payload.get("connectionNote") if isinstance(payload.get("connectionNote"), str) else None,
        "last_death_event": payload.get("lastDeathEvent") if isinstance(payload.get("lastDeathEvent"), dict) else None,
        "death_event_log_path": payload.get("deathEventLogPath") if isinstance(payload.get("deathEventLogPath"), str) else None,
        "search_execution": payload.get("searchExecution") if isinstance(payload.get("searchExecution"), dict) else None,
        "recorded_at": payload.get("recordedAt"),
    }
    entities = status.get("entities")
    if isinstance(entities, dict):
        result["nearby_entities"] = entities
    return result


def _tcp_probe(host: str, port: int, timeout_sec: float = 0.35) -> bool:
    try:
        with socket.create_connection((host, int(port)), timeout=timeout_sec):
            return True
    except Exception:
        return False


def _write_status_line(block: str) -> None:
    global _STATUS_LINE_LENGTH
    with _STATUS_LINE_LOCK:
        if _enable_vt_mode():
            _enter_alternate_screen()
            sys.stdout.write("\033[H\033[2J" + block.rstrip("\n"))
            sys.stdout.flush()
            return
        if os.name == "nt":
            os.system("cls")
            sys.stdout.write(block.rstrip("\n"))
            sys.stdout.flush()
            return
        padded = block
        if _STATUS_LINE_LENGTH > len(block):
            padded = block + (" " * (_STATUS_LINE_LENGTH - len(block)))
        _STATUS_LINE_LENGTH = len(block)
        sys.stdout.write("\r" + padded)
        sys.stdout.flush()


def _connection_state_label(state: Any, has_position: bool) -> str:
    raw = str(state or "").strip().lower()
    if raw == "connected":
        return "connected"
    if raw == "awaiting_observation":
        return "awaiting observation"
    if raw == "starting":
        return "starting"
    if raw == "reconnecting":
        return "reconnecting"
    if raw == "disconnected":
        return "not connected"
    return "connected" if has_position else "not connected"


def _format_minecraft_status(payload: dict[str, Any]) -> str:
    connection = payload.get("connection") if isinstance(payload.get("connection"), dict) else {}
    if not connection:
        target = payload.get("minecraft_target") if isinstance(payload.get("minecraft_target"), dict) else {}
    else:
        target = connection.get("minecraft_target") if isinstance(connection.get("minecraft_target"), dict) else {}
    host = target.get("host") or "127.0.0.1"
    port = target.get("port") or 25565
    observation = payload.get("observation") if isinstance(payload.get("observation"), dict) else {}
    inventory = observation.get("inventory") if isinstance(observation.get("inventory"), dict) else {}
    position = payload.get("position") if isinstance(payload.get("position"), dict) else observation.get("position")
    task = payload.get("current_task") or payload.get("goal") or "idle"
    goal = payload.get("goal") or "idle"
    health = payload.get("health") if payload.get("health") is not None else observation.get("health")
    hunger = payload.get("hunger") if payload.get("hunger") is not None else observation.get("hunger")
    phase = payload.get("display_stage") or payload.get("current_task_stage") or payload.get("last_phase") or "waiting_for_task"
    connection_state = payload.get("connection_state") or observation.get("connection_state")
    connection_label = _connection_state_label(connection_state, isinstance(position, dict))
    lines = [
        "==================== Minecraft Status ====================",
        f"Connection : {connection_label}",
        f"Target     : {host}:{port}",
        f"Goal       : {goal}",
        f"Task       : {task}",
        f"Phase      : {phase}",
        f"Position   : {_format_position(position if isinstance(position, dict) else None)}",
        f"HP/Hunger  : {health if health is not None else '-'} / {hunger if hunger is not None else '-'}",
        f"Inventory  : {_inventory_summary(inventory)}",
        f"Notice     : {_SERVICE_NOTICE}",
        f"Errors     : {SERVICE_ERROR_LOG_PATH}",
    ]
    return "\n".join(lines) + "\n"


def _log_service_status(prefix: str, payload: dict[str, Any] | None = None) -> None:
    global _SERVICE_NOTICE
    _SERVICE_NOTICE = prefix
    if payload is None:
        return
    status_block = _format_minecraft_status(payload)
    _write_status_line(status_block)
    _set_console_title("Voyager-Service | Minecraft status board")


def _status_poller(stop_event: threading.Event) -> None:
    last_error: str | None = None
    while not stop_event.is_set():
        try:
            current = STATE.build_status()
            status_block = _format_minecraft_status(current)
            _write_status_line(status_block)
            _set_console_title("Voyager-Service | Minecraft status board")
            current_error = current.get("last_error") if isinstance(current.get("last_error"), str) else None
            if current_error and current_error != last_error:
                _append_error_log(SERVICE_ERROR_LOG_PATH, "voyager_service", current_error)
            last_error = current_error
        except Exception as exc:
            _append_error_log(SERVICE_ERROR_LOG_PATH, "voyager_service_status_poller", str(exc))
        stop_event.wait(1.0)


class UpstreamDirectBridge:
    def __init__(self) -> None:
        self.goal_override = self._load_goal_override()
        self.updated_at = time.time()
        self.runner_process: subprocess.Popen[str] | None = None
        self.runner_mode: str | None = None
        self.runner_goal: str | None = None
        self.last_runner_exit_code: int | None = None
        self._runtime_probe_cache_lock = threading.Lock()
        self._runtime_probe_cache: dict[str, Any] = {}

    def _load_goal_override(self) -> str | None:
        try:
            payload = json.loads(GOAL_STATE_PATH.read_text(encoding="utf-8"))
        except Exception:
            return None
        if not isinstance(payload, dict):
            return None
        goal = str(payload.get("goal_override") or payload.get("goal") or "").strip()
        return goal or None

    def persist_goal_override(self, goal: str | None) -> str:
        goal_text = str(goal or "").strip() or DEFAULT_VOYAGER_GOAL
        GOAL_STATE_PATH.parent.mkdir(parents=True, exist_ok=True)
        GOAL_STATE_PATH.write_text(
            json.dumps(
                {
                    "goal_override": goal_text,
                    "goal": goal_text,
                    "updated_at": time.time(),
                },
                ensure_ascii=False,
                indent=2,
            ),
            encoding="utf-8",
        )
        self.goal_override = goal_text
        self.updated_at = time.time()
        return goal_text

    def get_goal(self) -> str:
        return self.goal_override or DEFAULT_VOYAGER_GOAL

    def _load_runner_status(self) -> dict[str, Any]:
        try:
            payload = json.loads(RUNNER_STATUS_PATH.read_text(encoding="utf-8"))
        except Exception:
            return {}
        return payload if isinstance(payload, dict) else {}

    def _process_alive(self) -> bool:
        return bool(self.runner_process and self.runner_process.poll() is None)

    def _cleanup_runner_handle(self) -> None:
        if self.runner_process and self.runner_process.poll() is not None:
            self.last_runner_exit_code = self.runner_process.returncode
            self.runner_process = None
            self.runner_mode = None
            self.runner_goal = None

    def _determine_mode(self, goal: str, requested_mode: str | None = None) -> str:
        if requested_mode in {"learn", "inference"}:
            return requested_mode
        return "learn" if goal.strip() == DEFAULT_VOYAGER_GOAL else "inference"

    def _runner_is_stale(self, runner_status: dict[str, Any] | None = None, max_age_seconds: float = 45.0) -> bool:
        status = runner_status if isinstance(runner_status, dict) else self._load_runner_status()
        updated_at = status.get("updated_at")
        if not isinstance(updated_at, (int, float)):
            return False
        return (time.time() - float(updated_at)) > max_age_seconds

    def _collect_runtime_probes(self, *, running: bool) -> dict[str, Any]:
        max_age_seconds = 1.5
        now_monotonic = time.monotonic()
        with self._runtime_probe_cache_lock:
            cached = dict(self._runtime_probe_cache) if self._runtime_probe_cache else {}
        cached_at = cached.get("captured_monotonic")
        if (
            cached
            and cached.get("running") == running
            and isinstance(cached_at, (int, float))
            and (now_monotonic - float(cached_at)) <= max_age_seconds
        ):
            return cached

        minecraft_target = {
            "host": os.environ.get("MINEFLAYER_HOST", "127.0.0.1"),
            "port": int(os.environ.get("MINEFLAYER_PORT", "25565")),
        }
        live_telemetry = _fetch_bridge_telemetry(timeout_sec=0.6) if running else {}
        payload = {
            "running": running,
            "captured_at": time.time(),
            "captured_monotonic": now_monotonic,
            "live_telemetry": live_telemetry if isinstance(live_telemetry, dict) else {},
            "bridge_http_reachable": _tcp_probe(BRIDGE_HTTP_HOST, BRIDGE_HTTP_PORT),
            "bridge_telemetry_alive": bool(live_telemetry),
            "minecraft_target": minecraft_target,
            "minecraft_tcp_reachable": _tcp_probe(str(minecraft_target["host"]), int(minecraft_target["port"])),
        }
        with self._runtime_probe_cache_lock:
            self._runtime_probe_cache = dict(payload)
        return payload

    def _terminate_runner(self) -> None:
        self._cleanup_runner_handle()
        if not self.runner_process:
            return
        proc = self.runner_process
        try:
            if os.name == "nt":
                subprocess.run(
                    ["taskkill", "/PID", str(proc.pid), "/T", "/F"],
                    check=False,
                    capture_output=True,
                    text=True,
                )
            else:
                proc.terminate()
                proc.wait(timeout=10)
        except Exception:
            try:
                proc.kill()
            except Exception:
                pass
        finally:
            self.last_runner_exit_code = proc.returncode
            self.runner_process = None
            self.runner_mode = None
            self.runner_goal = None

    def start_runner(self, goal: str, requested_mode: str | None = None) -> None:
        goal_text = self.persist_goal_override(goal)
        mode = self._determine_mode(goal_text, requested_mode)
        current_status = self._load_runner_status()
        if self._process_alive():
            same_mode = self.runner_mode == mode
            same_goal = self.runner_goal == goal_text
            stale = self._runner_is_stale(current_status)
            if same_mode and same_goal and not stale:
                return
            self._terminate_runner()
        RUNNER_STATUS_PATH.parent.mkdir(parents=True, exist_ok=True)
        RUNNER_STATUS_PATH.unlink(missing_ok=True)
        self.last_runner_exit_code = None
        env = os.environ.copy()
        env["PYTHONIOENCODING"] = "utf-8"
        env["PYTHONUTF8"] = "1"
        runner_python = str(VOYAGER_VENV_PYTHON if VOYAGER_VENV_PYTHON.exists() else Path(sys.executable))
        runner_script = REPO_ROOT / "evelyn_core" / "runtime" / "evelyn_core" / "upstream_voyager_runner.py"
        args = [
            runner_python,
            str(runner_script),
            "--mode",
            mode,
            "--goal",
            goal_text,
            "--status-path",
            str(RUNNER_STATUS_PATH),
        ]
        creationflags = 0
        RUNNER_LOG_PATH.parent.mkdir(parents=True, exist_ok=True)
        log_handle = open(RUNNER_LOG_PATH, "a", encoding="utf-8")
        popen_kwargs: dict[str, Any] = {
            "cwd": str(REPO_ROOT),
            "env": env,
            "stdin": subprocess.DEVNULL,
            "stdout": log_handle,
            "stderr": log_handle,
            "text": True,
        }
        if os.name == "nt":
            creationflags = getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0) | getattr(subprocess, "CREATE_NO_WINDOW", 0)
        try:
            self.runner_process = subprocess.Popen(
                args,
                creationflags=creationflags,
                **popen_kwargs,
            )
        finally:
            log_handle.close()
        self.runner_mode = mode
        self.runner_goal = goal_text
        self.updated_at = time.time()

    def stop_runner(self) -> None:
        self._terminate_runner()
        self.updated_at = time.time()

    def build_status(self) -> dict[str, Any]:
        self._cleanup_runner_handle()
        goal = self.get_goal()
        runner_status = self._load_runner_status()
        observation = runner_status.get("observation") if isinstance(runner_status.get("observation"), dict) else {}
        running = bool(self._process_alive())
        runner_exit_code = self.last_runner_exit_code
        runner_status_stale = self._runner_is_stale(runner_status) if runner_status else False
        mode = self.runner_mode or runner_status.get("mode") or self._determine_mode(goal)
        current_task = runner_status.get("current_task")
        current_task_stage_raw = runner_status.get("current_task_stage")
        display_stage = runner_status.get("display_stage")
        current_task_stage = display_stage or current_task_stage_raw
        completed_tasks = runner_status.get("completed_tasks") if isinstance(runner_status.get("completed_tasks"), list) else []
        failed_tasks = runner_status.get("failed_tasks") if isinstance(runner_status.get("failed_tasks"), list) else []
        last_phase = runner_status.get("last_phase")
        last_phase_at = runner_status.get("last_phase_at")
        last_action_program_name = runner_status.get("last_action_program_name")
        last_action_code = runner_status.get("last_action_code")
        last_ai_response_preview = runner_status.get("last_ai_response_preview")
        last_env_event_count = runner_status.get("last_env_event_count")
        last_critique = runner_status.get("last_critique")
        last_progress_message = runner_status.get("last_progress_message")
        progress_messages = runner_status.get("progress_messages") if isinstance(runner_status.get("progress_messages"), list) else []
        last_rollout_info = runner_status.get("last_rollout_info") if isinstance(runner_status.get("last_rollout_info"), dict) else None
        last_task_result = runner_status.get("last_task_result") if isinstance(runner_status.get("last_task_result"), dict) else None
        last_completion_reason = runner_status.get("last_completion_reason") if isinstance(runner_status.get("last_completion_reason"), str) else None
        last_success = runner_status.get("last_success") if isinstance(runner_status.get("last_success"), bool) else None
        last_search_metrics = runner_status.get("last_search_metrics") if isinstance(runner_status.get("last_search_metrics"), dict) else None
        speculative_next_task = runner_status.get("speculative_next_task") if isinstance(runner_status.get("speculative_next_task"), dict) else None
        last_speculative_decision = runner_status.get("last_speculative_decision") if isinstance(runner_status.get("last_speculative_decision"), dict) else None
        last_inventory_plan = runner_status.get("last_inventory_plan") if isinstance(runner_status.get("last_inventory_plan"), dict) else None
        active_plan_state = runner_status.get("active_plan_state") if isinstance(runner_status.get("active_plan_state"), dict) else None
        last_task_contract_decision = runner_status.get("last_task_contract_decision") if isinstance(runner_status.get("last_task_contract_decision"), dict) else None
        current_task_bookkeeping = runner_status.get("current_task_bookkeeping") if isinstance(runner_status.get("current_task_bookkeeping"), dict) else None
        last_task_bookkeeping = runner_status.get("last_task_bookkeeping") if isinstance(runner_status.get("last_task_bookkeeping"), dict) else None
        last_world_effect_verification = runner_status.get("last_world_effect_verification") if isinstance(runner_status.get("last_world_effect_verification"), dict) else None
        last_critic_result = runner_status.get("last_critic_result") if isinstance(runner_status.get("last_critic_result"), dict) else None
        last_recovery_boundary = runner_status.get("last_recovery_boundary") if isinstance(runner_status.get("last_recovery_boundary"), dict) else None
        execution_session = runner_status.get("execution_session") if isinstance(runner_status.get("execution_session"), dict) else None
        reset_audit_log = runner_status.get("reset_audit_log") if isinstance(runner_status.get("reset_audit_log"), list) else []
        search_metrics_history = runner_status.get("search_metrics_history") if isinstance(runner_status.get("search_metrics_history"), list) else []
        resume_checkpoint = runner_status.get("resume_checkpoint") if isinstance(runner_status.get("resume_checkpoint"), dict) else None
        stability_signals = _stability_signals(
            display_stage=display_stage,
            last_phase_at=last_phase_at,
            execution_session=execution_session,
            reset_audit_log=reset_audit_log,
            active_plan_state=active_plan_state,
        )
        inventory = observation.get("inventory") if isinstance(observation.get("inventory"), dict) else {}
        inventory_slots = observation.get("inventory_slots") if isinstance(observation.get("inventory_slots"), list) else []
        position = observation.get("position") if isinstance(observation.get("position"), dict) else None
        health = observation.get("health")
        hunger = observation.get("hunger")
        inventory_used = observation.get("inventory_used")
        if inventory_used is None:
            inventory_used = observation.get("inventoryUsed")
        equipment = observation.get("equipment") if isinstance(observation.get("equipment"), list) else []
        nearby_entities = observation.get("nearby_entities") if isinstance(observation.get("nearby_entities"), dict) else {}
        runtime_probes = self._collect_runtime_probes(running=running)
        live_telemetry = runtime_probes.get("live_telemetry") if isinstance(runtime_probes.get("live_telemetry"), dict) else {}
        if running and live_telemetry:
            inventory = live_telemetry.get("inventory") if isinstance(live_telemetry.get("inventory"), dict) and (not inventory or not isinstance(inventory, dict)) else inventory
            inventory_slots = live_telemetry.get("inventory_slots") if isinstance(live_telemetry.get("inventory_slots"), list) and not inventory_slots else inventory_slots
            position = live_telemetry.get("position") if isinstance(live_telemetry.get("position"), dict) and position is None else position
            health = live_telemetry.get("health") if live_telemetry.get("health") is not None and health is None else health
            hunger = live_telemetry.get("hunger") if live_telemetry.get("hunger") is not None and hunger is None else hunger
            inventory_used = live_telemetry.get("inventory_used") if live_telemetry.get("inventory_used") is not None and inventory_used is None else inventory_used
            equipment = live_telemetry.get("equipment") if isinstance(live_telemetry.get("equipment"), list) and not equipment else equipment
            nearby_entities = live_telemetry.get("nearby_entities") if isinstance(live_telemetry.get("nearby_entities"), dict) and not nearby_entities else nearby_entities
            observation = dict(observation)
            observation["inventory"] = inventory
            if isinstance(inventory_slots, list):
                observation["inventory_slots"] = inventory_slots
            observation["position"] = position
            observation["health"] = health
            observation["hunger"] = hunger
            if inventory_used is not None:
                observation["inventory_used"] = inventory_used
                observation["inventoryUsed"] = inventory_used
            if isinstance(equipment, list):
                observation["equipment"] = equipment
            if isinstance(nearby_entities, dict):
                observation["nearby_entities"] = nearby_entities
            if isinstance(live_telemetry.get("connection_state"), str):
                observation["connection_state"] = live_telemetry.get("connection_state")
            if isinstance(live_telemetry.get("connection_note"), str):
                observation["connection_note"] = live_telemetry.get("connection_note")
            if isinstance(live_telemetry.get("last_death_event"), dict):
                observation["last_death_event"] = live_telemetry.get("last_death_event")
            if isinstance(live_telemetry.get("death_event_log_path"), str):
                observation["death_event_log_path"] = live_telemetry.get("death_event_log_path")
            if isinstance(live_telemetry.get("search_execution"), dict):
                observation["search_execution"] = live_telemetry.get("search_execution")
        hostiles_nearby = len(nearby_entities) if isinstance(nearby_entities, dict) else None
        voyager_evaluation = {
            "goal": goal,
            "unique_item_count": len(inventory),
            "unique_items": sorted(inventory.keys()),
            "tech_tree": {"milestones": {}, "highest_unlocked": "unknown"},
            "travel_distance_blocks": 0.0,
            "skill_library": {
                "size": 0,
                "reuse_ready": False,
                "transfer_eval_mode": "upstream_runtime",
            },
        }
        activity_text = "running upstream Voyager" if running else "upstream Voyager idle"
        if current_task:
            activity_text = f"task: {current_task}"
        elif runner_status.get("last_error"):
            activity_text = f"error: {runner_status.get('last_error')}"
        last_result = runner_status.get("last_result") if isinstance(runner_status.get("last_result"), dict) else None
        last_error = runner_status.get("last_error")
        if not running and last_error is None and runner_exit_code is not None and not runner_status:
            last_error = f"runner exited before writing status file (code {runner_exit_code})"
        minecraft_target = runtime_probes.get("minecraft_target") if isinstance(runtime_probes.get("minecraft_target"), dict) else {
            "host": os.environ.get("MINEFLAYER_HOST", "127.0.0.1"),
            "port": int(os.environ.get("MINEFLAYER_PORT", "25565")),
        }
        bridge_http_reachable = bool(runtime_probes.get("bridge_http_reachable"))
        bridge_telemetry_alive = bool(runtime_probes.get("bridge_telemetry_alive"))
        minecraft_tcp_reachable = bool(runtime_probes.get("minecraft_tcp_reachable"))
        has_live_position = isinstance(position, dict)
        raw_connection_state = observation.get("connection_state") if isinstance(observation.get("connection_state"), str) else (runner_status.get("connection_state") if isinstance(runner_status.get("connection_state"), str) else None)
        if not running:
            connection_state = "disconnected"
        elif raw_connection_state == "connected":
            connection_state = "connected"
        elif has_live_position:
            connection_state = "connected"
        elif raw_connection_state in {"awaiting_observation", "starting", "reconnecting", "disconnected"}:
            connection_state = raw_connection_state
        elif runner_status:
            connection_state = "awaiting_observation"
        else:
            connection_state = "starting"
        bridge_alive = bridge_http_reachable or bridge_telemetry_alive
        minecraft_connected = running and connection_state == "connected"
        health_domains = {
            "service_http": {
                "ok": True,
                "state": "listening",
                "host": os.environ.get("MINECRAFT_AUTONOMY_SERVICE_HOST", "127.0.0.1"),
                "port": int(os.environ.get("MINECRAFT_AUTONOMY_SERVICE_PORT", "8765")),
            },
            "runner_process": {
                "ok": running,
                "state": "running" if running else ("error" if last_error else "idle"),
                "exit_code": runner_exit_code,
            },
            "runner_status_file": {
                "ok": bool(runner_status) and not runner_status_stale,
                "present": bool(runner_status),
                "stale": runner_status_stale,
            },
            "bridge_http": {
                "ok": bridge_http_reachable,
                "host": BRIDGE_HTTP_HOST,
                "port": BRIDGE_HTTP_PORT,
            },
            "bridge_telemetry": {
                "ok": bridge_telemetry_alive,
                "recorded_at": live_telemetry.get("recorded_at") if isinstance(live_telemetry, dict) else None,
            },
            "minecraft_tcp": {
                "ok": minecraft_tcp_reachable,
                "host": minecraft_target["host"],
                "port": minecraft_target["port"],
            },
            "task_bookkeeping": {
                "last_completion_reason": last_completion_reason,
                "last_success": last_success,
                "has_last_rollout_info": isinstance(last_rollout_info, dict),
                "has_last_task_result": isinstance(last_task_result, dict),
                "has_last_search_metrics": isinstance(last_search_metrics, dict),
                "has_current_task_bookkeeping": isinstance(current_task_bookkeeping, dict),
                "has_last_task_bookkeeping": isinstance(last_task_bookkeeping, dict),
                "has_last_task_contract_decision": isinstance(last_task_contract_decision, dict),
                "has_last_world_effect_verification": isinstance(last_world_effect_verification, dict),
                "has_last_critic_result": isinstance(last_critic_result, dict),
                "has_last_recovery_boundary": isinstance(last_recovery_boundary, dict),
            },
            "resume_checkpoint": resume_checkpoint,
        }
        if last_error:
            runtime_recovery_domain = "runner_exception"
            runtime_recovery_reason = last_error
        elif not minecraft_tcp_reachable:
            runtime_recovery_domain = "minecraft_dependency"
            runtime_recovery_reason = "Minecraft server is not reachable on the configured TCP port."
        elif running and not bridge_http_reachable:
            runtime_recovery_domain = "bridge_http"
            runtime_recovery_reason = "Runner is up but the bridge HTTP port is not reachable."
        elif running and bridge_http_reachable and not minecraft_connected:
            runtime_recovery_domain = "runner_runtime"
            runtime_recovery_reason = "Runner and bridge are up, but Minecraft connection is not yet healthy."
        else:
            runtime_recovery_domain = "healthy"
            runtime_recovery_reason = None
        runtime_boundary = {
            "scope": "runtime",
            "domain": runtime_recovery_domain,
            "reason": runtime_recovery_reason,
            "healthy": runtime_recovery_domain == "healthy",
        }
        task_boundary = _task_recovery_boundary(
            last_recovery_boundary=last_recovery_boundary,
            last_completion_reason=last_completion_reason,
            last_success=last_success,
            last_task_result=last_task_result,
            current_task_bookkeeping=current_task_bookkeeping,
            last_task_bookkeeping=last_task_bookkeeping,
            last_critic_result=last_critic_result,
        )
        if not runtime_boundary["healthy"]:
            recovery_scope = "runtime"
            recovery_domain = runtime_boundary["domain"]
            recovery_reason = runtime_boundary["reason"]
        elif not stability_signals.get("healthy", True):
            recovery_scope = "runtime"
            recovery_domain = "runtime_stability"
            recovery_reason = ", ".join(stability_signals.get("alerts") or []) or "stability alerts detected"
        elif not task_boundary.get("healthy", True):
            recovery_scope = "task"
            recovery_domain = task_boundary.get("domain")
            recovery_reason = task_boundary.get("reason")
        else:
            recovery_scope = "healthy"
            recovery_domain = "healthy"
            recovery_reason = None
        status_summary = {
            "loop_running": running,
            "activity": {"code": "running" if running else "idle", "text": activity_text},
            "goal": {"current": goal, "stage": mode, "task": current_task},
            "connection": {
                "sidecar_process_running": running,
                "bridge_alive": bridge_alive,
                "bridge_http_reachable": bridge_http_reachable,
                "bridge_telemetry_alive": bridge_telemetry_alive,
                "minecraft_tcp_reachable": minecraft_tcp_reachable,
                "minecraft_connected": minecraft_connected,
                "connection_state": connection_state,
                "connection_note": observation.get("connection_note") if isinstance(observation.get("connection_note"), str) else runner_status.get("connection_note"),
                "minecraft_target": minecraft_target,
                "last_observation_available": has_live_position,
            },
            "last_step": {
                "phase": last_phase,
                "display_stage": display_stage,
                "raw_phase": current_task_stage_raw,
                "phase_at": last_phase_at,
                "action_program": last_action_program_name,
                "env_event_count": last_env_event_count,
                "progress": last_progress_message,
                "completion_reason": last_completion_reason,
                "success": last_success,
                "speculative_next_task": speculative_next_task,
                "last_speculative_decision": last_speculative_decision,
                "last_inventory_plan": last_inventory_plan,
                "active_plan_state": active_plan_state,
                "last_task_contract_decision": last_task_contract_decision,
                "current_task_bookkeeping": current_task_bookkeeping,
                "last_task_bookkeeping": last_task_bookkeeping,
                "last_world_effect_verification": last_world_effect_verification,
                "last_critic_result": last_critic_result,
                "last_recovery_boundary": last_recovery_boundary,
                "execution_session": execution_session,
                "reset_audit_log": reset_audit_log,
            },
            "last_completed": last_result if isinstance(last_result, dict) else None,
            "health_domains": health_domains,
            "recovery_boundaries": {
                "runtime_boundary": runtime_boundary,
                "task_boundary": task_boundary,
            },
            "stability_signals": stability_signals,
            "evaluation": voyager_evaluation,
            "position": position,
            "health": health,
            "hunger": hunger,
            "hostiles_nearby": hostiles_nearby,
        }
        return {
            "service": "voyager_minecraft",
            "mode": "upstream_direct_bridge",
            "running": running,
            "loop_running": running,
            "connected": minecraft_connected,
            "bridge_alive": bridge_alive,
            "bridge_http_reachable": bridge_http_reachable,
            "bridge_telemetry_alive": bridge_telemetry_alive,
            "minecraft_tcp_reachable": minecraft_tcp_reachable,
            "minecraft_connected": minecraft_connected,
            "connection_state": connection_state,
            "connection_note": observation.get("connection_note") if isinstance(observation.get("connection_note"), str) else runner_status.get("connection_note"),
            "minecraft_target": minecraft_target,
            "last_observation_available": has_live_position,
            "sidecar_process_running": running,
            "goal": goal,
            "goal_override": goal,
            "stage": mode,
            "current_task": current_task,
            "current_task_stage": current_task_stage,
            "display_stage": display_stage,
            "current_task_stage_raw": current_task_stage_raw,
            "autonomy_current_execution": None,
            "autonomy_last_result": last_result,
            "executor_last_step": last_phase,
            "executor_last_step_summary": {
                "phase": last_phase,
                "display_stage": display_stage,
                "raw_phase": current_task_stage_raw,
                "phase_at": last_phase_at,
                "action_program": last_action_program_name,
                "env_event_count": last_env_event_count,
                "critique": last_critique,
                "progress": last_progress_message,
            } if any(value is not None for value in [last_phase, last_phase_at, last_action_program_name, last_env_event_count, last_critique, last_progress_message]) else None,
            "last_action": last_action_program_name,
            "last_result": last_result,
            "last_rollout_info": last_rollout_info,
            "last_task_result": last_task_result,
            "last_completion_reason": last_completion_reason,
            "last_success": last_success,
            "last_search_metrics": last_search_metrics,
            "speculative_next_task": speculative_next_task,
            "last_speculative_decision": last_speculative_decision,
            "last_inventory_plan": last_inventory_plan,
            "active_plan_state": active_plan_state,
            "last_task_contract_decision": last_task_contract_decision,
            "current_task_bookkeeping": current_task_bookkeeping,
            "last_task_bookkeeping": last_task_bookkeeping,
            "last_world_effect_verification": last_world_effect_verification,
            "last_critic_result": last_critic_result,
            "last_recovery_boundary": last_recovery_boundary,
            "execution_session": execution_session,
            "reset_audit_log": reset_audit_log,
            "search_metrics_history": search_metrics_history,
            "last_error": last_error,
            "last_progress_message": last_progress_message,
            "progress_messages": progress_messages,
            "last_ai_response_preview": last_ai_response_preview,
            "last_action_code": last_action_code,
            "observation": observation,
            "last_death_event": observation.get("last_death_event") if isinstance(observation, dict) else None,
            "death_event_log_path": observation.get("death_event_log_path") if isinstance(observation, dict) else None,
            "search_execution": observation.get("search_execution") if isinstance(observation, dict) else None,
            "voyager_evaluation": voyager_evaluation,
            "status_summary": status_summary,
            "health_domains": health_domains,
            "stability_signals": stability_signals,
            "resume_checkpoint": resume_checkpoint,
            "recovery_state": {
                "scope": recovery_scope,
                "domain": recovery_domain,
                "reason": recovery_reason,
                "healthy": recovery_domain == "healthy",
                "runtime_boundary": runtime_boundary,
                "task_boundary": task_boundary,
                "stability_signals": stability_signals,
            },
            "agent_models": {
                "action": os.environ.get("VOYAGER_CODEX_MODEL") or None,
                "curriculum": os.environ.get("VOYAGER_CURRICULUM_MODEL_NAME") or None,
                "critic": os.environ.get("VOYAGER_CRITIC_MODEL_NAME") or None,
                "skill": os.environ.get("VOYAGER_SKILL_MODEL_NAME") or None,
            },
            "curriculum_last_llm_task": None,
            "codex_gateway": {
                "enabled": True,
                "backend": os.environ.get("VOYAGER_ACTION_BACKEND", "codex-gateway"),
                "url": os.environ.get("VOYAGER_CODEX_GATEWAY_URL") or None,
                "model": os.environ.get("VOYAGER_CODEX_MODEL") or None,
            },
            "local_runtime_services": [],
            "learned_skill_library_size": 0,
            "voyager_repo": str(VOYAGER_REPO),
            "voyager_repo_present": VOYAGER_REPO.exists(),
            "voyager_curriculum_present": VOYAGER_CURRICULUM_PATH.exists(),
            "updated_at": runner_status.get("updated_at") or self.updated_at,
            "note": "Discord/Codex/LLM/API bridge backed by upstream Voyager runtime.",
            "completed_tasks": completed_tasks,
            "failed_tasks": failed_tasks,
            "runner_status_path": str(RUNNER_STATUS_PATH),
            "runner_log_path": str(RUNNER_LOG_PATH),
            "runner_exit_code": runner_exit_code,
            "last_phase": last_phase,
            "last_phase_at": last_phase_at,
        }


STATE = UpstreamDirectBridge()


async def health(_: web.Request) -> web.Response:
    STATE._cleanup_runner_handle()
    return web.json_response(
        {
            "ok": True,
            "service": "voyager_minecraft",
            "mode": "upstream_direct_bridge",
            "runner_alive": STATE._process_alive(),
        }
    )


async def status(_: web.Request) -> web.Response:
    return web.json_response(STATE.build_status())


async def observe(_: web.Request) -> web.Response:
    current = STATE.build_status()
    return web.json_response(current.get("observation") or {})


async def start(request: web.Request) -> web.Response:
    payload = await request.json() if request.can_read_body else {}
    goal = str((payload or {}).get("goal") or STATE.get_goal()).strip() or DEFAULT_VOYAGER_GOAL
    mode = str((payload or {}).get("mode") or "").strip() or None
    STATE.start_runner(goal, mode)
    current = STATE.build_status()
    _log_service_status("Runner start requested", current)
    return web.json_response(current)


async def stop(_: web.Request) -> web.Response:
    STATE.stop_runner()
    current = STATE.build_status()
    _log_service_status("Runner stop requested", current)
    return web.json_response(current)


async def set_goal(request: web.Request) -> web.Response:
    payload = await request.json() if request.can_read_body else {}
    goal = str((payload or {}).get("goal") or "").strip()
    if not goal:
        raise web.HTTPBadRequest(text=json.dumps({"error": "goal text is empty"}), content_type="application/json")
    STATE.persist_goal_override(goal)
    if STATE._process_alive():
        mode = STATE.runner_mode or STATE._determine_mode(goal)
        STATE.start_runner(goal, mode)
    return web.json_response(STATE.build_status())


def _acquire_service_lock(port: int):
    lock_path = RUNTIME_ARTIFACTS_ROOT / "locks" / f"voyager_service_{port}.lock"
    lock_path.parent.mkdir(parents=True, exist_ok=True)
    handle = lock_path.open("a+")
    if os.name == "nt":
        mutex_name = f"Global\\Evelyn-Voyager-Service-Process-{port}"
        mutex_handle = ctypes.windll.kernel32.CreateMutexW(None, False, mutex_name)
        if not mutex_handle:
            handle.close()
            raise RuntimeError(f"Unable to create Voyager service mutex for port {port}")
        already_exists = ctypes.windll.kernel32.GetLastError() == 183
        if already_exists:
            ctypes.windll.kernel32.CloseHandle(mutex_handle)
            handle.close()
            raise RuntimeError(f"Voyager service already running for port {port}")
        global _SERVICE_MUTEX_HANDLE
        _SERVICE_MUTEX_HANDLE = mutex_handle
        try:
            msvcrt.locking(handle.fileno(), msvcrt.LK_NBLCK, 1)
        except OSError:
            ctypes.windll.kernel32.CloseHandle(mutex_handle)
            _SERVICE_MUTEX_HANDLE = None
            handle.close()
            raise RuntimeError(f"Voyager service already running for port {port}")
    return handle


def build_app() -> web.Application:
    app = web.Application()
    app.router.add_get("/health", health)
    app.router.add_get("/status", status)
    app.router.add_get("/observe", observe)
    app.router.add_post("/start", start)
    app.router.add_post("/stop", stop)
    app.router.add_post("/goal", set_goal)
    return app


def main() -> None:
    global _SERVICE_LOCK_HANDLE
    parser = argparse.ArgumentParser()
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8765)
    args = parser.parse_args()
    _SERVICE_LOCK_HANDLE = _acquire_service_lock(args.port)
    startup_status = STATE.build_status()
    _log_service_status(f"HTTP ready at http://{args.host}:{args.port}", startup_status)
    _log_service_status("Waiting for Voyager /start request", startup_status)
    poller_stop = threading.Event()
    poller = threading.Thread(target=_status_poller, args=(poller_stop,), daemon=True)
    poller.start()
    try:
        web.run_app(build_app(), host=args.host, port=args.port, handle_signals=True, print=None)
    finally:
        poller_stop.set()
        if _SERVICE_LOCK_HANDLE is not None:
            try:
                if os.name == "nt":
                    _SERVICE_LOCK_HANDLE.seek(0)
                    msvcrt.locking(_SERVICE_LOCK_HANDLE.fileno(), msvcrt.LK_UNLCK, 1)
            except Exception:
                pass
            try:
                _SERVICE_LOCK_HANDLE.close()
            except Exception:
                pass
            _SERVICE_LOCK_HANDLE = None
        if _SERVICE_MUTEX_HANDLE is not None:
            try:
                ctypes.windll.kernel32.CloseHandle(_SERVICE_MUTEX_HANDLE)
            except Exception:
                pass
            _SERVICE_MUTEX_HANDLE = None
        _leave_alternate_screen()
        sys.stdout.write("\n")
        sys.stdout.flush()


if __name__ == "__main__":
    main()
