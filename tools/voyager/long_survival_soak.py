from __future__ import annotations

import argparse
from dataclasses import dataclass, field
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
        BOT_USERNAME,
        OWNER_LABEL,
        PRODUCTION_CONTAINER,
        PRODUCTION_PORT,
        RUN_LABEL,
        MatrixSafetyError,
        OwnedJavaServer,
        _bot_settings as _matrix_bot_settings,
        _combat_episodes,
        _completed,
        _container_running,
        _port_in_use,
        _production_stopped,
        _read_json,
    )
else:
    from combat_matrix import (  # type: ignore[no-redef]
        BOT_IMAGE,
        BOT_USERNAME,
        OWNER_LABEL,
        PRODUCTION_CONTAINER,
        PRODUCTION_PORT,
        RUN_LABEL,
        MatrixSafetyError,
        OwnedJavaServer,
        _bot_settings as _matrix_bot_settings,
        _combat_episodes,
        _completed,
        _container_running,
        _port_in_use,
        _production_stopped,
        _read_json,
    )


SOAK_SCHEMA = "evelyn.validation.long-survival-soak.v4"
REPORT_SCHEMA = "evelyn.validation.long-survival-soak-report.v3"
FIXTURE_RELATIVE = Path("runtime_artifacts/validation/long_survival_soak")
GAME_PORT = 25574
CONTAINER_NAME = "evelyn-long-survival-soak"
OWNER_VALUE = "long_survival_soak"
DURATION_SECONDS = 1_200
SERVER_START_TIMEOUT_SECONDS = 90
BOT_READY_TIMEOUT_SECONDS = 45
MAX_CRITICAL_SECONDS = 180
AUTONOMOUS_PROGRESS_TIMEOUT_SECONDS = 300
WORLD_PROGRESS_POLL_SECONDS = 15
MIN_COVERAGE = 0.95
MIN_FINAL_HEALTH_EXCLUSIVE = 10
MIN_FINAL_HUNGER_EXCLUSIVE = 6
WORLD_SEED = 5_031_407
SCRIPT_REPO_ROOT = Path(__file__).resolve().parents[2]
NATURAL_GAMERULES = (
    "spawn_mobs",
    "natural_health_regeneration",
    "advance_time",
    "advance_weather",
    "mob_drops",
    "mob_griefing",
)
SURVIVAL_ACTIONS = (
    "bootstrap_tools",
    "acquire_food",
    "eat_inventory_food",
    "shelter_until_safe_dawn",
    "handle_hostile",
)
WORLD_PROGRESS_OBJECTIVES = (
    ("walked_cm", "evwalk", "minecraft.custom:minecraft.walk_one_cm"),
    ("sprinted_cm", "evsprint", "minecraft.custom:minecraft.sprint_one_cm"),
    ("wooden_pickaxes_crafted", "evtool", "minecraft.crafted:minecraft.wooden_pickaxe"),
    ("dirt_used", "evdirt", "minecraft.used:minecraft.dirt"),
)
LOG_PROGRESS_OBJECTIVES = tuple(
    (f"evlog{index}", f"minecraft.mined:minecraft.{wood}_log")
    for index, wood in enumerate((
        "oak", "spruce", "birch", "jungle", "acacia",
        "dark_oak", "mangrove", "cherry", "pale_oak",
    ))
)


def _number(value: Any) -> float | None:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    result = float(value)
    return result if result == result and abs(result) != float("inf") else None


def _wait_for_soak_ready_status(
    status_path: Path,
    container_id: str,
    deadline: float,
    *,
    command_runner: Callable[..., Any],
    monotonic: Callable[[], float],
    epoch: Callable[[], float],
    sleeper: Callable[[float], None],
) -> Mapping[str, Any] | None:
    next_container_check = monotonic()
    first_ready_update = None
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


def cleanup_plan(
    repo_root: Path,
    *,
    artifact_root: Path | None = None,
    game_port: int = GAME_PORT,
) -> dict[str, Any]:
    repo = repo_root.resolve()
    expected = (repo / FIXTURE_RELATIVE).resolve()
    requested = (artifact_root or expected).resolve()
    validation = (repo / "runtime_artifacts/validation").resolve()
    if requested != expected:
        raise MatrixSafetyError("artifact_root_not_exact_long_soak_fixture")
    if game_port != GAME_PORT or game_port == PRODUCTION_PORT:
        raise MatrixSafetyError("game_port_not_exact_long_soak_port")
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
    expected = (SCRIPT_REPO_ROOT / FIXTURE_RELATIVE).resolve()
    if repo != SCRIPT_REPO_ROOT or artifact_root.resolve() != expected:
        raise MatrixSafetyError("run_root_not_script_owned_workspace")
    cleanup_plan(repo, artifact_root=artifact_root)
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
        raise MatrixSafetyError("long_soak_image_missing")
    if not _production_stopped(command_runner):
        raise MatrixSafetyError("production_mindcraft_must_be_stopped")
    if _completed(command_runner, ("docker", "inspect", CONTAINER_NAME)).returncode == 0:
        raise MatrixSafetyError("long_soak_container_name_already_exists")


def _prepare_server_directory(server_dir: Path) -> None:
    server_dir.mkdir(parents=True, exist_ok=False)
    (server_dir / "eula.txt").write_text("eula=true\n", encoding="utf-8")
    properties = "\n".join((
        "allow-flight=false",
        "difficulty=normal",
        "enable-query=false",
        "enable-rcon=false",
        "enforce-secure-profile=false",
        "force-gamemode=true",
        "gamemode=survival",
        "generate-structures=true",
        "hardcore=false",
        "level-name=world",
        f"level-seed={WORLD_SEED}",
        "max-players=2",
        "motd=Evelyn isolated long survival soak",
        "online-mode=true",
        "pause-when-empty-seconds=-1",
        "pvp=true",
        f"server-port={GAME_PORT}",
        "simulation-distance=8",
        "spawn-animals=true",
        "spawn-monsters=true",
        "spawn-npcs=true",
        "spawn-protection=0",
        "view-distance=8",
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
    server = OwnedJavaServer(process)
    try:
        server.wait_for(
            re.compile(r"Done \(.+\)! For help, type"), SERVER_START_TIMEOUT_SECONDS,
        )
        server.wait_for(re.compile(r"Starting minecraft server version 1\.21\.11"), 1)
        _add_scoreboard_objective(server, "evcm", "dummy")
        for _key, objective, criterion in WORLD_PROGRESS_OBJECTIVES:
            _add_scoreboard_objective(server, objective, criterion)
        for objective, criterion in LOG_PROGRESS_OBJECTIVES:
            _add_scoreboard_objective(server, objective, criterion)
        server.command("difficulty normal")
        for gamerule in NATURAL_GAMERULES:
            server.command(f"gamerule {gamerule} true")
        server.command("gamerule keep_inventory false")
        return server
    except BaseException:
        server.stop()
        raise


def verify_natural_server_setup(
    server: OwnedJavaServer,
    server_dir: Path,
    *,
    sleeper: Callable[[float], None] = time.sleep,
) -> bool:
    if not (server_dir / "world").is_dir():
        return False
    checks = [server.query_result(f"run gamerule {name}") == 1 for name in NATURAL_GAMERULES]
    checks.append(server.query_result("run gamerule keep_inventory") == 0)
    cursor = server._cursor()
    server.command("difficulty")
    server.wait_for(re.compile(r"difficulty is normal", re.IGNORECASE), 3, after=cursor)
    before = server.query_result("run time query daytime")
    sleeper(0.6)
    after = server.query_result("run time query daytime")
    advanced = (after - before) % 24_000
    checks.append(0 < advanced < 200)
    return all(checks)


def _add_scoreboard_objective(
    server: OwnedJavaServer,
    name: str,
    criterion: str,
) -> None:
    cursor = server._cursor()
    server.command(f"scoreboard objectives add {name} {criterion}")
    server.wait_for(
        re.compile(rf"Created new objective \[{re.escape(name)}\]$"),
        3,
        after=cursor,
    )


def _world_progress_stats(server: OwnedJavaServer) -> dict[str, int]:
    values = {
        key: max(0, server.query_result(
            f"run scoreboard players get {BOT_USERNAME} {objective}"
        ))
        for key, objective, _criterion in WORLD_PROGRESS_OBJECTIVES
    }
    values["logs_mined"] = sum(
        max(0, server.query_result(
            f"run scoreboard players get {BOT_USERNAME} {objective}"
        ))
        for objective, _criterion in LOG_PROGRESS_OBJECTIVES
    )
    return values


def _bot_settings() -> str:
    settings = json.loads(_matrix_bot_settings())
    settings["host"] = "host.docker.internal"
    settings["port"] = GAME_PORT
    return json.dumps(settings, separators=(",", ":"))


def docker_run_command(
    repo_root: Path,
    artifact_root: Path,
    run_id: str,
    *,
    image: str = BOT_IMAGE,
) -> tuple[str, ...]:
    profiles = (repo_root / "bot_profiles").resolve()
    runtime = (artifact_root / "bot").resolve()
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
        "--env", "MINEFLAYER_HOST=host.docker.internal",
        "--env", f"MINEFLAYER_PORT={GAME_PORT}",
        "--env", "MINEFLAYER_AUTH=microsoft",
        "--env", f"MINECRAFT_USERNAME={BOT_USERNAME}",
        "--env", "MINECRAFT_VERSION=1.21.11",
        "--env", "MINEFLAYER_PROFILES_FOLDER=/app/bot_profiles",
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
        "sh", "-lc",
        "cp -R /run/evelyn-auth-seed/. /app/bot_profiles/ && "
        "chmod -R u+rwX /app/bot_profiles && exec node main.js",
    )


def _start_bot_container(
    repo_root: Path,
    artifact_root: Path,
    run_id: str,
    *,
    image: str,
    command_runner: Callable[..., Any],
) -> str:
    try:
        result = _completed(
            command_runner,
            docker_run_command(repo_root, artifact_root, run_id, image=image),
            timeout=30,
        )
    except (OSError, subprocess.TimeoutExpired):
        _cleanup_failed_bot_start(run_id, command_runner)
        raise
    container_id = str(result.stdout or "").strip()
    if result.returncode != 0 or not re.fullmatch(r"[0-9a-f]{12,64}", container_id):
        _cleanup_failed_bot_start(run_id, command_runner)
        raise MatrixSafetyError("long_soak_bot_start_failed")
    return container_id


def _cleanup_failed_bot_start(
    run_id: str,
    command_runner: Callable[..., Any],
) -> None:
    identity = _completed(command_runner, (
        "docker", "inspect", "--format",
        f'{{{{.Id}}}}|{{{{.Name}}}}|{{{{index .Config.Labels "{OWNER_LABEL}"}}}}|'
        f'{{{{index .Config.Labels "{RUN_LABEL}"}}}}',
        CONTAINER_NAME,
    ))
    if identity.returncode != 0:
        return
    container_id = str(identity.stdout or "").strip().split("|", 1)[0]
    if not re.fullmatch(r"[0-9a-f]{64}", container_id):
        raise MatrixSafetyError("long_soak_failed_start_container_identity_invalid")
    _remove_owned_container(container_id, run_id, command_runner)


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
        raise MatrixSafetyError("long_soak_container_ownership_lost")
    removed = _completed(command_runner, ("docker", "rm", "--force", container_id), timeout=20)
    if removed.returncode != 0:
        raise MatrixSafetyError("long_soak_container_cleanup_failed")


def _container_absent(command_runner: Callable[..., Any]) -> bool:
    if _completed(command_runner, ("docker", "info")).returncode != 0:
        return False
    return _completed(command_runner, ("docker", "inspect", CONTAINER_NAME)).returncode != 0


@dataclass
class SoakEvidence:
    started_epoch: float
    samples: int = 0
    valid_samples: int = 0
    min_health: float | None = None
    min_hunger: float | None = None
    final_health: float | None = None
    final_hunger: float | None = None
    final_connected_fresh: bool = False
    death_count: int = 0
    walked_cm: int = 0
    sprinted_cm: int = 0
    logs_mined: int = 0
    wooden_pickaxes_crafted: int = 0
    dirt_used: int = 0
    pickaxe_inventory_observed: bool = False
    goal_manager_off_observed: bool = False
    navigation_path_updates: int = 0
    navigation_nonempty_path_updates: int = 0
    navigation_goal_reached: int = 0
    navigation_verified_goal_reached: int = 0
    navigation_partial_updates: int = 0
    navigation_timeout_updates: int = 0
    navigation_no_path_updates: int = 0
    navigation_stuck_resets: int = 0
    navigation_active_final: bool = False
    action_entries: dict[str, int] = field(
        default_factory=lambda: dict.fromkeys(SURVIVAL_ACTIONS, 0),
    )
    action_successes: dict[str, int] = field(
        default_factory=lambda: dict.fromkeys(SURVIVAL_ACTIONS, 0),
    )
    action_failures: dict[str, int] = field(
        default_factory=lambda: dict.fromkeys(SURVIVAL_ACTIONS, 0),
    )
    combat_episode_count: int = 0
    combat_outcomes: dict[str, int] = field(default_factory=dict)
    critical_episodes: int = 0
    critical_resolved: int = 0
    critical_since: float | None = None
    longest_critical_seconds: float = 0
    runtime_error_codes: set[str] = field(default_factory=set)
    abort_reason: str | None = None
    _last_action_phase: str | None = field(default=None, repr=False)
    _last_terminal: tuple[str, bool] | None = field(default=None, repr=False)
    _critical_death_observed: bool = field(default=False, repr=False)

    @property
    def autonomous_world_progress_observed(self) -> bool:
        return (
            self.walked_cm + self.sprinted_cm > 0
            and self.logs_mined > 0
            and self.wooden_pickaxes_crafted > 0
            and self.pickaxe_inventory_observed
        )

    def observe_world_progress(self, values: Mapping[str, Any] | None) -> None:
        if not isinstance(values, Mapping):
            return
        for key in ("walked_cm", "sprinted_cm", "logs_mined", "wooden_pickaxes_crafted", "dirt_used"):
            value = _number(values.get(key))
            if value is not None:
                setattr(self, key, max(getattr(self, key), max(0, int(value))))

    def observe(
        self,
        status: Mapping[str, Any] | None,
        combat: Sequence[Mapping[str, Any]],
        *,
        now_epoch: float,
        now_monotonic: float,
        world_progress: Mapping[str, Any] | None = None,
    ) -> None:
        self.samples += 1
        updated = _number(status.get("updated_at")) if status else None
        age = now_epoch - updated if updated is not None else None
        fresh = age is not None and -1 <= age <= 3
        connected = bool(
            status
            and status.get("running") is True
            and status.get("connected") is True
            and status.get("connection_state") == "connected"
        )
        valid = fresh and connected
        if valid:
            self.valid_samples += 1
        self.final_connected_fresh = valid

        health = _number(status.get("health")) if status else None
        hunger = _number(status.get("hunger")) if status else None
        self.final_health = health
        self.final_hunger = hunger
        if health is not None:
            self.min_health = health if self.min_health is None else min(self.min_health, health)
        if hunger is not None:
            self.min_hunger = hunger if self.min_hunger is None else min(self.min_hunger, hunger)

        controller = status.get("survival_controller") if status else None
        task_contract = status.get("task_contract") if status else None
        if (
            isinstance(task_contract, Mapping)
            and task_contract.get("goal_manager_mode") == "off"
        ):
            self.goal_manager_off_observed = True
        if status and status.get("last_error"):
            self.runtime_error_codes.add("runtime_error")
        if isinstance(controller, Mapping) and controller.get("last_error"):
            self.runtime_error_codes.add("survival_error")

        navigation = status.get("navigation") if status else None
        if isinstance(navigation, Mapping) and navigation.get("content_free") is True:
            for source, target in (
                ("path_updates", "navigation_path_updates"),
                ("nonempty_path_updates", "navigation_nonempty_path_updates"),
                ("goal_reached", "navigation_goal_reached"),
                ("verified_goal_reached", "navigation_verified_goal_reached"),
                ("partial_updates", "navigation_partial_updates"),
                ("timeout_updates", "navigation_timeout_updates"),
                ("no_path_updates", "navigation_no_path_updates"),
                ("stuck_resets", "navigation_stuck_resets"),
            ):
                value = _number(navigation.get(source))
                if value is not None:
                    setattr(self, target, max(getattr(self, target), max(0, int(value))))
            self.navigation_active_final = navigation.get("active") is True

        if isinstance(controller, Mapping):
            phase = controller.get("phase")
            action_phase = phase if phase in SURVIVAL_ACTIONS else None
            if action_phase != self._last_action_phase:
                if action_phase:
                    self.action_entries[action_phase] += 1
                self._last_action_phase = action_phase

            decision = controller.get("last_decision")
            success = controller.get("last_success")
            if decision not in SURVIVAL_ACTIONS or not isinstance(success, bool):
                self._last_terminal = None
            else:
                terminal = (decision, success)
                if terminal != self._last_terminal:
                    target = self.action_successes if success else self.action_failures
                    target[decision] += 1
                    self._last_terminal = terminal

        inventory = status.get("inventory") if status else None
        if isinstance(inventory, Mapping) and any(
            str(name).endswith("_pickaxe") and (_number(count) or 0) > 0
            for name, count in inventory.items()
        ):
            self.pickaxe_inventory_observed = True
        self.observe_world_progress(world_progress)

        previous_death_count = self.death_count
        combat_deaths = sum(episode.get("outcome") == "death" for episode in combat)
        self.combat_episode_count = max(self.combat_episode_count, len(combat))
        for outcome in ("success", "failure", "interrupted", "death"):
            self.combat_outcomes[outcome] = max(
                self.combat_outcomes.get(outcome, 0),
                sum(episode.get("outcome") == outcome for episode in combat),
            )
        self.death_count = max(self.death_count, combat_deaths)
        status_goal = status.get("goal_manager") if status else None
        if isinstance(status_goal, Mapping):
            self.death_count = max(self.death_count, int(status_goal.get("death_count") or 0))
        if status and (status.get("phase") == "respawning" or status.get("last_death_event")):
            self.death_count = max(self.death_count, 1)
        if health is not None and health <= 0:
            self.death_count = max(self.death_count, 1)

        critical_observable = valid and health is not None and hunger is not None
        critical = bool(
            critical_observable
            and (health <= MIN_FINAL_HEALTH_EXCLUSIVE or hunger <= MIN_FINAL_HUNGER_EXCLUSIVE)
        )
        death_observed_now = self.death_count > previous_death_count
        if self.critical_since is not None and death_observed_now:
            self._critical_death_observed = True
        if critical_observable:
            if critical and self.critical_since is None:
                self.critical_since = now_monotonic
                self.critical_episodes += 1
                self._critical_death_observed = death_observed_now
            elif not critical and self.critical_since is not None:
                self.longest_critical_seconds = max(
                    self.longest_critical_seconds, now_monotonic - self.critical_since,
                )
                if not self._critical_death_observed:
                    self.critical_resolved += 1
                self.critical_since = None
                self._critical_death_observed = False
        if self.critical_since is not None:
            self.longest_critical_seconds = max(
                self.longest_critical_seconds, now_monotonic - self.critical_since,
            )


def build_report(
    evidence: SoakEvidence,
    *,
    elapsed_seconds: float,
    target_seconds: int = DURATION_SECONDS,
    natural_setup_verified: bool,
    cleanup_verified: bool,
    live_execution: bool,
) -> dict[str, Any]:
    coverage = evidence.valid_samples / evidence.samples if evidence.samples else 0.0
    critical_ok = (
        evidence.critical_since is None
        and evidence.critical_resolved == evidence.critical_episodes
    )
    gates = {
        "duration_complete": elapsed_seconds >= target_seconds,
        "connected_fresh_coverage": coverage >= MIN_COVERAGE,
        "death_zero": evidence.death_count == 0,
        "critical_resolved": critical_ok,
        "run_completed_without_abort": evidence.abort_reason is None,
        "goal_manager_off_observed": evidence.goal_manager_off_observed,
        "autonomous_world_progress": evidence.autonomous_world_progress_observed,
        "navigation_verified_goal_reached": evidence.navigation_verified_goal_reached > 0,
        "final_connected_fresh": evidence.final_connected_fresh,
        "final_health_above_10": (
            evidence.final_health is not None
            and evidence.final_health > MIN_FINAL_HEALTH_EXCLUSIVE
        ),
        "final_hunger_above_6": (
            evidence.final_hunger is not None
            and evidence.final_hunger > MIN_FINAL_HUNGER_EXCLUSIVE
        ),
        "runtime_errors_zero": not evidence.runtime_error_codes,
        "natural_setup_verified": natural_setup_verified,
        "cleanup_verified": cleanup_verified,
    }
    return {
        "schema": REPORT_SCHEMA,
        "contentFree": True,
        "liveExecution": live_execution,
        "mode": "long_survival_soak",
        "targetSec": target_seconds,
        "elapsedSec": round(elapsed_seconds, 1),
        "abortReason": evidence.abort_reason,
        "samples": evidence.samples,
        "validSamples": evidence.valid_samples,
        "connectedFreshCoverage": round(coverage, 4),
        "minHealth": evidence.min_health,
        "minHunger": evidence.min_hunger,
        "final": {
            "connectedFresh": evidence.final_connected_fresh,
            "health": evidence.final_health,
            "hunger": evidence.final_hunger,
        },
        "deathCount": evidence.death_count,
        "critical": {
            "episodes": evidence.critical_episodes,
            "resolved": evidence.critical_resolved,
            "active": evidence.critical_since is not None,
            "longestSec": round(evidence.longest_critical_seconds, 1),
        },
        "autonomousWorldProgress": {
            "observed": evidence.autonomous_world_progress_observed,
            "timeoutSec": AUTONOMOUS_PROGRESS_TIMEOUT_SECONDS,
            "timedOut": evidence.abort_reason == "autonomous_progress_timeout",
            "movementObserved": evidence.walked_cm + evidence.sprinted_cm > 0,
            "logCollectionObserved": evidence.logs_mined > 0,
            "toolCraftObserved": evidence.wooden_pickaxes_crafted > 0,
            "pickaxeInventoryObserved": evidence.pickaxe_inventory_observed,
            "walkedCm": evidence.walked_cm,
            "sprintedCm": evidence.sprinted_cm,
            "movedCm": evidence.walked_cm + evidence.sprinted_cm,
            "logsMined": evidence.logs_mined,
            "woodenPickaxesCrafted": evidence.wooden_pickaxes_crafted,
            "dirtUsed": evidence.dirt_used,
        },
        "navigation": {
            "goalReachedObserved": evidence.navigation_goal_reached > 0,
            "verifiedGoalReachedObserved": evidence.navigation_verified_goal_reached > 0,
            "pathUpdates": evidence.navigation_path_updates,
            "nonemptyPathUpdates": evidence.navigation_nonempty_path_updates,
            "goalReached": evidence.navigation_goal_reached,
            "verifiedGoalReached": evidence.navigation_verified_goal_reached,
            "partialUpdates": evidence.navigation_partial_updates,
            "timeoutUpdates": evidence.navigation_timeout_updates,
            "noPathUpdates": evidence.navigation_no_path_updates,
            "stuckResets": evidence.navigation_stuck_resets,
            "activeAtFinalSample": evidence.navigation_active_final,
        },
        "runtimeConfiguration": {
            "goalManagerOffObserved": evidence.goal_manager_off_observed,
            "bootstrapExecutionVerified": evidence.autonomous_world_progress_observed,
        },
        "survivalActions": {
            action: {
                "entered": evidence.action_entries[action],
                "succeeded": evidence.action_successes[action],
                "failed": evidence.action_failures[action],
            }
            for action in SURVIVAL_ACTIONS
        },
        "combatEpisodes": {
            "total": evidence.combat_episode_count,
            "success": evidence.combat_outcomes.get("success", 0),
            "failure": evidence.combat_outcomes.get("failure", 0),
            "interrupted": evidence.combat_outcomes.get("interrupted", 0),
            "death": evidence.combat_outcomes.get("death", 0),
        },
        "runtimeErrors": {
            "count": len(evidence.runtime_error_codes),
            "codes": sorted(evidence.runtime_error_codes),
        },
        "naturalConditions": {
            "difficulty": "normal",
            "daylightAdvances": True,
            "weatherAdvances": True,
            "mobSpawning": True,
            "naturalRegeneration": True,
            "verified": natural_setup_verified,
        },
        "freshWorld": True,
        "freshArtifact": True,
        "authMountReadOnly": True,
        "authWorkingCopyEphemeral": True,
        "acceptance": gates,
        "cleanupVerified": cleanup_verified,
        "passed": all(gates.values()),
    }


def _write_report(path: Path, report: Mapping[str, Any]) -> None:
    temporary = path.with_suffix(".tmp")
    temporary.write_text(
        json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True),
        encoding="utf-8",
    )
    for attempt in range(5):
        try:
            temporary.replace(path)
            return
        except PermissionError:
            if attempt == 4:
                raise
            time.sleep(0.05)


def _latch_abort_reason(
    evidence: SoakEvidence,
    *,
    elapsed_seconds: float,
    now_monotonic: float,
) -> None:
    if evidence.abort_reason is not None:
        return
    if evidence.death_count:
        evidence.abort_reason = "death_observed"
    elif evidence.runtime_error_codes:
        evidence.abort_reason = "runtime_error_observed"
    elif (
        elapsed_seconds > AUTONOMOUS_PROGRESS_TIMEOUT_SECONDS
        and not evidence.autonomous_world_progress_observed
    ):
        evidence.abort_reason = "autonomous_progress_timeout"
    elif (
        evidence.critical_since is not None
        and now_monotonic - evidence.critical_since > MAX_CRITICAL_SECONDS
    ):
        evidence.abort_reason = "critical_unresolved"


def _runtime_isolation_failure(
    server: OwnedJavaServer,
    container_id: str,
    command_runner: Callable[..., Any],
    port_probe: Callable[[int], bool],
) -> str | None:
    if server.process.poll() is not None or not port_probe(GAME_PORT):
        return "server_exit"
    if not _container_running(container_id, command_runner):
        return "container_exit"
    if not _production_stopped(command_runner) or port_probe(PRODUCTION_PORT):
        return "production_mindcraft_started_during_soak"
    return None


def monitor_soak(
    artifact_root: Path,
    container_id: str,
    server: OwnedJavaServer,
    evidence: SoakEvidence,
    *,
    duration_seconds: int = DURATION_SECONDS,
    command_runner: Callable[..., Any] = subprocess.run,
    port_probe: Callable[[int], bool] = _port_in_use,
    monotonic: Callable[[], float] = time.monotonic,
    epoch: Callable[[], float] = time.time,
    sleeper: Callable[[float], None] = time.sleep,
    world_progress_reader: Callable[[OwnedJavaServer], Mapping[str, Any]] = _world_progress_stats,
) -> float:
    started = monotonic()
    deadline = started + duration_seconds
    next_runtime_check = started
    next_progress_check = started
    status_path = artifact_root / "bot/mindcraft/status.json"
    combat_path = artifact_root / "bot/mindcraft/combat_history.json"
    while monotonic() < deadline and evidence.abort_reason is None:
        sleeper(min(1.0, max(0.0, deadline - monotonic())))
        now = monotonic()
        progress = None
        if now >= next_progress_check and not evidence.autonomous_world_progress_observed:
            progress = world_progress_reader(server)
            next_progress_check = now + WORLD_PROGRESS_POLL_SECONDS
        evidence.observe(
            _read_json(status_path),
            _combat_episodes(combat_path),
            now_epoch=epoch(),
            now_monotonic=now,
            world_progress=progress,
        )
        _latch_abort_reason(
            evidence,
            elapsed_seconds=now - started,
            now_monotonic=now,
        )

        if now >= next_runtime_check and evidence.abort_reason is None:
            evidence.abort_reason = _runtime_isolation_failure(
                server, container_id, command_runner, port_probe,
            )
            next_runtime_check = now + 5

        partial = build_report(
            evidence,
            elapsed_seconds=now - started,
            target_seconds=duration_seconds,
            natural_setup_verified=True,
            cleanup_verified=False,
            live_execution=True,
        )
        _write_report(artifact_root / "monitor_status.json", partial)
    now = monotonic()
    elapsed = now - started
    final_runtime_failure = _runtime_isolation_failure(
        server, container_id, command_runner, port_probe,
    )
    if evidence.abort_reason is None:
        evidence.abort_reason = final_runtime_failure
    if server.process.poll() is None:
        final_progress = world_progress_reader(server)
        final_status = _read_json(status_path)
        final_combat = _combat_episodes(combat_path)
        evidence.observe(
            final_status,
            final_combat,
            now_epoch=epoch(),
            now_monotonic=now,
            world_progress=final_progress,
        )
        _latch_abort_reason(
            evidence,
            elapsed_seconds=elapsed,
            now_monotonic=now,
        )
    post_collection_runtime_failure = _runtime_isolation_failure(
        server, container_id, command_runner, port_probe,
    )
    if evidence.abort_reason is None:
        evidence.abort_reason = post_collection_runtime_failure
    _write_report(artifact_root / "monitor_status.json", build_report(
        evidence,
        elapsed_seconds=elapsed,
        target_seconds=duration_seconds,
        natural_setup_verified=True,
        cleanup_verified=False,
        live_execution=True,
    ))
    return elapsed


def run_soak(
    repo_root: Path,
    artifact_root: Path,
    server_jar: Path,
    java_executable: str,
    *,
    image: str = BOT_IMAGE,
    command_runner: Callable[..., Any] = subprocess.run,
    port_probe: Callable[[int], bool] = _port_in_use,
    preflight: Callable[..., None] = preflight_run,
    server_factory: Callable[..., OwnedJavaServer] = start_server,
    natural_verifier: Callable[..., bool] = verify_natural_server_setup,
    bot_starter: Callable[..., str] = _start_bot_container,
    ready_waiter: Callable[..., Mapping[str, Any] | None] = _wait_for_soak_ready_status,
    monitor: Callable[..., float] = monitor_soak,
    container_remover: Callable[..., None] = _remove_owned_container,
    production_checker: Callable[[Callable[..., Any]], bool] = _production_stopped,
    container_absent_checker: Callable[[Callable[..., Any]], bool] = _container_absent,
    epoch: Callable[[], float] = time.time,
) -> dict[str, Any]:
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
    (artifact_root / "owner.json").write_text(json.dumps({
        "schema": SOAK_SCHEMA,
        "owner": OWNER_VALUE,
        "runId": run_id,
        "contentFree": True,
    }, separators=(",", ":")), encoding="utf-8")
    (artifact_root / "bot").mkdir()
    _prepare_server_directory(artifact_root / "server")

    evidence = SoakEvidence(started_epoch=epoch())
    server = None
    container_id = None
    elapsed = 0.0
    natural_setup_verified = False
    server_cleanup_ok = True
    container_cleanup_ok = True
    try:
        server = server_factory(artifact_root / "server", server_jar, java_executable)
        natural_setup_verified = natural_verifier(server, artifact_root / "server")
        if not natural_setup_verified:
            raise MatrixSafetyError("long_soak_natural_setup_unverified")
        container_id = bot_starter(
            repo_root,
            artifact_root,
            run_id,
            image=image,
            command_runner=command_runner,
        )
        ready = ready_waiter(
            artifact_root / "bot/mindcraft/status.json",
            container_id,
            time.monotonic() + BOT_READY_TIMEOUT_SECONDS,
            command_runner=command_runner,
            monotonic=time.monotonic,
            epoch=epoch,
            sleeper=time.sleep,
        )
        if ready is None:
            raise MatrixSafetyError("long_soak_bot_ready_timeout")
        task_contract = ready.get("task_contract") if isinstance(ready, Mapping) else None
        if (
            not isinstance(task_contract, Mapping)
            or task_contract.get("goal_manager_mode") != "off"
        ):
            raise MatrixSafetyError("long_soak_goal_manager_off_unverified")
        evidence.observe(
            ready,
            [],
            now_epoch=epoch(),
            now_monotonic=time.monotonic(),
        )
        elapsed = monitor(
            artifact_root,
            container_id,
            server,
            evidence,
            command_runner=command_runner,
            port_probe=port_probe,
        )
    except (MatrixSafetyError, OSError, ValueError, subprocess.TimeoutExpired):
        evidence.runtime_error_codes.add("soak_infrastructure_error")
        evidence.abort_reason = evidence.abort_reason or "soak_infrastructure_failed"
    finally:
        if container_id is not None:
            try:
                container_remover(container_id, run_id, command_runner)
            except (MatrixSafetyError, OSError, subprocess.TimeoutExpired):
                container_cleanup_ok = False
        if server is not None:
            try:
                server.stop()
            except (MatrixSafetyError, OSError, subprocess.TimeoutExpired):
                server_cleanup_ok = False

    cleanup_verified = (
        server_cleanup_ok
        and container_cleanup_ok
        and (server is None or server.process.poll() is not None)
        and not port_probe(GAME_PORT)
        and not port_probe(PRODUCTION_PORT)
        and production_checker(command_runner)
        and container_absent_checker(command_runner)
    )
    report = build_report(
        evidence,
        elapsed_seconds=elapsed,
        natural_setup_verified=natural_setup_verified,
        cleanup_verified=cleanup_verified,
        live_execution=True,
    )
    _write_report(artifact_root / "report.json", report)
    return report


def dry_run_manifest(
    repo_root: Path,
    artifact_root: Path | None,
    game_port: int,
) -> dict[str, Any]:
    return {
        "schema": SOAK_SCHEMA,
        "contentFree": True,
        "liveExecution": False,
        "mode": "dry_run",
        "durationSec": DURATION_SECONDS,
        "world": {
            "fresh": True,
            "seed": WORLD_SEED,
            "difficulty": "normal",
            "naturalDaylight": True,
            "naturalWeather": True,
            "naturalMobSpawning": True,
            "naturalRegeneration": True,
        },
        "runtime": {
            "freshArtifact": True,
            "authMountReadOnly": True,
            "authWorkingCopyEphemeral": True,
            "goalManagerMode": "off",
            "deterministicToolBootstrap": True,
            "productionContainerMustRemainStopped": PRODUCTION_CONTAINER,
        },
        "acceptance": {
            "minConnectedFreshCoverage": MIN_COVERAGE,
            "deathCount": 0,
            "allCriticalEpisodesResolved": True,
            "runCompletedWithoutAbort": True,
            "goalManagerOffObserved": True,
            "autonomousWorldProgress": {
                "withinSeconds": AUTONOMOUS_PROGRESS_TIMEOUT_SECONDS,
                "walkedOrSprintedCmGreaterThan": 0,
                "logsMinedGreaterThan": 0,
                "woodenPickaxesCraftedGreaterThan": 0,
                "pickaxeInventoryObserved": True,
            },
            "navigationVerifiedGoalReachedGreaterThan": 0,
            "finalHealthGreaterThan": MIN_FINAL_HEALTH_EXCLUSIVE,
            "finalHungerGreaterThan": MIN_FINAL_HUNGER_EXCLUSIVE,
            "runtimeErrorCount": 0,
            "cleanupVerified": True,
        },
        "cleanup": cleanup_plan(
            repo_root,
            artifact_root=artifact_root,
            game_port=game_port,
        ),
    }


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Run or inspect Evelyn's owned 20-minute isolated survival soak.",
    )
    parser.add_argument("--repo-root", type=Path, default=SCRIPT_REPO_ROOT)
    parser.add_argument("--artifact-root", type=Path)
    parser.add_argument("--game-port", type=int, default=GAME_PORT)
    parser.add_argument("--run", action="store_true", help="Run the exact 20-minute soak.")
    parser.add_argument("--server-jar", type=Path, help="Existing vanilla Minecraft 1.21.11 jar.")
    parser.add_argument("--java", type=Path, help="Java executable; defaults to PATH.")
    parser.add_argument("--cleanup-plan", action="store_true")
    args = parser.parse_args(argv)

    try:
        if args.run:
            if args.cleanup_plan:
                raise MatrixSafetyError("run_mode_conflicts_with_cleanup_plan")
            if args.game_port != GAME_PORT:
                raise MatrixSafetyError("game_port_not_exact_long_soak_port")
            if args.server_jar is None:
                raise MatrixSafetyError("run_requires_server_jar")
            java = str(args.java.resolve()) if args.java else shutil.which("java")
            if not java:
                raise MatrixSafetyError("java_executable_missing")
            artifact_root = args.artifact_root or (SCRIPT_REPO_ROOT / FIXTURE_RELATIVE)
            payload = run_soak(
                args.repo_root,
                artifact_root,
                args.server_jar,
                java,
            )
        elif args.cleanup_plan:
            payload = {
                "schema": SOAK_SCHEMA,
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
    except (OSError, subprocess.TimeoutExpired):
        error_code = "soak_command_failed"
    except (MatrixSafetyError, ValueError, json.JSONDecodeError) as error:
        error_code = str(error)
    else:
        print(json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True))
        return 0 if payload.get("passed", True) else 1

    print(json.dumps({
        "schema": SOAK_SCHEMA,
        "contentFree": True,
        "liveExecution": False,
        "ok": False,
        "errorCode": error_code,
    }, separators=(",", ":")))
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
