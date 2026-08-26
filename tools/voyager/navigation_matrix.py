from __future__ import annotations

import argparse
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass
import json
from pathlib import Path
import re
import shutil
import subprocess
import time
from typing import Any, Callable, Mapping, Sequence
from uuid import uuid4

if __package__:
    from .combat_matrix import (
        BOT_IMAGE,
        OWNER_LABEL,
        PRODUCTION_CONTAINER,
        PRODUCTION_PORT,
        RUN_LABEL,
        MatrixSafetyError,
        OwnedJavaServer,
        _bot_settings as _matrix_bot_settings,
        _completed,
        _container_running,
        _port_in_use,
        _production_stopped,
        _read_json,
    )
    from .long_survival_soak import LOG_PROGRESS_OBJECTIVES, WORLD_PROGRESS_OBJECTIVES
else:
    from combat_matrix import (  # type: ignore[no-redef]
        BOT_IMAGE,
        OWNER_LABEL,
        PRODUCTION_CONTAINER,
        PRODUCTION_PORT,
        RUN_LABEL,
        MatrixSafetyError,
        OwnedJavaServer,
        _bot_settings as _matrix_bot_settings,
        _completed,
        _container_running,
        _port_in_use,
        _production_stopped,
        _read_json,
    )
    from long_survival_soak import (  # type: ignore[no-redef]
        LOG_PROGRESS_OBJECTIVES,
        WORLD_PROGRESS_OBJECTIVES,
    )


MATRIX_SCHEMA = "evelyn.validation.navigation-matrix.v1"
REPORT_SCHEMA = "evelyn.validation.navigation-matrix-report.v1"
FIXTURE_RELATIVE = Path("runtime_artifacts/validation/navigation_matrix")
OWNER_VALUE = "navigation_matrix"
CELL_LABEL = "evelyn.validation.cell"
DEFAULT_WORKERS = 2
MAX_WORKERS = 4
CELL_TIMEOUT_SECONDS = 120
BOT_READY_TIMEOUT_SECONDS = 45
SERVER_START_TIMEOUT_SECONDS = 90
SCRIPT_REPO_ROOT = Path(__file__).resolve().parents[2]


@dataclass(frozen=True)
class NavigationCell:
    id: str
    port: int
    username: str
    container_name: str


CELLS = (
    NavigationCell("direct_flat", 25575, "EvelynNav01", "evelyn-navigation-direct-flat"),
    NavigationCell("detour_wall", 25576, "EvelynNav02", "evelyn-navigation-detour-wall"),
    NavigationCell("stair_up", 25577, "EvelynNav03", "evelyn-navigation-stair-up"),
    NavigationCell(
        "blocked_batch_fallback",
        25578,
        "EvelynNav04",
        "evelyn-navigation-blocked-batch-fallback",
    ),
)

GAMERULES = (
    ("spawn_mobs", False),
    ("natural_health_regeneration", True),
    ("advance_time", False),
    ("advance_weather", False),
    ("mob_drops", False),
    ("mob_griefing", False),
    ("keep_inventory", True),
)


def _number(value: Any) -> float | None:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    result = float(value)
    return result if result == result and abs(result) != float("inf") else None


def validate_cells(cells: Sequence[NavigationCell] = CELLS) -> None:
    expected = (
        ("direct_flat", 25575),
        ("detour_wall", 25576),
        ("stair_up", 25577),
        ("blocked_batch_fallback", 25578),
    )
    if tuple((cell.id, cell.port) for cell in cells) != expected:
        raise MatrixSafetyError("navigation_cells_not_exact_fixed_manifest")
    for field in ("id", "port", "username", "container_name"):
        values = [getattr(cell, field) for cell in cells]
        if len(values) != len(set(values)):
            raise MatrixSafetyError(f"navigation_cell_{field}_not_unique")
    if any(
        not re.fullmatch(r"[A-Za-z0-9_]{1,16}", cell.username)
        or cell.port == PRODUCTION_PORT
        for cell in cells
    ):
        raise MatrixSafetyError("navigation_cell_identity_invalid")


def cleanup_plan(
    repo_root: Path,
    *,
    artifact_root: Path | None = None,
) -> dict[str, Any]:
    validate_cells()
    repo = repo_root.resolve()
    expected = (repo / FIXTURE_RELATIVE).resolve()
    requested = (artifact_root or expected).resolve()
    validation = (repo / "runtime_artifacts/validation").resolve()
    if requested != expected:
        raise MatrixSafetyError("artifact_root_not_exact_navigation_matrix_fixture")
    if expected.parent != validation or expected == repo:
        raise MatrixSafetyError("artifact_root_outside_exact_validation_parent")
    try:
        validation.relative_to(repo)
    except ValueError as error:
        raise MatrixSafetyError("validation_parent_resolves_outside_repo") from error
    if any(path.is_symlink() for path in (repo, validation, expected) if path.exists()):
        raise MatrixSafetyError("cleanup_path_symlink_rejected")
    if any(cell.container_name == PRODUCTION_CONTAINER for cell in CELLS):
        raise MatrixSafetyError("production_container_target_rejected")
    relative = FIXTURE_RELATIVE.as_posix()
    return {
        "container_names": [cell.container_name for cell in CELLS],
        "server_pid_files": [f"{relative}/cells/{cell.id}/server.pid" for cell in CELLS],
        "artifact_roots": [relative],
        "ports": [cell.port for cell in CELLS],
        "wildcards": False,
    }


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
    validate_cells()
    repo = repo_root.resolve()
    expected = (SCRIPT_REPO_ROOT / FIXTURE_RELATIVE).resolve()
    if repo != SCRIPT_REPO_ROOT or artifact_root.resolve() != expected:
        raise MatrixSafetyError("run_root_not_script_owned_workspace")
    cleanup_plan(repo, artifact_root=artifact_root)
    if not (repo / "docker-compose.fast-control.yml").is_file():
        raise MatrixSafetyError("run_workspace_sentinel_missing")
    if artifact_exists(artifact_root):
        raise MatrixSafetyError("run_artifact_root_must_not_exist")
    jar = server_jar.resolve()
    if not jar.is_file() or jar.is_symlink():
        raise MatrixSafetyError("validation_server_jar_missing_or_unsafe")
    java = Path(java_executable)
    if not java.is_file() or java.is_symlink():
        raise MatrixSafetyError("java_executable_missing_or_unsafe")
    if any(port_probe(port) for port in (PRODUCTION_PORT, *(cell.port for cell in CELLS))):
        raise MatrixSafetyError("minecraft_validation_or_production_port_in_use")
    if _completed(command_runner, ("docker", "info")).returncode != 0:
        raise MatrixSafetyError("docker_unavailable")
    if _completed(command_runner, ("docker", "image", "inspect", image)).returncode != 0:
        raise MatrixSafetyError("navigation_matrix_image_missing")
    if not _production_stopped(command_runner):
        raise MatrixSafetyError("production_mindcraft_must_be_stopped")
    for cell in CELLS:
        if _completed(command_runner, ("docker", "inspect", cell.container_name)).returncode == 0:
            raise MatrixSafetyError("navigation_matrix_container_name_already_exists")


def _fixture_commands(cell: NavigationCell) -> tuple[str, ...]:
    common = (
        "difficulty peaceful",
        *(f"gamerule {name} {str(value).lower()}" for name, value in GAMERULES),
        "weather clear",
        "time set 6000",
        "gamerule respawn_radius 0",
        "forceload add -32 -32 32 32",
        "fill -32 100 -32 32 107 -1 minecraft:air",
        "fill -32 100 0 32 107 32 minecraft:air",
        "fill -32 99 -32 32 99 32 minecraft:stone",
        "fill -32 100 -32 -32 107 32 minecraft:barrier",
        "fill 32 100 -32 32 107 32 minecraft:barrier",
        "fill -32 100 -32 32 107 -32 minecraft:barrier",
        "fill -32 100 32 32 107 32 minecraft:barrier",
        "setworldspawn 0 100 0",
    )
    fixtures = {
        "direct_flat": (
            "fill 8 100 0 8 102 0 minecraft:oak_log",
        ),
        "detour_wall": (
            "fill 5 100 -5 5 104 5 minecraft:bedrock",
            "fill 12 100 0 12 102 0 minecraft:oak_log",
        ),
        "stair_up": (
            "fill 3 100 -1 3 100 1 minecraft:stone",
            "fill 4 100 -1 4 101 1 minecraft:stone",
            "fill 5 100 -1 5 102 1 minecraft:stone",
            "fill 6 100 -1 6 103 1 minecraft:stone",
            "fill 7 100 -4 15 103 4 minecraft:stone",
            "fill 12 104 0 12 106 0 minecraft:oak_log",
        ),
        "blocked_batch_fallback": (
            "setblock 4 110 0 minecraft:oak_log",
            "setblock -4 110 0 minecraft:oak_log",
            "setblock 0 110 4 minecraft:oak_log",
            "setblock 0 110 -4 minecraft:oak_log",
            "fill 20 100 0 20 102 0 minecraft:oak_log",
        ),
    }
    return common + fixtures[cell.id]


def _prepare_server_directory(server_dir: Path, cell: NavigationCell) -> None:
    if cell not in CELLS:
        raise MatrixSafetyError("navigation_cell_not_fixed_manifest_member")
    server_dir.mkdir(parents=True, exist_ok=False)
    (server_dir / "eula.txt").write_text("eula=true\n", encoding="utf-8")
    properties = "\n".join((
        "allow-flight=false",
        "difficulty=peaceful",
        "enable-query=false",
        "enable-rcon=false",
        "enforce-secure-profile=false",
        "force-gamemode=true",
        "gamemode=survival",
        "generate-structures=false",
        "hardcore=false",
        "level-name=world",
        "level-seed=5031408",
        "level-type=minecraft:flat",
        "max-players=1",
        f"motd=Evelyn navigation matrix {cell.id}",
        "online-mode=false",
        "pause-when-empty-seconds=-1",
        "pvp=false",
        f"server-port={cell.port}",
        "simulation-distance=5",
        "spawn-animals=false",
        "spawn-monsters=false",
        "spawn-npcs=false",
        "spawn-protection=0",
        "view-distance=5",
        "white-list=false",
        "",
    ))
    (server_dir / "server.properties").write_text(properties, encoding="utf-8")


def _add_objective(server: OwnedJavaServer, name: str, criterion: str) -> None:
    cursor = server._cursor()
    server.command(f"scoreboard objectives add {name} {criterion}")
    server.wait_for(re.compile(rf"Created new objective \[{re.escape(name)}\]$"), 3, after=cursor)


def start_server(
    cell: NavigationCell,
    server_dir: Path,
    server_jar: Path,
    java_executable: str,
    *,
    popen_factory: Callable[..., Any] = subprocess.Popen,
) -> OwnedJavaServer:
    creation_flags = getattr(subprocess, "CREATE_NO_WINDOW", 0)
    process = popen_factory(
        [java_executable, "-Xms512M", "-Xmx1G", "-jar", str(server_jar.resolve()), "nogui"],
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
    server = OwnedJavaServer(process)
    try:
        server.wait_for(re.compile(r"Done \(.+\)! For help, type"), SERVER_START_TIMEOUT_SECONDS)
        server.wait_for(re.compile(r"Starting minecraft server version 1\.21\.11"), 1)
        _add_objective(server, "evcm", "dummy")
        for _key, objective, criterion in WORLD_PROGRESS_OBJECTIVES:
            if objective != "evdirt":
                _add_objective(server, objective, criterion)
        for objective, criterion in LOG_PROGRESS_OBJECTIVES:
            _add_objective(server, objective, criterion)
        for command in _fixture_commands(cell):
            server.command(command)
        return server
    except BaseException:
        server.stop()
        raise


def verify_cell_setup(server: OwnedJavaServer, cell: NavigationCell) -> bool:
    checks = [
        server.query_result("run time query daytime") == 6_000,
        server.query_result("run gamerule spawn_mobs") == 0,
        server.query_result("run gamerule advance_time") == 0,
        server.query_result("if block 0 99 0 minecraft:stone") >= 1,
        server.query_result("if block 0 100 0 minecraft:air") >= 1,
        server.query_result("unless entity @a") >= 1,
    ]
    fixture_checks = {
        "direct_flat": ("if block 8 100 0 minecraft:oak_log",),
        "detour_wall": (
            "if block 5 102 0 minecraft:bedrock",
            "if block 12 100 0 minecraft:oak_log",
        ),
        "stair_up": (
            "if block 3 100 0 minecraft:stone",
            "if block 6 103 0 minecraft:stone",
            "if block 12 104 0 minecraft:oak_log",
        ),
        "blocked_batch_fallback": (
            "if block 4 110 0 minecraft:oak_log",
            "if block -4 110 0 minecraft:oak_log",
            "if block 0 110 4 minecraft:oak_log",
            "if block 0 110 -4 minecraft:oak_log",
            "if block 20 100 0 minecraft:oak_log",
        ),
    }
    checks.extend(server.query_result(tail) >= 1 for tail in fixture_checks[cell.id])
    return all(checks)


def _bot_settings(cell: NavigationCell) -> str:
    settings = json.loads(_matrix_bot_settings())
    settings.update({
        "host": "host.docker.internal",
        "port": cell.port,
        "auth": "offline",
        "profiles": ["/app/runtime_artifacts/profile.json"],
    })
    return json.dumps(settings, separators=(",", ":"))


def _write_cell_profile(bot_root: Path, cell: NavigationCell) -> None:
    source = SCRIPT_REPO_ROOT / "external/mindcraft_evelyn/profiles/evelyn.json"
    profile = json.loads(source.read_text(encoding="utf-8"))
    if not isinstance(profile, dict):
        raise MatrixSafetyError("navigation_profile_template_invalid")
    profile["name"] = cell.username
    if isinstance(profile.get("conversing"), str):
        profile["conversing"] = profile["conversing"].replace("Evelyn_0428", cell.username)
    (bot_root / "profile.json").write_text(
        json.dumps(profile, ensure_ascii=False, separators=(",", ":")),
        encoding="utf-8",
    )


def docker_run_command(
    repo_root: Path,
    cell_root: Path,
    cell: NavigationCell,
    run_id: str,
    *,
    image: str = BOT_IMAGE,
) -> tuple[str, ...]:
    expected_cell_root = (SCRIPT_REPO_ROOT / FIXTURE_RELATIVE / "cells" / cell.id).resolve()
    if (
        repo_root.resolve() != SCRIPT_REPO_ROOT
        or cell not in CELLS
        or cell_root.resolve() != expected_cell_root
    ):
        raise MatrixSafetyError("navigation_container_scope_invalid")
    runtime = (cell_root / "bot").resolve()
    return (
        "docker", "run", "--detach",
        "--name", cell.container_name,
        "--label", f"{OWNER_LABEL}={OWNER_VALUE}",
        "--label", f"{RUN_LABEL}={run_id}",
        "--label", f"{CELL_LABEL}={cell.id}",
        "--stop-timeout", "5",
        "--cap-drop", "ALL",
        "--security-opt", "no-new-privileges",
        "--add-host", "host.docker.internal:host-gateway",
        "--mount", f"type=bind,source={runtime},target=/app/runtime_artifacts",
        "--workdir", "/app/mindcraft",
        "--env", f"SETTINGS_JSON={_bot_settings(cell)}",
        "--env", "MINEFLAYER_HOST=host.docker.internal",
        "--env", f"MINEFLAYER_PORT={cell.port}",
        "--env", "MINEFLAYER_AUTH=offline",
        "--env", f"MINECRAFT_USERNAME={cell.username}",
        "--env", "MINECRAFT_VERSION=1.21.11",
        "--env", "MINDCRAFT_STATUS_PATH=/app/runtime_artifacts/mindcraft/status.json",
        "--env", "MINDCRAFT_GOAL_MANAGER_STATE_PATH=/app/runtime_artifacts/mindcraft/goal_manager_state.json",
        "--env", "MINDCRAFT_COMBAT_HISTORY_PATH=/app/runtime_artifacts/mindcraft/combat_history.json",
        "--env", "MINDCRAFT_GOAL_MANAGER_MODE=off",
        "--env", "MINDCRAFT_GOAL=",
        "--env", "MINDCRAFT_CODEX_ENABLED=false",
        "--env", "MINDCRAFT_DETERMINISTIC_TOOL_BOOTSTRAP=true",
        "--env", "MINDCRAFT_MODE_INTERVAL_MS=100",
        "--env", "MINDCRAFT_INTERRUPT_POLL_MS=100",
        "--env", "MINDCRAFT_INTERRUPT_STOP_WAIT_MS=1200",
        "--env", "MINDCRAFT_SELF_PROMPT_COOLDOWN_MS=300",
        "--env", "MINDCRAFT_ALLOWED_PLAYERS=",
        image,
        "sh", "-lc", "exec node main.js",
    )


def _remove_owned_container(
    container_id: str,
    cell: NavigationCell,
    run_id: str,
    command_runner: Callable[..., Any],
) -> None:
    identity = _completed(command_runner, (
        "docker", "inspect", "--format",
        f'{{{{.Id}}}}|{{{{.Name}}}}|{{{{index .Config.Labels "{OWNER_LABEL}"}}}}|'
        f'{{{{index .Config.Labels "{RUN_LABEL}"}}}}|'
        f'{{{{index .Config.Labels "{CELL_LABEL}"}}}}',
        container_id,
    ))
    expected = (
        f"{container_id}|/{cell.container_name}|{OWNER_VALUE}|{run_id}|{cell.id}"
    )
    if identity.returncode != 0 or str(identity.stdout).strip() != expected:
        raise MatrixSafetyError("navigation_matrix_container_ownership_lost")
    removed = _completed(command_runner, ("docker", "rm", "--force", container_id), timeout=20)
    if removed.returncode != 0:
        raise MatrixSafetyError("navigation_matrix_container_cleanup_failed")


def _cleanup_failed_bot_start(
    cell: NavigationCell,
    run_id: str,
    command_runner: Callable[..., Any],
) -> None:
    identity = _completed(command_runner, ("docker", "inspect", "--format", "{{.Id}}", cell.container_name))
    container_id = str(identity.stdout or "").strip()
    if identity.returncode != 0:
        return
    if not re.fullmatch(r"[0-9a-f]{64}", container_id):
        raise MatrixSafetyError("navigation_matrix_failed_start_identity_invalid")
    _remove_owned_container(container_id, cell, run_id, command_runner)


def _start_bot_container(
    repo_root: Path,
    cell_root: Path,
    cell: NavigationCell,
    run_id: str,
    *,
    image: str,
    command_runner: Callable[..., Any],
) -> str:
    try:
        result = _completed(
            command_runner,
            docker_run_command(repo_root, cell_root, cell, run_id, image=image),
            timeout=30,
        )
    except (OSError, subprocess.TimeoutExpired):
        _cleanup_failed_bot_start(cell, run_id, command_runner)
        raise
    container_id = str(result.stdout or "").strip()
    if result.returncode != 0 or not re.fullmatch(r"[0-9a-f]{12,64}", container_id):
        _cleanup_failed_bot_start(cell, run_id, command_runner)
        raise MatrixSafetyError("navigation_matrix_bot_start_failed")
    return container_id


def _container_absent(cell: NavigationCell, command_runner: Callable[..., Any]) -> bool:
    if _completed(command_runner, ("docker", "info")).returncode != 0:
        return False
    return _completed(command_runner, ("docker", "inspect", cell.container_name)).returncode != 0


def _all_containers_absent(command_runner: Callable[..., Any]) -> bool:
    return all(_container_absent(cell, command_runner) for cell in CELLS)


def _wait_for_ready_status(
    status_path: Path,
    container_id: str,
    deadline: float,
    *,
    command_runner: Callable[..., Any],
    monotonic: Callable[[], float],
    epoch: Callable[[], float],
    sleeper: Callable[[float], None],
) -> Mapping[str, Any] | None:
    first_update = None
    next_container_check = monotonic()
    while monotonic() < deadline:
        status = _read_json(status_path)
        controller = status.get("survival_controller") if status else None
        updated = _number(status.get("updated_at")) if status else None
        fresh = updated is not None and -1 <= epoch() - updated <= 3
        if (
            status
            and status.get("running") is True
            and status.get("connected") is True
            and status.get("connection_state") == "connected"
            and isinstance(controller, Mapping)
            and fresh
        ):
            if first_update is None:
                first_update = updated
            elif updated > first_update:
                return status
        now = monotonic()
        if now >= next_container_check:
            if not _production_stopped(command_runner):
                raise MatrixSafetyError("production_mindcraft_started_during_navigation_cell")
            if not _container_running(container_id, command_runner):
                return None
            next_container_check = now + 1
        sleeper(0.1)
    return None


def _world_progress(server: OwnedJavaServer, username: str) -> dict[str, int]:
    values = {
        key: max(0, server.query_result(f"run scoreboard players get {username} {objective}"))
        for key, objective, _criterion in WORLD_PROGRESS_OBJECTIVES
        if key in {"walked_cm", "sprinted_cm", "wooden_pickaxes_crafted"}
    }
    values["logs_mined"] = sum(
        max(0, server.query_result(f"run scoreboard players get {username} {objective}"))
        for objective, _criterion in LOG_PROGRESS_OBJECTIVES
    )
    return values


def _empty_evidence() -> dict[str, Any]:
    return {
        "connected_fresh": False,
        "goal_manager_off": False,
        "path_updates": 0,
        "nonempty_path_updates": 0,
        "verified_goal_reached": 0,
        "partial_updates": 0,
        "timeout_updates": 0,
        "no_path_updates": 0,
        "stuck_resets": 0,
        "walked_cm": 0,
        "sprinted_cm": 0,
        "logs_mined": 0,
        "wooden_pickaxes_crafted": 0,
        "pickaxe_inventory": False,
        "death_count": 0,
        "runtime_error": False,
    }


def _observe(evidence: dict[str, Any], status: Mapping[str, Any] | None, progress: Mapping[str, Any], *, now: float) -> None:
    updated = _number(status.get("updated_at")) if status else None
    evidence["connected_fresh"] = bool(
        status
        and status.get("running") is True
        and status.get("connected") is True
        and status.get("connection_state") == "connected"
        and updated is not None
        and -1 <= now - updated <= 3
    )
    task_contract = status.get("task_contract") if status else None
    if isinstance(task_contract, Mapping) and task_contract.get("goal_manager_mode") == "off":
        evidence["goal_manager_off"] = True
    navigation = status.get("navigation") if status else None
    if isinstance(navigation, Mapping) and navigation.get("content_free") is True:
        for key in (
            "path_updates", "nonempty_path_updates", "verified_goal_reached",
            "partial_updates", "timeout_updates", "no_path_updates", "stuck_resets",
        ):
            value = _number(navigation.get(key))
            if value is not None:
                evidence[key] = max(evidence[key], max(0, int(value)))
    for key in ("walked_cm", "sprinted_cm", "logs_mined", "wooden_pickaxes_crafted"):
        value = _number(progress.get(key))
        if value is not None:
            evidence[key] = max(evidence[key], max(0, int(value)))
    inventory = status.get("inventory") if status else None
    if isinstance(inventory, Mapping) and any(
        str(name).endswith("_pickaxe") and (_number(count) or 0) > 0
        for name, count in inventory.items()
    ):
        evidence["pickaxe_inventory"] = True
    controller = status.get("survival_controller") if status else None
    evidence["runtime_error"] = evidence["runtime_error"] or bool(
        (status and status.get("last_error"))
        or (isinstance(controller, Mapping) and controller.get("last_error"))
    )
    goal = status.get("goal_manager") if status else None
    if isinstance(goal, Mapping):
        evidence["death_count"] = max(evidence["death_count"], int(goal.get("death_count") or 0))
    health = _number(status.get("health")) if status else None
    if status and (status.get("phase") == "respawning" or status.get("last_death_event")):
        evidence["death_count"] = max(evidence["death_count"], 1)
    if health is not None and health <= 0:
        evidence["death_count"] = max(evidence["death_count"], 1)


def _acceptance(evidence: Mapping[str, Any], cell: NavigationCell) -> dict[str, bool]:
    recovery = sum(int(evidence[key]) for key in (
        "timeout_updates", "no_path_updates", "stuck_resets",
    )) > 0
    return {
        "connected_fresh": evidence["connected_fresh"] is True,
        "goal_manager_off": evidence["goal_manager_off"] is True,
        "navigation_path_observed": int(evidence["path_updates"]) > 0,
        "navigation_nonempty_path_observed": int(evidence["nonempty_path_updates"]) > 0,
        "navigation_verified_goal_reached": int(evidence["verified_goal_reached"]) > 0,
        "movement_observed": int(evidence["walked_cm"]) + int(evidence["sprinted_cm"]) > 0,
        "log_collection_observed": int(evidence["logs_mined"]) > 0,
        "pickaxe_crafted_observed": int(evidence["wooden_pickaxes_crafted"]) > 0,
        "pickaxe_inventory_observed": evidence["pickaxe_inventory"] is True,
        "death_zero": int(evidence["death_count"]) == 0,
        "runtime_errors_zero": evidence["runtime_error"] is False,
        "fallback_recovery_observed": cell.id != "blocked_batch_fallback" or recovery,
    }


def _cell_report(
    cell: NavigationCell,
    evidence: Mapping[str, Any],
    *,
    infrastructure_valid: bool,
    setup_verified: bool,
    cleanup_verified: bool,
    error_code: str | None,
) -> dict[str, Any]:
    acceptance = _acceptance(evidence, cell)
    acceptance.update({
        "infrastructure_valid": infrastructure_valid,
        "setup_verified": setup_verified,
        "cleanup_verified": cleanup_verified,
    })
    return {
        "contentFree": True,
        "cell": cell.id,
        "port": cell.port,
        "infrastructureCode": error_code,
        "navigation": {
            key: evidence[key]
            for key in (
                "path_updates", "nonempty_path_updates", "verified_goal_reached",
                "partial_updates", "timeout_updates", "no_path_updates", "stuck_resets",
            )
        },
        "worldProgress": {
            "walkedCm": evidence["walked_cm"],
            "sprintedCm": evidence["sprinted_cm"],
            "logsMined": evidence["logs_mined"],
            "woodenPickaxesCrafted": evidence["wooden_pickaxes_crafted"],
            "pickaxeInventoryObserved": evidence["pickaxe_inventory"],
        },
        "deathCount": evidence["death_count"],
        "runtimeErrorObserved": evidence["runtime_error"],
        "acceptance": acceptance,
        "cleanupVerified": cleanup_verified,
        "passed": all(acceptance.values()),
    }


def _monitor_cell(
    cell: NavigationCell,
    status_path: Path,
    container_id: str,
    server: OwnedJavaServer,
    evidence: dict[str, Any],
    *,
    command_runner: Callable[..., Any],
    port_probe: Callable[[int], bool],
    monotonic: Callable[[], float] = time.monotonic,
    epoch: Callable[[], float] = time.time,
    sleeper: Callable[[float], None] = time.sleep,
    progress_reader: Callable[[OwnedJavaServer, str], Mapping[str, Any]] = _world_progress,
) -> str | None:
    deadline = monotonic() + CELL_TIMEOUT_SECONDS
    next_runtime_check = monotonic()
    next_progress_check = monotonic()
    progress: Mapping[str, Any] = {}
    while monotonic() < deadline:
        now = monotonic()
        if now >= next_progress_check:
            progress = progress_reader(server, cell.username)
            next_progress_check = now + 5
        _observe(evidence, _read_json(status_path), progress, now=epoch())
        acceptance = _acceptance(evidence, cell)
        if all(acceptance.values()):
            return None
        if evidence["death_count"] or evidence["runtime_error"]:
            return "navigation_cell_runtime_failure"
        if now >= next_runtime_check:
            if server.process.poll() is not None or not port_probe(cell.port):
                return "navigation_server_exit"
            if not _container_running(container_id, command_runner):
                return "navigation_container_exit"
            if not _production_stopped(command_runner) or port_probe(PRODUCTION_PORT):
                return "production_mindcraft_started_during_navigation_cell"
            next_runtime_check = now + 1
        sleeper(0.2)
    progress = progress_reader(server, cell.username)
    _observe(evidence, _read_json(status_path), progress, now=epoch())
    return None if all(_acceptance(evidence, cell).values()) else "navigation_cell_timeout"


def run_navigation_cell(
    cell: NavigationCell,
    repo_root: Path,
    artifact_root: Path,
    server_jar: Path,
    java_executable: str,
    run_id: str,
    *,
    image: str = BOT_IMAGE,
    command_runner: Callable[..., Any] = subprocess.run,
    port_probe: Callable[[int], bool] = _port_in_use,
    server_factory: Callable[..., OwnedJavaServer] = start_server,
    bot_starter: Callable[..., str] = _start_bot_container,
    ready_waiter: Callable[..., Mapping[str, Any] | None] = _wait_for_ready_status,
    monitor: Callable[..., str | None] = _monitor_cell,
    container_remover: Callable[..., None] = _remove_owned_container,
    production_checker: Callable[[Callable[..., Any]], bool] = _production_stopped,
    container_absent_checker: Callable[[NavigationCell, Callable[..., Any]], bool] = _container_absent,
) -> dict[str, Any]:
    if (
        repo_root.resolve() != SCRIPT_REPO_ROOT
        or artifact_root.resolve() != (SCRIPT_REPO_ROOT / FIXTURE_RELATIVE).resolve()
        or cell not in CELLS
    ):
        raise MatrixSafetyError("navigation_cell_scope_invalid")
    cell_root = artifact_root / "cells" / cell.id
    cell_root.mkdir(exist_ok=False)
    (cell_root / "owner.json").write_text(json.dumps({
        "schema": MATRIX_SCHEMA,
        "owner": OWNER_VALUE,
        "runId": run_id,
        "cell": cell.id,
        "contentFree": True,
    }, separators=(",", ":")), encoding="utf-8")
    bot_root = cell_root / "bot"
    (bot_root / "mindcraft").mkdir(parents=True)
    _write_cell_profile(bot_root, cell)
    _prepare_server_directory(cell_root / "server", cell)

    evidence = _empty_evidence()
    server = None
    container_id = None
    infrastructure_valid = True
    setup_verified = False
    error_code = None
    server_cleanup_ok = True
    container_cleanup_ok = True
    try:
        if not production_checker(command_runner) or port_probe(PRODUCTION_PORT):
            raise MatrixSafetyError("production_mindcraft_started_during_navigation_cell")
        server = server_factory(cell, cell_root / "server", server_jar, java_executable)
        setup_verified = verify_cell_setup(server, cell)
        if not setup_verified:
            raise MatrixSafetyError("navigation_cell_setup_unverified")
        container_id = bot_starter(
            repo_root, cell_root, cell, run_id, image=image, command_runner=command_runner,
        )
        ready = ready_waiter(
            cell_root / "bot/mindcraft/status.json",
            container_id,
            time.monotonic() + BOT_READY_TIMEOUT_SECONDS,
            command_runner=command_runner,
            monotonic=time.monotonic,
            epoch=time.time,
            sleeper=time.sleep,
        )
        if ready is None:
            raise MatrixSafetyError("navigation_bot_ready_unverified")
        task_contract = ready.get("task_contract") if isinstance(ready, Mapping) else None
        if not isinstance(task_contract, Mapping) or task_contract.get("goal_manager_mode") != "off":
            raise MatrixSafetyError("navigation_goal_manager_off_unverified")
        _observe(evidence, ready, {}, now=time.time())
        error_code = monitor(
            cell,
            cell_root / "bot/mindcraft/status.json",
            container_id,
            server,
            evidence,
            command_runner=command_runner,
            port_probe=port_probe,
        )
    except Exception as error:
        infrastructure_valid = False
        error_code = str(error) if isinstance(error, MatrixSafetyError) else "navigation_cell_infrastructure_failed"
    finally:
        if container_id is not None:
            try:
                container_remover(container_id, cell, run_id, command_runner)
            except Exception:
                container_cleanup_ok = False
        if server is not None:
            try:
                server.stop()
            except Exception:
                server_cleanup_ok = False

    cleanup_verified = bool(
        server_cleanup_ok
        and container_cleanup_ok
        and (server is None or server.process.poll() is not None)
        and not port_probe(cell.port)
        and not port_probe(PRODUCTION_PORT)
        and production_checker(command_runner)
        and container_absent_checker(cell, command_runner)
    )
    return _cell_report(
        cell,
        evidence,
        infrastructure_valid=infrastructure_valid,
        setup_verified=setup_verified,
        cleanup_verified=cleanup_verified,
        error_code=error_code,
    )


def _failed_cell(cell: NavigationCell, error_code: str) -> dict[str, Any]:
    return _cell_report(
        cell,
        _empty_evidence(),
        infrastructure_valid=False,
        setup_verified=False,
        cleanup_verified=False,
        error_code=error_code,
    )


def _write_report(path: Path, report: Mapping[str, Any]) -> None:
    temporary = path.with_suffix(".tmp")
    temporary.write_text(json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True), encoding="utf-8")
    temporary.replace(path)


def run_matrix(
    repo_root: Path,
    artifact_root: Path,
    server_jar: Path,
    java_executable: str,
    *,
    workers: int = DEFAULT_WORKERS,
    image: str = BOT_IMAGE,
    command_runner: Callable[..., Any] = subprocess.run,
    port_probe: Callable[[int], bool] = _port_in_use,
    preflight: Callable[..., None] = preflight_run,
    cell_runner: Callable[..., dict[str, Any]] = run_navigation_cell,
    production_checker: Callable[[Callable[..., Any]], bool] = _production_stopped,
    containers_absent_checker: Callable[[Callable[..., Any]], bool] = _all_containers_absent,
) -> dict[str, Any]:
    if isinstance(workers, bool) or not isinstance(workers, int) or not 1 <= workers <= MAX_WORKERS:
        raise MatrixSafetyError("navigation_workers_out_of_range")
    preflight(
        repo_root,
        artifact_root,
        server_jar,
        java_executable,
        image=image,
        command_runner=command_runner,
        port_probe=port_probe,
    )
    run_id = uuid4().hex
    artifact_root.mkdir(parents=True, exist_ok=False)
    (artifact_root / "cells").mkdir()
    (artifact_root / "owner.json").write_text(json.dumps({
        "schema": MATRIX_SCHEMA,
        "owner": OWNER_VALUE,
        "runId": run_id,
        "contentFree": True,
    }, separators=(",", ":")), encoding="utf-8")

    observations: dict[str, dict[str, Any]] = {}
    with ThreadPoolExecutor(max_workers=workers, thread_name_prefix="evelyn-nav") as executor:
        futures = {
            executor.submit(
                cell_runner,
                cell,
                repo_root,
                artifact_root,
                server_jar,
                java_executable,
                run_id,
                image=image,
                command_runner=command_runner,
                port_probe=port_probe,
            ): cell
            for cell in CELLS
        }
        for future in as_completed(futures):
            cell = futures[future]
            try:
                observations[cell.id] = future.result()
            except Exception:
                observations[cell.id] = _failed_cell(cell, "navigation_cell_runner_failed")

    cleanup_verified = bool(
        all(observations.get(cell.id, {}).get("cleanupVerified") is True for cell in CELLS)
        and not any(port_probe(port) for port in (PRODUCTION_PORT, *(cell.port for cell in CELLS)))
        and production_checker(command_runner)
        and containers_absent_checker(command_runner)
    )
    ordered = {cell.id: observations.get(cell.id, _failed_cell(cell, "navigation_cell_missing")) for cell in CELLS}
    report = {
        "schema": REPORT_SCHEMA,
        "contentFree": True,
        "liveExecution": True,
        "mode": "parallel_navigation_matrix",
        "workers": workers,
        "cellCount": len(CELLS),
        "cells": ordered,
        "cleanupVerified": cleanup_verified,
        "passed": cleanup_verified and all(item["passed"] is True for item in ordered.values()),
    }
    _write_report(artifact_root / "report.json", report)
    return report


def dry_run_manifest(repo_root: Path, artifact_root: Path | None = None) -> dict[str, Any]:
    validate_cells()
    return {
        "schema": MATRIX_SCHEMA,
        "contentFree": True,
        "liveExecution": False,
        "mode": "dry_run",
        "defaultWorkers": DEFAULT_WORKERS,
        "maxWorkers": MAX_WORKERS,
        "cells": [
            {
                "id": cell.id,
                "port": cell.port,
                "username": cell.username,
                "container": cell.container_name,
                "serverAuth": "offline",
                "requiresRecoveryEvidence": cell.id == "blocked_batch_fallback",
            }
            for cell in CELLS
        ],
        "acceptance": {
            "connectedFresh": True,
            "goalManagerMode": "off",
            "pathUpdatesGreaterThan": 0,
            "nonemptyPathUpdatesGreaterThan": 0,
            "verifiedGoalReachedGreaterThan": 0,
            "movementCmGreaterThan": 0,
            "logsMinedGreaterThan": 0,
            "woodenPickaxesCraftedGreaterThan": 0,
            "pickaxeInventoryObserved": True,
            "deathCount": 0,
            "runtimeErrors": 0,
            "cleanupVerified": True,
        },
        "cleanup": cleanup_plan(repo_root, artifact_root=artifact_root),
    }


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Run Evelyn's isolated parallel navigation matrix.")
    parser.add_argument("--repo-root", type=Path, default=SCRIPT_REPO_ROOT)
    parser.add_argument("--artifact-root", type=Path)
    parser.add_argument("--run", action="store_true")
    parser.add_argument("--server-jar", type=Path)
    parser.add_argument("--java", type=Path)
    parser.add_argument("--workers", type=int, default=DEFAULT_WORKERS)
    parser.add_argument("--cleanup-plan", action="store_true")
    args = parser.parse_args(argv)
    try:
        artifact_root = args.artifact_root or (SCRIPT_REPO_ROOT / FIXTURE_RELATIVE)
        if args.run:
            if args.cleanup_plan:
                raise MatrixSafetyError("run_mode_conflicts_with_cleanup_plan")
            if args.server_jar is None:
                raise MatrixSafetyError("run_requires_server_jar")
            java = str(args.java.resolve()) if args.java else shutil.which("java")
            if not java:
                raise MatrixSafetyError("java_executable_missing")
            payload = run_matrix(
                args.repo_root,
                artifact_root,
                args.server_jar,
                java,
                workers=args.workers,
            )
        elif args.cleanup_plan:
            payload = {
                "schema": MATRIX_SCHEMA,
                "contentFree": True,
                "liveExecution": False,
                "mode": "cleanup_plan_only",
                "cleanup": cleanup_plan(args.repo_root, artifact_root=artifact_root),
            }
        else:
            payload = dry_run_manifest(args.repo_root, artifact_root)
    except (OSError, subprocess.TimeoutExpired):
        error_code = "navigation_matrix_command_failed"
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
