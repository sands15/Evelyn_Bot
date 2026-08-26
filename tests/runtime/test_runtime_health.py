from __future__ import annotations

import asyncio
import json
import sys
import os
import unittest
from pathlib import Path
from typing import Any
from unittest.mock import patch


REPO_ROOT = next(path for path in Path(__file__).resolve().parents if (path / "main.py").exists())
RUNTIME_ROOT = REPO_ROOT / "evelyn_core" / "runtime"
if str(RUNTIME_ROOT) not in sys.path:
    sys.path.insert(0, str(RUNTIME_ROOT))

from evelyn_core.runtime_health import (  # noqa: E402
    apply_runtime_health_overrides,
    check_service,
    collect_runtime_health,
    public_runtime_health_snapshot,
)
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
            if state == "up":
                payload = {
                    "service": "mindcraft_minecraft",
                    "runtime": "mindcraft",
                    "running": True,
                    "telemetry_fresh": True,
                    "minecraft_connected": True,
                    "world_lease_authorized": True,
                    "recovery_state": {
                        "scope": "healthy",
                        "domain": "healthy",
                        "healthy": True,
                    },
                    "functional_readiness": {
                        "schema": "minecraft_autonomy.readiness.v1",
                        "state": "ready",
                        "ready": True,
                        "blockers": [],
                        "dependencies": {
                            "worldLeaseAuthorized": True,
                            "runnerAlive": True,
                            "telemetryFresh": True,
                            "minecraftConnected": True,
                            "taskContractReady": True,
                            "effectObserverReady": True,
                            "autonomyActive": True,
                        },
                        "taskContract": {
                            "schema": "mindcraft.task-contract.v1",
                            "goalManagerMode": "gated",
                            "autonomyState": "active",
                            "commandGate": "evelyn_goal_manager",
                            "effectVerification": "explicit_postcondition",
                        },
                        "contentFree": True,
                    },
                }
            elif state == "task_unverified":
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
            elif state == "mindcraft_http_only":
                payload = {
                    "service": "mindcraft_minecraft",
                    "runtime": "mindcraft",
                    "running": False,
                    "connected": False,
                }
            elif state == "mindcraft_blocked":
                payload = {
                    "service": "mindcraft_minecraft",
                    "runtime": "mindcraft",
                    "running": True,
                    "telemetry_fresh": True,
                    "minecraft_connected": False,
                    "world_lease_authorized": True,
                    "functional_readiness": {
                        "schema": "minecraft_autonomy.readiness.v1",
                        "state": "starting",
                        "ready": False,
                        "blockers": [
                            "minecraft_not_connected"
                        ],
                        "dependencies": {
                            "worldLeaseAuthorized": True,
                            "runnerAlive": True,
                            "telemetryFresh": True,
                            "minecraftConnected": False,
                            "taskContractReady": True,
                            "effectObserverReady": True,
                            "autonomyActive": True,
                        },
                        "taskContract": {
                            "schema": "mindcraft.task-contract.v1",
                            "goalManagerMode": "gated",
                            "autonomyState": "active",
                            "commandGate": "evelyn_goal_manager",
                            "effectVerification": "explicit_postcondition",
                        },
                        "contentFree": True,
                    },
                }
            elif state == "mindcraft_inconsistent":
                payload = {
                    "service": "mindcraft_minecraft",
                    "runtime": "mindcraft",
                    "running": True,
                    "telemetry_fresh": True,
                    "minecraft_connected": False,
                    "world_lease_authorized": True,
                    "recovery_state": {
                        "scope": "healthy",
                        "domain": "healthy",
                        "healthy": True,
                    },
                    "functional_readiness": {
                        "schema": "minecraft_autonomy.readiness.v1",
                        "state": "ready",
                        "ready": True,
                        "blockers": [],
                        "dependencies": {
                            "worldLeaseAuthorized": True,
                            "runnerAlive": True,
                            "telemetryFresh": True,
                            "minecraftConnected": True,
                            "taskContractReady": True,
                            "effectObserverReady": True,
                            "autonomyActive": True,
                        },
                        "taskContract": {
                            "schema": "mindcraft.task-contract.v1",
                            "goalManagerMode": "gated",
                            "autonomyState": "active",
                            "commandGate": "evelyn_goal_manager",
                            "effectVerification": "explicit_postcondition",
                        },
                        "contentFree": True,
                    },
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
    async def test_probe_timeout_is_bounded_and_content_free(self) -> None:
        probe = HealthProbeSpec(
            kind="tcp",
            host="127.0.0.1",
            port=1,
            timeout_ms=20,
        )
        service = ServiceSpec(
            id="stt",
            label="STT",
            kind="http",
            required=True,
            host=probe.host,
            port=probe.port,
            checks=(probe,),
        )

        async def blocked_probe(*_args):
            await asyncio.Event().wait()

        result = await asyncio.wait_for(
            check_service(service, probe_runner=blocked_probe),
            timeout=0.5,
        )

        self.assertFalse(result["ready"])
        self.assertEqual(result["state"], "down")
        self.assertEqual(result["reason"], "timeout")
        self.assertEqual(
            result["checks"],
            [
                {
                    "kind": "tcp",
                    "ok": False,
                    "reason": "timeout",
                    "elapsedMs": result["checks"][0]["elapsedMs"],
                }
            ],
        )

    async def test_public_projection_removes_raw_probe_evidence(self) -> None:
        raw = {
            "revision": 7,
            "ok": False,
            "fullyHealthy": False,
            "coreState": "down",
            "optionalDegraded": True,
            "overallState": "down",
            "summary": "private summary",
            "manifestVersion": "1.1",
            "runtimeName": "evelyn-local",
            "checkedAt": 1234.5,
            "services": [
                {
                    "id": "local_io_bridge",
                    "label": "Local I/O Bridge",
                    "required": False,
                    "host": "private-host",
                    "defaultHost": "C:\\private\\host",
                    "hostEnv": "PRIVATE_HOST",
                    "port": 0,
                    "state": "degraded",
                    "ready": False,
                    "reason": "check_failed",
                    "checkedAt": 1234.0,
                    "elapsedMs": 3.2,
                    "checks": [
                        {
                            "kind": "artifact_json",
                            "ok": False,
                            "reason": "artifact_stale",
                            "target": "/app/runtime_artifacts/local_bridge/status.json",
                            "payload": {
                                "pid": 4242,
                                "outputDevices": [
                                    {"name": "Private Headphones"}
                                ],
                            },
                            "error": "PrivateProbeError",
                            "ageSec": 9.0,
                            "staleAfterSec": 4.0,
                        }
                    ],
                    "suggestedActions": [
                        {
                            "id": "start_local_io_bridge",
                            "label": "Start Local I/O Bridge",
                            "risk": "medium",
                            "requiresConfirm": True,
                            "strategy": "start_if_down",
                        }
                    ],
                }
            ],
            "diagnostics": [
                {
                    "code": "LOCAL_IO_BRIDGE_DOWN",
                    "severity": "error",
                    "message": "Local I/O Bridge is unavailable.",
                    "details": "read C:\\private\\status.json",
                    "serviceIds": ["local_io_bridge"],
                    "suggestedActions": [],
                }
            ],
            "legacyServices": {
                "botReady": True,
                "privatePath": "C:\\private\\legacy.json",
            },
            "observability": {
                "exceptions": {
                    "schema": "runtime_errors.summary.v1",
                    "state": "attention",
                    "generatedAt": 1234.0,
                    "recentAfterSec": 3600.0,
                    "summary": {
                        "sourceCount": 1,
                        "availableCount": 1,
                        "staleCount": 0,
                        "currentErrorCount": 0,
                        "recentErrorCount": 1,
                        "totalCount": 1,
                        "privatePath": "C:\\private\\summary.json",
                    },
                    "sources": {
                        "localBridge": {
                            "id": "localBridge",
                            "label": "Private device label",
                            "state": "ready",
                            "available": True,
                            "stale": False,
                            "heartbeatAt": 1234.0,
                            "errorCount": 1,
                            "lastErrorAt": 1233.0,
                            "lastErrorCode": "tts_warmup_attempt_failed",
                            "lastErrorType": "TimeoutError",
                            "errorCounters": {
                                "tts_warmup_attempt_failed": 1,
                            },
                            "privatePayload": {
                                "path": "C:\\private\\errors.json",
                            },
                        },
                        "fastControlContinuity": {
                            "id": "fastControlContinuity",
                            "label": "private",
                            "state": "ready",
                            "available": True,
                            "stale": False,
                            "errorCount": 0,
                            "hasCurrentError": False,
                        },
                    },
                    "recentErrors": [
                        {
                            "source": "localBridge",
                            "at": 1233.0,
                            "code": "tts_warmup_attempt_failed",
                            "type": "TimeoutError",
                            "message": "C:\\private\\error.wav",
                        }
                    ],
                    "warnings": [],
                    "privatePayload": {"pid": 7777},
                }
            },
            "capabilities": {
                "voiceLocal": {
                    "state": "unavailable",
                    "ready": False,
                    "blockers": [
                        {
                            "code": "local_io_bridge_degraded",
                            "message": "Local I/O Bridge is unavailable.",
                            "serviceId": "local_io_bridge",
                        }
                    ],
                    "warnings": [],
                    "dependencies": [
                        {
                            "id": "local_io_bridge",
                            "label": "Local I/O Bridge",
                            "state": "degraded",
                            "ready": False,
                            "reason": "check_failed",
                            "checkedAt": 1234.0,
                        }
                    ],
                    "repairActions": [
                        {
                            "actionId": "start_host_supervisor_manual",
                            "serviceId": "host_supervisor",
                            "label": "Start Host Supervisor",
                            "requiresConfirm": False,
                            "manualCommand": "start_local.bat --background",
                        }
                    ],
                }
            },
        }

        health_only_sources = {
            "controlPage": "Control Page",
            "botApi": "Bot API",
            "mainLlm": "Main LLM",
            "subLlm": "Sub LLM",
            "routerLlm": "Router LLM",
            "tts": "TTS",
        }
        for source_id in health_only_sources:
            raw["observability"]["exceptions"]["sources"][source_id] = {
                "id": source_id,
                "label": "private",
                "state": "down",
                "available": True,
                "stale": False,
                "errorCount": 0,
                "hasCurrentError": True,
            }

        public = public_runtime_health_snapshot(raw)
        service = public["services"][0]
        check = service["checks"][0]

        self.assertEqual(public["schema"], "runtime_health.public.v1")
        self.assertEqual(public["revision"], 7)
        self.assertEqual(service["state"], "degraded")
        self.assertEqual(check["reason"], "artifact_stale")
        self.assertNotIn("host", service)
        self.assertNotIn("defaultHost", service)
        self.assertNotIn("hostEnv", service)
        self.assertNotIn("target", check)
        self.assertNotIn("payload", check)
        self.assertNotIn("error", check)
        self.assertEqual(public["diagnostics"][0]["details"], "")
        self.assertEqual(
            public["capabilities"]["voiceLocal"]["repairActions"][0][
                "manualCommand"
            ],
            "start_local.bat --background",
        )
        serialized = str(public)
        self.assertNotIn("Private Headphones", serialized)
        self.assertNotIn("PrivateProbeError", serialized)
        self.assertNotIn("/app/runtime_artifacts", serialized)
        self.assertNotIn("C:\\private", serialized)
        self.assertNotIn("Private device label", serialized)
        self.assertNotIn("privatePayload", serialized)
        self.assertNotIn("7777", serialized)
        forbidden_keys = {
            "defaultHost",
            "error",
            "host",
            "hostEnv",
            "payload",
            "portEnv",
            "target",
        }

        def assert_closed(value: Any) -> None:
            if isinstance(value, dict):
                self.assertFalse(forbidden_keys.intersection(value))
                for child in value.values():
                    assert_closed(child)
            elif isinstance(value, list):
                for child in value:
                    assert_closed(child)

        assert_closed(public)
        self.assertEqual(
            public["observability"]["exceptions"]["sources"][
                "localBridge"
            ]["label"],
            "Local I/O Bridge",
        )
        self.assertEqual(
            public["observability"]["exceptions"]["sources"][
                "fastControlContinuity"
            ]["label"],
            "Fast Control Continuity",
        )
        projected_sources = public["observability"]["exceptions"]["sources"]
        self.assertEqual(
            {
                source_id: projected_sources[source_id]["label"]
                for source_id in health_only_sources
            },
            health_only_sources,
        )
        self.assertFalse(public["privacy"]["rawProbePayloads"])
        self.assertFalse(public["privacy"]["filesystemPaths"])

    async def test_public_projection_preserves_computed_readiness(self) -> None:
        manifest = load_service_manifest(force=True)
        health = await collect_runtime_health(
            manifest=manifest,
            probe_runner=fake_probe({}),
        )

        public = public_runtime_health_snapshot(health)
        self.assertFalse(public["legacyServices"]["codexRequired"])
        self.assertEqual(public["legacyServices"]["codexBackend"], "local")
        voyager = next(
            service
            for service in public["services"]
            if service["id"] == "voyager"
        )

        self.assertTrue(voyager["runtimeReady"])
        self.assertEqual(
            voyager["functionalReadiness"]["state"],
            "ready",
        )
        self.assertTrue(
            voyager["functionalReadiness"]["dependencies"][
                "minecraftConnected"
            ]
        )
        for service in public["services"]:
            for check in service["checks"]:
                self.assertNotIn("payload", check)
                self.assertNotIn("target", check)

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

    def test_public_health_keeps_only_content_free_stall_metrics(self) -> None:
        raw = {
            "observability": {
                "exceptions": {
                    "summary": {},
                    "sources": {
                        "conversationContinuity": {
                            "id": "conversationContinuity",
                            "state": "degraded",
                            "available": True,
                            "stale": False,
                            "errorCount": 0,
                            "hasCurrentError": False,
                            "completedTurnCommit": {
                                "schema": (
                                    "conversation_continuity."
                                    "commit-metrics.v1"
                                ),
                                "state": "warning",
                                "inFlight": True,
                                "inFlightCount": 1,
                                "stallAgeMs": 800.0,
                                "stalled": True,
                                "artifactDeadlineMs": 500.0,
                                "warningCode": (
                                    "conversation_continuity_commit_stalled"
                                ),
                                "privateText": "hidden request",
                            },
                        }
                    },
                    "recentErrors": [],
                    "warnings": [],
                }
            }
        }

        public = public_runtime_health_snapshot(raw)
        metrics = public["observability"]["exceptions"]["sources"][
            "conversationContinuity"
        ]["completedTurnCommit"]

        self.assertTrue(metrics["inFlight"])
        self.assertEqual(metrics["inFlightCount"], 1)
        self.assertEqual(metrics["stallAgeMs"], 800.0)
        self.assertTrue(metrics["stalled"])
        self.assertEqual(metrics["artifactDeadlineMs"], 500.0)
        self.assertNotIn("privateText", json.dumps(public))

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
        self.assertFalse(health["legacyServices"]["codexRequired"])
        self.assertEqual(health["legacyServices"]["codexBackend"], "local")
        self.assertNotIn("codex_gateway", {item["id"] for item in health["services"]})
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
        with patch.dict(os.environ, {"VOYAGER_ACTION_BACKEND": "codex-gateway"}):
            health = await collect_runtime_health(manifest=manifest, probe_runner=fake_probe({"codex_gateway": "action_failed"}))
        services = {service["id"]: service for service in health["services"]}
        codes = {diagnostic["code"] for diagnostic in health["diagnostics"]}

        self.assertTrue(services["codex_gateway"]["ready"])
        self.assertIn("CODEX_GATEWAY_ACTION_FAILED", codes)

    async def test_mindcraft_codex_opt_in_requires_the_gateway(self) -> None:
        manifest = load_service_manifest(force=True)
        with patch.dict(
            os.environ,
            {
                "VOYAGER_ACTION_BACKEND": "local",
                "MINDCRAFT_CODEX_ENABLED": "true",
            },
        ):
            health = await collect_runtime_health(
                manifest=manifest,
                probe_runner=fake_probe({"codex_gateway": "down"}),
            )
        services = {service["id"]: service for service in health["services"]}

        self.assertIn("codex_gateway", services)
        self.assertEqual(services["codex_gateway"]["state"], "down")
        self.assertTrue(health["legacyServices"]["codexRequired"])

    async def test_optional_voyager_stack_failures_are_warning_diagnostics(self) -> None:
        manifest = load_service_manifest(force=True)
        with patch.dict(os.environ, {"VOYAGER_ACTION_BACKEND": "codex-gateway"}):
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
        self.assertEqual(diagnostics["CODEX_GATEWAY_DOWN"]["suggestedActions"], [])

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

    async def test_mindcraft_http_without_readiness_contract_fails_closed(
        self,
    ) -> None:
        manifest = load_service_manifest(force=True)
        health = await collect_runtime_health(
            manifest=manifest,
            probe_runner=fake_probe(
                {"voyager": "mindcraft_http_only"}
            ),
        )
        services = {
            service["id"]: service
            for service in health["services"]
        }
        diagnostics = {
            diagnostic["code"]: diagnostic
            for diagnostic in health["diagnostics"]
        }

        voyager = services["voyager"]
        self.assertTrue(voyager["httpReady"])
        self.assertFalse(voyager["runtimeReady"])
        self.assertFalse(voyager["ready"])
        self.assertEqual(
            voyager["readinessContractState"],
            "missing",
        )
        self.assertEqual(
            voyager["readinessBlockers"],
            ["readiness_contract_missing"],
        )
        self.assertEqual(health["overallState"], "degraded")
        self.assertIn(
            "VOYAGER_RUNTIME_RECOVERY_REQUIRED",
            diagnostics,
        )

    async def test_mindcraft_readiness_recomputes_fixed_blockers(
        self,
    ) -> None:
        manifest = load_service_manifest(force=True)
        health = await collect_runtime_health(
            manifest=manifest,
            probe_runner=fake_probe(
                {"voyager": "mindcraft_blocked"}
            ),
        )
        voyager = next(
            service
            for service in health["services"]
            if service["id"] == "voyager"
        )

        self.assertFalse(voyager["runtimeReady"])
        self.assertEqual(
            voyager["readinessContractState"],
            "valid",
        )
        self.assertEqual(
            voyager["readinessBlockers"],
            ["minecraft_not_connected"],
        )
        self.assertEqual(
            voyager["functionalReadiness"]["state"],
            "starting",
        )

    async def test_mindcraft_inconsistent_readiness_is_invalid(
        self,
    ) -> None:
        manifest = load_service_manifest(force=True)
        health = await collect_runtime_health(
            manifest=manifest,
            probe_runner=fake_probe(
                {"voyager": "mindcraft_inconsistent"}
            ),
        )
        voyager = next(
            service
            for service in health["services"]
            if service["id"] == "voyager"
        )

        self.assertFalse(voyager["runtimeReady"])
        self.assertEqual(
            voyager["readinessContractState"],
            "invalid",
        )
        self.assertEqual(
            voyager["readinessBlockers"],
            ["readiness_contract_invalid"],
        )


if __name__ == "__main__":
    unittest.main()
