from __future__ import annotations

import asyncio
import hashlib
import json
import sys
import threading
import unittest
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

from aiohttp import web
from aiohttp.test_utils import TestClient, TestServer


REPO_ROOT = next(
    path for path in Path(__file__).resolve().parents if (path / "main.py").exists()
)
RUNTIME_ROOT = REPO_ROOT / "evelyn_core" / "runtime"
if str(RUNTIME_ROOT) not in sys.path:
    sys.path.insert(0, str(RUNTIME_ROOT))

from evelyn_core import control_page_server  # noqa: E402


TASK_ID = "fast-action-7"
APPROVAL_ID = "approval-0123456789abcdef"
CLAIM = {
    "approvalId": APPROVAL_ID,
    "claimId": "claim-0123456789abcdef",
    "stageId": "stage-0123456789abcdef",
    "hostInstanceId": "host-0123456789abcdef",
    "taskId": TASK_ID,
    "grantId": "grant-0123456789abcdef",
    "grantExpiresAt": 4_000_000_000.0,
    "actionRunId": "action-0123456789abcdef",
    "stepId": 2,
    "surface": "control_page",
    "tool": "edit",
    "argsHash": "a" * 64,
    "baseSha256": "b" * 64,
    "candidateSha256": "c" * 64,
    "previewDigest": "d" * 64,
    "dirtyBaseAcknowledged": True,
}
MUTATION_RESULT = {
    "attempted": True,
    "executed": True,
    "observed": True,
    "verified": True,
    "outcome": "succeeded",
    "code": "workspace_edit_completed",
    "summary": "Workspace file edited.",
    "evidence": {"sha256": "c" * 64},
}


def public_preview_response(*, full_diff: str | None = None) -> dict:
    diff = (
        full_diff
        if full_diff is not None
        else "--- a/module.py\n+++ b/module.py\n@@ -1 +1 @@\n-old\n+new\n"
    )
    return {
        "ok": True,
        "schema": "task_approval.preview-response.v1",
        "preview": {
            "schema": "task_approval.preview.v1",
            "taskId": TASK_ID,
            "approvalId": APPROVAL_ID,
            "step": 2,
            "maxSteps": 5,
            "tool": "workspace_edit",
            "effect": "UTF-8 파일 1개 생성 또는 교체",
            "path": "module.py",
            "mode": "replace",
            "baseSha256": "b" * 64,
            "candidateSha256": "c" * 64,
            "diffSha256": hashlib.sha256(diff.encode("utf-8")).hexdigest(),
            "previewDigest": "e" * 64,
            "fullDiff": diff,
            "diffTruncated": False,
            "dirtyStatus": "modified",
            "gitStatus": " M module.py",
            "tracked": True,
            "dirtyBaseAcknowledgementRequired": True,
            "bytes": 4,
            "requiresExplicitConfirmation": True,
            "automaticRetry": False,
        },
        "confirmToken": "t" * 43,
        "confirmExpiresAt": 2_000_000_000.0,
    }


class TaskApprovalControlPageTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self) -> None:
        self.client = TestClient(
            TestServer(
                control_page_server.create_app(
                    manage_voice_capture_consent=False,
                )
            )
        )
        await self.client.start_server()
        self.origin = str(self.client.make_url("/")).rstrip("/")
        session = await self.client.get(
            "/api/control-page/session",
            headers={"Origin": self.origin},
        )
        self.csrf = (await session.json())["csrfToken"]

    async def asyncTearDown(self) -> None:
        await self.client.close()

    def headers(self) -> dict[str, str]:
        return {
            "Origin": self.origin,
            "X-Evelyn-CSRF-Token": self.csrf,
        }

    @staticmethod
    def direct_request(payload: dict) -> MagicMock:
        encoded = json.dumps(payload).encode("utf-8")
        request = MagicMock()
        request.content_length = len(encoded)
        request.read = AsyncMock(return_value=encoded)
        return request

    async def test_preview_requires_csrf_and_exact_locator(self) -> None:
        preview = public_preview_response()
        bot = AsyncMock(return_value=(200, preview))
        with patch.object(
            control_page_server,
            "_task_approval_bot_post",
            new=bot,
        ):
            missing_csrf = await self.client.post(
                "/api/control-page/task-approval/preview",
                headers={"Origin": self.origin},
                json={"taskId": TASK_ID, "approvalId": APPROVAL_ID},
            )
            extra = await self.client.post(
                "/api/control-page/task-approval/preview",
                headers=self.headers(),
                json={
                    "taskId": TASK_ID,
                    "approvalId": APPROVAL_ID,
                    "args": {"newText": "private"},
                },
            )
            accepted = await self.client.post(
                "/api/control-page/task-approval/preview",
                headers=self.headers(),
                json={"taskId": TASK_ID, "approvalId": APPROVAL_ID},
            )

        self.assertEqual(missing_csrf.status, 403)
        self.assertEqual(extra.status, 400)
        self.assertEqual(accepted.status, 200)
        bot.assert_awaited_once_with(
            "/internal/task-approval/preview",
            {"taskId": TASK_ID, "approvalId": APPROVAL_ID},
        )

    async def test_internal_bot_response_is_size_bounded(self) -> None:
        async def oversized(_request: web.Request) -> web.Response:
            return web.Response(
                body=b"x"
                * (control_page_server._TASK_APPROVAL_BOT_RESPONSE_MAX_BYTES + 1),
                content_type="application/json",
            )

        app = web.Application()
        app.router.add_post("/internal/task-approval/preview", oversized)
        server = TestServer(app)
        await server.start_server()
        try:
            with patch.object(
                control_page_server,
                "BOT_API_BASE",
                str(server.make_url("/")).rstrip("/"),
            ):
                status, payload = await control_page_server._task_approval_bot_post(
                    "/internal/task-approval/preview",
                    {"taskId": TASK_ID, "approvalId": APPROVAL_ID},
                )
        finally:
            await server.close()

        self.assertEqual(status, 502)
        self.assertEqual(payload["error"], "task_approval_bot_response_invalid")

    async def test_internal_preview_transport_accepts_host_bounded_escaped_diff(self) -> None:
        full_diff = "한" * 11_000 + "\\" * 8_000
        self.assertLessEqual(
            len(full_diff.encode("utf-8")),
            control_page_server.WORKSPACE_EDIT_MAX_PREVIEW_BYTES,
        )
        response_payload = public_preview_response(full_diff=full_diff)
        self.assertGreater(
            len(json.dumps(response_payload).encode("utf-8")),
            64 * 1024,
        )

        async def preview(_request: web.Request) -> web.Response:
            return web.json_response(response_payload)

        app = web.Application()
        app.router.add_post("/internal/task-approval/preview", preview)
        server = TestServer(app)
        await server.start_server()
        try:
            with patch.object(
                control_page_server,
                "BOT_API_BASE",
                str(server.make_url("/")).rstrip("/"),
            ):
                status, payload = await control_page_server._task_approval_bot_post(
                    "/internal/task-approval/preview",
                    {"taskId": TASK_ID, "approvalId": APPROVAL_ID},
                )
        finally:
            await server.close()

        self.assertEqual(status, 200)
        self.assertEqual(payload["preview"]["fullDiff"], full_diff)

    async def test_public_preview_rejects_extra_private_or_mismatched_fields(self) -> None:
        responses = []
        extra_top = public_preview_response()
        extra_top["rawArgs"] = {"newText": "private"}
        responses.append(extra_top)
        extra_preview = public_preview_response()
        extra_preview["preview"]["stageId"] = "stage-private"
        responses.append(extra_preview)
        mismatched = public_preview_response()
        mismatched["preview"]["taskId"] = "fast-action-other"
        responses.append(mismatched)

        with patch.object(
            control_page_server,
            "_task_approval_bot_post",
            new=AsyncMock(side_effect=[(200, value) for value in responses]),
        ):
            for _ in responses:
                response = await self.client.post(
                    "/api/control-page/task-approval/preview",
                    headers=self.headers(),
                    json={"taskId": TASK_ID, "approvalId": APPROVAL_ID},
                )
                payload = await response.json()
                self.assertEqual(response.status, 502)
                self.assertEqual(
                    payload["error"],
                    "task_approval_bot_response_invalid",
                )
                encoded = json.dumps(payload, ensure_ascii=False)
                self.assertNotIn("private", encoded)
                self.assertNotIn("stage-private", encoded)

    async def test_public_preview_reprojects_only_exact_safe_fields(self) -> None:
        preview = public_preview_response()
        with patch.object(
            control_page_server,
            "_task_approval_bot_post",
            new=AsyncMock(return_value=(200, preview)),
        ):
            response = await self.client.post(
                "/api/control-page/task-approval/preview",
                headers=self.headers(),
                json={"taskId": TASK_ID, "approvalId": APPROVAL_ID},
            )

        payload = await response.json()
        self.assertEqual(response.status, 200)
        self.assertEqual(set(payload), set(preview))
        self.assertEqual(set(payload["preview"]), set(preview["preview"]))
        self.assertEqual(payload["preview"]["effect"], "UTF-8 파일 1개 생성 또는 교체")

    async def test_apply_claims_once_mutates_once_and_completes_without_leaking_claim(self) -> None:
        calls: list[tuple[str, dict]] = []

        async def bot(path: str, payload: dict):
            calls.append((path, payload))
            if path.endswith("/claim"):
                return 200, {"ok": True, "claim": dict(CLAIM)}
            if path.endswith("/complete"):
                return 200, {"ok": True, "state": "resuming"}
            raise AssertionError(path)

        mutation_client = MagicMock()
        mutation_client.apply.return_value = dict(MUTATION_RESULT)
        with (
            patch.object(
                control_page_server,
                "_task_approval_bot_post",
                new=AsyncMock(side_effect=bot),
            ),
            patch.object(
                control_page_server,
                "WorkspaceMutationHostClient",
                return_value=mutation_client,
            ) as client_type,
            patch.object(
                control_page_server,
                "EVELYN_WORKSPACE_MUTATION_AUTH_TOKEN",
                "m" * 43,
            ),
        ):
            response = await self.client.post(
                "/api/control-page/task-approval/apply",
                headers=self.headers(),
                json={
                    "taskId": TASK_ID,
                    "approvalId": APPROVAL_ID,
                    "confirmToken": "t" * 43,
                    "userConfirmed": True,
                    "dirtyBaseAcknowledged": True,
                },
            )

        payload = await response.json()
        self.assertEqual(response.status, 202)
        self.assertEqual(payload["state"], "resuming")
        self.assertFalse(payload["automaticRetry"])
        self.assertNotIn("claim", payload)
        self.assertNotIn("confirmToken", payload)
        mutation_client.apply.assert_called_once_with(CLAIM)
        client_type.assert_called_once_with(
            timeout_sec=control_page_server.TASK_APPROVAL_MUTATION_TIMEOUT_SEC,
            auth_token="m" * 43,
        )
        self.assertEqual(calls[0][0], "/internal/task-approval/claim")
        self.assertTrue(calls[0][1]["dirtyBaseAcknowledged"])
        self.assertEqual(
            calls[1],
            (
                "/internal/task-approval/complete",
                {
                    "taskId": TASK_ID,
                    "approvalId": APPROVAL_ID,
                    "claimId": CLAIM["claimId"],
                    "result": MUTATION_RESULT,
                },
            ),
        )

    async def test_cancelled_apply_waits_for_host_and_completes_claim_once(self) -> None:
        entered = threading.Event()
        release = threading.Event()
        returned = threading.Event()
        calls: list[tuple[str, dict]] = []

        async def bot(path: str, payload: dict):
            calls.append((path, payload))
            if path.endswith("/claim"):
                return 200, {"ok": True, "claim": dict(CLAIM)}
            if path.endswith("/complete"):
                return 200, {"ok": True, "state": "resuming"}
            raise AssertionError(path)

        def apply(_claim: dict) -> dict:
            entered.set()
            release.wait(timeout=2.0)
            returned.set()
            return dict(MUTATION_RESULT)

        mutation_client = MagicMock()
        mutation_client.apply.side_effect = apply
        request = self.direct_request(
            {
                "taskId": TASK_ID,
                "approvalId": APPROVAL_ID,
                "confirmToken": "t" * 43,
                "userConfirmed": True,
                "dirtyBaseAcknowledged": True,
            }
        )
        with (
            patch.object(
                control_page_server,
                "_task_approval_bot_post",
                new=AsyncMock(side_effect=bot),
            ),
            patch.object(
                control_page_server,
                "WorkspaceMutationHostClient",
                return_value=mutation_client,
            ),
            patch.object(
                control_page_server,
                "EVELYN_WORKSPACE_MUTATION_AUTH_TOKEN",
                "m" * 43,
            ),
        ):
            outer = asyncio.create_task(
                control_page_server.task_approval_apply_handler(request)
            )
            self.assertTrue(await asyncio.to_thread(entered.wait, 1.0))
            outer.cancel()
            await asyncio.sleep(0)
            outer_was_draining = not outer.done()
            release.set()
            with self.assertRaises(asyncio.CancelledError):
                await asyncio.wait_for(outer, 1.0)

        self.assertTrue(outer_was_draining)
        self.assertTrue(returned.is_set())
        mutation_client.apply.assert_called_once_with(CLAIM)
        self.assertEqual(
            calls,
            [
                (
                    "/internal/task-approval/claim",
                    {
                        "taskId": TASK_ID,
                        "approvalId": APPROVAL_ID,
                        "confirmToken": "t" * 43,
                        "userConfirmed": True,
                        "dirtyBaseAcknowledged": True,
                    },
                ),
                (
                    "/internal/task-approval/complete",
                    {
                        "taskId": TASK_ID,
                        "approvalId": APPROVAL_ID,
                        "claimId": CLAIM["claimId"],
                        "result": MUTATION_RESULT,
                    },
                ),
            ],
        )

    async def test_replayed_click_cannot_dispatch_a_second_host_apply(self) -> None:
        claim_count = 0

        async def bot(path: str, payload: dict):
            nonlocal claim_count
            if path.endswith("/claim"):
                claim_count += 1
                if claim_count == 1:
                    return 200, {"ok": True, "claim": dict(CLAIM)}
                return 409, {"ok": False, "error": "task_approval_claim_denied"}
            return 200, {"ok": True}

        mutation_client = MagicMock()
        mutation_client.apply.return_value = dict(MUTATION_RESULT)
        request = {
            "taskId": TASK_ID,
            "approvalId": APPROVAL_ID,
            "confirmToken": "t" * 43,
            "userConfirmed": True,
            "dirtyBaseAcknowledged": True,
        }
        with (
            patch.object(
                control_page_server,
                "_task_approval_bot_post",
                new=AsyncMock(side_effect=bot),
            ),
            patch.object(
                control_page_server,
                "WorkspaceMutationHostClient",
                return_value=mutation_client,
            ),
            patch.object(
                control_page_server,
                "EVELYN_WORKSPACE_MUTATION_AUTH_TOKEN",
                "m" * 43,
            ),
        ):
            first = await self.client.post(
                "/api/control-page/task-approval/apply",
                headers=self.headers(),
                json=request,
            )
            second = await self.client.post(
                "/api/control-page/task-approval/apply",
                headers=self.headers(),
                json=request,
            )

        self.assertEqual(first.status, 202)
        self.assertEqual(second.status, 409)
        mutation_client.apply.assert_called_once_with(CLAIM)

    async def test_host_client_exception_completes_as_exact_unverified_result_without_retry(self) -> None:
        completions: list[dict] = []

        async def bot(path: str, payload: dict):
            if path.endswith("/claim"):
                return 200, {"ok": True, "claim": dict(CLAIM)}
            completions.append(payload)
            return 200, {"ok": True}

        mutation_client = MagicMock()
        mutation_client.apply.side_effect = OSError("result lost")
        with (
            patch.object(
                control_page_server,
                "_task_approval_bot_post",
                new=AsyncMock(side_effect=bot),
            ),
            patch.object(
                control_page_server,
                "WorkspaceMutationHostClient",
                return_value=mutation_client,
            ),
            patch.object(
                control_page_server,
                "EVELYN_WORKSPACE_MUTATION_AUTH_TOKEN",
                "m" * 43,
            ),
        ):
            response = await self.client.post(
                "/api/control-page/task-approval/apply",
                headers=self.headers(),
                json={
                    "taskId": TASK_ID,
                    "approvalId": APPROVAL_ID,
                    "confirmToken": "t" * 43,
                    "userConfirmed": True,
                    "dirtyBaseAcknowledged": True,
                },
            )

        self.assertEqual(response.status, 202)
        mutation_client.apply.assert_called_once_with(CLAIM)
        result = completions[0]["result"]
        self.assertEqual(
            set(result),
            {
                "attempted",
                "executed",
                "observed",
                "verified",
                "outcome",
                "code",
                "summary",
                "evidence",
            },
        )
        self.assertEqual(result["outcome"], "outcome_unverified")
        self.assertFalse(result["verified"])

    async def test_expired_grant_is_completed_without_dispatching_host_apply(self) -> None:
        completions: list[dict] = []
        expired_claim = {**CLAIM, "grantExpiresAt": 100.0}

        async def bot(path: str, payload: dict):
            if path.endswith("/claim"):
                return 200, {"ok": True, "claim": expired_claim}
            completions.append(payload)
            return 200, {"ok": True, "state": "resuming"}

        mutation_client = MagicMock()
        with (
            patch.object(
                control_page_server,
                "_task_approval_bot_post",
                new=AsyncMock(side_effect=bot),
            ),
            patch.object(
                control_page_server,
                "WorkspaceMutationHostClient",
                return_value=mutation_client,
            ),
            patch.object(control_page_server.time, "time", return_value=100.0),
        ):
            response = await self.client.post(
                "/api/control-page/task-approval/apply",
                headers=self.headers(),
                json={
                    "taskId": TASK_ID,
                    "approvalId": APPROVAL_ID,
                    "confirmToken": "t" * 43,
                    "userConfirmed": True,
                    "dirtyBaseAcknowledged": True,
                },
            )

        self.assertEqual(response.status, 202)
        mutation_client.apply.assert_not_called()
        self.assertEqual(
            completions[0]["result"]["code"],
            "task_grant_expired",
        )

    async def test_cancel_consumes_the_exact_host_stage(self) -> None:
        calls: list[tuple[str, dict]] = []

        async def bot(path: str, payload: dict):
            calls.append((path, payload))
            if path.endswith("/cancel"):
                return 200, {"ok": True, "claim": dict(CLAIM)}
            if path.endswith("/cancel-complete"):
                return 200, {"ok": True, "state": "cancelled"}
            raise AssertionError(path)

        mutation_client = MagicMock()
        mutation_client.cancel.return_value = {
            **MUTATION_RESULT,
            "code": "workspace_edit_stage_cancelled",
            "evidence": {
                "approvalId": APPROVAL_ID,
                "stageId": CLAIM["stageId"],
                "hostInstanceId": CLAIM["hostInstanceId"],
            },
        }
        with (
            patch.object(
                control_page_server,
                "_task_approval_bot_post",
                new=AsyncMock(side_effect=bot),
            ),
            patch.object(
                control_page_server,
                "WorkspaceMutationHostClient",
                return_value=mutation_client,
            ),
            patch.object(
                control_page_server,
                "EVELYN_WORKSPACE_MUTATION_AUTH_TOKEN",
                "m" * 43,
            ),
        ):
            response = await self.client.post(
                "/api/control-page/task-approval/cancel",
                headers=self.headers(),
                json={"taskId": TASK_ID, "approvalId": APPROVAL_ID},
            )

        self.assertEqual(response.status, 200)
        mutation_client.cancel.assert_called_once_with(CLAIM)
        self.assertEqual(
            calls,
            [
                (
                    "/internal/task-approval/cancel",
                    {"taskId": TASK_ID, "approvalId": APPROVAL_ID},
                ),
                (
                    "/internal/task-approval/cancel-complete",
                    {
                        "taskId": TASK_ID,
                        "approvalId": APPROVAL_ID,
                        "claimId": CLAIM["claimId"],
                        "result": mutation_client.cancel.return_value,
                    },
                ),
            ],
        )

    async def test_cancelled_cancel_waits_for_host_and_completes_claim_once(self) -> None:
        entered = threading.Event()
        release = threading.Event()
        returned = threading.Event()
        calls: list[tuple[str, dict]] = []
        cancel_result = {
            **MUTATION_RESULT,
            "code": "workspace_edit_stage_cancelled",
            "evidence": {
                "approvalId": APPROVAL_ID,
                "stageId": CLAIM["stageId"],
                "hostInstanceId": CLAIM["hostInstanceId"],
            },
        }

        async def bot(path: str, payload: dict):
            calls.append((path, payload))
            if path.endswith("/cancel"):
                return 200, {"ok": True, "claim": dict(CLAIM)}
            if path.endswith("/cancel-complete"):
                return 200, {"ok": True, "state": "cancelled"}
            raise AssertionError(path)

        def cancel(_claim: dict) -> dict:
            entered.set()
            release.wait(timeout=2.0)
            returned.set()
            return dict(cancel_result)

        mutation_client = MagicMock()
        mutation_client.cancel.side_effect = cancel
        request = self.direct_request(
            {"taskId": TASK_ID, "approvalId": APPROVAL_ID}
        )
        with (
            patch.object(
                control_page_server,
                "_task_approval_bot_post",
                new=AsyncMock(side_effect=bot),
            ),
            patch.object(
                control_page_server,
                "WorkspaceMutationHostClient",
                return_value=mutation_client,
            ),
            patch.object(
                control_page_server,
                "EVELYN_WORKSPACE_MUTATION_AUTH_TOKEN",
                "m" * 43,
            ),
        ):
            outer = asyncio.create_task(
                control_page_server.task_approval_cancel_handler(request)
            )
            self.assertTrue(await asyncio.to_thread(entered.wait, 1.0))
            outer.cancel()
            await asyncio.sleep(0)
            outer_was_draining = not outer.done()
            release.set()
            with self.assertRaises(asyncio.CancelledError):
                await asyncio.wait_for(outer, 1.0)

        self.assertTrue(outer_was_draining)
        self.assertTrue(returned.is_set())
        mutation_client.cancel.assert_called_once_with(CLAIM)
        self.assertEqual(
            calls,
            [
                (
                    "/internal/task-approval/cancel",
                    {"taskId": TASK_ID, "approvalId": APPROVAL_ID},
                ),
                (
                    "/internal/task-approval/cancel-complete",
                    {
                        "taskId": TASK_ID,
                        "approvalId": APPROVAL_ID,
                        "claimId": CLAIM["claimId"],
                        "result": cancel_result,
                    },
                ),
            ],
        )

    def test_mutation_secret_is_not_loaded_by_bot_api(self) -> None:
        source = (
            RUNTIME_ROOT / "evelyn_core" / "fast_control_api.py"
        ).read_text(encoding="utf-8")
        self.assertNotIn("EVELYN_WORKSPACE_MUTATION_AUTH_TOKEN", source)


if __name__ == "__main__":
    unittest.main()
