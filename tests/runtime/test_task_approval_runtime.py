from __future__ import annotations

import asyncio
import dataclasses
import hashlib
import json
import sys
import threading
import unittest
from pathlib import Path


RUNTIME_ROOT = Path(__file__).resolve().parents[2] / "evelyn_core" / "runtime"
if str(RUNTIME_ROOT) not in sys.path:
    sys.path.insert(0, str(RUNTIME_ROOT))

from evelyn_core.task_approval_runtime import (
    TASK_APPROVAL_PREVIEW_SCHEMA,
    TaskApprovalManager,
    TaskApprovalRequest,
)
from evelyn_core.workspace_task_tools import workspace_task_args_hash


def _request(*, task_id: str = "task-one", args: dict | None = None) -> TaskApprovalRequest:
    resolved_args = args or {
        "mode": "replace",
        "path": "README.md",
        "oldText": "old",
        "newText": "new",
        "expectedSha256": "a" * 64,
    }
    return TaskApprovalRequest(
        task_id=task_id,
        grant_id="grant-one",
        action_run_id="action-one",
        step_id=1,
        tool="workspace_edit",
        args_hash=workspace_task_args_hash(resolved_args),
        surface="control_page",
        args=resolved_args,
        max_steps=6,
        grant_expires_at=1_000.0,
    )


def _preview(
    request: TaskApprovalRequest,
    *,
    dirty_status: str = "clean",
    issued_at: float = 100.0,
    expires_at: float = 500.0,
) -> dict:
    full_diff = "--- a/README.md\n+++ b/README.md\n@@ -1 +1 @@\n-old\n+new\n"
    value = {
        "stageId": "stage-one",
        "hostInstanceId": "host-one",
        "path": "README.md",
        "mode": "replace",
        "baseSha256": "a" * 64,
        "candidateSha256": "b" * 64,
        "diffSha256": hashlib.sha256(full_diff.encode()).hexdigest(),
        "fullDiff": full_diff,
        "diffTruncated": False,
        "gitStatus": " M README.md" if dirty_status != "clean" else "",
        "dirtyStatus": dirty_status,
        "tracked": dirty_status != "untracked",
        "dirtyBaseAcknowledgementRequired": dirty_status
        in {"modified", "staged", "modified_and_staged", "untracked", "deleted"},
        "bytes": 3,
        "issuedAt": issued_at,
        "expiresAt": expires_at,
        "argsHash": request.args_hash,
    }
    value["previewDigest"] = hashlib.sha256(
        json.dumps(
            value,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        ).encode()
    ).hexdigest()
    return value


def _host_result(*, outcome: str = "succeeded", sha256: str = "b" * 64) -> dict:
    return {
        "attempted": True,
        "executed": outcome != "blocked",
        "observed": True,
        "verified": outcome != "outcome_unverified",
        "outcome": outcome,
        "code": "workspace_edit_completed",
        "summary": "done",
        "evidence": {"path": "README.md", "sha256": sha256},
    }


async def _started_wait(
    manager: TaskApprovalManager,
    request: TaskApprovalRequest,
    preview: dict,
) -> asyncio.Task:
    task = asyncio.create_task(manager.wait(request, preview))
    await asyncio.sleep(0)
    return task


class TaskApprovalRuntimeTests(unittest.IsolatedAsyncioTestCase):
    async def test_nonfinite_grant_expiry_never_admits_or_displaces_claim(self) -> None:
        for grant_expires_at in (float("inf"), float("nan")):
            with self.subTest(grant_expires_at=grant_expires_at):
                manager = TaskApprovalManager(now=lambda: 100.0)
                malformed = dataclasses.replace(
                    _request(),
                    grant_expires_at=grant_expires_at,
                )

                self.assertFalse(malformed.valid())
                self.assertEqual(
                    (await manager.wait(malformed, _preview(malformed))).state,
                    "unsupported",
                )
                self.assertEqual(manager.public_snapshot(), {})

                request = _request()
                waiter = await _started_wait(manager, request, _preview(request))
                public = manager.public_snapshot()
                issued = manager.issue_preview(request.task_id, public["approvalId"])
                claim = manager.claim(
                    request.task_id,
                    public["approvalId"],
                    issued["confirmToken"],
                    True,
                )
                assert claim is not None

                self.assertEqual(
                    (await manager.wait(malformed, _preview(malformed))).state,
                    "unsupported",
                )
                self.assertEqual(manager.task_cancel_barrier(request.task_id), "claimed")
                self.assertTrue(manager.complete(claim, _host_result()))
                self.assertEqual((await waiter).state, "approved")
                self.assertTrue(manager.release_task_cancel_barrier(request.task_id))
                self.assertEqual(manager.public_snapshot(), {})

    async def test_exact_lifecycle_redacts_public_state_and_consumes_token_once(self) -> None:
        now = [100.0]
        manager = TaskApprovalManager(now=lambda: now[0], generation="generation-one")
        request = _request()
        waiter = await _started_wait(manager, request, _preview(request))

        public = manager.public_snapshot()
        encoded_public = json.dumps(public)
        self.assertEqual(public["taskId"], request.task_id)
        self.assertNotIn("fullDiff", encoded_public)
        self.assertNotIn("oldText", encoded_public)
        self.assertNotIn("newText", encoded_public)
        self.assertNotIn("Token", encoded_public)

        issued = manager.issue_preview(public["taskId"], public["approvalId"])
        self.assertTrue(issued["ok"])
        self.assertEqual(issued["preview"]["schema"], TASK_APPROVAL_PREVIEW_SCHEMA)
        token = issued["confirmToken"]
        self.assertNotIn(token, repr(manager))
        claim = manager.claim(
            public["taskId"],
            public["approvalId"],
            token,
            True,
        )
        self.assertIsNotNone(claim)
        assert claim is not None
        self.assertIsNone(
            manager.claim(public["taskId"], public["approvalId"], token, True)
        )
        self.assertEqual(
            set(claim.to_host_claim()),
            {
                "approvalId",
                "claimId",
                "stageId",
                "hostInstanceId",
                "taskId",
                "grantId",
                "grantExpiresAt",
                "actionRunId",
                "stepId",
                "surface",
                "tool",
                "argsHash",
                "baseSha256",
                "candidateSha256",
                "previewDigest",
                "dirtyBaseAcknowledged",
            },
        )
        self.assertEqual(
            claim.to_host_claim()["grantExpiresAt"],
            request.grant_expires_at,
        )
        self.assertTrue(manager.complete(claim, _host_result()))
        self.assertFalse(manager.complete(claim, _host_result()))
        resolution = await waiter
        self.assertEqual(resolution.state, "approved")
        self.assertEqual(resolution.receipt["outcome"], "succeeded")
        self.assertEqual(
            manager.task_cancel_barrier(request.task_id),
            "resuming",
        )
        self.assertFalse(manager.release_task_cancel_barrier("other-task"))
        self.assertTrue(manager.release_task_cancel_barrier(request.task_id))
        self.assertEqual(manager.public_snapshot(), {})

    async def test_post_apply_barrier_blocks_successor_until_verification_finishes(self) -> None:
        manager = TaskApprovalManager(now=lambda: 100.0)
        request = _request()
        waiter = await _started_wait(manager, request, _preview(request))
        public = manager.public_snapshot()
        issued = manager.issue_preview(request.task_id, public["approvalId"])
        claim = manager.claim(
            request.task_id,
            public["approvalId"],
            issued["confirmToken"],
            True,
        )
        assert claim is not None

        self.assertTrue(manager.complete(claim, _host_result()))
        self.assertEqual((await waiter).state, "approved")
        blocked = await manager.wait(
            _request(task_id="task-two"),
            _preview(_request(task_id="task-two")),
        )

        self.assertEqual(blocked.state, "unsupported")
        self.assertEqual(manager.task_cancel_barrier(request.task_id), "resuming")
        self.assertTrue(manager.release_task_cancel_barrier(request.task_id))

    async def test_expired_claim_remains_a_direct_cancel_barrier(self) -> None:
        now = [100.0]
        manager = TaskApprovalManager(now=lambda: now[0])
        request = _request()
        waiter = await _started_wait(manager, request, _preview(request))
        public = manager.public_snapshot()
        issued = manager.issue_preview(request.task_id, public["approvalId"])
        claim = manager.claim(
            request.task_id,
            public["approvalId"],
            issued["confirmToken"],
            True,
        )
        self.assertIsNotNone(claim)

        now[0] = 501.0
        self.assertEqual(manager.public_snapshot(), {})
        self.assertEqual(
            manager.task_cancel_barrier(request.task_id),
            "claimed",
        )
        self.assertEqual(manager.task_cancel_barrier("other-task"), "")

        self.assertIsNone(manager.cancel(request.task_id, public["approvalId"]))
        resolution = await waiter
        self.assertEqual(resolution.state, "uncertain")

    async def test_claim_requires_every_public_binding_confirmation_and_dirty_iff(self) -> None:
        manager = TaskApprovalManager(now=lambda: 100.0)
        request = _request()
        waiter = await _started_wait(
            manager,
            request,
            _preview(request, dirty_status="modified"),
        )
        public = manager.public_snapshot()
        issued = manager.issue_preview(request.task_id, public["approvalId"])
        token = issued["confirmToken"]

        attempts = (
            ("wrong-task", public["approvalId"], token, True, True),
            (request.task_id, "wrong-approval", token, True, True),
            (request.task_id, public["approvalId"], "wrong-token", True, True),
            (request.task_id, public["approvalId"], token, False, True),
            (request.task_id, public["approvalId"], token, True, False),
        )
        for values in attempts:
            with self.subTest(values=values[:2]):
                self.assertIsNone(manager.claim(*values))

        claim = manager.claim(
            request.task_id,
            public["approvalId"],
            token,
            True,
            True,
        )
        self.assertIsNotNone(claim)
        assert claim is not None
        self.assertTrue(claim.dirty_base_acknowledged)
        self.assertTrue(manager.complete(claim, _host_result()))
        self.assertEqual((await waiter).state, "approved")

    async def test_clean_preview_rejects_spurious_dirty_acknowledgement(self) -> None:
        manager = TaskApprovalManager(now=lambda: 100.0)
        request = _request()
        waiter = await _started_wait(manager, request, _preview(request))
        public = manager.public_snapshot()
        issued = manager.issue_preview(request.task_id, public["approvalId"])
        token = issued["confirmToken"]
        self.assertIsNone(
            manager.claim(request.task_id, public["approvalId"], token, True, True)
        )
        claim = manager.claim(request.task_id, public["approvalId"], token, True, False)
        self.assertIsNotNone(claim)
        assert claim is not None
        manager.complete(claim, _host_result())
        self.assertEqual((await waiter).state, "approved")

    async def test_preview_token_and_pending_approval_expire_without_replay(self) -> None:
        now = [100.0]
        manager = TaskApprovalManager(now=lambda: now[0])
        request = _request()
        waiter = await _started_wait(manager, request, _preview(request))
        public = manager.public_snapshot()
        issued = manager.issue_preview(request.task_id, public["approvalId"])
        now[0] = 131.0
        self.assertIsNone(
            manager.claim(
                request.task_id,
                public["approvalId"],
                issued["confirmToken"],
                True,
            )
        )
        renewed = manager.issue_preview(request.task_id, public["approvalId"])
        self.assertTrue(renewed["ok"])
        now[0] = 501.0
        self.assertFalse(manager.issue_preview(request.task_id, public["approvalId"])["ok"])
        self.assertEqual((await waiter).state, "expired")

    async def test_restart_does_not_restore_or_accept_old_token(self) -> None:
        first = TaskApprovalManager(now=lambda: 100.0, generation="generation-one")
        request = _request()
        waiter = await _started_wait(first, request, _preview(request))
        public = first.public_snapshot()
        issued = first.issue_preview(request.task_id, public["approvalId"])

        restarted = TaskApprovalManager(now=lambda: 100.0, generation="generation-two")
        self.assertEqual(restarted.public_snapshot(), {})
        self.assertIsNone(
            restarted.claim(
                request.task_id,
                public["approvalId"],
                issued["confirmToken"],
                True,
            )
        )
        cancel_claim = first.cancel(request.task_id, public["approvalId"])
        self.assertIsNotNone(cancel_claim)
        self.assertEqual((await waiter).state, "cancelled")

    async def test_only_one_live_edit_approval_exists_and_second_never_waits(self) -> None:
        manager = TaskApprovalManager(now=lambda: 100.0)
        first_request = _request(task_id="task-one")
        first = await _started_wait(manager, first_request, _preview(first_request))
        second_request = _request(task_id="task-two")

        second = await asyncio.wait_for(
            manager.wait(second_request, _preview(second_request)),
            timeout=0.1,
        )
        self.assertEqual(second.state, "unsupported")
        public = manager.public_snapshot()
        self.assertEqual(public["taskId"], "task-one")
        manager.cancel("task-one", public["approvalId"])
        self.assertEqual((await first).state, "cancelled")

    async def test_expired_first_waiter_is_resolved_before_successor_install(self) -> None:
        now = [100.0]
        manager = TaskApprovalManager(now=lambda: now[0])
        first_request = _request(task_id="task-one")
        first = await _started_wait(manager, first_request, _preview(first_request))
        now[0] = 401.0
        second_request = _request(task_id="task-two")
        second = await _started_wait(
            manager,
            second_request,
            _preview(second_request, issued_at=400.0, expires_at=500.0),
        )

        self.assertEqual((await asyncio.wait_for(first, 0.1)).state, "expired")
        public = manager.public_snapshot()
        self.assertEqual(public["taskId"], "task-two")
        manager.cancel("task-two", public["approvalId"])
        self.assertEqual((await second).state, "cancelled")

    async def test_request_arguments_are_deeply_frozen_and_hash_bound(self) -> None:
        source = {
            "mode": "create",
            "path": "new.txt",
            "newText": "safe",
            "nested": {"items": ["one"]},
        }
        request = _request(args=source)
        source["newText"] = "changed"
        source["nested"]["items"].append("two")

        self.assertEqual(request.args["newText"], "safe")
        self.assertEqual(tuple(request.args["nested"]["items"]), ("one",))
        with self.assertRaises(TypeError):
            request.args["newText"] = "mutated"
        self.assertTrue(request.valid())

    async def test_cancel_after_claim_resolves_uncertain_and_returns_same_host_claim(self) -> None:
        manager = TaskApprovalManager(now=lambda: 100.0)
        request = _request()
        waiter = await _started_wait(manager, request, _preview(request))
        public = manager.public_snapshot()
        issued = manager.issue_preview(request.task_id, public["approvalId"])
        claim = manager.claim(
            request.task_id,
            public["approvalId"],
            issued["confirmToken"],
            True,
        )
        self.assertIsNotNone(claim)

        cancelled_claim = manager.cancel(request.task_id, public["approvalId"])
        self.assertEqual(cancelled_claim, claim)
        self.assertEqual((await asyncio.wait_for(waiter, 0.1)).state, "uncertain")

    async def test_cancel_before_claim_consumes_pending_authority_and_token(self) -> None:
        manager = TaskApprovalManager(now=lambda: 100.0)
        request = _request()
        waiter = await _started_wait(manager, request, _preview(request))
        public = manager.public_snapshot()
        issued = manager.issue_preview(request.task_id, public["approvalId"])

        cancel_claim = manager.cancel(request.task_id, public["approvalId"])
        self.assertIsNotNone(cancel_claim)
        self.assertIsNone(
            manager.claim(
                request.task_id,
                public["approvalId"],
                issued["confirmToken"],
                True,
            )
        )
        self.assertEqual((await waiter).state, "cancelled")
        self.assertEqual(manager.public_snapshot(), {})

    async def test_two_phase_cancel_resolves_only_after_exact_host_cleanup(self) -> None:
        manager = TaskApprovalManager(now=lambda: 100.0, generation="generation-one")
        request = _request()
        waiter = await _started_wait(manager, request, _preview(request))
        public = manager.public_snapshot()

        claim = manager.prepare_cancel(request.task_id, public["approvalId"])
        self.assertIsNotNone(claim)
        assert claim is not None
        self.assertFalse(waiter.done())
        self.assertEqual(manager.public_snapshot()["state"], "cancelling")
        result = {
            "attempted": True,
            "executed": True,
            "observed": True,
            "verified": True,
            "outcome": "succeeded",
            "code": "workspace_edit_stage_cancelled",
            "summary": "cancelled",
            "evidence": {
                "approvalId": claim.approval_id,
                "stageId": claim.stage_id,
                "hostInstanceId": claim.host_instance_id,
            },
        }

        self.assertTrue(manager.complete_cancel(claim, result))
        self.assertFalse(manager.complete_cancel(claim, result))
        resolution = await asyncio.wait_for(waiter, 0.1)
        self.assertEqual(resolution.state, "cancelled")
        self.assertEqual(resolution.receipt["code"], "workspace_edit_stage_cancelled")

    async def test_two_phase_cancel_binding_mismatch_is_uncertain(self) -> None:
        manager = TaskApprovalManager(now=lambda: 100.0)
        request = _request()
        waiter = await _started_wait(manager, request, _preview(request))
        public = manager.public_snapshot()
        claim = manager.prepare_cancel(request.task_id, public["approvalId"])
        assert claim is not None
        forged = {
            "attempted": True,
            "executed": True,
            "observed": True,
            "verified": True,
            "outcome": "succeeded",
            "code": "workspace_edit_stage_cancelled",
            "summary": "cancelled",
            "evidence": {
                "approvalId": claim.approval_id,
                "stageId": "stage-other",
                "hostInstanceId": claim.host_instance_id,
            },
        }

        self.assertTrue(manager.complete_cancel(claim, forged))
        self.assertEqual((await asyncio.wait_for(waiter, 0.1)).state, "uncertain")

    async def test_concurrent_claim_cancel_linearizes_to_one_exact_authority(self) -> None:
        manager = TaskApprovalManager(now=lambda: 100.0)
        request = _request()
        waiter = await _started_wait(manager, request, _preview(request))
        public = manager.public_snapshot()
        issued = manager.issue_preview(request.task_id, public["approvalId"])
        barrier = threading.Barrier(2)

        def claim():
            barrier.wait()
            return manager.claim(
                request.task_id,
                public["approvalId"],
                issued["confirmToken"],
                True,
            )

        def cancel():
            barrier.wait()
            return manager.cancel(request.task_id, public["approvalId"])

        claimed, cancelled = await asyncio.gather(
            asyncio.to_thread(claim),
            asyncio.to_thread(cancel),
        )
        authorities = [value for value in (claimed, cancelled) if value is not None]
        self.assertTrue(authorities)
        self.assertEqual({value.claim_id for value in authorities}, {authorities[0].claim_id})
        resolution = await asyncio.wait_for(waiter, 0.1)
        self.assertIn(resolution.state, {"cancelled", "uncertain"})

    async def test_cross_thread_complete_wakes_owner_loop_safely(self) -> None:
        manager = TaskApprovalManager(now=lambda: 100.0)
        request = _request()
        waiter = await _started_wait(manager, request, _preview(request))
        public = manager.public_snapshot()
        issued = manager.issue_preview(request.task_id, public["approvalId"])
        claim = manager.claim(request.task_id, public["approvalId"], issued["confirmToken"], True)
        assert claim is not None

        completed = await asyncio.to_thread(manager.complete, claim, _host_result())
        self.assertTrue(completed)
        self.assertEqual((await asyncio.wait_for(waiter, 0.1)).state, "approved")

    async def test_claimed_approval_expiry_is_uncertain_not_safe_expired(self) -> None:
        now = [100.0]
        manager = TaskApprovalManager(now=lambda: now[0])
        request = _request()
        waiter = await _started_wait(manager, request, _preview(request))
        public = manager.public_snapshot()
        issued = manager.issue_preview(request.task_id, public["approvalId"])
        self.assertIsNotNone(
            manager.claim(
                request.task_id,
                public["approvalId"],
                issued["confirmToken"],
                True,
            )
        )
        now[0] = 501.0
        manager.issue_preview(request.task_id, public["approvalId"])
        self.assertEqual((await waiter).state, "uncertain")

    async def test_late_host_success_after_claim_is_uncertain_not_approved(self) -> None:
        now = [100.0]
        manager = TaskApprovalManager(now=lambda: now[0])
        request = _request()
        waiter = await _started_wait(manager, request, _preview(request))
        public = manager.public_snapshot()
        issued = manager.issue_preview(request.task_id, public["approvalId"])
        claim = manager.claim(
            request.task_id,
            public["approvalId"],
            issued["confirmToken"],
            True,
        )
        assert claim is not None

        now[0] = public["expiresAt"] + 1.0

        self.assertFalse(manager.complete(claim, _host_result()))
        resolution = await asyncio.wait_for(waiter, 0.1)
        self.assertEqual(resolution.state, "uncertain")
        self.assertEqual(resolution.receipt["outcome"], "outcome_unverified")
        self.assertNotEqual(
            manager.task_cancel_barrier(request.task_id),
            "resuming",
        )

    async def test_complete_rejects_each_forged_private_binding(self) -> None:
        manager = TaskApprovalManager(now=lambda: 100.0, generation="generation-one")
        request = _request()
        waiter = await _started_wait(manager, request, _preview(request))
        public = manager.public_snapshot()
        issued = manager.issue_preview(request.task_id, public["approvalId"])
        claim = manager.claim(request.task_id, public["approvalId"], issued["confirmToken"], True)
        assert claim is not None

        forged_requests = (
            dataclasses.replace(request, task_id="task-other"),
            dataclasses.replace(request, grant_id="grant-other"),
            dataclasses.replace(request, action_run_id="action-other"),
            dataclasses.replace(request, step_id=2),
            dataclasses.replace(request, tool="workspace_other"),
            dataclasses.replace(request, args_hash="c" * 64),
            dataclasses.replace(request, args={"path": "other.txt"}),
            dataclasses.replace(request, surface="surface-other"),
            dataclasses.replace(request, max_steps=7),
            dataclasses.replace(request, grant_expires_at=999.0),
        )
        forged_claims = [dataclasses.replace(claim, request=value) for value in forged_requests]
        forged_claims.extend(
            [
                dataclasses.replace(claim, approval_id="approval-other"),
                dataclasses.replace(claim, claim_id="claim-other"),
                dataclasses.replace(claim, generation="generation-other"),
                dataclasses.replace(claim, stage_id="stage-other"),
                dataclasses.replace(claim, host_instance_id="host-other"),
                dataclasses.replace(claim, base_sha256="c" * 64),
                dataclasses.replace(claim, candidate_sha256="c" * 64),
                dataclasses.replace(claim, preview_digest="c" * 64),
                dataclasses.replace(claim, dirty_base_acknowledged=True),
            ]
        )
        for forged in forged_claims:
            with self.subTest(claim=forged.claim_id):
                self.assertFalse(manager.complete(forged, _host_result()))
        self.assertTrue(manager.complete(claim, _host_result()))
        self.assertEqual((await waiter).state, "approved")

    async def test_completed_host_sha_must_match_exact_staged_candidate(self) -> None:
        manager = TaskApprovalManager(now=lambda: 100.0)
        request = _request()
        waiter = await _started_wait(manager, request, _preview(request))
        public = manager.public_snapshot()
        issued = manager.issue_preview(request.task_id, public["approvalId"])
        claim = manager.claim(request.task_id, public["approvalId"], issued["confirmToken"], True)
        assert claim is not None

        self.assertTrue(manager.complete(claim, _host_result(sha256="c" * 64)))
        resolution = await waiter
        self.assertEqual(resolution.state, "uncertain")
        self.assertEqual(resolution.receipt["outcome"], "outcome_unverified")

    async def test_malformed_preview_and_result_fail_closed(self) -> None:
        manager = TaskApprovalManager(now=lambda: 100.0)
        request = _request()
        malformed = _preview(request)
        malformed["diffTruncated"] = True
        self.assertEqual((await manager.wait(request, malformed)).state, "unsupported")

        waiter = await _started_wait(manager, request, _preview(request))
        public = manager.public_snapshot()
        issued = manager.issue_preview(request.task_id, public["approvalId"])
        claim = manager.claim(request.task_id, public["approvalId"], issued["confirmToken"], True)
        assert claim is not None
        self.assertTrue(manager.complete(claim, {"outcome": "succeeded"}))
        resolution = await waiter
        self.assertEqual(resolution.state, "uncertain")
        self.assertEqual(resolution.receipt["outcome"], "outcome_unverified")


if __name__ == "__main__":
    unittest.main()
