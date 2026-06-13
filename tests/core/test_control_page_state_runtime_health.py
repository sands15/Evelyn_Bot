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
    async def asyncTearDown(self) -> None:
        control_page_server.bot_state_last_success_at = 0.0

    async def test_runtime_repair_preview_handler_blocks_live_execution(self) -> None:
        class _Request:
            async def json(self) -> dict[str, object]:
                return {"dryRun": False, "serviceId": "bot_api", "actionId": "restart"}

        response = await control_page_server.runtime_repair_preview_handler(_Request())
        payload = json.loads(response.text or "{}")

        self.assertEqual(response.status, 409)
        self.assertFalse(payload["ok"])
        self.assertTrue(payload["dryRun"])
        self.assertTrue(payload["dryRunOnly"])
        self.assertEqual(payload["error"], "repair_execution_not_enabled")
        self.assertIn("Use dryRun=true", payload["message"])

    def test_control_plane_status_text_covers_port_combinations_and_proxy_timeout(self) -> None:
        control_page_only = control_page_server.build_control_plane_state(ports={"bot": False})
        bot_open_timeout = control_page_server.build_control_plane_state(
            ports={"bot": True},
            proxy_failure={"kind": "http_timeout", "target": "http://127.0.0.1:8798/api/control-page/state"},
        )
        bot_open_ready = control_page_server.build_control_plane_state(ports={"bot": True})

        self.assertTrue(control_page_only["controlPage"]["ready"])
        self.assertFalse(control_page_only["botApi"]["ready"])
        self.assertFalse(control_page_only["botApi"]["portOpen"])
        self.assertEqual(control_page_only["botApi"]["state"], "down")
        self.assertIn("Control-Page is live on 8799", control_page_only["statusText"])
        self.assertIn("Bot API is not reachable on 8798", control_page_only["statusText"])

        self.assertFalse(bot_open_timeout["botApi"]["ready"])
        self.assertTrue(bot_open_timeout["botApi"]["portOpen"])
        self.assertEqual(bot_open_timeout["botApi"]["state"], "proxy_failed")
        self.assertIn("proxy timed out", bot_open_timeout["statusText"])
        self.assertEqual(bot_open_timeout["lastProxyFailure"]["kind"], "http_timeout")

        self.assertTrue(bot_open_ready["botApi"]["ready"])
        self.assertEqual(bot_open_ready["botApi"]["state"], "ready")
        self.assertIn("both responding", bot_open_ready["statusText"])

    def test_control_plane_status_text_marks_stale_runtime_health_cache(self) -> None:
        stale = control_page_server.build_control_plane_state(
            ports={"bot": True},
            cache_age_sec=control_page_server.RUNTIME_HEALTH_CACHE_TTL_SEC + 10,
        )

        self.assertTrue(stale["healthCache"]["stale"])
        self.assertEqual(stale["healthCache"]["ttlSec"], control_page_server.RUNTIME_HEALTH_CACHE_TTL_SEC)
        self.assertIn("Runtime health data is stale", stale["statusText"])
        self.assertIn("refresh before trusting readiness", stale["statusText"])

    async def test_degraded_state_keeps_required_schema_when_bot_api_is_missing(self) -> None:
        service_health = {
            "legacyServices": {
                "botReady": False,
                "mainReady": True,
                "routerReady": True,
                "subReady": True,
                "ttsReady": True,
                "visionReady": False,
                "summary": "Control-Page is live; Bot API is down.",
            },
            "manifestVersion": "1.0",
            "overallState": "degraded",
        }

        with patch.object(control_page_server, "cached_runtime_health", new=AsyncMock(return_value=service_health)):
            payload = await control_page_server.degraded_state(
                proxy_failure={"kind": "port_closed", "target": "http://127.0.0.1:8798/api/control-page/state"}
            )

        self.assertFalse(payload["ok"])
        self.assertIn("bootProgress", payload)
        self.assertIn("runtime", payload)
        self.assertIn("chat", payload)
        self.assertIn("commands", payload)
        self.assertIn("statusText", payload)
        self.assertEqual(payload["ui"]["reason"], "bot_api_unavailable")
        self.assertFalse(payload["runtime"]["services"]["botReady"])
        self.assertTrue(payload["runtime"]["services"]["mainReady"])
        self.assertEqual(payload["runtime"]["manifestVersion"], "1.0")
        self.assertEqual(payload["runtime"]["serviceHealth"], service_health)
        self.assertEqual(payload["runtime"]["controlPlane"]["botApi"]["state"], "down")
        self.assertEqual(payload["runtime"]["controlPlane"]["lastProxyFailure"]["kind"], "port_closed")

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

    async def test_state_handler_8799_only_returns_degraded_control_page_schema(self) -> None:
        class _Request(dict):
            query_string = ""

        service_health = {
            "legacyServices": {
                "botReady": False,
                "mainReady": True,
                "routerReady": True,
                "subReady": True,
                "ttsReady": True,
                "summary": "Control-Page is live; Bot API is down.",
            },
            "manifestVersion": "1.0",
            "overallState": "degraded",
        }

        async def _proxy_json(request: dict, method: str, path: str, body: object | None = None) -> None:
            control_page_server.remember_proxy_failure(
                request,
                control_page_server.proxy_failure_payload(
                    "port_closed",
                    url="http://127.0.0.1:8798/api/control-page/state",
                    detail="connection refused",
                ),
            )
            return None

        request = _Request()
        with patch.object(control_page_server, "proxy_json", new=AsyncMock(side_effect=_proxy_json)):
            with patch.object(control_page_server, "cached_runtime_health", new=AsyncMock(return_value=service_health)):
                response = await control_page_server.state_handler(request)

        payload = json.loads(response.text or "{}")
        self.assertFalse(payload["ok"])
        self.assertEqual(payload["runtime"]["controlPlane"]["controlPage"]["port"], 8799)
        self.assertEqual(payload["runtime"]["controlPlane"]["botApi"]["port"], 8798)
        self.assertEqual(payload["runtime"]["controlPlane"]["botApi"]["state"], "down")
        self.assertEqual(payload["runtime"]["controlPlane"]["lastProxyFailure"]["kind"], "port_closed")
        self.assertFalse(payload["runtime"]["services"]["botReady"])
        self.assertIn("Bot API is not reachable on 8798", payload["statusText"])

    async def test_memory_graph_endpoint_passes_include_internal_only_when_requested(self) -> None:
        class _Request:
            def __init__(self, query: dict[str, str]) -> None:
                self.query = query

        calls: list[dict[str, object]] = []

        def _export_memory_graph(**kwargs: object) -> dict[str, object]:
            calls.append(dict(kwargs))
            return {"ok": True, "nodes": [], "edges": [], "stats": {"include_internal": kwargs.get("include_internal")}}

        with patch.object(control_page_server, "export_memory_graph", side_effect=_export_memory_graph):
            default_response = await control_page_server.memory_graph_handler(_Request({"max_nodes": "160"}))
            internal_response = await control_page_server.memory_graph_handler(
                _Request({"max_nodes": "160", "include_internal": "true"})
            )

        default_payload = json.loads(default_response.text or "{}")
        internal_payload = json.loads(internal_response.text or "{}")
        self.assertEqual(calls[0], {"max_nodes": 160, "include_internal": False})
        self.assertEqual(calls[1], {"max_nodes": 160, "include_internal": True})
        self.assertFalse(default_payload["stats"]["include_internal"])
        self.assertTrue(internal_payload["stats"]["include_internal"])

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
            "services": [
                {
                    "id": "bot_api",
                    "state": "up",
                    "checkedAt": 1234.5,
                }
            ],
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
        self.assertEqual(payload["runtime"]["controlPlane"]["botApi"]["lastCheckedAt"], 1234.5)
        self.assertGreater(payload["runtime"]["controlPlane"]["botApi"]["lastSuccessfulStateAt"], 0)

    async def test_degraded_state_keeps_last_successful_bot_state_timestamp(self) -> None:
        service_health = {
            "legacyServices": {
                "botReady": False,
                "mainReady": True,
                "routerReady": True,
                "subReady": True,
                "ttsReady": True,
                "summary": "Control-Page is live; Bot API is down.",
            },
            "manifestVersion": "1.0",
            "services": [{"id": "bot_api", "state": "down", "checkedAt": 2000.0}],
        }

        with patch.object(control_page_server, "bot_state_last_success_at", 1999.0):
            with patch.object(control_page_server, "cached_runtime_health", new=AsyncMock(return_value=service_health)):
                payload = await control_page_server.degraded_state(
                    proxy_failure={"kind": "port_closed", "target": "http://127.0.0.1:8798/api/control-page/state"}
                )

        bot_api = payload["runtime"]["controlPlane"]["botApi"]
        self.assertEqual(bot_api["lastCheckedAt"], 2000.0)
        self.assertEqual(bot_api["lastSuccessfulStateAt"], 1999.0)

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
