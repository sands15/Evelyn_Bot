from __future__ import annotations

import asyncio
import sys
import unittest
from dataclasses import fields
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock, call, patch

import discord
from discord.ext import commands


REPO_ROOT = next(path for path in Path(__file__).resolve().parents if (path / "main.py").exists())
RUNTIME_ROOT = REPO_ROOT / "evelyn_core" / "runtime"
if str(RUNTIME_ROOT) not in sys.path:
    sys.path.insert(0, str(RUNTIME_ROOT))

from evelyn_core.discord_app_composition_runtime import (  # noqa: E402
    DiscordAppComposition,
    DiscordAppCompositionDeps,
    DiscordCommandCompositionDeps,
    DiscordEventCompositionDeps,
    build_discord_intents,
)


def make_command_deps(**overrides) -> DiscordCommandCompositionDeps:
    values = {field.name: Mock(name=field.name) for field in fields(DiscordCommandCompositionDeps)}
    values.update(
        model_name="main",
        router_model_name="router",
        summary_model_name="summary",
        stt_model_name="stt",
        voice_debug_save_audio=False,
        vad_enabled=True,
        vad_provider="silero",
        default_command_prefix="!",
        guild_only_message=lambda: "guild only",
        autonomy_enabled=True,
        autonomy_engines={},
        command_session=lambda: object(),
        is_control_command_authorized=lambda _ctx: True,
    )
    values.update(overrides)
    return DiscordCommandCompositionDeps(**values)


class DiscordIntentsTests(unittest.TestCase):
    def test_build_discord_intents_enables_required_events(self) -> None:
        intents = build_discord_intents()

        self.assertTrue(intents.message_content)
        self.assertTrue(intents.guilds)
        self.assertTrue(intents.voice_states)
        self.assertTrue(intents.members)


def make_event_deps(**overrides) -> DiscordEventCompositionDeps:
    values = {field.name: Mock(name=field.name) for field in fields(DiscordEventCompositionDeps)}
    values.update(
        bot_user=lambda: SimpleNamespace(id=99),
        bot_guilds=lambda: [],
        clean_text=lambda text: text,
        start_control_page_server=AsyncMock(),
        ensure_startup_components_ready=AsyncMock(),
        ensure_local_mic_service_started=AsyncMock(),
        ensure_control_page_background_tasks_started=AsyncMock(),
        ensure_listening_voice_client=AsyncMock(),
        voice_rejoin_on_ready=False,
        restore_last_voice_channel=AsyncMock(return_value=(False, "no_saved_voice_channel")),
        autonomy_enabled=False,
        text_message_handler=lambda: object(),
        log=Mock(),
        recover_search_followups=None,
    )
    values.update(overrides)
    return DiscordEventCompositionDeps(**values)


def make_composition(*, events=None, commands_deps=None) -> DiscordAppComposition:
    return DiscordAppComposition(
        DiscordAppCompositionDeps(
            events=events or make_event_deps(),
            commands=commands_deps or make_command_deps(),
        )
    )


class DiscordAppCompositionTests(unittest.TestCase):
    def test_register_preserves_command_names_aliases_checks_and_errors(self) -> None:
        checker = lambda _ctx: True
        composition = make_composition(
            commands_deps=make_command_deps(is_control_command_authorized=checker)
        )
        bot = commands.Bot(command_prefix="!", intents=discord.Intents.none(), help_command=None)

        bindings = composition.register(bot)

        expected_commands = {
            "들어와": ["join"],
            "다시들어와": ["rejoin"],
            "나가": ["leave"],
            "재시작": ["restart"],
            "종료": ["shutdown", "quit", "exit"],
            "상태": ["status"],
            "이블린페이지": ["page", "homepage", "website", "landing"],
            "접두사": ["prefix"],
            "자율시작": ["autonomy-on"],
            "자율정지": ["autonomy-off"],
            "자율상태": ["autonomy-status"],
            "마크접속": ["mc-connect", "minecraft-connect"],
            "마크종료": ["mc-disconnect", "minecraft-disconnect"],
            "마크상태": ["mc-status", "minecraft-status"],
            "마크목표": ["mc-goal", "minecraft-goal"],
            "관찰채널": ["observe-channel"],
            "명령채널": ["command-channel"],
            "도움말": ["help"],
            "초기화": ["reset"],
        }
        self.assertEqual({command.name for command in bot.commands}, set(expected_commands))
        for name, aliases in expected_commands.items():
            self.assertEqual(bot.get_command(name).aliases, aliases)
        self.assertIs(bot.get_command("join"), bindings.join_voice)
        self.assertIs(bot.get_command("exit"), bindings.shutdown_bot_command)
        self.assertIs(bot.get_command("landing"), bindings.evelyn_page_command)
        self.assertIs(bot.get_command("minecraft-goal"), bindings.minecraft_goal_command)

        protected = {
            "재시작",
            "종료",
            "접두사",
            "자율시작",
            "자율정지",
            "마크접속",
            "마크종료",
            "마크목표",
            "관찰채널",
            "명령채널",
            "초기화",
        }
        for name in expected_commands:
            command = bot.get_command(name)
            self.assertEqual(command.checks, [checker] if name in protected else [])
            if name in protected:
                self.assertIs(command.on_error.__self__, composition)
                self.assertIs(command.on_error.__func__, composition.control_command_error.__func__)

        self.assertEqual(list(bot.get_command("접두사").clean_params), ["new_prefix"])
        self.assertEqual(list(bot.get_command("마크목표").clean_params), ["goal"])
        self.assertEqual(list(bot.get_command("관찰채널").clean_params), ["action", "channel"])
        self.assertIs(bot.on_ready.__self__, composition)
        self.assertIs(bot.on_message.__self__, composition)

    def test_command_replies_use_one_post_delivery_continuity_owner(
        self,
    ) -> None:
        command_deps = make_command_deps(
            get_guild_command_prefix=lambda _guild_id: "!",
            build_help_command_text=(
                lambda **_kwargs: "help reply"
            ),
            build_minecraft_goal_missing_reply=(
                lambda: "missing goal"
            ),
        )
        composition = make_composition(commands_deps=command_deps)
        recorder = Mock()
        composition.mark_text_session_from_command = recorder

        def context(content: str) -> SimpleNamespace:
            return SimpleNamespace(
                guild=SimpleNamespace(id=1),
                channel=SimpleNamespace(id=2),
                author=SimpleNamespace(id=3),
                message=SimpleNamespace(id=4, content=content),
                send=AsyncMock(return_value=SimpleNamespace(id=5)),
            )

        help_ctx = context("!도움말")
        minecraft_ctx = context("!마크목표")
        denied_ctx = context("!재시작")

        asyncio.run(composition.help_command(help_ctx))
        asyncio.run(
            composition.minecraft_goal_command(
                minecraft_ctx,
                goal="",
            )
        )
        asyncio.run(
            composition.control_command_error(
                denied_ctx,
                commands.CheckFailure(),
            )
        )

        self.assertEqual(
            recorder.call_args_list,
            [
                call(help_ctx, "!도움말", "help reply"),
                call(minecraft_ctx, "!마크목표", "missing goal"),
                call(
                    denied_ctx,
                    "!재시작",
                    "이 명령은 허용된 Discord ID이거나 "
                    "서버 관리자 권한이 있어야 쓸 수 있어.",
                ),
            ],
        )

    def test_on_ready_initializes_services_without_resuming_autonomy(self) -> None:
        class VoiceClient:
            channel = SimpleNamespace(name="General")

            @staticmethod
            def is_listening() -> bool:
                return True

        voice_client = VoiceClient()
        guild = SimpleNamespace(id=7, voice_client=voice_client)
        mark = Mock()
        rearm = AsyncMock()
        events = make_event_deps(
            bot_user=lambda: SimpleNamespace(id=99, __str__=lambda _self: "Evelyn"),
            bot_guilds=lambda: [guild],
            mark_startup_component=mark,
            voice_client_type=VoiceClient,
            ensure_listening_voice_client=rearm,
            autonomy_enabled=True,
        )

        asyncio.run(make_composition(events=events).on_ready())

        mark.assert_called_once()
        events.ensure_voice_worker_started.assert_called_once_with()
        events.start_control_page_server.assert_awaited_once_with()
        events.ensure_startup_components_ready.assert_awaited_once_with()
        events.ensure_local_mic_service_started.assert_awaited_once_with()
        events.ensure_vision_watch_started.assert_called_once_with()
        events.ensure_control_page_background_tasks_started.assert_awaited_once_with()
        rearm.assert_awaited_once_with(guild, voice_client.channel)
        events.log.assert_any_call(
            "[AUTONOMY] guild=7 available approval_required=true"
        )

    def test_on_ready_records_fixed_error_code_in_runtime_status(self) -> None:
        runtime_status = Mock()
        failure = RuntimeError("C:\\private\\token")
        events = make_event_deps(
            runtime_status=runtime_status,
            start_control_page_server=AsyncMock(side_effect=failure),
        )

        asyncio.run(make_composition(events=events).on_ready())

        runtime_status.record_error.assert_called_once_with(
            "control_page_start_failed",
            failure,
        )

    def test_on_ready_recovers_promised_search_followups(self) -> None:
        recover = AsyncMock(
            return_value={
                "pending": 1,
                "resumed": 1,
                "verified": 0,
                "redelivered": 0,
                "uncertain": 0,
            }
        )
        events = make_event_deps(
            recover_search_followups=recover,
        )

        asyncio.run(make_composition(events=events).on_ready())

        recover.assert_awaited_once_with()
        self.assertTrue(
            any(
                "recovery_complete" in str(call_args)
                for call_args in events.log.call_args_list
            )
        )

    def test_on_message_resolves_fresh_handler_dependencies(self) -> None:
        handler_deps = object()
        events = make_event_deps(text_message_handler=Mock(return_value=handler_deps))
        message = object()

        with patch(
            "evelyn_core.discord_app_composition_runtime.handle_discord_text_message",
            new=AsyncMock(),
        ) as handler:
            asyncio.run(make_composition(events=events).on_message(message))

        events.text_message_handler.assert_called_once_with()
        handler.assert_awaited_once_with(message, handler_deps)

    def test_main_uses_explicit_discord_app_composition_bindings(self) -> None:
        main_source = (REPO_ROOT / "main.py").read_text(encoding="utf-8")
        runtime_source = (
            RUNTIME_ROOT / "evelyn_core" / "discord_app_composition_runtime.py"
        ).read_text(encoding="utf-8")

        self.assertIn("discord_app_composition = DiscordAppComposition(", main_source)
        self.assertIn("discord_app_bindings = discord_app_composition.register(bot)", main_source)
        self.assertIn("on_ready = discord_app_bindings.on_ready", main_source)
        self.assertIn("reset_guild_memory = discord_app_bindings.reset_guild_memory", main_source)
        self.assertNotIn("@bot.event", main_source)
        self.assertNotIn("@bot.command", main_source)
        self.assertNotIn("globals()", runtime_source)


if __name__ == "__main__":
    unittest.main()
