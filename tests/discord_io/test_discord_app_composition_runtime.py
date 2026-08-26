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
from evelyn_core.autonomy import AutonomyEngine  # noqa: E402
from evelyn_core.discord_runtime_status import DiscordRuntimeStatus  # noqa: E402


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
        enable_minecraft_autonomy_route=AsyncMock(return_value=False),
        disable_minecraft_autonomy_route=AsyncMock(return_value=False),
        is_minecraft_autonomy_route_enabled=lambda _guild_id: False,
        command_session=lambda: object(),
        is_control_command_authorized=lambda _ctx: True,
        guild_is_open=lambda _guild_id: True,
        guild_epoch=lambda _guild_id: 0,
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
    def test_autonomy_start_same_guild_reset_during_route_fences_stale_start(
        self,
    ) -> None:
        async def scenario() -> tuple[AsyncMock, Mock, SimpleNamespace]:
            epochs = {7: 0}
            open_guilds = {7}
            route_started = asyncio.Event()
            route_release = asyncio.Event()
            engine = SimpleNamespace(
                stop=AsyncMock(),
                start=AsyncMock(),
            )
            grant = Mock(return_value={"ok": True})

            def guild_epoch(guild_id: int) -> int:
                if guild_id not in open_guilds:
                    raise RuntimeError("guild_reset_in_progress")
                return epochs.get(guild_id, 0)

            def reset_guild(guild_id: int) -> None:
                open_guilds.discard(guild_id)
                epochs[guild_id] = epochs.get(guild_id, 0) + 1
                open_guilds.add(guild_id)

            async def enable_route(_guild_id: int) -> bool:
                route_started.set()
                await route_release.wait()
                return True

            deps = make_command_deps(
                guild_is_open=lambda guild_id: guild_id in open_guilds,
                guild_epoch=guild_epoch,
                get_or_create_autonomy_engine=lambda _guild_id: engine,
                is_minecraft_autonomy_route_enabled=lambda _guild_id: True,
                enable_minecraft_autonomy_route=enable_route,
                grant_autonomy_authorization=grant,
                revoke_autonomy_authorization=Mock(),
                reset_guild_runtime_state=reset_guild,
                get_guild_command_prefix=lambda _guild_id: "!",
                build_reset_guild_memory_reply=lambda **_kwargs: "reset-ok",
            )
            composition = make_composition(commands_deps=deps)
            composition.mark_text_session_from_command = Mock()

            def context(content: str) -> SimpleNamespace:
                return SimpleNamespace(
                    guild=SimpleNamespace(id=7, name="Guild"),
                    author=SimpleNamespace(id=3),
                    channel=SimpleNamespace(id=2),
                    message=SimpleNamespace(id=4, content=content),
                    send=AsyncMock(return_value=SimpleNamespace(id=5)),
                )

            start_ctx = context("!자율시작")
            start_task = asyncio.create_task(
                composition.autonomy_start_command(start_ctx)
            )
            await asyncio.wait_for(route_started.wait(), timeout=1.0)
            reset_ctx = context("!초기화")
            await composition.reset_guild_memory(reset_ctx)
            route_release.set()
            await asyncio.wait_for(start_task, timeout=1.0)
            reset_ctx.send.assert_awaited_once_with("reset-ok")
            return engine.start, grant, start_ctx

        start, grant, start_ctx = asyncio.run(scenario())

        grant.assert_not_called()
        start.assert_not_awaited()
        start_ctx.send.assert_awaited_once_with(
            "길드 상태가 초기화되어 자율 행동 시작을 취소했어. 다시 요청해줘."
        )

    def test_autonomy_start_reset_during_executor_connect_fences_commit(
        self,
    ) -> None:
        async def scenario() -> tuple[
            AutonomyEngine,
            SimpleNamespace,
            Mock,
            Mock,
        ]:
            class BlockingExecutor:
                def __init__(inner_self) -> None:
                    inner_self.connect_started = asyncio.Event()
                    inner_self.connect_release = asyncio.Event()
                    inner_self.connected = False
                    inner_self.disconnect_count = 0

                async def connect(inner_self) -> None:
                    inner_self.connect_started.set()
                    await inner_self.connect_release.wait()
                    inner_self.connected = True

                async def disconnect(inner_self) -> None:
                    inner_self.disconnect_count += 1
                    inner_self.connected = False

                async def observe(inner_self) -> dict:
                    return {}

                async def execute_step(
                    inner_self,
                    _step: dict,
                    *,
                    context=None,
                ) -> dict:
                    del context
                    return {}

            epochs = {7: 0}
            open_guilds = {7}
            authorized_actions: list[str] = []
            executor = BlockingExecutor()
            engine = AutonomyEngine(
                guild_id=7,
                executor=executor,
                get_authorized_actions=lambda _guild_id: list(
                    authorized_actions
                ),
            )
            engine.load_persisted_state = lambda: None
            engine.persist_state = lambda: None

            def grant(_guild_id: int, _issuer: str, *, scopes) -> dict:
                authorized_actions[:] = scopes
                return {"ok": True}

            grant_mock = Mock(side_effect=grant)

            def revoke(_guild_id: int, *, reason_code: str) -> dict:
                authorized_actions.clear()
                return {"ok": True, "reason": reason_code}

            revoke_mock = Mock(side_effect=revoke)

            def guild_epoch(guild_id: int) -> int:
                if guild_id not in open_guilds:
                    raise RuntimeError("guild_reset_in_progress")
                return epochs.get(guild_id, 0)

            def reset_guild(guild_id: int) -> None:
                self.assertIsNone(engine._task)
                self.assertFalse(engine.state.enabled)
                self.assertEqual(engine.state.status, "idle")
                open_guilds.discard(guild_id)
                epochs[guild_id] = epochs.get(guild_id, 0) + 1
                engine.state.enabled = False
                engine.state.status = "idle"
                engine.state.safety_mode = "constrained"
                engine.state.allowed_actions = []
                open_guilds.add(guild_id)

            deps = make_command_deps(
                guild_is_open=lambda guild_id: guild_id in open_guilds,
                guild_epoch=guild_epoch,
                get_or_create_autonomy_engine=lambda _guild_id: engine,
                grant_autonomy_authorization=grant_mock,
                revoke_autonomy_authorization=revoke_mock,
                reset_guild_runtime_state=reset_guild,
                get_guild_command_prefix=lambda _guild_id: "!",
                build_reset_guild_memory_reply=lambda **_kwargs: "reset-ok",
            )
            composition = make_composition(commands_deps=deps)
            composition.mark_text_session_from_command = Mock()

            def command_context(content: str) -> SimpleNamespace:
                return SimpleNamespace(
                    guild=SimpleNamespace(id=7, name="Guild"),
                    author=SimpleNamespace(id=3),
                    channel=SimpleNamespace(id=2),
                    message=SimpleNamespace(id=4, content=content),
                    send=AsyncMock(return_value=SimpleNamespace(id=5)),
                )

            start_ctx = command_context("!자율시작")
            start_task = asyncio.create_task(
                composition.autonomy_start_command(start_ctx)
            )
            await asyncio.wait_for(
                executor.connect_started.wait(),
                timeout=1.0,
            )
            reset_ctx = command_context("!초기화")
            await composition.reset_guild_memory(reset_ctx)
            reset_ctx.send.assert_awaited_once_with("reset-ok")
            executor.connect_release.set()
            await asyncio.wait_for(start_task, timeout=1.0)
            return engine, start_ctx, grant_mock, revoke_mock

        engine, start_ctx, grant, revoke = asyncio.run(scenario())

        grant.assert_called_once()
        revoke.assert_called_once_with(7, reason_code="start_failed")
        self.assertFalse(engine.executor.connected)
        self.assertEqual(engine.executor.disconnect_count, 1)
        self.assertFalse(engine.state.enabled)
        self.assertEqual(engine.state.status, "idle")
        self.assertEqual(engine.state.safety_mode, "constrained")
        self.assertIsNone(engine._task)
        start_ctx.send.assert_awaited_once_with(
            "길드 상태가 초기화되어 자율 행동 시작을 취소했어. 다시 요청해줘."
        )

    def test_autonomy_start_other_guild_reset_does_not_stale_command(
        self,
    ) -> None:
        async def scenario() -> tuple[SimpleNamespace, Mock, SimpleNamespace]:
            epochs = {7: 0, 8: 0}
            open_guilds = {7, 8}
            route_started = asyncio.Event()
            route_release = asyncio.Event()
            engine = SimpleNamespace(
                stop=AsyncMock(),
                start=AsyncMock(return_value=True),
            )
            grant = Mock(return_value={"ok": True})

            def guild_epoch(guild_id: int) -> int:
                if guild_id not in open_guilds:
                    raise RuntimeError("guild_reset_in_progress")
                return epochs[guild_id]

            def reset_guild(guild_id: int) -> None:
                open_guilds.discard(guild_id)
                epochs[guild_id] += 1
                open_guilds.add(guild_id)

            async def enable_route(_guild_id: int) -> bool:
                route_started.set()
                await route_release.wait()
                return True

            deps = make_command_deps(
                guild_is_open=lambda guild_id: guild_id in open_guilds,
                guild_epoch=guild_epoch,
                get_or_create_autonomy_engine=lambda _guild_id: engine,
                is_minecraft_autonomy_route_enabled=lambda _guild_id: True,
                enable_minecraft_autonomy_route=enable_route,
                grant_autonomy_authorization=grant,
                revoke_autonomy_authorization=Mock(),
                reset_guild_runtime_state=reset_guild,
                get_guild_command_prefix=lambda _guild_id: "!",
                build_reset_guild_memory_reply=lambda **_kwargs: "reset-ok",
            )
            composition = make_composition(commands_deps=deps)
            composition.mark_text_session_from_command = Mock()

            def context(guild_id: int, content: str) -> SimpleNamespace:
                return SimpleNamespace(
                    guild=SimpleNamespace(id=guild_id, name="Guild"),
                    author=SimpleNamespace(id=3),
                    channel=SimpleNamespace(id=2),
                    message=SimpleNamespace(id=4, content=content),
                    send=AsyncMock(return_value=SimpleNamespace(id=5)),
                )

            start_ctx = context(7, "!자율시작")
            start_task = asyncio.create_task(
                composition.autonomy_start_command(start_ctx)
            )
            await asyncio.wait_for(route_started.wait(), timeout=1.0)
            await composition.reset_guild_memory(context(8, "!초기화"))
            route_release.set()
            await asyncio.wait_for(start_task, timeout=1.0)
            return engine, grant, start_ctx

        engine, grant, start_ctx = asyncio.run(scenario())

        grant.assert_called_once()
        engine.start.assert_awaited_once()
        self.assertTrue(engine.start.await_args.kwargs["is_current"]())
        start_ctx.send.assert_awaited_once_with(
            "🤖 자율 행동 루프를 시작했어."
        )

    def test_guild_reset_block_fences_effects_but_allows_recovery(self) -> None:
        engine_factory = Mock()
        grant = Mock()
        revoke = Mock(return_value={"ok": True})
        enable_mode = AsyncMock(
            return_value={
                "connected": True,
                "outcome_verified": True,
                "outcome_code": "minecraft_connected",
            }
        )
        enable_route = AsyncMock(return_value=True)
        set_goal = AsyncMock()
        ensure_voice = AsyncMock()
        save_prefix = Mock()
        reset = Mock()
        deps = make_command_deps(
            guild_is_open=lambda guild_id: guild_id == 8,
            get_or_create_autonomy_engine=engine_factory,
            grant_autonomy_authorization=grant,
            revoke_autonomy_authorization=revoke,
            enable_minecraft_mode=enable_mode,
            enable_minecraft_autonomy_route=enable_route,
            set_minecraft_goal=set_goal,
            ensure_listening_voice_client=ensure_voice,
            save_guild_command_prefix=save_prefix,
            reset_guild_runtime_state=reset,
            get_guild_command_prefix=lambda _guild_id: "!",
            build_reset_guild_memory_reply=lambda **_kwargs: "reset-ok",
            build_minecraft_connect_reply=lambda _observed: "connect-ok",
        )
        composition = make_composition(commands_deps=deps)
        composition.mark_text_session_from_command = Mock()

        def context(guild_id: int, content: str) -> SimpleNamespace:
            return SimpleNamespace(
                guild=SimpleNamespace(id=guild_id, name="Guild"),
                author=SimpleNamespace(id=3),
                channel=SimpleNamespace(id=2),
                message=SimpleNamespace(id=4, content=content),
                send=AsyncMock(return_value=SimpleNamespace(id=5)),
                typing=Mock(return_value=AsyncMock()),
            )

        asyncio.run(composition.autonomy_start_command(context(7, "!자율시작")))
        blocked_voice = context(7, "!들어와")
        blocked_voice.author.voice = SimpleNamespace(
            channel=SimpleNamespace(id=9, name="Voice")
        )
        asyncio.run(composition.join_voice(blocked_voice))
        asyncio.run(composition.rejoin_voice(blocked_voice))
        asyncio.run(composition.minecraft_connect_command(context(7, "!마크접속")))
        asyncio.run(
            composition.minecraft_goal_command(
                context(7, "!마크목표 diamond"),
                goal="diamond",
            )
        )
        asyncio.run(composition.set_guild_prefix(context(7, "!접두사 ?"), "?"))

        engine_factory.assert_not_called()
        grant.assert_not_called()
        ensure_voice.assert_not_awaited()
        enable_mode.assert_not_awaited()
        enable_route.assert_not_awaited()
        set_goal.assert_not_awaited()
        save_prefix.assert_not_called()

        asyncio.run(composition.reset_guild_memory(context(7, "!초기화")))
        reset.assert_called_once_with(7)
        asyncio.run(composition.autonomy_stop_command(context(7, "!자율정지")))
        revoke.assert_called_once_with(
            7,
            reason_code="explicit_autonomy_stop",
        )

        asyncio.run(composition.minecraft_connect_command(context(8, "!마크접속")))
        enable_mode.assert_awaited_once()
        enable_route.assert_awaited_once_with(8)

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
        for name in ("마크접속", "마크종료", "마크상태"):
            self.assertFalse(bot.get_command(name).ignore_extra)

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
        self.assertIs(bot.on_command_error.__self__, composition)

    def test_command_replies_use_one_post_delivery_continuity_owner(
        self,
    ) -> None:
        command_deps = make_command_deps(
            get_guild_command_prefix=lambda _guild_id: "!",
            build_help_command_text=(
                lambda **_kwargs: "help reply"
            ),
            build_minecraft_goal_missing_reply=(
                lambda _prefix: "missing goal"
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

    def test_on_ready_retries_transient_saved_channel_restore(self) -> None:
        class VoiceClient:
            pass

        guild = SimpleNamespace(id=7, voice_client=None)
        restore = AsyncMock(
            side_effect=[
                (False, "voice_rearm_failed"),
                (True, "General"),
            ]
        )
        events = make_event_deps(
            bot_guilds=lambda: [guild],
            voice_client_type=VoiceClient,
            voice_rejoin_on_ready=True,
            restore_last_voice_channel=restore,
        )

        with patch(
            "evelyn_core.discord_app_composition_runtime.asyncio.sleep",
            new=AsyncMock(),
        ) as sleep:
            asyncio.run(make_composition(events=events).on_ready())

        self.assertEqual(restore.await_count, 2)
        self.assertEqual(
            restore.await_args_list,
            [call(guild), call(guild)],
        )
        sleep.assert_awaited_once_with(0.5)
        events.log.assert_any_call(
            "[VOICE READY REJOIN] guild=7 channel=General"
        )

    def test_on_ready_does_not_retry_permanent_saved_channel_failure(self) -> None:
        class VoiceClient:
            pass

        guild = SimpleNamespace(id=7, voice_client=None)
        restore = AsyncMock(
            return_value=(False, "saved_channel_not_available")
        )
        events = make_event_deps(
            bot_guilds=lambda: [guild],
            voice_client_type=VoiceClient,
            voice_rejoin_on_ready=True,
            restore_last_voice_channel=restore,
        )

        with patch(
            "evelyn_core.discord_app_composition_runtime.asyncio.sleep",
            new=AsyncMock(),
        ) as sleep:
            asyncio.run(make_composition(events=events).on_ready())

        restore.assert_awaited_once_with(guild)
        sleep.assert_not_awaited()

    def test_on_ready_stale_generation_aborts_saved_channel_retry(self) -> None:
        async def scenario() -> tuple[AsyncMock, AsyncMock]:
            class VoiceClient:
                pass

            guild = SimpleNamespace(id=7, voice_client=None)
            restore = AsyncMock(
                side_effect=[
                    (False, "voice_rearm_failed"),
                    (True, "General"),
                    AssertionError("stale ready generation retried"),
                ]
            )
            events = make_event_deps(
                bot_guilds=lambda: [guild],
                voice_client_type=VoiceClient,
                voice_rejoin_on_ready=True,
                restore_last_voice_channel=restore,
            )
            composition = make_composition(events=events)
            retry_waiting = asyncio.Event()
            release_retry = asyncio.Event()

            async def controlled_sleep(delay: float) -> None:
                self.assertEqual(delay, 0.5)
                retry_waiting.set()
                await release_retry.wait()

            with patch(
                "evelyn_core.discord_app_composition_runtime.asyncio.sleep",
                side_effect=controlled_sleep,
            ) as sleep:
                stale_ready = asyncio.create_task(composition.on_ready())
                await asyncio.wait_for(retry_waiting.wait(), timeout=1.0)
                current_ready = asyncio.create_task(composition.on_ready())
                await asyncio.wait_for(current_ready, timeout=1.0)
                release_retry.set()
                await asyncio.wait_for(stale_ready, timeout=1.0)
            return restore, sleep

        restore, sleep = asyncio.run(scenario())

        self.assertEqual(restore.await_count, 2)
        sleep.assert_awaited_once_with(0.5)

    def test_on_ready_records_fixed_error_code_in_runtime_status(self) -> None:
        runtime_status = Mock()
        private_error = "PRIVATE_CONTROL_PAGE_START C:\\private\\token"
        failure = RuntimeError(private_error)
        events = make_event_deps(
            runtime_status=runtime_status,
            start_control_page_server=AsyncMock(side_effect=failure),
        )

        asyncio.run(make_composition(events=events).on_ready())

        runtime_status.record_error.assert_called_once_with(
            "control_page_start_failed",
            failure,
        )
        events.mark_startup_component.assert_any_call(
            "control_api", "failed", "control_page_start_failed:RuntimeError"
        )
        events.log.assert_any_call(
            "[CONTROL PAGE] start_fail "
            "errorCode=control_page_start_failed errorType=RuntimeError"
        )
        self.assertNotIn(private_error, repr(events.mark_startup_component.call_args_list))
        self.assertNotIn(private_error, repr(events.log.call_args_list))

    def test_autonomy_start_failure_records_type_only_runtime_error(self) -> None:
        private_error = "PRIVATE_AUTONOMY_START C:\\private\\token"
        private_observer_error = "PRIVATE_OBSERVER C:\\private\\observer"
        runtime_status = DiscordRuntimeStatus(
            gateway_ready=lambda: False,
            bot_guilds=lambda: [],
            voice_client_type=object,
            now=lambda: 123.0,
        )
        original_record_error = runtime_status.record_error

        def record_then_fail(code, exc) -> None:
            original_record_error(code, exc)
            raise OSError(private_observer_error)

        runtime_status.record_error = Mock(side_effect=record_then_fail)
        engine = SimpleNamespace(
            stop=AsyncMock(),
            start=AsyncMock(side_effect=RuntimeError(private_error)),
        )
        revoke = Mock()
        composition = make_composition(
            events=make_event_deps(runtime_status=runtime_status),
            commands_deps=make_command_deps(
                get_or_create_autonomy_engine=Mock(return_value=engine),
                grant_autonomy_authorization=Mock(return_value={"ok": True}),
                revoke_autonomy_authorization=revoke,
            ),
        )
        composition.mark_text_session_from_command = Mock()
        ctx = SimpleNamespace(
            guild=SimpleNamespace(id=7),
            author=SimpleNamespace(id=3),
            channel=SimpleNamespace(id=2),
            message=SimpleNamespace(id=4, content="!autonomy-on"),
            send=AsyncMock(),
        )

        asyncio.run(composition.autonomy_start_command(ctx))

        snapshot = runtime_status.snapshot()
        self.assertEqual(snapshot["lastErrorCode"], "autonomy_start_failed")
        self.assertEqual(snapshot["lastErrorType"], "RuntimeError")
        self.assertNotIn(private_error, repr(snapshot))
        self.assertNotIn(private_observer_error, repr(snapshot))
        revoke.assert_called_once_with(7, reason_code="start_failed")
        ctx.send.assert_awaited_once_with(
            "❌ 자율 행동 시작에 실패했고 승인은 폐기했어."
        )

    def test_autonomy_stop_failure_records_type_only_runtime_error(self) -> None:
        private_error = "PRIVATE_AUTONOMY_STOP C:\\private\\token"
        private_observer_error = "PRIVATE_OBSERVER C:\\private\\observer"
        runtime_status = DiscordRuntimeStatus(
            gateway_ready=lambda: False,
            bot_guilds=lambda: [],
            voice_client_type=object,
            now=lambda: 123.0,
        )
        original_record_error = runtime_status.record_error

        def record_then_fail(code, exc) -> None:
            original_record_error(code, exc)
            raise OSError(private_observer_error)

        runtime_status.record_error = Mock(side_effect=record_then_fail)
        revoke = Mock()
        composition = make_composition(
            events=make_event_deps(runtime_status=runtime_status),
            commands_deps=make_command_deps(
                autonomy_engines={
                    7: SimpleNamespace(
                        stop=AsyncMock(
                            side_effect=RuntimeError(private_error)
                        )
                    )
                },
                revoke_autonomy_authorization=revoke,
            ),
        )
        composition.mark_text_session_from_command = Mock()
        ctx = SimpleNamespace(
            guild=SimpleNamespace(id=7),
            author=SimpleNamespace(id=3),
            channel=SimpleNamespace(id=2),
            message=SimpleNamespace(id=4, content="!autonomy-off"),
            send=AsyncMock(),
        )

        asyncio.run(composition.autonomy_stop_command(ctx))

        snapshot = runtime_status.snapshot()
        self.assertEqual(snapshot["lastErrorCode"], "autonomy_stop_failed")
        self.assertEqual(snapshot["lastErrorType"], "RuntimeError")
        self.assertNotIn(private_error, repr(snapshot))
        self.assertNotIn(private_observer_error, repr(snapshot))
        revoke.assert_called_once_with(7, reason_code="explicit_autonomy_stop")
        self.assertIn(
            "autonomy_stop_failed",
            ctx.send.await_args.args[0],
        )

    def test_minecraft_connect_requires_verified_autonomy_route(self) -> None:
        private_error = "PRIVATE_MINECRAFT_ROUTE C:\\private\\token"

        for route_result, error_type in (
            (False, "RuntimeError"),
            (OSError(private_error), "OSError"),
        ):
            with self.subTest(error_type=error_type):
                runtime_status = DiscordRuntimeStatus(
                    gateway_ready=lambda: False,
                    bot_guilds=lambda: [],
                    voice_client_type=object,
                    now=lambda: 123.0,
                )
                route = (
                    AsyncMock(side_effect=route_result)
                    if isinstance(route_result, BaseException)
                    else AsyncMock(return_value=route_result)
                )
                build_reply = Mock(return_value="✅ SUCCESS_CANARY")
                commands_deps = make_command_deps(
                    enable_minecraft_mode=AsyncMock(
                        return_value={
                            "connected": True,
                            "outcome_verified": True,
                            "outcome_code": "minecraft_connected",
                        }
                    ),
                    enable_minecraft_autonomy_route=route,
                    build_minecraft_connect_reply=build_reply,
                )
                composition = make_composition(
                    events=make_event_deps(runtime_status=runtime_status),
                    commands_deps=commands_deps,
                )
                composition.mark_text_session_from_command = Mock()
                ctx = SimpleNamespace(
                    guild=SimpleNamespace(id=7),
                    author=SimpleNamespace(id=3),
                    channel=SimpleNamespace(id=2),
                    message=SimpleNamespace(id=4, content="!마크접속"),
                    send=AsyncMock(return_value=SimpleNamespace(id=5)),
                    typing=Mock(return_value=AsyncMock()),
                )

                asyncio.run(composition.minecraft_connect_command(ctx))

                snapshot = runtime_status.snapshot()
                self.assertEqual(snapshot["errorCount"], 1)
                self.assertEqual(
                    snapshot["lastErrorCode"],
                    "minecraft_connect_failed",
                )
                self.assertEqual(snapshot["lastErrorType"], error_type)
                self.assertNotIn(private_error, repr(snapshot))
                self.assertNotIn(private_error, repr(commands_deps.log.call_args_list))
                route.assert_awaited_once_with(7)
                build_reply.assert_not_called()
                ctx.typing.assert_called_once_with()
                self.assertEqual(ctx.send.await_count, 1)
                reply = ctx.send.await_args.args[0]
                self.assertIn("minecraft_connect_failed", reply)
                self.assertNotIn("SUCCESS_CANARY", reply)
                self.assertNotIn(private_error, reply)

    def test_minecraft_connect_reply_failure_is_not_effect_failure(self) -> None:
        private_error = "PRIVATE_MINECRAFT_SEND C:\\private\\token"
        runtime_status = DiscordRuntimeStatus(
            gateway_ready=lambda: False,
            bot_guilds=lambda: [],
            voice_client_type=object,
            now=lambda: 123.0,
        )
        send_error = OSError(private_error)
        composition = make_composition(
            events=make_event_deps(runtime_status=runtime_status),
            commands_deps=make_command_deps(
                enable_minecraft_mode=AsyncMock(
                    return_value={
                        "connected": True,
                        "outcome_verified": True,
                        "outcome_code": "minecraft_connected",
                    }
                ),
                enable_minecraft_autonomy_route=AsyncMock(return_value=True),
                build_minecraft_connect_reply=Mock(
                    return_value="✅ minecraft ready"
                ),
            ),
        )
        composition.mark_text_session_from_command = Mock()
        ctx = SimpleNamespace(
            guild=SimpleNamespace(id=7),
            author=SimpleNamespace(id=3),
            channel=SimpleNamespace(id=2),
            message=SimpleNamespace(id=4, content="!마크접속"),
            send=AsyncMock(side_effect=send_error),
            typing=Mock(return_value=AsyncMock()),
        )

        with self.assertRaises(OSError) as raised:
            asyncio.run(composition.minecraft_connect_command(ctx))

        self.assertIs(raised.exception, send_error)
        ctx.typing.assert_called_once_with()
        self.assertEqual(ctx.send.await_count, 1)
        self.assertEqual(
            ctx.send.await_args.args[0],
            "✅ minecraft ready",
        )
        self.assertEqual(runtime_status.snapshot()["errorCount"], 0)
        composition.mark_text_session_from_command.assert_not_called()

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

    def test_search_recovery_blocks_text_ingress_until_initial_run_completes(
        self,
    ) -> None:
        async def scenario() -> tuple[AsyncMock, AsyncMock]:
            recovery_started = asyncio.Event()
            release_recovery = asyncio.Event()

            async def recover() -> dict[str, int]:
                recovery_started.set()
                await release_recovery.wait()
                return {"pending": 0}

            events = make_event_deps(recover_search_followups=AsyncMock(side_effect=recover))
            composition = make_composition(events=events)
            handler = AsyncMock()

            with patch(
                "evelyn_core.discord_app_composition_runtime.handle_discord_text_message",
                new=handler,
            ):
                await composition.on_message(object())
                handler.assert_not_awaited()

                ready_task = asyncio.create_task(composition.on_ready())
                await asyncio.wait_for(recovery_started.wait(), timeout=1.0)
                message_task = asyncio.create_task(composition.on_message(object()))
                await asyncio.sleep(0)
                self.assertFalse(message_task.done())
                handler.assert_not_awaited()

                release_recovery.set()
                await asyncio.wait_for(
                    asyncio.gather(ready_task, message_task),
                    timeout=1.0,
                )

            return events.recover_search_followups, handler

        recover, handler = asyncio.run(scenario())

        recover.assert_awaited_once_with()
        handler.assert_awaited_once()

    def test_search_recovery_runs_once_across_ready_and_voice_rearm(self) -> None:
        class VoiceClient:
            def __init__(self, channel) -> None:
                self.channel = channel

            @staticmethod
            def is_connected() -> bool:
                return True

            @staticmethod
            def is_listening() -> bool:
                return True

        async def scenario() -> tuple[AsyncMock, AsyncMock]:
            recover = AsyncMock(return_value={"pending": 0})
            channel = SimpleNamespace(id=9, name="General")
            client = VoiceClient(channel)
            guild = SimpleNamespace(id=7, voice_client=client)
            ensure = AsyncMock(return_value=client)
            events = make_event_deps(
                voice_client_type=VoiceClient,
                ensure_listening_voice_client=ensure,
                recover_search_followups=recover,
            )
            composition = make_composition(events=events)

            await composition.on_ready()
            await composition.on_ready()
            await composition.on_voice_state_update(
                SimpleNamespace(id=99, guild=guild),
                SimpleNamespace(channel=SimpleNamespace(id=8, name="Old")),
                SimpleNamespace(channel=channel),
            )
            return recover, ensure

        recover, ensure = asyncio.run(scenario())

        recover.assert_awaited_once_with()
        self.assertGreaterEqual(ensure.await_count, 1)

    def test_search_recovery_failure_is_permanent_and_fails_closed(self) -> None:
        class VoiceClient:
            channel = SimpleNamespace(id=9, name="General")

            @staticmethod
            def is_connected() -> bool:
                return True

        async def scenario() -> tuple[AsyncMock, AsyncMock, AsyncMock, Mock]:
            failure = OSError("private recovery failure")
            recover = AsyncMock(side_effect=failure)
            ensure = AsyncMock(return_value=VoiceClient())
            runtime_status = Mock()
            events = make_event_deps(
                runtime_status=runtime_status,
                voice_client_type=VoiceClient,
                ensure_listening_voice_client=ensure,
                recover_search_followups=recover,
            )
            composition = make_composition(events=events)
            handler = AsyncMock()

            with patch(
                "evelyn_core.discord_app_composition_runtime.handle_discord_text_message",
                new=handler,
            ):
                await composition.on_ready()
                await composition.on_ready()
                await composition.on_message(object())
                await composition.on_voice_state_update(
                    SimpleNamespace(
                        id=99,
                        guild=SimpleNamespace(id=7, voice_client=VoiceClient()),
                    ),
                    SimpleNamespace(channel=SimpleNamespace(id=8)),
                    SimpleNamespace(channel=VoiceClient.channel),
                )

            runtime_status.record_error.assert_called_once_with(
                "search_followup_recovery_failed",
                failure,
            )
            return recover, handler, ensure, runtime_status

        recover, handler, ensure, _runtime_status = asyncio.run(scenario())

        recover.assert_awaited_once_with()
        handler.assert_not_awaited()
        ensure.assert_not_awaited()

    def test_voice_state_rearm_never_triggers_search_recovery(self) -> None:
        class VoiceClient:
            def __init__(self, *, connected: bool) -> None:
                self.channel = SimpleNamespace(name="General")
                self.connected = connected

            def is_connected(self) -> bool:
                return self.connected

            @staticmethod
            def is_listening() -> bool:
                return True

        async def run_case(rearmed_client):
            recover = AsyncMock(return_value={"pending": 0})
            ensure = AsyncMock(return_value=rearmed_client)
            guild = SimpleNamespace(
                id=7,
                voice_client=VoiceClient(connected=True),
            )
            member = SimpleNamespace(id=99, guild=guild)
            events = make_event_deps(
                voice_client_type=VoiceClient,
                ensure_listening_voice_client=ensure,
                recover_search_followups=recover,
            )
            composition = make_composition(events=events)
            await composition.on_ready()
            recover.reset_mock()
            await composition.on_voice_state_update(
                member,
                SimpleNamespace(channel=None),
                SimpleNamespace(channel=guild.voice_client.channel),
            )
            return ensure, recover

        ensure, recover = asyncio.run(run_case(None))
        ensure.assert_awaited_once()
        recover.assert_not_awaited()

        ensure, recover = asyncio.run(
            run_case(VoiceClient(connected=False))
        )
        ensure.assert_awaited_once()
        recover.assert_not_awaited()

        ensure, recover = asyncio.run(run_case(VoiceClient(connected=True)))
        ensure.assert_awaited_once()
        recover.assert_not_awaited()

    def test_voice_channel_event_forces_cleanup_when_after_differs(self) -> None:
        class VoiceClient:
            def __init__(self, channel) -> None:
                self.channel = channel
                self.connected = True
                self._listener_generation = 4

            def is_connected(self) -> bool:
                return self.connected

            @staticmethod
            def is_listening() -> bool:
                return True

        async def run_case() -> list[tuple[AsyncMock, bool]]:
            old_channel = SimpleNamespace(id=8, name="Old")
            new_channel = SimpleNamespace(id=9, name="New")
            calls = []
            for before_channel, expected_force in (
                (old_channel, True),
                (None, True),
                (new_channel, False),
            ):
                client = VoiceClient(new_channel)
                guild = SimpleNamespace(id=7, voice_client=client)
                member = SimpleNamespace(id=99, guild=guild)
                ensure = AsyncMock(return_value=client)
                events = make_event_deps(
                    voice_client_type=VoiceClient,
                    ensure_listening_voice_client=ensure,
                )
                await make_composition(events=events).on_voice_state_update(
                    member,
                    SimpleNamespace(channel=before_channel),
                    SimpleNamespace(channel=new_channel),
                )
                calls.append((ensure, expected_force))
            return calls

        for ensure, expected_force in asyncio.run(run_case()):
            ensure.assert_awaited_once()
            self.assertEqual(
                ensure.await_args.kwargs["force_listener_reset"],
                expected_force,
            )
            self.assertIs(
                ensure.await_args.kwargs["expected_voice_client"],
                ensure.await_args.args[0].voice_client,
            )

    def test_voice_channel_event_retries_transient_failure_after_listener_stop(self) -> None:
        class VoiceClient:
            def __init__(self, channel) -> None:
                self.channel = channel
                self.connected = True
                self.listening = True

            def is_connected(self) -> bool:
                return self.connected

            def is_listening(self) -> bool:
                return self.listening

        async def scenario() -> tuple[VoiceClient, AsyncMock, AsyncMock]:
            old_channel = SimpleNamespace(id=8, name="Old")
            target_channel = SimpleNamespace(id=9, name="New")
            client = VoiceClient(target_channel)
            guild = SimpleNamespace(id=7, voice_client=client)
            member = SimpleNamespace(id=99, guild=guild)

            async def rearm(*_args, **_kwargs):
                client.listening = False
                if ensure.await_count == 1:
                    raise RuntimeError("private transient rearm failure")
                client.listening = True
                return client

            ensure = AsyncMock(side_effect=rearm)
            events = make_event_deps(
                voice_client_type=VoiceClient,
                ensure_listening_voice_client=ensure,
            )
            composition = make_composition(events=events)
            with patch(
                "evelyn_core.discord_app_composition_runtime.asyncio.sleep",
                new=AsyncMock(),
            ) as sleep:
                await composition.on_voice_state_update(
                    member,
                    SimpleNamespace(channel=old_channel),
                    SimpleNamespace(channel=target_channel),
                )
            return client, ensure, sleep

        client, ensure, sleep = asyncio.run(scenario())

        self.assertEqual(ensure.await_count, 2)
        self.assertTrue(client.is_listening())
        sleep.assert_awaited_once_with(0.5)

    def test_voice_channel_event_rearm_retry_is_bounded_and_type_only(self) -> None:
        class VoiceClient:
            def __init__(self, channel) -> None:
                self.channel = channel

            @staticmethod
            def is_connected() -> bool:
                return True

            @staticmethod
            def is_listening() -> bool:
                return False

        channel = SimpleNamespace(id=9, name="New")
        client = VoiceClient(channel)
        guild = SimpleNamespace(id=7, voice_client=client)
        member = SimpleNamespace(id=99, guild=guild)
        failures = [
            RuntimeError(f"private failure {index}")
            for index in range(1, 4)
        ]
        ensure = AsyncMock(
            side_effect=[*failures, AssertionError("unbounded rearm")]
        )
        runtime_status = Mock()
        events = make_event_deps(
            voice_client_type=VoiceClient,
            ensure_listening_voice_client=ensure,
            runtime_status=runtime_status,
        )

        with patch(
            "evelyn_core.discord_app_composition_runtime.asyncio.sleep",
            new=AsyncMock(),
        ) as sleep:
            asyncio.run(
                make_composition(events=events).on_voice_state_update(
                    member,
                    SimpleNamespace(channel=SimpleNamespace(id=8)),
                    SimpleNamespace(channel=channel),
                )
            )

        self.assertEqual(ensure.await_count, 3)
        self.assertEqual(sleep.await_count, 2)
        runtime_status.record_error.assert_called_once_with(
            "voice_state_rearm_failed",
            failures[-1],
        )
        events.log.assert_any_call(
            "[VOICE STATE REARM FAIL] guild=7 errorType=RuntimeError"
        )
        self.assertNotIn("private failure", str(events.log.call_args_list))

    def test_voice_channel_event_rearm_retry_stops_after_newer_move(self) -> None:
        class VoiceClient:
            def __init__(self, channel) -> None:
                self.channel = channel

            @staticmethod
            def is_connected() -> bool:
                return True

        async def scenario() -> AsyncMock:
            target_channel = SimpleNamespace(id=9, name="New")
            client = VoiceClient(target_channel)
            guild = SimpleNamespace(id=7, voice_client=client)
            member = SimpleNamespace(id=99, guild=guild)
            ensure = AsyncMock(
                side_effect=RuntimeError("superseded rearm")
            )
            events = make_event_deps(
                voice_client_type=VoiceClient,
                ensure_listening_voice_client=ensure,
            )

            async def move_again(_delay: float) -> None:
                client.channel = SimpleNamespace(id=10, name="Newest")

            with patch(
                "evelyn_core.discord_app_composition_runtime.asyncio.sleep",
                side_effect=move_again,
            ):
                await make_composition(events=events).on_voice_state_update(
                    member,
                    SimpleNamespace(channel=SimpleNamespace(id=8)),
                    SimpleNamespace(channel=target_channel),
                )
            return ensure

        ensure = asyncio.run(scenario())

        ensure.assert_awaited_once()

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
        self.assertIn("admit_search_followup_recovery=(", main_source)
        self.assertIn(
            "discord_app_composition.admit_search_followup_ingress()",
            main_source,
        )
        self.assertNotIn("@bot.event", main_source)
        self.assertNotIn("@bot.command", main_source)
        self.assertNotIn("globals()", runtime_source)


if __name__ == "__main__":
    unittest.main()
