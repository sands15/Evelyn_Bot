from __future__ import annotations

import sys
import unittest
from pathlib import Path


REPO_ROOT = next(path for path in Path(__file__).resolve().parents if (path / "main.py").exists())
RUNTIME_ROOT = REPO_ROOT / "evelyn_core" / "runtime"
if str(RUNTIME_ROOT) not in sys.path:
    sys.path.insert(0, str(RUNTIME_ROOT))

from evelyn_core.discord_commands import (  # noqa: E402
    build_autonomy_status_command_text,
    build_channel_setting_list_reply,
    build_help_command_text,
    build_minecraft_connect_reply,
    build_minecraft_goal_missing_reply,
    build_minecraft_goal_updated_reply,
    build_minecraft_status_command_text,
    build_prefix_current_reply,
    build_prefix_reset_reply,
    build_prefix_saved_reply,
    build_reset_guild_memory_reply,
    build_status_command_text,
    control_command_check_failure_message,
    guild_only_command_message,
    is_control_command_authorized_payload,
    normalize_channel_setting_action,
)


class DiscordCommandHelperTests(unittest.TestCase):
    def test_control_command_authorization_accepts_allowlist_or_admin(self) -> None:
        self.assertTrue(is_control_command_authorized_payload(author_id=7, is_administrator=False, allowed_user_ids={7}))
        self.assertTrue(is_control_command_authorized_payload(author_id=8, is_administrator=True, allowed_user_ids={7}))
        self.assertFalse(is_control_command_authorized_payload(author_id=8, is_administrator=False, allowed_user_ids={7}))
        self.assertEqual(control_command_check_failure_message(), "이 명령은 허용된 Discord ID이거나 서버 관리자 권한이 있어야 쓸 수 있어.")
        self.assertEqual(guild_only_command_message(), "이 명령은 길드에서만 쓸 수 있어.")

    def test_autonomy_status_text_summarizes_state(self) -> None:
        class Summary:
            def __init__(self, summary: str) -> None:
                self.summary = summary

        class State:
            status = "running"
            safety_mode = "normal"
            current_goal = Summary("explore")
            current_plan = Summary("look around")
            failure_count = 2
            last_error = ""
            allowed_actions = ["a", "b", "c", "d", "e", "f", "g"]

        text = build_autonomy_status_command_text(State(), minecraft_enabled=True)

        self.assertIn("- status: running", text)
        self.assertIn("- goal: explore", text)
        self.assertIn("- minecraft_autonomy: on", text)
        self.assertIn("- allowed: a, b, c, d, e, f, ...", text)

    def test_status_command_text_preserves_runtime_fields(self) -> None:
        text = build_status_command_text(
            model_name="main-model",
            router_model_name="router-model",
            summary_model_name="summary-model",
            stt_model_name="stt-model",
            voice_channel_name="General",
            listening=True,
            voice_debug_save_audio=False,
            opus_env_state=None,
            opus_runtime_value=True,
            vad_enabled=True,
            vad_provider="silero",
        )

        self.assertIn("모델: main-model", text)
        self.assertIn("음성채널: General", text)
        self.assertIn("리스닝: on", text)
        self.assertIn("디버그 오디오 저장: off", text)
        self.assertIn("OPUS_ERROR_TO_SILENCE(env): unset", text)
        self.assertIn("OPUS_ERROR_TO_SILENCE(runtime): True", text)
        self.assertIn("VAD: on (silero)", text)

    def test_prefix_replies_use_current_prefix(self) -> None:
        self.assertIn("`?접두사 ?`", build_prefix_current_reply("?"))
        self.assertEqual(build_prefix_reset_reply("!"), "✅ 명령어 시작 부호를 기본값 `!` 로 되돌렸어.")
        self.assertIn("`!초기화`", build_prefix_saved_reply("!"))

    def test_channel_setting_list_reply_resolves_mentions(self) -> None:
        class Channel:
            mention = "#general"

        reply = build_channel_setting_list_reply(
            label="👀 관찰채널",
            channel_ids=[1, 2],
            resolve_channel=lambda channel_id: Channel() if channel_id == 1 else None,
        )

        self.assertEqual(reply, "👀 관찰채널: #general, #2")
        self.assertEqual(normalize_channel_setting_action(None), "목록")
        self.assertEqual(normalize_channel_setting_action(" ADD "), "add")

    def test_help_text_includes_control_commands_only_when_authorized(self) -> None:
        regular = build_help_command_text(prefix="!", control_authorized=False)
        admin = build_help_command_text(prefix="!", control_authorized=True)

        self.assertIn("- !상태 / !접두사", regular)
        self.assertNotIn("!재시작", regular)
        self.assertIn("- !재시작 / !종료", admin)

    def test_minecraft_connect_reply_reports_success_or_last_error(self) -> None:
        success = build_minecraft_connect_reply(
            {
                "connected": True,
                "outcome_verified": True,
                "outcome_code": "minecraft_connected",
                "position": {"x": 1},
                "objective_stage": "wood",
                "objective_goal": "diamond",
            }
        )
        failure = build_minecraft_connect_reply({"wait_last_error": "timeout"})
        position_only = build_minecraft_connect_reply(
            {"position": {"x": 1}}
        )

        self.assertIn("Voyager 기반 마인크래프트 자율 모드 시작 완료", success)
        self.assertIn("- goal: diamond", success)
        self.assertIn("last_error=timeout", failure)
        self.assertIn("접속 실패", position_only)

    def test_minecraft_status_text_summarizes_voyager_evaluation(self) -> None:
        text = build_minecraft_status_command_text(
            {
                "running": True,
                "connected": True,
                "goal": "diamond",
                "stage": "mine",
                "current_task": "collect",
                "current_task_stage": "iron",
                "last_progress_message": "found cave",
                "world_lease": {
                    "state": "authorized",
                    "lease": {"expiresAt": 2000.0},
                },
                "observation": {"position": {"x": 1}, "hunger": 18, "health": 20, "hostiles_nearby": 2},
                "voyager_evaluation": {
                    "goal": "diamond_pickaxe",
                    "unique_item_count": 12,
                    "travel_distance_blocks": 345,
                    "tech_tree": {"highest_unlocked": "iron"},
                    "skill_library": {"size": 9},
                },
            }
        )

        self.assertIn("- running: on", text)
        self.assertIn("- world_lease: authorized", text)
        self.assertIn("- lease_expires_at: 2000.0", text)
        self.assertIn("- eval_goal: diamond_pickaxe", text)
        self.assertIn("- tech_tree: iron", text)
        self.assertIn("- skill_library: 9", text)
        self.assertIn("- hostiles: 2", text)

    def test_minecraft_goal_and_reset_replies(self) -> None:
        self.assertIn("마크목표 diamond", build_minecraft_goal_missing_reply())
        self.assertEqual(
            build_minecraft_goal_updated_reply(
                "diamond",
                {
                    "goal": "diamond",
                    "stage": "mine",
                    "outcome_verified": True,
                    "outcome_code": "minecraft_goal_confirmed",
                },
            ),
            "🎯 마인크래프트 목표를 바꿨어.\n- goal: diamond\n- stage: mine",
        )
        self.assertIn(
            "확인하지 못했어",
            build_minecraft_goal_updated_reply(
                "diamond",
                {"goal": "other", "stage": "mine"},
            ),
        )
        self.assertEqual(
            build_reset_guild_memory_reply(guild_name="Home", current_prefix="!"),
            "🧹 Home 메모리와 대화 히스토리를 이 길드만 초기화했어. 명령어 시작 부호 `!` 설정은 유지했어.",
        )


if __name__ == "__main__":
    unittest.main()
