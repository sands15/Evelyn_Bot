from __future__ import annotations

import argparse
import asyncio
import json
import os
import subprocess
import threading
import time
from pathlib import Path
from typing import Any

from aiohttp import web

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
)
from .runtime_config_schema import (
    MINDCRAFT_SERVICE_SETTINGS,
    load_runtime_settings,
)
from .runtime_error_observability import RuntimeErrorCounter


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
WORLD_LEASE_SECRET_PATH = (
    RUNTIME_ARTIFACTS_ROOT
    / "secrets"
    / "minecraft_world_lease.json"
)
WORLD_LEASE_GUARD_INTERVAL_SEC = 5.0
_MINDCRAFT_CONFIG = load_runtime_settings(
    "mindcraft",
    MINDCRAFT_SERVICE_SETTINGS,
)
STATUS_PATH = Path(
    _MINDCRAFT_CONFIG["MINDCRAFT_STATUS_PATH"]
    or RUNTIME_ARTIFACTS_ROOT / "mindcraft" / "status.json"
)
GOAL_STATE_PATH = RUNTIME_ARTIFACTS_ROOT / "voyager" / "voyager_goal_state.json"
LOG_PATH = RUNTIME_ARTIFACTS_ROOT / "logs" / "mindcraft.log"
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


def _clean_goal(value: Any) -> str:
    return str(value or "").strip() or DEFAULT_GOAL


def _allowed_players() -> list[str]:
    return [
        item.strip()
        for item in str(_MINDCRAFT_CONFIG["MINDCRAFT_ALLOWED_PLAYERS"]).split(",")
        if item.strip()
    ]


def _functional_readiness(
    *,
    world_lease_authorized: bool,
    running: bool,
    telemetry_fresh: bool,
    connected: bool,
    telemetry: dict[str, Any],
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


class MindcraftRuntime:
    def __init__(self) -> None:
        self._process: subprocess.Popen[str] | None = None
        self._log_handle: Any | None = None
        self._lock = threading.RLock()
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
            "load_memory": True,
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

    def start(self, goal: str | None = None) -> None:
        with self._lock:
            requested_goal = _clean_goal(goal or self.get_goal())
            self._manual_stop = False
            self.persist_goal(requested_goal)
            if self.process_alive():
                return
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

            LOG_PATH.parent.mkdir(parents=True, exist_ok=True)
            self._log_handle = LOG_PATH.open("a", encoding="utf-8", buffering=1)
            try:
                self._process = subprocess.Popen(
                    ["node", "main.js"],
                    cwd=str(MINDCRAFT_ROOT),
                    env=env,
                    stdout=self._log_handle,
                    stderr=subprocess.STDOUT,
                    text=True,
                )
            except Exception as exc:
                self.runtime_errors.record("mindcraft_start_failed", exc)
                raise
            self._started_at = time.time()
            self._last_exit_code = None
            self._restart_backoff_until = 0.0

    def _cleanup_process_state(self) -> None:
        process = self._process
        if process is not None and process.poll() is not None:
            self._last_exit_code = process.returncode
            self._process = None
            if self._log_handle is not None:
                try:
                    self._log_handle.close()
                except Exception as exc:
                    self.runtime_errors.record("mindcraft_log_close_failed", exc)
                finally:
                    self._log_handle = None

    def _ensure_process_running(self) -> None:
        if self._manual_stop or not self._auto_restart:
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
                telemetry = _read_json(STATUS_PATH)
                telemetry["last_error"] = (
                    f"mindcraft_auto_restart_failed:{type(exc).__name__}"
                )
                _write_json(STATUS_PATH, telemetry)

    def reconcile_world_lease(self) -> bool:
        lease_status, error = load_guarded_world_lease(
            WORLD_LEASE_STATUS_PATH,
            WORLD_LEASE_SECRET_PATH,
        )
        authorized = bool(lease_status)
        self._last_world_lease_error_code = error
        if not authorized:
            if self.process_alive() or not self._manual_stop:
                self.stop()
            return False
        self._ensure_process_running()
        return True

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
                self._process = None
                if self._log_handle is not None:
                    self._log_handle.close()
                    self._log_handle = None
                telemetry = _read_json(STATUS_PATH)
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
                _write_json(STATUS_PATH, telemetry)
            except Exception as exc:
                self.runtime_errors.record("mindcraft_stop_failed", exc)
                raise

    def restart_for_goal(self, goal: str) -> None:
        with self._lock:
            was_running = self.process_alive()
            self.persist_goal(goal)
            if was_running:
                self.stop()
                self.start(goal)

    def build_status(self) -> dict[str, Any]:
        world_lease_authorized = self.reconcile_world_lease()
        process = self._process
        if process is not None and process.poll() is not None:
            self._last_exit_code = process.returncode
        self._cleanup_process_state()
        running = self.process_alive()
        telemetry = _read_json(STATUS_PATH)
        updated_at = telemetry.get("updated_at")
        telemetry_fresh = isinstance(updated_at, (int, float)) and time.time() - float(updated_at) <= 10
        connected = bool(running and telemetry_fresh and telemetry.get("connected"))
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
                "escalation": str(_MINDCRAFT_CONFIG["MINDCRAFT_CODEX_MODEL"]),
            },
            "codex_gateway": {
                "enabled": True,
                "url": str(_MINDCRAFT_CONFIG["MINDCRAFT_CODEX_GATEWAY_URL"]),
                "model": str(_MINDCRAFT_CONFIG["MINDCRAFT_CODEX_MODEL"]),
            },
            "command_policy": "outbound_chat_disabled_by_default",
            "blocked_command_count": int(telemetry.get("blocked_command_count") or 0),
            "last_blocked_command": telemetry.get("last_blocked_command"),
            "telemetry_fresh": telemetry_fresh,
            "updated_at": updated_at or self._started_at or time.time(),
            "runner_exit_code": self._last_exit_code,
            "world_lease_authorized": world_lease_authorized,
            "world_lease_error_code": (
                "" if world_lease_authorized
                else self._last_world_lease_error_code
            ),
            "functional_readiness": functional_readiness,
            "configuration": _MINDCRAFT_CONFIG.public_summary(),
            **self.runtime_errors.snapshot(),
            "note": "Evelyn Mindcraft v0.1.4 runtime with non-operator survival policy.",
        }


STATE = MindcraftRuntime()


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


async def start(request: web.Request) -> web.Response:
    payload = await request.json() if request.can_read_body else {}
    valid, error = validate_world_lease_request(
        payload,
        status_path=WORLD_LEASE_STATUS_PATH,
        secret_path=WORLD_LEASE_SECRET_PATH,
    )
    if not valid:
        raise web.HTTPForbidden(
            text=json.dumps({"error": error}),
            content_type="application/json",
        )
    STATE.start(_clean_goal((payload or {}).get("goal") or STATE.get_goal()))
    return web.json_response(STATE.build_status())


async def stop(_: web.Request) -> web.Response:
    STATE.stop()
    return web.json_response(STATE.build_status())


async def set_goal(request: web.Request) -> web.Response:
    payload = await request.json() if request.can_read_body else {}
    valid, error = validate_world_lease_request(
        payload,
        status_path=WORLD_LEASE_STATUS_PATH,
        secret_path=WORLD_LEASE_SECRET_PATH,
    )
    if not valid:
        raise web.HTTPForbidden(
            text=json.dumps({"error": error}),
            content_type="application/json",
        )
    goal = str((payload or {}).get("goal") or "").strip()
    if not goal:
        raise web.HTTPBadRequest(text=json.dumps({"error": "goal text is empty"}), content_type="application/json")
    STATE.restart_for_goal(goal)
    return web.json_response(STATE.build_status())


async def _cleanup(_: web.Application) -> None:
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


def build_app() -> web.Application:
    app = web.Application()
    app.router.add_get("/health", health)
    app.router.add_get("/status", status)
    app.router.add_get("/observe", observe)
    app.router.add_post("/start", start)
    app.router.add_post("/stop", stop)
    app.router.add_post("/goal", set_goal)
    app.cleanup_ctx.append(_world_lease_guard_context)
    app.on_cleanup.append(_cleanup)
    return app


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8765)
    args = parser.parse_args()
    web.run_app(build_app(), host=args.host, port=args.port, handle_signals=True, print=None)


if __name__ == "__main__":
    main()
