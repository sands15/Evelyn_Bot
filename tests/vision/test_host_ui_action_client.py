from __future__ import annotations

import asyncio
import json
from pathlib import Path
import sys
import tempfile
import time
import unittest


REPO_ROOT = next(
    path for path in Path(__file__).resolve().parents if (path / "main.py").exists()
)
RUNTIME_ROOT = REPO_ROOT / "evelyn_core" / "runtime"
if str(RUNTIME_ROOT) not in sys.path:
    sys.path.insert(0, str(RUNTIME_ROOT))

from evelyn_core.host_ui_action_client import (  # noqa: E402
    apply_host_ui_action,
    preview_host_ui_action,
)
from evelyn_core.runtime_artifact_io import atomic_json_write  # noqa: E402


class HostUiActionClientTests(unittest.IsolatedAsyncioTestCase):
    async def fake_host(
        self,
        root: Path,
        *,
        response_age_sec: float = 0.0,
    ) -> None:
        requests = root / "host_ui_action" / "requests"
        for _ in range(100):
            candidates = list(requests.glob("*.json")) if requests.exists() else []
            if candidates:
                break
            await asyncio.sleep(0.005)
        request = json.loads(candidates[0].read_text(encoding="utf-8"))
        request_id = request["requestId"]
        operation = request["operation"]
        created_at = time.time() - response_age_sec
        preview = {}
        result = {}
        if operation == "preview":
            preview = {
                "ok": True,
                "schema": "ui_action.preview.v1",
                "confirmToken": "t" * 43,
                "expiresAt": created_at + 30.0,
                "requiresExplicitConfirmation": True,
                "action": "invoke",
                "postcondition": "target_absent",
                "target": {
                    "elementId": "a" * 20,
                    "name": "확인",
                    "controlType": "Button",
                    "windowTitle": "Evelyn",
                    "windowClass": "Chrome_WidgetWin_1",
                },
                "policy": {
                    "reobserveBeforeExecute": True,
                    "verifyAfterExecute": True,
                    "automaticRetry": False,
                    "arbitraryCoordinates": False,
                },
            }
        else:
            result = {
                "ok": True,
                "schema": "ui_action.result.v1",
                "state": "verified",
                "error": "",
                "operationId": f"ui-action-{'a' * 24}",
                "action": "invoke",
                "postcondition": "target_absent",
                "executed": True,
                "verified": True,
                "automaticRetry": False,
            }
        atomic_json_write(
            root
            / "host_ui_action"
            / "responses"
            / f"{request_id}.json",
            {
                "schema": "host_ui_action.response.v1",
                "requestId": request_id,
                "createdAt": created_at,
                "expiresAt": created_at + 30.0,
                "ok": True,
                "operation": operation,
                "errorCode": "",
                "preview": preview,
                "result": result,
            },
        )

    async def test_preview_and_apply_contracts_are_consumed(self) -> None:
        with tempfile.TemporaryDirectory() as temp_root:
            root = Path(temp_root)
            preview_task = asyncio.create_task(self.fake_host(root))
            preview = await preview_host_ui_action(
                element_id="a" * 20,
                action="invoke",
                postcondition="target_absent",
                artifacts_root=root,
                timeout_sec=1.0,
                poll_interval_sec=0.005,
            )
            await preview_task
            apply_task = asyncio.create_task(self.fake_host(root))
            applied = await apply_host_ui_action(
                confirm_token=preview["preview"]["confirmToken"],
                artifacts_root=root,
                timeout_sec=1.0,
                poll_interval_sec=0.005,
            )
            await apply_task
            requests = list(
                (root / "host_ui_action" / "requests").glob("*.json")
            )
            responses = list(
                (root / "host_ui_action" / "responses").glob("*.json")
            )

        self.assertTrue(preview["ok"])
        self.assertEqual(preview["preview"]["target"]["name"], "확인")
        self.assertTrue(applied["ok"])
        self.assertTrue(applied["result"]["verified"])
        self.assertEqual(requests, [])
        self.assertEqual(responses, [])

    async def test_stale_response_fails_closed(self) -> None:
        with tempfile.TemporaryDirectory() as temp_root:
            root = Path(temp_root)
            host_task = asyncio.create_task(
                self.fake_host(root, response_age_sec=11.0)
            )
            result = await preview_host_ui_action(
                element_id="a" * 20,
                action="invoke",
                postcondition="target_absent",
                artifacts_root=root,
                timeout_sec=1.0,
                poll_interval_sec=0.005,
            )
            await host_task

        self.assertFalse(result["ok"])
        self.assertEqual(result["error"], "ui_action_response_stale")

    async def test_incomplete_preview_contract_fails_closed(self) -> None:
        async def invalid_host(root: Path) -> None:
            requests = root / "host_ui_action" / "requests"
            for _ in range(100):
                candidates = (
                    list(requests.glob("*.json"))
                    if requests.exists()
                    else []
                )
                if candidates:
                    break
                await asyncio.sleep(0.005)
            request = json.loads(candidates[0].read_text(encoding="utf-8"))
            created_at = time.time()
            atomic_json_write(
                root
                / "host_ui_action"
                / "responses"
                / f"{request['requestId']}.json",
                {
                    "schema": "host_ui_action.response.v1",
                    "requestId": request["requestId"],
                    "createdAt": created_at,
                    "expiresAt": created_at + 30.0,
                    "ok": True,
                    "operation": "preview",
                    "errorCode": "",
                    "preview": {
                        "schema": "ui_action.preview.v1",
                        "confirmToken": "t" * 43,
                    },
                    "result": {},
                },
            )

        with tempfile.TemporaryDirectory() as temp_root:
            root = Path(temp_root)
            host_task = asyncio.create_task(invalid_host(root))
            result = await preview_host_ui_action(
                element_id="a" * 20,
                action="invoke",
                postcondition="target_absent",
                artifacts_root=root,
                timeout_sec=1.0,
                poll_interval_sec=0.005,
            )
            await host_task

        self.assertFalse(result["ok"])
        self.assertEqual(
            result["error"],
            "ui_action_invalid_preview_contract",
        )

    async def test_unverified_execution_is_preserved_as_failure(self) -> None:
        async def unverified_host(root: Path) -> None:
            requests = root / "host_ui_action" / "requests"
            for _ in range(100):
                candidates = (
                    list(requests.glob("*.json"))
                    if requests.exists()
                    else []
                )
                if candidates:
                    break
                await asyncio.sleep(0.005)
            request = json.loads(candidates[0].read_text(encoding="utf-8"))
            created_at = time.time()
            error = "ui_action_outcome_unverified"
            atomic_json_write(
                root
                / "host_ui_action"
                / "responses"
                / f"{request['requestId']}.json",
                {
                    "schema": "host_ui_action.response.v1",
                    "requestId": request["requestId"],
                    "createdAt": created_at,
                    "expiresAt": created_at + 30.0,
                    "ok": False,
                    "operation": "apply",
                    "errorCode": error,
                    "preview": {},
                    "result": {
                        "ok": False,
                        "schema": "ui_action.result.v1",
                        "state": "outcome_unverified",
                        "error": error,
                        "operationId": f"ui-action-{'a' * 24}",
                        "action": "invoke",
                        "postcondition": "target_absent",
                        "executed": True,
                        "verified": False,
                        "automaticRetry": False,
                    },
                },
            )

        with tempfile.TemporaryDirectory() as temp_root:
            root = Path(temp_root)
            host_task = asyncio.create_task(unverified_host(root))
            result = await apply_host_ui_action(
                confirm_token="t" * 43,
                artifacts_root=root,
                timeout_sec=1.0,
                poll_interval_sec=0.005,
            )
            await host_task

        self.assertFalse(result["ok"])
        self.assertEqual(result["error"], "ui_action_outcome_unverified")
        self.assertTrue(result["result"]["executed"])
        self.assertFalse(result["result"]["verified"])

    async def test_arbitrary_input_is_rejected_before_queue_write(self) -> None:
        with tempfile.TemporaryDirectory() as temp_root:
            root = Path(temp_root)
            result = await preview_host_ui_action(
                element_id="1; calc.exe",
                action="click",
                postcondition="anything_changed",
                artifacts_root=root,
            )
            queued = list(root.rglob("*.json"))

        self.assertFalse(result["ok"])
        self.assertEqual(
            result["error"],
            "ui_action_invalid_preview_request",
        )
        self.assertEqual(queued, [])


if __name__ == "__main__":
    unittest.main()
