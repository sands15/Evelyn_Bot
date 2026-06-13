from __future__ import annotations

import asyncio
import json
import socket
import sys
import unittest
from pathlib import Path
from unittest.mock import AsyncMock, patch

from aiohttp import web


REPO_ROOT = next(path for path in Path(__file__).resolve().parents if (path / "main.py").exists())
RUNTIME_ROOT = REPO_ROOT / "evelyn_core" / "runtime"
if str(RUNTIME_ROOT) not in sys.path:
    sys.path.insert(0, str(RUNTIME_ROOT))

from evelyn_core import control_page_server  # noqa: E402


def unused_tcp_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.bind(("127.0.0.1", 0))
        return int(sock.getsockname()[1])


class _Request(dict):
    query_string = ""


class ControlPageProxyIntegrationTests(unittest.IsolatedAsyncioTestCase):
    async def asyncTearDown(self) -> None:
        control_page_server.runtime_health_cache = None
        control_page_server.runtime_health_cache_at = 0.0
        control_page_server.bot_state_last_success_at = 0.0

    async def start_bot_api(self, handler) -> tuple[web.AppRunner, int]:
        app = web.Application()
        app.router.add_get("/api/control-page/state", handler)
        runner = web.AppRunner(app)
        await runner.setup()
        site = web.TCPSite(runner, "127.0.0.1", 0)
        await site.start()
        sockets = site._server.sockets if site._server else []  # noqa: SLF001
        port = int(sockets[0].getsockname()[1])
        return runner, port

    def service_health(self, *, bot_ready: bool) -> dict[str, object]:
        return {
            "manifestVersion": "test",
            "overallState": "up" if bot_ready else "degraded",
            "legacyServices": {
                "botReady": bot_ready,
                "mainReady": True,
                "routerReady": True,
                "subReady": True,
                "ttsReady": True,
                "visionReady": True,
                "summary": "test service health",
            },
        }

    async def test_state_handler_matrix_bot_api_port_closed(self) -> None:
        port = unused_tcp_port()

        with patch.object(control_page_server, "BOT_API_PORT", port):
            with patch.object(control_page_server, "BOT_API_BASE", f"http://127.0.0.1:{port}"):
                with patch.object(
                    control_page_server,
                    "cached_runtime_health",
                    new=AsyncMock(return_value=self.service_health(bot_ready=False)),
                ):
                    response = await control_page_server.state_handler(_Request())

        payload = json.loads(response.text or "{}")
        self.assertFalse(payload["ok"])
        self.assertEqual(payload["runtime"]["controlPlane"]["botApi"]["port"], port)
        self.assertEqual(payload["runtime"]["controlPlane"]["botApi"]["state"], "down")
        self.assertEqual(payload["runtime"]["controlPlane"]["lastProxyFailure"]["kind"], "port_closed")

    async def test_state_handler_matrix_bot_api_port_open_but_proxy_timeout(self) -> None:
        async def slow_state(_: web.Request) -> web.Response:
            await asyncio.sleep(0.2)
            return web.json_response({"ok": True})

        runner, port = await self.start_bot_api(slow_state)
        try:
            with patch.object(control_page_server, "BOT_API_PORT", port):
                with patch.object(control_page_server, "BOT_API_BASE", f"http://127.0.0.1:{port}"):
                    with patch.object(control_page_server, "PROXY_TIMEOUT_SEC", 0.03):
                        with patch.object(
                            control_page_server,
                            "cached_runtime_health",
                            new=AsyncMock(return_value=self.service_health(bot_ready=False)),
                        ):
                            response = await control_page_server.state_handler(_Request())
        finally:
            await runner.cleanup()

        payload = json.loads(response.text or "{}")
        self.assertFalse(payload["ok"])
        self.assertEqual(payload["runtime"]["controlPlane"]["botApi"]["port"], port)
        self.assertEqual(payload["runtime"]["controlPlane"]["botApi"]["state"], "proxy_failed")
        self.assertEqual(payload["runtime"]["controlPlane"]["lastProxyFailure"]["kind"], "http_timeout")

    async def test_state_handler_matrix_full_ok_ready(self) -> None:
        async def ready_state(_: web.Request) -> web.Response:
            return web.json_response(
                {
                    "ok": True,
                    "runtime": {"services": {"botReady": False, "mainReady": False}},
                }
            )

        runner, port = await self.start_bot_api(ready_state)
        try:
            with patch.object(control_page_server, "BOT_API_PORT", port):
                with patch.object(control_page_server, "BOT_API_BASE", f"http://127.0.0.1:{port}"):
                    with patch.object(
                        control_page_server,
                        "MODEL_PORTS",
                        {"main": port, "router": port, "sub": port, "tts": port, "voyager": port, "codex": port},
                    ):
                        with patch.object(
                            control_page_server,
                            "cached_runtime_health",
                            new=AsyncMock(return_value=self.service_health(bot_ready=True)),
                        ):
                            response = await control_page_server.state_handler(_Request())
        finally:
            await runner.cleanup()

        payload = json.loads(response.text or "{}")
        self.assertTrue(payload["ok"])
        self.assertTrue(payload["runtime"]["services"]["botReady"])
        self.assertTrue(payload["runtime"]["services"]["mainReady"])
        self.assertEqual(payload["runtime"]["controlPlane"]["botApi"]["port"], port)
        self.assertEqual(payload["runtime"]["controlPlane"]["botApi"]["state"], "ready")
        self.assertGreater(payload["runtime"]["controlPlane"]["botApi"]["lastSuccessfulStateAt"], 0)
        self.assertIn("both responding", payload["statusText"])


if __name__ == "__main__":
    unittest.main()
