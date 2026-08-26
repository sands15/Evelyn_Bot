from __future__ import annotations

import io
import json
import sys
import tempfile
import unittest
from contextlib import redirect_stdout
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from tools.voyager.combat_matrix import (
    CONTAINER_NAME,
    EMERGENCY_ZOMBIE_CELL_ID,
    FIXTURE_RELATIVE,
    GAME_PORT,
    MatrixSafetyError,
    SCRIPT_REPO_ROOT,
    _first_tactical_episode,
    _invalid_observation,
    _leading_reflex_episode_durations,
    _observe_projectile_reflex,
    _observe_terminal_episode,
    _remove_owned_container,
    _scenario_latencies,
    _startup_diagnosis,
    _terminal_episodes,
    _wait_for_emergency_admission,
    _wait_for_connected_status,
    base_server_commands,
    build_projectile_report,
    build_report,
    build_scenarios,
    cleanup_plan,
    docker_run_command,
    emergency_zombie_scenario,
    emergency_zombie_spawn_command,
    evaluate_projectile_smoke,
    evaluate_scenario,
    main,
    preflight_run,
    projectile_launch_command,
    projectile_setup_commands,
    run_batch,
    run_projectile_cell,
    run_scenario_cell,
    scenario_commands,
    smoke_scenarios,
    verify_base_server_setup,
    verify_emergency_cleanup,
    verify_emergency_zombie_spawn,
    verify_projectile_setup,
    verify_scenario_setup,
    validate_manifest,
)


def passing_observation(scenario):
    observation = {
        "infrastructure_valid": True,
        "runtime_error": None,
        "death_count": 0,
        "episode_count": 1,
        "episode": {
            "outcome": "success",
            "verified": True,
            "tactic": scenario.expected_preset,
            "damage": 0,
            "durationMs": 1_000,
        },
        "reflex_reason": "hostile",
        "reflex_to_action_ms": 1,
        "reflex_durations_ms": [1_100],
        "wake_to_decision_ms": 1_116,
        "decision_to_action_ms": 1,
        "remaining_hostiles": 0,
        "verified_clear": True,
    }
    if scenario.expected_disposition == "flee":
        observation.update({
            "remaining_hostiles": scenario.hostile_count,
            "verified_clear": False,
            "min_stable_distance_meters": 18.0,
            "safe_stable_ms": 2_000,
        })
    return observation


class CombatMatrixTests(unittest.TestCase):
    def test_startup_diagnosis_is_fixed_content_free_and_separates_infra(self) -> None:
        calls = []

        def fake_run(command, **kwargs):
            calls.append((tuple(command), kwargs))
            if command[:2] == ["docker", "top"]:
                return SimpleNamespace(
                    returncode=0,
                    stdout="ARGS\nnode main.js\nnode src/process/init_agent.js --secret token\n",
                    stderr="",
                )
            if command[:2] == ["docker", "logs"]:
                return SimpleNamespace(
                    returncode=0,
                    stdout="private-value ECONNREFUSED private-value",
                    stderr="",
                )
            self.fail(f"unexpected command: {command}")

        output = io.StringIO()
        with redirect_stdout(output):
            diagnosis = _startup_diagnosis("container123", fake_run)
        observation = _invalid_observation("bot_connection_unverified", diagnosis)
        result = evaluate_projectile_smoke(observation)
        serialized = json.dumps(result)

        self.assertEqual(output.getvalue(), "")
        self.assertEqual(diagnosis, {
            "startup_child_state": "running",
            "startup_log_category": "startup_connection_failed",
            "runtime_error": None,
        })
        self.assertIn("infrastructure_invalid", result["failure_codes"])
        self.assertNotIn("runtime_error", result["failure_codes"])
        self.assertEqual(result["metrics"]["infrastructure_code"], "bot_connection_unverified")
        self.assertNotIn("private-value", serialized)
        self.assertNotIn("--secret", serialized)
        self.assertEqual([call[0][1] for call in calls], ["top", "logs"])
        self.assertEqual(calls[0][0][-2:], ("-eo", "pid,args"))
        self.assertTrue(all(call[1]["capture_output"] for call in calls))

    def test_missing_startup_child_is_an_actual_fixed_runtime_error(self) -> None:
        def fake_run(command, **_kwargs):
            if command[:2] == ["docker", "top"]:
                return SimpleNamespace(returncode=0, stdout="ARGS\nnode main.js\n", stderr="")
            return SimpleNamespace(
                returncode=0,
                stdout="private-value TypeError private-value",
                stderr="",
            )

        diagnosis = _startup_diagnosis("container123", fake_run)
        result = evaluate_projectile_smoke(
            _invalid_observation("bot_connection_unverified", diagnosis)
        )
        self.assertIn("runtime_error", result["failure_codes"])
        self.assertEqual(result["metrics"]["runtime_error_code"], "startup_child_not_running")
        self.assertEqual(result["metrics"]["startup_log_category"], "startup_process_failed")
        self.assertNotIn("private-value", json.dumps(result))

    def test_startup_diagnosis_runs_before_owned_container_removal(self) -> None:
        calls = []
        clock = [0.0]
        container_id = "abc123def456"
        run_id = "run123"

        def fake_run(command, **_kwargs):
            calls.append(tuple(command))
            if command[:3] == ["docker", "run", "--detach"]:
                return SimpleNamespace(returncode=0, stdout=f"{container_id}\n", stderr="")
            if command[:3] == ["docker", "inspect", "--format"]:
                if command[-1] == "evelyn-mindcraft":
                    return SimpleNamespace(returncode=1, stdout="", stderr="")
                if command[3] == "{{.State.Running}}":
                    return SimpleNamespace(returncode=0, stdout="true\n", stderr="")
                return SimpleNamespace(
                    returncode=0,
                    stdout=(
                        f"{container_id}|/evelyn-combat-matrix-batch|"
                        f"combat_matrix_batch|{run_id}\n"
                    ),
                    stderr="",
                )
            if command[:2] == ["docker", "top"]:
                return SimpleNamespace(
                    returncode=0,
                    stdout="ARGS\nnode src/process/init_agent.js\n",
                    stderr="",
                )
            if command[:2] == ["docker", "logs"]:
                return SimpleNamespace(returncode=0, stdout="ECONNREFUSED", stderr="")
            if command[:3] == ["docker", "rm", "--force"]:
                return SimpleNamespace(returncode=0, stdout=container_id, stderr="")
            self.fail(f"unexpected command: {command}")

        class Server:
            def command(self, _command):
                pass

        with tempfile.TemporaryDirectory() as directory:
            observation = run_projectile_cell(
                SCRIPT_REPO_ROOT,
                Path(directory),
                run_id,
                Server(),
                command_runner=fake_run,
                monotonic=lambda: clock[0],
                sleeper=lambda seconds: clock.__setitem__(0, clock[0] + seconds),
            )

        top_index = next(index for index, call in enumerate(calls) if call[1] == "top")
        logs_index = next(index for index, call in enumerate(calls) if call[1] == "logs")
        remove_index = next(index for index, call in enumerate(calls) if call[1:3] == ("rm", "--force"))
        self.assertLess(top_index, logs_index)
        self.assertLess(logs_index, remove_index)
        self.assertEqual(observation["infrastructure_code"], "bot_connection_unverified")
        self.assertIsNone(observation["runtime_error"])

    def test_ready_barrier_requires_two_controller_updates(self) -> None:
        clock = [0.0]
        with tempfile.TemporaryDirectory() as directory:
            status_path = Path(directory) / "status.json"

            def write_status(updated_at):
                status_path.write_text(json.dumps({
                    "connected": True,
                    "connection_state": "connected",
                    "survival_controller": {"updated_at": updated_at},
                }), encoding="utf-8")

            write_status(10.0)

            def sleeper(seconds):
                clock[0] += seconds
                if clock[0] >= 0.2:
                    write_status(11.0)

            def fake_run(command, **_kwargs):
                if command[-1] == "container123":
                    return SimpleNamespace(returncode=0, stdout="true\n", stderr="")
                return SimpleNamespace(returncode=1, stdout="", stderr="")

            ready = _wait_for_connected_status(
                status_path,
                "container123",
                1.0,
                command_runner=fake_run,
                monotonic=lambda: clock[0],
                sleeper=sleeper,
            )

        self.assertEqual(ready["survival_controller"]["updated_at"], 11.0)
        self.assertGreaterEqual(clock[0], 0.2)

    def test_projectile_smoke_requires_fresh_fast_isolated_shield_effect(self) -> None:
        good = {
            "infrastructure_valid": True,
            "runtime_error": None,
            "death_count": 0,
            "reflex_reason": "projectile",
            "reflex_to_action_ms": 100,
            "response": "shield",
            "shield_blocked_damage": 30,
            "damage": 0,
            "hostile_count": 0,
            "raw_log": "must-not-escape",
        }
        report = build_projectile_report(good)
        self.assertTrue(report["passed"])
        self.assertTrue(report["contentFree"])
        self.assertNotIn("must-not-escape", json.dumps(report))
        cases = (
            ({**good, "reflex_reason": "hostile"}, "projectile_reflex_unverified"),
            ({**good, "reflex_to_action_ms": 101}, "reflex_start_latency_exceeded"),
            ({**good, "shield_blocked_damage": 0}, "shield_effect_unverified"),
            ({**good, "damage": 0.5}, "projectile_damage_observed"),
            ({**good, "hostile_count": 1}, "hostile_reflex_not_isolated"),
        )
        for observation, code in cases:
            with self.subTest(code=code):
                self.assertIn(code, evaluate_projectile_smoke(observation)["failure_codes"])

    def test_projectile_fixture_uses_real_arrow_and_shield_stat_probe(self) -> None:
        commands = projectile_setup_commands()
        self.assertIn(
            "scoreboard objectives add evshield minecraft.custom:minecraft.damage_blocked_by_shield",
            commands,
        )
        self.assertIn(
            "item replace entity Evelyn_0428 weapon.offhand with minecraft:shield",
            commands,
        )
        launch = projectile_launch_command()
        self.assertIn("summon minecraft:arrow", launch)
        self.assertIn("Motion:[-1.2d,0.0d,0.0d]", launch)
        self.assertIn('Tags:["evelyn_matrix","evelyn_projectile_fixture"]', launch)
        self.assertFalse(any("skeleton" in command for command in commands + (launch,)))

        class Server:
            def query_tagged_count(self):
                return 0

            def query_result(self, tail):
                return {
                    "run time query daytime": 6_000,
                    "if items entity Evelyn_0428 weapon.offhand minecraft:shield": 1,
                    "if score Evelyn_0428 evshield matches 0": 1,
                    "run data get entity Evelyn_0428 Health 100": 2_000,
                }.get(tail, 0)

        self.assertTrue(verify_projectile_setup(Server()))

    def test_projectile_observer_latches_first_fresh_reason_and_settles_effect(self) -> None:
        clock = [0.0]
        with tempfile.TemporaryDirectory() as directory:
            status_path = Path(directory) / "status.json"
            status_path.write_text(json.dumps({
                "survival_controller": {
                    "last_reflex_at": 9.0,
                    "reflex_reason": "hostile",
                    "reflex_to_action_ms": 9,
                },
                "hostiles_nearby": [],
            }), encoding="utf-8")

            def sleeper(seconds):
                clock[0] += seconds
                if clock[0] >= 0.1:
                    status_path.write_text(json.dumps({
                        "survival_controller": {
                            "last_reflex_at": 10.0,
                            "reflex_reason": "projectile",
                            "reflex_to_action_ms": 1,
                        },
                        "hostiles_nearby": [],
                    }), encoding="utf-8")

            def fake_run(command, **_kwargs):
                if command[-1] == "container123":
                    return SimpleNamespace(returncode=0, stdout="true\n", stderr="")
                return SimpleNamespace(returncode=1, stdout="", stderr="")

            _, sampled = _observe_projectile_reflex(
                status_path,
                "container123",
                3.0,
                9.0,
                command_runner=fake_run,
                monotonic=lambda: clock[0],
                sleeper=sleeper,
            )
        self.assertEqual(sampled["reflex_reason"], "projectile")
        self.assertEqual(sampled["reflex_to_action_ms"], 1)
        self.assertEqual(sampled["hostile_count"], 0)
        self.assertGreaterEqual(clock[0], 1.35)

    def test_manifest_is_exact_five_by_two_by_two_product(self) -> None:
        scenarios = build_scenarios()
        validate_manifest(scenarios)
        self.assertEqual(len(scenarios), 20)
        self.assertEqual(len({item.id for item in scenarios}), 20)
        self.assertEqual(
            {(item.threat, item.loadout, item.time) for item in scenarios},
            {
                (threat, loadout, time_name)
                for threat in {
                    "single_zombie", "single_skeleton", "zombie_skeleton",
                    "three_zombies", "creeper",
                }
                for loadout in {"unprotected", "protected"}
                for time_name in {"day", "night"}
            },
        )
        self.assertTrue(all(
            item.expected_disposition == "flee" and item.expected_preset == "disengage"
            for item in scenarios if item.threat == "creeper"
        ))
        zombie_day = {
            item.loadout: item
            for item in scenarios
            if item.threat == "single_zombie" and item.time == "day"
        }
        self.assertEqual(
            (zombie_day["unprotected"].expected_disposition, zombie_day["unprotected"].expected_preset),
            ("flee", "disengage"),
        )
        self.assertEqual(
            (zombie_day["protected"].expected_disposition, zombie_day["protected"].expected_preset),
            ("fight", "melee"),
        )

    def test_fight_requires_verified_clear(self) -> None:
        scenario = next(item for item in build_scenarios() if item.expected_disposition == "fight")
        good = passing_observation(scenario)
        self.assertTrue(evaluate_scenario(scenario, good)["passed"])

        uncleared = {**good, "remaining_hostiles": 1, "verified_clear": False}
        result = evaluate_scenario(scenario, uncleared)
        self.assertFalse(result["passed"])
        self.assertIn("fight_clear_unverified", result["failure_codes"])

    def test_flee_requires_live_hostiles_beyond_18m_for_two_seconds(self) -> None:
        scenario = next(
            item for item in build_scenarios()
            if item.expected_disposition == "flee" and item.threat != "creeper"
        )
        good = passing_observation(scenario)
        self.assertTrue(evaluate_scenario(scenario, good)["passed"])

        cases = (
            ({**good, "remaining_hostiles": 0}, "flee_hostile_removed"),
            ({**good, "min_stable_distance_meters": 17.9}, "flee_distance_unverified"),
            ({**good, "safe_stable_ms": 1_999}, "flee_stability_unverified"),
        )
        for observation, expected_code in cases:
            with self.subTest(expected_code=expected_code):
                result = evaluate_scenario(scenario, observation)
                self.assertFalse(result["passed"])
                self.assertIn(expected_code, result["failure_codes"])

    def test_creeper_flee_accepts_safe_self_removal_but_not_invalid_counts(self) -> None:
        scenario = next(
            item for item in build_scenarios()
            if item.id == "creeper__protected__day"
        )
        removed = {
            **passing_observation(scenario),
            "remaining_hostiles": 0,
            "verified_clear": True,
        }
        self.assertTrue(evaluate_scenario(scenario, removed)["passed"])
        self.assertEqual(scenario.to_dict()["pass_rules"]["remaining_hostiles"], [0, 1])

        invalid = evaluate_scenario(scenario, {**removed, "remaining_hostiles": 2})
        self.assertFalse(invalid["passed"])
        self.assertIn("flee_hostile_removed", invalid["failure_codes"])

    def test_policy_alignment_does_not_hide_failed_zombie_disengage(self) -> None:
        scenario = next(
            item for item in build_scenarios()
            if item.id == "single_zombie__unprotected__day"
        )
        observation = passing_observation(scenario)
        observation["episode"] = {
            **observation["episode"],
            "outcome": "failure",
            "verified": False,
            "tactic": "disengage",
        }
        result = evaluate_scenario(scenario, observation)
        self.assertFalse(result["passed"])
        self.assertIn("terminal_success_unverified", result["failure_codes"])
        self.assertNotIn("unexpected_combat_preset", result["failure_codes"])

    def test_flee_success_waits_for_external_stability_but_failure_returns_immediately(self) -> None:
        def observe(outcome, *, require_safe_stable=True):
            clock = [0.0]
            with tempfile.TemporaryDirectory() as directory:
                root = Path(directory)
                status_path = root / "status.json"
                history_path = root / "combat_history.json"
                status_path.write_text(json.dumps({
                    "hostiles_nearby": [{"distance": 20}],
                }), encoding="utf-8")
                history_path.write_text(json.dumps({
                    "schemaVersion": 1,
                    "episodes": [],
                }), encoding="utf-8")

                def sleeper(seconds):
                    clock[0] += seconds
                    if clock[0] >= 1.5:
                        history_path.write_text(json.dumps({
                            "schemaVersion": 1,
                            "episodes": [{"outcome": outcome, "tactic": "disengage"}],
                        }), encoding="utf-8")

                def fake_run(command, **_kwargs):
                    if command[-1] == "container123":
                        return SimpleNamespace(returncode=0, stdout="true\n", stderr="")
                    return SimpleNamespace(returncode=1, stdout="", stderr="")

                _observe_terminal_episode(
                    status_path,
                    history_path,
                    "container123",
                    5.0,
                    None,
                    None,
                    require_safe_stable=require_safe_stable,
                    command_runner=fake_run,
                    monotonic=lambda: clock[0],
                    sleeper=sleeper,
                )
                return clock[0]

        self.assertGreaterEqual(observe("success"), 2.0)
        self.assertLess(observe("failure"), 2.0)
        self.assertLess(observe("success", require_safe_stable=False), 2.0)

    def test_negative_runtime_metrics_fail_closed(self) -> None:
        scenario = next(item for item in build_scenarios() if item.expected_disposition == "fight")
        good = passing_observation(scenario)
        cases = (
            ({**good, "episode": {**good["episode"], "damage": -1}}, "damage_limit_exceeded"),
            ({**good, "episode": {**good["episode"], "durationMs": -1}}, "duration_limit_exceeded"),
            ({**good, "reflex_to_action_ms": -1}, "latency_unverified"),
            ({**good, "reflex_durations_ms": [-1]}, "latency_unverified"),
            ({**good, "wake_to_decision_ms": -1}, "latency_unverified"),
            ({**good, "decision_to_action_ms": -1}, "latency_unverified"),
        )
        for observation, code in cases:
            with self.subTest(code=code):
                self.assertIn(code, evaluate_scenario(scenario, observation)["failure_codes"])

    def test_interrupted_reflex_is_not_counted_as_terminal_episode(self) -> None:
        episodes = [
            {"outcome": "interrupted", "tactic": "disengage", "durationMs": 1_100},
            {"outcome": "interrupted", "tactic": "disengage", "durationMs": 200},
            {"outcome": "success", "tactic": "melee"},
        ]
        self.assertEqual(_terminal_episodes(episodes), [episodes[2]])
        self.assertEqual(_leading_reflex_episode_durations(episodes), [1_100, 200])

    def test_later_reflex_death_is_not_attributed_as_the_p1_episode(self) -> None:
        scenario = emergency_zombie_scenario()
        episodes = [
            {
                "outcome": "interrupted", "verified": False, "tactic": "disengage",
                "damage": 3, "durationMs": 1_100,
            },
            {
                "outcome": "interrupted", "verified": False, "tactic": "melee",
                "damage": 3, "durationMs": 1_002,
            },
            {
                "outcome": "death", "verified": False, "tactic": "disengage",
                "damage": 1, "durationMs": 1_030,
            },
        ]
        tactical = _first_tactical_episode(episodes, [1_100])
        observation = {
            **passing_observation(scenario),
            "death_count": 1,
            "episode_count": len(_terminal_episodes(episodes)),
            "episode": tactical,
            "remaining_hostiles": 1,
            "verified_clear": False,
        }

        result = evaluate_scenario(scenario, observation)

        self.assertIs(tactical, episodes[1])
        self.assertEqual(result["metrics"]["preset"], "melee")
        self.assertEqual(result["metrics"]["outcome"], "interrupted")
        self.assertEqual(result["metrics"]["duration_ms"], 1_002)
        self.assertEqual(result["metrics"]["damage"], 3)
        self.assertIn("death_observed", result["failure_codes"])
        self.assertNotIn("unexpected_combat_preset", result["failure_codes"])

        interrupted_flee = [episodes[0], {**episodes[1], "tactic": "disengage"}, episodes[2]]
        self.assertIs(_first_tactical_episode(interrupted_flee, [1_100]), interrupted_flee[1])
        self.assertIsNone(_first_tactical_episode(episodes, []))

    def test_two_reflex_urgent_chain_uses_total_active_time(self) -> None:
        scenario = next(
            item for item in build_scenarios()
            if item.id == "zombie_skeleton__protected__night"
        )
        observation = {
            **passing_observation(scenario),
            "reflex_to_action_ms": 0,
            "reflex_durations_ms": [1_100, 1_100],
            "wake_to_decision_ms": 2_258,
            "decision_to_action_ms": 0,
        }
        result = evaluate_scenario(scenario, observation)
        self.assertTrue(result["passed"])
        self.assertEqual(result["metrics"]["reflex_total_duration_ms"], 2_200)
        self.assertEqual(result["metrics"]["p1_after_reflex_ms"], 58)

    def test_split_latency_boundaries_and_missing_evidence_fail_closed(self) -> None:
        scenario = next(item for item in build_scenarios() if item.expected_disposition == "fight")
        boundary = {
            **passing_observation(scenario),
            "reflex_to_action_ms": 100,
            "reflex_durations_ms": [1_250, 1_250],
            "wake_to_decision_ms": 2_850,
            "decision_to_action_ms": 100,
        }
        self.assertTrue(evaluate_scenario(scenario, boundary)["passed"])
        cases = (
            ({**boundary, "reflex_to_action_ms": 101}, "reflex_start_latency_exceeded"),
            ({**boundary, "reflex_durations_ms": [1_250, 1_251], "wake_to_decision_ms": 2_851}, "reflex_duration_exceeded"),
            ({**boundary, "wake_to_decision_ms": 2_851}, "tactical_latency_exceeded"),
            ({**boundary, "decision_to_action_ms": 101}, "action_latency_exceeded"),
            ({**boundary, "reflex_to_action_ms": None}, "latency_unverified"),
        )
        for observation, code in cases:
            with self.subTest(code=code):
                self.assertIn(code, evaluate_scenario(scenario, observation)["failure_codes"])

    def test_collector_freezes_reflex_chain_when_first_wake_is_latched(self) -> None:
        clock = [0.0]
        first = {"outcome": "interrupted", "tactic": "disengage", "durationMs": 1_100}
        later = {"outcome": "interrupted", "tactic": "disengage", "durationMs": 1_100}
        terminal = {"outcome": "success", "tactic": "melee"}
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            status_path = root / "status.json"
            history_path = root / "combat_history.json"
            status_path.write_text(json.dumps({
                "survival_controller": {"updated_at": 10.0},
                "hostiles_nearby": [],
            }), encoding="utf-8")
            history_path.write_text(json.dumps({
                "schemaVersion": 1,
                "episodes": [first],
            }), encoding="utf-8")

            def sleeper(seconds):
                clock[0] += seconds
                if clock[0] >= 0.1:
                    status_path.write_text(json.dumps({
                        "survival_controller": {
                            "updated_at": 11.0,
                            "last_reflex_at": 10.5,
                            "reflex_reason": "hostile",
                            "reflex_to_action_ms": 1,
                            "wake_to_decision_ms": 1_116,
                            "decision_to_action_ms": 1,
                        },
                        "hostiles_nearby": [],
                    }), encoding="utf-8")
                if clock[0] >= 0.2:
                    history_path.write_text(json.dumps({
                        "schemaVersion": 1,
                        "episodes": [first, later, terminal],
                    }), encoding="utf-8")

            def fake_run(command, **_kwargs):
                if command[-1] == "container123":
                    return SimpleNamespace(returncode=0, stdout="true\n", stderr="")
                return SimpleNamespace(returncode=1, stdout="", stderr="")

            _, _, sampled = _observe_terminal_episode(
                status_path,
                history_path,
                "container123",
                1.0,
                10.0,
                9.0,
                require_safe_stable=False,
                command_runner=fake_run,
                monotonic=lambda: clock[0],
                sleeper=sleeper,
            )
        self.assertEqual(sampled["reflex_durations"], [1_100])

    def test_scenario_latency_ignores_ready_baseline_values(self) -> None:
        stale = {
            "updated_at": 10.0,
            "last_reflex_at": 9.0,
            "reflex_reason": "hostile",
            "reflex_to_action_ms": 2,
            "wake_to_decision_ms": 499,
            "decision_to_action_ms": 249,
        }
        tactical_only = {**stale, "updated_at": 10.1, "wake_to_decision_ms": 58}
        fresh = {**tactical_only, "last_reflex_at": 10.2, "reflex_to_action_ms": 1}
        self.assertEqual(
            _scenario_latencies(stale, 10.0, 9.0),
            (None, None, None, None),
        )
        self.assertEqual(
            _scenario_latencies(tactical_only, 10.0, 9.0),
            (58.0, 249.0, None, None),
        )
        self.assertEqual(
            _scenario_latencies(fresh, 10.0, 9.0),
            (58.0, 249.0, "hostile", 1.0),
        )

    def test_offline_evaluation_never_claims_live_execution(self) -> None:
        observations = {
            scenario.id: passing_observation(scenario)
            for scenario in build_scenarios()
        }
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "observations.json"
            path.write_text(json.dumps(observations), encoding="utf-8")
            output = io.StringIO()
            with redirect_stdout(output):
                exit_code = main(["--evaluate", str(path)])
        payload = json.loads(output.getvalue())
        self.assertEqual(exit_code, 0)
        self.assertTrue(payload["passed"])
        self.assertEqual(payload["mode"], "offline_evaluation")
        self.assertFalse(payload["liveExecution"])

    def test_container_cleanup_rejects_wrong_owner_without_removal(self) -> None:
        calls = []

        def fake_run(command, **_kwargs):
            calls.append(tuple(command))
            return SimpleNamespace(
                returncode=0,
                stdout="abc123def456|/evelyn-combat-matrix-batch|wrong-owner|run123\n",
                stderr="",
            )

        with self.assertRaisesRegex(MatrixSafetyError, "ownership_lost"):
            _remove_owned_container("abc123def456", "run123", fake_run)
        self.assertFalse(any(command[:3] == ("docker", "rm", "--force") for command in calls))

    def test_smoke_plan_is_exact_two_cells_with_reset_and_loadout(self) -> None:
        scenarios = smoke_scenarios()
        self.assertEqual([item.id for item in scenarios], [
            "single_zombie__unprotected__day",
            "single_skeleton__protected__day",
        ])
        zombie = scenario_commands(scenarios[0])
        skeleton = scenario_commands(scenarios[1])
        self.assertIn("kill @e[type=!minecraft:player]", zombie)
        self.assertIn("time set 6000", zombie)
        self.assertTrue(any("summon minecraft:zombie" in command for command in zombie))
        self.assertIn("Invulnerable:1b", "\n".join(zombie))
        self.assertFalse(any("iron_chestplate" in command for command in zombie))
        self.assertIn(
            "item replace entity Evelyn_0428 armor.chest with minecraft:iron_chestplate",
            skeleton,
        )
        self.assertIn("give Evelyn_0428 minecraft:arrow 32", skeleton)
        self.assertTrue(any("summon minecraft:skeleton" in command for command in skeleton))
        flee = next(item for item in build_scenarios() if item.expected_disposition == "flee")
        self.assertIn("Invulnerable:1b", "\n".join(scenario_commands(flee)))

    def test_emergency_zombie_fixture_requires_exact_fallback_context(self) -> None:
        scenario = emergency_zombie_scenario()
        canonical = next(
            item for item in build_scenarios()
            if item.id == "single_zombie__unprotected__night"
        )
        commands = scenario_commands(scenario)
        rendered = "\n".join(commands)

        self.assertEqual(scenario.id, EMERGENCY_ZOMBIE_CELL_ID)
        self.assertEqual(canonical.expected_disposition, "flee")
        self.assertEqual(scenario.expected_disposition, "fight")
        self.assertEqual(scenario.expected_preset, "melee")
        self.assertEqual(scenario.max_damage, 8.0)
        self.assertIn("time set 13000", commands)
        self.assertIn(
            "fill -3 99 -3 4 103 4 minecraft:bedrock hollow",
            commands,
        )
        self.assertIn(
            "attribute Evelyn_0428 minecraft:max_health base set 20",
            commands,
        )
        self.assertIn(
            "item replace entity Evelyn_0428 weapon.mainhand with minecraft:iron_sword",
            commands,
        )
        self.assertIn("damage Evelyn_0428 10 minecraft:generic", commands)
        self.assertLess(
            commands.index("effect give Evelyn_0428 minecraft:instant_health 1 10 true"),
            commands.index("effect clear Evelyn_0428 minecraft:instant_health"),
        )
        self.assertLess(
            commands.index("effect clear Evelyn_0428 minecraft:instant_health"),
            commands.index("damage Evelyn_0428 10 minecraft:generic"),
        )
        self.assertNotIn("summon minecraft:zombie", rendered)
        spawn = emergency_zombie_spawn_command()
        self.assertIn("summon minecraft:zombie 3.5 100 3.5", spawn)
        self.assertIn('Tags:["evelyn_matrix"]', spawn)
        self.assertNotIn("Invulnerable:1b", rendered)
        self.assertNotIn("Invulnerable:1b", spawn)
        self.assertFalse(any("iron_chestplate" in command for command in commands))

        queries = []

        class Server:
            def query_tagged_count(self):
                return 0

            def query_result(self, tail):
                queries.append(tail)
                if tail == "run time query daytime":
                    return 13_000
                if tail == "run data get entity Evelyn_0428 Health 100":
                    return 1_000
                if tail.startswith("unless items entity"):
                    return 1
                return 2_000 if "max_health base get 100" in tail else 1

        self.assertTrue(verify_scenario_setup(Server(), scenario))
        self.assertIn(
            "positioned 0.5 100 0.5 if entity "
            "@a[name=Evelyn_0428,distance=..0.1,limit=1]",
            queries,
        )
        self.assertIn(
            "positioned 0.5 100 0.5 unless entity "
            "@e[type=!minecraft:player,distance=..8]",
            queries,
        )
        self.assertIn("if block -3 99 -3 minecraft:bedrock", queries)
        self.assertIn("if block 3 102 3 minecraft:air", queries)

        class SpawnServer:
            def query_tagged_count(self):
                return 1

            def query_result(self, tail):
                if tail == "run data get entity Evelyn_0428 Health 100":
                    return 1_000
                return 1

        self.assertTrue(verify_emergency_zombie_spawn(SpawnServer()))

        result = evaluate_scenario(scenario, {
            **passing_observation(scenario),
            "episode": {
                **passing_observation(scenario)["episode"],
                "damage": 8.0,
            },
        })
        self.assertTrue(result["passed"])
        self.assertEqual(result["metrics"]["remaining_hostiles"], 0)

        over_damage = passing_observation(scenario)
        over_damage["episode"]["damage"] = 8.1
        self.assertIn(
            "damage_limit_exceeded",
            evaluate_scenario(scenario, over_damage)["failure_codes"],
        )
        wrong_preset = passing_observation(scenario)
        wrong_preset["episode"]["tactic"] = "disengage"
        self.assertIn(
            "unexpected_combat_preset",
            evaluate_scenario(scenario, wrong_preset)["failure_codes"],
        )
        uncleared = {
            **passing_observation(scenario),
            "remaining_hostiles": 1,
            "verified_clear": False,
        }
        self.assertIn(
            "fight_clear_unverified",
            evaluate_scenario(scenario, uncleared)["failure_codes"],
        )

    def test_emergency_admission_waits_four_seconds_then_two_fresh_centered_samples(self) -> None:
        clock = [0.0]
        with tempfile.TemporaryDirectory() as directory:
            status_path = Path(directory) / "status.json"

            def write_status(updated_at, *, centered=True):
                status_path.write_text(json.dumps({
                    "connected": True,
                    "connection_state": "connected",
                    "updated_at": updated_at,
                    "health": 10,
                    "inventory": {"iron_sword": 1},
                    "hostiles_nearby": [],
                    "position": {
                        "x": 0.5 if centered else 1.5,
                        "y": 100,
                        "z": 0.5,
                    },
                    "survival_controller": {"updated_at": updated_at},
                }), encoding="utf-8")

            write_status(10.0, centered=False)

            def sleeper(seconds):
                clock[0] += seconds
                write_status(10.0 + clock[0], centered=clock[0] >= 4.0)

            def fake_run(command, **_kwargs):
                if command[-1] == "container123":
                    return SimpleNamespace(returncode=0, stdout="true\n", stderr="")
                return SimpleNamespace(returncode=1, stdout="", stderr="")

            settled = _wait_for_emergency_admission(
                status_path,
                "container123",
                6.0,
                centered=False,
                minimum_stable_seconds=4.0,
                minimum_fresh_samples=1,
                after_updated_at=None,
                command_runner=fake_run,
                monotonic=lambda: clock[0],
                sleeper=sleeper,
            )
            self.assertIsNotNone(settled)
            self.assertGreaterEqual(clock[0], 4.0)
            first_updated = settled["updated_at"]
            baseline = _wait_for_emergency_admission(
                status_path,
                "container123",
                6.0,
                centered=True,
                minimum_stable_seconds=0,
                minimum_fresh_samples=2,
                after_updated_at=first_updated,
                command_runner=fake_run,
                monotonic=lambda: clock[0],
                sleeper=sleeper,
            )

        self.assertIsNotNone(baseline)
        self.assertGreater(baseline["updated_at"], first_updated)

    def test_emergency_admission_rejects_a_frozen_status_snapshot(self) -> None:
        clock = [0.0]
        with tempfile.TemporaryDirectory() as directory:
            status_path = Path(directory) / "status.json"
            status_path.write_text(json.dumps({
                "connected": True,
                "connection_state": "connected",
                "updated_at": 11.0,
                "health": 10,
                "inventory": {"iron_sword": 1},
                "hostiles_nearby": [],
                "position": {"x": 0.5, "y": 100, "z": 0.5},
                "survival_controller": {"updated_at": 11.0},
            }), encoding="utf-8")

            def fake_run(command, **_kwargs):
                if command[-1] == "container123":
                    return SimpleNamespace(returncode=0, stdout="true\n", stderr="")
                return SimpleNamespace(returncode=1, stdout="", stderr="")

            result = _wait_for_emergency_admission(
                status_path,
                "container123",
                4.1,
                centered=False,
                minimum_stable_seconds=4.0,
                minimum_fresh_samples=2,
                after_updated_at=10.0,
                command_runner=fake_run,
                monotonic=lambda: clock[0],
                sleeper=lambda seconds: clock.__setitem__(0, clock[0] + seconds),
            )

        self.assertIsNone(result)

    def test_emergency_admission_failure_is_infrastructure_invalid_and_cleans_cage(self) -> None:
        commands = []

        class Server:
            def command(self, command):
                commands.append(command)

            def query_tagged_count(self):
                return 0

            def query_result(self, _tail):
                return 1

        ready = {
            "connected": True,
            "connection_state": "connected",
            "survival_controller": {"updated_at": 1.0},
        }
        with tempfile.TemporaryDirectory() as directory, patch(
            "tools.voyager.combat_matrix._start_bot_container",
            return_value="abc123def456",
        ), patch(
            "tools.voyager.combat_matrix._wait_for_connected_status",
            return_value=ready,
        ), patch(
            "tools.voyager.combat_matrix._wait_for_emergency_admission",
            return_value=None,
        ), patch(
            "tools.voyager.combat_matrix._remove_owned_container",
        ):
            observation = run_scenario_cell(
                emergency_zombie_scenario(),
                SCRIPT_REPO_ROOT,
                Path(directory),
                "run123",
                Server(),
            )

        result = evaluate_scenario(emergency_zombie_scenario(), observation)
        self.assertEqual(observation["infrastructure_code"], "emergency_admission_unverified")
        self.assertIn("infrastructure_invalid", result["failure_codes"])
        self.assertNotIn("summon minecraft:zombie 3.5 100 3.5", commands)
        self.assertEqual(commands[-3:], [
            "kill @e[tag=evelyn_matrix]",
            "fill -3 99 -3 4 103 4 minecraft:air",
            "fill -3 99 -3 4 99 4 minecraft:stone",
        ])

        class CleanupServer:
            def query_tagged_count(self):
                return 0

            def query_result(self, _tail):
                return 1

        self.assertTrue(verify_emergency_cleanup(CleanupServer()))

    def test_emergency_cell_baselines_only_after_settle_then_spawns(self) -> None:
        scenario = emergency_zombie_scenario()
        events = []

        class Server:
            def __init__(self):
                self.tagged = 0

            def command(self, command):
                events.append(command)
                if command == emergency_zombie_spawn_command():
                    self.tagged = 1
                elif command == "kill @e[tag=evelyn_matrix]":
                    self.tagged = 0

            def query_tagged_count(self):
                return self.tagged

            def query_result(self, tail):
                if tail == "run time query daytime":
                    return 13_000
                if tail == "run data get entity Evelyn_0428 Health 100":
                    return 1_000
                if "max_health base get 100" in tail:
                    return 2_000
                return 1

        ready = {
            "connected": True,
            "connection_state": "connected",
            "updated_at": 1.0,
            "survival_controller": {"updated_at": 1.0, "last_reflex_at": 1.0},
        }
        settled = {
            **ready,
            "updated_at": 10.0,
            "survival_controller": {"updated_at": 10.0, "last_reflex_at": 5.0},
        }
        baseline = {
            **ready,
            "updated_at": 12.0,
            "survival_controller": {"updated_at": 12.0, "last_reflex_at": 6.0},
        }
        server = Server()

        def observe(*args, **_kwargs):
            server.tagged = 0
            return (
                {"survival_controller": {}},
                [
                    {
                        "outcome": "interrupted",
                        "verified": False,
                        "tactic": "disengage",
                        "damage": 0,
                        "durationMs": 100,
                    },
                    {
                        "outcome": "success",
                        "verified": True,
                        "tactic": "melee",
                        "damage": 0,
                        "durationMs": 1_000,
                    },
                ],
                {
                    "death_count": 0,
                    "reflex_reason": "hostile",
                    "reflex_action": 1,
                    "reflex_durations": [100],
                    "wake": 116,
                    "action": 1,
                    "min_stable_distance_meters": None,
                    "safe_stable_ms": 0,
                },
            )

        with tempfile.TemporaryDirectory() as directory, patch(
            "tools.voyager.combat_matrix._start_bot_container",
            return_value="abc123def456",
        ), patch(
            "tools.voyager.combat_matrix._wait_for_connected_status",
            return_value=ready,
        ), patch(
            "tools.voyager.combat_matrix._wait_for_emergency_admission",
            side_effect=[settled, baseline],
        ) as wait_admission, patch(
            "tools.voyager.combat_matrix._observe_terminal_episode",
            side_effect=observe,
        ) as observe_episode, patch(
            "tools.voyager.combat_matrix._remove_owned_container",
        ):
            observation = run_scenario_cell(
                scenario,
                SCRIPT_REPO_ROOT,
                Path(directory),
                "run123",
                server,
            )

        self.assertTrue(evaluate_scenario(scenario, observation)["passed"])
        self.assertEqual(wait_admission.call_count, 2)
        self.assertEqual(
            wait_admission.call_args_list[0].kwargs["minimum_stable_seconds"],
            4.0,
        )
        self.assertEqual(
            wait_admission.call_args_list[0].kwargs["minimum_fresh_samples"],
            2,
        )
        self.assertEqual(
            wait_admission.call_args_list[0].kwargs["after_updated_at"],
            1.0,
        )
        self.assertEqual(
            wait_admission.call_args_list[1].kwargs["minimum_fresh_samples"],
            2,
        )
        self.assertEqual(observe_episode.call_args.args[4:6], (12.0, 6.0))
        self.assertIn(emergency_zombie_spawn_command(), events)

    def test_12111_base_gamerules_use_exact_names_and_are_verified(self) -> None:
        commands = base_server_commands()
        expected = {
            "gamerule spawn_mobs false",
            "gamerule natural_health_regeneration false",
            "gamerule advance_time false",
            "gamerule advance_weather false",
            "gamerule mob_drops false",
            "gamerule mob_griefing false",
            "gamerule keep_inventory true",
        }
        self.assertTrue(expected.issubset(commands))
        self.assertFalse(any(name in "\n".join(commands) for name in (
            "doMobSpawning", "naturalRegeneration", "doDaylightCycle",
            "doWeatherCycle", "doMobLoot", "mobGriefing", "keepInventory",
        )))

        class Server:
            def __init__(self, wrong_rule=None):
                self.wrong_rule = wrong_rule

            def query_tagged_count(self):
                return 0

            def query_result(self, tail):
                if tail == "run time query daytime":
                    return 6_000
                if tail.startswith("run gamerule "):
                    rule = tail.rsplit(" ", 1)[-1]
                    value = 1 if rule == "keep_inventory" else 0
                    return 1 - value if rule == self.wrong_rule else value
                return 1

        self.assertTrue(verify_base_server_setup(Server()))
        self.assertFalse(verify_base_server_setup(Server("spawn_mobs")))

    def test_protected_inventory_probe_uses_non_destructive_clear_count(self) -> None:
        scenario = next(
            item for item in smoke_scenarios()
            if item.id == "single_skeleton__protected__day"
        )
        queries = []

        class Server:
            def query_tagged_count(self):
                return 1

            def query_result(self, tail):
                queries.append(tail)
                if tail == "run time query daytime":
                    return 6_000
                return 1

        self.assertTrue(verify_scenario_setup(Server(), scenario))
        self.assertIn("run clear Evelyn_0428 minecraft:bow 0", queries)
        self.assertIn("run clear Evelyn_0428 minecraft:arrow 0", queries)
        self.assertFalse(any("inventory.*" in query for query in queries))

    def test_run_preflight_fakes_docker_and_rejects_busy_ports(self) -> None:
        calls = []

        def fake_run(command, **_kwargs):
            calls.append(tuple(command))
            if tuple(command[-2:]) == ("--format", "{{.State.Running}}"):
                return SimpleNamespace(returncode=1, stdout="", stderr="")
            if command[:3] == ["docker", "inspect", "--format"]:
                return SimpleNamespace(returncode=0, stdout="false\n", stderr="")
            if command[:2] == ["docker", "inspect"]:
                return SimpleNamespace(returncode=1, stdout="", stderr="")
            return SimpleNamespace(returncode=0, stdout="", stderr="")

        with tempfile.TemporaryDirectory() as directory:
            jar = Path(directory) / "server.jar"
            jar.write_bytes(b"fixture")
            artifact = SCRIPT_REPO_ROOT / FIXTURE_RELATIVE
            preflight_run(
                SCRIPT_REPO_ROOT,
                artifact,
                jar,
                sys.executable,
                command_runner=fake_run,
                port_probe=lambda _port: False,
                artifact_exists=lambda _path: False,
            )
            self.assertIn(("docker", "info"), calls)
            with self.assertRaisesRegex(MatrixSafetyError, "port_in_use"):
                preflight_run(
                    SCRIPT_REPO_ROOT,
                    artifact,
                    jar,
                    sys.executable,
                    command_runner=fake_run,
                    port_probe=lambda port: port == GAME_PORT,
                    artifact_exists=lambda _path: False,
                )

    def test_docker_contract_has_owned_name_labels_and_only_cell_mount(self) -> None:
        command = docker_run_command(
            SCRIPT_REPO_ROOT,
            SCRIPT_REPO_ROOT / "runtime_artifacts/validation/combat_matrix_batch/cells/a/bot",
            "run123",
        )
        rendered = "\n".join(command)
        self.assertIn("evelyn-combat-matrix-batch", command)
        self.assertIn("evelyn.validation.owner=combat_matrix_batch", command)
        self.assertIn("evelyn.validation.run=run123", command)
        self.assertIn("target=/app/runtime_artifacts", rendered)
        self.assertIn("target=/run/evelyn-auth-seed,readonly", rendered)
        self.assertIn("/app/bot_profiles:rw,noexec,nosuid,nodev,mode=0700", command)
        self.assertIn("cp -R /run/evelyn-auth-seed/. /app/bot_profiles/", command[-1])
        self.assertIn("exec node main.js", command[-1])
        self.assertNotIn("25565", rendered)

    def test_smoke_batch_uses_one_owned_server_two_cells_and_finally_stops(self) -> None:
        events = []

        class FakeServer:
            def command(self, command):
                events.append(("command", command))

            def query_tagged_count(self):
                events.append(("server", "query"))
                return 0

            def query_result(self, tail):
                events.append(("server", "query_result"))
                if tail.startswith("run gamerule "):
                    return 1 if tail.endswith("keep_inventory") else 0
                if tail == "run time query daytime":
                    return 6_000
                return 1

            def stop(self):
                events.append(("server", "stop"))

        def fake_preflight(*_args, **_kwargs):
            events.append(("preflight", "ok"))

        def fake_server_factory(*_args, **_kwargs):
            events.append(("server", "start"))
            return FakeServer()

        def fake_cell(scenario, *_args, **_kwargs):
            events.append(("cell", scenario.id))
            return passing_observation(scenario)

        def fake_run(command, **_kwargs):
            return SimpleNamespace(
                returncode=0 if command == ["docker", "info"] else 1,
                stdout="",
                stderr="",
            )

        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            artifact = root / "combat_matrix_batch"
            jar = root / "server.jar"
            jar.write_bytes(b"fixture")
            report = run_batch(
                root,
                artifact,
                jar,
                sys.executable,
                smoke=True,
                command_runner=fake_run,
                port_probe=lambda _port: False,
                preflight=fake_preflight,
                server_factory=fake_server_factory,
                cell_runner=fake_cell,
            )
            self.assertTrue(report["passed"])
            self.assertEqual(report["summary"]["total"], 2)
            self.assertEqual(len([event for event in events if event[0] == "cell"]), 2)
            self.assertEqual(events[-1], ("server", "stop"))
            self.assertTrue((artifact / "report.json").is_file())

    def test_projectile_batch_selects_only_projectile_cell_and_verifies_cleanup(self) -> None:
        events = []

        class FakeServer:
            def command(self, command):
                events.append(("command", command))

            def query_tagged_count(self):
                return 0

            def query_result(self, tail):
                if tail.startswith("run gamerule "):
                    return 1 if tail.endswith("keep_inventory") else 0
                if tail == "run time query daytime":
                    return 6_000
                return 1

            def stop(self):
                events.append(("server", "stop"))

        def fake_projectile_cell(*_args, **_kwargs):
            events.append(("cell", "projectile"))
            return {
                "infrastructure_valid": True,
                "runtime_error": None,
                "death_count": 0,
                "reflex_reason": "projectile",
                "reflex_to_action_ms": 1,
                "response": "shield",
                "shield_blocked_damage": 30,
                "damage": 0,
                "hostile_count": 0,
            }

        def forbidden_combat_cell(*_args, **_kwargs):
            self.fail("combat cell must not run in projectile-smoke mode")

        def fake_run(command, **_kwargs):
            return SimpleNamespace(
                returncode=0 if command == ["docker", "info"] else 1,
                stdout="",
                stderr="",
            )

        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            artifact = root / "combat_matrix_batch"
            jar = root / "server.jar"
            jar.write_bytes(b"fixture")
            report = run_batch(
                root,
                artifact,
                jar,
                sys.executable,
                projectile_smoke=True,
                command_runner=fake_run,
                port_probe=lambda _port: False,
                preflight=lambda *_args, **_kwargs: None,
                server_factory=lambda *_args, **_kwargs: FakeServer(),
                cell_runner=forbidden_combat_cell,
                projectile_cell_runner=fake_projectile_cell,
            )
        self.assertTrue(report["passed"])
        self.assertTrue(report["cleanupVerified"])
        self.assertEqual(report["mode"], "projectile_smoke")
        self.assertEqual(report["summary"]["total"], 1)
        self.assertEqual(events[-1], ("server", "stop"))

    def test_emergency_zombie_batch_reuses_one_owned_cell_and_cleanup(self) -> None:
        events = []

        class FakeServer:
            def command(self, command):
                events.append(("command", command))

            def query_tagged_count(self):
                return 0

            def query_result(self, tail):
                if tail.startswith("run gamerule "):
                    return 1 if tail.endswith("keep_inventory") else 0
                if tail == "run time query daytime":
                    return 6_000
                return 1

            def stop(self):
                events.append(("server", "stop"))

        def fake_cell(scenario, *_args, **_kwargs):
            events.append(("cell", scenario.id))
            return passing_observation(scenario)

        def fake_run(command, **_kwargs):
            return SimpleNamespace(
                returncode=0 if command == ["docker", "info"] else 1,
                stdout="",
                stderr="",
            )

        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            artifact = root / "combat_matrix_batch"
            jar = root / "server.jar"
            jar.write_bytes(b"fixture")
            report = run_batch(
                root,
                artifact,
                jar,
                sys.executable,
                emergency_zombie_smoke=True,
                command_runner=fake_run,
                port_probe=lambda _port: False,
                preflight=lambda *_args, **_kwargs: None,
                server_factory=lambda *_args, **_kwargs: FakeServer(),
                cell_runner=fake_cell,
            )

        self.assertTrue(report["passed"])
        self.assertTrue(report["cleanupVerified"])
        self.assertEqual(report["mode"], "emergency_zombie_smoke")
        self.assertEqual(report["summary"]["total"], 1)
        self.assertEqual(
            [event for event in events if event[0] == "cell"],
            [("cell", EMERGENCY_ZOMBIE_CELL_ID)],
        )
        self.assertEqual(events[-1], ("server", "stop"))

    def test_emergency_zombie_cli_forwards_only_the_owned_smoke_mode(self) -> None:
        payload = {
            "contentFree": True,
            "liveExecution": True,
            "passed": True,
            "cleanupVerified": True,
        }
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            jar = root / "server.jar"
            artifact = root / "artifact"
            jar.write_bytes(b"fixture")
            output = io.StringIO()
            with patch(
                "tools.voyager.combat_matrix.run_batch",
                return_value=payload,
            ) as runner, redirect_stdout(output):
                exit_code = main([
                    "--run",
                    "--emergency-zombie-smoke",
                    "--server-jar", str(jar),
                    "--java", sys.executable,
                    "--artifact-root", str(artifact),
                ])

        self.assertEqual(exit_code, 0)
        self.assertEqual(json.loads(output.getvalue()), payload)
        kwargs = runner.call_args.kwargs
        self.assertTrue(kwargs["emergency_zombie_smoke"])
        self.assertFalse(kwargs["smoke"])
        self.assertFalse(kwargs["projectile_smoke"])

    def test_report_is_content_free_and_drops_raw_observation_fields(self) -> None:
        observations = {}
        for scenario in build_scenarios():
            observation = passing_observation(scenario)
            observation.update({
                "player_name": "secret-player",
                "position": {"x": 1, "y": 2, "z": 3},
                "raw_log": "secret transcript",
            })
            observations[scenario.id] = observation
        report = build_report(observations)
        serialized = json.dumps(report)
        self.assertTrue(report["contentFree"])
        self.assertTrue(report["passed"])
        self.assertNotIn("secret-player", serialized)
        self.assertNotIn("secret transcript", serialized)
        self.assertNotIn('"position"', serialized)

    def test_cleanup_targets_are_exact_and_unsafe_roots_or_ports_fail_closed(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "runtime_artifacts/validation").mkdir(parents=True)
            plan = cleanup_plan(root)
            self.assertEqual(plan["container_names"], [CONTAINER_NAME])
            self.assertEqual(plan["artifact_roots"], [FIXTURE_RELATIVE.as_posix()])
            self.assertEqual(plan["ports"], [GAME_PORT])
            self.assertFalse(plan["wildcards"])
            self.assertNotIn("evelyn-mindcraft", json.dumps(plan))
            self.assertNotIn("*", json.dumps(plan))
            with self.assertRaises(MatrixSafetyError):
                cleanup_plan(root, artifact_root=root / "runtime_artifacts")
            with self.assertRaises(MatrixSafetyError):
                cleanup_plan(root, game_port=25565)


if __name__ == "__main__":
    unittest.main()
