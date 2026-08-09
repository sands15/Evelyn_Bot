from __future__ import annotations

import sys
import unittest
from pathlib import Path

from aiohttp import web
from aiohttp.test_utils import TestClient, TestServer


REPO_ROOT = next(path for path in Path(__file__).resolve().parents if (path / "main.py").exists())
RUNTIME_ROOT = REPO_ROOT / "evelyn_core" / "runtime"
if str(RUNTIME_ROOT) not in sys.path:
    sys.path.insert(0, str(RUNTIME_ROOT))

from evelyn_core.control_page_http import (  # noqa: E402
    CONTROL_PAGE_CSRF_HEADER,
    control_page_cors_middleware,
    control_page_session_handler,
    reject_browser_origin_middleware,
)
from evelyn_core.control_page_server import create_app as create_public_control_app  # noqa: E402
from evelyn_core.fast_control_api import create_app as create_internal_control_app  # noqa: E402


class ControlPageSecurityIntegrationTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self) -> None:
        self.mutations = 0

        async def state_handler(_: web.Request) -> web.Response:
            return web.json_response({"ok": True})

        async def mutation_handler(_: web.Request) -> web.Response:
            self.mutations += 1
            return web.json_response({"ok": True, "mutations": self.mutations})

        app = web.Application(middlewares=[control_page_cors_middleware])
        app.router.add_get("/api/control-page/state", state_handler)
        app.router.add_get("/api/control-page/session", control_page_session_handler)
        app.router.add_post("/api/control-page/mutate", mutation_handler)
        self.client = TestClient(TestServer(app))
        await self.client.start_server()
        self.origin = str(self.client.make_url("/")).rstrip("/")

    async def asyncTearDown(self) -> None:
        await self.client.close()

    async def test_trusted_origin_can_read_and_mutate_with_session_token(self) -> None:
        state_response = await self.client.get(
            "/api/control-page/state",
            headers={"Origin": self.origin},
        )
        self.assertEqual(state_response.status, 200)
        self.assertEqual(state_response.headers["Access-Control-Allow-Origin"], self.origin)

        session_response = await self.client.get(
            "/api/control-page/session",
            headers={"Origin": self.origin},
        )
        token = (await session_response.json())["csrfToken"]
        mutation_response = await self.client.post(
            "/api/control-page/mutate",
            headers={"Origin": self.origin, CONTROL_PAGE_CSRF_HEADER: token},
            json={"action": "test"},
        )

        self.assertEqual(mutation_response.status, 200)
        self.assertEqual(self.mutations, 1)

    async def test_untrusted_origin_and_missing_token_never_reach_handler(self) -> None:
        untrusted = await self.client.post(
            "/api/control-page/mutate",
            headers={"Origin": "https://evil.example"},
            json={"action": "test"},
        )
        missing_token = await self.client.post(
            "/api/control-page/mutate",
            headers={"Origin": self.origin},
            json={"action": "test"},
        )

        self.assertEqual(untrusted.status, 403)
        self.assertEqual((await untrusted.json())["error"], "origin_not_allowed")
        self.assertEqual(missing_token.status, 403)
        self.assertEqual((await missing_token.json())["error"], "csrf_token_required")
        self.assertEqual(self.mutations, 0)

    async def test_dns_rebinding_host_header_is_rejected(self) -> None:
        response = await self.client.get(
            "/api/control-page/state",
            headers={"Host": "evil.example:8799", "Origin": "http://evil.example:8799"},
        )

        self.assertEqual(response.status, 403)
        self.assertEqual((await response.json())["error"], "host_not_allowed")


class InternalControlApiSecurityIntegrationTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self) -> None:
        async def mutation_handler(_: web.Request) -> web.Response:
            return web.json_response({"ok": True})

        app = web.Application(middlewares=[reject_browser_origin_middleware])
        app.router.add_post("/api/control-page/mutate", mutation_handler)
        self.client = TestClient(TestServer(app))
        await self.client.start_server()

    async def asyncTearDown(self) -> None:
        await self.client.close()

    async def test_server_json_is_allowed_but_browser_and_form_posts_are_rejected(self) -> None:
        server_call = await self.client.post("/api/control-page/mutate", json={"action": "test"})
        browser_call = await self.client.post(
            "/api/control-page/mutate",
            headers={"Origin": "http://127.0.0.1:8799"},
            json={"action": "test"},
        )
        form_call = await self.client.post("/api/control-page/mutate", data={"action": "test"})

        self.assertEqual(server_call.status, 200)
        self.assertEqual(browser_call.status, 403)
        self.assertEqual(form_call.status, 415)


class RealControlAppSecurityWiringTests(unittest.IsolatedAsyncioTestCase):
    async def test_public_app_wires_session_and_blocks_unprotected_shutdown(self) -> None:
        client = TestClient(TestServer(create_public_control_app()))
        await client.start_server()
        try:
            origin = str(client.make_url("/")).rstrip("/")
            session_response = await client.get(
                "/api/control-page/session",
                headers={"Origin": origin},
            )
            shutdown_response = await client.post(
                "/api/control-page/shutdown",
                headers={"Origin": origin},
                json={},
            )
            discord_mode_response = await client.post(
                "/api/control-page/discord-mode/preview",
                headers={"Origin": origin},
                json={"enabled": False},
            )

            self.assertEqual(session_response.status, 200)
            self.assertTrue((await session_response.json())["csrfToken"])
            self.assertEqual(shutdown_response.status, 403)
            self.assertEqual((await shutdown_response.json())["error"], "csrf_token_required")
            self.assertEqual(discord_mode_response.status, 403)
            self.assertEqual((await discord_mode_response.json())["error"], "csrf_token_required")
        finally:
            await client.close()

    async def test_internal_app_wires_browser_origin_rejection(self) -> None:
        client = TestClient(TestServer(create_internal_control_app()))
        await client.start_server()
        try:
            response = await client.post(
                "/api/control-page/shutdown",
                headers={"Origin": "http://127.0.0.1:8799"},
                json={},
            )

            self.assertEqual(response.status, 403)
            self.assertEqual((await response.json())["error"], "browser_origin_not_allowed")
        finally:
            await client.close()


if __name__ == "__main__":
    unittest.main()
