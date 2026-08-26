from __future__ import annotations

import unittest
import json
import sys
from unittest.mock import AsyncMock, Mock, patch
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

    def test_memory_vault_open_failure_does_not_expose_exception(self) -> None:
        secret = "Bearer secret C:\\private http://internal"
        with (
            patch.object(
                control_page_server,
                "ensure_memory_vault_layout",
                return_value=Path("C:/Vault"),
            ),
            patch.object(
                control_page_server,
                "open_url_with_system",
                side_effect=OSError(secret),
            ),
            patch.object(
                control_page_server,
                "open_path_with_system",
                side_effect=OSError(secret),
            ),
        ):
            payload = control_page_server.open_memory_vault_payload()

        serialized = json.dumps(payload)
        self.assertFalse(payload["ok"])
        self.assertEqual(payload["error"], "open_memory_vault_failed")
        self.assertNotIn("secret", serialized)
        self.assertNotIn("private", serialized)
        self.assertNotIn("internal", serialized)
        self.assertIn("apply_runtime_health_overrides", self.local_server)
        self.assertIn("from .runtime_services import load_service_manifest, manifest_to_dict", self.local_server)

    def test_state_payload_merges_runtime_service_health(self) -> None:
        self.assertIn("async def current_boot_progress() -> dict[str, Any]:", self.local_server)
        self.assertIn("service_health = await cached_runtime_health()", self.local_server)
        self.assertIn('"serviceHealth": service_health', self.local_server)
        self.assertIn('runtime["serviceHealth"] = service_health', self.local_server)
        self.assertIn('runtime["manifestVersion"] = service_health.get("manifestVersion")', self.local_server)
        self.assertIn('runtime["controlPlane"] = control_plane', self.local_server)
        self.assertIn('"lastProxyFailure": safe_proxy_failure', self.local_server)
        self.assertIn("public_error_code(", self.local_server)

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

    def test_discord_mode_has_confirmed_public_endpoints(self) -> None:
        self.assertIn("async def discord_mode_preview_handler", self.local_server)
        self.assertIn("async def discord_mode_apply_handler", self.local_server)
        self.assertIn('"start_discord_bot" if enabled else "stop_discord_bot"', self.local_server)
        self.assertIn(
            'app.router.add_post("/api/control-page/discord-mode/preview", discord_mode_preview_handler)',
            self.local_server,
        )
        self.assertIn(
            'app.router.add_post("/api/control-page/discord-mode/apply", discord_mode_apply_handler)',
            self.local_server,
        )

    def test_runtime_health_is_cached_for_state_polling(self) -> None:
        self.assertIn("RUNTIME_HEALTH_CACHE_TTL_SEC", self.local_server)
        self.assertIn("RUNTIME_HEALTH_CACHE_MAX_STALE_SEC", self.local_server)
        self.assertIn("async def cached_runtime_health", self.local_server)
        self.assertIn("RuntimeHealthSnapshotCache(", self.local_server)
        self.assertIn("CONTROL_PAGE_RUNTIME_HEALTH_CACHE.get", self.local_server)
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

    async def test_discord_mode_preview_maps_exact_boolean_to_fixed_action(self) -> None:
        client = Mock()
        client.preview.side_effect = lambda action_id: {
            "ok": True,
            "previewToken": f"token-{action_id}",
            "expiresAt": 1120.0,
        }

        for enabled, action_id in (
            (True, "start_discord_bot"),
            (False, "stop_discord_bot"),
        ):
            class _Request:
                async def json(self) -> dict[str, object]:
                    return {"enabled": enabled}

            with self.subTest(enabled=enabled), patch.object(
                control_page_server,
                "HostSupervisorClient",
                return_value=client,
            ):
                response = await control_page_server.discord_mode_preview_handler(_Request())
                payload = json.loads(response.text or "{}")

            self.assertEqual(response.status, 200)
            self.assertTrue(payload["ok"], payload)
            self.assertIs(payload["enabled"], enabled)
            self.assertEqual(payload["actionId"], action_id)
            self.assertEqual(payload["confirmToken"], f"token-{action_id}")

        self.assertEqual(
            [call.args[0] for call in client.preview.call_args_list],
            ["start_discord_bot", "stop_discord_bot"],
        )

    async def test_discord_mode_rejects_non_boolean_or_extra_fields(self) -> None:
        for body in (
            {},
            {"enabled": 1},
            {"enabled": "false"},
            {"enabled": False, "command": "anything"},
        ):
            class _Request:
                async def json(self) -> dict[str, object]:
                    return body

            with self.subTest(body=body):
                response = await control_page_server.discord_mode_preview_handler(_Request())
                payload = json.loads(response.text or "{}")

            self.assertEqual(response.status, 400)
            self.assertEqual(payload["error"], "invalid_discord_mode_request")

        class _ApplyRequest:
            async def json(self) -> dict[str, object]:
                return {"enabled": False}

        response = await control_page_server.discord_mode_apply_handler(_ApplyRequest())
        payload = json.loads(response.text or "{}")
        self.assertEqual(response.status, 400)
        self.assertEqual(payload["error"], "invalid_discord_mode_request")

    async def test_discord_mode_apply_is_accepted_then_health_confirms(self) -> None:
        private = "PRIVATE_HOST_DETAIL"
        client = Mock()
        client.apply.return_value = {
            "ok": True,
            "status": "stopped",
            "detail": private,
        }

        class _Request:
            async def json(self) -> dict[str, object]:
                return {"enabled": False, "confirmToken": "one-time-token"}

        with (
            patch.object(
                control_page_server,
                "HostSupervisorClient",
                return_value=client,
            ),
            patch.object(
                control_page_server.CONTROL_PAGE_RUNTIME_HEALTH_CACHE,
                "clear",
            ) as clear_cache,
            patch.object(
                control_page_server,
                "_reconcile_stopped_discord_voice_input_owner",
                new=AsyncMock(
                    return_value={"ok": True, "retired": True}
                ),
            ) as reconcile,
        ):
            response = await control_page_server.discord_mode_apply_handler(_Request())
            payload = json.loads(response.text or "{}")

        self.assertEqual(response.status, 202)
        self.assertEqual(payload["state"], "stopping")
        self.assertNotIn("ready", payload)
        self.assertNotIn(private, repr(payload))
        client.apply.assert_called_once_with("stop_discord_bot", "one-time-token")
        reconcile.assert_awaited_once_with()
        clear_cache.assert_called_once_with()

    async def test_discord_stop_fails_closed_when_owner_is_unreconciled(
        self,
    ) -> None:
        private = "PRIVATE_UNSIGNED_HOST_RESPONSE"
        client = Mock()
        client.apply.return_value = {
            "ok": True,
            "status": "stopped",
        }

        class _Request:
            async def json(self) -> dict[str, object]:
                return {
                    "enabled": False,
                    "confirmToken": "one-time-token",
                }

        with (
            patch.object(
                control_page_server,
                "HostSupervisorClient",
                return_value=client,
            ),
            patch.object(
                control_page_server,
                "_reconcile_stopped_discord_voice_input_owner",
                new=AsyncMock(
                    return_value={
                        "ok": False,
                        "error": (
                            "voice_input_lease_retirement_unverified"
                        ),
                        "httpStatus": 503,
                        "detail": private,
                    }
                ),
            ) as reconcile,
            patch.object(
                control_page_server.CONTROL_PAGE_RUNTIME_HEALTH_CACHE,
                "clear",
            ) as clear_cache,
        ):
            response = await (
                control_page_server.discord_mode_apply_handler(_Request())
            )
            payload = json.loads(response.text or "{}")

        self.assertEqual(response.status, 503)
        self.assertFalse(payload["ok"])
        self.assertFalse(payload["enabled"])
        self.assertEqual(payload["state"], "stopped_unreconciled")
        self.assertFalse(payload["automaticRetry"])
        self.assertNotIn(private, repr(payload))
        reconcile.assert_awaited_once_with()
        clear_cache.assert_called_once_with()

    async def test_cached_health_returns_browser_safe_projection(self) -> None:
        raw_health = {
            "ok": False,
            "fullyHealthy": False,
            "coreState": "down",
            "optionalDegraded": True,
            "overallState": "down",
            "manifestVersion": "1.1",
            "runtimeName": "evelyn-local",
            "services": [
                {
                    "id": "local_io_bridge",
                    "label": "Local I/O Bridge",
                    "required": False,
                    "state": "degraded",
                    "ready": False,
                    "reason": "check_failed",
                    "checks": [
                        {
                            "kind": "artifact_json",
                            "ok": False,
                            "reason": "artifact_stale",
                            "target": "/app/runtime_artifacts/private.json",
                            "payload": {"pid": 42},
                        }
                    ],
                }
            ],
            "diagnostics": [],
            "legacyServices": {},
            "capabilities": {},
        }
        with patch.object(
            control_page_server.CONTROL_PAGE_RUNTIME_HEALTH_CACHE,
            "get",
            new=AsyncMock(return_value=raw_health),
        ):
            public = await control_page_server.cached_runtime_health()

        check = public["services"][0]["checks"][0]
        self.assertEqual(public["schema"], "runtime_health.public.v1")
        self.assertNotIn("target", check)
        self.assertNotIn("payload", check)
        self.assertFalse(public["privacy"]["rawProbePayloads"])

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
            cache_age_sec=(
                control_page_server.RUNTIME_HEALTH_CACHE_MAX_STALE_SEC
                + 1
            ),
        )

        self.assertTrue(stale["healthCache"]["stale"])
        self.assertEqual(stale["healthCache"]["ttlSec"], control_page_server.RUNTIME_HEALTH_CACHE_TTL_SEC)
        self.assertEqual(
            stale["healthCache"]["maxStaleSec"],
            control_page_server.RUNTIME_HEALTH_CACHE_MAX_STALE_SEC,
        )
        self.assertIn("Runtime health data is stale", stale["statusText"])
        self.assertIn("refresh before trusting readiness", stale["statusText"])

    async def test_boot_progress_uses_snapshot_cache_age(self) -> None:
        service_health = {
            "cache": {
                "ageSec": 2.75,
                "stale": False,
            },
            "legacyServices": {
                "botReady": True,
                "mainReady": True,
                "routerReady": True,
                "subReady": True,
                "ttsReady": True,
            },
            "services": [],
        }

        with patch.object(
            control_page_server,
            "cached_runtime_health",
            new=AsyncMock(return_value=service_health),
        ):
            progress = await control_page_server.current_boot_progress()

        self.assertEqual(progress["healthCacheAgeSec"], 2.75)

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
        self.assertNotIn(
            "detail",
            payload["runtime"]["controlPlane"]["lastProxyFailure"],
        )
        self.assertNotIn(
            "target",
            payload["runtime"]["controlPlane"]["lastProxyFailure"],
        )
        self.assertNotIn(
            "connection refused",
            json.dumps(payload, ensure_ascii=False),
        )
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
