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

from evelyn_core.codex_gateway_auth import (  # noqa: E402
    gateway_auth_headers,
    gateway_request_authorized,
    resolve_gateway_token,
)
from evelyn_core.codex_gateway_server import build_app  # noqa: E402


class CodexGatewayAuthUnitTests(unittest.TestCase):
    def test_token_file_is_created_once_and_reused(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            token_path = Path(temp_dir) / "gateway.token"
            first = resolve_gateway_token(create=True, token_path=token_path)
            second = resolve_gateway_token(create=True, token_path=token_path)

            self.assertGreaterEqual(len(first), 48)
            self.assertEqual(first, second)
            self.assertEqual(token_path.read_text(encoding="utf-8").strip(), first)
            self.assertEqual(gateway_auth_headers(token_path=token_path), {"Authorization": f"Bearer {first}"})

    def test_authorization_requires_exact_bearer_token(self) -> None:
        self.assertTrue(gateway_request_authorized("Bearer exact-token", "exact-token"))
        self.assertFalse(gateway_request_authorized(None, "exact-token"))
        self.assertFalse(gateway_request_authorized("Bearer wrong-token", "exact-token"))
        self.assertFalse(gateway_request_authorized("Basic exact-token", "exact-token"))


class CodexGatewayAuthIntegrationTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self) -> None:
        self.submit = AsyncMock(return_value="OK")
        self.submit_patch = patch("evelyn_core.codex_gateway_server._submit_backend", self.submit)
        self.submit_patch.start()
        self.client = TestClient(TestServer(build_app(action_token="integration-secret")))
        await self.client.start_server()

    async def asyncTearDown(self) -> None:
        await self.client.close()
        self.submit_patch.stop()

    async def test_health_stays_read_only_and_reports_action_auth(self) -> None:
        response = await self.client.get("/health")
        payload = await response.json()

        self.assertEqual(response.status, 200)
        self.assertTrue(payload["actionAuthRequired"])
        self.assertEqual(payload["actionAuthScheme"], "Bearer")

    async def test_action_rejects_missing_and_wrong_tokens_without_execution(self) -> None:
        missing = await self.client.post("/codex/action", json={"prompt": "hello"})
        wrong = await self.client.post(
            "/codex/action",
            headers={"Authorization": "Bearer wrong"},
            json={"prompt": "hello"},
        )

        self.assertEqual(missing.status, 401)
        self.assertEqual(wrong.status, 401)
        self.submit.assert_not_awaited()

    async def test_action_accepts_shared_token(self) -> None:
        response = await self.client.post(
            "/codex/action",
            headers={"Authorization": "Bearer integration-secret"},
            json={"prompt": "hello", "source": "integration"},
        )
        payload = await response.json()

        self.assertEqual(response.status, 200)
        self.assertTrue(payload["ok"])
        self.assertEqual(payload["content"], "OK")
        self.submit.assert_awaited_once()


if __name__ == "__main__":
    unittest.main()
