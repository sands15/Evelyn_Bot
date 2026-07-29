from __future__ import annotations

import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import AsyncMock, patch

from aiohttp.test_utils import TestClient, TestServer


REPO_ROOT = next(path for path in Path(__file__).resolve().parents if (path / "main.py").exists())
RUNTIME_ROOT = REPO_ROOT / "evelyn_core" / "runtime"
if str(RUNTIME_ROOT) not in sys.path:
    sys.path.insert(0, str(RUNTIME_ROOT))

from evelyn_core import control_page_server  # noqa: E402
from evelyn_core.control_page_http import CONTROL_PAGE_CSRF_HEADER  # noqa: E402
from evelyn_core.voice_validation import VoiceValidationManager  # noqa: E402


READY_CAPABILITIES = {
    "voiceLocal": {"state": "ready", "ready": True, "blockers": []},
    "voiceDiscord": {"state": "ready", "ready": True, "blockers": []},
}


class VoiceValidationApiTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.manager = VoiceValidationManager(root=Path(self.temp_dir.name))
        self.manager_patch = patch.object(
            control_page_server,
            "get_voice_validation_manager",
            return_value=self.manager,
        )
        self.health_patch = patch.object(
            control_page_server,
            "cached_runtime_health",
            new=AsyncMock(return_value={"capabilities": READY_CAPABILITIES}),
        )
        self.manager_patch.start()
        self.health_patch.start()
        self.client = TestClient(TestServer(control_page_server.create_app()))
        await self.client.start_server()
        self.origin = str(self.client.make_url("/")).rstrip("/")
        session_response = await self.client.get(
            "/api/control-page/session",
            headers={"Origin": self.origin},
        )
        self.csrf = (await session_response.json())["csrfToken"]

    async def asyncTearDown(self):
        await self.client.close()
        self.health_patch.stop()
        self.manager_patch.stop()
        self.temp_dir.cleanup()

    def headers(self):
        return {
            "Origin": self.origin,
            CONTROL_PAGE_CSRF_HEADER: self.csrf,
        }

    async def test_start_get_and_abort_follow_public_session_contract(self):
        started_response = await self.client.post(
            "/api/control-page/voice-validation/start",
            headers=self.headers(),
            json={"suite": "voice-p0.v1", "surfaces": ["local", "discord"]},
        )
        started = await started_response.json()
        self.assertEqual(started_response.status, 201)
        self.assertEqual(started["session"]["schema"], "voice_validation.session.v1")
        self.assertEqual(started["session"]["state"], "running")

        state_response = await self.client.get(
            "/api/control-page/voice-validation",
            headers={"Origin": self.origin},
        )
        state = await state_response.json()
        self.assertEqual(state["session"]["sessionId"], started["session"]["sessionId"])

        aborted_response = await self.client.post(
            "/api/control-page/voice-validation/abort",
            headers=self.headers(),
            json={"sessionId": started["session"]["sessionId"]},
        )
        aborted = await aborted_response.json()
        self.assertEqual(aborted_response.status, 200)
        self.assertEqual(aborted["session"]["state"], "aborted")

    async def test_every_mutating_route_requires_csrf(self):
        for suffix in ("start", "confirm", "retry", "abort"):
            with self.subTest(route=suffix):
                response = await self.client.post(
                    f"/api/control-page/voice-validation/{suffix}",
                    headers={"Origin": self.origin},
                    json={},
                )
                self.assertEqual(response.status, 403)
                self.assertEqual((await response.json())["error"], "csrf_token_required")

    async def test_preflight_options_is_non_mutating(self):
        response = await self.client.options(
            "/api/control-page/voice-validation/start",
            headers={"Origin": self.origin},
        )
        self.assertEqual(response.status, 204)
        self.assertIn("POST", response.headers["Access-Control-Allow-Methods"])


if __name__ == "__main__":
    unittest.main()
