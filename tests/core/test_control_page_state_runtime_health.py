from __future__ import annotations

import unittest
import json
import sys
from unittest.mock import AsyncMock, patch
from pathlib import Path


REPO_ROOT = next(path for path in Path(__file__).resolve().parents if (path / "main.py").exists())
LOCAL_SERVER = REPO_ROOT / "evelyn_core" / "runtime" / "evelyn_core" / "control_page_server.py"
RUNTIME_ROOT = REPO_ROOT / "evelyn_core" / "runtime"
if str(RUNTIME_ROOT) not in sys.path:
    sys.path.insert(0, str(RUNTIME_ROOT))

from evelyn_core import control_page_server


class ControlPageRuntimeHealthContractTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.local_server = LOCAL_SERVER.read_text(encoding="utf-8")

    def test_control_page_imports_manifest_health_layer(self) -> None:
        self.assertIn("collect_runtime_health", self.local_server)
        self.assertIn("apply_runtime_health_overrides", self.local_server)
        self.assertIn("from .runtime_services import load_service_manifest, manifest_to_dict", self.local_server)

    def test_state_payload_merges_runtime_service_health(self) -> None:
        self.assertIn("async def current_boot_progress() -> dict[str, Any]:", self.local_server)
        self.assertIn("service_health = await cached_runtime_health()", self.local_server)
        self.assertIn('"serviceHealth": service_health', self.local_server)
        self.assertIn('runtime["serviceHealth"] = service_health', self.local_server)
        self.assertIn('runtime["manifestVersion"] = service_health.get("manifestVersion")', self.local_server)
        self.assertIn('runtime["controlPlane"] = control_plane', self.local_server)
        self.assertIn('"lastProxyFailure": dict(proxy_failure or {})', self.local_server)

    def test_runtime_health_has_dedicated_read_only_endpoints(self) -> None:
        self.assertIn("async def runtime_health_handler", self.local_server)
        self.assertIn("async def runtime_health_override_handler", self.local_server)
        self.assertIn("async def runtime_manifest_handler", self.local_server)
        self.assertIn('app.router.add_get("/api/control-page/runtime-health", runtime_health_handler)', self.local_server)
        self.assertIn('app.router.add_post("/api/control-page/runtime-health/override", runtime_health_override_handler)', self.local_server)
        self.assertIn('app.router.add_get("/api/control-page/runtime-manifest", runtime_manifest_handler)', self.local_server)
        self.assertIn("request_is_loopback(request)", self.local_server)
        self.assertIn("apply_runtime_health_overrides", self.local_server)

    def test_runtime_repair_has_dry_run_preview_endpoints(self) -> None:
        self.assertIn("append_repair_event", self.local_server)
        self.assertIn("execute_runtime_repair_plan", self.local_server)
        self.assertIn("build_runtime_repair_plan", self.local_server)
        self.assertIn("async def runtime_repair_handler", self.local_server)
        self.assertIn("async def runtime_repair_preview_handler", self.local_server)
        self.assertIn("async def runtime_repair_apply_handler", self.local_server)
        self.assertIn('app.router.add_get("/api/control-page/runtime-repair", runtime_repair_handler)', self.local_server)
        self.assertIn('app.router.add_post("/api/control-page/runtime-repair/preview", runtime_repair_preview_handler)', self.local_server)
        self.assertIn('app.router.add_post("/api/control-page/runtime-repair/apply", runtime_repair_apply_handler)', self.local_server)
        self.assertIn('payload.get("dryRun") is False', self.local_server)
        self.assertIn("confirm_token = str", self.local_server)
        self.assertIn('"event": "apply_response"', self.local_server)
        self.assertIn('"repairLog"', self.local_server)

    def test_runtime_health_is_cached_for_state_polling(self) -> None:
        self.assertIn("RUNTIME_HEALTH_CACHE_TTL_SEC", self.local_server)
        self.assertIn("async def cached_runtime_health", self.local_server)
        self.assertIn("runtime_health_cache_lock = asyncio.Lock()", self.local_server)
        self.assertIn("cached_runtime_health(force=True)", self.local_server)

    def test_static_control_page_assets_declare_utf8(self) -> None:
        self.assertIn('".html": "text/html; charset=utf-8"', self.local_server)
        self.assertIn('".js": "application/javascript; charset=utf-8"', self.local_server)
        self.assertIn('".css": "text/css; charset=utf-8"', self.local_server)
        self.assertIn('response.headers["Content-Type"] = static_content_type(index_path)', self.local_server)
        self.assertIn('response.headers["Content-Type"] = content_type', self.local_server)


class ControlPageStateMergeTests(unittest.IsolatedAsyncioTestCase):
    async def test_degraded_state_separates_control_page_from_bot_api(self) -> None:
        service_health = {
            "legacyServices": {
                "botReady": False,
                "mainReady": True,
                "routerReady": True,
                "subReady": True,
                "ttsReady": True,
                "summary": "",
            },
            "manifestVersion": "1.0",
        }
        proxy_failure = {
            "kind": "port_closed",
            "target": "http://127.0.0.1:8798/api/control-page/state",
        }

        with patch.object(control_page_server, "cached_runtime_health", new=AsyncMock(return_value=service_health)):
            payload = await control_page_server.degraded_state(proxy_failure=proxy_failure)

        control_plane = payload["runtime"]["controlPlane"]
        self.assertEqual(control_plane["controlPage"]["role"], "Control-Page")
        self.assertEqual(control_plane["botApi"]["role"], "Bot API")
        self.assertEqual(control_plane["botApi"]["state"], "down")
        self.assertEqual(control_plane["lastProxyFailure"]["kind"], "port_closed")
        self.assertIn("Bot API", payload["statusText"])
        self.assertNotIn("Discord bot processor", payload["statusText"])

    async def test_state_handler_uses_manifest_legacy_services(self) -> None:
        # This test validates that /api/control-page/state payload keeps legacy fields
        # consistent with runtime.serviceHealth.legacyServices coming from manifest-based health.
        class _Request:
            query_string = ""

        proxied_state = {
            "runtime": {
                "services": {
                    "botReady": True,
                    "mainReady": False,
                    "routerReady": True,
                    "subReady": True,
                    "ttsReady": False,
                    "visionReady": False,
                    "legacyOnly": "unchanged",
                },
            },
            "ok": True,
        }
        service_health = {
            "legacyServices": {
                "botReady": False,
                "mainReady": True,
                "routerReady": False,
                "subReady": True,
                "ttsReady": True,
                "summary": "manifest-based-summary",
                "visionReady": True,
                "codexRequired": True,
                "codexBackend": "codex-gateway",
            },
            "manifestVersion": "1.0",
        }

        async def _proxy_json(_: object, method: str, path: str, body: object | None = None) -> control_page_server.web.Response:
            return control_page_server.web.Response(status=200, text=json.dumps(proxied_state))

        with patch.object(control_page_server, "proxy_json", new=AsyncMock(side_effect=_proxy_json)):
            with patch.object(control_page_server, "cached_runtime_health", new=AsyncMock(return_value=service_health)):
                response = await control_page_server.state_handler(_Request())

        response_text = response.text or "{}"
        payload = json.loads(response_text)
        services = payload["runtime"]["services"]

        self.assertFalse(services["botReady"])
        self.assertTrue(services["mainReady"])
        self.assertFalse(services["routerReady"])
        self.assertTrue(services["ttsReady"])
        self.assertEqual(services["summary"], "manifest-based-summary")
        self.assertTrue(services["visionReady"])
        self.assertTrue(services["codexRequired"])
        self.assertEqual(services["legacyOnly"], "unchanged")
        self.assertEqual(payload["runtime"]["controlPlane"]["botApi"]["role"], "Bot API")

    async def test_state_handler_reports_invalid_bot_api_json_as_proxy_failure(self) -> None:
        class _Request(dict):
            query_string = ""

        service_health = {
            "legacyServices": {
                "botReady": True,
                "mainReady": True,
                "routerReady": True,
                "subReady": True,
                "ttsReady": True,
            },
            "manifestVersion": "1.0",
        }

        async def _proxy_json(_: object, method: str, path: str, body: object | None = None) -> control_page_server.web.Response:
            return control_page_server.web.Response(status=200, text="{not-json")

        with patch.object(control_page_server, "proxy_json", new=AsyncMock(side_effect=_proxy_json)):
            with patch.object(control_page_server, "cached_runtime_health", new=AsyncMock(return_value=service_health)):
                response = await control_page_server.state_handler(_Request())

        payload = json.loads(response.text or "{}")
        self.assertEqual(payload["runtime"]["controlPlane"]["lastProxyFailure"]["kind"], "json_parse_failed")
        self.assertIn("invalid state JSON", payload["runtime"]["controlPlane"]["statusText"])


if __name__ == "__main__":
    unittest.main()
