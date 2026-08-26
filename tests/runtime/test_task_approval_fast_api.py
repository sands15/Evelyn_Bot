from __future__ import annotations

import asyncio
import hashlib
import json
import sys
import unittest
from pathlib import Path
from unittest.mock import patch

from aiohttp.test_utils import TestClient, TestServer


REPO_ROOT = next(
    path for path in Path(__file__).resolve().parents if (path / "main.py").exists()
)
RUNTIME_ROOT = REPO_ROOT / "evelyn_core" / "runtime"
if str(RUNTIME_ROOT) not in sys.path:
    sys.path.insert(0, str(RUNTIME_ROOT))

from evelyn_core import fast_control_api as fast_api  # noqa: E402
from evelyn_core.task_approval_runtime import (  # noqa: E402
    TaskApprovalManager,
    TaskApprovalRequest,
)
from evelyn_core.workspace_task_tools import workspace_task_args_hash  # noqa: E402


AUTH_TOKEN = "internal-task-approval-token-0123456789abcdef"
TASK_ID = "fast-action-9"


def canonical_bytes(value):
    return json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")


def cancel_result(claim: dict) -> dict:
    return {
        "attempted": True,
        "executed": True,
        "observed": True,
        "verified": True,
        "outcome": "succeeded",
        "code": "workspace_edit_stage_cancelled",
        "summary": "cancelled",
        "evidence": {
            "approvalId": claim["approvalId"],
            "stageId": claim["stageId"],
            "hostInstanceId": claim["hostInstanceId"],
        },
    }


class TaskApprovalFastApiTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self) -> None:
        self.clock = 1_000.0
        self.manager = TaskApprovalManager(
            now=lambda: self.clock,
            generation="approval-test-generation",
        )
        self.manager_patch = patch.object(
            fast_api,
            "TASK_APPROVAL_MANAGER",
            self.manager,
        )
        self.token_patch = patch.object(
            fast_api,
            "EVELYN_INTERNAL_CONTROL_TOKEN",
            AUTH_TOKEN,
        )
        self.manager_patch.start()
        self.token_patch.start()
        fast_api.TASK_APPROVAL_CLAIMS.clear()
        self.waiters: list[asyncio.Task] = []
        self.client = TestClient(
            TestServer(
                fast_api.create_app(
                    enable_minecraft_world_lease_owner=False,
                )
            )
        )
        await self.client.start_server()

    async def asyncTearDown(self) -> None:
        for waiter in self.waiters:
            if not waiter.done():
                waiter.cancel()
        await asyncio.gather(*self.waiters, return_exceptions=True)
        await self.client.close()
        fast_api.TASK_APPROVAL_CLAIMS.clear()
        self.token_patch.stop()
        self.manager_patch.stop()

    @property
    def headers(self):
        return {fast_api.EVELYN_INTERNAL_CONTROL_HEADER: AUTH_TOKEN}

    async def start_pending(self, *, dirty_status="modified"):
        args = {
            "path": "module.py",
            "mode": "replace",
            "oldText": "old\n",
            "newText": "new\n",
            "expectedSha256": "b" * 64,
        }
        args_hash = workspace_task_args_hash(args)
        request = TaskApprovalRequest(
            task_id=TASK_ID,
            grant_id="grant-0123456789abcdef",
            action_run_id="action-0123456789abcdef",
            step_id=2,
            max_steps=5,
            tool="workspace_edit",
            args_hash=args_hash,
            surface="control_page",
            args=args,
            grant_expires_at=self.clock + 120.0,
        )
        full_diff = "--- a/module.py\n+++ b/module.py\n@@ -1 +1 @@\n-old\n+new\n"
        dirty_required = dirty_status not in {"clean", "absent"}
        stage = {
            "stageId": "stage-0123456789abcdef",
            "hostInstanceId": "host-0123456789abcdef",
            "path": "module.py",
            "mode": "replace",
            "baseSha256": "b" * 64,
            "candidateSha256": "c" * 64,
            "diffSha256": hashlib.sha256(full_diff.encode("utf-8")).hexdigest(),
            "fullDiff": full_diff,
            "diffTruncated": False,
            "gitStatus": " M module.py",
            "dirtyStatus": dirty_status,
            "tracked": True,
            "dirtyBaseAcknowledgementRequired": dirty_required,
            "bytes": 4,
            "issuedAt": self.clock,
            "expiresAt": self.clock + 120.0,
            "argsHash": args_hash,
        }
        stage["previewDigest"] = hashlib.sha256(canonical_bytes(stage)).hexdigest()
        waiter = asyncio.create_task(self.manager.wait(request, stage))
        self.waiters.append(waiter)
        await asyncio.sleep(0)
        snapshot = self.manager.public_snapshot()
        self.assertEqual(snapshot["taskId"], TASK_ID)
        return waiter, snapshot

    async def preview(self, snapshot):
        return await self.client.post(
            "/internal/task-approval/preview",
            headers=self.headers,
            json={
                "taskId": snapshot["taskId"],
                "approvalId": snapshot["approvalId"],
            },
        )

    async def test_preview_is_safe_and_claim_complete_is_one_shot(self) -> None:
        waiter, snapshot = await self.start_pending()
        preview_response = await self.preview(snapshot)
        preview_payload = await preview_response.json()

        self.assertEqual(preview_response.status, 200)
        self.assertEqual(
            preview_response.headers["Cache-Control"],
            "no-store",
        )
        preview = preview_payload["preview"]
        self.assertFalse(preview["diffTruncated"])
        self.assertEqual(preview["step"], 2)
        self.assertEqual(preview["maxSteps"], 5)
        self.assertIn("-old", preview["fullDiff"])
        self.assertEqual(preview["path"], "module.py")
        self.assertEqual(preview["gitStatus"], " M module.py")
        self.assertNotIn("stageId", preview)
        self.assertNotIn("hostInstanceId", preview)
        self.assertNotIn("argsHash", preview)
        self.assertNotIn("grantId", preview)

        claim_request = {
            "taskId": TASK_ID,
            "approvalId": snapshot["approvalId"],
            "confirmToken": preview_payload["confirmToken"],
            "userConfirmed": True,
            "dirtyBaseAcknowledged": True,
        }
        claim_response = await self.client.post(
            "/internal/task-approval/claim",
            headers=self.headers,
            json=claim_request,
        )
        claimed = await claim_response.json()
        replay = await self.client.post(
            "/internal/task-approval/claim",
            headers=self.headers,
            json=claim_request,
        )

        self.assertEqual(claim_response.status, 200)
        self.assertEqual(replay.status, 409)
        self.assertEqual(claimed["claim"]["tool"], "edit")
        self.assertEqual(claimed["claim"]["taskId"], TASK_ID)
        self.assertEqual(
            claimed["claim"]["grantExpiresAt"],
            self.clock + 120.0,
        )
        self.assertTrue(claimed["claim"]["dirtyBaseAcknowledged"])
        self.assertNotIn("generation", claimed["claim"])

        host_result = {
            "attempted": True,
            "executed": True,
            "observed": True,
            "verified": True,
            "outcome": "succeeded",
            "code": "workspace_edit_completed",
            "summary": "Workspace file edited.",
            "evidence": {"sha256": "c" * 64},
        }
        complete_response = await self.client.post(
            "/internal/task-approval/complete",
            headers=self.headers,
            json={
                "taskId": TASK_ID,
                "approvalId": snapshot["approvalId"],
                "claimId": claimed["claim"]["claimId"],
                "result": host_result,
            },
        )
        completion_replay = await self.client.post(
            "/internal/task-approval/complete",
            headers=self.headers,
            json={
                "taskId": TASK_ID,
                "approvalId": snapshot["approvalId"],
                "claimId": claimed["claim"]["claimId"],
                "result": host_result,
            },
        )

        self.assertEqual(complete_response.status, 200)
        self.assertEqual(completion_replay.status, 409)
        resolution = await waiter
        self.assertEqual(resolution.state, "approved")
        self.assertEqual(resolution.receipt["code"], "workspace_edit_completed")

    async def test_wrong_binding_expired_token_restart_and_browser_origin_fail_closed(self) -> None:
        _waiter, snapshot = await self.start_pending(dirty_status="clean")
        wrong = await self.client.post(
            "/internal/task-approval/preview",
            headers=self.headers,
            json={"taskId": "fast-action-wrong", "approvalId": snapshot["approvalId"]},
        )
        browser = await self.client.post(
            "/internal/task-approval/preview",
            headers={**self.headers, "Origin": "http://127.0.0.1:8799"},
            json={"taskId": TASK_ID, "approvalId": snapshot["approvalId"]},
        )
        missing_auth = await self.client.post(
            "/internal/task-approval/preview",
            json={"taskId": TASK_ID, "approvalId": snapshot["approvalId"]},
        )
        preview_payload = await (await self.preview(snapshot)).json()
        self.clock = float(preview_payload["confirmExpiresAt"]) + 0.1
        expired = await self.client.post(
            "/internal/task-approval/claim",
            headers=self.headers,
            json={
                "taskId": TASK_ID,
                "approvalId": snapshot["approvalId"],
                "confirmToken": preview_payload["confirmToken"],
                "userConfirmed": True,
                "dirtyBaseAcknowledged": False,
            },
        )

        self.assertEqual(wrong.status, 409)
        self.assertEqual(browser.status, 403)
        self.assertEqual(missing_auth.status, 403)
        self.assertEqual(expired.status, 409)

        restarted = TaskApprovalManager(
            now=lambda: self.clock,
            generation="approval-restarted-generation",
        )
        with patch.object(fast_api, "TASK_APPROVAL_MANAGER", restarted):
            after_restart = await self.client.post(
                "/internal/task-approval/claim",
                headers=self.headers,
                json={
                    "taskId": TASK_ID,
                    "approvalId": snapshot["approvalId"],
                    "confirmToken": preview_payload["confirmToken"],
                    "userConfirmed": True,
                    "dirtyBaseAcknowledged": False,
                },
            )
        self.assertEqual(after_restart.status, 409)

    async def test_public_state_has_locator_but_no_diff_or_private_binding(self) -> None:
        _waiter, snapshot = await self.start_pending()
        public = fast_api._public_fast_action_snapshot()
        approval = public["approval"]

        self.assertEqual(approval["taskId"], TASK_ID)
        self.assertEqual(approval["approvalId"], snapshot["approvalId"])
        self.assertEqual(approval["step"], 2)
        self.assertEqual(approval["maxSteps"], 5)
        encoded = json.dumps(public, ensure_ascii=False)
        for forbidden in (
            "fullDiff",
            "stageId",
            "hostInstanceId",
            "grantId",
            "actionRunId",
            "argsHash",
            "confirmToken",
            "oldText",
            "newText",
        ):
            self.assertNotIn(forbidden, encoded)

    async def test_cancel_returns_exact_claim_then_resolves_after_host_cleanup(self) -> None:
        waiter, snapshot = await self.start_pending()
        response = await self.client.post(
            "/internal/task-approval/cancel",
            headers=self.headers,
            json={"taskId": TASK_ID, "approvalId": snapshot["approvalId"]},
        )
        payload = await response.json()

        self.assertEqual(response.status, 200)
        self.assertEqual(payload["claim"]["tool"], "edit")
        self.assertFalse(payload["claim"]["dirtyBaseAcknowledged"])
        self.assertFalse(waiter.done())
        self.assertEqual(self.manager.public_snapshot()["state"], "cancelling")
        completion = await self.client.post(
            "/internal/task-approval/cancel-complete",
            headers=self.headers,
            json={
                "taskId": TASK_ID,
                "approvalId": snapshot["approvalId"],
                "claimId": payload["claim"]["claimId"],
                "result": cancel_result(payload["claim"]),
            },
        )

        self.assertEqual(completion.status, 200)
        resolution = await waiter
        self.assertEqual(resolution.state, "cancelled")

    async def test_cancel_after_claim_reuses_binding_and_denies_late_complete(self) -> None:
        waiter, snapshot = await self.start_pending()
        issued = await (await self.preview(snapshot)).json()
        claim_response = await self.client.post(
            "/internal/task-approval/claim",
            headers=self.headers,
            json={
                "taskId": TASK_ID,
                "approvalId": snapshot["approvalId"],
                "confirmToken": issued["confirmToken"],
                "userConfirmed": True,
                "dirtyBaseAcknowledged": True,
            },
        )
        claimed = (await claim_response.json())["claim"]

        cancel_response = await self.client.post(
            "/internal/task-approval/cancel",
            headers=self.headers,
            json={"taskId": TASK_ID, "approvalId": snapshot["approvalId"]},
        )
        cancelled = await cancel_response.json()
        late_complete = await self.client.post(
            "/internal/task-approval/complete",
            headers=self.headers,
            json={
                "taskId": TASK_ID,
                "approvalId": snapshot["approvalId"],
                "claimId": claimed["claimId"],
                "result": {
                    "attempted": True,
                    "executed": True,
                    "observed": True,
                    "verified": True,
                    "outcome": "succeeded",
                    "code": "workspace_edit_completed",
                    "summary": "Workspace file edited.",
                    "evidence": {},
                },
            },
        )

        self.assertEqual(cancel_response.status, 200)
        self.assertEqual(cancelled["claim"]["claimId"], claimed["claimId"])
        self.assertEqual(late_complete.status, 409)
        self.assertIn(claimed["claimId"], fast_api.TASK_APPROVAL_CLAIMS)
        cancel_complete = await self.client.post(
            "/internal/task-approval/cancel-complete",
            headers=self.headers,
            json={
                "taskId": TASK_ID,
                "approvalId": snapshot["approvalId"],
                "claimId": claimed["claimId"],
                "result": cancel_result(claimed),
            },
        )
        self.assertEqual(cancel_complete.status, 200)
        self.assertNotIn(claimed["claimId"], fast_api.TASK_APPROVAL_CLAIMS)
        self.assertEqual((await waiter).state, "cancelled")


if __name__ == "__main__":
    unittest.main()
