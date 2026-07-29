from __future__ import annotations

import sys
import unittest
from pathlib import Path
from unittest.mock import patch

from aiohttp.test_utils import TestClient, TestServer


REPO_ROOT = next(path for path in Path(__file__).resolve().parents if (path / "main.py").exists())
RUNTIME_ROOT = REPO_ROOT / "evelyn_core" / "runtime"
if str(RUNTIME_ROOT) not in sys.path:
    sys.path.insert(0, str(RUNTIME_ROOT))

from evelyn_core import control_page_server  # noqa: E402


class StorageRetentionApiTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self) -> None:
        self.payload = {
            "ok": True,
            "available": True,
            "stale": False,
            "state": "clear",
            "report": {
                "schema": "storage_retention.report.v1",
                "state": "clear",
                "generatedAt": 1000.0,
                "nextScanAt": 2000.0,
                "dryRun": True,
                "automaticDeletion": False,
                "summary": {
                    "scopeCount": 3,
                    "errorCount": 0,
                    "candidateCount": 0,
                    "candidateBytes": 0,
                },
                "scopes": {},
            },
            "policy": {
                "dryRunOnly": True,
                "automaticDeletion": False,
                "applyApiAvailable": False,
            },
        }
        self.report_patch = patch.object(
            control_page_server,
            "read_storage_retention_report",
            return_value=self.payload,
        )
        self.report_patch.start()
        self.client = TestClient(TestServer(control_page_server.create_app()))
        await self.client.start_server()
        self.origin = str(self.client.make_url("/")).rstrip("/")

    async def asyncTearDown(self) -> None:
        await self.client.close()
        self.report_patch.stop()

    async def test_get_exposes_read_only_dry_run_contract(self) -> None:
        response = await self.client.get(
            "/api/control-page/storage-retention",
            headers={"Origin": self.origin},
        )
        payload = await response.json()

        self.assertEqual(response.status, 200)
        self.assertEqual(payload["report"]["schema"], "storage_retention.report.v1")
        self.assertTrue(payload["policy"]["dryRunOnly"])
        self.assertFalse(payload["policy"]["automaticDeletion"])
        self.assertFalse(payload["policy"]["applyApiAvailable"])

    async def test_no_apply_route_is_registered(self) -> None:
        response = await self.client.get(
            "/api/control-page/storage-retention/apply",
            headers={"Origin": self.origin},
        )
        self.assertEqual(response.status, 404)


if __name__ == "__main__":
    unittest.main()
