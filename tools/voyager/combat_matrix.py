from __future__ import annotations

import argparse
from collections import deque
import itertools
import json
import re
import shutil
import socket
import subprocess
import threading
import time
from uuid import uuid4
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any, Callable, Mapping, Sequence


MATRIX_SCHEMA = "evelyn.validation.combat-matrix.v1"
REPORT_SCHEMA = "evelyn.validation.combat-matrix-report.v1"
PROJECTILE_REPORT_SCHEMA = "evelyn.validation.projectile-smoke-report.v1"
FIXTURE_RELATIVE = Path("runtime_artifacts/validation/combat_matrix_batch")
GAME_PORT = 25573
PRODUCTION_PORT = 25565
CONTAINER_NAME = "evelyn-combat-matrix-batch"
PRODUCTION_CONTAINER = "evelyn-mindcraft"
SAFE_DISTANCE_METERS = 18.0
SAFE_STABLE_MS = 2_000
CELL_TIMEOUT_SECONDS = 35.0
BOT_READY_TIMEOUT_SECONDS = 30.0
SERVER_START_TIMEOUT_SECONDS = 90.0
MAX_REFLEX_TO_ACTION_MS = 100
MAX_REFLEX_DURATION_MS = 1_250
MAX_P1_AFTER_REFLEX_MS = 250
MAX_P1_ACTION_MS = 100
PROJECTILE_EFFECT_SETTLE_SECONDS = 1.25
PROJECTILE_TIMEOUT_SECONDS = 5.0
EMERGENCY_ADMISSION_TIMEOUT_SECONDS = 15.0
EMERGENCY_LOW_HEALTH_SETTLE_SECONDS = 4.0
BOT_IMAGE = "evelyn-fast-control-voyager:latest"
BOT_USERNAME = "Evelyn_0428"
OWNER_LABEL = "evelyn.validation.owner"
RUN_LABEL = "evelyn.validation.run"
OWNER_VALUE = "combat_matrix_batch"
SCRIPT_REPO_ROOT = Path(__file__).resolve().parents[2]
SMOKE_IDS = (
    "single_zombie__unprotected__day",
    "single_skeleton__protected__day",
)
PROJECTILE_CELL_ID = "incoming_arrow__shield"
EMERGENCY_ZOMBIE_CELL_ID = "single_zombie__emergency_melee"
BASE_GAMERULES = (
    ("spawn_mobs", False),
    ("natural_health_regeneration", False),
    ("advance_time", False),
    ("advance_weather", False),
    ("mob_drops", False),
    ("mob_griefing", False),
    ("keep_inventory", True),
)
INFRASTRUCTURE_CODES = frozenset({
    "batch_infrastructure_failed",
    "bot_connection_unverified",
    "cell_infrastructure_failed",
    "cell_not_started",
    "emergency_admission_unverified",
    "emergency_spawn_unverified",
    "production_mindcraft_started_during_batch",
    "projectile_cell_infrastructure_failed",
    "projectile_observation_missing",
    "projectile_setup_unverified",
    "scenario_setup_unverified",
    "server_cleanup_failed",
})
RUNTIME_ERROR_CODES = frozenset({
    "mindcraft_runtime_error",
    "startup_child_not_running",
    "startup_process_error",
    "startup_protocol_error",
})
STARTUP_CHILD_STATES = frozenset({"running", "not_running", "unknown"})
STARTUP_LOG_PATTERNS = (
    ("startup_process_failed", (
        "failed to start agent process", "err_module_not_found", "uncaught exception",
        "agent process exited", "exited too quickly",
        "syntaxerror", "referenceerror", "typeerror",
    )),
    ("startup_protocol_failed", (
        "partialreaderror", "unsupported protocol", "protocol error", "packet parse",
    )),
    ("startup_authentication_failed", (
        "authentication failed", "failed to authenticate", "invalid credentials",
        "invalid_grant", "xboxreplay",
    )),
    ("startup_connection_failed", (
        "econnrefused", "econnreset", "enotfound", "etimedout",
        "connect timeout", "socket hang up",
    )),
)
STARTUP_LOG_CATEGORIES = frozenset({
    *(category for category, _patterns in STARTUP_LOG_PATTERNS),
    "startup_logs_unavailable",
    "startup_logs_unclassified",
})

THREATS: tuple[tuple[str, tuple[str, ...]], ...] = (
    ("single_zombie", ("zombie",)),
    ("single_skeleton", ("skeleton",)),
    ("zombie_skeleton", ("zombie", "skeleton")),
    ("three_zombies", ("zombie", "zombie", "zombie")),
    ("creeper", ("creeper",)),
)
LOADOUTS = ("unprotected", "protected")
TIMES = ("day", "night")


class MatrixSafetyError(ValueError):
    pass


@dataclass(frozen=True)
class Scenario:
    id: str
    threat: str
    mobs: tuple[str, ...]
    loadout: str
    time: str
    time_of_day: int
    expected_disposition: str
    expected_preset: str
    max_damage: float

    @property
    def hostile_count(self) -> int:
        return len(self.mobs)

    def to_dict(self) -> dict[str, Any]:
        result = asdict(self)
        result["mobs"] = list(self.mobs)
        result["hostile_count"] = self.hostile_count
        result["pass_rules"] = pass_rules(self)
        return result


def _expected_disposition(threat: str, loadout: str, time_name: str) -> tuple[str, str]:
    if threat == "creeper":
        return "flee", "disengage"
    if threat == "single_zombie":
        return ("flee", "disengage") if loadout == "unprotected" else ("fight", "melee")
    if loadout == "unprotected" and time_name == "night":
        return "flee", "disengage"
    if loadout == "protected" and threat == "single_skeleton":
        return "fight", "shield_close"
    return "flee", "disengage"


def _max_damage(threat: str, loadout: str, disposition: str) -> float:
    if disposition == "fight" and threat == "creeper":
        return 2.0
    if disposition == "fight":
        return 8.0 if loadout == "unprotected" else 6.0
    if threat in {"zombie_skeleton", "three_zombies"}:
        return 8.0 if loadout == "unprotected" else 6.0
    return 6.0 if loadout == "unprotected" else 4.0


def build_scenarios() -> tuple[Scenario, ...]:
    scenarios = []
    for (threat, mobs), loadout, time_name in itertools.product(THREATS, LOADOUTS, TIMES):
        disposition, preset = _expected_disposition(threat, loadout, time_name)
        scenarios.append(Scenario(
            id=f"{threat}__{loadout}__{time_name}",
            threat=threat,
            mobs=mobs,
            loadout=loadout,
            time=time_name,
            time_of_day=6_000 if time_name == "day" else 13_000,
            expected_disposition=disposition,
            expected_preset=preset,
            max_damage=_max_damage(threat, loadout, disposition),
        ))
    return tuple(scenarios)


def smoke_scenarios() -> tuple[Scenario, ...]:
    by_id = {scenario.id: scenario for scenario in build_scenarios()}
    return tuple(by_id[scenario_id] for scenario_id in SMOKE_IDS)


def emergency_zombie_scenario() -> Scenario:
    return Scenario(
        id=EMERGENCY_ZOMBIE_CELL_ID,
        threat="single_zombie",
        mobs=("zombie",),
        loadout="unprotected",
        time="night",
        time_of_day=13_000,
        expected_disposition="fight",
        expected_preset="melee",
        max_damage=8.0,
    )


def pass_rules(scenario: Scenario) -> dict[str, Any]:
    common = {
        "max_damage": scenario.max_damage,
        "max_duration_ms": 25_000,
        "max_reflex_to_action_ms": MAX_REFLEX_TO_ACTION_MS,
        "max_reflex_duration_ms": MAX_REFLEX_DURATION_MS,
        "max_p1_after_reflex_ms": MAX_P1_AFTER_REFLEX_MS,
        "max_decision_to_action_ms": MAX_P1_ACTION_MS,
        "death_count": 0,
        "terminal_episode_count": 1,
    }
    if scenario.expected_disposition == "fight":
        return {
            **common,
            "expected_preset": scenario.expected_preset,
            "require_verified_clear": True,
            "remaining_hostiles": 0,
        }
    return {
        **common,
        "expected_preset": "disengage",
        "min_safe_distance_meters": SAFE_DISTANCE_METERS,
        "min_safe_stable_ms": SAFE_STABLE_MS,
        "remaining_hostiles": (
            [0, scenario.hostile_count]
            if scenario.threat == "creeper"
            else scenario.hostile_count
        ),
    }


def validate_manifest(scenarios: Sequence[Scenario]) -> None:
    expected = set(itertools.product(
        (name for name, _ in THREATS),
        LOADOUTS,
        TIMES,
    ))
    actual = {(item.threat, item.loadout, item.time) for item in scenarios}
    ids = {item.id for item in scenarios}
    if len(scenarios) != 20 or len(ids) != 20 or actual != expected:
        raise ValueError("combat_matrix_must_be_exact_20_cell_product")
    for item in scenarios:
        if item.expected_disposition not in {"fight", "flee"}:
            raise ValueError("invalid_expected_disposition")
        if item.max_damage < 0 or item.hostile_count < 1:
            raise ValueError("invalid_scenario_limit")


def cleanup_plan(
    repo_root: Path,
    *,
    artifact_root: Path | None = None,
    game_port: int = GAME_PORT,
) -> dict[str, Any]:
    repo = repo_root.resolve()
    expected = (repo / FIXTURE_RELATIVE).resolve()
    requested = (artifact_root or expected).resolve()
    if requested != expected:
        raise MatrixSafetyError("artifact_root_not_exact_validation_fixture")
    if game_port != GAME_PORT or game_port == PRODUCTION_PORT:
        raise MatrixSafetyError("game_port_not_exact_isolated_port")
    validation = (repo / "runtime_artifacts/validation").resolve()
    if expected.parent != validation or expected == repo:
        raise MatrixSafetyError("artifact_root_outside_exact_validation_parent")
    try:
        validation.relative_to(repo)
    except ValueError as error:
        raise MatrixSafetyError("validation_parent_resolves_outside_repo") from error
    if any(path.is_symlink() for path in (repo, validation, expected) if path.exists()):
        raise MatrixSafetyError("cleanup_path_symlink_rejected")
    if CONTAINER_NAME == PRODUCTION_CONTAINER:
        raise MatrixSafetyError("production_container_target_rejected")
    relative = FIXTURE_RELATIVE.as_posix()
    return {
        "container_names": [CONTAINER_NAME],
        "server_pid_files": [f"{relative}/server.pid"],
        "artifact_roots": [relative],
        "ports": [GAME_PORT],
        "wildcards": False,
    }


def _number(value: Any) -> float | None:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    result = float(value)
    return result if result == result and abs(result) != float("inf") else None


def _integer(value: Any) -> int | None:
    return value if isinstance(value, int) and not isinstance(value, bool) else None


def _fixed_code(value: Any, allowed: frozenset[str]) -> str | None:
    return value if isinstance(value, str) and value in allowed else None


def evaluate_scenario(scenario: Scenario, observation: Mapping[str, Any]) -> dict[str, Any]:
    failures: list[str] = []
    episode = observation.get("episode")
    episode = episode if isinstance(episode, Mapping) else {}
    infrastructure_code = _fixed_code(
        observation.get("infrastructure_code"), INFRASTRUCTURE_CODES,
    )
    runtime_error_code = _fixed_code(observation.get("runtime_error"), RUNTIME_ERROR_CODES)
    if observation.get("infrastructure_valid") is not True:
        failures.append("infrastructure_invalid")
    if runtime_error_code is not None:
        failures.append("runtime_error")
    if _integer(observation.get("death_count")) != 0:
        failures.append("death_observed")
    if _integer(observation.get("episode_count")) != 1:
        failures.append("terminal_episode_count_mismatch")
    if episode.get("outcome") != "success" or episode.get("verified") is not True:
        failures.append("terminal_success_unverified")
    if episode.get("tactic") != scenario.expected_preset:
        failures.append("unexpected_combat_preset")

    damage = _number(episode.get("damage"))
    duration = _number(episode.get("duration_ms", episode.get("durationMs")))
    wake = _number(observation.get("wake_to_decision_ms"))
    action = _number(observation.get("decision_to_action_ms"))
    reflex_reason = observation.get("reflex_reason")
    reflex_action = _number(observation.get("reflex_to_action_ms"))
    raw_reflex_durations = observation.get("reflex_durations_ms")
    if isinstance(raw_reflex_durations, Sequence) and not isinstance(
        raw_reflex_durations, (str, bytes, bytearray)
    ):
        reflex_durations = [_number(value) for value in raw_reflex_durations]
    elif "reflex_duration_ms" in observation:
        # Backward-compatible input for pre-chain offline observations.
        reflex_durations = [_number(observation.get("reflex_duration_ms"))]
    else:
        reflex_durations = []
    reflex_total_duration = None
    p1_after_reflex = None
    if damage is None or damage < 0 or damage > scenario.max_damage:
        failures.append("damage_limit_exceeded")
    if duration is None or duration < 0 or duration > 25_000:
        failures.append("duration_limit_exceeded")
    latency_verified = (
        reflex_reason == "hostile" and
        reflex_action is not None and reflex_action >= 0 and
        bool(reflex_durations) and
        all(duration is not None and duration >= 0 for duration in reflex_durations) and
        wake is not None and wake >= 0 and
        action is not None and action >= 0
    )
    if latency_verified:
        reflex_total_duration = sum(reflex_durations)
        p1_after_reflex = wake - reflex_action - reflex_total_duration
        if p1_after_reflex < 0:
            latency_verified = False
    if not latency_verified:
        failures.append("latency_unverified")
    else:
        if reflex_action > MAX_REFLEX_TO_ACTION_MS:
            failures.append("reflex_start_latency_exceeded")
        if any(duration > MAX_REFLEX_DURATION_MS for duration in reflex_durations):
            failures.append("reflex_duration_exceeded")
        if p1_after_reflex > MAX_P1_AFTER_REFLEX_MS:
            failures.append("tactical_latency_exceeded")
        if action > MAX_P1_ACTION_MS:
            failures.append("action_latency_exceeded")

    remaining = _integer(observation.get("remaining_hostiles"))
    stable_distance = _number(observation.get("min_stable_distance_meters"))
    stable_ms = _number(observation.get("safe_stable_ms"))
    if scenario.expected_disposition == "fight":
        if observation.get("verified_clear") is not True or remaining != 0:
            failures.append("fight_clear_unverified")
    else:
        allowed_remaining = (
            {0, scenario.hostile_count}
            if scenario.threat == "creeper"
            else {scenario.hostile_count}
        )
        if remaining not in allowed_remaining:
            failures.append("flee_hostile_removed")
        if stable_distance is None or stable_distance < SAFE_DISTANCE_METERS:
            failures.append("flee_distance_unverified")
        if stable_ms is None or stable_ms < SAFE_STABLE_MS:
            failures.append("flee_stability_unverified")

    metrics = {
        "outcome": episode.get("outcome") if episode.get("outcome") in {"success", "failure", "death", "interrupted"} else None,
        "preset": episode.get("tactic") if episode.get("tactic") in {"disengage", "melee", "bow", "shield_close"} else None,
        "verified": episode.get("verified") is True,
        "damage": damage,
        "duration_ms": duration,
        "reflex_reason": reflex_reason if reflex_reason == "hostile" else None,
        "reflex_to_action_ms": reflex_action,
        "reflex_durations_ms": reflex_durations,
        "reflex_total_duration_ms": reflex_total_duration,
        "p1_after_reflex_ms": p1_after_reflex,
        "wake_to_decision_ms": wake,
        "decision_to_action_ms": action,
        "remaining_hostiles": remaining,
        "infrastructure_code": infrastructure_code,
        "runtime_error_code": runtime_error_code,
        "startup_child_state": _fixed_code(
            observation.get("startup_child_state"), STARTUP_CHILD_STATES,
        ),
        "startup_log_category": _fixed_code(
            observation.get("startup_log_category"), STARTUP_LOG_CATEGORIES,
        ),
    }
    if scenario.expected_disposition == "flee":
        metrics.update({
            "min_stable_distance_meters": stable_distance,
            "safe_stable_ms": stable_ms,
        })
    return {
        "id": scenario.id,
        "passed": not failures,
        "failure_codes": failures,
        "metrics": metrics,
    }


def build_report(
    observations: Mapping[str, Mapping[str, Any]],
    scenarios: Sequence[Scenario] | None = None,
) -> dict[str, Any]:
    selected = tuple(scenarios) if scenarios is not None else build_scenarios()
    known_ids = {
        *(scenario.id for scenario in build_scenarios()),
        EMERGENCY_ZOMBIE_CELL_ID,
    }
    if not selected or len({scenario.id for scenario in selected}) != len(selected):
        raise ValueError("combat_matrix_scenario_selection_invalid")
    if any(scenario.id not in known_ids for scenario in selected):
        raise ValueError("combat_matrix_scenario_selection_unknown")
    if scenarios is None:
        validate_manifest(selected)
    results = []
    for item in selected:
        observation = observations.get(item.id, {})
        results.append(evaluate_scenario(
            item,
            observation if isinstance(observation, Mapping) else {},
        ))
    passed = sum(result["passed"] for result in results)
    infrastructure_failures = sum(
        "infrastructure_invalid" in result["failure_codes"] for result in results
    )
    return {
        "schema": REPORT_SCHEMA,
        "contentFree": True,
        "liveExecution": False,
        "passed": passed == len(results),
        "summary": {
            "total": len(results),
            "passed": passed,
            "failed": len(results) - passed,
            "infrastructureFailures": infrastructure_failures,
        },
        "scenarios": results,
    }


def evaluate_projectile_smoke(observation: Mapping[str, Any]) -> dict[str, Any]:
    failures: list[str] = []
    infrastructure_code = _fixed_code(
        observation.get("infrastructure_code"), INFRASTRUCTURE_CODES,
    )
    runtime_error_code = _fixed_code(observation.get("runtime_error"), RUNTIME_ERROR_CODES)
    if observation.get("infrastructure_valid") is not True:
        failures.append("infrastructure_invalid")
    if runtime_error_code is not None:
        failures.append("runtime_error")
    if _integer(observation.get("death_count")) != 0:
        failures.append("death_observed")
    if observation.get("reflex_reason") != "projectile":
        failures.append("projectile_reflex_unverified")
    reflex_action = _number(observation.get("reflex_to_action_ms"))
    if reflex_action is None or reflex_action < 0:
        failures.append("latency_unverified")
    elif reflex_action > MAX_REFLEX_TO_ACTION_MS:
        failures.append("reflex_start_latency_exceeded")
    if observation.get("response") != "shield":
        failures.append("shield_response_unverified")
    blocked_damage = _integer(observation.get("shield_blocked_damage"))
    if blocked_damage is None or blocked_damage <= 0:
        failures.append("shield_effect_unverified")
    damage = _number(observation.get("damage"))
    if damage is None or damage != 0:
        failures.append("projectile_damage_observed")
    if _integer(observation.get("hostile_count")) != 0:
        failures.append("hostile_reflex_not_isolated")
    return {
        "id": PROJECTILE_CELL_ID,
        "passed": not failures,
        "failure_codes": failures,
        "metrics": {
            "reflex_reason": (
                "projectile" if observation.get("reflex_reason") == "projectile" else None
            ),
            "reflex_to_action_ms": reflex_action,
            "response": "shield" if observation.get("response") == "shield" else None,
            "shield_blocked_damage": blocked_damage,
            "damage": damage,
            "hostile_count": _integer(observation.get("hostile_count")),
            "death_count": _integer(observation.get("death_count")),
            "infrastructure_code": infrastructure_code,
            "runtime_error_code": runtime_error_code,
            "startup_child_state": _fixed_code(
                observation.get("startup_child_state"), STARTUP_CHILD_STATES,
            ),
            "startup_log_category": _fixed_code(
                observation.get("startup_log_category"), STARTUP_LOG_CATEGORIES,
            ),
        },
    }


def build_projectile_report(observation: Mapping[str, Any]) -> dict[str, Any]:
    result = evaluate_projectile_smoke(observation)
    return {
        "schema": PROJECTILE_REPORT_SCHEMA,
        "contentFree": True,
        "liveExecution": False,
        "passed": result["passed"],
        "summary": {
            "total": 1,
            "passed": int(result["passed"]),
            "failed": int(not result["passed"]),
            "infrastructureFailures": int("infrastructure_invalid" in result["failure_codes"]),
        },
        "scenarios": [result],
    }


def scenario_commands(scenario: Scenario, username: str = BOT_USERNAME) -> tuple[str, ...]:
    if not re.fullmatch(r"[A-Za-z0-9_]{3,16}", username):
        raise MatrixSafetyError("matrix_username_invalid")
    commands = [
        "kill @e[type=!minecraft:player]",
        f"time set {scenario.time_of_day}",
        f"gamemode survival {username}",
        f"effect clear {username}",
        f"clear {username}",
        f"item replace entity {username} armor.head with air",
        f"item replace entity {username} armor.chest with air",
        f"item replace entity {username} armor.legs with air",
        f"item replace entity {username} armor.feet with air",
        f"item replace entity {username} weapon.offhand with air",
        *(
            ("fill -3 99 -3 4 103 4 minecraft:bedrock hollow",)
            if scenario.id == EMERGENCY_ZOMBIE_CELL_ID
            else ()
        ),
        f"tp {username} 0.5 100 0.5 0 0",
        f"attribute {username} minecraft:max_health base set 20",
        f"effect give {username} minecraft:instant_health 1 10 true",
        f"effect give {username} minecraft:saturation 1 10 true",
        f"item replace entity {username} weapon.mainhand with minecraft:iron_sword",
    ]
    if scenario.loadout == "protected":
        commands.extend([
            f"item replace entity {username} armor.head with minecraft:iron_helmet",
            f"item replace entity {username} armor.chest with minecraft:iron_chestplate",
            f"item replace entity {username} armor.legs with minecraft:iron_leggings",
            f"item replace entity {username} armor.feet with minecraft:iron_boots",
            f"item replace entity {username} weapon.offhand with minecraft:shield",
            f"give {username} minecraft:bow 1",
            f"give {username} minecraft:arrow 32",
        ])
    if scenario.id == EMERGENCY_ZOMBIE_CELL_ID:
        commands.append(f"effect clear {username} minecraft:instant_health")
        commands.append(f"damage {username} 10 minecraft:generic")
        return tuple(commands)
    offsets = ((6, 0), (-3, 5), (-3, -5))
    for index, mob in enumerate(scenario.mobs):
        x, z = offsets[index]
        invulnerable = ",Invulnerable:1b" if scenario.expected_disposition == "flee" else ""
        commands.append(
            f'summon minecraft:{mob} {x + 0.5} 100 {z + 0.5} '
            f'{{PersistenceRequired:1b,CanPickUpLoot:0b{invulnerable},Tags:["evelyn_matrix"]}}'
        )
    return tuple(commands)


def emergency_zombie_spawn_command() -> str:
    return (
        'summon minecraft:zombie 3.5 100 3.5 '
        '{PersistenceRequired:1b,CanPickUpLoot:0b,Tags:["evelyn_matrix"]}'
    )


def projectile_setup_commands(username: str = BOT_USERNAME) -> tuple[str, ...]:
    if not re.fullmatch(r"[A-Za-z0-9_]{3,16}", username):
        raise MatrixSafetyError("matrix_username_invalid")
    return (
        "kill @e[type=!minecraft:player]",
        "time set 6000",
        f"gamemode survival {username}",
        f"effect clear {username}",
        f"clear {username}",
        f"item replace entity {username} armor.head with air",
        f"item replace entity {username} armor.chest with air",
        f"item replace entity {username} armor.legs with air",
        f"item replace entity {username} armor.feet with air",
        f"item replace entity {username} weapon.mainhand with air",
        f"item replace entity {username} weapon.offhand with minecraft:shield",
        f"tp {username} 0.5 100 0.5 -90 0",
        f"effect give {username} minecraft:instant_health 1 10 true",
        f"effect give {username} minecraft:saturation 1 10 true",
        "scoreboard objectives add evshield minecraft.custom:minecraft.damage_blocked_by_shield",
        f"scoreboard players set {username} evshield 0",
    )


def projectile_launch_command() -> str:
    return (
        'summon minecraft:arrow 8.5 101.65 0.5 '
        '{Motion:[-1.2d,0.0d,0.0d],pickup:0b,Tags:["evelyn_matrix","evelyn_projectile_fixture"]}'
    )


def base_server_commands() -> tuple[str, ...]:
    return (
        "difficulty normal",
        *(f"gamerule {name} {str(value).lower()}" for name, value in BASE_GAMERULES),
        "weather clear",
        "time set 6000",
        "forceload add -32 -32 32 32",
        "fill -32 99 -32 32 99 32 minecraft:stone",
        "fill -32 100 -32 32 104 32 minecraft:air",
        "fill -32 105 -32 32 105 32 minecraft:stone",
        "fill -32 100 -32 -32 104 32 minecraft:barrier",
        "fill 32 100 -32 32 104 32 minecraft:barrier",
        "fill -32 100 -32 32 104 -32 minecraft:barrier",
        "fill -32 100 32 32 104 32 minecraft:barrier",
        "setworldspawn 0 100 0",
        "scoreboard objectives add evcm dummy",
    )


def _port_in_use(port: int) -> bool:
    for family, address in (
        (socket.AF_INET, ("127.0.0.1", port)),
        (socket.AF_INET6, ("::1", port)),
    ):
        try:
            with socket.socket(family, socket.SOCK_STREAM) as probe:
                probe.settimeout(0.2)
                if probe.connect_ex(address) == 0:
                    return True
        except OSError:
            continue
    return False


def _completed(
    command_runner: Callable[..., Any],
    command: Sequence[str],
    *,
    timeout: float = 15,
) -> Any:
    return command_runner(
        list(command), capture_output=True, text=True, timeout=timeout, check=False,
    )


def preflight_run(
    repo_root: Path,
    artifact_root: Path,
    server_jar: Path,
    java_executable: str,
    *,
    image: str = BOT_IMAGE,
    command_runner: Callable[..., Any] = subprocess.run,
    port_probe: Callable[[int], bool] = _port_in_use,
    artifact_exists: Callable[[Path], bool] = Path.exists,
) -> None:
    repo = repo_root.resolve()
    expected_artifact = (SCRIPT_REPO_ROOT / FIXTURE_RELATIVE).resolve()
    if repo != SCRIPT_REPO_ROOT or artifact_root.resolve() != expected_artifact:
        raise MatrixSafetyError("run_root_not_script_owned_workspace")
    if not (repo / "docker-compose.fast-control.yml").is_file():
        raise MatrixSafetyError("run_workspace_sentinel_missing")
    if artifact_exists(artifact_root):
        raise MatrixSafetyError("run_artifact_root_must_not_exist")
    profiles = (repo / "bot_profiles").resolve()
    if not profiles.is_dir() or profiles.is_symlink():
        raise MatrixSafetyError("bot_profiles_not_exact_workspace_directory")
    jar = server_jar.resolve()
    if not jar.is_file() or jar.is_symlink():
        raise MatrixSafetyError("validation_server_jar_missing_or_unsafe")
    java = Path(java_executable)
    if not java.is_file():
        raise MatrixSafetyError("java_executable_missing")
    if any(port_probe(port) for port in (PRODUCTION_PORT, GAME_PORT)):
        raise MatrixSafetyError("minecraft_validation_or_production_port_in_use")

    if _completed(command_runner, ("docker", "info")).returncode != 0:
        raise MatrixSafetyError("docker_unavailable")
    if _completed(command_runner, ("docker", "image", "inspect", image)).returncode != 0:
        raise MatrixSafetyError("combat_matrix_image_missing")
    production = _completed(
        command_runner,
        ("docker", "inspect", "--format", "{{.State.Running}}", PRODUCTION_CONTAINER),
    )
    if production.returncode == 0 and str(production.stdout).strip().lower() != "false":
        raise MatrixSafetyError("production_mindcraft_must_be_stopped")
    collision = _completed(command_runner, ("docker", "inspect", CONTAINER_NAME))
    if collision.returncode == 0:
        raise MatrixSafetyError("combat_matrix_container_name_already_exists")


class OwnedJavaServer:
    def __init__(self, process: Any):
        self.process = process
        self._lines: deque[tuple[int, str]] = deque(maxlen=4_000)
        self._sequence = 0
        self._lock = threading.Lock()
        self._reader = threading.Thread(target=self._read_output, daemon=True)
        self._reader.start()

    def _read_output(self) -> None:
        stream = self.process.stdout
        if stream is None:
            return
        for line in iter(stream.readline, ""):
            with self._lock:
                self._sequence += 1
                self._lines.append((self._sequence, line.rstrip()))

    def _cursor(self) -> int:
        with self._lock:
            return self._sequence

    def wait_for(self, pattern: re.Pattern[str], timeout: float, after: int = 0) -> re.Match[str]:
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            if self.process.poll() is not None:
                raise MatrixSafetyError("validation_server_exited")
            with self._lock:
                lines = tuple(self._lines)
            for sequence, line in lines:
                if sequence <= after:
                    continue
                match = pattern.search(line)
                if match:
                    return match
            time.sleep(0.05)
        raise MatrixSafetyError("validation_server_console_timeout")

    def command(self, command: str) -> None:
        if self.process.poll() is not None or self.process.stdin is None:
            raise MatrixSafetyError("validation_server_not_running")
        self.process.stdin.write(f"{command}\n")
        self.process.stdin.flush()

    def query_result(self, execute_tail: str) -> int:
        if not execute_tail or "\n" in execute_tail or "\r" in execute_tail:
            raise MatrixSafetyError("validation_server_query_invalid")
        holder = f"q{uuid4().hex[:8]}"
        cursor = self._cursor()
        self.command(f"scoreboard players set {holder} evcm 0")
        self.command(f"execute store result score {holder} evcm {execute_tail}")
        self.command(f"scoreboard players get {holder} evcm")
        match = self.wait_for(
            re.compile(rf"\b{re.escape(holder)}\b has (-?\d+) \[evcm\]"),
            3,
            after=cursor,
        )
        return int(match.group(1))

    def query_tagged_count(self) -> int:
        return self.query_result("if entity @e[tag=evelyn_matrix]")

    def stop(self) -> None:
        if self.process.poll() is not None:
            return
        try:
            self.command("stop")
            self.process.wait(timeout=12)
            return
        except (OSError, subprocess.TimeoutExpired):
            pass
        self.process.terminate()
        try:
            self.process.wait(timeout=5)
        except subprocess.TimeoutExpired:
            self.process.kill()
            self.process.wait(timeout=5)


def _prepare_server_directory(server_dir: Path) -> None:
    server_dir.mkdir(parents=True, exist_ok=False)
    (server_dir / "eula.txt").write_text("eula=true\n", encoding="utf-8")
    properties = "\n".join((
        "allow-flight=false",
        "difficulty=normal",
        "enable-query=false",
        "enable-rcon=false",
        "enforce-whitelist=false",
        "gamemode=survival",
        "level-name=world",
        "level-seed=5031406",
        "max-players=2",
        "motd=Evelyn isolated combat matrix",
        "online-mode=true",
        "pvp=true",
        f"server-port={GAME_PORT}",
        "simulation-distance=5",
        "spawn-protection=0",
        "view-distance=5",
        "white-list=false",
        "",
    ))
    (server_dir / "server.properties").write_text(properties, encoding="utf-8")


def start_server(
    server_dir: Path,
    server_jar: Path,
    java_executable: str,
    *,
    popen_factory: Callable[..., Any] = subprocess.Popen,
) -> OwnedJavaServer:
    creation_flags = getattr(subprocess, "CREATE_NO_WINDOW", 0)
    process = popen_factory(
        [java_executable, "-Xms1G", "-Xmx2G", "-jar", str(server_jar.resolve()), "nogui"],
        cwd=server_dir,
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
        encoding="utf-8",
        errors="replace",
        bufsize=1,
        creationflags=creation_flags,
    )
    (server_dir.parent / "server.pid").write_text(f"{process.pid}\n", encoding="ascii")
    owned = OwnedJavaServer(process)
    try:
        owned.wait_for(re.compile(r"Done \(.+\)! For help, type"), SERVER_START_TIMEOUT_SECONDS)
        owned.wait_for(re.compile(r"Starting minecraft server version 1\.21\.11"), 1)
        for command in base_server_commands():
            owned.command(command)
        return owned
    except BaseException:
        owned.stop()
        raise


def verify_base_server_setup(server: OwnedJavaServer) -> bool:
    checks = [
        server.query_tagged_count() == 0,
        server.query_result("run time query daytime") == 6_000,
        server.query_result("if block 0 99 0 minecraft:stone") >= 1,
        server.query_result("if block 0 105 0 minecraft:stone") >= 1,
    ]
    checks.extend(
        server.query_result(f"run gamerule {name}") == int(value)
        for name, value in BASE_GAMERULES
    )
    return all(checks)


def verify_scenario_setup(
    server: OwnedJavaServer,
    scenario: Scenario,
    username: str = BOT_USERNAME,
) -> bool:
    checks = [
        server.query_result("run time query daytime") == scenario.time_of_day,
        server.query_result(
            f"if items entity {username} weapon.mainhand minecraft:iron_sword"
        ) >= 1,
        server.query_tagged_count() == (
            0 if scenario.id == EMERGENCY_ZOMBIE_CELL_ID else scenario.hostile_count
        ),
    ]
    if scenario.id == EMERGENCY_ZOMBIE_CELL_ID:
        checks.extend((
            server.query_result(
                f"run attribute {username} minecraft:max_health base get 100"
            ) == 2_000,
            server.query_result(
                f"run data get entity {username} Health 100"
            ) == 1_000,
            server.query_result(
                f"positioned 0.5 100 0.5 if entity "
                f"@a[name={username},distance=..0.1,limit=1]"
            ) >= 1,
            server.query_result(
                "positioned 0.5 100 0.5 unless entity "
                "@e[type=!minecraft:player,distance=..8]"
            ) >= 1,
            server.query_result("if block -3 99 -3 minecraft:bedrock") >= 1,
            server.query_result("if block 4 103 4 minecraft:bedrock") >= 1,
            server.query_result("if block 0 99 0 minecraft:bedrock") >= 1,
            server.query_result("if block 0 103 0 minecraft:bedrock") >= 1,
            server.query_result("if block -3 101 0 minecraft:bedrock") >= 1,
            server.query_result("if block 4 101 0 minecraft:bedrock") >= 1,
            server.query_result("if block 0 101 -3 minecraft:bedrock") >= 1,
            server.query_result("if block 0 101 4 minecraft:bedrock") >= 1,
            server.query_result("if block -2 100 -2 minecraft:air") >= 1,
            server.query_result("if block 3 102 3 minecraft:air") >= 1,
            server.query_result("if block 0 100 0 minecraft:air") >= 1,
            server.query_result("if block 0 102 0 minecraft:air") >= 1,
        ))
    if scenario.loadout == "protected":
        for slot, item in (
            ("armor.head", "iron_helmet"),
            ("armor.chest", "iron_chestplate"),
            ("armor.legs", "iron_leggings"),
            ("armor.feet", "iron_boots"),
            ("weapon.offhand", "shield"),
        ):
            checks.append(server.query_result(
                f"if items entity {username} {slot} minecraft:{item}"
            ) >= 1)
        checks.extend(
            server.query_result(f"run clear {username} minecraft:{item} 0") >= 1
            for item in ("bow", "arrow")
        )
    else:
        for slot in ("armor.head", "armor.chest", "armor.legs", "armor.feet", "weapon.offhand"):
            checks.append(server.query_result(
                f"unless items entity {username} {slot} *"
            ) >= 1)
    return all(checks)


def verify_emergency_zombie_spawn(
    server: OwnedJavaServer,
    username: str = BOT_USERNAME,
) -> bool:
    return all((
        server.query_tagged_count() == 1,
        server.query_result(
            f"positioned 0.5 100 0.5 if entity "
            "@e[type=minecraft:zombie,tag=evelyn_matrix,distance=..8,limit=1]"
        ) >= 1,
        server.query_result(
            "as @e[type=minecraft:zombie,tag=evelyn_matrix,limit=1] "
            "at @s if block ~ ~ ~ minecraft:air if block ~ ~1 ~ minecraft:air"
        ) >= 1,
        server.query_result(f"run data get entity {username} Health 100") == 1_000,
    ))


def verify_emergency_cleanup(server: OwnedJavaServer) -> bool:
    return all((
        server.query_tagged_count() == 0,
        server.query_result("if block -3 99 -3 minecraft:stone") >= 1,
        server.query_result("if block 4 99 4 minecraft:stone") >= 1,
        server.query_result("if block 0 99 0 minecraft:stone") >= 1,
        server.query_result("if block -3 100 -3 minecraft:air") >= 1,
        server.query_result("if block 4 103 4 minecraft:air") >= 1,
        server.query_result("if block 0 103 0 minecraft:air") >= 1,
    ))


def verify_projectile_setup(
    server: OwnedJavaServer,
    username: str = BOT_USERNAME,
) -> bool:
    return all((
        server.query_tagged_count() == 0,
        server.query_result("run time query daytime") == 6_000,
        server.query_result(
            f"if items entity {username} weapon.offhand minecraft:shield"
        ) >= 1,
        server.query_result(f"if score {username} evshield matches 0") >= 1,
        server.query_result(f"run data get entity {username} Health 100") == 2_000,
    ))


def _bot_settings() -> str:
    return json.dumps({
        "minecraft_version": "1.21.11",
        "host": "host.docker.internal",
        "port": GAME_PORT,
        "auth": "microsoft",
        "mindserver_port": 8080,
        "auto_open_ui": False,
        "base_profile": "survival",
        "profiles": ["/app/mindcraft/profiles/evelyn.json"],
        "load_memory": False,
        "init_message": None,
        "only_chat_with": [],
        "speak": False,
        "chat_ingame": False,
        "language": "ko",
        "render_bot_view": False,
        "allow_insecure_coding": False,
        "allow_vision": False,
        "blocked_actions": [
            "!newAction", "!setMode", "!attackPlayer", "!digDown",
            "!checkBlueprint", "!checkBlueprintLevel", "!getBlueprint",
            "!getBlueprintLevel", "!searchWiki",
        ],
        "code_timeout_mins": 1,
        "max_messages": 4,
        "num_examples": 1,
        "narrate_behavior": False,
        "chat_bot_messages": False,
        "spawn_timeout": 30,
        "log_all_prompts": False,
    }, separators=(",", ":"))


def docker_run_command(
    repo_root: Path,
    bot_artifact_root: Path,
    run_id: str,
    *,
    image: str = BOT_IMAGE,
) -> tuple[str, ...]:
    profiles = (repo_root / "bot_profiles").resolve()
    runtime = bot_artifact_root.resolve()
    return (
        "docker", "run", "--detach",
        "--name", CONTAINER_NAME,
        "--label", f"{OWNER_LABEL}={OWNER_VALUE}",
        "--label", f"{RUN_LABEL}={run_id}",
        "--stop-timeout", "5",
        "--cap-drop", "ALL",
        "--security-opt", "no-new-privileges",
        "--add-host", "host.docker.internal:host-gateway",
        "--mount", f"type=bind,source={runtime},target=/app/runtime_artifacts",
        "--mount", f"type=bind,source={profiles},target=/run/evelyn-auth-seed,readonly",
        "--tmpfs", "/app/bot_profiles:rw,noexec,nosuid,nodev,mode=0700",
        "--workdir", "/app/mindcraft",
        "--env", f"SETTINGS_JSON={_bot_settings()}",
        "--env", f"MINEFLAYER_HOST=host.docker.internal",
        "--env", f"MINEFLAYER_PORT={GAME_PORT}",
        "--env", "MINEFLAYER_AUTH=microsoft",
        "--env", f"MINECRAFT_USERNAME={BOT_USERNAME}",
        "--env", "MINECRAFT_VERSION=1.21.11",
        "--env", "MINEFLAYER_PROFILES_FOLDER=/app/bot_profiles",
        "--env", "MINDCRAFT_STATUS_PATH=/app/runtime_artifacts/mindcraft/status.json",
        "--env", "MINDCRAFT_GOAL_MANAGER_STATE_PATH=/app/runtime_artifacts/mindcraft/goal_manager_state.json",
        "--env", "MINDCRAFT_COMBAT_HISTORY_PATH=/app/runtime_artifacts/mindcraft/combat_history.json",
        "--env", "MINDCRAFT_GOAL_MANAGER_MODE=gated",
        "--env", "MINDCRAFT_CODEX_ENABLED=false",
        "--env", "MINDCRAFT_DETERMINISTIC_TOOL_BOOTSTRAP=false",
        "--env", "MINDCRAFT_MODE_INTERVAL_MS=100",
        "--env", "MINDCRAFT_INTERRUPT_POLL_MS=100",
        "--env", "MINDCRAFT_INTERRUPT_STOP_WAIT_MS=1200",
        "--env", "MINDCRAFT_SELF_PROMPT_COOLDOWN_MS=300",
        "--env", "MINDCRAFT_ALLOWED_PLAYERS=",
        image,
        "sh", "-lc",
        "cp -R /run/evelyn-auth-seed/. /app/bot_profiles/ && "
        "chmod -R u+rwX /app/bot_profiles && exec node main.js",
    )


def _start_bot_container(
    repo_root: Path,
    bot_artifact_root: Path,
    run_id: str,
    *,
    image: str,
    command_runner: Callable[..., Any],
) -> str:
    result = _completed(
        command_runner,
        docker_run_command(repo_root, bot_artifact_root, run_id, image=image),
        timeout=30,
    )
    container_id = str(result.stdout or "").strip()
    if result.returncode != 0 or not re.fullmatch(r"[0-9a-f]{12,64}", container_id):
        raise MatrixSafetyError("combat_matrix_bot_start_failed")
    return container_id


def _container_running(container_id: str, command_runner: Callable[..., Any]) -> bool:
    result = _completed(
        command_runner,
        ("docker", "inspect", "--format", "{{.State.Running}}", container_id),
    )
    return result.returncode == 0 and str(result.stdout).strip().lower() == "true"


def _remove_owned_container(
    container_id: str,
    run_id: str,
    command_runner: Callable[..., Any],
) -> None:
    identity = _completed(command_runner, (
        "docker", "inspect", "--format",
        f'{{{{.Id}}}}|{{{{.Name}}}}|{{{{index .Config.Labels "{OWNER_LABEL}"}}}}|'
        f'{{{{index .Config.Labels "{RUN_LABEL}"}}}}',
        container_id,
    ))
    expected = f"{container_id}|/{CONTAINER_NAME}|{OWNER_VALUE}|{run_id}"
    if identity.returncode != 0 or str(identity.stdout).strip() != expected:
        raise MatrixSafetyError("combat_matrix_container_ownership_lost")
    removed = _completed(command_runner, ("docker", "rm", "--force", container_id), timeout=20)
    if removed.returncode != 0:
        raise MatrixSafetyError("combat_matrix_container_cleanup_failed")


def _read_json(path: Path) -> Mapping[str, Any] | None:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
        return value if isinstance(value, Mapping) else None
    except (OSError, json.JSONDecodeError):
        return None


def _combat_episodes(path: Path) -> list[Mapping[str, Any]]:
    payload = _read_json(path)
    episodes = payload.get("episodes") if payload and payload.get("schemaVersion") == 1 else None
    if not isinstance(episodes, list):
        return []
    return [episode for episode in episodes if isinstance(episode, Mapping)]


def _terminal_episodes(episodes: Sequence[Mapping[str, Any]]) -> list[Mapping[str, Any]]:
    return [
        episode for episode in episodes
        if episode.get("outcome") in {"success", "failure", "death"}
    ]


def _leading_reflex_episode_durations(
    episodes: Sequence[Mapping[str, Any]],
) -> list[float | None]:
    durations = []
    for episode in episodes:
        if episode.get("outcome") != "interrupted" or episode.get("tactic") != "disengage":
            break
        durations.append(_number(episode.get("duration_ms", episode.get("durationMs"))))
    return durations


def _first_tactical_episode(
    episodes: Sequence[Mapping[str, Any]],
    reflex_durations: Sequence[float | None],
) -> Mapping[str, Any] | None:
    if not reflex_durations:
        return None
    reflex_count = len(reflex_durations)
    return episodes[reflex_count] if reflex_count < len(episodes) else None


def _startup_diagnosis(
    container_id: str,
    command_runner: Callable[..., Any],
) -> dict[str, str | None]:
    child_state = "unknown"
    top_result = None
    top_output = ""
    try:
        top_result = _completed(
            command_runner,
            ("docker", "top", container_id, "-eo", "pid,args"),
            timeout=5,
        )
        if top_result.returncode == 0:
            top_output = str(top_result.stdout or "")
            child_state = "running" if "init_agent.js" in top_output else "not_running"
    except (OSError, subprocess.TimeoutExpired):
        pass
    finally:
        top_output = ""
        top_result = None

    log_category = "startup_logs_unavailable"
    logs_result = None
    raw_logs = ""
    try:
        logs_result = _completed(
            command_runner,
            ("docker", "logs", "--tail", "200", container_id),
            timeout=5,
        )
        if logs_result.returncode == 0:
            raw_logs = "\n".join((
                str(logs_result.stdout or ""),
                str(logs_result.stderr or ""),
            )).casefold()
            log_category = "startup_logs_unclassified"
            for category, patterns in STARTUP_LOG_PATTERNS:
                if any(pattern in raw_logs for pattern in patterns):
                    log_category = category
                    break
    except (OSError, subprocess.TimeoutExpired):
        pass
    finally:
        raw_logs = ""
        logs_result = None

    runtime_error = None
    if child_state == "not_running":
        runtime_error = "startup_child_not_running"
    elif log_category == "startup_protocol_failed":
        runtime_error = "startup_protocol_error"
    elif log_category == "startup_process_failed":
        runtime_error = "startup_process_error"
    return {
        "startup_child_state": child_state,
        "startup_log_category": log_category,
        "runtime_error": runtime_error,
    }


def _invalid_observation(
    infrastructure_code: str,
    startup_diagnosis: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    diagnosis = startup_diagnosis if isinstance(startup_diagnosis, Mapping) else {}
    return {
        "infrastructure_valid": False,
        "infrastructure_code": _fixed_code(infrastructure_code, INFRASTRUCTURE_CODES),
        "runtime_error": _fixed_code(diagnosis.get("runtime_error"), RUNTIME_ERROR_CODES),
        "startup_child_state": _fixed_code(
            diagnosis.get("startup_child_state"), STARTUP_CHILD_STATES,
        ),
        "startup_log_category": _fixed_code(
            diagnosis.get("startup_log_category"), STARTUP_LOG_CATEGORIES,
        ),
        "death_count": 0,
        "episode_count": 0,
        "episode": {},
        "reflex_reason": None,
        "reflex_to_action_ms": None,
        "reflex_durations_ms": None,
        "wake_to_decision_ms": None,
        "decision_to_action_ms": None,
        "remaining_hostiles": None,
        "verified_clear": False,
    }


def _wait_for_connected_status(
    status_path: Path,
    container_id: str,
    deadline: float,
    *,
    command_runner: Callable[..., Any],
    monotonic: Callable[[], float],
    sleeper: Callable[[float], None],
) -> Mapping[str, Any] | None:
    next_container_check = monotonic()
    first_ready_update = None
    while monotonic() < deadline:
        status = _read_json(status_path)
        if status and status.get("connected") is True and status.get("connection_state") == "connected":
            controller = status.get("survival_controller")
            updated = _number(
                controller.get("updated_at") if isinstance(controller, Mapping) else None
            )
            if isinstance(controller, Mapping) and updated is not None:
                if first_ready_update is None:
                    first_ready_update = updated
                elif updated > first_ready_update:
                    return status
        now = monotonic()
        if now >= next_container_check:
            if not _production_stopped(command_runner):
                raise MatrixSafetyError("production_mindcraft_started_during_cell")
            if not _container_running(container_id, command_runner):
                return None
            next_container_check = now + 1
        sleeper(0.1)
    return None


def _emergency_admission_status(
    status: Mapping[str, Any] | None,
    *,
    centered: bool,
) -> bool:
    if not isinstance(status, Mapping):
        return False
    inventory = status.get("inventory")
    hostiles = status.get("hostiles_nearby")
    controller = status.get("survival_controller")
    position = status.get("position")
    admitted = (
        status.get("connected") is True and
        status.get("connection_state") == "connected" and
        _number(status.get("health")) == 10 and
        isinstance(inventory, Mapping) and
        (_number(inventory.get("iron_sword")) or 0) >= 1 and
        isinstance(hostiles, list) and
        len(hostiles) == 0 and
        isinstance(controller, Mapping) and
        _number(controller.get("updated_at")) is not None and
        _number(status.get("updated_at")) is not None
    )
    if not admitted or not centered:
        return admitted
    if not isinstance(position, Mapping):
        return False
    x = _number(position.get("x"))
    y = _number(position.get("y"))
    z = _number(position.get("z"))
    return (
        x is not None and y is not None and z is not None and
        abs(x - 0.5) <= 0.1 and
        abs(y - 100.0) <= 0.1 and
        abs(z - 0.5) <= 0.1
    )


def _wait_for_emergency_admission(
    status_path: Path,
    container_id: str,
    deadline: float,
    *,
    centered: bool,
    minimum_stable_seconds: float,
    minimum_fresh_samples: int,
    after_updated_at: float | None,
    command_runner: Callable[..., Any],
    monotonic: Callable[[], float],
    sleeper: Callable[[float], None],
) -> Mapping[str, Any] | None:
    stable_since = None
    fresh_updates: list[float] = []
    next_container_check = monotonic()
    while monotonic() < deadline:
        status = _read_json(status_path)
        if _emergency_admission_status(status, centered=centered):
            now = monotonic()
            if stable_since is None:
                stable_since = now
            updated = _number(status.get("updated_at"))
            if (
                updated is not None and
                (after_updated_at is None or updated > after_updated_at) and
                (not fresh_updates or updated > fresh_updates[-1])
            ):
                fresh_updates.append(updated)
            if (
                now - stable_since >= minimum_stable_seconds and
                len(fresh_updates) >= minimum_fresh_samples
            ):
                return status
        else:
            stable_since = None
            fresh_updates.clear()
        now = monotonic()
        if now >= next_container_check:
            if not _production_stopped(command_runner):
                raise MatrixSafetyError("production_mindcraft_started_during_cell")
            if not _container_running(container_id, command_runner):
                return None
            next_container_check = now + 1
        sleeper(0.1)
    return None


def _scenario_latencies(
    controller: Mapping[str, Any] | None,
    baseline_updated_at: float | None,
    baseline_last_reflex_at: float | None,
) -> tuple[float | None, float | None, str | None, float | None]:
    if not isinstance(controller, Mapping):
        return None, None, None, None
    updated = _number(controller.get("updated_at"))
    tactical_fresh = (
        updated is not None and
        baseline_updated_at is not None and
        updated > baseline_updated_at
    )
    last_reflex_at = _number(controller.get("last_reflex_at"))
    reflex_fresh = (
        last_reflex_at is not None and
        (baseline_last_reflex_at is None or last_reflex_at > baseline_last_reflex_at)
    )
    reason = controller.get("reflex_reason") if reflex_fresh else None
    if reason not in {"hostile", "projectile"}:
        reason = None
    return (
        _number(controller.get("wake_to_decision_ms")) if tactical_fresh else None,
        _number(controller.get("decision_to_action_ms")) if tactical_fresh else None,
        reason,
        _number(controller.get("reflex_to_action_ms")) if reason else None,
    )


def _observe_terminal_episode(
    status_path: Path,
    history_path: Path,
    container_id: str,
    deadline: float,
    baseline_controller_updated_at: float | None,
    baseline_last_reflex_at: float | None,
    *,
    require_safe_stable: bool,
    command_runner: Callable[..., Any],
    monotonic: Callable[[], float],
    sleeper: Callable[[float], None],
) -> tuple[Mapping[str, Any] | None, list[Mapping[str, Any]], dict[str, Any]]:
    last_status: Mapping[str, Any] | None = None
    wake = None
    action = None
    reflex_reason = None
    reflex_action = None
    reflex_durations = None
    death_count = 0
    safe_since = None
    stable_ms = 0.0
    stable_minimum = None
    next_container_check = monotonic()
    while monotonic() < deadline:
        wake_latched = False
        status = _read_json(status_path)
        if status is not None:
            last_status = status
            controller = status.get("survival_controller")
            if isinstance(controller, Mapping):
                current_wake, current_action, current_reflex_reason, current_reflex_action = _scenario_latencies(
                    controller, baseline_controller_updated_at, baseline_last_reflex_at,
                )
                wake_latched = wake is None and current_wake is not None
                if wake_latched:
                    wake = current_wake
                if action is None and current_action is not None:
                    action = current_action
                if reflex_reason is None and current_reflex_reason == "hostile":
                    reflex_reason = current_reflex_reason
                    reflex_action = current_reflex_action
            goal = status.get("goal_manager")
            if isinstance(goal, Mapping):
                death_count = max(death_count, _integer(goal.get("death_count")) or 0)
            if status.get("phase") == "respawning" or status.get("last_death_event"):
                death_count = max(1, death_count)

            hostiles = status.get("hostiles_nearby")
            distances = []
            if isinstance(hostiles, list):
                distances = [
                    value for item in hostiles if isinstance(item, Mapping)
                    if (value := _number(item.get("distance"))) is not None
                ]
            nearest = min(distances) if distances else 24.1
            now = monotonic()
            if nearest >= SAFE_DISTANCE_METERS:
                if safe_since is None:
                    safe_since = now
                    stable_minimum = nearest
                else:
                    stable_minimum = min(float(stable_minimum), nearest)
                stable_ms = max(stable_ms, (now - safe_since) * 1000)
            else:
                safe_since = None
                stable_minimum = None

        episodes = _combat_episodes(history_path)
        if wake_latched:
            reflex_durations = _leading_reflex_episode_durations(episodes)
        terminal_episodes = _terminal_episodes(episodes)
        terminal_failed = any(
            episode.get("outcome") in {"failure", "death"}
            for episode in terminal_episodes
        )
        if terminal_episodes and (
            not require_safe_stable or
            terminal_failed or
            stable_ms >= SAFE_STABLE_MS
        ):
            return last_status, episodes, {
                "wake": wake,
                "action": action,
                "reflex_reason": reflex_reason,
                "reflex_action": reflex_action,
                "reflex_durations": reflex_durations,
                "death_count": death_count,
                "safe_stable_ms": stable_ms,
                "min_stable_distance_meters": stable_minimum,
            }
        now = monotonic()
        if now >= next_container_check:
            if not _production_stopped(command_runner):
                raise MatrixSafetyError("production_mindcraft_started_during_cell")
            if not _container_running(container_id, command_runner):
                break
            next_container_check = now + 1
        sleeper(0.1)
    return last_status, _combat_episodes(history_path), {
        "wake": wake,
        "action": action,
        "reflex_reason": reflex_reason,
        "reflex_action": reflex_action,
        "reflex_durations": reflex_durations,
        "death_count": death_count,
        "safe_stable_ms": stable_ms,
        "min_stable_distance_meters": stable_minimum,
    }


def _observe_projectile_reflex(
    status_path: Path,
    container_id: str,
    deadline: float,
    baseline_last_reflex_at: float | None,
    *,
    command_runner: Callable[..., Any],
    monotonic: Callable[[], float],
    sleeper: Callable[[float], None],
) -> tuple[Mapping[str, Any] | None, dict[str, Any]]:
    last_status: Mapping[str, Any] | None = None
    reflex_reason = None
    reflex_action = None
    reflex_seen_at = None
    death_count = 0
    hostile_count = 0
    runtime_error = False
    next_container_check = monotonic()
    while monotonic() < deadline:
        status = _read_json(status_path)
        if status is not None:
            last_status = status
            controller = status.get("survival_controller")
            _, _, current_reason, current_action = _scenario_latencies(
                controller if isinstance(controller, Mapping) else None,
                None,
                baseline_last_reflex_at,
            )
            if reflex_reason is None and current_reason is not None:
                reflex_reason = current_reason
                reflex_action = current_action
                reflex_seen_at = monotonic()
            hostiles = status.get("hostiles_nearby")
            if isinstance(hostiles, list):
                hostile_count = max(hostile_count, len(hostiles))
            goal = status.get("goal_manager")
            if isinstance(goal, Mapping):
                death_count = max(death_count, _integer(goal.get("death_count")) or 0)
            if status.get("phase") == "respawning" or status.get("last_death_event"):
                death_count = max(1, death_count)
            runtime_error = runtime_error or bool(status.get("last_error")) or bool(
                controller.get("last_error") if isinstance(controller, Mapping) else None
            )
        now = monotonic()
        if reflex_seen_at is not None and now - reflex_seen_at >= PROJECTILE_EFFECT_SETTLE_SECONDS:
            break
        if now >= next_container_check:
            if not _production_stopped(command_runner):
                raise MatrixSafetyError("production_mindcraft_started_during_cell")
            if not _container_running(container_id, command_runner):
                break
            next_container_check = now + 1
        sleeper(0.05)
    return last_status, {
        "reflex_reason": reflex_reason,
        "reflex_to_action_ms": reflex_action,
        "death_count": death_count,
        "hostile_count": hostile_count,
        "runtime_error": runtime_error,
    }


def run_scenario_cell(
    scenario: Scenario,
    repo_root: Path,
    artifact_root: Path,
    run_id: str,
    server: OwnedJavaServer,
    *,
    image: str = BOT_IMAGE,
    command_runner: Callable[..., Any] = subprocess.run,
    monotonic: Callable[[], float] = time.monotonic,
    sleeper: Callable[[float], None] = time.sleep,
) -> dict[str, Any]:
    cell_root = artifact_root / "cells" / scenario.id
    bot_root = cell_root / "bot"
    (bot_root / "mindcraft").mkdir(parents=True, exist_ok=False)
    status_path = bot_root / "mindcraft" / "status.json"
    history_path = bot_root / "mindcraft" / "combat_history.json"
    container_id = None
    observation = _invalid_observation("cell_not_started")
    try:
        container_id = _start_bot_container(
            repo_root, bot_root, run_id, image=image, command_runner=command_runner,
        )
        ready_deadline = monotonic() + BOT_READY_TIMEOUT_SECONDS
        ready_status = _wait_for_connected_status(
            status_path,
            container_id,
            ready_deadline,
            command_runner=command_runner,
            monotonic=monotonic,
            sleeper=sleeper,
        )
        if ready_status is None:
            return _invalid_observation(
                "bot_connection_unverified",
                _startup_diagnosis(container_id, command_runner),
            )

        for command in scenario_commands(scenario):
            server.command(command)
        baseline_status = ready_status
        if scenario.id == EMERGENCY_ZOMBIE_CELL_ID:
            settled = _wait_for_emergency_admission(
                status_path,
                container_id,
                monotonic() + EMERGENCY_ADMISSION_TIMEOUT_SECONDS,
                centered=False,
                minimum_stable_seconds=EMERGENCY_LOW_HEALTH_SETTLE_SECONDS,
                minimum_fresh_samples=2,
                after_updated_at=_number(ready_status.get("updated_at")),
                command_runner=command_runner,
                monotonic=monotonic,
                sleeper=sleeper,
            )
            if settled is None:
                return _invalid_observation("emergency_admission_unverified")
            server.command(f"tp {BOT_USERNAME} 0.5 100 0.5 0 0")
            if not verify_scenario_setup(server, scenario):
                return _invalid_observation("emergency_admission_unverified")
            baseline_status = _wait_for_emergency_admission(
                status_path,
                container_id,
                monotonic() + EMERGENCY_ADMISSION_TIMEOUT_SECONDS,
                centered=True,
                minimum_stable_seconds=0,
                minimum_fresh_samples=2,
                after_updated_at=_number(settled.get("updated_at")),
                command_runner=command_runner,
                monotonic=monotonic,
                sleeper=sleeper,
            )
            if baseline_status is None:
                return _invalid_observation("emergency_admission_unverified")
            server.command(emergency_zombie_spawn_command())
            if not verify_emergency_zombie_spawn(server):
                return _invalid_observation("emergency_spawn_unverified")
        elif not verify_scenario_setup(server, scenario):
            return _invalid_observation("scenario_setup_unverified")

        baseline_controller = baseline_status.get("survival_controller")
        baseline_controller_updated_at = _number(
            baseline_controller.get("updated_at")
            if isinstance(baseline_controller, Mapping)
            else None
        )
        baseline_last_reflex_at = _number(
            baseline_controller.get("last_reflex_at")
            if isinstance(baseline_controller, Mapping)
            else None
        )

        deadline = monotonic() + CELL_TIMEOUT_SECONDS
        status, episodes, sampled = _observe_terminal_episode(
            status_path,
            history_path,
            container_id,
            deadline,
            baseline_controller_updated_at,
            baseline_last_reflex_at,
            require_safe_stable=scenario.expected_disposition == "flee",
            command_runner=command_runner,
            monotonic=monotonic,
            sleeper=sleeper,
        )
        remaining = server.query_tagged_count()
        controller = status.get("survival_controller") if isinstance(status, Mapping) else None
        outer_error = status.get("last_error") if isinstance(status, Mapping) else None
        survival_error = controller.get("last_error") if isinstance(controller, Mapping) else None
        terminal_episodes = _terminal_episodes(episodes)
        episode = _first_tactical_episode(
            episodes,
            sampled["reflex_durations"] or (),
        ) or {}
        observation = {
            "infrastructure_valid": True,
            "infrastructure_code": None,
            "runtime_error": "mindcraft_runtime_error" if outer_error or survival_error else None,
            "death_count": sampled["death_count"],
            "episode_count": len(terminal_episodes),
            "episode": episode,
            "reflex_reason": sampled["reflex_reason"],
            "reflex_to_action_ms": sampled["reflex_action"],
            "reflex_durations_ms": sampled["reflex_durations"],
            "wake_to_decision_ms": sampled["wake"],
            "decision_to_action_ms": sampled["action"],
            "remaining_hostiles": remaining,
            "verified_clear": remaining == 0,
        }
        if scenario.expected_disposition == "flee":
            observation.update({
                "min_stable_distance_meters": sampled["min_stable_distance_meters"],
                "safe_stable_ms": sampled["safe_stable_ms"],
            })
        return observation
    except (MatrixSafetyError, OSError, ValueError):
        return _invalid_observation("cell_infrastructure_failed")
    finally:
        try:
            server.command("kill @e[tag=evelyn_matrix]")
            if scenario.id == EMERGENCY_ZOMBIE_CELL_ID:
                server.command("fill -3 99 -3 4 103 4 minecraft:air")
                server.command("fill -3 99 -3 4 99 4 minecraft:stone")
                if not verify_emergency_cleanup(server):
                    raise MatrixSafetyError("combat_matrix_arena_reset_unverified")
        finally:
            if container_id is not None:
                _remove_owned_container(container_id, run_id, command_runner)


def run_projectile_cell(
    repo_root: Path,
    artifact_root: Path,
    run_id: str,
    server: OwnedJavaServer,
    *,
    image: str = BOT_IMAGE,
    command_runner: Callable[..., Any] = subprocess.run,
    monotonic: Callable[[], float] = time.monotonic,
    sleeper: Callable[[float], None] = time.sleep,
) -> dict[str, Any]:
    bot_root = artifact_root / "cells" / PROJECTILE_CELL_ID / "bot"
    (bot_root / "mindcraft").mkdir(parents=True, exist_ok=False)
    status_path = bot_root / "mindcraft" / "status.json"
    container_id = None
    try:
        container_id = _start_bot_container(
            repo_root, bot_root, run_id, image=image, command_runner=command_runner,
        )
        ready_status = _wait_for_connected_status(
            status_path,
            container_id,
            monotonic() + BOT_READY_TIMEOUT_SECONDS,
            command_runner=command_runner,
            monotonic=monotonic,
            sleeper=sleeper,
        )
        if ready_status is None:
            return _invalid_observation(
                "bot_connection_unverified",
                _startup_diagnosis(container_id, command_runner),
            )
        for command in projectile_setup_commands():
            server.command(command)
        if not verify_projectile_setup(server):
            return _invalid_observation("projectile_setup_unverified")
        ready_controller = ready_status.get("survival_controller")
        baseline_last_reflex_at = _number(
            ready_controller.get("last_reflex_at") if isinstance(ready_controller, Mapping) else None
        )
        health_before = server.query_result(
            f"run data get entity {BOT_USERNAME} Health 100"
        )
        server.command(projectile_launch_command())
        _, sampled = _observe_projectile_reflex(
            status_path,
            container_id,
            monotonic() + PROJECTILE_TIMEOUT_SECONDS,
            baseline_last_reflex_at,
            command_runner=command_runner,
            monotonic=monotonic,
            sleeper=sleeper,
        )
        health_after = server.query_result(
            f"run data get entity {BOT_USERNAME} Health 100"
        )
        blocked_damage = server.query_result(
            f"run scoreboard players get {BOT_USERNAME} evshield"
        )
        return {
            "infrastructure_valid": True,
            "infrastructure_code": None,
            "runtime_error": "mindcraft_runtime_error" if sampled["runtime_error"] else None,
            "death_count": max(sampled["death_count"], int(health_after <= 0)),
            "reflex_reason": sampled["reflex_reason"],
            "reflex_to_action_ms": sampled["reflex_to_action_ms"],
            "response": "shield" if blocked_damage > 0 else None,
            "shield_blocked_damage": blocked_damage,
            "damage": max(0.0, (health_before - health_after) / 100),
            "hostile_count": sampled["hostile_count"],
        }
    except (MatrixSafetyError, OSError, ValueError):
        return _invalid_observation("projectile_cell_infrastructure_failed")
    finally:
        try:
            server.command("kill @e[tag=evelyn_matrix]")
        finally:
            if container_id is not None:
                _remove_owned_container(container_id, run_id, command_runner)


def _production_stopped(command_runner: Callable[..., Any]) -> bool:
    result = _completed(
        command_runner,
        ("docker", "inspect", "--format", "{{.State.Running}}", PRODUCTION_CONTAINER),
    )
    return result.returncode != 0 or str(result.stdout).strip().lower() == "false"


def _matrix_container_absent(command_runner: Callable[..., Any]) -> bool:
    if _completed(command_runner, ("docker", "info")).returncode != 0:
        return False
    return _completed(command_runner, ("docker", "inspect", CONTAINER_NAME)).returncode != 0


def _write_report(path: Path, report: Mapping[str, Any]) -> None:
    temporary = path.with_suffix(".tmp")
    temporary.write_text(
        json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True),
        encoding="utf-8",
    )
    temporary.replace(path)


def run_batch(
    repo_root: Path,
    artifact_root: Path,
    server_jar: Path,
    java_executable: str,
    *,
    smoke: bool = False,
    projectile_smoke: bool = False,
    emergency_zombie_smoke: bool = False,
    image: str = BOT_IMAGE,
    command_runner: Callable[..., Any] = subprocess.run,
    port_probe: Callable[[int], bool] = _port_in_use,
    preflight: Callable[..., None] = preflight_run,
    server_factory: Callable[..., OwnedJavaServer] = start_server,
    cell_runner: Callable[..., dict[str, Any]] = run_scenario_cell,
    projectile_cell_runner: Callable[..., dict[str, Any]] = run_projectile_cell,
) -> dict[str, Any]:
    if sum(map(bool, (smoke, projectile_smoke, emergency_zombie_smoke))) > 1:
        raise MatrixSafetyError("smoke_modes_conflict")
    preflight(
        repo_root,
        artifact_root,
        server_jar,
        java_executable,
        image=image,
        command_runner=command_runner,
        port_probe=port_probe,
    )
    selected = (
        ()
        if projectile_smoke
        else (
            (emergency_zombie_scenario(),)
            if emergency_zombie_smoke
            else (smoke_scenarios() if smoke else build_scenarios())
        )
    )
    run_id = uuid4().hex
    artifact_root.mkdir(parents=True, exist_ok=False)
    (artifact_root / "owner.json").write_text(json.dumps({
        "schema": MATRIX_SCHEMA,
        "owner": OWNER_VALUE,
        "runId": run_id,
        "contentFree": True,
    }, separators=(",", ":")), encoding="utf-8")
    _prepare_server_directory(artifact_root / "server")
    observations: dict[str, Mapping[str, Any]] = {}
    projectile_observation: Mapping[str, Any] | None = None
    server = None
    batch_error = None
    server_cleanup_ok = True
    try:
        server = server_factory(
            artifact_root / "server", server_jar, java_executable,
        )
        if not verify_base_server_setup(server):
            raise MatrixSafetyError("combat_matrix_arena_reset_unverified")
        if not _production_stopped(command_runner) or port_probe(PRODUCTION_PORT):
            batch_error = "production_mindcraft_started_during_batch"
        elif projectile_smoke:
            projectile_observation = projectile_cell_runner(
                repo_root,
                artifact_root,
                run_id,
                server,
                image=image,
                command_runner=command_runner,
            )
        else:
            for scenario in selected:
                if not _production_stopped(command_runner) or port_probe(PRODUCTION_PORT):
                    batch_error = "production_mindcraft_started_during_batch"
                    break
                observations[scenario.id] = cell_runner(
                    scenario,
                    repo_root,
                    artifact_root,
                    run_id,
                    server,
                    image=image,
                    command_runner=command_runner,
                )
                if not _production_stopped(command_runner) or port_probe(PRODUCTION_PORT):
                    batch_error = "production_mindcraft_started_during_batch"
                    break
    except (MatrixSafetyError, OSError, ValueError):
        batch_error = "batch_infrastructure_failed"
    finally:
        if server is not None:
            try:
                server.stop()
            except (MatrixSafetyError, OSError, subprocess.TimeoutExpired):
                batch_error = "server_cleanup_failed"
                server_cleanup_ok = False

    if batch_error and projectile_smoke:
        projectile_observation = _invalid_observation(batch_error)
    elif batch_error:
        for scenario in selected:
            observations.setdefault(scenario.id, _invalid_observation(batch_error))
    if projectile_smoke:
        report = build_projectile_report(
            projectile_observation or _invalid_observation("projectile_observation_missing")
        )
        report["mode"] = "projectile_smoke"
    else:
        report = build_report(observations, selected)
        report["mode"] = (
            "emergency_zombie_smoke"
            if emergency_zombie_smoke
            else ("smoke" if smoke else "full")
        )
    report["liveExecution"] = True
    cleanup_ok = (
        server_cleanup_ok and
        not port_probe(GAME_PORT) and
        not port_probe(PRODUCTION_PORT) and
        _production_stopped(command_runner) and
        _matrix_container_absent(command_runner)
    )
    if not cleanup_ok:
        report["passed"] = False
        report["cleanupVerified"] = False
    else:
        report["cleanupVerified"] = True
    _write_report(artifact_root / "report.json", report)
    return report


def dry_run_manifest(repo_root: Path, artifact_root: Path | None, game_port: int) -> dict[str, Any]:
    scenarios = build_scenarios()
    validate_manifest(scenarios)
    return {
        "schema": MATRIX_SCHEMA,
        "contentFree": True,
        "liveExecution": False,
        "mode": "dry_run",
        "scenario_count": len(scenarios),
        "isolation": {
            "natural_spawns": False,
            "natural_regeneration": False,
            "time_frozen_per_scenario": True,
            "fresh_runtime_and_history_per_scenario": True,
            "production_container_must_remain_stopped": PRODUCTION_CONTAINER,
            "protected_loadout": ["iron_sword", "iron_armor", "shield", "bow", "arrows"],
            "unprotected_loadout": ["iron_sword"],
        },
        "scenarios": [item.to_dict() for item in scenarios],
        "cleanup": cleanup_plan(repo_root, artifact_root=artifact_root, game_port=game_port),
    }


def _load_observations(path: Path) -> Mapping[str, Mapping[str, Any]]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(payload, Mapping):
        raise ValueError("observations_must_be_object")
    scenarios = payload.get("scenarios", payload)
    if not isinstance(scenarios, Mapping):
        raise ValueError("observations_scenarios_must_be_object")
    return scenarios


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Run, dry-run, or score Evelyn's isolated combat matrix.")
    parser.add_argument("--repo-root", type=Path, default=Path(__file__).resolve().parents[2])
    parser.add_argument("--artifact-root", type=Path)
    parser.add_argument("--game-port", type=int, default=GAME_PORT)
    parser.add_argument("--run", action="store_true", help="Run the isolated batch. Requires --server-jar.")
    parser.add_argument("--smoke", action="store_true", help="With --run, execute the canonical two-cell smoke.")
    parser.add_argument(
        "--projectile-smoke",
        action="store_true",
        help="With --run, execute one incoming-arrow shield fixture without hostile mobs.",
    )
    parser.add_argument(
        "--emergency-zombie-smoke",
        action="store_true",
        help=(
            "With --run, execute one dry, low-health, close-zombie emergency melee fixture."
        ),
    )
    parser.add_argument("--server-jar", type=Path, help="Existing vanilla Minecraft 1.21.11 server jar.")
    parser.add_argument("--java", type=Path, help="Java executable; defaults to PATH.")
    parser.add_argument("--list", action="store_true", help="List the validated 20-cell manifest.")
    parser.add_argument("--cleanup-plan", action="store_true", help="Print exact non-destructive cleanup targets.")
    parser.add_argument("--evaluate", type=Path, help="Score content-free observations without starting services.")
    args = parser.parse_args(argv)

    try:
        if (args.smoke or args.projectile_smoke or args.emergency_zombie_smoke) and not args.run:
            raise MatrixSafetyError("smoke_requires_run")
        if sum(map(bool, (
            args.smoke,
            args.projectile_smoke,
            args.emergency_zombie_smoke,
        ))) > 1:
            raise MatrixSafetyError("smoke_modes_conflict")
        if args.run:
            if args.evaluate or args.cleanup_plan or args.list:
                raise MatrixSafetyError("run_mode_conflicts_with_read_only_mode")
            if args.game_port != GAME_PORT:
                raise MatrixSafetyError("game_port_not_exact_isolated_port")
            if args.server_jar is None:
                raise MatrixSafetyError("run_requires_server_jar")
            java = str(args.java.resolve()) if args.java else shutil.which("java")
            if not java:
                raise MatrixSafetyError("java_executable_missing")
            artifact_root = args.artifact_root or (SCRIPT_REPO_ROOT / FIXTURE_RELATIVE)
            payload = run_batch(
                args.repo_root,
                artifact_root,
                args.server_jar,
                java,
                smoke=args.smoke,
                projectile_smoke=args.projectile_smoke,
                emergency_zombie_smoke=args.emergency_zombie_smoke,
            )
        elif args.evaluate:
            payload = build_report(_load_observations(args.evaluate))
            payload["mode"] = "offline_evaluation"
        elif args.cleanup_plan:
            payload = {
                "schema": MATRIX_SCHEMA,
                "contentFree": True,
                "liveExecution": False,
                "mode": "cleanup_plan_only",
                "cleanup": cleanup_plan(
                    args.repo_root,
                    artifact_root=args.artifact_root,
                    game_port=args.game_port,
                ),
            }
        else:
            payload = dry_run_manifest(args.repo_root, args.artifact_root, args.game_port)
            if args.list:
                payload = {
                    "schema": MATRIX_SCHEMA,
                    "contentFree": True,
                    "liveExecution": False,
                    "mode": "list",
                    "scenario_count": payload["scenario_count"],
                    "scenarios": payload["scenarios"],
                }
    except (OSError, subprocess.TimeoutExpired):
        error_code = "observation_read_failed"
    except (MatrixSafetyError, ValueError, json.JSONDecodeError) as error:
        error_code = str(error)
    else:
        print(json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True))
        return 0 if payload.get("passed", True) else 1

    print(json.dumps({
        "schema": MATRIX_SCHEMA,
        "contentFree": True,
        "liveExecution": False,
        "ok": False,
        "errorCode": error_code,
    }, separators=(",", ":")))
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
