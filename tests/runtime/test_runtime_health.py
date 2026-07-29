from __future__ import annotations

import sys
import unittest
from pathlib import Path
from typing import Any
from unittest.mock import patch


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
        if service.id == "voyager" and check.kind == "http" and check.path == "/status":
            if state == "task_unverified":
                payload = {
                    "recovery_state": {
                        "scope": "task",
                        "domain": "task_bookkeeping_unverified",
                        "subdomain": "mining",
                        "reason": "bookkeeping status 'effect_verified' has no explicit success flag",
                        "recommended_action": "verify_target_block_and_tool",
                        "healthy": False,
                    },
                    "last_task_contract_decision": {"contract": "mine_coal", "status": "accepted"},
                    "current_task_bookkeeping": {"status": "effect_verified"},
                    "last_world_effect_verification": {"outcome": "present"},
                    "last_critic_result": {"reason": "not checked"},
                }
            elif state == "contract_failed":
                payload = {
                    "recovery_state": {
                        "scope": "task",
                        "domain": "task_failed",
                        "subdomain": "pathfinding",
                        "reason": "move_distance_unmet",
                        "recommended_action": "replan_route",
                        "healthy": False,
                    },
                    "last_task_contract_decision": {"contract": "move_to_tree", "success": False, "reason": "distance_unmet"},
                    "current_task_bookkeeping": {"status": "failed", "success": False},
                    "last_critic_result": {"success": False, "reason": "target not reached"},
                }
            elif state == "runtime_recovery":
                payload = {
                    "recovery_state": {
                        "scope": "runtime",
                        "domain": "bridge_http",
                        "reason": "Runner is up but the bridge HTTP port is not reachable.",
                        "healthy": False,
                    }
                }
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
    async def test_runtime_error_observability_is_additive(self) -> None:
        expected = {
            "schema": "runtime_errors.summary.v1",
            "state": "clear",
        }
        manifest = load_service_manifest(force=True)
        with patch(
            "evelyn_core.runtime_health.collect_runtime_error_observability",
            return_value=expected,
        ):
            health = await collect_runtime_health(
                manifest=manifest,
                probe_runner=fake_probe({}),
            )

        self.assertEqual(health["observability"]["exceptions"], expected)
        self.assertEqual(health["overallState"], "up")

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
        self.assertEqual(health["legacyServices"]["summary"], "Control-Page and Evelyn runtime are ready.")

    async def test_legacy_summary_uses_operator_facing_language(self) -> None:
        manifest = load_service_manifest(force=True)
        bot_down = await collect_runtime_health(manifest=manifest, probe_runner=fake_probe({"bot_api": "down"}))
        model_starting = await collect_runtime_health(manifest=manifest, probe_runner=fake_probe({"main_llm": "down"}))

        for health in (bot_down, model_starting):
            summary = str(health["legacyServices"]["summary"])
            self.assertNotIn("bot processor", summary.lower())
            self.assertNotIn("control page live |", summary.lower())

        self.assertEqual(bot_down["legacyServices"]["summary"], "Control-Page is open; Bot API is not ready.")
        self.assertEqual(
            model_starting["legacyServices"]["summary"],
            "Control-Page is open; model or voice services are still starting.",
        )

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

    async def test_optional_voyager_stack_failures_are_warning_diagnostics(self) -> None:
        manifest = load_service_manifest(force=True)
        health = await collect_runtime_health(
            manifest=manifest,
            probe_runner=fake_probe({"voyager": "down", "codex_gateway": "down"}),
        )
        services = {service["id"]: service for service in health["services"]}
        diagnostics = {diagnostic["code"]: diagnostic for diagnostic in health["diagnostics"]}

        self.assertEqual(health["overallState"], "degraded")
        self.assertEqual(services["voyager"]["state"], "down")
        self.assertEqual(services["codex_gateway"]["state"], "down")
        self.assertEqual(diagnostics["VOYAGER_DOWN"]["severity"], "warning")
        self.assertEqual(diagnostics["CODEX_GATEWAY_DOWN"]["severity"], "warning")
        self.assertIn("Minecraft autonomy", diagnostics["VOYAGER_DOWN"]["message"])
        self.assertIn("Voyager code execution", diagnostics["CODEX_GATEWAY_DOWN"]["message"])
        self.assertEqual(diagnostics["VOYAGER_DOWN"]["suggestedActions"][0]["id"], "start_voyager")
        self.assertEqual(diagnostics["CODEX_GATEWAY_DOWN"]["suggestedActions"][0]["id"], "start_codex_gateway")

    async def test_voyager_status_contract_unverified_is_warning_diagnostic(self) -> None:
        manifest = load_service_manifest(force=True)
        health = await collect_runtime_health(manifest=manifest, probe_runner=fake_probe({"voyager": "task_unverified"}))
        services = {service["id"]: service for service in health["services"]}
        diagnostics = {diagnostic["code"]: diagnostic for diagnostic in health["diagnostics"]}

        self.assertTrue(health["ok"])
        self.assertFalse(health["fullyHealthy"])
        self.assertEqual(health["coreState"], "up")
        self.assertTrue(health["optionalDegraded"])
        self.assertEqual(health["overallState"], "degraded")
        self.assertTrue(services["voyager"]["ready"])
        self.assertIn("VOYAGER_TASK_CONTRACT_UNVERIFIED", diagnostics)
        self.assertEqual(diagnostics["VOYAGER_TASK_CONTRACT_UNVERIFIED"]["severity"], "warning")
        self.assertIn("task_bookkeeping_unverified", diagnostics["VOYAGER_TASK_CONTRACT_UNVERIFIED"]["details"])
        self.assertIn("contract=accepted", diagnostics["VOYAGER_TASK_CONTRACT_UNVERIFIED"]["details"])
        self.assertIn("bookkeeping=effect_verified", diagnostics["VOYAGER_TASK_CONTRACT_UNVERIFIED"]["details"])

    async def test_voyager_status_contract_failure_is_warning_diagnostic(self) -> None:
        manifest = load_service_manifest(force=True)
        health = await collect_runtime_health(manifest=manifest, probe_runner=fake_probe({"voyager": "contract_failed"}))
        diagnostics = {diagnostic["code"]: diagnostic for diagnostic in health["diagnostics"]}

        self.assertEqual(health["overallState"], "degraded")
        self.assertIn("VOYAGER_TASK_CONTRACT_FAILED", diagnostics)
        self.assertIn("pathfinding", diagnostics["VOYAGER_TASK_CONTRACT_FAILED"]["details"])
        self.assertIn("contract=success=false", diagnostics["VOYAGER_TASK_CONTRACT_FAILED"]["details"])

    async def test_voyager_status_runtime_recovery_is_warning_diagnostic(self) -> None:
        manifest = load_service_manifest(force=True)
        health = await collect_runtime_health(manifest=manifest, probe_runner=fake_probe({"voyager": "runtime_recovery"}))
        services = {service["id"]: service for service in health["services"]}
        diagnostics = {diagnostic["code"]: diagnostic for diagnostic in health["diagnostics"]}

        self.assertTrue(health["ok"])
        self.assertFalse(health["fullyHealthy"])
        self.assertEqual(health["overallState"], "degraded")
        self.assertTrue(services["voyager"]["httpReady"])
        self.assertFalse(services["voyager"]["runtimeReady"])
        self.assertFalse(services["voyager"]["ready"])
        self.assertTrue(health["legacyServices"]["voyagerHttpReady"])
        self.assertFalse(health["legacyServices"]["voyagerRuntimeReady"])
        self.assertFalse(health["legacyServices"]["voyagerReady"])
        self.assertIn("VOYAGER_RUNTIME_RECOVERY_REQUIRED", diagnostics)
        self.assertIn("bridge_http", diagnostics["VOYAGER_RUNTIME_RECOVERY_REQUIRED"]["details"])


if __name__ == "__main__":
    unittest.main()
