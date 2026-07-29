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
from evelyn_core import codex_gateway_server as gateway  # noqa: E402
from evelyn_core.codex_gateway_server import build_app  # noqa: E402
from evelyn_core.runtime_config_schema import (  # noqa: E402
    CODEX_GATEWAY_SETTINGS,
    load_runtime_settings,
)
from evelyn_core.runtime_error_observability import RuntimeErrorCounter  # noqa: E402


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

    def test_public_status_strips_paths_and_backend_output(self) -> None:
        public = gateway._public_last_request(
            {
                "phase": "error",
                "cwd": "C:\\private\\workspace",
                "stderr_preview": "secret-token",
                "stdout_preview": "private output",
                "prompt_chars": 10,
            }
        )

        self.assertEqual(
            public,
            {
                "phase": "error",
                "prompt_chars": 10,
            },
        )

    def test_request_workdir_cannot_escape_configured_root(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            allowed = Path(temp_dir) / "allowed"
            outside = Path(temp_dir) / "outside"
            allowed.mkdir()
            outside.mkdir()
            with patch.object(gateway, "DEFAULT_WORKDIR", str(allowed)):
                self.assertEqual(
                    gateway._resolve_request_workdir(allowed),
                    str(allowed.resolve()),
                )
                with self.assertRaisesRegex(
                    ValueError,
                    "codex_workdir_outside_allowed_root",
                ):
                    gateway._resolve_request_workdir(outside)

    def test_custom_shell_backend_is_disabled_by_default(self) -> None:
        settings = load_runtime_settings(
            "codex_gateway",
            CODEX_GATEWAY_SETTINGS,
            environ={
                "VOYAGER_CODEX_GATEWAY_COMMAND": "private-command",
                "EVELYN_ALLOW_CUSTOM_GATEWAY_COMMAND": "false",
            },
        )
        with patch.object(gateway, "_GATEWAY_CONFIG", settings):
            self.assertEqual(gateway._backend_command(), "")

    def test_request_metadata_is_bounded_and_timeout_is_finite(self) -> None:
        self.assertEqual(
            gateway._safe_request_label(
                "user email@example.com private",
                fallback="voyager-action",
            ),
            "user_email_example.com_private",
        )
        self.assertEqual(
            gateway._safe_request_label(
                "organization/model",
                fallback="default",
                allow_slash=True,
            ),
            "organization/model",
        )
        self.assertEqual(gateway._request_timeout("30"), 30.0)
        for invalid in ("nan", "inf", "0", "1801", "invalid"):
            with self.subTest(invalid=invalid):
                with self.assertRaisesRegex(ValueError, "codex_timeout_invalid"):
                    gateway._request_timeout(invalid)


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

    async def test_backend_failure_returns_fixed_public_error(self) -> None:
        self.submit.side_effect = RuntimeError("C:\\private\\token")
        counter = RuntimeErrorCounter(now=lambda: 1000.0)
        with patch.object(gateway, "_RUNTIME_ERRORS", counter):
            response = await self.client.post(
                "/codex/action",
                headers={"Authorization": "Bearer integration-secret"},
                json={"prompt": "hello"},
            )
            payload = await response.json()

        self.assertEqual(response.status, 500)
        self.assertEqual(payload["error"], "codex_backend_failed")
        self.assertNotIn("private", str(payload))
        self.assertEqual(counter.snapshot()["errorCount"], 1)

    async def test_invalid_timeout_is_rejected_without_backend_execution(self) -> None:
        response = await self.client.post(
            "/codex/action",
            headers={"Authorization": "Bearer integration-secret"},
            json={"prompt": "hello", "timeout_sec": "nan"},
        )
        payload = await response.json()

        self.assertEqual(response.status, 400)
        self.assertEqual(payload["error"], "codex_timeout_invalid")
        self.submit.assert_not_awaited()


if __name__ == "__main__":
    unittest.main()
