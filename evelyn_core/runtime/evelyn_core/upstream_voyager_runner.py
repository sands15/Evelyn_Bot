from __future__ import annotations

import argparse
import ctypes
import json
import os
import sys
import threading
import time
import traceback
from pathlib import Path
from typing import Any
from urllib import error as urllib_error
from urllib import request as urllib_request
from urllib.parse import urlparse

from evelyn_core.bounded_logs import append_bounded_log
from evelyn_core.paths import get_repo_root, get_runtime_artifacts_root

REPO_ROOT = get_repo_root()
UPSTREAM_ROOT = REPO_ROOT / "third_party" / "Voyager"
if str(UPSTREAM_ROOT) not in sys.path:
    sys.path.insert(0, str(UPSTREAM_ROOT))

from evelyn_core.config import (  # noqa: E402
    MINDCRAFT_LOCAL_LLM_URL,
    MINDCRAFT_LOCAL_MODEL,
    OPENAI_API_KEY,
    OPENAI_CHAT_COMPLETIONS_URL,
    VOYAGER_ACTION_BACKEND,
    VOYAGER_CODEX_MODEL,
    VOYAGER_CODEX_GATEWAY_URL,
    VOYAGER_CRITIC_LLM_URL,
    VOYAGER_CRITIC_MODEL_NAME,
    VOYAGER_CURRICULUM_LLM_URL,
    VOYAGER_CURRICULUM_MODEL_NAME,
    VOYAGER_SKILL_LLM_URL,
    VOYAGER_SKILL_MODEL_NAME,
)

DEFAULT_GOAL = "discovering as many diverse things as possible"
RUNTIME_ARTIFACTS_ROOT = get_runtime_artifacts_root()
DEFAULT_STATUS_PATH = RUNTIME_ARTIFACTS_ROOT / "voyager" / "upstream_bridge_status.json"
DEFAULT_CKPT_DIR = REPO_ROOT / "bot_memory" / "upstream_ckpt"
DEFAULT_SKILL_LIBRARY_DIR = REPO_ROOT / "third_party" / "Voyager" / "skill_library"
RUNNER_ERROR_LOG_PATH = RUNTIME_ARTIFACTS_ROOT / "logs" / "upstream_bridge_errors.log"
BRIDGE_HTTP_HOST = os.environ.get("VOYAGER_BRIDGE_HOST", "127.0.0.1")
BRIDGE_HTTP_PORT = int(os.environ.get("VOYAGER_BRIDGE_PORT", "3000"))
_RUNNER_STATUS_LINE_LENGTH = 0
_RUNNER_VT_MODE_ENABLED: bool | None = None
_RUNNER_ALT_SCREEN_ENABLED = False
_RUNNER_FILE_STATUS_LAST_EMIT_AT = 0.0
_FILE_STATUS_INTERVAL_SEC = max(1.0, float(os.environ.get("EVELYN_STATUS_LOG_INTERVAL_SEC", "30")))
_LOG_MAX_BYTES = max(1024, int(os.environ.get("EVELYN_LOG_MAX_BYTES", str(25 * 1024 * 1024))))
_LOG_BACKUP_COUNT = max(1, int(os.environ.get("EVELYN_LOG_BACKUP_COUNT", "4")))


def _configure_console_encoding() -> None:
    for stream_name in ("stdout", "stderr"):
        stream = getattr(sys, stream_name, None)
        if stream is None:
            continue
        try:
            stream.reconfigure(encoding="utf-8", errors="replace")
        except Exception:
            pass


def _set_console_title(text: str) -> None:
    if os.name != "nt":
        return
    try:
        ctypes.windll.kernel32.SetConsoleTitleW(str(text)[:240])
    except Exception:
        pass


def _append_error_log(path: Path, source: str, message: str, details: str | None = None) -> None:
    try:
        stamp = time.strftime("%Y-%m-%d %H:%M:%S")
        payload = f"[{stamp}] {source}: {message}\n"
        if details:
            payload += f"{details}\n"
        append_bounded_log(path, payload + "\n", max_bytes=_LOG_MAX_BYTES, backup_count=_LOG_BACKUP_COUNT)
    except Exception:
        pass


def _enable_vt_mode() -> bool:
    global _RUNNER_VT_MODE_ENABLED
    if _RUNNER_VT_MODE_ENABLED is not None:
        return _RUNNER_VT_MODE_ENABLED
    if os.name != "nt":
        _RUNNER_VT_MODE_ENABLED = True
        return True
    try:
        kernel32 = ctypes.windll.kernel32
        handle = kernel32.GetStdHandle(-11)
        mode = ctypes.c_uint32()
        if not kernel32.GetConsoleMode(handle, ctypes.byref(mode)):
            _RUNNER_VT_MODE_ENABLED = False
            return False
        enable_vt = 0x0004
        if mode.value & enable_vt:
            _RUNNER_VT_MODE_ENABLED = True
            return True
        _RUNNER_VT_MODE_ENABLED = bool(kernel32.SetConsoleMode(handle, mode.value | enable_vt))
        return _RUNNER_VT_MODE_ENABLED
    except Exception:
        _RUNNER_VT_MODE_ENABLED = False
        return False


def _enter_alternate_screen() -> None:
    global _RUNNER_ALT_SCREEN_ENABLED
    if _RUNNER_ALT_SCREEN_ENABLED:
        return
    if _enable_vt_mode():
        sys.stdout.write("\033[?1049h\033[?25l")
        sys.stdout.flush()
        _RUNNER_ALT_SCREEN_ENABLED = True


def _leave_alternate_screen() -> None:
    global _RUNNER_ALT_SCREEN_ENABLED
    if not _RUNNER_ALT_SCREEN_ENABLED:
        return
    sys.stdout.write("\033[?25h\033[?1049l")
    sys.stdout.flush()
    _RUNNER_ALT_SCREEN_ENABLED = False


def _inventory_summary(inventory: dict[str, Any], limit: int = 8) -> str:
    if not inventory:
        return "empty"
    parts: list[str] = []
    for name in sorted(inventory.keys())[:limit]:
        parts.append(f"{name}:{inventory.get(name)}")
    if len(inventory) > limit:
        parts.append(f"+{len(inventory) - limit} more")
    return ", ".join(parts)


def _format_position(position: dict[str, Any] | None) -> str:
    if not isinstance(position, dict):
        return "-"
    try:
        return f"({round(float(position.get('x')), 1)}, {round(float(position.get('y')), 1)}, {round(float(position.get('z')), 1)})"
    except Exception:
        return f"({position.get('x')}, {position.get('y')}, {position.get('z')})"


def _write_status_line(line: str) -> None:
    global _RUNNER_FILE_STATUS_LAST_EMIT_AT, _RUNNER_STATUS_LINE_LENGTH
    if not sys.stdout.isatty():
        now = time.monotonic()
        if now - _RUNNER_FILE_STATUS_LAST_EMIT_AT < _FILE_STATUS_INTERVAL_SEC:
            return
        _RUNNER_FILE_STATUS_LAST_EMIT_AT = now
        sys.stdout.write(line.rstrip("\n") + "\n")
        sys.stdout.flush()
        return
    if _enable_vt_mode():
        _enter_alternate_screen()
        sys.stdout.write("\033[H\033[2J" + line.rstrip("\n"))
        sys.stdout.flush()
        return
    if os.name == "nt":
        os.system("cls")
        sys.stdout.write(line.rstrip("\n"))
        sys.stdout.flush()
        return
    padded = line
    if _RUNNER_STATUS_LINE_LENGTH > len(line):
        padded = line + (" " * (_RUNNER_STATUS_LINE_LENGTH - len(line)))
    _RUNNER_STATUS_LINE_LENGTH = len(line)
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


def _render_runner_status_line(payload: dict[str, Any]) -> str:
    observation = payload.get("observation") if isinstance(payload.get("observation"), dict) else {}
    inventory = observation.get("inventory") if isinstance(observation.get("inventory"), dict) else {}
    position = observation.get("position") if isinstance(observation.get("position"), dict) else None
    health = observation.get("health")
    hunger = observation.get("hunger")
    task = payload.get("current_task") or payload.get("goal") or "idle"
    goal = payload.get("goal") or "idle"
    phase = payload.get("display_stage") or payload.get("current_task_stage") or payload.get("last_phase") or ("running" if payload.get("running") else "idle")
    connection_state = payload.get("connection_state") or observation.get("connection_state")
    connection_label = _connection_state_label(connection_state, bool(position))
    lines = [
        "==================== Minecraft Status ====================",
        f"Connection : {connection_label}",
        f"Goal       : {goal}",
        f"Task       : {task}",
        f"Phase      : {phase}",
        f"Position   : {_format_position(position)}",
        f"HP/Hunger  : {health if health is not None else '-'} / {hunger if hunger is not None else '-'}",
        f"Inventory  : {_inventory_summary(inventory)}",
        f"Errors     : {RUNNER_ERROR_LOG_PATH}",
    ]
    return "\n".join(lines) + "\n"


def _compute_display_stage(*, running: bool, current_task: Any, last_phase: Any, last_error: Any, current_task_bookkeeping: Any, last_task_result: Any) -> str:
    if last_error:
        return "blocked"
    if not running:
        return "idle"
    phase = str(last_phase or "").strip().lower()
    bookkeeping = current_task_bookkeeping if isinstance(current_task_bookkeeping, dict) else {}
    bookkeeping_status = str(bookkeeping.get("status") or "").strip().lower()
    if phase == "persistent_session_bootstrap":
        return "bootstrapping_session"
    if phase in {"persistent_session_ready", "task_selection"}:
        return "selecting_task"
    if phase in {"task_session_turnover", "task_rollout_finished", "objective_node_advanced", "curriculum_progress_update"}:
        if bookkeeping_status in {"completed", "effect_verified"} or isinstance(last_task_result, dict):
            return "between_tasks"
    if phase in {"task_session_start", "task_observation_ready", "awaiting_action_llm", "action_llm_request", "action_llm_response", "action_program_ready", "minecraft_step_response", "action_step_complete", "skill_retrieval"}:
        return "executing_task"
    if phase in {"effect_verifier_check", "effect_verifier_result", "critic_check", "critic_result"}:
        return "verifying_result"
    if phase == "skill_persistence":
        return "persisting_skill"
    if phase in {"death_recovery_required", "death_interrupt_observed"} or bookkeeping_status == "recovery_required":
        return "recovery_required"
    if phase == "action_parse_failed":
        return "action_parse_failed"
    if current_task:
        return "executing_task"
    return "waiting_for_task"


def _explicit_success_value(value: Any) -> bool | None:
    if isinstance(value, bool):
        return value
    return None


def _derive_last_success(
    *,
    last_success: Any,
    last_task_result: Any,
    current_task_bookkeeping: Any,
    last_task_bookkeeping: Any,
    last_critic_result: Any,
) -> bool | None:
    explicit = _explicit_success_value(last_success)
    if explicit is not None:
        return explicit
    for candidate in (last_task_result, current_task_bookkeeping, last_task_bookkeeping, last_critic_result):
        if not isinstance(candidate, dict):
            continue
        explicit = _explicit_success_value(candidate.get("success"))
        if explicit is not None:
            return explicit
    return None


def _copy_task_status_from_voyager(status: "RunnerStatus", voyager: Any) -> None:
    last_task_result = _to_jsonable(getattr(voyager, "last_task_result", None))
    current_task_bookkeeping = _to_jsonable(getattr(voyager, "current_task_bookkeeping", None))
    last_task_bookkeeping = _to_jsonable(getattr(voyager, "last_task_bookkeeping", None))
    last_critic_result = _to_jsonable(getattr(voyager, "last_critic_result", None))
    status.last_rollout_info = _to_jsonable(getattr(voyager, "last_rollout_info", None))
    status.last_task_result = last_task_result
    status.last_completion_reason = getattr(voyager, "last_completion_reason", None)
    status.last_success = _derive_last_success(
        last_success=getattr(voyager, "last_success", None),
        last_task_result=last_task_result,
        current_task_bookkeeping=current_task_bookkeeping,
        last_task_bookkeeping=last_task_bookkeeping,
        last_critic_result=last_critic_result,
    )
    status.last_search_metrics = _to_jsonable(getattr(voyager, "last_search_metrics", None))
    status.speculative_next_task = _to_jsonable(getattr(voyager, "current_speculative_next_task", None))
    status.last_speculative_decision = _to_jsonable(getattr(voyager, "last_speculative_decision", None))
    status.last_inventory_plan = _to_jsonable(getattr(voyager, "last_inventory_plan", None))
    status.active_plan_state = _to_jsonable(getattr(getattr(voyager, "curriculum_agent", None), "active_plan_state", None))
    status.last_task_contract_decision = _to_jsonable(
        getattr(voyager, "last_task_contract_decision", None)
        or getattr(getattr(voyager, "curriculum_agent", None), "last_task_contract_decision", None)
    )
    status.current_task_bookkeeping = current_task_bookkeeping
    status.last_task_bookkeeping = last_task_bookkeeping
    status.last_world_effect_verification = _to_jsonable(getattr(voyager, "last_world_effect_verification", None))
    status.last_critic_result = last_critic_result
    status.last_recovery_boundary = _to_jsonable(getattr(voyager, "last_recovery_boundary", None))
    status.execution_session = _to_jsonable(getattr(voyager, "execution_session", None))
    status.reset_audit_log = _to_jsonable(getattr(voyager, "reset_audit_log", None)) or []


class RunnerStatus:
    def __init__(self, path: Path, mode: str, goal: str) -> None:
        self.path = path
        self.mode = mode
        self.goal = goal
        self.started_at = time.time()
        self.last_error: str | None = None
        self.last_result: dict[str, Any] | None = None
        self.current_task: str | None = None
        self.current_task_stage: str | None = None
        self.display_stage: str | None = None
        self.last_progress_message: str | None = None
        self.progress_messages: list[str] = []
        self.completed_tasks: list[str] = []
        self.failed_tasks: list[Any] = []
        self.observation: dict[str, Any] = {}
        self.running = False
        self.iteration = 0
        self.resume_checkpoint: dict[str, Any] | None = None
        self.last_rollout_info: dict[str, Any] | None = None
        self.last_task_result: dict[str, Any] | None = None
        self.last_completion_reason: str | None = None
        self.last_success: bool | None = None
        self.last_search_metrics: dict[str, Any] | None = None
        self.speculative_next_task: dict[str, Any] | None = None
        self.last_speculative_decision: dict[str, Any] | None = None
        self.last_inventory_plan: dict[str, Any] | None = None
        self.active_plan_state: dict[str, Any] | None = None
        self.last_task_contract_decision: dict[str, Any] | None = None
        self.current_task_bookkeeping: dict[str, Any] | None = None
        self.last_task_bookkeeping: dict[str, Any] | None = None
        self.last_world_effect_verification: dict[str, Any] | None = None
        self.last_critic_result: dict[str, Any] | None = None
        self.last_recovery_boundary: dict[str, Any] | None = None
        self.execution_session: dict[str, Any] | None = None
        self.reset_audit_log: list[dict[str, Any]] = []
        self.search_metrics_history: list[dict[str, Any]] = []
        self._last_search_metrics_fingerprint: str | None = None

    def write(self, **extra: Any) -> None:
        metrics_payload = _to_jsonable(self.last_search_metrics)
        if isinstance(metrics_payload, dict):
            fingerprint = json.dumps(metrics_payload, ensure_ascii=False, sort_keys=True)
            if fingerprint != self._last_search_metrics_fingerprint:
                self._last_search_metrics_fingerprint = fingerprint
                self.search_metrics_history.append(metrics_payload)
                self.search_metrics_history = self.search_metrics_history[-10:]
        payload = {
            "mode": self.mode,
            "goal": self.goal,
            "started_at": self.started_at,
            "updated_at": time.time(),
            "running": self.running,
            "current_task": self.current_task,
            "current_task_stage": self.current_task_stage,
            "display_stage": self.display_stage,
            "completed_tasks": self.completed_tasks,
            "failed_tasks": self.failed_tasks,
            "last_error": self.last_error,
            "last_result": self.last_result,
            "observation": self.observation,
            "connection_state": self.observation.get("connection_state") if isinstance(self.observation, dict) else None,
            "connection_note": self.observation.get("connection_note") if isinstance(self.observation, dict) else None,
            "last_death_event": self.observation.get("last_death_event") if isinstance(self.observation, dict) else None,
            "death_event_log_path": self.observation.get("death_event_log_path") if isinstance(self.observation, dict) else None,
            "last_progress_message": self.last_progress_message,
            "progress_messages": self.progress_messages,
            "iteration": self.iteration,
            "resume_checkpoint": self.resume_checkpoint,
            "last_rollout_info": self.last_rollout_info,
            "last_task_result": self.last_task_result,
            "last_completion_reason": self.last_completion_reason,
            "last_success": self.last_success,
            "last_search_metrics": self.last_search_metrics,
            "speculative_next_task": self.speculative_next_task,
            "last_speculative_decision": self.last_speculative_decision,
            "last_inventory_plan": self.last_inventory_plan,
            "active_plan_state": self.active_plan_state,
            "last_task_contract_decision": self.last_task_contract_decision,
            "current_task_bookkeeping": self.current_task_bookkeeping,
            "last_task_bookkeeping": self.last_task_bookkeeping,
            "last_world_effect_verification": self.last_world_effect_verification,
            "last_critic_result": self.last_critic_result,
            "last_recovery_boundary": self.last_recovery_boundary,
            "execution_session": self.execution_session,
            "reset_audit_log": self.reset_audit_log,
            "search_metrics_history": self.search_metrics_history,
        }
        payload.update(extra)
        json_payload = _to_jsonable(payload)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.path.write_text(json.dumps(json_payload, ensure_ascii=False, indent=2), encoding="utf-8")
        phase = payload.get("last_phase") or ("running" if self.running else "idle")
        task = payload.get("current_task") or self.goal
        line = _render_runner_status_line(json_payload if isinstance(json_payload, dict) else payload)
        _set_console_title("Voyager-Runner | Minecraft status board")
        _write_status_line(line)


def _openai_base_url() -> str | None:
    target = str(OPENAI_CHAT_COMPLETIONS_URL or "").strip()
    if not target:
        return None
    parsed = urlparse(target)
    if not parsed.scheme or not parsed.netloc:
        return None
    path = parsed.path or ""
    if path.endswith("/chat/completions"):
        path = path[: -len("/chat/completions")]
    if not path:
        path = "/v1"
    return f"{parsed.scheme}://{parsed.netloc}{path}"


def configure_environment() -> None:
    if OPENAI_API_KEY:
        os.environ["OPENAI_API_KEY"] = OPENAI_API_KEY
    base = _openai_base_url()
    if base:
        os.environ["OPENAI_API_BASE"] = base
        os.environ.setdefault("OPENAI_BASE_URL", base)
    if VOYAGER_ACTION_BACKEND:
        os.environ["VOYAGER_ACTION_BACKEND"] = VOYAGER_ACTION_BACKEND
    if VOYAGER_ACTION_BACKEND.strip().lower() == "codex-gateway" and VOYAGER_CODEX_GATEWAY_URL:
        os.environ["VOYAGER_CODEX_GATEWAY_URL"] = VOYAGER_CODEX_GATEWAY_URL
    if VOYAGER_ACTION_BACKEND.strip().lower() == "codex-gateway" and VOYAGER_CODEX_MODEL:
        os.environ["VOYAGER_CODEX_MODEL"] = VOYAGER_CODEX_MODEL


def _to_jsonable(value: Any) -> Any:
    if isinstance(value, (str, int, float, bool)) or value is None:
        return value
    if isinstance(value, dict):
        return {str(k): _to_jsonable(v) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [_to_jsonable(v) for v in value]
    if hasattr(value, "x") and hasattr(value, "y") and hasattr(value, "z"):
        try:
            return {"x": float(value.x), "y": float(value.y), "z": float(value.z)}
        except Exception:
            return str(value)
    return str(value)


def _fetch_bridge_telemetry() -> dict[str, Any]:
    url = f"http://{BRIDGE_HTTP_HOST}:{BRIDGE_HTTP_PORT}/telemetry"
    req = urllib_request.Request(
        url,
        data=b"{}",
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    try:
        with urllib_request.urlopen(req, timeout=2.0) as resp:
            payload = json.loads(resp.read().decode("utf-8", errors="replace"))
    except (urllib_error.URLError, TimeoutError, json.JSONDecodeError, OSError, ValueError):
        return {}
    if not isinstance(payload, dict):
        return {}
    status = payload.get("status") if isinstance(payload.get("status"), dict) else {}
    result: dict[str, Any] = {
        "inventory": payload.get("inventory") if isinstance(payload.get("inventory"), dict) else {},
        "position": status.get("position") if isinstance(status.get("position"), dict) else None,
        "health": status.get("health"),
        "hunger": status.get("food"),
        "connection_state": payload.get("connectionState") if isinstance(payload.get("connectionState"), str) else None,
        "connection_note": payload.get("connectionNote") if isinstance(payload.get("connectionNote"), str) else None,
        "last_death_event": payload.get("lastDeathEvent") if isinstance(payload.get("lastDeathEvent"), dict) else None,
        "death_event_log_path": payload.get("deathEventLogPath") if isinstance(payload.get("deathEventLogPath"), str) else None,
        "recorded_at": payload.get("recordedAt"),
    }
    entities = status.get("entities")
    if isinstance(entities, dict):
        result["nearby_entities"] = entities
    return result


def _merge_live_telemetry(observation: dict[str, Any], live_telemetry: dict[str, Any]) -> dict[str, Any]:
    merged = dict(observation)
    if not live_telemetry:
        return merged
    if isinstance(live_telemetry.get("inventory"), dict) and (not isinstance(merged.get("inventory"), dict) or not merged.get("inventory")):
        merged["inventory"] = live_telemetry.get("inventory")
    if isinstance(live_telemetry.get("position"), dict) and not isinstance(merged.get("position"), dict):
        merged["position"] = live_telemetry.get("position")
    if live_telemetry.get("health") is not None and merged.get("health") is None:
        merged["health"] = live_telemetry.get("health")
    if live_telemetry.get("hunger") is not None and merged.get("hunger") is None:
        merged["hunger"] = live_telemetry.get("hunger")
    if isinstance(live_telemetry.get("nearby_entities"), dict) and not isinstance(merged.get("nearby_entities"), dict):
        merged["nearby_entities"] = live_telemetry.get("nearby_entities")
    for key in ("connection_state", "connection_note", "last_death_event", "death_event_log_path", "recorded_at"):
        if live_telemetry.get(key) is not None:
            merged[key] = live_telemetry.get(key)
    return merged


def summarize_events(events: Any) -> dict[str, Any]:
    if not isinstance(events, list):
        return {}
    summary: dict[str, Any] = {}
    progress_messages: list[str] = []
    for event in events:
        if not (isinstance(event, (list, tuple)) and len(event) == 2):
            continue
        _, payload = event
        if not isinstance(payload, dict):
            continue
        chat_log = payload.get("onChat")
        if isinstance(chat_log, str) and chat_log.strip():
            for line in chat_log.splitlines():
                clean = line.strip()
                if clean:
                    progress_messages.append(clean)
        if isinstance(payload.get("inventory"), dict):
            summary["inventory"] = payload.get("inventory")
        status = payload.get("status")
        if isinstance(status, dict):
            if status.get("position") is not None:
                summary["position"] = _to_jsonable(status.get("position"))
            if status.get("health") is not None:
                summary["health"] = status.get("health")
            if status.get("food") is not None:
                summary["hunger"] = status.get("food")
            if status.get("biome") is not None:
                summary["biome"] = status.get("biome")
            entities = status.get("entities")
            if isinstance(entities, dict):
                summary["nearby_entities"] = entities
        if isinstance(payload.get("voxels"), list):
            summary["nearby_blocks"] = payload.get("voxels")[:16]
        if isinstance(payload.get("connectionState"), str):
            summary["connection_state"] = payload.get("connectionState")
        if isinstance(payload.get("connectionNote"), str):
            summary["connection_note"] = payload.get("connectionNote")
        if isinstance(payload.get("lastDeathEvent"), dict):
            summary["last_death_event"] = payload.get("lastDeathEvent")
        if isinstance(payload.get("deathEventLogPath"), str):
            summary["death_event_log_path"] = payload.get("deathEventLogPath")
    if progress_messages:
        summary["progress_messages"] = progress_messages[-5:]
        summary["last_progress_message"] = progress_messages[-1]
    return summary


def inspect_resume_checkpoint(ckpt_dir: str) -> dict[str, Any]:
    ckpt_path = Path(ckpt_dir)
    action_state = ckpt_path / "action" / "chest_memory.json"
    events_dir = ckpt_path / "events"
    summary: dict[str, Any] = {
        "checkpoint_dir": str(ckpt_path),
        "action_state_present": action_state.exists(),
        "events_dir_present": events_dir.exists(),
        "events_file_count": 0,
        "resume_enabled": False,
        "reason": "missing_checkpoint_state",
        "warnings": [],
        "malformed_event_files": [],
    }
    if not action_state.exists() or not events_dir.exists():
        return summary

    event_files = sorted(path for path in events_dir.iterdir() if path.is_file())
    summary["events_file_count"] = len(event_files)
    if not event_files:
        summary["reason"] = "missing_event_history"
        return summary

    inspected_payloads: list[tuple[Path, list[Any]]] = []
    inspected_files = 0
    for record in reversed(event_files):
        inspected_files += 1
        try:
            payload = json.loads(record.read_text(encoding="utf-8"))
        except Exception:
            summary["malformed_event_files"].append(record.name)
            continue
        if not isinstance(payload, list):
            summary["malformed_event_files"].append(record.name)
            continue
        if payload:
            inspected_payloads.append((record, payload))
        if len(inspected_payloads) >= 20 or inspected_files >= 200:
            break

    observe_records = 0
    missing_position_records = 0
    missing_inventory_used_records = 0
    for _, payload in reversed(inspected_payloads):
        for item in payload:
            if not (isinstance(item, list) and len(item) == 2 and isinstance(item[1], dict)):
                continue
            if item[0] != "observe":
                continue
            observe_records += 1
            status = item[1].get("status") if isinstance(item[1].get("status"), dict) else {}
            if status.get("position") is None:
                missing_position_records += 1
            if status.get("inventoryUsed") is None:
                missing_inventory_used_records += 1

    if summary["malformed_event_files"]:
        summary["reason"] = "malformed_event_history"
        summary["warnings"].append("Malformed resume event files detected; disabling resume for safety.")
        return summary

    if observe_records == 0:
        summary["reason"] = "missing_observe_history"
        summary["warnings"].append("Resume event history contained no observe records; disabling resume for safety.")
        return summary

    if missing_position_records == observe_records or missing_inventory_used_records == observe_records:
        summary["reason"] = "insufficient_observation_telemetry"
        summary["warnings"].append("Recent resume observe records were missing required telemetry; disabling resume for safety.")
        return summary

    if missing_position_records:
        summary["warnings"].append(f"{missing_position_records} recent resume records were missing position telemetry.")
    if missing_inventory_used_records:
        summary["warnings"].append(f"{missing_inventory_used_records} recent resume records were missing inventoryUsed telemetry.")

    summary["resume_enabled"] = True
    summary["reason"] = "checkpoint_ready"
    return summary


def build_voyager(goal: str) -> tuple[Any, dict[str, Any]]:
    from voyager import Voyager  # type: ignore  # noqa: E402

    codex_action = VOYAGER_ACTION_BACKEND.strip().lower() == "codex-gateway"
    ckpt_dir = str(DEFAULT_CKPT_DIR)
    skill_library_dir = str(DEFAULT_SKILL_LIBRARY_DIR)
    mc_port = int(os.getenv("MINEFLAYER_PORT", "25565"))
    resume_summary = inspect_resume_checkpoint(ckpt_dir)
    resume_ready = bool(resume_summary.get("resume_enabled"))
    events_dir = Path(ckpt_dir) / "events"
    resumed_iterations = len(list(events_dir.glob("*"))) if events_dir.exists() else 0
    max_iterations = max(160, resumed_iterations + 160)
    voyager = Voyager(
        mc_port=mc_port,
        openai_api_key=OPENAI_API_KEY,
        env_wait_ticks=20,
        env_request_timeout=600,
        max_iterations=max_iterations,
        action_agent_model_name=("codex-gateway" if codex_action else MINDCRAFT_LOCAL_MODEL),
        action_agent_temperature=0,
        action_agent_task_max_retries=4,
        action_agent_llm_url=(VOYAGER_CODEX_GATEWAY_URL if codex_action else MINDCRAFT_LOCAL_LLM_URL),
        curriculum_agent_model_name=VOYAGER_CURRICULUM_MODEL_NAME,
        curriculum_agent_temperature=0,
        curriculum_agent_qa_model_name=VOYAGER_CURRICULUM_MODEL_NAME,
        curriculum_agent_qa_temperature=0,
        curriculum_agent_llm_url=VOYAGER_CURRICULUM_LLM_URL,
        curriculum_agent_qa_llm_url=VOYAGER_CURRICULUM_LLM_URL,
        critic_agent_model_name=VOYAGER_CRITIC_MODEL_NAME,
        critic_agent_temperature=0,
        critic_agent_llm_url=VOYAGER_CRITIC_LLM_URL,
        skill_manager_model_name=VOYAGER_SKILL_MODEL_NAME,
        skill_manager_temperature=0,
        skill_manager_llm_url=VOYAGER_SKILL_LLM_URL,
        openai_api_request_timeout=240,
        ckpt_dir=ckpt_dir,
        skill_library_dir=skill_library_dir,
        resume=resume_ready,
    )
    return voyager, resume_summary


def monitor_loop(voyager: Any, status: RunnerStatus, stop_event: threading.Event) -> None:
    while not stop_event.is_set():
        try:
            try:
                voyager.refresh_live_state(refresh_messages=False)
            except Exception:
                pass
            status.current_task = voyager.task
            status.current_task_stage = getattr(voyager, "last_phase", None)
            status.display_stage = _compute_display_stage(
                running=True,
                current_task=voyager.task,
                last_phase=getattr(voyager, "last_phase", None),
                last_error=status.last_error,
                current_task_bookkeeping=getattr(voyager, "current_task_bookkeeping", None),
                last_task_result=getattr(voyager, "last_task_result", None),
            )
            status.iteration = max(0, voyager.recorder.iteration)
            status.completed_tasks = list(getattr(voyager.curriculum_agent, "completed_tasks", []) or [])
            status.failed_tasks = list(getattr(voyager.curriculum_agent, "failed_tasks", []) or [])
            summary = summarize_events(getattr(voyager, "last_events", None))
            status.observation = _merge_live_telemetry(summary, _fetch_bridge_telemetry())
            status.last_progress_message = status.observation.get("last_progress_message") if isinstance(status.observation.get("last_progress_message"), str) else None
            status.progress_messages = list(status.observation.get("progress_messages", []) or [])[:5]
            _copy_task_status_from_voyager(status, voyager)
            status.write(
                last_phase=getattr(voyager, "last_phase", None),
                last_phase_at=getattr(voyager, "last_phase_at", None),
                last_action_program_name=getattr(voyager, "last_action_program_name", None),
                last_action_code=getattr(voyager, "last_action_code", None),
                last_ai_response_preview=getattr(voyager, "last_ai_response_preview", None),
                last_env_event_count=getattr(voyager, "last_env_event_count", None),
                last_critique=getattr(voyager, "last_critique", None),
            )
        except Exception as exc:
            status.last_error = f"monitor_error:{exc}"
            status.write()
            _append_error_log(RUNNER_ERROR_LOG_PATH, "monitor_loop", str(exc))
        stop_event.wait(1.0)


def run_learn(voyager: Any, status: RunnerStatus) -> dict[str, Any]:
    return voyager.learn()


def run_inference(voyager: Any, status: RunnerStatus) -> dict[str, Any]:
    return voyager.inference(task=status.goal)


def main() -> int:
    _configure_console_encoding()
    parser = argparse.ArgumentParser()
    parser.add_argument("--mode", choices=["learn", "inference"], required=True)
    parser.add_argument("--goal", required=True)
    parser.add_argument("--status-path", default=str(DEFAULT_STATUS_PATH))
    args = parser.parse_args()

    configure_environment()
    status = RunnerStatus(Path(args.status_path), args.mode, args.goal or DEFAULT_GOAL)
    status.running = True
    status.write(
        note="starting",
        python_executable=sys.executable,
        upstream_root=str(UPSTREAM_ROOT),
        status_path=str(status.path),
    )

    voyager: Any | None = None
    stop_event: threading.Event | None = None
    monitor: threading.Thread | None = None
    try:
        voyager, resume_summary = build_voyager(status.goal)
        status.resume_checkpoint = _to_jsonable(resume_summary)
        stop_event = threading.Event()
        monitor = threading.Thread(target=monitor_loop, args=(voyager, status, stop_event), daemon=True)
        monitor.start()
        if args.mode == "learn":
            result = run_learn(voyager, status)
        else:
            result = run_inference(voyager, status)
        status.last_result = _to_jsonable(result)
        status.last_error = None
        status.observation = _merge_live_telemetry(summarize_events(getattr(voyager, "last_events", None)), _fetch_bridge_telemetry())
        status.current_task_stage = getattr(voyager, "last_phase", None)
        status.display_stage = _compute_display_stage(
            running=True,
            current_task=getattr(voyager, "task", None),
            last_phase=getattr(voyager, "last_phase", None),
            last_error=status.last_error,
            current_task_bookkeeping=getattr(voyager, "current_task_bookkeeping", None),
            last_task_result=getattr(voyager, "last_task_result", None),
        )
        status.last_progress_message = status.observation.get("last_progress_message") if isinstance(status.observation.get("last_progress_message"), str) else None
        status.progress_messages = list(status.observation.get("progress_messages", []) or [])[:5]
        status.completed_tasks = list(getattr(voyager.curriculum_agent, "completed_tasks", []) or [])
        status.failed_tasks = list(getattr(voyager.curriculum_agent, "failed_tasks", []) or [])
        _copy_task_status_from_voyager(status, voyager)
        status.running = False
        status.write(
            note="finished",
            python_executable=sys.executable,
            upstream_root=str(UPSTREAM_ROOT),
            status_path=str(status.path),
            last_phase=getattr(voyager, "last_phase", None),
            last_phase_at=getattr(voyager, "last_phase_at", None),
            last_action_program_name=getattr(voyager, "last_action_program_name", None),
            last_action_code=getattr(voyager, "last_action_code", None),
            last_ai_response_preview=getattr(voyager, "last_ai_response_preview", None),
            last_env_event_count=getattr(voyager, "last_env_event_count", None),
            last_critique=getattr(voyager, "last_critique", None),
        )
        return 0
    except Exception as exc:
        status.last_error = str(exc)
        status.last_result = {
            "status": "error",
            "reason": "upstream_runner_exception",
            "error": str(exc),
            "error_type": type(exc).__name__,
        }
        if voyager is not None:
            status.observation = _merge_live_telemetry(summarize_events(getattr(voyager, "last_events", None)), _fetch_bridge_telemetry())
            status.current_task_stage = getattr(voyager, "last_phase", None)
            status.display_stage = _compute_display_stage(
                running=False,
                current_task=getattr(voyager, "task", None),
                last_phase=getattr(voyager, "last_phase", None),
                last_error=status.last_error,
                current_task_bookkeeping=getattr(voyager, "current_task_bookkeeping", None),
                last_task_result=getattr(voyager, "last_task_result", None),
            )
            status.last_progress_message = status.observation.get("last_progress_message") if isinstance(status.observation.get("last_progress_message"), str) else None
            status.progress_messages = list(status.observation.get("progress_messages", []) or [])[:5]
            status.completed_tasks = list(getattr(voyager.curriculum_agent, "completed_tasks", []) or [])
            status.failed_tasks = list(getattr(voyager.curriculum_agent, "failed_tasks", []) or [])
            _copy_task_status_from_voyager(status, voyager)
        status.running = False
        status.write(
            note="failed",
            python_executable=sys.executable,
            upstream_root=str(UPSTREAM_ROOT),
            status_path=str(status.path),
            traceback=traceback.format_exc(),
            last_phase=getattr(voyager, "last_phase", None) if voyager is not None else None,
            last_phase_at=getattr(voyager, "last_phase_at", None) if voyager is not None else None,
            last_action_program_name=getattr(voyager, "last_action_program_name", None) if voyager is not None else None,
            last_action_code=getattr(voyager, "last_action_code", None) if voyager is not None else None,
            last_ai_response_preview=getattr(voyager, "last_ai_response_preview", None) if voyager is not None else None,
            last_env_event_count=getattr(voyager, "last_env_event_count", None) if voyager is not None else None,
            last_critique=getattr(voyager, "last_critique", None) if voyager is not None else None,
        )
        _append_error_log(RUNNER_ERROR_LOG_PATH, "upstream_runner", str(exc), traceback.format_exc())
        return 1
    finally:
        if stop_event is not None:
            stop_event.set()
        _leave_alternate_screen()
        sys.stdout.write("\n")
        sys.stdout.flush()


if __name__ == "__main__":
    raise SystemExit(main())
