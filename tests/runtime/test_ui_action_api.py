from __future__ import annotations

import sys
import unittest
from pathlib import Path
from unittest.mock import AsyncMock, patch

from aiohttp.test_utils import TestClient, TestServer


REPO_ROOT = next(
    path for path in Path(__file__).resolve().parents if (path / "main.py").exists()
)
RUNTIME_ROOT = REPO_ROOT / "evelyn_core" / "runtime"
if str(RUNTIME_ROOT) not in sys.path:
    sys.path.insert(0, str(RUNTIME_ROOT))

from evelyn_core import fast_control_api as fast_api  # noqa: E402


class UiActionFastApiTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self) -> None:
        self.client = TestClient(TestServer(fast_api.create_app()))
        await self.client.start_server()

    async def asyncTearDown(self) -> None:
        await self.client.close()

    async def test_preview_accepts_only_exact_bounded_contract(self) -> None:
        provider = AsyncMock(
            return_value={
                "ok": True,
                "operation": "preview",
                "error": "",
                "preview": {"schema": "ui_action.preview.v1"},
                "result": {},
            }
        )
        with patch.object(
            fast_api,
            "preview_host_ui_action",
            new=provider,
        ):
            accepted = await self.client.post(
                "/api/control-page/ui-action/preview",
                json={
                    "elementId": "a" * 20,
                    "action": "invoke",
                    "postcondition": "target_absent",
                },
            )
            rejected = await self.client.post(
                "/api/control-page/ui-action/preview",
                json={
                    "elementId": "a" * 20,
                    "action": "invoke",
                    "postcondition": "target_absent",
                    "command": "calc.exe",
                },
            )

        self.assertEqual(accepted.status, 200)
        self.assertEqual(rejected.status, 400)
        provider.assert_awaited_once_with(
            element_id="a" * 20,
            action="invoke",
            postcondition="target_absent",
        )

    async def test_targets_accepts_only_an_empty_contract(self) -> None:
        provider = AsyncMock(
            return_value={
                "ok": True,
                "operation": "discover",
                "error": "",
                "targets": {"schema": "ui_action.targets.v1"},
                "preview": {},
                "result": {},
            }
        )
        with patch.object(
            fast_api,
            "discover_host_ui_action",
            new=provider,
        ):
            accepted = await self.client.post(
                "/api/control-page/ui-action/targets",
                json={},
            )
            rejected = await self.client.post(
                "/api/control-page/ui-action/targets",
                json={"command": "calc.exe"},
            )

        self.assertEqual(accepted.status, 200)
        self.assertEqual(rejected.status, 400)
        provider.assert_awaited_once_with()

    async def test_apply_requires_explicit_confirmation_marker(self) -> None:
        provider = AsyncMock(
            return_value={
                "ok": True,
                "operation": "apply",
                "error": "",
                "preview": {},
                "result": {
                    "schema": "ui_action.result.v1",
                    "state": "verified",
                },
            }
        )
        with patch.object(
            fast_api,
            "apply_host_ui_action",
            new=provider,
        ):
            missing = await self.client.post(
                "/api/control-page/ui-action/apply",
                json={"confirmToken": "t" * 43},
            )
            accepted = await self.client.post(
                "/api/control-page/ui-action/apply",
                json={
                    "confirmToken": "t" * 43,
                    "userConfirmed": True,
                },
            )

        self.assertEqual(missing.status, 400)
        self.assertEqual(
            (await missing.json())["error"],
            "ui_action_explicit_confirmation_required",
        )
        self.assertEqual(accepted.status, 200)
        provider.assert_awaited_once_with(confirm_token="t" * 43)

    async def test_status_exposes_policy_without_target_text(self) -> None:
        fast_api.LOCAL_BRIDGE_STATUS.clear()
        fast_api.LOCAL_BRIDGE_STATUS.update(
            {
                "ready": True,
                "hostUiAction": {
                    "schema": "host_ui_action.status.v1",
                    "state": "running",
                    "auditReady": True,
                    "processedCount": 1,
                },
            }
        )

        response = await self.client.get("/api/control-page/ui-action")
        payload = await response.json()

        self.assertTrue(payload["ok"])
        self.assertFalse(payload["policy"]["arbitraryCoordinates"])
        self.assertNotIn("target", payload["status"])


if __name__ == "__main__":
    unittest.main()
