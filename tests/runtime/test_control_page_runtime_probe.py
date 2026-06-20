from __future__ import annotations

import asyncio
import sys
import unittest
from pathlib import Path
from typing import Any


REPO_ROOT = next(path for path in Path(__file__).resolve().parents if (path / "main.py").exists())
RUNTIME_ROOT = REPO_ROOT / "evelyn_core" / "runtime"
if str(RUNTIME_ROOT) not in sys.path:
    sys.path.insert(0, str(RUNTIME_ROOT))

from evelyn_core.control_page_runtime_probe import (  # noqa: E402
    build_bot_api_state_url,
    probe_control_page_runtime_services,
)


class ControlPageRuntimeProbeTests(unittest.IsolatedAsyncioTestCase):
    def test_build_bot_api_state_url_normalizes_path(self) -> None:
        self.assertEqual(
            build_bot_api_state_url(host="127.0.0.1", port=8798, path="api/state"),
            "http://127.0.0.1:8798/api/state",
        )
        self.assertEqual(
            build_bot_api_state_url(host="127.0.0.1", port=8798, path="/api/state"),
            "http://127.0.0.1:8798/api/state",
        )

    async def test_probe_runtime_services_combines_tcp_bot_api_voyager_and_codex(self) -> None:
        async def tcp_probe(label: str, host: str, port: int) -> tuple[str, bool]:
            _ = host, port
            return label, label != "router"

        async def http_json_get(url: str, timeout_sec: float) -> tuple[int, Any]:
            _ = timeout_sec
            if url.endswith("/api/state"):
                return 200, {"status": "ready"}
            if url.endswith("/health"):
                return 200, {"backend": "codex-gateway", "ok": False, "codex_login_message": "login required"}
            raise AssertionError(f"unexpected url {url}")

        services = await probe_control_page_runtime_services(
            service_urls={
                "main": "http://127.0.0.1:9820/v1/chat/completions",
                "router": "http://127.0.0.1:9822/v1/chat/completions",
                "sub": "http://127.0.0.1:9821/v1/chat/completions",
                "tts": "http://127.0.0.1:8880",
            },
            bot_api_host="127.0.0.1",
            bot_api_port=8798,
            bot_api_state_path="/api/state",
            bot_api_probe_timeout_sec=0.75,
            action_backend="codex-gateway",
            codex_gateway_port=8799,
            voyager_alive_probe=lambda: asyncio.sleep(0, result=True),
            tcp_probe=tcp_probe,
            http_json_get=http_json_get,
        )

        self.assertTrue(services["botReady"])
        self.assertTrue(services["mainReady"])
        self.assertFalse(services["routerReady"])
        self.assertTrue(services["voyagerReady"])
        self.assertTrue(services["codexRequired"])
        self.assertFalse(services["codexReady"])
        self.assertEqual(services["codexError"], "login required")

    async def test_probe_runtime_services_marks_bot_api_down_without_http_probe(self) -> None:
        http_called = False

        async def tcp_probe(label: str, host: str, port: int) -> tuple[str, bool]:
            _ = host, port
            return label, label != "bot_api"

        async def http_json_get(url: str, timeout_sec: float) -> tuple[int, Any]:
            nonlocal http_called
            http_called = True
            return 200, {}

        services = await probe_control_page_runtime_services(
            service_urls={"main": "http://127.0.0.1:9820"},
            bot_api_host="127.0.0.1",
            bot_api_port=8798,
            bot_api_state_path="/api/state",
            bot_api_probe_timeout_sec=0.75,
            action_backend="direct",
            codex_gateway_port=8799,
            voyager_alive_probe=lambda: asyncio.sleep(0, result=False),
            tcp_probe=tcp_probe,
            http_json_get=http_json_get,
        )

        self.assertFalse(http_called)
        self.assertFalse(services["botReady"])
        self.assertFalse(services["botApiPortOpen"])
        self.assertEqual(services["botApiReason"], "CP_UP_BOT_DOWN")
        self.assertFalse(services["codexRequired"])
        self.assertIsNone(services["codexReady"])

    async def test_probe_runtime_services_maps_bot_api_timeout(self) -> None:
        async def tcp_probe(label: str, host: str, port: int) -> tuple[str, bool]:
            _ = host, port
            return label, True

        async def http_json_get(url: str, timeout_sec: float) -> tuple[int, Any]:
            _ = url, timeout_sec
            raise asyncio.TimeoutError()

        services = await probe_control_page_runtime_services(
            service_urls={"main": "http://127.0.0.1:9820"},
            bot_api_host="127.0.0.1",
            bot_api_port=8798,
            bot_api_state_path="/api/state",
            bot_api_probe_timeout_sec=0.75,
            action_backend="direct",
            codex_gateway_port=8799,
            voyager_alive_probe=lambda: asyncio.sleep(0, result=True),
            tcp_probe=tcp_probe,
            http_json_get=http_json_get,
        )

        self.assertFalse(services["botReady"])
        self.assertTrue(services["botApiPortOpen"])
        self.assertEqual(services["botApiState"], "partial")
        self.assertEqual(services["botApiReason"], "CP_BOT_PROXY_TIMEOUT")
        self.assertEqual(services["botApiErrorKind"], "bot_api_timeout")


if __name__ == "__main__":
    unittest.main()
