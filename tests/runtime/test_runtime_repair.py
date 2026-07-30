from __future__ import annotations

import asyncio
import json
import sys
import tempfile
import unittest
from pathlib import Path
from typing import Any
from unittest.mock import patch


REPO_ROOT = next(path for path in Path(__file__).resolve().parents if (path / "main.py").exists())
RUNTIME_ROOT = REPO_ROOT / "evelyn_core" / "runtime"
if str(RUNTIME_ROOT) not in sys.path:
    sys.path.insert(0, str(RUNTIME_ROOT))

from evelyn_core.runtime_health import collect_runtime_health  # noqa: E402
from evelyn_core import runtime_repair as runtime_repair_module  # noqa: E402
from evelyn_core.runtime_repair import (  # noqa: E402
    append_repair_event,
    build_runtime_repair_plan,
    execute_runtime_repair_plan,
    runtime_repair_capabilities,
)
from evelyn_core.runtime_services import HealthProbeSpec, ServiceSpec, load_service_manifest  # noqa: E402


def fake_probe(states: dict[str, str]):
    async def runner(service: ServiceSpec, check: HealthProbeSpec) -> dict[str, Any]:
        state = states.get(service.id, "up")
        if state == "down":
            return {"kind": check.kind, "ok": False, "reason": "connection_failed"}
        return {"kind": check.kind, "ok": True, "reason": "ok", "status": 200 if check.kind == "http" else None}

    return runner


class RuntimeRepairTests(unittest.TestCase):
    def setUp(self) -> None:
        self.direct_windows_patch = patch.object(
            runtime_repair_module,
            "direct_windows_repair_enabled",
            return_value=True,
        )
        self.direct_windows_patch.start()
        self.addCleanup(self.direct_windows_patch.stop)

    def health(self, states: dict[str, str]) -> dict[str, Any]:
        manifest = load_service_manifest(force=True)
        return asyncio.run(collect_runtime_health(manifest=manifest, probe_runner=fake_probe(states)))

    def test_capabilities_include_allowed_services_only(self) -> None:
        manifest = load_service_manifest(force=True)
        payload = runtime_repair_capabilities(manifest=manifest, health=self.health({"bot_api": "down"}))
        service_ids = {item["id"] for item in payload["repairableServices"]}

        self.assertFalse(payload["dryRunOnly"])
        self.assertTrue(payload["executionSupported"])
        self.assertIn("bot_api", service_ids)
        self.assertIn("main_llm", service_ids)
        self.assertIn("tts", service_ids)
        self.assertIn("voyager", service_ids)
        self.assertIn("codex_gateway", service_ids)

    def test_down_allowed_service_returns_dry_run_command_preview(self) -> None:
        manifest = load_service_manifest(force=True)
        plan = build_runtime_repair_plan(service_id="bot_api", manifest=manifest, health=self.health({"bot_api": "down"}))

        self.assertTrue(plan["ok"])
        self.assertTrue(plan["eligible"])
        self.assertTrue(plan["dryRun"])
        self.assertTrue(plan["dryRunOnly"])
        self.assertTrue(plan["cooldownOk"])
        self.assertEqual(plan["planStatus"], "ready")
        self.assertEqual(plan["serviceId"], "bot_api")
        self.assertEqual(plan["actionId"], "start_bot_api")
        self.assertEqual(plan["commandPreview"][0], "powershell.exe")
        self.assertIn("powershell.exe", plan["commandText"])
        self.assertIn("preview_only", {item["id"] for item in plan["riskChecks"]})
        self.assertIn("would start Bot API", plan["inferredSideEffects"])
        self.assertIn("start_bot.ps1", plan["launcherPath"])
        self.assertFalse(plan["safety"]["willExecute"])
        self.assertRegex(plan["confirmToken"], r"^confirm-[0-9a-f]{16}$")

    def test_plan_includes_blocking_required_services_and_recommended_order(self) -> None:
        manifest = load_service_manifest(force=True)
        plan = build_runtime_repair_plan(
            service_id="bot_api",
            manifest=manifest,
            health=self.health({"bot_api": "down", "main_llm": "down"}),
        )

        self.assertTrue(plan["ok"])
        self.assertTrue(plan["dryRun"])
        self.assertTrue(plan["runtimeHealthSummary"])
        self.assertIsInstance(plan["blockingServices"], list)
        self.assertIsInstance(plan["recommendedOrder"], list)
        self.assertEqual(plan["blockingServices"][0]["serviceId"], "main_llm")
        self.assertEqual(plan["recommendedOrder"][0]["serviceId"], "main_llm")
        self.assertEqual(plan["recommendedOrder"][1]["serviceId"], "bot_api")

    def test_up_service_is_not_needed(self) -> None:
        manifest = load_service_manifest(force=True)
        plan = build_runtime_repair_plan(service_id="tts", manifest=manifest, health=self.health({}))

        self.assertTrue(plan["ok"])
        self.assertFalse(plan["eligible"])
        self.assertEqual(plan["planStatus"], "not_needed")

    def test_explicit_local_bridge_restart_is_eligible_while_up(self) -> None:
        manifest = load_service_manifest(force=True)
        health = self.health({})
        with patch.object(
            runtime_repair_module,
            "HostSupervisorClient",
        ) as client:
            client.return_value.status.return_value = {"available": True}
            client.return_value.preview.return_value = {
                "ok": True,
                "previewToken": "restart-token",
                "expiresAt": 1120.0,
            }
            plan = build_runtime_repair_plan(
                service_id="local_io_bridge",
                action_id="restart_local_bridge",
                manifest=manifest,
                health=health,
            )

        self.assertTrue(plan["ok"])
        self.assertTrue(plan["eligible"])
        self.assertEqual(plan["planStatus"], "ready")
        self.assertEqual(plan["executionMode"], "host_supervisor")
        self.assertEqual(plan["confirmToken"], "restart-token")
        client.return_value.preview.assert_called_once_with("restart_local_bridge")

    def test_optional_voyager_stack_services_return_dry_run_command_preview(self) -> None:
        manifest = load_service_manifest(force=True)
        voyager = build_runtime_repair_plan(service_id="voyager", manifest=manifest, health=self.health({"voyager": "down"}))
        codex = build_runtime_repair_plan(
            service_id="codex_gateway",
            manifest=manifest,
            health=self.health({"codex_gateway": "down"}),
        )

        self.assertTrue(voyager["ok"])
        self.assertTrue(voyager["eligible"])
        self.assertEqual(voyager["planStatus"], "ready")
        self.assertEqual(voyager["serviceId"], "voyager")
        self.assertIn("start_voyager_service.ps1", voyager["launcherPath"])
        self.assertFalse(voyager["required"])
        self.assertFalse(voyager["safety"]["willExecute"])

        self.assertTrue(codex["ok"])
        self.assertTrue(codex["eligible"])
        self.assertEqual(codex["planStatus"], "ready")
        self.assertEqual(codex["serviceId"], "codex_gateway")
        self.assertIn("start_codex_gateway.ps1", codex["launcherPath"])
        self.assertFalse(codex["required"])
        self.assertFalse(codex["safety"]["willExecute"])

    def test_unknown_and_unsupported_actions_are_rejected(self) -> None:
        manifest = load_service_manifest(force=True)
        unknown = build_runtime_repair_plan(action_id="start_missing", manifest=manifest, health=self.health({}))
        unsupported = build_runtime_repair_plan(service_id="bot_api", action_id="restart_bot_api", manifest=manifest, health=self.health({"bot_api": "down"}))

        self.assertFalse(unknown["ok"])
        self.assertEqual(unknown["error"], "unknown_service")
        self.assertFalse(unsupported["ok"])
        self.assertEqual(unsupported["error"], "unsupported_repair_action")

    def test_health_suggested_action_maps_to_preview_action(self) -> None:
        manifest = load_service_manifest(force=True)
        health = self.health({"bot_api": "down"})
        bot = next(item for item in health["services"] if item["id"] == "bot_api")
        action_id = bot["suggestedActions"][0]["id"]
        plan = build_runtime_repair_plan(action_id=action_id, manifest=manifest, health=health)

        self.assertEqual(action_id, "start_bot_api")
        self.assertTrue(plan["ok"])
        self.assertEqual(plan["serviceId"], "bot_api")

    def test_apply_requires_preview_confirm_token(self) -> None:
        manifest = load_service_manifest(force=True)
        plan = build_runtime_repair_plan(service_id="bot_api", manifest=manifest, health=self.health({"bot_api": "down"}))
        response = execute_runtime_repair_plan(
            plan=plan,
            reason="operator clicked apply",
            confirm_token="wrong-token",
        )

        self.assertFalse(response["ok"])
        self.assertEqual(response["error"], "confirm_token_required")
        self.assertEqual(response["serviceId"], "bot_api")
        self.assertFalse(response["safety"]["willExecute"])

    def test_apply_executes_with_fake_runner_after_confirmation(self) -> None:
        manifest = load_service_manifest(force=True)
        plan = build_runtime_repair_plan(service_id="bot_api", manifest=manifest, health=self.health({"bot_api": "down"}))
        calls: list[tuple[list[str], str]] = []

        def fake_runner(command: list[str], cwd: str) -> dict[str, Any]:
            calls.append((command, cwd))
            return {"pid": 12345, "fake": True}

        with tempfile.TemporaryDirectory() as tmp:
            response = execute_runtime_repair_plan(
                plan=plan,
                confirm_token=plan["confirmToken"],
                reason="operator confirmed",
                runner=fake_runner,
                log_path=Path(tmp) / "repair_log.jsonl",
            )

        self.assertTrue(response["ok"])
        self.assertEqual(response["status"], "started")
        self.assertEqual(response["serviceId"], "bot_api")
        self.assertEqual(response["runner"]["pid"], 12345)
        self.assertTrue(response["safety"]["willExecute"])
        self.assertEqual(len(calls), 1)
        self.assertEqual(calls[0][0][0], "powershell.exe")

    def test_apply_cooldown_blocks_repeated_start(self) -> None:
        manifest = load_service_manifest(force=True)
        plan = build_runtime_repair_plan(service_id="bot_api", manifest=manifest, health=self.health({"bot_api": "down"}))
        calls = 0

        def fake_runner(command: list[str], cwd: str) -> dict[str, Any]:
            nonlocal calls
            calls += 1
            return {"pid": 12345}

        with tempfile.TemporaryDirectory() as tmp:
            log_path = Path(tmp) / "repair_log.jsonl"
            first = execute_runtime_repair_plan(
                plan=plan,
                confirm_token=plan["confirmToken"],
                runner=fake_runner,
                log_path=log_path,
                now_ts=1000.0,
            )
            second = execute_runtime_repair_plan(
                plan=plan,
                confirm_token=plan["confirmToken"],
                runner=fake_runner,
                log_path=log_path,
                now_ts=1001.0,
            )

        self.assertTrue(first["ok"])
        self.assertFalse(second["ok"])
        self.assertEqual(second["error"], "repair_cooldown_active")
        self.assertEqual(calls, 1)

    def test_simulated_down_service_is_preview_only(self) -> None:
        manifest = load_service_manifest(force=True)
        health = self.health({"vision": "down"})
        for service in health["services"]:
            if service["id"] == "vision":
                service["simulated"] = True
        plan = build_runtime_repair_plan(service_id="vision", manifest=manifest, health=health)

        self.assertTrue(plan["ok"])
        self.assertFalse(plan["eligible"])
        self.assertEqual(plan["planStatus"], "simulated_only")
        self.assertNotIn("confirmToken", plan)
        self.assertFalse(plan["safety"]["willExecute"])
        self.assertTrue(plan["safety"]["simulated"])

    def test_repair_event_log_is_jsonl(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            log_path = Path(tmp) / "repair_log.jsonl"
            result = append_repair_event(
                {"event": "apply_blocked", "serviceId": "bot_api", "actionId": "start_bot_api"},
                log_path=log_path,
            )

            self.assertTrue(result["ok"])
            self.assertTrue(log_path.exists())
            row = json.loads(log_path.read_text(encoding="utf-8").strip())
            self.assertEqual(row["event"], "apply_blocked")
            self.assertEqual(row["serviceId"], "bot_api")
            self.assertEqual(row["actionId"], "start_bot_api")
            self.assertIn("at", row)

    def test_docker_control_page_reports_supervisor_unavailable(self) -> None:
        manifest = load_service_manifest(force=True)
        health = self.health({"tts": "down"})
        with patch.object(
            runtime_repair_module,
            "direct_windows_repair_enabled",
            return_value=False,
        ), patch.object(
            runtime_repair_module,
            "HostSupervisorClient",
        ) as client:
            client.return_value.status.return_value = {
                "available": False,
                "error": "host_supervisor_unavailable",
                "manualCommand": "start_local.bat --background",
            }
            plan = build_runtime_repair_plan(
                service_id="tts",
                manifest=manifest,
                health=health,
            )

        self.assertFalse(plan["ok"])
        self.assertEqual(plan["error"], "host_supervisor_unavailable")
        self.assertEqual(plan["manualCommand"], "start_local.bat --background")

    def test_docker_control_page_preview_and_apply_use_supervisor_token(self) -> None:
        manifest = load_service_manifest(force=True)
        health = self.health({"tts": "down"})
        with tempfile.TemporaryDirectory() as tmp:
            log_path = Path(tmp) / "repair.jsonl"
            with patch.object(
                runtime_repair_module,
                "direct_windows_repair_enabled",
                return_value=False,
            ), patch.object(
                runtime_repair_module,
                "HostSupervisorClient",
            ) as client:
                client.return_value.status.return_value = {"available": True}
                client.return_value.preview.return_value = {
                    "ok": True,
                    "previewToken": "supervisor-token",
                    "expiresAt": 1120.0,
                }
                client.return_value.apply.return_value = {
                    "ok": True,
                    "status": "started",
                }
                plan = build_runtime_repair_plan(
                    service_id="tts",
                    manifest=manifest,
                    health=health,
                )
                response = execute_runtime_repair_plan(
                    plan=plan,
                    confirm_token=plan["confirmToken"],
                    log_path=log_path,
                )

        self.assertEqual(plan["executionMode"], "host_supervisor")
        self.assertEqual(plan["confirmToken"], "supervisor-token")
        client.return_value.apply.assert_called_once_with(
            "start_tts",
            "supervisor-token",
        )
        self.assertTrue(response["ok"])


if __name__ == "__main__":
    unittest.main()
