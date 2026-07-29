from __future__ import annotations

import sys
import unittest
from pathlib import Path
from unittest.mock import AsyncMock, patch

from aiohttp.test_utils import TestClient, TestServer


REPO_ROOT = next(path for path in Path(__file__).resolve().parents if (path / "main.py").exists())
RUNTIME_ROOT = REPO_ROOT / "evelyn_core" / "runtime"
if str(RUNTIME_ROOT) not in sys.path:
    sys.path.insert(0, str(RUNTIME_ROOT))

from evelyn_core import control_page_server  # noqa: E402


class RuntimeErrorsApiTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self) -> None:
        self.summary = {
            "schema": "runtime_errors.summary.v1",
            "state": "clear",
            "generatedAt": 1000.0,
            "recentAfterSec": 3600.0,
            "summary": {
                "sourceCount": 3,
                "availableCount": 3,
                "staleCount": 0,
                "currentErrorCount": 0,
                "recentErrorCount": 0,
                "totalCount": 0,
            },
            "sources": {},
            "recentErrors": [],
            "warnings": [],
            "privacy": {
                "exceptionMessages": False,
                "stackTraces": False,
                "filesystemPaths": False,
            },
        }
        self.summary_patch = patch.object(
            control_page_server,
            "cached_runtime_health",
            new_callable=AsyncMock,
            return_value={
                "observability": {
                    "exceptions": self.summary,
                }
            },
        )
        self.summary_patch.start()
        self.client = TestClient(TestServer(control_page_server.create_app()))
        await self.client.start_server()
        self.origin = str(self.client.make_url("/")).rstrip("/")

    async def asyncTearDown(self) -> None:
        await self.client.close()
        self.summary_patch.stop()

    async def test_get_exposes_read_only_privacy_contract(self) -> None:
        response = await self.client.get(
            "/api/control-page/runtime-errors",
            headers={"Origin": self.origin},
        )
        payload = await response.json()

        self.assertEqual(response.status, 200)
        self.assertEqual(payload["errors"]["schema"], "runtime_errors.summary.v1")
        self.assertFalse(payload["errors"]["privacy"]["exceptionMessages"])
        self.assertFalse(payload["errors"]["privacy"]["stackTraces"])
        self.assertFalse(payload["errors"]["privacy"]["filesystemPaths"])

    async def test_no_mutating_route_is_registered(self) -> None:
        response = await self.client.get(
            "/api/control-page/runtime-errors/apply",
            headers={"Origin": self.origin},
        )
        self.assertEqual(response.status, 404)


if __name__ == "__main__":
    unittest.main()
