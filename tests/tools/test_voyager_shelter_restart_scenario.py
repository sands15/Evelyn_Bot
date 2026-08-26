from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import patch

from tools.voyager.combat_matrix import (
    BOT_IMAGE,
    BOT_USERNAME,
    PRODUCTION_PORT,
    MatrixSafetyError,
)
from tools.voyager.shelter_restart_scenario import (
    CONTAINER_NAME,
    CONTROLLED_RECOVERY_FOOD,
    CONTROLLED_RECOVERY_FOOD_COUNT,
    GAME_PORT,
    INITIAL_TIME,
    GRACEFUL_STOP_SECONDS,
    MAX_CONSECUTIVE_STALE_SAMPLES,
    MAX_DURATION_SECONDS,
    MIN_SHELTER_DIRT_USED,
    OWNER_VALUE,
    REQUIRED_COMPLETED_CYCLES,
    SCRIPT_REPO_ROOT,
    SHELTER_SUCCESS,
    SPAWN_RADIUS,
    WORLD_SPAWN,
    CycleEvidence,
    ScenarioEvidence,
    _write_report,
    _stop_owned_container,
    _summon_controlled_husk,
    cleanup_plan,
    docker_run_command,
    dry_run_manifest,
    experience_prefix_restored,
    preflight_run,
)


def completed(returncode: int = 0, stdout: str = "") -> SimpleNamespace:
    return SimpleNamespace(returncode=returncode, stdout=stdout, stderr="")


class ShelterRestartScenarioTests(unittest.TestCase):
    def test_controlled_husk_includes_bounded_recovery_food(self) -> None:
        class Server:
            def __init__(self) -> None:
                self.commands: list[str] = []

            def command(self, command: str) -> None:
                self.commands.append(command)

            def query_result(self, command: str) -> int:
                self.commands.append(command)
                return 1

        server = Server()
        _summon_controlled_husk(server)

        self.assertIn(
            f"give {BOT_USERNAME} {CONTROLLED_RECOVERY_FOOD} {CONTROLLED_RECOVERY_FOOD_COUNT}",
            server.commands,
        )

    def test_dry_run_is_exact_bounded_fresh_world_contract(self) -> None:
        payload = dry_run_manifest(SCRIPT_REPO_ROOT, None, GAME_PORT)

        self.assertFalse(payload["liveExecution"])
        self.assertEqual(payload["maxDurationSec"], MAX_DURATION_SECONDS)
        self.assertTrue(payload["world"]["fresh"])
        self.assertEqual(payload["world"]["initialTime"], INITIAL_TIME)
        self.assertEqual(payload["world"]["spawnRadius"], SPAWN_RADIUS)
        self.assertEqual(payload["world"]["worldSpawn"], list(WORLD_SPAWN))
        self.assertEqual(
            payload["world"]["requiredCompletedDayNightCycles"],
            REQUIRED_COMPLETED_CYCLES,
        )
        self.assertEqual(payload["restart"]["count"], 1)
        self.assertEqual(payload["restart"]["signal"], "SIGTERM")
        self.assertEqual(payload["restart"]["graceSec"], GRACEFUL_STOP_SECONDS)
        self.assertEqual(
            payload["restart"]["controlledExperience"]["recoveryFood"],
            "cooked_beef",
        )
        self.assertEqual(
            payload["restart"]["controlledExperience"]["recoveryFoodCount"],
            CONTROLLED_RECOVERY_FOOD_COUNT,
        )
        self.assertEqual(payload["acceptance"]["shelterVerification"], SHELTER_SUCCESS)
        self.assertEqual(payload["acceptance"]["dirtUsedAtLeast"], MIN_SHELTER_DIRT_USED)
        self.assertEqual(payload["cleanup"]["ports"], [GAME_PORT])

    def test_cleanup_plan_rejects_any_alternate_target(self) -> None:
        with self.assertRaisesRegex(MatrixSafetyError, "artifact_root_not_exact"):
            cleanup_plan(SCRIPT_REPO_ROOT, artifact_root=SCRIPT_REPO_ROOT / "other")
        with self.assertRaisesRegex(MatrixSafetyError, "game_port_not_exact"):
            cleanup_plan(SCRIPT_REPO_ROOT, game_port=PRODUCTION_PORT)

    def test_cycle_evidence_requires_two_night_to_day_transitions(self) -> None:
        cycles = CycleEvidence()
        for daytime in (INITIAL_TIME, 12_000, 23_999, 0, 11_999, 12_000, 0):
            cycles.observe(daytime)

        self.assertEqual(cycles.night_entries, 2)
        self.assertEqual(cycles.completed_cycles, 2)
        self.assertEqual(cycles.day_entries, 3)

    def test_shelter_telemetry_latches_failure_code_then_verified_success(self) -> None:
        evidence = ScenarioEvidence(started_epoch=100)
        common = {
            "updated_at": 100,
            "connected": True,
            "connection_state": "connected",
            "health": 20,
            "hunger": 20,
        }
        failed = {
            **common,
            "survival_controller": {
                "last_decision": "shelter_until_safe_dawn",
                "last_success": False,
                "shelter_verification": "shelter_context_unsafe",
            },
        }
        succeeded = {
            **common,
            "survival_controller": {
                "last_decision": "shelter_until_safe_dawn",
                "last_success": True,
                "shelter_verification": SHELTER_SUCCESS,
            },
        }

        self.assertTrue(evidence.observe(
            failed, now_epoch=101, daytime=13_000, death_count=0, dirt_used=0,
        ))
        self.assertTrue(evidence.observe(
            succeeded,
            now_epoch=101,
            daytime=0,
            death_count=0,
            dirt_used=MIN_SHELTER_DIRT_USED,
        ))
        self.assertEqual(evidence.shelter_failure_codes, {"shelter_context_unsafe"})
        self.assertTrue(evidence.shelter_success)
        self.assertEqual(evidence.dirt_used, MIN_SHELTER_DIRT_USED)

    def test_shelter_success_count_survives_later_non_shelter_snapshot(self) -> None:
        evidence = ScenarioEvidence(started_epoch=100)
        common = {
            "updated_at": 100,
            "connected": True,
            "connection_state": "connected",
            "health": 20,
            "hunger": 20,
        }

        evidence.observe(
            {
                **common,
                "survival_controller": {"shelter_success_count": 0},
            },
            now_epoch=101,
            daytime=13_000,
            death_count=0,
            dirt_used=0,
        )
        evidence.observe(
            {
                **common,
                "survival_controller": {
                    "last_decision": "acquire_food",
                    "last_success": False,
                    "shelter_success_count": 1,
                },
            },
            now_epoch=101,
            daytime=0,
            death_count=0,
            dirt_used=MIN_SHELTER_DIRT_USED,
        )

        self.assertTrue(evidence.shelter_success)

    def test_transient_stale_sample_requires_a_bounded_consecutive_failure(self) -> None:
        evidence = ScenarioEvidence(started_epoch=100)
        stale = {
            "updated_at": 90,
            "connected": True,
            "connection_state": "connected",
            "health": 20,
            "hunger": 20,
        }
        fresh = {**stale, "updated_at": 100}

        evidence.observe(stale, now_epoch=100, daytime=9_000, death_count=0, dirt_used=0)
        self.assertEqual(evidence.consecutive_stale_samples, 1)
        evidence.observe(fresh, now_epoch=100, daytime=9_001, death_count=0, dirt_used=0)
        self.assertEqual(evidence.consecutive_stale_samples, 0)
        for offset in range(MAX_CONSECUTIVE_STALE_SAMPLES):
            evidence.observe(
                stale,
                now_epoch=100 + offset,
                daytime=9_002 + offset,
                death_count=0,
                dirt_used=0,
            )
        self.assertEqual(
            evidence.consecutive_stale_samples,
            MAX_CONSECUTIVE_STALE_SAMPLES,
        )

    def test_experience_recovery_requires_exact_prefix_and_new_episode(self) -> None:
        before = [{"outcome": "success", "verified": True, "tactic": "melee"}]
        appended = [*before, {"outcome": "success", "verified": True, "tactic": "melee"}]

        self.assertTrue(experience_prefix_restored(before, appended))
        self.assertFalse(experience_prefix_restored(before, before))
        self.assertFalse(experience_prefix_restored(before, appended[1:]))

    def test_container_command_is_dedicated_and_reuses_only_scenario_runtime(self) -> None:
        artifact = SCRIPT_REPO_ROOT / "runtime_artifacts/validation/shelter_restart_scenario"
        command = docker_run_command(SCRIPT_REPO_ROOT, artifact, "a" * 32)
        rendered = " ".join(command)

        self.assertIn(f"--name {CONTAINER_NAME}", rendered)
        self.assertIn(f"--stop-timeout {GRACEFUL_STOP_SECONDS}", rendered)
        self.assertIn(f"{OWNER_VALUE}", rendered)
        self.assertIn(f"MINEFLAYER_PORT={GAME_PORT}", command)
        self.assertIn("MINDCRAFT_CODEX_ENABLED=false", command)
        self.assertIn("MINDCRAFT_GOAL_MANAGER_MODE=off", command)
        self.assertIn("MINDCRAFT_DETERMINISTIC_TOOL_BOOTSTRAP=false", command)
        self.assertIn("target=/run/evelyn-auth-seed,readonly", rendered)
        self.assertNotIn(str(PRODUCTION_PORT), rendered)

    def test_preflight_rejects_nonfresh_artifact_before_docker_checks(self) -> None:
        calls = []

        def runner(*_args, **_kwargs):
            calls.append(True)
            return completed()

        with self.assertRaisesRegex(MatrixSafetyError, "run_artifact_root_must_not_exist"):
            preflight_run(
                SCRIPT_REPO_ROOT,
                SCRIPT_REPO_ROOT / "runtime_artifacts/validation/shelter_restart_scenario",
                SCRIPT_REPO_ROOT / "missing.jar",
                str(SCRIPT_REPO_ROOT / "missing-java"),
                command_runner=runner,
                artifact_exists=lambda _path: True,
            )
        self.assertEqual(calls, [])

    def test_graceful_stop_checks_identity_exit_zero_then_removes_without_force(self) -> None:
        container_id = "a" * 64
        run_id = "b" * 32
        commands = []

        def runner(command, **_kwargs):
            commands.append(tuple(command))
            if command[:3] == ["docker", "inspect", "--format"]:
                if "State.Running" in command[3]:
                    return completed(stdout="false|0\n")
                return completed(
                    stdout=f"{container_id}|/{CONTAINER_NAME}|{OWNER_VALUE}|{run_id}\n"
                )
            return completed(stdout=f"{container_id}\n")

        _stop_owned_container(container_id, run_id, runner)

        self.assertIn(
            ("docker", "stop", "--time", str(GRACEFUL_STOP_SECONDS), container_id),
            commands,
        )
        self.assertIn(("docker", "rm", container_id), commands)
        self.assertNotIn(("docker", "rm", "--force", container_id), commands)

    def test_graceful_stop_rejects_nonzero_exit(self) -> None:
        container_id = "a" * 64
        run_id = "b" * 32

        def runner(command, **_kwargs):
            if command[:3] == ["docker", "inspect", "--format"]:
                if "State.Running" in command[3]:
                    return completed(stdout="false|137\n")
                return completed(
                    stdout=f"{container_id}|/{CONTAINER_NAME}|{OWNER_VALUE}|{run_id}\n"
                )
            return completed(stdout=f"{container_id}\n")

        with self.assertRaisesRegex(MatrixSafetyError, "graceful_exit_unverified"):
            _stop_owned_container(container_id, run_id, runner)

    def test_report_write_retries_a_transient_windows_sharing_violation(self) -> None:
        original_replace = Path.replace
        attempts = 0

        def flaky_replace(source: Path, target: Path) -> Path:
            nonlocal attempts
            attempts += 1
            if attempts == 1:
                raise PermissionError("sharing violation")
            return original_replace(source, target)

        with TemporaryDirectory() as temporary_directory:
            report_path = Path(temporary_directory) / "report.json"
            with patch.object(Path, "replace", flaky_replace):
                _write_report(report_path, {"contentFree": True})

            self.assertEqual(attempts, 2)
            self.assertEqual(report_path.read_text(encoding="utf-8").strip(), '{\n  "contentFree": true\n}')


if __name__ == "__main__":
    unittest.main()
