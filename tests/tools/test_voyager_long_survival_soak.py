from __future__ import annotations

import json
import subprocess
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace

from tools.voyager.long_survival_soak import (
    AUTONOMOUS_PROGRESS_TIMEOUT_SECONDS,
    CONTAINER_NAME,
    DURATION_SECONDS,
    FIXTURE_RELATIVE,
    GAME_PORT,
    LOG_PROGRESS_OBJECTIVES,
    OWNER_VALUE,
    PRODUCTION_CONTAINER,
    PRODUCTION_PORT,
    SCRIPT_REPO_ROOT,
    SoakEvidence,
    _add_scoreboard_objective,
    _remove_owned_container,
    _start_bot_container,
    _wait_for_soak_ready_status,
    _world_progress_stats,
    build_report,
    cleanup_plan,
    docker_run_command,
    dry_run_manifest,
    monitor_soak,
    preflight_run,
    run_soak,
    verify_natural_server_setup,
    MatrixSafetyError,
)


def _result(returncode=0, stdout="", stderr=""):
    return SimpleNamespace(returncode=returncode, stdout=stdout, stderr=stderr)


def _passing_status(
    updated_at: float,
    *,
    health: float = 20,
    hunger: float = 20,
    inventory=None,
    controller=None,
    navigation=None,
):
    return {
        "running": True,
        "connected": True,
        "connection_state": "connected",
        "updated_at": updated_at,
        "health": health,
        "hunger": hunger,
        "last_error": None,
        "task_contract": {"goal_manager_mode": "off"},
        "inventory": inventory or {},
        "navigation": navigation or {
            "path_updates": 1,
            "nonempty_path_updates": 1,
            "goal_reached": 1,
            "verified_goal_reached": 1,
            "partial_updates": 0,
            "timeout_updates": 0,
            "no_path_updates": 0,
            "stuck_resets": 0,
            "active": False,
            "content_free": True,
        },
        "survival_controller": controller or {
            "phase": "planner_control",
            "last_decision": None,
            "last_success": None,
            "last_error": None,
        },
        "goal_manager": {
            "death_count": 0,
        },
    }


class LongSurvivalSoakTests(unittest.TestCase):
    def test_ready_barrier_uses_two_fresh_status_updates_during_long_action(self) -> None:
        clock = [0.0]
        write_stage = [0]
        with tempfile.TemporaryDirectory() as directory:
            status_path = Path(directory) / "status.json"

            def write_status(updated_at):
                status_path.write_text(json.dumps({
                    "running": True,
                    "connected": True,
                    "connection_state": "connected",
                    "updated_at": updated_at,
                    "survival_controller": {
                        "phase": "bootstrap_tools",
                        "updated_at": 900.0,
                    },
                }), encoding="utf-8")

            write_status(900.0)

            def sleeper(seconds):
                clock[0] += seconds
                if clock[0] >= 0.4 and write_stage[0] == 1:
                    write_status(1_000.4)
                    write_stage[0] = 2
                elif clock[0] >= 0.2 and write_stage[0] == 0:
                    write_status(1_000.2)
                    write_stage[0] = 1

            def runner(command, **_kwargs):
                if command[-1] == "container123":
                    return _result(stdout="true\n")
                return _result(returncode=1)

            ready = _wait_for_soak_ready_status(
                status_path,
                "container123",
                1.0,
                command_runner=runner,
                monotonic=lambda: clock[0],
                epoch=lambda: 1_000 + clock[0],
                sleeper=sleeper,
            )

        self.assertEqual(ready["updated_at"], 1_000.4)
        self.assertEqual(ready["survival_controller"]["updated_at"], 900.0)
        self.assertGreaterEqual(clock[0], 0.4)

    def test_log_stat_criteria_use_java_scoreboard_resource_location_syntax(self) -> None:
        for _objective, criterion in LOG_PROGRESS_OBJECTIVES:
            self.assertRegex(criterion, r"^minecraft\.mined:minecraft\.[a-z0-9_]+_log$")
            self.assertNotIn("minecraft:", criterion.split(":", 1)[1])

    def test_dry_run_contract_is_exact_natural_twenty_minutes(self) -> None:
        payload = dry_run_manifest(SCRIPT_REPO_ROOT, None, GAME_PORT)
        self.assertFalse(payload["liveExecution"])
        self.assertEqual(payload["durationSec"], 1_200)
        self.assertEqual(payload["cleanup"]["ports"], [GAME_PORT])
        self.assertTrue(payload["world"]["fresh"])
        self.assertEqual(payload["world"]["difficulty"], "normal")
        self.assertTrue(payload["world"]["naturalDaylight"])
        self.assertTrue(payload["world"]["naturalWeather"])
        self.assertTrue(payload["world"]["naturalMobSpawning"])
        self.assertTrue(payload["world"]["naturalRegeneration"])
        self.assertTrue(payload["runtime"]["authMountReadOnly"])
        self.assertTrue(payload["runtime"]["authWorkingCopyEphemeral"])
        self.assertEqual(payload["runtime"]["goalManagerMode"], "off")
        self.assertTrue(payload["runtime"]["deterministicToolBootstrap"])
        self.assertEqual(payload["acceptance"]["navigationVerifiedGoalReachedGreaterThan"], 0)

    def test_cleanup_plan_rejects_alternate_root_and_port(self) -> None:
        with self.assertRaisesRegex(MatrixSafetyError, "artifact_root_not_exact"):
            cleanup_plan(SCRIPT_REPO_ROOT, artifact_root=SCRIPT_REPO_ROOT / "other")
        with self.assertRaisesRegex(MatrixSafetyError, "game_port_not_exact"):
            cleanup_plan(SCRIPT_REPO_ROOT, game_port=PRODUCTION_PORT)
        plan = cleanup_plan(SCRIPT_REPO_ROOT)
        self.assertEqual(plan["container_names"], [CONTAINER_NAME])
        self.assertEqual(plan["artifact_roots"], [FIXTURE_RELATIVE.as_posix()])
        self.assertFalse(plan["wildcards"])

    def test_preflight_refuses_nonfresh_artifact_before_starting_services(self) -> None:
        expected = SCRIPT_REPO_ROOT / FIXTURE_RELATIVE
        with self.assertRaisesRegex(MatrixSafetyError, "root_must_not_exist"):
            preflight_run(
                SCRIPT_REPO_ROOT,
                expected,
                Path("missing.jar"),
                "missing-java",
                artifact_exists=lambda _path: True,
            )

    def test_container_command_has_exact_identity_port_and_read_only_auth(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            artifact = Path(directory)
            command = docker_run_command(SCRIPT_REPO_ROOT, artifact, "run123")
        joined = "\n".join(command)
        self.assertIn(f"--name\n{CONTAINER_NAME}", joined)
        self.assertIn(f"evelyn.validation.owner={OWNER_VALUE}", joined)
        self.assertIn(f"MINEFLAYER_PORT={GAME_PORT}", command)
        self.assertIn("target=/run/evelyn-auth-seed,readonly", joined)
        self.assertIn("/app/bot_profiles:rw,noexec,nosuid,nodev,mode=0700", command)
        self.assertIn("cp -R /run/evelyn-auth-seed/. /app/bot_profiles/", command[-1])
        self.assertIn("exec node main.js", command[-1])
        self.assertNotIn(PRODUCTION_CONTAINER, command)
        self.assertIn("MINDCRAFT_GOAL_MANAGER_MODE=off", command)
        self.assertIn("MINDCRAFT_GOAL=", command)
        self.assertIn("MINDCRAFT_DETERMINISTIC_TOOL_BOOTSTRAP=true", command)
        settings = json.loads(next(
            item.removeprefix("SETTINGS_JSON=")
            for item in command if item.startswith("SETTINGS_JSON=")
        ))
        self.assertEqual(settings["port"], GAME_PORT)
        self.assertFalse(settings["load_memory"])
        self.assertIsNone(settings["init_message"])

    def test_failed_detached_start_cleans_only_exact_owned_container(self) -> None:
        container_id = "a" * 64

        for failure in ("invalid", "timeout"):
            removed = []

            def runner(command, **_kwargs):
                if command[1] == "run":
                    if failure == "timeout":
                        raise subprocess.TimeoutExpired(command, 30)
                    return _result(returncode=1)
                if command[1:3] == ["inspect", "--format"]:
                    return _result(stdout=(
                        f"{container_id}|/{CONTAINER_NAME}|{OWNER_VALUE}|run123\n"
                    ))
                if command[1:3] == ["rm", "--force"]:
                    removed.append(command[-1])
                    return _result()
                return _result(returncode=1)

            with tempfile.TemporaryDirectory() as directory:
                expected = subprocess.TimeoutExpired if failure == "timeout" else MatrixSafetyError
                with self.assertRaises(expected):
                    _start_bot_container(
                        SCRIPT_REPO_ROOT,
                        Path(directory),
                        "run123",
                        image="fixture-image",
                        command_runner=runner,
                    )
            self.assertEqual(removed, [container_id])

    def test_natural_server_verifier_checks_rules_difficulty_world_and_time(self) -> None:
        class Server:
            def __init__(self):
                self.times = iter((6_000, 6_012))
                self.commands = []

            def query_result(self, tail):
                if tail == "run gamerule keep_inventory":
                    return 0
                if tail.startswith("run gamerule "):
                    return 1
                return next(self.times)

            def _cursor(self):
                return 3

            def command(self, command):
                self.commands.append(command)

            def wait_for(self, pattern, timeout, after=0):
                self.pattern = pattern
                self.after = after
                return pattern.search("The difficulty is normal")

        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "world").mkdir()
            server = Server()
            self.assertTrue(verify_natural_server_setup(server, root, sleeper=lambda _s: None))
        self.assertEqual(server.commands, ["difficulty"])
        self.assertEqual(server.after, 3)

    def test_world_progress_uses_server_stats_for_walk_logs_craft_and_dirt(self) -> None:
        values = {
            "evwalk": 321,
            "evsprint": 462,
            "evtool": 1,
            "evdirt": 16,
            "evlog0": 2,
            "evlog2": 1,
        }

        class Server:
            def query_result(self, tail):
                return values.get(tail.rsplit(" ", 1)[-1], 0)

        self.assertEqual(_world_progress_stats(Server()), {
            "walked_cm": 321,
            "sprinted_cm": 462,
            "wooden_pickaxes_crafted": 1,
            "dirt_used": 16,
            "logs_mined": 3,
        })

    def test_sprint_distance_counts_as_real_autonomous_movement(self) -> None:
        evidence = SoakEvidence(
            started_epoch=100,
            sprinted_cm=462,
            logs_mined=3,
            wooden_pickaxes_crafted=1,
            pickaxe_inventory_observed=True,
        )
        self.assertTrue(evidence.autonomous_world_progress_observed)

    def test_progress_objective_add_requires_exact_success_barrier(self) -> None:
        class Server:
            command_text = None

            @staticmethod
            def _cursor():
                return 7

            @classmethod
            def command(cls, command):
                cls.command_text = command

            @classmethod
            def wait_for(cls, pattern, timeout, after=0):
                line = (
                    "Created new objective [evwalk]"
                    if cls.command_text.endswith("minecraft.custom:minecraft.walk_one_cm")
                    else "Unknown scoreboard criterion"
                )
                match = pattern.search(line)
                if match is None:
                    raise MatrixSafetyError("validation_server_console_timeout")
                self.assertEqual(timeout, 3)
                self.assertEqual(after, 7)
                return match

        _add_scoreboard_objective(
            Server(), "evwalk", "minecraft.custom:minecraft.walk_one_cm",
        )
        with self.assertRaisesRegex(MatrixSafetyError, "console_timeout"):
            _add_scoreboard_objective(Server(), "evbad", "minecraft.invalid:criterion")

    def test_survival_action_latches_distinguish_shelter_entry_and_terminal_outcome(self) -> None:
        evidence = SoakEvidence(started_epoch=100)
        controllers = (
            ("shelter_until_safe_dawn", None, None),
            ("shelter_until_safe_dawn", None, None),
            ("reassess", "shelter_until_safe_dawn", True),
            ("planner_control", "shelter_until_safe_dawn", True),
            ("shelter_until_safe_dawn", "shelter_until_safe_dawn", None),
            ("planner_control", "shelter_until_safe_dawn", False),
        )
        for index, (phase, decision, success) in enumerate(controllers, start=1):
            evidence.observe(
                _passing_status(100 + index, controller={
                    "phase": phase,
                    "last_decision": decision,
                    "last_success": success,
                    "last_error": None,
                }),
                [],
                now_epoch=100 + index,
                now_monotonic=index,
            )

        self.assertEqual(evidence.action_entries["shelter_until_safe_dawn"], 2)
        self.assertEqual(evidence.action_successes["shelter_until_safe_dawn"], 1)
        self.assertEqual(evidence.action_failures["shelter_until_safe_dawn"], 1)

    def test_goal_manager_metadata_cannot_claim_autonomous_world_progress(self) -> None:
        status = _passing_status(101)
        status["goal_manager"].update({
            "completed_count": 9,
            "last_progress_at": 101,
            "last_execution": {"goalProgress": True},
        })
        evidence = SoakEvidence(started_epoch=100)
        evidence.observe(status, [], now_epoch=101, now_monotonic=1)
        report = build_report(
            evidence,
            elapsed_seconds=DURATION_SECONDS,
            natural_setup_verified=True,
            cleanup_verified=True,
            live_execution=True,
        )
        self.assertFalse(report["acceptance"]["autonomous_world_progress"])
        self.assertNotIn("goalProgress", report)
        self.assertFalse(report["passed"])

    def test_acceptance_passes_only_after_critical_recovers_and_world_progresses(self) -> None:
        evidence = SoakEvidence(started_epoch=100)
        evidence.observe(
            _passing_status(101, health=10, hunger=7, controller={
                "phase": "bootstrap_tools",
                "last_decision": None,
                "last_success": None,
                "last_error": None,
            }),
            [],
            now_epoch=101,
            now_monotonic=1,
            world_progress={"walked_cm": 200, "logs_mined": 2},
        )
        evidence.observe(
            _passing_status(
                103,
                health=12,
                hunger=7,
                inventory={"wooden_pickaxe": 1},
                controller={
                    "phase": "reassess",
                    "last_decision": "bootstrap_tools",
                    "last_success": True,
                    "last_error": None,
                },
            ),
            [],
            now_epoch=103,
            now_monotonic=3,
            world_progress={
                "walked_cm": 250,
                "logs_mined": 3,
                "wooden_pickaxes_crafted": 1,
            },
        )
        report = build_report(
            evidence,
            elapsed_seconds=DURATION_SECONDS,
            natural_setup_verified=True,
            cleanup_verified=True,
            live_execution=True,
        )
        self.assertTrue(report["passed"])
        self.assertEqual(report["critical"]["episodes"], 1)
        self.assertEqual(report["critical"]["resolved"], 1)
        self.assertEqual(report["minHealth"], 10)
        self.assertTrue(report["autonomousWorldProgress"]["observed"])
        self.assertNotIn("goalProgress", report)
        evidence.abort_reason = "autonomous_progress_timeout"
        aborted = build_report(
            evidence,
            elapsed_seconds=DURATION_SECONDS,
            natural_setup_verified=True,
            cleanup_verified=True,
            live_execution=True,
        )
        self.assertFalse(aborted["acceptance"]["run_completed_without_abort"])
        self.assertFalse(aborted["passed"])

    def test_survival_progress_without_a_reached_path_cannot_pass(self) -> None:
        evidence = SoakEvidence(started_epoch=100)
        evidence.observe(
            _passing_status(
                101,
                inventory={"wooden_pickaxe": 1},
                controller={
                    "phase": "reassess",
                    "last_decision": "bootstrap_tools",
                    "last_success": True,
                    "last_error": None,
                },
                navigation={
                    "path_updates": 4,
                    "nonempty_path_updates": 2,
                    "goal_reached": 4,
                    "verified_goal_reached": 0,
                    "partial_updates": 2,
                    "timeout_updates": 1,
                    "no_path_updates": 1,
                    "stuck_resets": 1,
                    "active": True,
                    "content_free": True,
                },
            ),
            [],
            now_epoch=101,
            now_monotonic=1,
            world_progress={
                "walked_cm": 250,
                "logs_mined": 3,
                "wooden_pickaxes_crafted": 1,
            },
        )
        report = build_report(
            evidence,
            elapsed_seconds=DURATION_SECONDS,
            natural_setup_verified=True,
            cleanup_verified=True,
            live_execution=True,
        )

        self.assertFalse(report["acceptance"]["navigation_verified_goal_reached"])
        self.assertFalse(report["passed"])
        self.assertTrue(report["navigation"]["goalReachedObserved"])
        self.assertEqual(report["navigation"]["partialUpdates"], 2)
        self.assertEqual(report["navigation"]["timeoutUpdates"], 1)
        self.assertEqual(report["navigation"]["stuckResets"], 1)

    def test_invalid_telemetry_cannot_claim_critical_recovery(self) -> None:
        evidence = SoakEvidence(started_epoch=100)
        evidence.observe(
            _passing_status(101, health=10, hunger=20),
            [],
            now_epoch=101,
            now_monotonic=1,
        )
        evidence.observe(None, [], now_epoch=102, now_monotonic=2)
        report = build_report(
            evidence,
            elapsed_seconds=DURATION_SECONDS,
            natural_setup_verified=True,
            cleanup_verified=True,
            live_execution=True,
        )
        self.assertTrue(report["critical"]["active"])
        self.assertFalse(report["acceptance"]["critical_resolved"])
        self.assertFalse(report["passed"])

    def test_respawn_does_not_count_death_terminated_critical_as_resolved(self) -> None:
        evidence = SoakEvidence(started_epoch=100)
        evidence.observe(
            _passing_status(101, health=10, hunger=7),
            [],
            now_epoch=101,
            now_monotonic=1,
        )
        death = _passing_status(102, health=0, hunger=7)
        death["phase"] = "respawning"
        evidence.observe(death, [], now_epoch=102, now_monotonic=2)
        evidence.observe(
            _passing_status(103, health=20, hunger=20),
            [],
            now_epoch=103,
            now_monotonic=3,
        )

        report = build_report(
            evidence,
            elapsed_seconds=DURATION_SECONDS,
            natural_setup_verified=True,
            cleanup_verified=True,
            live_execution=True,
        )

        self.assertEqual(report["deathCount"], 1)
        self.assertEqual(report["critical"]["episodes"], 1)
        self.assertEqual(report["critical"]["resolved"], 0)
        self.assertFalse(report["critical"]["active"])
        self.assertFalse(report["acceptance"]["death_zero"])
        self.assertFalse(report["acceptance"]["critical_resolved"])
        self.assertFalse(report["passed"])

    def test_strict_thresholds_coverage_and_runtime_errors_fail_closed(self) -> None:
        evidence = SoakEvidence(
            started_epoch=100,
            samples=100,
            valid_samples=94,
            final_health=10,
            final_hunger=6,
            final_connected_fresh=True,
            runtime_error_codes={"runtime_error"},
        )
        report = build_report(
            evidence,
            elapsed_seconds=DURATION_SECONDS - 1,
            natural_setup_verified=True,
            cleanup_verified=True,
            live_execution=True,
        )
        for gate in (
            "duration_complete",
            "connected_fresh_coverage",
            "autonomous_world_progress",
            "final_health_above_10",
            "final_hunger_above_6",
            "runtime_errors_zero",
        ):
            self.assertFalse(report["acceptance"][gate], gate)
        self.assertFalse(report["passed"])

    def test_monitor_samples_content_free_fresh_status_without_live_services(self) -> None:
        clock = [0.0]
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            status_path = root / "bot/mindcraft/status.json"
            status_path.parent.mkdir(parents=True)
            status_path.write_text(json.dumps(_passing_status(1_000)), encoding="utf-8")

            class Process:
                @staticmethod
                def poll():
                    return None

            server = SimpleNamespace(process=Process())

            def sleeper(seconds):
                clock[0] += seconds
                status_path.write_text(
                    json.dumps(_passing_status(1_000 + clock[0])), encoding="utf-8",
                )

            def command_runner(command, **_kwargs):
                if command[-1] == "container123":
                    return _result(stdout="true\n")
                if command[-1] == PRODUCTION_CONTAINER:
                    return _result(returncode=1)
                return _result(returncode=1)

            evidence = SoakEvidence(started_epoch=1_000)
            elapsed = monitor_soak(
                root,
                "container123",
                server,
                evidence,
                duration_seconds=3,
                command_runner=command_runner,
                port_probe=lambda port: port == GAME_PORT,
                monotonic=lambda: clock[0],
                epoch=lambda: 1_000 + clock[0],
                sleeper=sleeper,
                world_progress_reader=lambda _server: {
                    "walked_cm": 10,
                    "logs_mined": 3,
                    "wooden_pickaxes_crafted": 1,
                },
            )
            monitor_payload = json.loads((root / "monitor_status.json").read_text(encoding="utf-8"))
        self.assertEqual(elapsed, 3)
        self.assertEqual(evidence.samples, 4)
        self.assertEqual(evidence.valid_samples, 4)
        self.assertIsNone(evidence.abort_reason)
        self.assertTrue(monitor_payload["contentFree"])

    def test_monitor_final_probe_reads_stats_before_final_status(self) -> None:
        clock = [0.0]
        calls = [0]
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            status_path = root / "bot/mindcraft/status.json"
            status_path.parent.mkdir(parents=True)

            def pending_status():
                return _passing_status(1_000 + clock[0], controller={
                    "phase": "bootstrap_tools",
                    "last_decision": None,
                    "last_success": None,
                    "last_error": None,
                })

            status_path.write_text(json.dumps(pending_status()), encoding="utf-8")

            class Process:
                @staticmethod
                def poll():
                    return None

            def sleeper(seconds):
                clock[0] += seconds
                status_path.write_text(json.dumps(pending_status()), encoding="utf-8")

            def command_runner(command, **_kwargs):
                if command[-1] == "container123":
                    return _result(stdout="true\n")
                if command[-1] == PRODUCTION_CONTAINER:
                    return _result(returncode=1)
                return _result(returncode=1)

            def progress_reader(_server):
                calls[0] += 1
                if calls[0] == 2:
                    status_path.write_text(json.dumps(_passing_status(
                        1_000 + clock[0],
                        inventory={"wooden_pickaxe": 1},
                        controller={
                            "phase": "reassess",
                            "last_decision": "bootstrap_tools",
                            "last_success": True,
                            "last_error": None,
                        },
                    )), encoding="utf-8")
                    return {
                        "walked_cm": 20,
                        "logs_mined": 3,
                        "wooden_pickaxes_crafted": 1,
                    }
                return {}

            evidence = SoakEvidence(started_epoch=1_000)
            monitor_soak(
                root,
                "container123",
                SimpleNamespace(process=Process()),
                evidence,
                duration_seconds=1,
                command_runner=command_runner,
                port_probe=lambda port: port == GAME_PORT,
                monotonic=lambda: clock[0],
                epoch=lambda: 1_000 + clock[0],
                sleeper=sleeper,
                world_progress_reader=progress_reader,
            )
            partial = json.loads((root / "monitor_status.json").read_text(encoding="utf-8"))

        self.assertEqual(calls[0], 2)
        self.assertTrue(evidence.autonomous_world_progress_observed)
        self.assertTrue(partial["autonomousWorldProgress"]["observed"])

    def test_monitor_aborts_only_after_autonomous_progress_timeout_boundary(self) -> None:
        def run(duration):
            clock = [0.0]
            directory = tempfile.TemporaryDirectory()
            root = Path(directory.name)
            status_path = root / "bot/mindcraft/status.json"
            status_path.parent.mkdir(parents=True)
            status_path.write_text(json.dumps(_passing_status(1_000)), encoding="utf-8")

            class Process:
                @staticmethod
                def poll():
                    return None

            def sleeper(seconds):
                clock[0] += seconds
                status_path.write_text(
                    json.dumps(_passing_status(1_000 + clock[0])), encoding="utf-8",
                )

            def command_runner(command, **_kwargs):
                if command[-1] == "container123":
                    return _result(stdout="true\n")
                if command[-1] == PRODUCTION_CONTAINER:
                    return _result(returncode=1)
                return _result(returncode=1)

            evidence = SoakEvidence(started_epoch=1_000)
            elapsed = monitor_soak(
                root,
                "container123",
                SimpleNamespace(process=Process()),
                evidence,
                duration_seconds=duration,
                command_runner=command_runner,
                port_probe=lambda port: port == GAME_PORT,
                monotonic=lambda: clock[0],
                epoch=lambda: 1_000 + clock[0],
                sleeper=sleeper,
                world_progress_reader=lambda _server: {},
            )
            partial = json.loads((root / "monitor_status.json").read_text(encoding="utf-8"))
            directory.cleanup()
            return evidence, elapsed, partial

        before, before_elapsed, _partial = run(AUTONOMOUS_PROGRESS_TIMEOUT_SECONDS - 1)
        self.assertEqual(before_elapsed, AUTONOMOUS_PROGRESS_TIMEOUT_SECONDS - 1)
        self.assertIsNone(before.abort_reason)

        after, after_elapsed, partial = run(AUTONOMOUS_PROGRESS_TIMEOUT_SECONDS + 180)
        self.assertEqual(after_elapsed, AUTONOMOUS_PROGRESS_TIMEOUT_SECONDS + 1)
        self.assertEqual(after.abort_reason, "autonomous_progress_timeout")
        self.assertEqual(partial["abortReason"], "autonomous_progress_timeout")
        self.assertTrue(partial["autonomousWorldProgress"]["timedOut"])

    def test_monitor_final_runtime_check_closes_last_interval_exit_window(self) -> None:
        clock = [0.0]
        container_checks = [0]
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            status_path = root / "bot/mindcraft/status.json"
            status_path.parent.mkdir(parents=True)
            status_path.write_text(json.dumps(_passing_status(1_000)), encoding="utf-8")

            class Process:
                @staticmethod
                def poll():
                    return None

            def sleeper(seconds):
                clock[0] += seconds
                status_path.write_text(
                    json.dumps(_passing_status(1_000 + clock[0])), encoding="utf-8",
                )

            def command_runner(command, **_kwargs):
                if command[-1] == "container123":
                    container_checks[0] += 1
                    return _result(stdout="true\n" if container_checks[0] <= 2 else "false\n")
                if command[-1] == PRODUCTION_CONTAINER:
                    return _result(returncode=1)
                return _result(returncode=1)

            evidence = SoakEvidence(started_epoch=1_000)
            monitor_soak(
                root,
                "container123",
                SimpleNamespace(process=Process()),
                evidence,
                duration_seconds=4,
                command_runner=command_runner,
                port_probe=lambda port: port == GAME_PORT,
                monotonic=lambda: clock[0],
                epoch=lambda: 1_000 + clock[0],
                sleeper=sleeper,
                world_progress_reader=lambda _server: {},
            )
            partial = json.loads((root / "monitor_status.json").read_text(encoding="utf-8"))

        self.assertEqual(container_checks[0], 3)
        self.assertEqual(evidence.abort_reason, "container_exit")
        self.assertEqual(partial["abortReason"], "container_exit")
        self.assertFalse(partial["acceptance"]["run_completed_without_abort"])

    def test_owned_container_cleanup_checks_all_identity_fields(self) -> None:
        container_id = "a" * 64
        removed = []

        def runner(command, **_kwargs):
            if command[1:3] == ["inspect", "--format"]:
                return _result(stdout=(
                    f"{container_id}|/{CONTAINER_NAME}|{OWNER_VALUE}|run123\n"
                ))
            removed.append(command)
            return _result()

        _remove_owned_container(container_id, "run123", runner)
        self.assertEqual(removed, [["docker", "rm", "--force", container_id]])

        def wrong_owner(command, **_kwargs):
            return _result(stdout=f"{container_id}|/{CONTAINER_NAME}|wrong|run123\n")

        with self.assertRaisesRegex(MatrixSafetyError, "ownership_lost"):
            _remove_owned_container(container_id, "run123", wrong_owner)

    def test_run_soak_always_cleans_owned_processes_on_monitor_failure(self) -> None:
        removed = []

        class Process:
            stopped = False

            def poll(self):
                return 0 if self.stopped else None

        class Server:
            def __init__(self):
                self.process = Process()

            def stop(self):
                self.process.stopped = True

        server = Server()
        with tempfile.TemporaryDirectory() as directory:
            artifact = Path(directory) / "soak"
            report = run_soak(
                SCRIPT_REPO_ROOT,
                artifact,
                Path("server.jar"),
                "java",
                preflight=lambda *_a, **_k: None,
                server_factory=lambda *_a, **_k: server,
                natural_verifier=lambda *_a, **_k: True,
                bot_starter=lambda *_a, **_k: "a" * 64,
                ready_waiter=lambda *_a, **_k: _passing_status(100),
                monitor=lambda *_a, **_k: (_ for _ in ()).throw(
                    MatrixSafetyError("test_monitor_failure")
                ),
                container_remover=lambda *args: removed.append(args[:2]),
                production_checker=lambda _runner: True,
                container_absent_checker=lambda _runner: True,
                port_probe=lambda _port: False,
                epoch=lambda: 100,
            )
            persisted = json.loads((artifact / "report.json").read_text(encoding="utf-8"))
        self.assertEqual(removed[0][0], "a" * 64)
        self.assertTrue(server.process.stopped)
        self.assertTrue(report["cleanupVerified"])
        self.assertFalse(report["passed"])
        self.assertEqual(persisted, report)


if __name__ == "__main__":
    unittest.main()
