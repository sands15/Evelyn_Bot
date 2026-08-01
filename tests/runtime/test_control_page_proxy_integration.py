from __future__ import annotations

import asyncio
import json
import os
import socket
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import AsyncMock, patch

from aiohttp import web
from aiohttp.test_utils import TestClient, TestServer


REPO_ROOT = next(path for path in Path(__file__).resolve().parents if (path / "main.py").exists())
RUNTIME_ROOT = REPO_ROOT / "evelyn_core" / "runtime"
if str(RUNTIME_ROOT) not in sys.path:
    sys.path.insert(0, str(RUNTIME_ROOT))

from evelyn_core import control_page_server  # noqa: E402
from evelyn_core.control_page_http import (  # noqa: E402
    CONTROL_PAGE_CSRF_HEADER,
    CONTROL_PAGE_CSRF_TOKEN,
    control_page_json_response,
    control_page_cors_middleware,
    reject_browser_origin_middleware,
)
from evelyn_core.control_page_state import (  # noqa: E402
    handle_control_page_chat_request,
)
from evelyn_core import memory_deletion_journal as deletion_journal  # noqa: E402
from evelyn_core import memory_deletion_outbound as deletion_outbound  # noqa: E402
from evelyn_core.memory_integrity_authenticity import (  # noqa: E402
    MEMORY_INTEGRITY_ANCHOR_DIR_ENV,
    MEMORY_INTEGRITY_BOOTSTRAP_ENV,
    MEMORY_INTEGRITY_KEY_FILE_ENV,
)


def unused_tcp_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.bind(("127.0.0.1", 0))
        return int(sock.getsockname()[1])


class _Request(dict):
    query_string = ""


class ControlPageProxyIntegrationTests(unittest.IsolatedAsyncioTestCase):
    async def asyncTearDown(self) -> None:
        control_page_server.CONTROL_PAGE_RUNTIME_HEALTH_CACHE.clear()
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

    async def test_action_events_handler_proxies_followup_events(self) -> None:
        async def action_events(_: web.Request) -> web.Response:
            return web.json_response(
                {
                    "ok": True,
                    "lastEventId": 2,
                    "activeCount": 0,
                    "events": [{"id": 2, "type": "completed", "taskId": "fast-action-1"}],
                    "tasks": [{"id": "fast-action-1", "status": "completed"}],
                }
            )

        app = web.Application()
        app.router.add_get("/api/control-page/action-events", action_events)
        runner = web.AppRunner(app)
        await runner.setup()
        site = web.TCPSite(runner, "127.0.0.1", 0)
        await site.start()
        sockets = site._server.sockets if site._server else []  # noqa: SLF001
        port = int(sockets[0].getsockname()[1])
        try:
            with patch.object(control_page_server, "BOT_API_BASE", f"http://127.0.0.1:{port}"):
                response = await control_page_server.action_events_handler(_Request())
        finally:
            await runner.cleanup()

        payload = json.loads(response.text or "{}")
        self.assertTrue(payload["ok"])
        self.assertEqual(payload["events"][0]["type"], "completed")
        self.assertEqual(payload["tasks"][0]["status"], "completed")

    async def test_chat_proxy_preserves_actual_bot_api_integrity_failure_as_503(
        self,
    ) -> None:
        private_canary = "PRIVATE_STALE_MEMORY_MUST_NOT_SURVIVE"
        request_factory_calls: list[str] = []
        chat_log: list[tuple[object, ...]] = []

        with tempfile.TemporaryDirectory() as temp_dir, patch.dict(
            os.environ,
            {
                MEMORY_INTEGRITY_KEY_FILE_ENV: "",
                MEMORY_INTEGRITY_ANCHOR_DIR_ENV: "",
                MEMORY_INTEGRITY_BOOTSTRAP_ENV: "",
            },
        ):
            index_dir = Path(temp_dir) / "memory_index"
            deletion_journal.append_memory_deletion_tombstone(
                index_dir,
                {
                    "schema": deletion_journal.MEMORY_DELETE_TOMBSTONE_V1_SCHEMA,
                    "noteId": "concept-0123456789abcdef",
                    "noteType": "concept",
                    "sourceType": "conversation",
                    "reason": "privacy_request",
                    "deletedAt": "2026-08-01T00:00:00Z",
                },
            )
            stale_position = deletion_journal.memory_deletion_journal_position(
                index_dir
            )
            deletion_journal.append_memory_deletion_tombstone(
                index_dir,
                {
                    "schema": deletion_journal.MEMORY_DELETE_TOMBSTONE_V1_SCHEMA,
                    "noteId": "concept-fedcba9876543210",
                    "noteType": "concept",
                    "sourceType": "conversation",
                    "reason": "privacy_request",
                    "deletedAt": "2026-08-01T00:00:01Z",
                },
            )

            def request_factory(*_args, **_kwargs):
                request_factory_calls.append("called")
                raise AssertionError("stale memory reached the HTTP factory")

            async def handle_input(_guild, _text: str) -> str:
                async with deletion_outbound.memory_deletion_outbound_request(
                    request_factory,
                    "http://llm.invalid/v1/chat/completions",
                    expected_position=stale_position,
                    memory_boundary_required=True,
                    memory_index_dir=index_dir,
                    json={"messages": [{"content": private_canary}]},
                ):
                    raise AssertionError("stale memory request was admitted")

            async def bot_chat(request: web.Request) -> web.Response:
                result, status = await handle_control_page_chat_request(
                    await request.json(),
                    discord_enabled=False,
                    select_guild=lambda _guild_id: None,
                    effective_guild_id=lambda _guild: 0,
                    append_chat_log=lambda *args: chat_log.append(args),
                    handle_input=handle_input,
                    ensure_minecraft_snapshot=AsyncMock(),
                    refresh_runtime_services=AsyncMock(),
                    build_state=AsyncMock(return_value={"ok": True}),
                )
                return control_page_json_response(result, status=status)

            backend = web.Application(
                middlewares=[reject_browser_origin_middleware]
            )
            backend.router.add_post("/api/control-page/chat", bot_chat)
            backend_runner = web.AppRunner(backend)
            await backend_runner.setup()
            backend_site = web.TCPSite(backend_runner, "127.0.0.1", 0)
            await backend_site.start()
            sockets = (
                backend_site._server.sockets  # noqa: SLF001
                if backend_site._server
                else []
            )
            backend_port = int(sockets[0].getsockname()[1])

            public_app = web.Application(
                middlewares=[control_page_cors_middleware]
            )
            public_app.router.add_post(
                "/api/control-page/chat",
                control_page_server.chat_handler,
            )
            client = TestClient(TestServer(public_app))
            await client.start_server()
            try:
                with patch.object(
                    control_page_server,
                    "BOT_API_BASE",
                    f"http://127.0.0.1:{backend_port}",
                ):
                    response = await client.post(
                        "/api/control-page/chat",
                        headers={
                            CONTROL_PAGE_CSRF_HEADER:
                            CONTROL_PAGE_CSRF_TOKEN,
                        },
                        json={
                            "text": "프록시 경계 테스트",
                            "source": "control_page",
                        },
                    )
                    payload = await response.json()
            finally:
                await client.close()
                await backend_runner.cleanup()

        self.assertEqual(response.status, 503)
        self.assertEqual(
            payload,
            {
                "ok": False,
                "error": "memory_deletion_journal_integrity_failed",
            },
        )
        self.assertIn("no-store", response.headers["Cache-Control"])
        self.assertNotIn(private_canary, str(payload))
        self.assertEqual(request_factory_calls, [])
        self.assertEqual(
            [entry[1] for entry in chat_log],
            ["user"],
        )


if __name__ == "__main__":
    unittest.main()
