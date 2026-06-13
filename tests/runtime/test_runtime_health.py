from __future__ import annotations

import sys
import unittest
from pathlib import Path
from typing import Any


REPO_ROOT = next(path for path in Path(__file__).resolve().parents if (path / "main.py").exists())
RUNTIME_ROOT = REPO_ROOT / "evelyn_core" / "runtime"
if str(RUNTIME_ROOT) not in sys.path:
    sys.path.insert(0, str(RUNTIME_ROOT))

from evelyn_core.runtime_health import apply_runtime_health_overrides, collect_runtime_health  # noqa: E402
from evelyn_core.runtime_services import HealthProbeSpec, ServiceSpec, load_service_manifest  # noqa: E402


def fake_probe(states: dict[str, str]):
    async def runner(service: ServiceSpec, check: HealthProbeSpec) -> dict[str, Any]:
        state = states.get(service.id, "up")
        target = f"{check.host}:{check.port}{check.path}"
        if state == "down":
            return {"kind": check.kind, "ok": False, "reason": "connection_failed", "target": target}
        if state == "partial" and check.kind == "http":
            return {"kind": "http", "ok": False, "reason": "timeout", "target": target, "status": None}
        payload = {"lastActionReady": False} if service.id == "codex_gateway" and state == "action_failed" and check.kind == "http" else None
        return {
            "kind": check.kind,
            "ok": True,
            "reason": "ok",
            "target": target,
            "status": 200 if check.kind == "http" else None,
            "payload": payload,
        }

    return runner


class RuntimeHealthTests(unittest.IsolatedAsyncioTestCase):
    async def test_all_services_up_returns_legacy_ready_flags(self) -> None:
        manifest = load_service_manifest(force=True)
        health = await collect_runtime_health(manifest=manifest, probe_runner=fake_probe({}))

        self.assertTrue(health["ok"])
        self.assertEqual(health["overallState"], "up")
        self.assertTrue(health["legacyServices"]["botReady"])
        self.assertTrue(health["legacyServices"]["mainReady"])
        self.assertTrue(health["legacyServices"]["routerReady"])
        self.assertTrue(health["legacyServices"]["subReady"])
        self.assertTrue(health["legacyServices"]["ttsReady"])
        self.assertTrue(health["legacyServices"]["sttReady"])

    async def test_stt_down_is_required_voice_input_diagnostic(self) -> None:
        manifest = load_service_manifest(force=True)
        health = await collect_runtime_health(manifest=manifest, probe_runner=fake_probe({"stt": "down"}))
        codes = {diagnostic["code"] for diagnostic in health["diagnostics"]}

        self.assertFalse(health["ok"])
        self.assertEqual(health["overallState"], "down")
        self.assertIn("STT_DOWN", codes)
        self.assertFalse(health["legacyServices"]["sttReady"])

    async def test_control_page_up_bot_api_down_is_explicit_diagnostic(self) -> None:
        manifest = load_service_manifest(force=True)
        health = await collect_runtime_health(manifest=manifest, probe_runner=fake_probe({"bot_api": "down"}))
        codes = {diagnostic["code"] for diagnostic in health["diagnostics"]}

        self.assertFalse(health["ok"])
        self.assertEqual(health["overallState"], "down")
        self.assertIn("CP_UP_BOT_DOWN", codes)
        self.assertIn("BOT_API_DOWN_WITH_CONTROL_PAGE_UP", codes)
        self.assertFalse(health["legacyServices"]["botReady"])

    async def test_bot_api_open_but_http_not_ready_is_partial(self) -> None:
        manifest = load_service_manifest(force=True)
        health = await collect_runtime_health(manifest=manifest, probe_runner=fake_probe({"bot_api": "partial"}))
        services = {service["id"]: service for service in health["services"]}
        codes = {diagnostic["code"] for diagnostic in health["diagnostics"]}

        self.assertEqual(services["bot_api"]["state"], "partial")
        self.assertIn("BOT_API_PARTIAL", codes)
        self.assertFalse(health["legacyServices"]["botReady"])

    async def test_safe_health_override_simulates_down_without_probe_failure(self) -> None:
        manifest = load_service_manifest(force=True)
        health = await collect_runtime_health(manifest=manifest, probe_runner=fake_probe({}))
        simulated = apply_runtime_health_overrides(
            health,
            {
                "vision": {
                    "serviceId": "vision",
                    "state": "down",
                    "reason": "operator_simulated_down",
                    "message": "Vision is safely simulated as down.",
                    "expiresAt": 2000.0,
                }
            },
            manifest=manifest,
            now_ts=1000.0,
        )
        services = {service["id"]: service for service in simulated["services"]}
        codes = {diagnostic["code"] for diagnostic in simulated["diagnostics"]}

        self.assertEqual(simulated["overallState"], "degraded")
        self.assertEqual(services["vision"]["state"], "down")
        self.assertTrue(services["vision"]["simulated"])
        self.assertEqual(services["vision"]["checks"][-1]["kind"], "override")
        self.assertIn("VISION_DOWN_SIMULATED", codes)
        self.assertEqual(simulated["simulatedOverrides"][0]["serviceId"], "vision")
        self.assertTrue(services["vision"]["suggestedActions"])

    async def test_codex_gateway_action_failure_is_warning_diagnostic(self) -> None:
        manifest = load_service_manifest(force=True)
        health = await collect_runtime_health(manifest=manifest, probe_runner=fake_probe({"codex_gateway": "action_failed"}))
        services = {service["id"]: service for service in health["services"]}
        codes = {diagnostic["code"] for diagnostic in health["diagnostics"]}

        self.assertTrue(services["codex_gateway"]["ready"])
        self.assertIn("CODEX_GATEWAY_ACTION_FAILED", codes)


if __name__ == "__main__":
    unittest.main()
