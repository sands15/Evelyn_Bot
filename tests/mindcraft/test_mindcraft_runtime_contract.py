from __future__ import annotations

import json
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock, patch


REPO_ROOT = next(path for path in Path(__file__).resolve().parents if (path / "main.py").exists())
RUNTIME_ROOT = REPO_ROOT / "evelyn_core" / "runtime"
if str(RUNTIME_ROOT) not in sys.path:
    sys.path.insert(0, str(RUNTIME_ROOT))

from evelyn_core import mindcraft_service  # noqa: E402


class MindcraftRuntimeContractTests(unittest.TestCase):
    def test_default_goal_targets_the_ender_dragon_with_survival_prerequisites(self) -> None:
        goal = mindcraft_service.DEFAULT_GOAL
        self.assertIn("Defeat the Ender Dragon", goal)
        self.assertIn("Nether access", goal)
        self.assertIn("Eyes of Ender", goal)
        self.assertIn("stronghold", goal)
        self.assertIn("Never use slash commands", goal)

    def test_settings_enforce_non_operator_survival(self) -> None:
        runtime = mindcraft_service.MindcraftRuntime()

        settings = runtime._settings("survive normally")

        self.assertEqual(settings["base_profile"], "survival")
        self.assertFalse(settings["allow_insecure_coding"])
        self.assertFalse(settings["allow_vision"])
        self.assertFalse(settings["render_bot_view"])
        self.assertFalse(settings["chat_ingame"])
        self.assertFalse(settings["narrate_behavior"])
        self.assertEqual(settings["max_commands"], -1)
        self.assertIn("!newAction", settings["blocked_actions"])
        self.assertIn("!setMode", settings["blocked_actions"])
        self.assertNotIn("!attack", settings["blocked_actions"])
        self.assertIn("!attackPlayer", settings["blocked_actions"])
        self.assertIn("!digDown", settings["blocked_actions"])

    def test_status_uses_fresh_mindcraft_telemetry(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            status_path = root / "status.json"
            goal_path = root / "goal.json"
            status_path.write_text(
                json.dumps(
                    {
                        "runtime": "mindcraft",
                        "connected": True,
                        "connection_state": "connected",
                        "phase": "survival",
                        "position": {"x": 1, "y": 64, "z": 2},
                        "health": 20,
                        "hunger": 18,
                        "inventory": {"oak_log": 4},
                        "hostiles_nearby": [],
                        "goal_manager": {
                            "mode": "shadow",
                            "current_subgoal": {
                                "id": "obtain_logs",
                                "kind": "obtain",
                                "target": "#logs",
                            },
                        },
                        "updated_at": mindcraft_service.time.time(),
                    }
                ),
                encoding="utf-8",
            )
            goal_path.write_text(json.dumps({"goal": "collect wood"}), encoding="utf-8")
            runtime = mindcraft_service.MindcraftRuntime()
            runtime._process = Mock()
            runtime._process.poll.return_value = None

            with (
                patch.object(mindcraft_service, "STATUS_PATH", status_path),
                patch.object(mindcraft_service, "GOAL_STATE_PATH", goal_path),
            ):
                payload = runtime.build_status()

        self.assertEqual(payload["runtime"], "mindcraft")
        self.assertTrue(payload["running"])
        self.assertTrue(payload["connected"])
        self.assertEqual(payload["command_policy"], "outbound_chat_disabled_by_default")
        self.assertEqual(payload["observation"]["inventory"]["oak_log"], 4)
        self.assertEqual(payload["current_subgoal"]["id"], "obtain_logs")
        self.assertEqual(payload["current_task_stage"], "obtain_logs")
        self.assertEqual(payload["configuration"]["schema"], "runtime_config.owner.v1")
        self.assertEqual(payload["errorCount"], 0)

    def test_stop_failure_is_counted_without_storing_exception_text(self) -> None:
        runtime = mindcraft_service.MindcraftRuntime()
        runtime._process = Mock()
        runtime._process.poll.return_value = None
        runtime._process.terminate.side_effect = OSError("private host path")

        with self.assertRaises(OSError):
            runtime.stop()

        snapshot = runtime.runtime_errors.snapshot()
        self.assertEqual(snapshot["errorCount"], 1)
        self.assertEqual(snapshot["lastErrorCode"], "mindcraft_stop_failed")
        self.assertEqual(snapshot["lastErrorType"], "OSError")
        self.assertNotIn("private host path", json.dumps(snapshot))

    def test_overlay_blocks_slash_commands_and_uses_profile_cache(self) -> None:
        runtime_source = (
            REPO_ROOT / "external" / "mindcraft_evelyn" / "src" / "utils" / "evelyn_runtime.js"
        ).read_text(encoding="utf-8")
        overlay_patch = (REPO_ROOT / "external" / "mindcraft_evelyn" / "evelyn.patch").read_text(
            encoding="utf-8"
        )

        self.assertIn("startsWith('/')", runtime_source)
        self.assertIn("MINDCRAFT_ALLOW_OUTBOUND_CHAT", runtime_source)
        self.assertIn("blocked outbound chat", runtime_source)
        self.assertIn("blocked outbound whisper", runtime_source)
        self.assertIn("bot.once('inject_allowed', installChatGuard)", runtime_source)
        self.assertIn("survival_controller", runtime_source)
        self.assertIn("goal_manager", runtime_source)
        self.assertIn("MINEFLAYER_PROFILES_FOLDER", overlay_patch)
        self.assertIn("process.env.MINECRAFT_USERNAME", overlay_patch)
        self.assertIn("self_prompter.start(process.env.MINDCRAFT_GOAL || init_message)", overlay_patch)
        self.assertIn("init_message && !save_data?.self_prompt", overlay_patch)
        self.assertIn("createEvelynSurvivalMode", overlay_patch)
        self.assertIn("minimumY", overlay_patch)
        self.assertIn("EvelynGoalManager", overlay_patch)
        self.assertIn("prepareForPrompt", overlay_patch)
        self.assertIn("gateCommand", overlay_patch)
        self.assertIn("handleMessage('system', msg, 1)", overlay_patch)

        profile = json.loads(
            (REPO_ROOT / "external" / "mindcraft_evelyn" / "profiles" / "evelyn.json").read_text(
                encoding="utf-8"
            )
        )
        self.assertEqual(
            profile["model"],
            {"api": "evelyn-planner", "model": "Qwen3-14B-Q4_K_M.gguf"},
        )
        self.assertEqual(
            profile["code_model"],
            {"api": "codex-gateway", "model": "gpt-5.5"},
        )
        self.assertEqual(profile["embedding"], {"api": "codex-gateway", "model": "hash-v1"})
        self.assertTrue(profile["modes"]["evelyn_survival"])
        self.assertFalse(profile["modes"]["cowardice"])
        self.assertFalse(profile["modes"]["self_defense"])

        survival_source = (
            REPO_ROOT
            / "external"
            / "mindcraft_evelyn"
            / "src"
            / "agent"
            / "evelyn_survival_mode.js"
        ).read_text(encoding="utf-8")
        self.assertIn("escape_to_surface", survival_source)
        self.assertIn("acquire_food", survival_source)
        self.assertIn("bootstrap_tools", survival_source)
        self.assertIn("maxDropDown = 1", survival_source)
        self.assertIn("handle_hostile", survival_source)
        self.assertIn("escapeFromHostiles", survival_source)
        self.assertIn("fightWithCustomPvp", survival_source)
        self.assertIn("custom_pvp_unavailable", survival_source)
        self.assertIn("verifyHostileOutcome", survival_source)
        self.assertNotIn("function fleeHostile", survival_source)

        combat_source = (
            REPO_ROOT
            / "external"
            / "mindcraft_evelyn"
            / "src"
            / "agent"
            / "evelyn_combat.js"
        ).read_text(encoding="utf-8")
        package = json.loads(
            (REPO_ROOT / "external" / "mindcraft_evelyn" / "package.json").read_text(
                encoding="utf-8"
            )
        )
        combat_patch = (
            REPO_ROOT / "external" / "mindcraft_evelyn" / "combat.patch"
        ).read_text(encoding="utf-8")
        dockerfile = (REPO_ROOT / "docker" / "Dockerfile.mindcraft").read_text(encoding="utf-8")
        escape_source = (
            REPO_ROOT
            / "external"
            / "mindcraft_evelyn"
            / "src"
            / "agent"
            / "evelyn_escape_controller.js"
        ).read_text(encoding="utf-8")

        self.assertIn("assessCombat", combat_source)
        self.assertIn("fightWithCustomPvp", combat_source)
        self.assertIn("selectCombatTarget", combat_source)
        self.assertEqual(package["dependencies"]["@nxg-org/mineflayer-custom-pvp"], "1.7.16")
        self.assertIn("bot.loadPlugin(customPvp)", combat_patch)
        self.assertIn("-            await attackEntity(bot, enemy, false)", combat_patch)
        self.assertIn("evelyn_combat.js", dockerfile)
        self.assertIn("evelyn_escape_controller.js", dockerfile)
        self.assertIn("combat.patch", dockerfile)
        self.assertIn("buildEscapeCandidates", escape_source)
        self.assertIn("chooseEscapeCandidate", escape_source)
        self.assertIn("evelynMovementOwner", escape_source)

    def test_codex_gateway_adapter_uses_bearer_contract(self) -> None:
        source = (
            REPO_ROOT / "external" / "mindcraft_evelyn" / "src" / "models" / "codex_gateway.js"
        ).read_text(encoding="utf-8")

        self.assertIn("Authorization", source)
        self.assertIn("Bearer ${resolveToken()}", source)
        self.assertIn("mindcraft-planner", source)
        self.assertIn("gpt-5.5", source)

    def test_hybrid_planner_routes_between_local_qwen_and_codex(self) -> None:
        source = (
            REPO_ROOT / "external" / "mindcraft_evelyn" / "src" / "models" / "evelyn_planner.js"
        ).read_text(encoding="utf-8")

        self.assertIn("static prefix = 'evelyn-planner'", source)
        self.assertIn("http://router_llm:9822/v1/chat/completions", source)
        self.assertIn("http://minecraft_llm:9823/v1/chat/completions", source)
        self.assertIn("Qwen3-14B-Q4_K_M.gguf", source)
        self.assertIn("chat_template_kwargs: {enable_thinking: false}", source)
        self.assertIn("Planner output violated command policy", source)
        self.assertIn("analyzeRecentTurns", source)
        self.assertIn("same_failed_command_repeated", source)
        self.assertIn("invalid_registry_name", source)
        self.assertIn("mindcraft-recovery-plan", source)
        self.assertIn("gate=recovery", source)
        self.assertIn("lastObservedExecutionSequence", source)
        self.assertIn("FORBIDDEN_RECOVERY_COMMANDS", source)
        self.assertIn("readGoalPolicy", source)
        self.assertIn("Local planner failed; escalating to recovery", source)
        self.assertIn("proposeSubgoals", source)
        self.assertIn("parseSubgoalCandidates", source)
        self.assertIn("['BlockName', 'ItemName', 'BlockOrItemName']", source)

    def test_goal_manager_overlay_and_container_contract(self) -> None:
        manager_source = (
            REPO_ROOT
            / "external"
            / "mindcraft_evelyn"
            / "src"
            / "agent"
            / "evelyn_goal_manager.js"
        ).read_text(encoding="utf-8")
        dockerfile = (REPO_ROOT / "docker" / "Dockerfile.mindcraft").read_text(encoding="utf-8")
        compose = (REPO_ROOT / "docker-compose.fast-control.yml").read_text(encoding="utf-8")

        self.assertIn("prepareForPrompt", manager_source)
        self.assertIn("predicateSatisfied", manager_source)
        self.assertIn("action_budget_exhausted", manager_source)
        self.assertIn("goal_manager_owns_autonomous_goal_control", manager_source)
        self.assertIn("observation_budget_exhausted", manager_source)
        self.assertIn("relocation_required_before_action", manager_source)
        self.assertIn("autonomous loop stopped unexpectedly", manager_source)
        self.assertIn("MINDCRAFT_GOAL_MANAGER_MODE", manager_source)
        self.assertIn("evelyn_goal_manager.js", dockerfile)
        self.assertIn("evelyn_world_state.js", dockerfile)
        self.assertIn("connection_handler.js", dockerfile)
        self.assertIn('MINDCRAFT_GOAL_MANAGER_MODE: "gated"', compose)
        self.assertIn("goal_manager_state.json", compose)


if __name__ == "__main__":
    unittest.main()
