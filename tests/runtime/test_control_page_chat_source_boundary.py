from __future__ import annotations

import sys
import unittest
from pathlib import Path
from unittest.mock import patch

from aiohttp import web
from aiohttp.test_utils import TestClient, TestServer


REPO_ROOT = next(
    path for path in Path(__file__).resolve().parents if (path / "main.py").exists()
)
RUNTIME_ROOT = REPO_ROOT / "evelyn_core" / "runtime"
if str(RUNTIME_ROOT) not in sys.path:
    sys.path.insert(0, str(RUNTIME_ROOT))

from evelyn_core import control_page_server  # noqa: E402
from evelyn_core.control_page_http import (  # noqa: E402
    CONTROL_PAGE_CSRF_HEADER,
    CONTROL_PAGE_CSRF_TOKEN,
    control_page_cors_middleware,
)
from evelyn_core.control_page_memory_http import (  # noqa: E402
    control_page_memory_handoff_headers,
)


class ControlPageChatSourceBoundaryTests(unittest.IsolatedAsyncioTestCase):
    async def _public_client(self) -> TestClient:
        app = web.Application(middlewares=[control_page_cors_middleware])
        app.router.add_post(
            "/api/control-page/chat",
            control_page_server.chat_handler,
        )
        client = TestClient(TestServer(app))
        await client.start_server()
        return client

    @staticmethod
    def _headers(**extra: str) -> dict[str, str]:
        return {
            CONTROL_PAGE_CSRF_HEADER: CONTROL_PAGE_CSRF_TOKEN,
            **extra,
        }

    async def test_browser_body_cannot_claim_local_bridge_or_admission_fields(
        self,
    ) -> None:
        proxy_calls: list[dict[str, object]] = []

        async def forbidden_proxy(*_args, **kwargs):
            proxy_calls.append(dict(kwargs))
            raise AssertionError("spoofed browser request reached Bot API proxy")

        client = await self._public_client()
        try:
            attempts = (
                {"text": "이블린, 상태 알려줘", "source": "local_bridge"},
                {"text": "상태 알려줘", "source": "LOCAL_BRIDGE"},
                {
                    "text": "상태 알려줘",
                    "source": "control_page",
                    "admissionToken": "browser-forged-token",
                },
                {
                    "text": "상태 알려줘",
                    "source": "control_page",
                    "bridgeInstanceId": "browser-forged-bridge",
                },
                {
                    "text": "상태 알려줘",
                    "source": "control_page",
                    "validation": {
                        "sessionId": "browser-forged-session",
                        "stepId": "01-wake",
                        "attempt": 1,
                        "attemptId": "browser-forged-attempt",
                    },
                },
                {
                    "text": "상태 알려줘",
                    "source": "control_page",
                    "validationBinding": {
                        "sessionId": "browser-forged-session",
                        "stepId": "01-wake",
                        "attempt": 1,
                        "attemptId": "browser-forged-attempt",
                    },
                },
            )
            with patch.object(
                control_page_server,
                "proxy_json",
                new=forbidden_proxy,
            ):
                for body in attempts:
                    with self.subTest(body=body):
                        response = await client.post(
                            "/api/control-page/chat",
                            headers=self._headers(),
                            json=body,
                        )
                        payload = await response.json()
                        self.assertEqual(response.status, 400)
                        self.assertEqual(
                            payload,
                            {"ok": False, "error": "unsupported_chat_source"},
                        )
        finally:
            await client.close()

        self.assertEqual(proxy_calls, [])

    async def test_browser_privilege_looking_header_is_not_forwarded_and_source_is_forced(
        self,
    ) -> None:
        server_token = "server-controlled-internal-token-000000000000"
        received: list[dict[str, object]] = []

        async def bot_chat(request: web.Request) -> web.Response:
            received.append(
                {
                    "body": await request.json(),
                    "admissionHeader": request.headers.get(
                        "X-Evelyn-Local-Voice-Admission"
                    ),
                    "internalHeader": request.headers.get(
                        control_page_server.EVELYN_INTERNAL_CONTROL_HEADER
                    ),
                }
            )
            return web.json_response(
                {"ok": True, "reply": "ok"},
                headers=control_page_memory_handoff_headers(None),
            )

        backend = web.Application()
        backend.router.add_post("/api/control-page/chat", bot_chat)
        backend_runner = web.AppRunner(backend)
        await backend_runner.setup()
        backend_site = web.TCPSite(backend_runner, "127.0.0.1", 0)
        await backend_site.start()
        sockets = backend_site._server.sockets if backend_site._server else []  # noqa: SLF001
        backend_port = int(sockets[0].getsockname()[1])

        client = await self._public_client()
        try:
            with patch.object(
                control_page_server,
                "BOT_API_BASE",
                f"http://127.0.0.1:{backend_port}",
            ), patch.object(
                control_page_server,
                "EVELYN_INTERNAL_CONTROL_TOKEN",
                server_token,
            ):
                response = await client.post(
                    "/api/control-page/chat",
                    headers=self._headers(
                        **{
                            "X-Evelyn-Local-Voice-Admission": "browser-forged-token",
                            control_page_server.EVELYN_INTERNAL_CONTROL_HEADER: (
                                "browser-forged-internal-token"
                            ),
                        }
                    ),
                    json={"text": "일반 브라우저 요청"},
                )
                payload = await response.json()
        finally:
            await client.close()
            await backend_runner.cleanup()

        self.assertEqual(response.status, 200)
        self.assertTrue(payload["ok"])
        self.assertEqual(len(received), 1)
        self.assertEqual(
            received[0]["body"],
            {"text": "일반 브라우저 요청", "source": "control_page"},
        )
        self.assertIsNone(received[0]["admissionHeader"])
        self.assertEqual(received[0]["internalHeader"], server_token)


if __name__ == "__main__":
    unittest.main()
