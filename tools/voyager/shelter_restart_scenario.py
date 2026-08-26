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
    from .long_survival_soak import (
        NATURAL_GAMERULES,
        _add_scoreboard_objective,
        _wait_for_soak_ready_status,
        verify_natural_server_setup,
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
    from long_survival_soak import (  # type: ignore[no-redef]
        NATURAL_GAMERULES,
        _add_scoreboard_objective,
        _wait_for_soak_ready_status,
        verify_natural_server_setup,
    )


SCHEMA = "evelyn.validation.shelter-restart.v1"
REPORT_SCHEMA = "evelyn.validation.shelter-restart-report.v1"
SCRIPT_REPO_ROOT = Path(__file__).resolve().parents[2]
FIXTURE_RELATIVE = Path("runtime_artifacts/validation/shelter_restart_scenario")
GAME_PORT = 25575
CONTAINER_NAME = "evelyn-shelter-restart"
OWNER_VALUE = "shelter_restart_scenario"
WORLD_SEED = 5_031_407
INITIAL_TIME = 9_000
SPAWN_RADIUS = 0
WORLD_SPAWN = (-104, 87, 8)
MAX_DURATION_SECONDS = 2_700
BOT_READY_TIMEOUT_SECONDS = 45
STATUS_MAX_AGE_SECONDS = 4
MAX_CONSECUTIVE_STALE_SAMPLES = 5
GRACEFUL_STOP_SECONDS = 15
MIN_CONNECTED_FRESH_COVERAGE = 0.95
MIN_SHELTER_DIRT_USED = 18
REQUIRED_COMPLETED_CYCLES = 2
CONTROLLED_TAG = "evelyn_shelter_restart"
CONTROLLED_RECOVERY_FOOD = "minecraft:cooked_beef"
CONTROLLED_RECOVERY_FOOD_COUNT = 2
SHELTER_SUCCESS = "shelter_dawn_exit_verified"


def _number(value: Any) -> float | None:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    result = float(value)
    return result if result == result and abs(result) != float("inf") else None


@dataclass
class CycleEvidence:
    phase: str | None = None
    day_entries: int = 0
    night_entries: int = 0
    completed_cycles: int = 0

    def observe(self, daytime: int) -> None:
        if not isinstance(daytime, int) or isinstance(daytime, bool) or not 0 <= daytime < 24_000:
            return
        current = "night" if daytime >= 12_000 else "day"
        if current == self.phase:
            return
        if current == "day":
            self.day_entries += 1
            if self.phase == "night":
                self.completed_cycles += 1
        else:
            self.night_entries += 1
        self.phase = current


@dataclass
class ScenarioEvidence:
    started_epoch: float
    samples: int = 0
    connected_fresh_samples: int = 0
    min_health: float | None = None
    min_hunger: float | None = None
    final_health: float | None = None
    final_hunger: float | None = None
    death_count: int = 0
    dirt_used: int = 0
    shelter_success: bool = False
    shelter_success_count_baseline: int | None = None
    shelter_failure_codes: set[str] = field(default_factory=set)
    runtime_error_codes: set[str] = field(default_factory=set)
    cycles: CycleEvidence = field(default_factory=CycleEvidence)
    graceful_restart_verified: bool = False
    experience_prefix_count: int = 0
    experience_prefix_restored: bool = False
    post_restart_episode_count: int = 0
    consecutive_stale_samples: int = 0

    def observe(
        self,
        status: Mapping[str, Any] | None,
        *,
        now_epoch: float,
        daytime: int,
        death_count: int,
        dirt_used: int,
    ) -> bool:
        self.samples += 1
        self.cycles.observe(daytime)
        self.death_count = max(self.death_count, max(0, death_count))
        self.dirt_used = max(self.dirt_used, max(0, dirt_used))
        updated = _number(status.get("updated_at")) if status else None
        fresh = updated is not None and -1 <= now_epoch - updated <= STATUS_MAX_AGE_SECONDS
        connected = bool(
            status
            and status.get("connected") is True
            and status.get("connection_state") == "connected"
            and fresh
        )
        if connected:
            self.connected_fresh_samples += 1
            self.consecutive_stale_samples = 0
        else:
            self.consecutive_stale_samples += 1
        health = _number(status.get("health")) if status else None
        hunger = _number(status.get("hunger")) if status else None
        if health is not None:
            self.final_health = health
            self.min_health = health if self.min_health is None else min(self.min_health, health)
        if hunger is not None:
            self.final_hunger = hunger
            self.min_hunger = hunger if self.min_hunger is None else min(self.min_hunger, hunger)
        controller = status.get("survival_controller") if status else None
        if status and status.get("last_error"):
            self.runtime_error_codes.add("mindcraft_runtime_error")
        if isinstance(controller, Mapping):
            shelter_success_count = controller.get("shelter_success_count")
            if (
                isinstance(shelter_success_count, int)
                and not isinstance(shelter_success_count, bool)
                and shelter_success_count >= 0
            ):
                if self.shelter_success_count_baseline is None:
                    self.shelter_success_count_baseline = shelter_success_count
                elif shelter_success_count > self.shelter_success_count_baseline:
                    self.shelter_success = True
            if controller.get("last_error"):
                self.runtime_error_codes.add("survival_action_error")
            if controller.get("last_decision") == "shelter_until_safe_dawn":
                verification = controller.get("shelter_verification")
                if controller.get("last_success") is True and verification == SHELTER_SUCCESS:
                    self.shelter_success = True
                elif controller.get("last_success") is False and isinstance(verification, str):
                    self.shelter_failure_codes.add(verification)
        return connected

    @property
    def connected_fresh_coverage(self) -> float:
        return self.connected_fresh_samples / self.samples if self.samples else 0.0

    @property
    def functional_complete(self) -> bool:
        return bool(
            self.shelter_success
            and self.dirt_used >= MIN_SHELTER_DIRT_USED
            and self.cycles.completed_cycles >= REQUIRED_COMPLETED_CYCLES
            and self.graceful_restart_verified
            and self.experience_prefix_restored
            and self.post_restart_episode_count > self.experience_prefix_count
        )


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
        raise MatrixSafetyError("artifact_root_not_exact_shelter_restart_fixture")
    if game_port != GAME_PORT or game_port == PRODUCTION_PORT:
        raise MatrixSafetyError("game_port_not_exact_shelter_restart_port")
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
    if not Path(java_executable).is_file():
        raise MatrixSafetyError("java_executable_missing")
    if port_probe(PRODUCTION_PORT) or port_probe(GAME_PORT):
        raise MatrixSafetyError("minecraft_validation_or_production_port_in_use")
    if _completed(command_runner, ("docker", "info")).returncode != 0:
        raise MatrixSafetyError("docker_unavailable")
    if _completed(command_runner, ("docker", "image", "inspect", image)).returncode != 0:
        raise MatrixSafetyError("shelter_restart_image_missing")
    if not _production_stopped(command_runner):
        raise MatrixSafetyError("production_mindcraft_must_be_stopped")
    if _completed(command_runner, ("docker", "inspect", CONTAINER_NAME)).returncode == 0:
        raise MatrixSafetyError("shelter_restart_container_name_already_exists")


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
        "motd=Evelyn isolated shelter restart scenario",
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
        creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
    )
    (server_dir.parent / "server.pid").write_text(f"{process.pid}\n", encoding="ascii")
    server = OwnedJavaServer(process)
    try:
        server.wait_for(re.compile(r"Done \(.+\)! For help, type"), 90)
        server.wait_for(re.compile(r"Starting minecraft server version 1\.21\.11"), 1)
        _add_scoreboard_objective(server, "evcm", "dummy")
        _add_scoreboard_objective(server, "evdirt", "minecraft.used:minecraft.dirt")
        _add_scoreboard_objective(server, "evdeath", "minecraft.custom:minecraft.deaths")
        server.command("difficulty normal")
        for gamerule in NATURAL_GAMERULES:
            server.command(f"gamerule {gamerule} true")
        server.command(f"gamerule respawn_radius {SPAWN_RADIUS}")
        server.command(f"setworldspawn {' '.join(map(str, WORLD_SPAWN))}")
        server.command("gamerule keep_inventory false")
        server.command(f"time set {INITIAL_TIME}")
        return server
    except BaseException:
        server.stop()
        raise


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
        "--stop-timeout", str(GRACEFUL_STOP_SECONDS),
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


def _start_bot(
    repo_root: Path,
    artifact_root: Path,
    run_id: str,
    *,
    image: str,
    command_runner: Callable[..., Any],
) -> str:
    result = _completed(
        command_runner,
        docker_run_command(repo_root, artifact_root, run_id, image=image),
        timeout=30,
    )
    container_id = str(result.stdout or "").strip()
    if result.returncode != 0 or not re.fullmatch(r"[0-9a-f]{12,64}", container_id):
        raise MatrixSafetyError("shelter_restart_bot_start_failed")
    return container_id


def _owned_identity(
    container_id: str,
    run_id: str,
    command_runner: Callable[..., Any],
) -> bool:
    identity = _completed(command_runner, (
        "docker", "inspect", "--format",
        f'{{{{.Id}}}}|{{{{.Name}}}}|{{{{index .Config.Labels "{OWNER_LABEL}"}}}}|'
        f'{{{{index .Config.Labels "{RUN_LABEL}"}}}}',
        container_id,
    ))
    return identity.returncode == 0 and str(identity.stdout).strip() == (
        f"{container_id}|/{CONTAINER_NAME}|{OWNER_VALUE}|{run_id}"
    )


def _stop_owned_container(
    container_id: str,
    run_id: str,
    command_runner: Callable[..., Any],
) -> None:
    if not _owned_identity(container_id, run_id, command_runner):
        raise MatrixSafetyError("shelter_restart_container_ownership_lost")
    stopped = _completed(
        command_runner,
        ("docker", "stop", "--time", str(GRACEFUL_STOP_SECONDS), container_id),
        timeout=GRACEFUL_STOP_SECONDS + 7,
    )
    if stopped.returncode != 0:
        raise MatrixSafetyError("shelter_restart_graceful_stop_failed")
    state = _completed(
        command_runner,
        ("docker", "inspect", "--format", "{{.State.Running}}|{{.State.ExitCode}}", container_id),
    )
    if state.returncode != 0 or str(state.stdout).strip() != "false|0":
        raise MatrixSafetyError("shelter_restart_graceful_exit_unverified")
    removed = _completed(command_runner, ("docker", "rm", container_id), timeout=10)
    if removed.returncode != 0:
        raise MatrixSafetyError("shelter_restart_container_remove_failed")


def _force_remove_owned_container(
    container_id: str,
    run_id: str,
    command_runner: Callable[..., Any],
) -> None:
    if not _owned_identity(container_id, run_id, command_runner):
        raise MatrixSafetyError("shelter_restart_container_ownership_lost")
    removed = _completed(command_runner, ("docker", "rm", "--force", container_id), timeout=20)
    if removed.returncode != 0:
        raise MatrixSafetyError("shelter_restart_container_cleanup_failed")


def _wait_ready(
    artifact_root: Path,
    container_id: str,
    *,
    command_runner: Callable[..., Any],
    epoch: Callable[[], float],
) -> Mapping[str, Any]:
    ready = _wait_for_soak_ready_status(
        artifact_root / "bot/mindcraft/status.json",
        container_id,
        time.monotonic() + BOT_READY_TIMEOUT_SECONDS,
        command_runner=command_runner,
        monotonic=time.monotonic,
        epoch=epoch,
        sleeper=time.sleep,
    )
    if ready is None:
        raise MatrixSafetyError("shelter_restart_bot_ready_timeout")
    contract = ready.get("task_contract")
    if not isinstance(contract, Mapping) or contract.get("goal_manager_mode") != "off":
        raise MatrixSafetyError("shelter_restart_goal_manager_off_unverified")
    return ready


def _terminal_success_count(episodes: Sequence[Mapping[str, Any]]) -> int:
    return sum(
        1 for episode in episodes
        if episode.get("outcome") == "success" and episode.get("verified") is True
    )


def experience_prefix_restored(
    before: Sequence[Mapping[str, Any]],
    after: Sequence[Mapping[str, Any]],
) -> bool:
    return len(after) > len(before) and list(after[:len(before)]) == list(before)


def _summon_controlled_husk(server: OwnedJavaServer) -> None:
    server.command(f"give {BOT_USERNAME} minecraft:iron_sword 1")
    server.command(
        f"give {BOT_USERNAME} {CONTROLLED_RECOVERY_FOOD} {CONTROLLED_RECOVERY_FOOD_COUNT}"
    )
    server.command(
        f'execute at {BOT_USERNAME} run summon minecraft:husk ~4 ~ ~ '
        f'{{Tags:["{CONTROLLED_TAG}"],PersistenceRequired:1b}}'
    )
    if server.query_result(f"if entity @e[tag={CONTROLLED_TAG}]") < 1:
        raise MatrixSafetyError("shelter_restart_controlled_hostile_unverified")


def _sample(
    server: OwnedJavaServer,
    artifact_root: Path,
    evidence: ScenarioEvidence,
    *,
    epoch: Callable[[], float],
) -> bool:
    return evidence.observe(
        _read_json(artifact_root / "bot/mindcraft/status.json"),
        now_epoch=epoch(),
        daytime=server.query_result("run time query daytime"),
        death_count=max(0, server.query_result(f"run scoreboard players get {BOT_USERNAME} evdeath")),
        dirt_used=max(0, server.query_result(f"run scoreboard players get {BOT_USERNAME} evdirt")),
    )


def build_report(
    evidence: ScenarioEvidence,
    *,
    elapsed_seconds: float,
    natural_setup_verified: bool,
    cleanup_verified: bool,
    abort_reason: str | None,
    live_execution: bool,
) -> dict[str, Any]:
    gates = {
        "natural_setup_verified": natural_setup_verified,
        "shelter_completed": evidence.shelter_success,
        "shelter_blocks_used": evidence.dirt_used >= MIN_SHELTER_DIRT_USED,
        "multiple_day_night_cycles": evidence.cycles.completed_cycles >= REQUIRED_COMPLETED_CYCLES,
        "graceful_restart_verified": evidence.graceful_restart_verified,
        "experience_prefix_restored": evidence.experience_prefix_restored,
        "post_restart_experience_appended": (
            evidence.post_restart_episode_count > evidence.experience_prefix_count
        ),
        "connected_fresh_coverage": (
            evidence.connected_fresh_coverage >= MIN_CONNECTED_FRESH_COVERAGE
        ),
        "death_free": evidence.death_count == 0,
        "runtime_error_free": not evidence.runtime_error_codes,
        "final_health_safe": evidence.final_health is not None and evidence.final_health > 10,
        "final_hunger_safe": evidence.final_hunger is not None and evidence.final_hunger > 6,
        "completed_within_bound": abort_reason is None and elapsed_seconds <= MAX_DURATION_SECONDS,
        "cleanup_verified": cleanup_verified,
    }
    return {
        "schema": REPORT_SCHEMA,
        "contentFree": True,
        "liveExecution": live_execution,
        "elapsedSec": round(elapsed_seconds, 1),
        "maxDurationSec": MAX_DURATION_SECONDS,
        "abortReason": abort_reason,
        "world": {
            "fresh": True,
            "seed": WORLD_SEED,
            "port": GAME_PORT,
            "initialTime": INITIAL_TIME,
            "naturalSetupVerified": natural_setup_verified,
            "dayEntries": evidence.cycles.day_entries,
            "nightEntries": evidence.cycles.night_entries,
            "completedCycles": evidence.cycles.completed_cycles,
        },
        "shelter": {
            "completed": evidence.shelter_success,
            "dirtUsed": evidence.dirt_used,
            "failureCodes": sorted(evidence.shelter_failure_codes),
        },
        "restart": {
            "graceful": evidence.graceful_restart_verified,
            "experiencePrefixCount": evidence.experience_prefix_count,
            "experiencePrefixRestored": evidence.experience_prefix_restored,
            "postRestartEpisodeCount": evidence.post_restart_episode_count,
        },
        "survival": {
            "samples": evidence.samples,
            "connectedFreshSamples": evidence.connected_fresh_samples,
            "connectedFreshCoverage": round(evidence.connected_fresh_coverage, 4),
            "deathCount": evidence.death_count,
            "minHealth": evidence.min_health,
            "minHunger": evidence.min_hunger,
            "finalHealth": evidence.final_health,
            "finalHunger": evidence.final_hunger,
        },
        "runtimeErrors": sorted(evidence.runtime_error_codes),
        "acceptance": gates,
        "cleanupVerified": cleanup_verified,
        "passed": all(gates.values()),
    }


def _write_report(path: Path, report: Mapping[str, Any]) -> None:
    temporary = path.with_suffix(".tmp")
    payload = json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True)
    for attempt in range(3):
        try:
            temporary.write_text(payload, encoding="utf-8")
            temporary.replace(path)
            return
        except PermissionError:
            if attempt == 2:
                raise
            time.sleep(0.05)


def run_scenario(
    repo_root: Path,
    artifact_root: Path,
    server_jar: Path,
    java_executable: str,
    *,
    image: str = BOT_IMAGE,
    command_runner: Callable[..., Any] = subprocess.run,
    port_probe: Callable[[int], bool] = _port_in_use,
    epoch: Callable[[], float] = time.time,
) -> dict[str, Any]:
    preflight_run(
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
        "schema": SCHEMA,
        "owner": OWNER_VALUE,
        "runId": run_id,
        "contentFree": True,
    }, separators=(",", ":")), encoding="utf-8")
    (artifact_root / "bot/mindcraft").mkdir(parents=True)
    _prepare_server_directory(artifact_root / "server")

    evidence = ScenarioEvidence(started_epoch=epoch())
    server: OwnedJavaServer | None = None
    container_id: str | None = None
    natural_setup_verified = False
    cleanup_verified = False
    abort_reason: str | None = None
    restarted = False
    pre_restart_spawned = False
    post_restart_spawned = False
    durable_prefix: list[Mapping[str, Any]] = []
    started = time.monotonic()
    deadline = started + MAX_DURATION_SECONDS
    history_path = artifact_root / "bot/mindcraft/combat_history.json"
    try:
        server = start_server(artifact_root / "server", server_jar, java_executable)
        natural_setup_verified = bool(
            verify_natural_server_setup(server, artifact_root / "server")
            and server.query_result("run gamerule respawn_radius") == SPAWN_RADIUS
        )
        if not natural_setup_verified:
            raise MatrixSafetyError("shelter_restart_natural_setup_unverified")
        container_id = _start_bot(
            repo_root, artifact_root, run_id, image=image, command_runner=command_runner,
        )
        _wait_ready(artifact_root, container_id, command_runner=command_runner, epoch=epoch)

        while time.monotonic() < deadline and not evidence.functional_complete:
            time.sleep(1)
            if server.process.poll() is not None or not port_probe(GAME_PORT):
                raise MatrixSafetyError("shelter_restart_server_exit")
            if port_probe(PRODUCTION_PORT) or not _production_stopped(command_runner):
                raise MatrixSafetyError("production_mindcraft_started_during_scenario")
            if container_id is None or not _container_running(container_id, command_runner):
                raise MatrixSafetyError("shelter_restart_container_exit")
            connected = _sample(server, artifact_root, evidence, epoch=epoch)
            if evidence.consecutive_stale_samples >= MAX_CONSECUTIVE_STALE_SAMPLES:
                raise MatrixSafetyError("shelter_restart_telemetry_stale")
            if evidence.death_count:
                raise MatrixSafetyError("shelter_restart_death_observed")
            if evidence.runtime_error_codes:
                raise MatrixSafetyError("shelter_restart_runtime_error_observed")

            episodes = _combat_episodes(history_path)
            if evidence.shelter_success and not restarted:
                if _terminal_success_count(episodes) == 0 and not pre_restart_spawned:
                    _summon_controlled_husk(server)
                    pre_restart_spawned = True
                elif _terminal_success_count(episodes) > 0:
                    server.command(f"kill @e[tag={CONTROLLED_TAG}]")
                    _stop_owned_container(container_id, run_id, command_runner)
                    container_id = None
                    durable_prefix = _combat_episodes(history_path)
                    if _terminal_success_count(durable_prefix) == 0:
                        raise MatrixSafetyError("shelter_restart_pre_restart_experience_missing")
                    evidence.experience_prefix_count = len(durable_prefix)
                    evidence.graceful_restart_verified = True
                    container_id = _start_bot(
                        repo_root,
                        artifact_root,
                        run_id,
                        image=image,
                        command_runner=command_runner,
                    )
                    _wait_ready(
                        artifact_root, container_id, command_runner=command_runner, epoch=epoch,
                    )
                    restarted = True

            if restarted:
                episodes = _combat_episodes(history_path)
                prefix_intact = list(episodes[:len(durable_prefix)]) == durable_prefix
                if len(episodes) < len(durable_prefix) or not prefix_intact:
                    raise MatrixSafetyError("shelter_restart_experience_prefix_lost")
                if not post_restart_spawned:
                    _summon_controlled_husk(server)
                    post_restart_spawned = True
                if (
                    experience_prefix_restored(durable_prefix, episodes)
                    and _terminal_success_count(episodes[len(durable_prefix):]) > 0
                ):
                    evidence.experience_prefix_restored = True
                    evidence.post_restart_episode_count = len(episodes)
                    server.command(f"kill @e[tag={CONTROLLED_TAG}]")

            _write_report(artifact_root / "monitor_status.json", build_report(
                evidence,
                elapsed_seconds=time.monotonic() - started,
                natural_setup_verified=natural_setup_verified,
                cleanup_verified=False,
                abort_reason=None,
                live_execution=True,
            ))

        if not evidence.functional_complete:
            raise MatrixSafetyError("shelter_restart_scenario_timeout")
    except (MatrixSafetyError, OSError, ValueError, subprocess.TimeoutExpired) as error:
        candidate = str(error)
        abort_reason = candidate if re.fullmatch(r"[a-z0-9_]+", candidate) else "scenario_infrastructure_error"
    finally:
        if server is not None and server.process.poll() is None:
            try:
                server.command(f"kill @e[tag={CONTROLLED_TAG}]")
            except (MatrixSafetyError, OSError):
                pass
        if container_id is not None:
            try:
                _stop_owned_container(container_id, run_id, command_runner)
                container_id = None
            except (MatrixSafetyError, OSError, subprocess.TimeoutExpired):
                try:
                    _force_remove_owned_container(container_id, run_id, command_runner)
                    container_id = None
                except (MatrixSafetyError, OSError, subprocess.TimeoutExpired):
                    abort_reason = abort_reason or "shelter_restart_cleanup_failed"
        if server is not None:
            try:
                server.stop()
            except (MatrixSafetyError, OSError, subprocess.TimeoutExpired):
                abort_reason = abort_reason or "shelter_restart_cleanup_failed"
        cleanup_verified = bool(
            (server is None or server.process.poll() is not None)
            and not port_probe(GAME_PORT)
            and not port_probe(PRODUCTION_PORT)
            and _production_stopped(command_runner)
            and _completed(command_runner, ("docker", "inspect", CONTAINER_NAME)).returncode != 0
        )
        if not cleanup_verified:
            abort_reason = abort_reason or "shelter_restart_cleanup_failed"

    report = build_report(
        evidence,
        elapsed_seconds=time.monotonic() - started,
        natural_setup_verified=natural_setup_verified,
        cleanup_verified=cleanup_verified,
        abort_reason=abort_reason,
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
        "schema": SCHEMA,
        "contentFree": True,
        "liveExecution": False,
        "mode": "dry_run",
        "maxDurationSec": MAX_DURATION_SECONDS,
        "world": {
            "fresh": True,
            "seed": WORLD_SEED,
            "port": GAME_PORT,
            "spawnRadius": SPAWN_RADIUS,
            "worldSpawn": list(WORLD_SPAWN),
            "difficulty": "normal",
            "initialTime": INITIAL_TIME,
            "naturalDaylight": True,
            "naturalWeather": True,
            "naturalMobSpawning": True,
            "requiredCompletedDayNightCycles": REQUIRED_COMPLETED_CYCLES,
        },
        "restart": {
            "count": 1,
            "signal": "SIGTERM",
            "graceSec": GRACEFUL_STOP_SECONDS,
            "requireExitCode": 0,
            "requireExperiencePrefixAndAppend": True,
            "controlledExperience": {
                "entity": "husk",
                "weapon": "iron_sword",
                "recoveryFood": CONTROLLED_RECOVERY_FOOD.removeprefix("minecraft:"),
                "recoveryFoodCount": CONTROLLED_RECOVERY_FOOD_COUNT,
            },
        },
        "runtime": {
            "freshArtifact": True,
            "authMountReadOnly": True,
            "authWorkingCopyEphemeral": True,
            "goalManagerMode": "off",
            "productionContainerMustRemainStopped": PRODUCTION_CONTAINER,
            "productionPortMustRemainClosed": PRODUCTION_PORT,
        },
        "acceptance": {
            "shelterVerification": SHELTER_SUCCESS,
            "dirtUsedAtLeast": MIN_SHELTER_DIRT_USED,
            "completedDayNightCyclesAtLeast": REQUIRED_COMPLETED_CYCLES,
            "gracefulRestart": True,
            "experiencePrefixRestoredAndAppended": True,
            "deathCount": 0,
            "runtimeErrorCount": 0,
            "cleanupVerified": True,
        },
        "cleanup": cleanup_plan(
            repo_root, artifact_root=artifact_root, game_port=game_port,
        ),
    }


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Run or inspect Evelyn's bounded isolated shelter/restart scenario.",
    )
    parser.add_argument("--repo-root", type=Path, default=SCRIPT_REPO_ROOT)
    parser.add_argument("--artifact-root", type=Path)
    parser.add_argument("--game-port", type=int, default=GAME_PORT)
    parser.add_argument("--run", action="store_true")
    parser.add_argument("--server-jar", type=Path)
    parser.add_argument("--java", type=Path)
    parser.add_argument("--cleanup-plan", action="store_true")
    args = parser.parse_args(argv)
    try:
        if args.run:
            if args.cleanup_plan:
                raise MatrixSafetyError("run_mode_conflicts_with_cleanup_plan")
            if args.game_port != GAME_PORT:
                raise MatrixSafetyError("game_port_not_exact_shelter_restart_port")
            if args.server_jar is None:
                raise MatrixSafetyError("run_requires_server_jar")
            java = str(args.java.resolve()) if args.java else shutil.which("java")
            if not java:
                raise MatrixSafetyError("java_executable_missing")
            payload = run_scenario(
                args.repo_root,
                args.artifact_root or (SCRIPT_REPO_ROOT / FIXTURE_RELATIVE),
                args.server_jar,
                java,
            )
        elif args.cleanup_plan:
            payload = {
                "schema": SCHEMA,
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
        error_code = "shelter_restart_command_failed"
    except (MatrixSafetyError, ValueError, json.JSONDecodeError) as error:
        error_code = str(error)
    else:
        print(json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True))
        return 0 if payload.get("passed", True) else 1
    print(json.dumps({
        "schema": SCHEMA,
        "contentFree": True,
        "liveExecution": False,
        "error": error_code,
    }, ensure_ascii=False, sort_keys=True))
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
