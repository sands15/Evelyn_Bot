from __future__ import annotations

import asyncio
import io
import json
import sys
import tempfile
import threading
import unittest
from pathlib import Path
from unittest.mock import Mock, patch


REPO_ROOT = next(path for path in Path(__file__).resolve().parents if (path / "main.py").exists())
RUNTIME_ROOT = REPO_ROOT / "evelyn_core" / "runtime"
if str(RUNTIME_ROOT) not in sys.path:
    sys.path.insert(0, str(RUNTIME_ROOT))

from evelyn_core import mindcraft_service  # noqa: E402


class MindcraftRuntimeContractTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp_dir.cleanup)
        self.world_action_lock_path = (
            Path(self.temp_dir.name) / "world_action.lock"
        )
        self.process_identity_path = (
            Path(self.temp_dir.name) / "process_identity.json"
        )
        self.child_runtime_path = (
            Path(self.temp_dir.name) / "child_runtime.json"
        )
        for active_patch in (
            patch.object(
                mindcraft_service,
                "WORLD_ACTION_LOCK_PATH",
                self.world_action_lock_path,
            ),
            patch.object(
                mindcraft_service,
                "MINDCRAFT_PROCESS_IDENTITY_PATH",
                self.process_identity_path,
            ),
            patch.object(
                mindcraft_service,
                "MINDCRAFT_CHILD_RUNTIME_PATH",
                self.child_runtime_path,
            ),
        ):
            active_patch.start()
            self.addCleanup(active_patch.stop)

    def test_default_goal_targets_the_ender_dragon_with_survival_prerequisites(self) -> None:
        goal = mindcraft_service.DEFAULT_GOAL
        self.assertIn("Defeat the Ender Dragon", goal)
        self.assertIn("Nether access", goal)
        self.assertIn("Eyes of Ender", goal)
        self.assertIn("stronghold", goal)
        self.assertIn("Never use slash commands", goal)

    def test_settings_enforce_non_operator_survival(self) -> None:
        runtime = mindcraft_service.MindcraftRuntime()

        settings = runtime._settings("survive normally")

        self.assertEqual(settings["base_profile"], "survival")
        self.assertFalse(settings["allow_insecure_coding"])
        self.assertFalse(settings["allow_vision"])
        self.assertFalse(settings["render_bot_view"])
        self.assertFalse(settings["chat_ingame"])
        self.assertFalse(settings["narrate_behavior"])
        self.assertEqual(settings["max_commands"], -1)
        self.assertIn("!newAction", settings["blocked_actions"])
        self.assertIn("!setMode", settings["blocked_actions"])
        self.assertNotIn("!attack", settings["blocked_actions"])
        self.assertIn("!attackPlayer", settings["blocked_actions"])
        self.assertIn("!digDown", settings["blocked_actions"])

    def test_live_start_keeps_running_goal_and_effect_binding(self) -> None:
        goal_path = Path(self.temp_dir.name) / "goal.json"
        runtime = mindcraft_service.MindcraftRuntime()
        process = Mock()
        process.poll.return_value = None
        runtime._process = process
        runtime._manual_stop = False
        runtime._world_effect_binding = {"grantId": "existing"}

        with patch.object(mindcraft_service, "GOAL_STATE_PATH", goal_path):
            runtime.persist_goal("collect oak logs")
            runtime.start(
                "find a village",
                world_effect_binding={"grantId": "replacement"},
            )

            self.assertEqual(runtime.get_goal(), "collect oak logs")

        self.assertIs(runtime._process, process)
        self.assertEqual(runtime._world_effect_binding, {"grantId": "existing"})
        self.assertFalse(runtime._manual_stop)

    def test_goal_restart_does_not_relabel_when_stop_fails(self) -> None:
        goal_path = Path(self.temp_dir.name) / "goal.json"
        runtime = mindcraft_service.MindcraftRuntime()
        process = Mock()
        process.poll.return_value = None
        process.terminate.side_effect = OSError("synthetic stop failure")
        runtime._process = process
        runtime._manual_stop = False

        with patch.object(mindcraft_service, "GOAL_STATE_PATH", goal_path):
            runtime.persist_goal("collect oak logs")

            with self.assertRaisesRegex(OSError, "synthetic stop failure"):
                runtime.restart_for_goal("find a village")

            self.assertEqual(runtime.get_goal(), "collect oak logs")

        self.assertIs(runtime._process, process)
        self.assertTrue(runtime.process_alive())
        self.assertTrue(runtime._manual_stop)

    def test_cold_runtime_does_not_auto_start_without_lease(self) -> None:
        runtime = mindcraft_service.MindcraftRuntime()
        runtime._ensure_process_running = Mock()

        with patch.object(
            mindcraft_service,
            "load_guarded_world_lease",
            return_value=(
                {},
                "minecraft_world_authorization_required",
            ),
        ):
            authorized = runtime.reconcile_world_lease()

        self.assertFalse(authorized)
        runtime._ensure_process_running.assert_not_called()
        self.assertTrue(runtime._manual_stop)

    def test_live_runner_is_stopped_when_lease_heartbeat_is_stale(
        self,
    ) -> None:
        runtime = mindcraft_service.MindcraftRuntime()
        runtime._manual_stop = False
        runtime._process = Mock()
        runtime._process.poll.return_value = None
        runtime.stop = Mock()

        with patch.object(
            mindcraft_service,
            "load_guarded_world_lease",
            return_value=(
                {},
                "minecraft_world_lease_heartbeat_stale",
            ),
        ):
            authorized = runtime.reconcile_world_lease()

        self.assertFalse(authorized)
        runtime.stop.assert_called_once_with()

    def test_live_lease_allows_only_previously_started_runtime_restart(
        self,
    ) -> None:
        runtime = mindcraft_service.MindcraftRuntime()
        runtime._ensure_process_running = Mock()

        with patch.object(
            mindcraft_service,
            "load_guarded_world_lease",
            return_value=({"active": True}, ""),
        ):
            self.assertTrue(runtime.reconcile_world_lease())

        runtime._ensure_process_running.assert_called_once_with()
        self.assertTrue(runtime._manual_stop)

    def test_reconcile_holds_action_lock_through_auto_restart_effect(
        self,
    ) -> None:
        runtime = mindcraft_service.MindcraftRuntime()
        effect_entered = threading.Event()
        allow_effect = threading.Event()
        results: list[bool] = []
        errors: list[BaseException] = []

        def ensure_process_running() -> None:
            effect_entered.set()
            if not allow_effect.wait(timeout=5.0):
                raise RuntimeError("auto-restart effect gate timed out")

        def reconcile() -> None:
            try:
                results.append(runtime.reconcile_world_lease())
            except BaseException as exc:  # pragma: no cover - asserted below
                errors.append(exc)

        runtime._ensure_process_running = ensure_process_running
        with patch.object(
            mindcraft_service,
            "load_guarded_world_lease",
            return_value=({"active": True, "processNonce": "old-epoch"}, ""),
        ):
            worker = threading.Thread(target=reconcile, daemon=True)
            worker.start()
            self.assertTrue(effect_entered.wait(timeout=5.0))

            successor = mindcraft_service.MinecraftOwnerLock(
                self.world_action_lock_path
            )
            try:
                with self.assertRaises(
                    mindcraft_service.MinecraftOwnerLockBusy
                ):
                    successor.acquire()
            finally:
                successor.release()
                allow_effect.set()
                worker.join(timeout=5.0)

        self.assertFalse(worker.is_alive())
        self.assertEqual(errors, [])
        self.assertEqual(results, [True])

    def test_shutdown_handoff_blocks_old_epoch_auto_restart(self) -> None:
        runtime = mindcraft_service.MindcraftRuntime()
        runtime._ensure_process_running = Mock()
        guarded_load = Mock(
            return_value=(
                {"active": True, "processNonce": "old-epoch"},
                "",
            )
        )
        shutdown_boundary = mindcraft_service.MinecraftOwnerLock(
            self.world_action_lock_path
        )
        shutdown_boundary.acquire()
        try:
            with patch.object(
                mindcraft_service,
                "load_guarded_world_lease",
                guarded_load,
            ):
                authorized = runtime.reconcile_world_lease()
        finally:
            shutdown_boundary.release()

        self.assertFalse(authorized)
        self.assertEqual(
            runtime._last_world_lease_error_code,
            "minecraft_world_action_lock_busy",
        )
        guarded_load.assert_not_called()
        runtime._ensure_process_running.assert_not_called()

    def test_reconcile_rejects_forged_action_lock_capability(self) -> None:
        unacquired_lock = mindcraft_service.MinecraftOwnerLock(
            self.world_action_lock_path
        )
        wrong_path_lock = mindcraft_service.MinecraftOwnerLock(
            self.world_action_lock_path.with_name("wrong.lock")
        )
        wrong_path_lock.acquire()
        self.addCleanup(wrong_path_lock.release)

        for action_lock in (unacquired_lock, wrong_path_lock):
            with self.subTest(lock_path=action_lock.path):
                runtime = mindcraft_service.MindcraftRuntime()
                runtime._manual_stop = False
                runtime.stop = Mock()
                runtime._ensure_process_running = Mock()
                guarded_load = Mock(
                    return_value=(
                        {"active": True, "processNonce": "old-epoch"},
                        "",
                    )
                )

                with patch.object(
                    mindcraft_service,
                    "load_guarded_world_lease",
                    guarded_load,
                ):
                    authorized = runtime.reconcile_world_lease(
                        world_action_lock=action_lock
                    )

                self.assertFalse(authorized)
                self.assertEqual(
                    runtime._last_world_lease_error_code,
                    "minecraft_world_action_lock_unavailable",
                )
                runtime.stop.assert_called_once_with()
                guarded_load.assert_not_called()
                runtime._ensure_process_running.assert_not_called()

    def test_status_uses_fresh_mindcraft_telemetry(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            status_path = root / "status.json"
            goal_path = root / "goal.json"
            status_path.write_text(
                json.dumps(
                    {
                        "runtime": "mindcraft",
                        "connected": True,
                        "connected_at": mindcraft_service.time.time() - 3.1,
                        "connection_state": "connected",
                        "phase": "survival",
                        "position": {"x": 1, "y": 64, "z": 2},
                        "health": 20,
                        "hunger": 18,
                        "inventory": {"oak_log": 4},
                        "hostiles_nearby": [],
                        "goal_manager": {
                            "mode": "gated",
                            "autonomy_state": "active",
                            "current_subgoal": {
                                "id": "obtain_logs",
                                "kind": "obtain",
                                "target": "#logs",
                            },
                        },
                        "task_contract": {
                            "schema": "mindcraft.task-contract.v1",
                            "ready": True,
                            "goal_manager_mode": "gated",
                            "command_gate": "evelyn_goal_manager",
                            "effect_verification": "explicit_postcondition",
                        },
                        "updated_at": mindcraft_service.time.time(),
                    }
                ),
                encoding="utf-8",
            )
            goal_path.write_text(json.dumps({"goal": "collect wood"}), encoding="utf-8")
            runtime = mindcraft_service.MindcraftRuntime()
            runtime._process = Mock()
            runtime._process.poll.return_value = None

            with (
                patch.object(mindcraft_service, "STATUS_PATH", status_path),
                patch.object(mindcraft_service, "GOAL_STATE_PATH", goal_path),
                patch.object(
                    mindcraft_service,
                    "load_guarded_world_lease",
                    return_value=({"active": True}, ""),
                ),
            ):
                payload = runtime.build_status()

        self.assertEqual(payload["runtime"], "mindcraft")
        self.assertTrue(payload["running"])
        self.assertTrue(payload["connected"])
        self.assertEqual(payload["command_policy"], "outbound_chat_disabled_by_default")
        self.assertEqual(payload["observation"]["inventory"]["oak_log"], 4)
        self.assertEqual(payload["current_subgoal"]["id"], "obtain_logs")
        self.assertEqual(payload["current_task_stage"], "obtain_logs")
        self.assertEqual(payload["configuration"]["schema"], "runtime_config.owner.v1")
        self.assertEqual(payload["errorCount"], 0)
        self.assertEqual(
            payload["child_runtime"],
            {
                "schema": "mindcraft_runtime.child-events.v1",
                "events": [],
                "updated_at": None,
                "content_free": True,
            },
        )
        readiness = payload["functional_readiness"]
        self.assertEqual(
            readiness["schema"],
            "minecraft_autonomy.readiness.v1",
        )
        self.assertTrue(readiness["ready"])
        self.assertEqual(readiness["state"], "ready")
        self.assertEqual(readiness["blockers"], [])
        self.assertTrue(
            all(readiness["dependencies"].values())
        )

    def test_status_projection_drops_legacy_raw_content(self) -> None:
        canary = "PRIVATE_MINDCRAFT_STATUS_CANARY"
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            status_path = root / "status.json"
            goal_path = root / "goal.json"
            status_path.write_text(
                json.dumps(
                    {
                        "runtime": "mindcraft",
                        "connected": True,
                        "connected_at": mindcraft_service.time.time() - 3.1,
                        "connection_state": "connected",
                        "phase": "survival",
                        "last_error": canary,
                        "last_blocked_command": "outbound_chat_disabled",
                        "private_status": canary,
                        "goal_manager": {
                            "mode": "gated",
                            "autonomy_state": "active",
                            "ultimate_goal": canary,
                            "current_subgoal": {
                                "id": "obtain_logs",
                                "target": canary,
                            },
                            "last_execution": {"result": canary},
                        },
                        "survival_controller": {
                            "last_error": canary,
                            "snapshot": {"private": canary},
                        },
                        "task_contract": {
                            "schema": "mindcraft.task-contract.v1",
                            "ready": True,
                            "goal_manager_mode": "gated",
                            "command_gate": "evelyn_goal_manager",
                            "effect_verification": "explicit_postcondition",
                        },
                        "updated_at": mindcraft_service.time.time(),
                    }
                ),
                encoding="utf-8",
            )
            goal_path.write_text(json.dumps({"goal": "collect wood"}), encoding="utf-8")
            runtime = mindcraft_service.MindcraftRuntime(
                process_identity_path=root / "process-identity.json"
            )
            runtime._process = Mock()
            runtime._process.poll.return_value = None

            with (
                patch.object(mindcraft_service, "STATUS_PATH", status_path),
                patch.object(mindcraft_service, "GOAL_STATE_PATH", goal_path),
                patch.object(runtime, "reconcile_world_lease", return_value=True),
                patch.object(mindcraft_service, "_effect_observer_ready", return_value=True),
            ):
                payload = runtime.build_status()
                runtime._process = None
                with patch.object(runtime, "_write_process_identity"):
                    runtime.stop()
                persisted = json.loads(status_path.read_text(encoding="utf-8"))

        self.assertTrue(payload["functional_readiness"]["ready"])
        self.assertEqual(payload["current_subgoal"], {"id": "obtain_logs"})
        self.assertEqual(payload["last_blocked_command"], "outbound_chat_disabled")
        self.assertIsNone(payload["last_error"])
        self.assertIsNone(payload["survival_controller"])
        self.assertNotIn("survival_controller", persisted)
        self.assertNotIn(canary, json.dumps(payload, sort_keys=True))
        self.assertNotIn(canary, json.dumps(persisted, sort_keys=True))

    def test_content_free_survival_controller_is_projected(self) -> None:
        canary = "PRIVATE_SURVIVAL_STATE_CANARY"
        projected = mindcraft_service._project_mindcraft_telemetry(
            {
                "survival_controller": {
                    "phase": "shelter_until_safe_dawn",
                    "last_decision": "shelter_until_safe_dawn",
                    "last_success": False,
                    "last_error": "survival_action_failed",
                    "recovery_progress": True,
                    "recovery_handoff_until": 123.5,
                    "wake_reason": "projectile",
                    "wake_to_decision_ms": 17.4,
                    "decision_to_action_ms": 2,
                    "reflex_reason": "projectile",
                    "reflex_to_action_ms": 1.6,
                    "bootstrap_phase": "candidate_unreached",
                    "bootstrap_candidate_count": 1,
                    "bootstrap_logs_before": 2,
                    "bootstrap_logs_after": 2,
                    "shelter_success_count": 7,
                    "last_reflex_at": 455.25,
                    "updated_at": 456.5,
                    "content_free": True,
                    "snapshot": {"private": canary},
                }
            }
        )

        self.assertEqual(
            projected["survival_controller"],
            {
                "phase": "shelter_until_safe_dawn",
                "last_decision": "shelter_until_safe_dawn",
                "last_success": False,
                "last_error": "survival_action_failed",
                "recovery_progress": True,
                "recovery_handoff_until": 123.5,
                "wake_reason": "projectile",
                "wake_to_decision_ms": 17,
                "decision_to_action_ms": 2,
                "reflex_reason": "projectile",
                "reflex_to_action_ms": 2,
                "bootstrap_phase": "candidate_unreached",
                "bootstrap_candidate_count": 1,
                "bootstrap_logs_before": 2,
                "bootstrap_logs_after": 2,
                "shelter_success_count": 7,
                "last_reflex_at": 455.25,
                "updated_at": 456.5,
                "content_free": True,
            },
        )
        self.assertNotIn(canary, json.dumps(projected, sort_keys=True))

        rejected = mindcraft_service._project_mindcraft_telemetry(
            {
                "survival_controller": {
                    "bootstrap_phase": canary,
                    "bootstrap_candidate_count": 5,
                    "bootstrap_logs_before": -1,
                    "bootstrap_logs_after": 65,
                    "shelter_success_count": -1,
                    "content_free": True,
                }
            }
        )["survival_controller"]
        self.assertIsNone(rejected["bootstrap_phase"])
        self.assertIsNone(rejected["bootstrap_candidate_count"])
        self.assertIsNone(rejected["bootstrap_logs_before"])
        self.assertIsNone(rejected["bootstrap_logs_after"])
        self.assertIsNone(rejected["shelter_success_count"])
        self.assertNotIn(canary, json.dumps(rejected, sort_keys=True))

    def test_child_output_drain_persists_only_bounded_fixed_categories(self) -> None:
        canary = "PRIVATE_PROMPT_COORDINATE_CANARY x=123 z=456"
        runtime = mindcraft_service.MindcraftRuntime(
            child_runtime_path=self.child_runtime_path
        )
        generation = runtime._reset_child_runtime()
        process = Mock()
        process.stdout = io.BytesIO(
            "\n".join(
                (
                    f"Error in spawn event: {canary}",
                    f"at Agent.requestInterrupt (/private/agent.js:251) {canary}",
                    f"UnhandledPromiseRejectionWarning: {canary}",
                    f"uncaughtException: {canary}",
                    f"Code execution refused stop after 10 seconds. {canary}",
                    f"Infinite action loop detected, shutting down. {canary}",
                    f"[LoginGuard] Disconnected: {canary}",
                    f"[LoginGuard] Kicked: {canary}",
                    "Agent process exited with code 1 and signal null",
                )
            ).encode("utf-8")
        )

        runtime._drain_child_output(process, generation)

        persisted = json.loads(self.child_runtime_path.read_text(encoding="utf-8"))
        self.assertEqual(
            [event["category"] for event in persisted["events"]],
            [
                "spawn_event_failed",
                "interrupt_plugin_not_ready",
                "unhandled_rejection",
                "uncaught_exception",
                "action_stop_timeout",
                "rapid_loop",
                "mineflayer_end",
                "mineflayer_kicked",
                "child_exit",
            ],
        )
        self.assertEqual(persisted["events"][-1]["exit_code"], 1)
        self.assertIsNone(persisted["events"][-1]["signal"])
        self.assertEqual(
            mindcraft_service._classify_mindcraft_child_output(
                b"Agent process exited with code null and signal SIGTERM"
            ),
            ("child_exit", None, "SIGTERM"),
        )
        self.assertTrue(persisted["content_free"])
        self.assertNotIn(canary, json.dumps(persisted, sort_keys=True))

        generation = runtime._reset_child_runtime()
        process.stdout = io.BytesIO(
            ("Infinite action loop detected\n" * 20).encode("ascii")
        )
        runtime._drain_child_output(process, generation)
        bounded = json.loads(self.child_runtime_path.read_text(encoding="utf-8"))
        self.assertEqual(
            len(bounded["events"]),
            mindcraft_service._MINDCRAFT_CHILD_EVENT_LIMIT,
        )

    def test_device_code_marker_projects_only_valid_ephemeral_status(self) -> None:
        canary = "PRIVATE_AUTH_CANARY"
        runtime = mindcraft_service.MindcraftRuntime(
            child_runtime_path=self.child_runtime_path
        )
        generation = runtime._reset_child_runtime()
        process = Mock()
        process.poll.return_value = None
        process.stdout = io.BytesIO(
            "\n".join(
                (
                    f"[EVELYN_MSA_DEVICE_CODE] ABCD2345 900 {canary}",
                    "[EVELYN_MSA_DEVICE_CODE] ABCD-234 900",
                    "[EVELYN_MSA_DEVICE_CODE] ABCD2345 99999",
                    "[EVELYN_MSA_DEVICE_CODE] ABCD2345 900",
                )
            ).encode("ascii")
        )

        with (
            patch.object(mindcraft_service.time, "time", return_value=1_000.0),
            patch.object(mindcraft_service.time, "monotonic", return_value=100.0),
        ):
            runtime._drain_child_output(process, generation)
            runtime._process = process
            with (
                patch.object(runtime, "reconcile_world_lease", return_value=True),
                patch.object(
                    mindcraft_service,
                    "_read_mindcraft_status",
                    return_value={
                        "connected": False,
                        "updated_at": 1_000.0,
                    },
                ),
            ):
                status = runtime.build_status()

        self.assertEqual(
            status["microsoft_auth"],
            {
                "state": "device_code_pending",
                "user_code": "ABCD2345",
                "verification_url": "https://www.microsoft.com/link",
                "expires_at": 1_900.0,
            },
        )
        persisted = self.child_runtime_path.read_text(encoding="utf-8")
        self.assertNotIn("ABCD2345", persisted)
        self.assertNotIn(canary, persisted)

    def test_device_code_challenge_clears_on_expiry_and_reset(self) -> None:
        runtime = mindcraft_service.MindcraftRuntime(
            child_runtime_path=self.child_runtime_path
        )
        generation = runtime._reset_child_runtime()
        process = Mock()
        process.poll.return_value = None
        process.stdout = io.BytesIO(
            b"[EVELYN_MSA_DEVICE_CODE] ABCD2345 900\n"
        )
        with (
            patch.object(mindcraft_service.time, "time", return_value=1_000.0),
            patch.object(mindcraft_service.time, "monotonic", return_value=100.0),
        ):
            runtime._drain_child_output(process, generation)

        with patch.object(
            mindcraft_service.time,
            "monotonic",
            return_value=1_000.0,
        ):
            self.assertIsNone(
                runtime._microsoft_auth_challenge_snapshot(
                    running=True,
                    connected=False,
                )
            )

        generation = runtime._reset_child_runtime()
        process.stdout = io.BytesIO(
            b"[EVELYN_MSA_DEVICE_CODE] WXYZ6789 900\n"
        )
        with (
            patch.object(mindcraft_service.time, "time", return_value=2_000.0),
            patch.object(mindcraft_service.time, "monotonic", return_value=200.0),
        ):
            runtime._drain_child_output(process, generation)
        runtime._reset_child_runtime()
        self.assertIsNone(
            runtime._microsoft_auth_challenge_snapshot(
                running=True,
                connected=False,
            )
        )

    def test_device_code_challenge_clears_when_telemetry_connects(self) -> None:
        runtime = mindcraft_service.MindcraftRuntime(
            child_runtime_path=self.child_runtime_path
        )
        generation = runtime._reset_child_runtime()
        process = Mock()
        process.poll.return_value = None
        process.stdout = io.BytesIO(
            b"[EVELYN_MSA_DEVICE_CODE] ABCD2345 900\n"
        )
        with (
            patch.object(mindcraft_service.time, "time", return_value=1_000.0),
            patch.object(mindcraft_service.time, "monotonic", return_value=100.0),
        ):
            runtime._drain_child_output(process, generation)
            runtime._process = process
            with (
                patch.object(runtime, "reconcile_world_lease", return_value=True),
                patch.object(
                    mindcraft_service,
                    "_read_mindcraft_status",
                    return_value={
                        "connected": True,
                        "connected_at": 997.0,
                        "updated_at": 1_000.0,
                    },
                ),
            ):
                status = runtime.build_status()

        self.assertNotIn("microsoft_auth", status)
        self.assertIsNone(
            runtime._microsoft_auth_challenge_snapshot(
                running=True,
                connected=False,
            )
        )

    def test_child_output_pipe_is_drained_without_raw_retention(self) -> None:
        canary = "PRIVATE_PIPE_CANARY"
        runtime = mindcraft_service.MindcraftRuntime(
            child_runtime_path=self.child_runtime_path
        )
        generation = runtime._reset_child_runtime()
        process = mindcraft_service.subprocess.Popen(
            [
                sys.executable,
                "-c",
                (
                    "import sys;"
                    "sys.stdout.buffer.write(b'x' * 262144);"
                    f"sys.stdout.buffer.write(b'\\nError in spawn event: {canary}\\n');"
                    "sys.stdout.buffer.flush()"
                ),
            ],
            stdout=mindcraft_service.subprocess.PIPE,
            stderr=mindcraft_service.subprocess.STDOUT,
        )
        try:
            runtime._start_child_output_drain(process, generation)
            drain = runtime._child_output_thread
            self.assertIsNotNone(drain)
            process.wait(timeout=5)
            drain.join(timeout=5)
            self.assertFalse(drain.is_alive())
        finally:
            if process.poll() is None:
                process.kill()
                process.wait(timeout=5)

        persisted = json.loads(self.child_runtime_path.read_text(encoding="utf-8"))
        self.assertEqual(
            [event["category"] for event in persisted["events"]],
            ["spawn_event_failed", "child_exit"],
        )
        self.assertEqual(persisted["events"][-1]["exit_code"], 0)
        self.assertNotIn(canary, json.dumps(persisted, sort_keys=True))

    def test_connection_readiness_requires_three_stable_seconds_and_no_fatal_child_event(
        self,
    ) -> None:
        telemetry = {"connected": True, "connected_at": 100.0}
        empty_child_runtime = {
            "events": [],
            "content_free": True,
        }
        self.assertFalse(
            mindcraft_service._stable_minecraft_connection(
                running=True,
                telemetry_fresh=True,
                telemetry=telemetry,
                child_runtime=empty_child_runtime,
                now=102.9,
            )
        )
        self.assertTrue(
            mindcraft_service._stable_minecraft_connection(
                running=True,
                telemetry_fresh=True,
                telemetry=telemetry,
                child_runtime=empty_child_runtime,
                now=103.0,
            )
        )
        self.assertFalse(
            mindcraft_service._stable_minecraft_connection(
                running=True,
                telemetry_fresh=True,
                telemetry=telemetry,
                child_runtime={
                    "events": [
                        {"category": "interrupt_plugin_not_ready"}
                    ]
                },
                now=110.0,
            )
        )
        self.assertFalse(
            mindcraft_service._stable_minecraft_connection(
                running=True,
                telemetry_fresh=True,
                telemetry={"connected": True},
                child_runtime=empty_child_runtime,
                now=110.0,
            )
        )

    def test_status_blocks_http_only_or_unverified_task_runtime(
        self,
    ) -> None:
        readiness = mindcraft_service._functional_readiness(
            world_lease_authorized=True,
            running=True,
            telemetry_fresh=True,
            connected=True,
            effect_observer_ready=True,
            telemetry={
                "goal_manager": {
                    "mode": "shadow",
                    "autonomy_state": "active",
                },
                "task_contract": {
                    "schema": "mindcraft.task-contract.v1",
                    "ready": False,
                    "goal_manager_mode": "shadow",
                    "command_gate": "evelyn_goal_manager",
                    "effect_verification": "explicit_postcondition",
                },
            },
        )

        self.assertFalse(readiness["ready"])
        self.assertEqual(readiness["state"], "blocked")
        self.assertEqual(
            readiness["blockers"],
            ["task_contract_unavailable"],
        )

    def test_status_reports_each_functional_dependency_fail_closed(
        self,
    ) -> None:
        readiness = mindcraft_service._functional_readiness(
            world_lease_authorized=False,
            running=False,
            telemetry_fresh=False,
            connected=False,
            effect_observer_ready=False,
            telemetry={},
        )

        self.assertFalse(readiness["ready"])
        self.assertEqual(readiness["state"], "blocked")
        self.assertEqual(
            readiness["blockers"],
            [
                "world_lease_unauthorized",
                "runner_not_alive",
                "telemetry_stale",
                "minecraft_not_connected",
                "task_contract_unavailable",
                "effect_observer_unavailable",
                "autonomy_not_active",
            ],
        )

    def test_health_exposes_liveness_and_functional_readiness(
        self,
    ) -> None:
        functional_readiness = {
            "schema": "minecraft_autonomy.readiness.v1",
            "state": "starting",
            "ready": False,
            "blockers": ["minecraft_not_connected"],
        }
        with patch.object(
            mindcraft_service.STATE,
            "build_status",
            return_value={
                "running": True,
                "functional_readiness": functional_readiness,
            },
        ):
            response = asyncio.run(mindcraft_service.health(Mock()))

        payload = json.loads(response.text)
        self.assertTrue(payload["ok"])
        self.assertTrue(payload["runner_alive"])
        self.assertEqual(
            payload["functional_readiness"],
            functional_readiness,
        )

    def test_stop_failure_is_counted_without_storing_exception_text(self) -> None:
        runtime = mindcraft_service.MindcraftRuntime()
        runtime._process = Mock()
        runtime._process.poll.return_value = None
        runtime._process.terminate.side_effect = OSError("private host path")

        with self.assertRaises(OSError):
            runtime.stop()

        snapshot = runtime.runtime_errors.snapshot()
        self.assertEqual(snapshot["errorCount"], 1)
        self.assertEqual(snapshot["lastErrorCode"], "mindcraft_stop_failed")
        self.assertEqual(snapshot["lastErrorType"], "OSError")
        self.assertNotIn("private host path", json.dumps(snapshot))

    def test_inflight_restart_reaps_only_exact_durable_process_identity(
        self,
    ) -> None:
        runtime = mindcraft_service.MindcraftRuntime()
        birth_identity = (
            "windows:987654"
            if mindcraft_service.os.name == "nt"
            else "linux:987654"
        )
        runtime._write_process_identity(
            state="active",
            pid=4321,
            birth_identity=birth_identity,
        )

        with (
            patch.object(
                mindcraft_service,
                "_process_birth_identity",
                side_effect=[birth_identity, None],
            ) as inspect_identity,
            patch.object(
                mindcraft_service,
                "_terminate_process_identity",
                return_value=True,
            ) as terminate_identity,
        ):
            reconciled, error = runtime.reconcile_inflight_restart()

        self.assertTrue(reconciled)
        self.assertEqual(error, "")
        self.assertEqual(inspect_identity.call_count, 2)
        terminate_identity.assert_called_once_with(4321, birth_identity)
        persisted = json.loads(
            self.process_identity_path.read_text(encoding="utf-8")
        )
        self.assertEqual(
            set(persisted),
            {
                "schema",
                "state",
                "pid",
                "birthIdentity",
                "updatedAt",
                "contentFree",
            },
        )
        self.assertEqual(persisted["state"], "stopped")
        self.assertEqual(persisted["pid"], 0)
        self.assertEqual(persisted["birthIdentity"], "")
        self.assertTrue(persisted["contentFree"])

    def test_inflight_restart_keeps_live_unreaped_identity_quarantined(
        self,
    ) -> None:
        runtime = mindcraft_service.MindcraftRuntime()
        birth_identity = (
            "windows:987654"
            if mindcraft_service.os.name == "nt"
            else "linux:987654"
        )
        runtime._write_process_identity(
            state="active",
            pid=4321,
            birth_identity=birth_identity,
        )

        with (
            patch.object(
                mindcraft_service,
                "_process_birth_identity",
                return_value=birth_identity,
            ),
            patch.object(
                mindcraft_service,
                "_terminate_process_identity",
                return_value=False,
            ) as terminate_identity,
        ):
            reconciled, error = runtime.reconcile_inflight_restart()

        self.assertFalse(reconciled)
        self.assertEqual(
            error,
            "minecraft_prior_process_stop_unverified",
        )
        terminate_identity.assert_called_once_with(4321, birth_identity)
        persisted = json.loads(
            self.process_identity_path.read_text(encoding="utf-8")
        )
        self.assertEqual(persisted["state"], "active")
        self.assertEqual(persisted["pid"], 4321)
        self.assertEqual(persisted["birthIdentity"], birth_identity)
        self.assertTrue(persisted["contentFree"])

    def test_inflight_restart_starting_marker_is_ambiguous_and_never_signalled(
        self,
    ) -> None:
        runtime = mindcraft_service.MindcraftRuntime()
        runtime._write_process_identity(state="starting")

        with patch.object(
            mindcraft_service,
            "_terminate_process_identity",
        ) as terminate_identity:
            reconciled, error = runtime.reconcile_inflight_restart()

        self.assertFalse(reconciled)
        self.assertEqual(
            error,
            "minecraft_prior_process_start_ambiguous",
        )
        terminate_identity.assert_not_called()
        persisted = json.loads(
            self.process_identity_path.read_text(encoding="utf-8")
        )
        self.assertEqual(persisted["state"], "starting")
        self.assertEqual(persisted["pid"], 0)
        self.assertEqual(persisted["birthIdentity"], "")

    def test_inflight_restart_missing_or_corrupt_identity_fails_closed(
        self,
    ) -> None:
        runtime = mindcraft_service.MindcraftRuntime()
        reconciled, error = runtime.reconcile_inflight_restart()
        self.assertFalse(reconciled)
        self.assertEqual(
            error,
            "minecraft_prior_process_identity_missing",
        )

        self.process_identity_path.write_text("{broken", encoding="utf-8")
        reconciled, error = runtime.reconcile_inflight_restart()
        self.assertFalse(reconciled)
        self.assertEqual(
            error,
            "minecraft_prior_process_identity_invalid",
        )

    def test_inflight_restart_reused_pid_is_not_signalled(self) -> None:
        runtime = mindcraft_service.MindcraftRuntime()
        prefix = "windows" if mindcraft_service.os.name == "nt" else "linux"
        old_identity = f"{prefix}:111111"
        replacement_identity = f"{prefix}:222222"
        runtime._write_process_identity(
            state="active",
            pid=4321,
            birth_identity=old_identity,
        )

        with (
            patch.object(
                mindcraft_service,
                "_process_birth_identity",
                side_effect=[replacement_identity, replacement_identity],
            ),
            patch.object(
                mindcraft_service,
                "_terminate_process_identity",
            ) as terminate_identity,
        ):
            reconciled, error = runtime.reconcile_inflight_restart()

        self.assertTrue(reconciled)
        self.assertEqual(error, "")
        terminate_identity.assert_not_called()
        persisted = json.loads(
            self.process_identity_path.read_text(encoding="utf-8")
        )
        self.assertEqual(persisted["state"], "stopped")

    def test_overlay_blocks_slash_commands_and_uses_profile_cache(self) -> None:
        runtime_source = (
            REPO_ROOT / "external" / "mindcraft_evelyn" / "src" / "utils" / "evelyn_runtime.js"
        ).read_text(encoding="utf-8")
        overlay_patch = (REPO_ROOT / "external" / "mindcraft_evelyn" / "evelyn.patch").read_text(
            encoding="utf-8"
        )
        upstream_modes = (
            REPO_ROOT / "external" / "mindcraft" / "src" / "agent" / "modes.js"
        ).read_text(encoding="utf-8")

        self.assertIn("startsWith('/')", runtime_source)
        self.assertIn("MINDCRAFT_ALLOW_OUTBOUND_CHAT", runtime_source)
        self.assertIn("blocked outbound chat", runtime_source)
        self.assertIn("blocked outbound whisper", runtime_source)
        self.assertIn("bot.once('inject_allowed', installChatGuard)", runtime_source)
        self.assertIn("survival_controller", runtime_source)
        self.assertIn("'acquire_food'", runtime_source)
        self.assertIn("goal_manager", runtime_source)
        self.assertIn("mindcraft.task-contract.v1", runtime_source)
        self.assertIn("effect_verification", runtime_source)
        self.assertIn("MINEFLAYER_PROFILES_FOLDER", overlay_patch)
        self.assertIn("process.env.MINECRAFT_USERNAME", overlay_patch)
        self.assertIn("onMsaCode", overlay_patch)
        self.assertIn("[EVELYN_MSA_DEVICE_CODE]", overlay_patch)
        self.assertNotIn("verification_uri", overlay_patch)
        self.assertIn("self_prompter.start(process.env.MINDCRAFT_GOAL || init_message)", overlay_patch)
        self.assertIn("init_message && !save_data?.self_prompt", overlay_patch)
        self.assertIn("createEvelynSurvivalMode", overlay_patch)
        self.assertLess(
            upstream_modes.index("name: 'self_preservation'"),
            upstream_modes.index("name: 'unstuck'"),
        )
        self.assertIn(
            "+    createEvelynSurvivalMode({ execute }),\n"
            "     {\n"
            "         name: 'unstuck'",
            overlay_patch,
        )
        self.assertIn("minimumY", overlay_patch)
        self.assertIn("EvelynGoalManager", overlay_patch)
        self.assertIn("prepareForPrompt", overlay_patch)
        self.assertIn("gateCommand", overlay_patch)
        self.assertIn("handleMessage('system', msg, 1)", overlay_patch)
        self.assertIn("diff --git a/main.js b/main.js", overlay_patch)
        self.assertIn("process.once('SIGTERM', gracefulShutdown)", overlay_patch)
        self.assertIn("Mindcraft.shutdown()", overlay_patch)

        profile = json.loads(
            (REPO_ROOT / "external" / "mindcraft_evelyn" / "profiles" / "evelyn.json").read_text(
                encoding="utf-8"
            )
        )
        self.assertEqual(
            profile["model"],
            {"api": "evelyn-planner", "model": "Qwen3-14B-Q4_K_M.gguf"},
        )
        self.assertEqual(
            profile["code_model"],
            {"api": "evelyn-planner", "model": "Qwen3-14B-Q4_K_M.gguf"},
        )
        self.assertEqual(profile["embedding"], {"api": "evelyn-planner", "model": "hash-v1"})
        self.assertEqual(profile["cooldown"], 300)
        self.assertLess(
            profile["conversing"].index("$COMMAND_DOCS"),
            profile["conversing"].index("$STATS"),
        )
        self.assertTrue(profile["modes"]["evelyn_survival"])
        self.assertFalse(profile["modes"]["cowardice"])
        self.assertFalse(profile["modes"]["self_defense"])

        survival_source = (
            REPO_ROOT
            / "external"
            / "mindcraft_evelyn"
            / "src"
            / "agent"
            / "evelyn_survival_mode.js"
        ).read_text(encoding="utf-8")
        self.assertIn("escape_to_surface", survival_source)
        self.assertIn("acquire_food", survival_source)
        self.assertIn("bootstrap_tools", survival_source)
        self.assertIn("maxDropDown = 1", survival_source)
        self.assertIn("handle_hostile", survival_source)
        self.assertIn("escapeFromHostiles", survival_source)
        self.assertIn("fightWithCustomPvp", survival_source)
        self.assertIn("custom_pvp_unavailable", survival_source)
        self.assertIn("verifyHostileOutcome", survival_source)
        self.assertNotIn("function fleeHostile", survival_source)

        combat_source = (
            REPO_ROOT
            / "external"
            / "mindcraft_evelyn"
            / "src"
            / "agent"
            / "evelyn_combat.js"
        ).read_text(encoding="utf-8")
        combat_experience_source = (
            REPO_ROOT
            / "external"
            / "mindcraft_evelyn"
            / "src"
            / "agent"
            / "evelyn_combat_experience.js"
        ).read_text(encoding="utf-8")
        package = json.loads(
            (REPO_ROOT / "external" / "mindcraft_evelyn" / "package.json").read_text(
                encoding="utf-8"
            )
        )
        combat_patch = (
            REPO_ROOT / "external" / "mindcraft_evelyn" / "combat.patch"
        ).read_text(encoding="utf-8")
        combat_smoke = (
            REPO_ROOT
            / "external"
            / "mindcraft_evelyn"
            / "scripts"
            / "verify_combat_runtime.mjs"
        ).read_text(encoding="utf-8")
        dockerfile = (REPO_ROOT / "docker" / "Dockerfile.mindcraft").read_text(encoding="utf-8")
        escape_source = (
            REPO_ROOT
            / "external"
            / "mindcraft_evelyn"
            / "src"
            / "agent"
            / "evelyn_escape_controller.js"
        ).read_text(encoding="utf-8")

        self.assertIn("assessCombat", combat_source)
        self.assertIn("fightWithCustomPvp", combat_source)
        self.assertIn("selectCombatTarget", combat_source)
        self.assertIn("selectCombatPreset", survival_source)
        self.assertIn("createCombatHistoryWriter", combat_experience_source)
        self.assertIn("COMBAT_HISTORY_SCHEMA_VERSION = 1", combat_experience_source)
        self.assertIn("successes >= 2", combat_experience_source)
        self.assertIn("consecutiveFailures >= FAILURE_QUARANTINE", combat_experience_source)
        self.assertEqual(package["dependencies"]["@nxg-org/mineflayer-custom-pvp"], "1.7.16")
        self.assertNotIn("mineflayer-pvp", package["dependencies"])
        self.assertIn("bot.loadPlugin(customPvp)", combat_patch)
        self.assertIn("-import { plugin as pvp } from 'mineflayer-pvp';", combat_patch)
        self.assertIn("-    bot.loadPlugin(pvp);", combat_patch)
        self.assertIn("bot.pvp = bot.swordpvp", combat_patch)
        self.assertIn("SwordPvp?.prototype?.attack", combat_smoke)
        self.assertIn("SwordPvp?.prototype?.stop", combat_smoke)
        self.assertIn("verify_combat_runtime.mjs", dockerfile)
        self.assertIn("-            await attackEntity(bot, enemy, false)", combat_patch)
        self.assertIn("evelyn_combat.js", dockerfile)
        self.assertIn("evelyn_combat_experience.js", dockerfile)
        self.assertIn("combat_experience.test.mjs", dockerfile)
        self.assertIn("combat_mode.test.mjs", dockerfile)
        self.assertIn(
            "COPY external/mindcraft_evelyn/tests/escape_controller.test.mjs "
            "/app/mindcraft/tests/escape_controller.test.mjs",
            dockerfile,
        )
        self.assertIn(
            "      /app/mindcraft/tests/escape_controller.test.mjs",
            dockerfile,
        )
        self.assertIn("goal_manager.test.mjs", dockerfile)
        self.assertIn(
            "COPY external/mindcraft_evelyn/tests/survival_hostile_reflex.test.mjs "
            "/app/mindcraft/tests/survival_hostile_reflex.test.mjs",
            dockerfile,
        )
        self.assertIn(
            "      /app/mindcraft/tests/survival_hostile_reflex.test.mjs",
            dockerfile,
        )
        self.assertIn("survival_mode.test.mjs", dockerfile)
        self.assertIn("latency.patch", dockerfile)
        self.assertIn("verify_latency_runtime.mjs", dockerfile)
        self.assertIn("evelyn_escape_controller.js", dockerfile)
        self.assertIn("combat.patch", dockerfile)
        self.assertIn("buildEscapeCandidates", escape_source)
        self.assertIn("chooseEscapeCandidate", escape_source)
        self.assertIn("evelynMovementOwner", escape_source)

    def test_codex_gateway_adapter_uses_bearer_contract(self) -> None:
        source = (
            REPO_ROOT / "external" / "mindcraft_evelyn" / "src" / "models" / "codex_gateway.js"
        ).read_text(encoding="utf-8")

        self.assertIn("Authorization", source)
        self.assertIn("Bearer ${resolveToken()}", source)
        self.assertIn("mindcraft-planner", source)
        self.assertIn("gpt-5.5", source)

    def test_planner_uses_broker_only_and_gates_codex(self) -> None:
        source = (
            REPO_ROOT / "external" / "mindcraft_evelyn" / "src" / "models" / "evelyn_planner.js"
        ).read_text(encoding="utf-8")

        self.assertIn("static prefix = 'evelyn-planner'", source)
        self.assertIn("MINDCRAFT_CODEX_ENABLED", source)
        self.assertIn("if (!this.codexEnabled) return 'local'", source)
        self.assertIn(
            "if (kind === 'memory') throw new Error('mindcraft_memory_summary_unavailable');\n"
            "            if (kind === 'classifier') return 'ignore';\n"
            "            return '지금은 안전하게 판단할 수 없어 멈출게. !stop';",
            source,
        )
        self.assertNotIn("CodexGateway", source)
        self.assertNotIn("this.codex.", source)
        self.assertNotIn("MINDCRAFT_CODEX_GATEWAY_URL", source)
        self.assertIn(
            "if (kind === 'memory' && content === '!stop')",
            source,
        )
        self.assertIn("MINDCRAFT_LLM_BROKER_URL", source)
        self.assertIn("MINDCRAFT_LLM_BROKER_TOKEN_FILE", source)
        self.assertIn("mindcraft.llm-request.v1", source)
        self.assertIn("mindcraft.llm-delivery-ack.v1", source)
        self.assertIn("BROKER_REQUEST_TIMEOUT_MS = 135 * 1000", source)
        self.assertIn("BROKER_MAX_ACK_BYTES = 4096", source)
        self.assertIn("readBoundedJson(response, BROKER_MAX_ACK_BYTES)", source)
        self.assertNotIn("response.json()", source)
        self.assertNotIn("http://router_llm:9822/v1/chat/completions", source)
        self.assertNotIn("http://minecraft_llm:9823/v1/chat/completions", source)
        self.assertIn("Planner output violated command policy", source)
        self.assertIn("analyzeRecentTurns", source)
        self.assertIn("same_failed_command_repeated", source)
        self.assertIn("invalid_registry_name", source)
        self.assertIn("requestBroker(\n            'recovery'", source)
        self.assertIn("gate=recovery", source)
        self.assertIn("pendingIssuance", source)
        self.assertIn("FORBIDDEN_RECOVERY_COMMANDS", source)
        self.assertIn("readGoalPolicy", source)
        self.assertIn("Local planner failed; escalating to recovery", source)
        self.assertIn("proposeSubgoals", source)
        self.assertIn("parseSubgoalCandidates", source)
        self.assertIn("['BlockName', 'ItemName', 'BlockOrItemName']", source)
        dockerfile = (
            REPO_ROOT / "docker" / "Dockerfile.mindcraft"
        ).read_text(encoding="utf-8")
        self.assertIn(
            "RUN node --test /app/mindcraft/tests/evelyn_planner.test.mjs",
            dockerfile,
        )

    def test_history_boundary_overlay_is_installed_and_fail_closed(self) -> None:
        overlay_root = REPO_ROOT / "external" / "mindcraft_evelyn"
        history_source = (overlay_root / "src" / "agent" / "history.js").read_text(
            encoding="utf-8"
        )
        boundary_source = (
            overlay_root / "src" / "utils" / "evelyn_history_boundary.js"
        ).read_text(encoding="utf-8")
        planner_source = (
            overlay_root / "src" / "models" / "evelyn_planner.js"
        ).read_text(encoding="utf-8")
        goal_manager_source = (
            overlay_root / "src" / "agent" / "evelyn_goal_manager.js"
        ).read_text(encoding="utf-8")
        goal_publish_source = goal_manager_source.partition("    publish() {")[2].partition(
            "    resetHistoryDerivedState()"
        )[0]
        patch_source = (overlay_root / "history_boundary.patch").read_text(
            encoding="utf-8"
        )
        agent_patch_source = (overlay_root / "evelyn.patch").read_text(
            encoding="utf-8"
        )
        sink_patch_source = (overlay_root / "history_sink_boundary.patch").read_text(
            encoding="utf-8"
        )
        translator_source = (
            overlay_root / "src" / "utils" / "translator.js"
        ).read_text(encoding="utf-8")
        runtime_overlay_source = (
            overlay_root / "src" / "utils" / "evelyn_runtime.js"
        ).read_text(encoding="utf-8")
        node_test = (overlay_root / "tests" / "evelyn_planner.test.mjs").read_text(
            encoding="utf-8"
        )
        dockerfile = (REPO_ROOT / "docker" / "Dockerfile.mindcraft").read_text(
            encoding="utf-8"
        )
        compose = (REPO_ROOT / "docker-compose.fast-control.yml").read_text(
            encoding="utf-8"
        )
        bot_api = compose.partition("  bot_api:")[2].partition("\n  control_page:")[0]
        voyager = compose.partition("  voyager:")[2].partition("\nvolumes:")[0]
        runtime_source = (
            REPO_ROOT
            / "evelyn_core"
            / "runtime"
            / "evelyn_core"
            / "mindcraft_service.py"
        ).read_text(encoding="utf-8")

        self.assertIn("mindcraft.history.ephemeral.v1", history_source)
        self.assertIn("mindcraft_history_persistence_disabled", history_source)
        self.assertIn("this.activeExposures", history_source)
        self.assertIn("persistent: false", history_source)
        self.assertNotIn("writeFileSync", history_source)
        self.assertNotIn("readFileSync", history_source)
        self.assertNotIn("renameSync", history_source)
        self.assertNotIn("summarizeMemories", history_source)
        self.assertNotIn("appendFullHistory", history_source)
        self.assertIn("const goalManager = this.agent.goal_manager", history_source)
        self.assertIn("AsyncLocalStorage", boundary_source)
        self.assertIn("bindMindcraftRecoveryIssuance", boundary_source)
        self.assertIn("claimMindcraftRecoveryIssuance", boundary_source)
        self.assertIn("discardMindcraftRecoveryIssuance", boundary_source)
        self.assertIn("assertMindcraftHistoryCurrent", planner_source)
        self.assertIn("withMindcraftHistoryExposure", planner_source)
        self.assertIn("pendingExecution", planner_source)
        self.assertNotIn("lastObservedExecutionSequence", planner_source)
        self.assertIn("resetHistoryDerivedState()", planner_source)
        self.assertNotIn("MINDCRAFT_PLANNER_STATE_PATH", planner_source)
        self.assertIn("await this.handleMessage(username, translation)", patch_source)
        self.assertIn("agent.history.clear()", patch_source)
        self.assertIn("stale response discarded before delivery", patch_source)
        self.assertIn("-    async _saveLog", patch_source)
        self.assertIn("const releaseTurnExposure = directUserClear ? null", sink_patch_source)
        self.assertIn("this.history.beginExposure(this.history.generation)", sink_patch_source)
        self.assertIn("releaseTurnExposure?.();", sink_patch_source)
        self.assertIn("await this.routeResponse", sink_patch_source)
        self.assertIn("await this.openChat", sink_patch_source)
        self.assertIn("inheritMindcraftHistorySnapshot", sink_patch_source)
        self.assertIn("const releaseExposure = agent.history.beginExposure", sink_patch_source)
        self.assertIn("await _scheduleProcessInMessage", sink_patch_source)
        self.assertGreaterEqual(
            sink_patch_source.count("clearTimeout(this.inMessageTimer)"),
            2,
        )
        self.assertIn(
            "await convoManager.startConversation(player_name, message)",
            sink_patch_source,
        )
        self.assertIn("convoManager.resetHistoryDerivedState()", sink_patch_source)
        self.assertIn("persistedGoalState", goal_manager_source)
        self.assertIn("contentFreeCurrentSubgoal", goal_manager_source)
        self.assertIn("for (const name of OBSERVATION_COMMANDS)", goal_manager_source)
        self.assertIn("recentActions: []", goal_manager_source)
        self.assertIn("resetHistoryDerivedState()", goal_manager_source)
        self.assertIn("this.persist({strict: true})", goal_manager_source)
        self.assertIn("currentSubgoal.observationCounts = {}", goal_manager_source)
        self.assertIn("currentSubgoal.observationStreak = 0", goal_manager_source)
        self.assertIn("currentSubgoal.gateRejects = 0", goal_manager_source)
        self.assertIn("return contentFreeExecution(execution)", goal_manager_source)
        self.assertNotIn("commandBinding", goal_manager_source)
        self.assertIn("const recoveryIssuance = claimMindcraftRecoveryIssuance", agent_patch_source)
        self.assertIn("discardMindcraftRecoveryIssuance(history)", agent_patch_source)
        self.assertIn("recoveryIssuance?.complete(goalExecution)", agent_patch_source)
        self.assertIn("recoveryIssuance?.discard()", agent_patch_source)
        self.assertIn("recoveryPlanInFlight", planner_source)
        self.assertIn("return String(message || '')", translator_source)
        self.assertNotIn("async function handleTranslation", translator_source)
        self.assertNotIn("google-translate", translator_source)
        self.assertNotIn("readFileSync", runtime_overlay_source)
        self.assertNotIn("const existing", runtime_overlay_source)
        self.assertNotIn("state.goal =", runtime_overlay_source)
        self.assertIn("connected: false", runtime_overlay_source)
        self.assertIn("connection_state: 'starting'", runtime_overlay_source)
        self.assertIn("phase: 'starting'", runtime_overlay_source)
        self.assertIn("contentFreeSurvivalState", runtime_overlay_source)
        self.assertNotIn("...survivalState", runtime_overlay_source)
        self.assertIn("survival_action_failed", runtime_overlay_source)
        self.assertIn("current_subgoal: current", goal_publish_source)
        self.assertIn("? {id: current.id}", goal_publish_source)
        self.assertIn("content_free: true", goal_publish_source)
        self.assertNotIn("ultimate_goal:", goal_publish_source)
        self.assertNotIn("priority_request", goal_publish_source)
        self.assertNotIn("minimum_kit", goal_publish_source)
        self.assertIn(
            "state.goal_manager = bot.evelynGoalState || null",
            runtime_overlay_source,
        )
        self.assertNotIn(
            "bot.evelynGoalState || state.goal_manager",
            runtime_overlay_source,
        )
        for status_code in (
            "slash_command_blocked",
            "outbound_chat_disabled",
            "outbound_whisper_disabled",
            "minecraft_kicked",
            "minecraft_disconnected",
            "minecraft_runtime_error",
        ):
            self.assertIn(status_code, runtime_overlay_source)
        self.assertIn("bot.on('kicked', () => {", runtime_overlay_source)
        self.assertIn("bot.on('error', () => {", runtime_overlay_source)
        self.assertNotIn("state.last_error = reason", runtime_overlay_source)
        self.assertNotIn("state.last_error = error", runtime_overlay_source)
        self.assertIn("planner recovery state is not persisted across restart", node_test)
        self.assertIn("ephemeral history fences model exposure", node_test)
        self.assertIn("mindcraft_history_persistence_disabled", node_test)
        self.assertIn("Agent whole-turn lease fences clear", node_test)
        self.assertIn("await waitFor(() => prepareStarted)", node_test)
        self.assertIn("await waitFor(() => routeStarted)", node_test)
        self.assertIn("await waitFor(() => pauseStarted)", node_test)
        self.assertIn("await waitFor(() => classifierStarted)", node_test)
        self.assertIn("scheduledHandleCalls", node_test)
        self.assertIn("PRIVATE_GOAL_RESULT_CANARY", node_test)
        self.assertIn("PRIVATE_OBSERVATION_CANARY", node_test)
        self.assertIn("PRIVATE_HISTORY_USER_CANARY", node_test)
        self.assertIn("PRIVATE_INTER_AGENT_CANARY", node_test)
        self.assertIn("assert.match(firstBody", node_test)
        self.assertNotIn("durable clear fences in-flight", node_test)
        self.assertIn(
            "patch -p1 < /tmp/evelyn-mindcraft-history-boundary.patch",
            dockerfile,
        )
        self.assertIn(
            "patch -p1 < /tmp/evelyn-mindcraft-history-sink-boundary.patch",
            dockerfile,
        )
        self.assertIn(
            "src/agent/history.js /app/mindcraft/src/agent/history.js",
            dockerfile,
        )
        self.assertIn("evelyn_history_boundary.js", dockerfile)
        self.assertIn("src/utils/translator.js /app/mindcraft/src/utils/translator.js", dockerfile)
        self.assertIn("container_name: evelyn-mindcraft", voyager)
        self.assertNotIn("./bot_memory/mindcraft", voyager)
        self.assertNotIn("MINDCRAFT_PLANNER_STATE_PATH", voyager)
        self.assertIn(
            'MINDCRAFT_LLM_BROKER_TOKEN_FILE: "/mindcraft-llm-broker/token"',
            bot_api,
        )
        self.assertIn(
            'MINDCRAFT_LLM_BROKER_URL: "http://bot_api:8798/internal/mindcraft-llm"',
            voyager,
        )
        self.assertIn(
            "mindcraft_llm_broker_token:/mindcraft-llm-broker:ro",
            voyager,
        )
        self.assertIn("bot_api:\n        condition: service_healthy", voyager)
        self.assertNotIn("MINDCRAFT_LOCAL_", voyager)
        self.assertNotIn("MINDCRAFT_ROUTER_", voyager)
        self.assertNotIn("VOYAGER_CODEX_GATEWAY_TOKEN_FILE", voyager)
        self.assertNotIn("codex_gateway_token:/gateway-token", voyager)
        self.assertIn('"load_memory": False', runtime_source)
        self.assertIn("stdout=subprocess.PIPE", runtime_source)
        self.assertIn("stderr=subprocess.STDOUT", runtime_source)
        self.assertIn("_MINDCRAFT_CHILD_OUTPUT_READ_LIMIT", runtime_source)
        self.assertIn('"interrupt_plugin_not_ready"', runtime_source)

    def test_goal_manager_overlay_and_container_contract(self) -> None:
        manager_source = (
            REPO_ROOT
            / "external"
            / "mindcraft_evelyn"
            / "src"
            / "agent"
            / "evelyn_goal_manager.js"
        ).read_text(encoding="utf-8")
        dockerfile = (REPO_ROOT / "docker" / "Dockerfile.mindcraft").read_text(encoding="utf-8")
        compose = (REPO_ROOT / "docker-compose.fast-control.yml").read_text(encoding="utf-8")
        evelyn_patch = (
            REPO_ROOT / "external" / "mindcraft_evelyn" / "evelyn.patch"
        ).read_text(encoding="utf-8")

        self.assertIn("prepareForPrompt", manager_source)
        self.assertIn("predicateSatisfied", manager_source)
        self.assertIn("action_budget_exhausted", manager_source)
        self.assertIn("goal_manager_owns_autonomous_goal_control", manager_source)
        self.assertIn("observation_budget_exhausted", manager_source)
        self.assertIn("relocation_required_before_action", manager_source)
        self.assertIn("autonomous loop stopped unexpectedly", manager_source)
        self.assertIn("MINDCRAFT_GOAL_MANAGER_MODE", manager_source)
        self.assertIn("evelyn_goal_manager.js", dockerfile)
        self.assertIn("evelyn_world_state.js", dockerfile)
        self.assertIn("connection_handler.js", dockerfile)
        self.assertIn('MINDCRAFT_GOAL_MANAGER_MODE: "gated"', compose)
        self.assertIn("goal_manager_state.json", compose)
        self.assertIn("MINDCRAFT_COMBAT_HISTORY_PATH", compose)
        self.assertIn("combat_history.json", compose)
        self.assertIn('MINDCRAFT_SELF_PROMPT_COOLDOWN_MS: "300"', compose)
        self.assertIn('MINDCRAFT_MODE_INTERVAL_MS: "100"', compose)
        self.assertIn('MINDCRAFT_INTERRUPT_POLL_MS: "100"', compose)
        self.assertIn('MINDCRAFT_INTERRUPT_STOP_WAIT_MS: "1200"', compose)
        self.assertIn("exitAfterCombatHistoryFlush", evelyn_patch)
        self.assertIn("process.once('SIGINT', () => this.cleanKill", evelyn_patch)
        self.assertIn("process.once('SIGTERM', () => this.cleanKill", evelyn_patch)
        self.assertIn("return exitAfterCombatHistoryFlush(this.bot, code)", evelyn_patch)

        latency_patch = (
            REPO_ROOT / "external" / "mindcraft_evelyn" / "latency.patch"
        ).read_text(encoding="utf-8")
        self.assertIn("currentSubgoal?.allowedCommands", latency_patch)
        self.assertIn("promptConvo(history, self_prompt)", latency_patch)
        self.assertIn("restrictToCurrentSubgoal = false", latency_patch)
        self.assertIn("stats += await getCommand('!nearbyBlocks').perform(this.agent)", latency_patch)
        self.assertIn("-            stats += await getCommand('!nearbyBlocks').perform(this.agent);", latency_patch)
        self.assertIn("MINDCRAFT_SELF_PROMPT_COOLDOWN_MS", latency_patch)
        self.assertIn("MINDCRAFT_MODE_INTERVAL_MS", latency_patch)
        self.assertIn("MINDCRAFT_INTERRUPT_POLL_MS", latency_patch)
        self.assertIn("MINDCRAFT_INTERRUPT_STOP_WAIT_MS", latency_patch)
        self.assertIn("return !this.executing", latency_patch)
        self.assertIn("Current action did not stop; new action rejected.", latency_patch)

    def test_runtime_lint_gate_is_installed_and_fail_closed(
        self,
    ) -> None:
        overlay_root = (
            REPO_ROOT / "external" / "mindcraft_evelyn"
        )
        package = json.loads(
            (overlay_root / "package.json").read_text(
                encoding="utf-8"
            )
        )
        dockerfile = (
            REPO_ROOT / "docker" / "Dockerfile.mindcraft"
        ).read_text(encoding="utf-8")
        lint_patch = (overlay_root / "lint.patch").read_text(
            encoding="utf-8"
        )
        smoke = (
            overlay_root
            / "scripts"
            / "verify_runtime_lint.mjs"
        ).read_text(encoding="utf-8")

        dependencies = package["dependencies"]
        self.assertEqual(dependencies["eslint"], "10.8.0")
        self.assertEqual(dependencies["@eslint/js"], "10.0.1")
        self.assertEqual(dependencies["globals"], "17.8.0")
        self.assertEqual(
            dependencies["eslint-plugin-no-floating-promise"],
            "2.0.0",
        )
        self.assertIn("lint.patch", dockerfile)
        self.assertIn("verify_runtime_lint.mjs", dockerfile)
        self.assertIn(
            "generated code will not execute",
            lint_patch,
        )
        self.assertNotIn("catch (error)", lint_patch)
        self.assertIn(
            "no-floating-promise/no-floating-promise",
            smoke,
        )


if __name__ == "__main__":
    unittest.main()
