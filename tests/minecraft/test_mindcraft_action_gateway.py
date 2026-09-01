from __future__ import annotations

import asyncio
import json
import sys
import tempfile
import time
import unittest
from pathlib import Path
from unittest.mock import Mock, patch


REPO_ROOT = next(
    path
    for path in Path(__file__).resolve().parents
    if (path / "main.py").exists()
)
RUNTIME_ROOT = REPO_ROOT / "evelyn_core" / "runtime"
if str(RUNTIME_ROOT) not in sys.path:
    sys.path.insert(0, str(RUNTIME_ROOT))

from evelyn_core import mindcraft_service  # noqa: E402
from evelyn_core.minecraft_action_contract import (  # noqa: E402
    validate_minecraft_action_dispatch,
    validate_minecraft_action_result,
)
from evelyn_core.minecraft_owner_lock import MinecraftOwnerLock  # noqa: E402
from evelyn_core.mindcraft_world_effect import (  # noqa: E402
    MindcraftWorldEffectProjector,
)


def bound_request(
    *,
    goal_run_id: str = "goal-run-1",
    action_run_id: str = "action-run-1",
) -> dict:
    return {
        "schema": "minecraft_autonomy.action-request.v1",
        "guildId": 7,
        "actionKey": "minecraft:find_food_source",
        "actionRunId": action_run_id,
        "authorizationGrantId": "grant-1",
        "contractCode": "mindcraft_food_recovery.v1",
        "parameters": {},
        "goalRunId": goal_run_id,
        "leaseId": "lease-1",
        "leaseProcessNonce": "lease-process-1",
    }


def ready_status() -> dict:
    return {
        "runtime": "mindcraft",
        "running": True,
        "telemetry_fresh": True,
        "minecraft_connected": True,
        "world_lease_authorized": True,
        "functional_readiness": {
            "schema": "minecraft_autonomy.readiness.v1",
            "state": "ready",
            "ready": True,
            "blockers": [],
            "dependencies": {
                "worldLeaseAuthorized": True,
                "runnerAlive": True,
                "telemetryFresh": True,
                "minecraftConnected": True,
                "taskContractReady": True,
                "effectObserverReady": True,
                "autonomyActive": True,
            },
            "taskContract": {
                "schema": "mindcraft.task-contract.v1",
                "goalManagerMode": "gated",
                "autonomyState": "active",
                "commandGate": "evelyn_goal_manager",
                "effectVerification": "explicit_postcondition",
            },
            "contentFree": True,
        },
    }


class FakeRuntime:
    def __init__(self) -> None:
        self.alive = True
        self.starts: list[tuple[str, dict]] = []
        self.stop_count = 0
        self.stop_error: Exception | None = None
        self.survive_stop = False
        self.restart_reconcile_ok = True
        self.restart_reconcile_error = ""
        self.restart_reconcile_count = 0

    def process_alive(self) -> bool:
        return self.alive

    def _child_runtime_snapshot(self) -> dict:
        return mindcraft_service._project_mindcraft_child_runtime({})

    def restart_for_action(
        self,
        *,
        goal: str,
        world_effect_binding: dict,
    ) -> None:
        self.alive = True
        self.starts.append((goal, dict(world_effect_binding)))

    def stop(self) -> None:
        self.stop_count += 1
        if self.stop_error is not None:
            raise self.stop_error
        if not self.survive_stop:
            self.alive = False

    def reconcile_inflight_restart(self) -> tuple[bool, str]:
        self.restart_reconcile_count += 1
        if not self.restart_reconcile_ok:
            return (
                False,
                self.restart_reconcile_error
                or "minecraft_prior_process_stop_unverified",
            )
        self.alive = False
        return True, ""


class FakeProjector:
    def __init__(self) -> None:
        self.bindings: list[dict] = []
        self.candidates: list[dict] = []
        self.archive_contexts: list[dict] = []
        self.disarms: list[str] = []
        self.verified = True
        self.guard_error = ""
        self.archive_allowed = True

    def archive_ready(self) -> bool:
        return self.archive_allowed

    def status(self) -> dict:
        return {
            "state": "idle",
            "auditReady": True,
            "statusReady": True,
        }

    def arm(self, binding: dict) -> dict:
        self.bindings.append(dict(binding))
        return {"accepted": True, "code": "armed"}

    def observe(
        self,
        candidate: dict,
        *,
        archive_context: dict | None = None,
    ) -> dict:
        self.candidates.append(dict(candidate))
        if archive_context is not None:
            self.archive_contexts.append(dict(archive_context))
            self.candidates[-1]["archiveGuildId"] = archive_context[
                "guildId"
            ]
        return {
            "verified": self.verified,
            "code": "effect_verified" if self.verified else "candidate_rejected",
        }

    def disarm(self, reason: str) -> dict:
        self.disarms.append(reason)
        return {"accepted": True}

    def active_guard_error(self) -> str:
        return self.guard_error


class FakeRequest:
    def __init__(self, payload: dict) -> None:
        self.payload = payload

    async def json(self) -> dict:
        return self.payload


class MindcraftActionGatewayTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        self.runtime = FakeRuntime()
        self.projector = FakeProjector()
        self.telemetry_path = self.root / "mindcraft-status.json"
        self.events_dir = self.root / "events"
        self.action_status_path = self.root / "action-status.json"
        self.lock_path = self.root / "world-action.lock"
        self.patches = (
            patch.object(
                mindcraft_service,
                "WORLD_EFFECT_EVENTS_DIR",
                self.events_dir,
            ),
            patch.object(
                mindcraft_service,
                "WORLD_ACTION_LOCK_PATH",
                self.lock_path,
            ),
            patch.object(
                mindcraft_service,
                "STATUS_PATH",
                self.telemetry_path,
            ),
            patch.object(
                mindcraft_service,
                "_load_exact_action_lease",
                return_value=({"lease": {}}, ""),
            ),
        )
        for active_patch in self.patches:
            active_patch.start()
            self.addCleanup(active_patch.stop)
        self.gateway = self.make_gateway()

    def make_gateway(self) -> mindcraft_service.MindcraftActionGateway:
        return mindcraft_service.MindcraftActionGateway(
            runtime=self.runtime,
            projector=self.projector,
            status_path=self.action_status_path,
            timeout_sec=30.0,
        )

    def lock(self) -> MinecraftOwnerLock:
        action_lock = MinecraftOwnerLock(self.lock_path)
        action_lock.acquire()
        return action_lock

    def recover_quarantined_action(self, request: dict) -> None:
        """Release a deliberately quarantined OS lock after assertions.

        The gateway must retain the world-action lock while its child still
        reports alive.  Windows consequently cannot remove the temporary lock
        file until the test simulates that child stopping and retries the
        exact cancellation path.
        """

        if self.gateway.admitted_world_action_lock() is None:
            return
        self.runtime.survive_stop = False
        self.gateway.cancel(request)

    def write_candidate(self, value: object, *, exact_path: bool = True) -> None:
        payload = (
            {
                "updated_at": time.time(),
                "goal_manager": {"postcondition_candidate": value},
            }
            if exact_path
            else {
                "updated_at": time.time(),
                "postcondition_candidate": value,
                "goal_manager": {"last_execution": value},
            }
        )
        self.telemetry_path.write_text(json.dumps(payload), encoding="utf-8")

    def test_dispatch_retains_lock_and_only_exact_candidate_completes(self) -> None:
        request = bound_request()
        action_lock = self.lock()

        accepted = self.gateway.dispatch(
            request,
            action_lock=action_lock,
            preflight_status=ready_status(),
            archive_parent_record_ids=("minecraft-command-1",),
        )

        self.assertEqual(
            validate_minecraft_action_dispatch(
                accepted,
                expected_request=request,
            ),
            accepted,
        )
        self.assertEqual(accepted["status"], "accepted")
        self.assertEqual(accepted["errorCode"], "")
        self.assertTrue(action_lock.acquired)
        self.assertIs(
            self.gateway.admitted_world_action_lock(),
            action_lock,
        )
        self.assertEqual(len(self.projector.bindings), 1)
        binding = self.projector.bindings[0]
        self.assertEqual(binding["actionRunId"], request["actionRunId"])
        self.assertEqual(binding["candidateSequence"], 1)
        self.assertNotIn("goal", binding)

        self.write_candidate({"goal": "echo"}, exact_path=False)
        still_running = self.gateway.poll()
        self.assertEqual(still_running["status"], "running")
        self.assertEqual(self.projector.candidates, [])
        self.assertTrue(action_lock.acquired)

        self.write_candidate({"opaque": "candidate"}, exact_path=True)
        completed = self.gateway.poll()

        self.assertEqual(
            validate_minecraft_action_result(
                completed,
                expected_request=request,
            ),
            completed,
        )
        self.assertFalse(action_lock.acquired)
        self.assertEqual(
            self.projector.archive_contexts[0]["parentRecordIds"],
            ["minecraft-command-1"],
        )
        self.assertEqual(self.runtime.stop_count, 1)
        self.assertFalse(self.runtime.alive)

    def test_archive_fault_blocks_dispatch_before_action_record(self) -> None:
        self.projector.archive_allowed = False
        request = bound_request()
        action_lock = self.lock()
        try:
            with self.assertRaisesRegex(
                RuntimeError,
                "minecraft_action_gateway_unavailable",
            ):
                self.gateway.dispatch(
                    request,
                    action_lock=action_lock,
                    preflight_status=ready_status(),
                )
            self.assertEqual(self.projector.bindings, [])
            payload = json.loads(
                self.action_status_path.read_text(encoding="utf-8")
            )
            self.assertEqual(payload["records"], [])
        finally:
            action_lock.release()

    def test_archive_readiness_exception_is_fail_closed(self) -> None:
        with patch.object(
            self.projector,
            "archive_ready",
            side_effect=RuntimeError("private backend detail"),
        ):
            self.assertFalse(self.gateway.available())

    def test_terminal_action_can_restart_with_fresh_binding_only(self) -> None:
        first = bound_request()
        first_lock = self.lock()
        self.gateway.dispatch(
            first,
            action_lock=first_lock,
            preflight_status=ready_status(),
        )
        self.write_candidate({"opaque": "candidate"})
        self.gateway.poll()

        repeated_lock = self.lock()
        with self.assertRaisesRegex(
            RuntimeError,
            "minecraft_action_replay_rejected",
        ):
            self.gateway.dispatch(
                first,
                action_lock=repeated_lock,
                preflight_status={},
            )
        repeated_lock.release()

        second = bound_request(
            goal_run_id="goal-run-2",
            action_run_id="action-run-2",
        )
        second_lock = self.lock()
        accepted = self.gateway.dispatch(
            second,
            action_lock=second_lock,
            preflight_status={},
        )

        self.assertEqual(accepted["goalRunId"], "goal-run-2")
        self.assertTrue(second_lock.acquired)
        self.assertEqual(len(self.runtime.starts), 2)
        self.assertNotEqual(
            self.projector.bindings[0]["producerNonce"],
            self.projector.bindings[1]["producerNonce"],
        )
        self.gateway.cancel(second)

    def test_real_projector_allows_only_exact_stopped_terminal_repeat_arm(
        self,
    ) -> None:
        real_projector = MindcraftWorldEffectProjector(
            status_path=self.root / "effect-status.json",
            events_dir=self.events_dir,
            validate_guarded_lease=mindcraft_service._effect_guarded_lease,
            validate_readiness=mindcraft_service._effect_guarded_readiness,
        )
        real_gateway = mindcraft_service.MindcraftActionGateway(
            runtime=self.runtime,
            projector=real_projector,
            status_path=self.root / "real-action-status.json",
            timeout_sec=30.0,
        )
        ready_telemetry = {
            "updated_at": time.time(),
            "connected_at": time.time() - 3.1,
            "connected": True,
            "task_contract": {
                "schema": "mindcraft.task-contract.v1",
                "ready": True,
                "goal_manager_mode": "gated",
                "command_gate": "evelyn_goal_manager",
                "effect_verification": "explicit_postcondition",
            },
            "goal_manager": {
                "mode": "gated",
                "autonomy_state": "active",
                "manual_pause_reason": "",
                "postcondition_candidate": None,
            },
        }
        self.telemetry_path.write_text(
            json.dumps(ready_telemetry),
            encoding="utf-8",
        )
        first = bound_request()
        with (
            patch.object(mindcraft_service, "ACTION_GATEWAY", real_gateway),
            patch.object(mindcraft_service, "STATE", self.runtime),
        ):
            real_gateway.dispatch(
                first,
                action_lock=self.lock(),
                preflight_status=ready_status(),
            )
            binding = real_projector.status()["binding"]
            candidate = {
                "schema": "mindcraft.postcondition-candidate.v1",
                **{
                    key: binding[key]
                    for key in (
                        "goalRunId",
                        "actionRunId",
                        "actionKey",
                        "contractCode",
                        "leaseId",
                        "leaseProcessNonce",
                        "producerNonce",
                    )
                },
                "candidateSequence": 1,
                "executionSequence": 1,
                "observedAt": time.time(),
                "evidenceCode": "mindcraft_explicit_postcondition_candidate",
                "postconditionCode": "food_reserve_ready",
                "beforeSatisfied": False,
                "afterSatisfied": True,
                "autonomous": True,
                "relevant": True,
                "actionSucceeded": True,
                "worldChanged": True,
                "goalProgress": True,
                "predicateCompleted": True,
                "completionDelta": 1,
                "blockedDelta": 0,
                "contentFree": True,
            }
            ready_telemetry["updated_at"] = time.time()
            ready_telemetry["goal_manager"].update(
                {
                    "autonomy_state": "manual_pause",
                    "manual_pause_reason": (
                        "world_effect_candidate_published"
                    ),
                    "postcondition_candidate": candidate,
                }
            )
            self.telemetry_path.write_text(
                json.dumps(ready_telemetry),
                encoding="utf-8",
            )
            completed = real_gateway.poll()
            self.assertEqual(completed["status"], "completed")
            self.assertFalse(self.runtime.alive)

            second = bound_request(
                goal_run_id="goal-run-2",
                action_run_id="action-run-2",
            )
            accepted = real_gateway.dispatch(
                second,
                action_lock=self.lock(),
                preflight_status={},
            )

            self.assertEqual(accepted["status"], "accepted")
            self.assertTrue(self.runtime.alive)
            self.assertEqual(real_projector.status()["state"], "armed")
            real_gateway.cancel(second)

    def test_running_action_fails_when_ready_telemetry_becomes_stale(
        self,
    ) -> None:
        real_projector = MindcraftWorldEffectProjector(
            status_path=self.root / "stale-effect-status.json",
            events_dir=self.events_dir,
            validate_guarded_lease=mindcraft_service._effect_guarded_lease,
            validate_readiness=mindcraft_service._effect_guarded_readiness,
        )
        real_gateway = mindcraft_service.MindcraftActionGateway(
            runtime=self.runtime,
            projector=real_projector,
            status_path=self.root / "stale-action-status.json",
            timeout_sec=30.0,
        )
        telemetry = {
            "updated_at": time.time(),
            "connected_at": time.time() - 3.1,
            "connected": True,
            "task_contract": {
                "schema": "mindcraft.task-contract.v1",
                "ready": True,
                "goal_manager_mode": "gated",
                "command_gate": "evelyn_goal_manager",
                "effect_verification": "explicit_postcondition",
            },
            "goal_manager": {
                "mode": "gated",
                "autonomy_state": "active",
                "manual_pause_reason": "",
                "postcondition_candidate": None,
            },
        }
        self.telemetry_path.write_text(
            json.dumps(telemetry),
            encoding="utf-8",
        )
        request = bound_request(
            goal_run_id="goal-run-stale",
            action_run_id="action-run-stale",
        )
        action_lock = self.lock()
        with (
            patch.object(mindcraft_service, "ACTION_GATEWAY", real_gateway),
            patch.object(mindcraft_service, "STATE", self.runtime),
        ):
            try:
                real_gateway.dispatch(
                    request,
                    action_lock=action_lock,
                    preflight_status=ready_status(),
                )
                self.assertEqual(real_gateway.poll()["status"], "running")

                telemetry["updated_at"] = time.time() - 11.0
                self.telemetry_path.write_text(
                    json.dumps(telemetry),
                    encoding="utf-8",
                )
                failed = real_gateway.poll()

                self.assertEqual(failed["status"], "failed")
                self.assertEqual(
                    failed["errorCode"],
                    "minecraft_runtime_not_ready",
                )
                self.assertEqual(self.runtime.stop_count, 1)
                self.assertFalse(self.runtime.alive)
                self.assertFalse(action_lock.acquired)
                self.assertEqual(real_projector.status()["state"], "idle")
            finally:
                if action_lock.acquired:
                    real_gateway.cancel(request)

    def test_restart_fails_inflight_and_requires_new_ready_request(self) -> None:
        first = bound_request()
        abandoned_lock = self.lock()
        self.gateway.dispatch(
            first,
            action_lock=abandoned_lock,
            preflight_status=ready_status(),
        )
        abandoned_lock.release()

        replacement = self.make_gateway()
        failed = replacement.get_status(first["goalRunId"])

        self.assertEqual(self.runtime.restart_reconcile_count, 1)
        self.assertFalse(self.runtime.alive)
        self.assertEqual(failed["status"], "failed")
        self.assertEqual(
            failed["errorCode"],
            "minecraft_action_authority_lost_on_restart",
        )
        replay_lock = self.lock()
        with self.assertRaisesRegex(RuntimeError, "replay_rejected"):
            replacement.dispatch(
                first,
                action_lock=replay_lock,
                preflight_status=ready_status(),
            )
        replay_lock.release()

        fresh = bound_request(
            goal_run_id="goal-run-after-restart",
            action_run_id="action-run-after-restart",
        )
        not_ready_lock = self.lock()
        with self.assertRaisesRegex(RuntimeError, "runtime_not_ready"):
            replacement.dispatch(
                fresh,
                action_lock=not_ready_lock,
                preflight_status={},
            )
        not_ready_lock.release()

        ready_lock = self.lock()
        accepted = replacement.dispatch(
            fresh,
            action_lock=ready_lock,
            preflight_status=ready_status(),
        )
        self.assertEqual(accepted["status"], "accepted")
        replacement.cancel(fresh)

    def test_restart_with_surviving_child_quarantines_without_terminal(self) -> None:
        first = bound_request()
        abandoned_lock = self.lock()
        self.gateway.dispatch(
            first,
            action_lock=abandoned_lock,
            preflight_status=ready_status(),
        )
        abandoned_lock.release()
        self.runtime.restart_reconcile_ok = False
        self.runtime.restart_reconcile_error = (
            "minecraft_prior_process_stop_unverified"
        )

        replacement = self.make_gateway()

        self.assertEqual(self.runtime.restart_reconcile_count, 1)
        self.assertTrue(self.runtime.alive)
        current = replacement.get_status(first["goalRunId"])
        self.assertEqual(current["status"], "running")
        self.assertNotEqual(current.get("status"), "failed")
        readiness = replacement.readiness_projection()
        self.assertEqual(readiness["state"], "unavailable")
        self.assertFalse(readiness["ready"])
        self.assertFalse(readiness["acceptsNewAction"])
        self.assertFalse(readiness["repeatActionReady"])
        self.assertFalse(readiness["active"])
        persisted = json.loads(
            self.action_status_path.read_text(encoding="utf-8")
        )
        self.assertFalse(persisted["available"])
        self.assertEqual(
            persisted["lastErrorCode"],
            "minecraft_prior_process_stop_unverified",
        )
        self.assertTrue(persisted["contentFree"])

        fresh = bound_request(
            goal_run_id="goal-run-orphan-blocked",
            action_run_id="action-run-orphan-blocked",
        )
        fresh_lock = self.lock()
        try:
            with self.assertRaisesRegex(
                RuntimeError,
                "minecraft_prior_process_stop_unverified",
            ):
                replacement.dispatch(
                    fresh,
                    action_lock=fresh_lock,
                    preflight_status=ready_status(),
                )
        finally:
            fresh_lock.release()

    def test_restart_with_starting_marker_never_converts_inflight_terminal(
        self,
    ) -> None:
        first = bound_request(
            goal_run_id="goal-run-starting-marker",
            action_run_id="action-run-starting-marker",
        )
        abandoned_lock = self.lock()
        self.gateway.dispatch(
            first,
            action_lock=abandoned_lock,
            preflight_status=ready_status(),
        )
        abandoned_lock.release()
        self.runtime.restart_reconcile_ok = False
        self.runtime.restart_reconcile_error = (
            "minecraft_prior_process_start_ambiguous"
        )

        replacement = self.make_gateway()

        current = replacement.get_status(first["goalRunId"])
        self.assertEqual(current["status"], "running")
        self.assertEqual(
            replacement._last_error_code,
            "minecraft_prior_process_start_ambiguous",
        )
        readiness = replacement.readiness_projection()
        self.assertEqual(readiness["state"], "unavailable")
        self.assertFalse(readiness["acceptsNewAction"])
        self.assertFalse(readiness["repeatActionReady"])

    def test_cancel_is_exact_content_free_and_stops_runtime(self) -> None:
        request = bound_request()
        action_lock = self.lock()
        self.gateway.dispatch(
            request,
            action_lock=action_lock,
            preflight_status=ready_status(),
        )

        cancelled = self.gateway.cancel(request)

        self.assertEqual(
            validate_minecraft_action_dispatch(
                cancelled,
                expected_request=request,
            ),
            cancelled,
        )
        self.assertEqual(cancelled["status"], "cancelled")
        self.assertEqual(cancelled["errorCode"], "minecraft_action_cancelled")
        self.assertEqual(self.runtime.stop_count, 1)
        self.assertFalse(action_lock.acquired)
        persisted = json.loads(
            self.action_status_path.read_text(encoding="utf-8")
        )
        self.assertTrue(persisted["contentFree"])
        self.assertNotIn("worldLease", json.dumps(persisted))

    def test_stop_exception_quarantines_active_action_and_lock(self) -> None:
        request = bound_request()
        action_lock = self.lock()
        self.gateway.dispatch(
            request,
            action_lock=action_lock,
            preflight_status=ready_status(),
        )
        self.runtime.stop_error = OSError("terminate denied")
        self.write_candidate({"opaque": "candidate"})

        with self.assertRaisesRegex(
            RuntimeError,
            "minecraft_action_stop_failed",
        ):
            self.gateway.poll()

        readiness = self.gateway.readiness_projection()
        self.assertFalse(readiness["ready"])
        self.assertFalse(readiness["acceptsNewAction"])
        self.assertTrue(readiness["active"])
        self.assertEqual(readiness["state"], "unavailable")
        self.assertTrue(self.runtime.alive)
        self.assertTrue(action_lock.acquired)
        self.assertIs(
            self.gateway.admitted_world_action_lock(),
            action_lock,
        )
        persisted = json.loads(
            self.action_status_path.read_text(encoding="utf-8")
        )
        self.assertFalse(persisted["available"])
        self.assertEqual(
            persisted["lastErrorCode"],
            "minecraft_action_stop_failed",
        )
        self.assertEqual(
            persisted["activeGoalRunId"],
            request["goalRunId"],
        )
        self.assertEqual(self.runtime.stop_count, 1)
        fenced = self.gateway.fail_closed(
            "minecraft_action_guard_failed"
        )
        self.assertEqual(fenced["status"], "running")
        self.assertEqual(self.runtime.stop_count, 1)
        self.assertTrue(action_lock.acquired)

        fresh = bound_request(
            goal_run_id="goal-run-quarantine",
            action_run_id="action-run-quarantine",
        )
        with self.assertRaisesRegex(
            RuntimeError,
            "minecraft_action_stop_failed",
        ):
            self.gateway.dispatch(
                fresh,
                action_lock=action_lock,
                preflight_status=ready_status(),
            )
        with self.assertRaisesRegex(
            RuntimeError,
            "minecraft_action_cancel_mismatch",
        ):
            self.gateway.cancel(fresh)

        self.runtime.stop_error = None
        recovered = self.gateway.cancel(request)
        self.assertEqual(recovered["status"], "cancelled")
        self.assertFalse(action_lock.acquired)
        with self.assertRaisesRegex(
            RuntimeError,
            "minecraft_action_stop_failed",
        ):
            self.gateway.cancel(request)

    def test_alive_after_stop_quarantines_shutdown_and_lock(self) -> None:
        request = bound_request()
        action_lock = self.lock()
        self.gateway.dispatch(
            request,
            action_lock=action_lock,
            preflight_status=ready_status(),
        )
        self.addCleanup(self.recover_quarantined_action, request)
        self.runtime.survive_stop = True

        with self.assertRaisesRegex(
            RuntimeError,
            "minecraft_action_stop_unverified",
        ):
            self.gateway.shutdown()

        readiness = self.gateway.readiness_projection()
        self.assertEqual(readiness["state"], "unavailable")
        self.assertTrue(readiness["active"])
        self.assertFalse(readiness["acceptsNewAction"])
        self.assertTrue(self.runtime.alive)
        self.assertTrue(action_lock.acquired)
        self.assertIs(
            self.gateway.admitted_world_action_lock(),
            action_lock,
        )
        persisted = json.loads(
            self.action_status_path.read_text(encoding="utf-8")
        )
        self.assertFalse(persisted["available"])
        self.assertEqual(
            persisted["lastErrorCode"],
            "minecraft_action_stop_unverified",
        )

    def test_alive_after_cancel_stop_never_publishes_cancelled(self) -> None:
        request = bound_request()
        action_lock = self.lock()
        self.gateway.dispatch(
            request,
            action_lock=action_lock,
            preflight_status=ready_status(),
        )
        self.addCleanup(self.recover_quarantined_action, request)
        self.runtime.survive_stop = True

        with self.assertRaisesRegex(
            RuntimeError,
            "minecraft_action_stop_unverified",
        ):
            self.gateway.cancel(request)

        current = self.gateway.get_status(request["goalRunId"])
        self.assertEqual(current["status"], "running")
        self.assertNotEqual(current["status"], "cancelled")
        self.assertTrue(self.runtime.alive)
        self.assertTrue(action_lock.acquired)
        self.assertIs(
            self.gateway.admitted_world_action_lock(),
            action_lock,
        )
        readiness = self.gateway.readiness_projection()
        self.assertFalse(readiness["ready"])
        self.assertTrue(readiness["active"])
        self.assertFalse(readiness["acceptsNewAction"])

    def test_public_gateway_readiness_keys_are_stable(self) -> None:
        self.assertEqual(
            set(self.gateway.readiness_projection()),
            {
                "schema",
                "state",
                "ready",
                "acceptsNewAction",
                "active",
                "terminalStatus",
                "repeatActionReady",
                "contentFree",
            },
        )


class MindcraftActionRuntimeBoundaryTests(unittest.TestCase):
    def test_cancel_handler_never_reacquires_retained_action_lock(self) -> None:
        request = bound_request()
        gateway = Mock()
        gateway.cancel.return_value = mindcraft_service._action_ack(
            request,
            status="cancelled",
            error_code="minecraft_action_cancelled",
        )
        with (
            patch.object(
                mindcraft_service,
                "_validate_action_payload",
                return_value=request,
            ),
            patch.object(mindcraft_service, "ACTION_GATEWAY", gateway),
            patch.object(
                mindcraft_service,
                "_acquire_world_action_lock",
                side_effect=AssertionError("cancel must not reacquire lock"),
            ),
        ):
            response = asyncio.run(
                mindcraft_service.cancel_action(
                    FakeRequest({"request": request, "worldLease": {}})
                )
            )

        self.assertEqual(response.status, 200)
        gateway.cancel.assert_called_once_with(request)

    def test_only_exact_candidate_pause_bypasses_active_readiness(self) -> None:
        request = bound_request()
        binding = {
            key: request[key]
            for key in (
                "goalRunId",
                "actionRunId",
                "actionKey",
                "contractCode",
                "leaseId",
                "leaseProcessNonce",
            )
        }
        binding["producerNonce"] = "producer-1"
        candidate = {
            "schema": "mindcraft.postcondition-candidate.v1",
            **binding,
        }
        telemetry = {
            "updated_at": time.time(),
            "connected_at": time.time() - 3.1,
            "connected": True,
            "task_contract": {
                "schema": "mindcraft.task-contract.v1",
                "ready": True,
                "goal_manager_mode": "gated",
                "command_gate": "evelyn_goal_manager",
                "effect_verification": "explicit_postcondition",
            },
            "goal_manager": {
                "mode": "gated",
                "autonomy_state": "manual_pause",
                "manual_pause_reason": "world_effect_candidate_published",
                "postcondition_candidate": candidate,
            },
        }
        gateway = Mock()
        gateway.request_for_binding.return_value = request
        gateway.repeat_arm_admitted.return_value = False
        state = Mock()
        state.process_alive.return_value = True
        state._child_runtime_snapshot.return_value = (
            mindcraft_service._project_mindcraft_child_runtime({})
        )
        with (
            tempfile.TemporaryDirectory() as temporary,
            patch.object(
                mindcraft_service,
                "STATUS_PATH",
                Path(temporary) / "status.json",
            ) as status_path,
            patch.object(mindcraft_service, "ACTION_GATEWAY", gateway),
            patch.object(mindcraft_service, "STATE", state),
            patch.object(
                mindcraft_service,
                "_load_exact_action_lease",
                return_value=({"lease": {}}, ""),
            ),
        ):
            status_path.write_text(json.dumps(telemetry), encoding="utf-8")
            accepted = mindcraft_service._effect_guarded_readiness(binding)
            telemetry["goal_manager"]["postcondition_candidate"] = {
                "goal": "echo only"
            }
            status_path.write_text(json.dumps(telemetry), encoding="utf-8")
            rejected = mindcraft_service._effect_guarded_readiness(binding)

        self.assertEqual(accepted, (True, ""))
        self.assertEqual(rejected, (False, "minecraft_runtime_not_ready"))

    def test_one_shot_restart_never_overwrites_durable_goal(self) -> None:
        runtime = mindcraft_service.MindcraftRuntime()
        binding = {
            "goalRunId": "goal-run-1",
            "actionRunId": "action-run-1",
            "actionKey": "minecraft:find_food_source",
            "contractCode": "mindcraft_food_recovery.v1",
            "leaseId": "lease-1",
            "leaseProcessNonce": "lease-process-1",
            "producerNonce": "producer-1",
        }
        with (
            tempfile.TemporaryDirectory() as temporary,
            patch.object(
                mindcraft_service,
                "STATUS_PATH",
                Path(temporary) / "status.json",
            ),
            patch.object(runtime, "start") as start,
        ):
            runtime.restart_for_action(
                goal="fixed internal goal",
                world_effect_binding=binding,
            )

        start.assert_called_once_with(
            "fixed internal goal",
            world_effect_binding=binding,
            persist_goal_state=False,
        )


if __name__ == "__main__":
    unittest.main()
