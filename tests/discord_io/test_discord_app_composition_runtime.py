from __future__ import annotations

import asyncio
import inspect
import json
import sys
import tempfile
import unittest
from dataclasses import fields, replace
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
from evelyn_core.discord_conversation_archive_runtime import (  # noqa: E402
    DiscordSharedSessionRegistry,
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
        conversation_archive_enabled=False,
        conversation_archive_read_self=None,
        conversation_archive_preview_delete=None,
        conversation_archive_apply_delete=None,
        conversation_archive_set_consent=None,
        conversation_archive_capture_feedback=None,
        conversation_archive_archive_autonomy_grant=None,
        conversation_archive_archive_minecraft_command=None,
        conversation_archive_sleep=None,
        conversation_archive_operator_authorized=lambda _ctx: True,
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
        conversation_archive_enabled=False,
        conversation_participation_tracker=None,
        conversation_participation_observer=None,
        conversation_consent_current=None,
        conversation_archive_ready=AsyncMock(return_value="boot-1"),
        conversation_archive_otp_delivery_worker=None,
        conversation_shared_session_registry=None,
        conversation_shared_session_open=AsyncMock(),
        conversation_shared_session_close=AsyncMock(),
        conversation_archive_command_guild_id=0,
        conversation_archive_command_ownership=("", ""),
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


def active_shared_sessions(
    *,
    guild_id: int = 7,
    text_channel_id: int = 8,
    voice_channel_id: int = 9,
    operator_user_id: int = 5,
) -> DiscordSharedSessionRegistry:
    sessions = DiscordSharedSessionRegistry(ttl_seconds=3600.0)
    sessions.begin_generation("boot-1")
    sessions.open(
        operator_user_id=operator_user_id,
        guild_id=guild_id,
        text_channel_id=text_channel_id,
        voice_channel_id=voice_channel_id,
    )
    return sessions


class FakeRemoteApplicationCommand:
    def __init__(self, command_id: int, payload: dict) -> None:
        self.id = int(command_id)
        self._payload = dict(payload)
        self.name = str(payload.get("name") or "")
        self.default_member_permissions = payload.get("default_member_permissions")
        self.dm_permission = payload.get("dm_permission", True)
        self.nsfw = payload.get("nsfw", False)

    def to_dict(self) -> dict:
        return dict(self._payload)


class FakeDiscordApplicationCommandRegistry:
    def __init__(
        self,
        *,
        guild_payloads=(),
        global_payloads=(),
        fail_upsert_at: int | None = None,
        ambiguous_upsert_failure: bool = False,
        invalid_upsert_shape_at: int | None = None,
        fail_edit_at: int | None = None,
        ambiguous_edit_failure: bool = False,
        drift_second_global_fetch: bool = False,
        concurrent_on_upsert_failure: dict | None = None,
    ) -> None:
        self.guild = [
            FakeRemoteApplicationCommand(1000 + index, payload)
            for index, payload in enumerate(guild_payloads)
        ]
        self.globals = [
            FakeRemoteApplicationCommand(2000 + index, payload)
            for index, payload in enumerate(global_payloads)
        ]
        self.fail_upsert_at = fail_upsert_at
        self.ambiguous_upsert_failure = ambiguous_upsert_failure
        self.invalid_upsert_shape_at = invalid_upsert_shape_at
        self.fail_edit_at = fail_edit_at
        self.ambiguous_edit_failure = ambiguous_edit_failure
        self.drift_second_global_fetch = drift_second_global_fetch
        self.concurrent_on_upsert_failure = concurrent_on_upsert_failure
        self.upsert_count = 0
        self.edit_count = 0
        self.global_fetch_count = 0
        self.next_id = 3000

    async def get_guild(self, _application_id, _guild_id):
        result = []
        for command in self.guild:
            payload = {**command.to_dict(), "id": str(command.id)}
            payload.pop("contexts", None)
            payload.pop("integration_types", None)
            result.append(payload)
        return result

    async def get_global(self, _application_id):
        self.global_fetch_count += 1
        result = [
            {**command.to_dict(), "id": str(command.id)} for command in self.globals
        ]
        if self.drift_second_global_fetch and self.global_fetch_count == 2:
            result.append(
                {
                    "id": "9999",
                    "type": 1,
                    "name": "concurrent-global",
                    "description": "changed outside the publisher",
                    "options": [],
                }
            )
        return result

    async def upsert(self, _application_id, _guild_id, payload):
        self.upsert_count += 1
        existing = next(
            (command for command in self.guild if command.name == payload["name"]),
            None,
        )
        command_id = existing.id if existing is not None else self.next_id
        if existing is None:
            self.next_id += 1
        response_payload = dict(payload)
        if self.invalid_upsert_shape_at == self.upsert_count:
            response_payload["name"] = "unexpected-temp-name"
        replacement = FakeRemoteApplicationCommand(command_id, response_payload)
        if existing is not None:
            self.guild.remove(existing)
        self.guild.append(replacement)
        if self.fail_upsert_at == self.upsert_count:
            if self.concurrent_on_upsert_failure is not None:
                self.guild.append(
                    FakeRemoteApplicationCommand(
                        self.next_id,
                        self.concurrent_on_upsert_failure,
                    )
                )
                self.next_id += 1
            if not self.ambiguous_upsert_failure:
                self.guild.remove(replacement)
                if existing is not None:
                    self.guild.append(existing)
            raise RuntimeError("simulated_upsert_failure")
        return {**replacement.to_dict(), "id": str(command_id)}

    async def post(self, _route, **kwargs):
        payload = kwargs["json"]
        existing = next(
            (command for command in self.guild if command.name == payload["name"]),
            None,
        )
        await kwargs["raise_for_status"](
            SimpleNamespace(status=200 if existing is not None else 201)
        )
        return await self.upsert(None, None, payload)

    async def edit(self, _application_id, _guild_id, command_id, payload):
        self.edit_count += 1
        command = next(
            (command for command in self.guild if command.id == command_id),
            None,
        )
        if command is None or any(
            other.id != command_id and other.name == payload["name"]
            for other in self.guild
        ):
            raise RuntimeError("simulated_edit_collision")
        replacement = FakeRemoteApplicationCommand(command_id, payload)
        if self.fail_edit_at != self.edit_count or self.ambiguous_edit_failure:
            self.guild.remove(command)
            self.guild.append(replacement)
        if self.fail_edit_at == self.edit_count:
            raise RuntimeError("simulated_edit_failure")
        return {**replacement.to_dict(), "id": str(command_id)}

    async def delete(self, _application_id, _guild_id, command_id):
        command = next(
            (command for command in self.guild if command.id == command_id),
            None,
        )
        if command is None:
            raise RuntimeError("simulated_delete_missing")
        self.guild.remove(command)


class DiscordAppCompositionTests(unittest.TestCase):
    def setUp(self) -> None:
        self._archive_command_temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self._archive_command_temporary.cleanup)
        self._archive_command_fixture_index = 0

    def _archive_command_fixture(self):
        self._archive_command_fixture_index += 1
        guild_id = 123456789012345678
        ownership_path = Path(self._archive_command_temporary.name) / (
            f"ownership-{self._archive_command_fixture_index}.json"
        )
        composition = make_composition(
            events=make_event_deps(
                conversation_archive_enabled=True,
                conversation_archive_command_guild_id=guild_id,
                conversation_archive_command_ownership=(
                    str(ownership_path),
                    "a" * 32,
                ),
            ),
            commands_deps=make_command_deps(conversation_archive_enabled=True),
        )
        bot = commands.Bot(
            command_prefix="!",
            intents=discord.Intents.none(),
            help_command=None,
            application_id=987654321098765432,
        )
        composition.register(bot)
        desired = {
            command.name: command.to_dict(bot.tree)
            for command in composition._conversation_archive_application_commands
        }
        globals_payload = [
            {
                "type": 1,
                "name": f"global-{index}",
                "description": f"existing global command {index}",
                "options": [],
            }
            for index in range(51)
        ]
        return composition, bot, guild_id, desired, globals_payload

    @staticmethod
    def _wire_archive_command_registry(bot, registry) -> None:
        bot.http.get_guild_commands = AsyncMock(side_effect=registry.get_guild)
        bot.http.get_global_commands = AsyncMock(side_effect=registry.get_global)
        bot.tree.sync = AsyncMock()
        bot.http.request = AsyncMock(side_effect=registry.post)
        bot.http.edit_guild_command = AsyncMock(side_effect=registry.edit)
        bot.http.delete_guild_command = AsyncMock(side_effect=registry.delete)
        bot.http.upsert_global_command = AsyncMock()
        bot.http.bulk_upsert_global_commands = AsyncMock()
        bot.http.bulk_upsert_guild_commands = AsyncMock()

    def _assert_no_bulk_or_global_command_mutation(self, bot) -> None:
        bot.tree.sync.assert_not_awaited()
        bot.http.upsert_global_command.assert_not_awaited()
        bot.http.bulk_upsert_global_commands.assert_not_awaited()
        bot.http.bulk_upsert_guild_commands.assert_not_awaited()

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

    def test_archive_application_commands_register_only_for_literal_true_and_are_guild_only(self) -> None:
        for enabled, expected in ((Mock(name="truthy"), False), (True, True)):
            with self.subTest(enabled=enabled):
                composition = make_composition(
                    commands_deps=make_command_deps(
                        conversation_archive_enabled=enabled,
                    )
                )
                bot = commands.Bot(
                    command_prefix="!",
                    intents=discord.Intents.none(),
                    help_command=None,
                )

                bindings = composition.register(bot)
                registered = {
                    name: bot.tree.get_command(name)
                    for name in (
                        "기록열람",
                        "기록삭제",
                        "기록동의",
                        "기록철회",
                        "피드백제출",
                    )
                }

                if not expected:
                    self.assertTrue(all(command is None for command in registered.values()))
                    self.assertIsNone(bindings.record_view_application_command)
                    self.assertIsNone(bindings.feedback_application_command)
                    continue
                self.assertTrue(all(command is not None for command in registered.values()))
                self.assertIs(
                    bindings.record_view_application_command,
                    registered["기록열람"],
                )
                for command in registered.values():
                    self.assertTrue(command.allowed_contexts.guild)
                    self.assertFalse(command.allowed_contexts.dm_channel)
                    self.assertFalse(command.allowed_contexts.private_channel)
                    self.assertTrue(command.allowed_installs.guild)
                    self.assertFalse(command.allowed_installs.user)
                self.assertEqual(
                    {parameter.name for parameter in registered["기록삭제"].parameters},
                    {"시작", "끝"},
                )
                self.assertEqual(
                    {
                        parameter.name
                        for parameter in registered["피드백제출"].parameters
                    },
                    {"출처", "분류", "교정", "변경범위"},
                )
                self.assertNotIn("sync(", inspect.getsource(composition.register))

    def test_archive_command_publish_preserves_foreign_and_global_commands(self) -> None:
        composition, bot, guild_id, desired, globals_payload = (
            self._archive_command_fixture()
        )
        foreign = {
            "type": 1,
            "name": "기존서버명령",
            "description": "must remain unchanged",
            "options": [],
        }
        registry = FakeDiscordApplicationCommandRegistry(
            guild_payloads=[foreign],
            global_payloads=globals_payload,
        )
        self._wire_archive_command_registry(bot, registry)

        asyncio.run(composition._publish_conversation_archive_application_commands())

        self.assertEqual(bot.http.request.await_count, 5)
        self.assertEqual(bot.http.edit_guild_command.await_count, 5)
        self.assertTrue(
            all(
                call.args[0].method == "POST"
                and str(guild_id) in call.args[0].url
                and callable(call.kwargs.get("raise_for_status"))
                for call in bot.http.request.await_args_list
            )
        )
        temporary_names = {
            call.kwargs["json"]["name"] for call in bot.http.request.await_args_list
        }
        self.assertTrue(temporary_names.isdisjoint(desired))
        self.assertTrue(all(len(name) == 32 for name in temporary_names))
        self.assertEqual(
            [command.to_dict() for command in registry.guild if command.name == foreign["name"]],
            [foreign],
        )
        self.assertEqual(len(registry.globals), 51)
        self.assertEqual(
            {command.name for command in registry.guild if command.name in desired},
            set(desired),
        )
        self._assert_no_bulk_or_global_command_mutation(bot)

    def test_archive_command_post_200_never_claims_or_deletes_concurrent_temp(
        self,
    ) -> None:
        composition, bot, _guild_id, _desired, globals_payload = (
            self._archive_command_fixture()
        )
        registry = FakeDiscordApplicationCommandRegistry(
            global_payloads=globals_payload,
        )
        self._wire_archive_command_registry(bot, registry)
        original_post = registry.post
        injected = False

        async def inject_same_temporary_name(route, **kwargs):
            nonlocal injected
            if not injected:
                injected = True
                registry.guild.append(
                    FakeRemoteApplicationCommand(8123, kwargs["json"])
                )
            return await original_post(route, **kwargs)

        bot.http.request.side_effect = inject_same_temporary_name

        with self.assertRaisesRegex(
            RuntimeError,
            "archive_command_publish_rollback_failed",
        ):
            asyncio.run(
                composition._publish_conversation_archive_application_commands()
            )

        bot.http.delete_guild_command.assert_not_awaited()
        self.assertEqual([command.id for command in registry.guild], [8123])
        self.assertFalse(composition._conversation_archive_owned_commands)
        self.assertTrue(
            composition._conversation_archive_command_recovery_required
        )

    def test_archive_command_patch_collision_deletes_only_owned_temp(self) -> None:
        composition, bot, _guild_id, desired, globals_payload = (
            self._archive_command_fixture()
        )
        registry = FakeDiscordApplicationCommandRegistry(
            global_payloads=globals_payload,
        )
        self._wire_archive_command_registry(bot, registry)
        original_edit = registry.edit
        concurrent_id = 8456
        injected = False

        async def inject_target_before_patch(application_id, guild_id, command_id, payload):
            nonlocal injected
            if not injected:
                injected = True
                registry.guild.append(
                    FakeRemoteApplicationCommand(concurrent_id, payload)
                )
            return await original_edit(application_id, guild_id, command_id, payload)

        bot.http.edit_guild_command.side_effect = inject_target_before_patch

        with self.assertRaisesRegex(
            RuntimeError,
            "archive_command_publish_rollback_failed",
        ):
            asyncio.run(
                composition._publish_conversation_archive_application_commands()
            )

        self.assertEqual(bot.http.delete_guild_command.await_count, 1)
        self.assertEqual([command.id for command in registry.guild], [concurrent_id])
        self.assertIn(registry.guild[0].name, desired)

    def test_overlapping_archive_publish_is_serialized_exactly_once(self) -> None:
        composition, bot, _guild_id, desired, globals_payload = (
            self._archive_command_fixture()
        )
        registry = FakeDiscordApplicationCommandRegistry(
            global_payloads=globals_payload,
        )
        self._wire_archive_command_registry(bot, registry)
        first_started = asyncio.Event()
        release_first = asyncio.Event()
        original_post = registry.post

        async def block_first(route, **kwargs):
            if not first_started.is_set():
                first_started.set()
                await release_first.wait()
            return await original_post(route, **kwargs)

        bot.http.request.side_effect = block_first

        async def scenario() -> None:
            first = asyncio.create_task(
                composition._publish_conversation_archive_application_commands()
            )
            await asyncio.wait_for(first_started.wait(), timeout=1.0)
            second = asyncio.create_task(
                composition._publish_conversation_archive_application_commands()
            )
            await asyncio.sleep(0)
            self.assertFalse(second.done())
            release_first.set()
            await asyncio.gather(first, second)

        asyncio.run(scenario())

        self.assertEqual(bot.http.request.await_count, 5)
        self.assertEqual(bot.http.edit_guild_command.await_count, 5)
        self.assertEqual(
            {command.name for command in registry.guild},
            set(desired),
        )
        bot.http.delete_guild_command.assert_not_awaited()

    def test_archive_command_publish_collision_is_no_write(self) -> None:
        composition, bot, _guild_id, desired, globals_payload = (
            self._archive_command_fixture()
        )
        collision = dict(desired["기록열람"])
        collision["description"] = "foreign command with a reserved name"
        registry = FakeDiscordApplicationCommandRegistry(
            guild_payloads=[collision],
            global_payloads=globals_payload,
        )
        self._wire_archive_command_registry(bot, registry)

        with self.assertRaisesRegex(RuntimeError, "archive_command_publish_collision"):
            asyncio.run(
                composition._publish_conversation_archive_application_commands()
            )

        bot.http.request.assert_not_awaited()
        bot.http.delete_guild_command.assert_not_awaited()
        self.assertEqual(registry.guild[0].to_dict(), collision)
        self._assert_no_bulk_or_global_command_mutation(bot)

    def test_archive_command_publish_exact_shape_is_idempotent_when_guild_get_omits_global_only_fields(
        self,
    ) -> None:
        composition, bot, _guild_id, desired, globals_payload = (
            self._archive_command_fixture()
        )
        registry = FakeDiscordApplicationCommandRegistry(
            guild_payloads=list(desired.values()),
            global_payloads=globals_payload,
        )
        raw_guild = asyncio.run(registry.get_guild(bot.application_id, _guild_id))
        self.assertTrue(
            all(
                "contexts" not in command and "integration_types" not in command
                for command in raw_guild
            )
        )
        self.assertTrue(
            all(
                payload["contexts"] == [0]
                and payload["integration_types"] == [0]
                for payload in desired.values()
            )
        )
        self._wire_archive_command_registry(bot, registry)

        asyncio.run(composition._publish_conversation_archive_application_commands())

        bot.http.request.assert_not_awaited()
        bot.http.delete_guild_command.assert_not_awaited()
        self.assertEqual(bot.http.get_guild_commands.await_count, 2)
        self.assertEqual(bot.http.get_global_commands.await_count, 2)
        self.assertTrue(composition._conversation_archive_commands_published)
        asyncio.run(composition._clear_conversation_archive_application_commands())
        bot.http.delete_guild_command.assert_not_awaited()
        self._assert_no_bulk_or_global_command_mutation(bot)

    def test_archive_command_invalid_returned_shape_rolls_back_only_returned_id(
        self,
    ) -> None:
        composition, bot, _guild_id, desired, globals_payload = (
            self._archive_command_fixture()
        )
        registry = FakeDiscordApplicationCommandRegistry(
            global_payloads=globals_payload,
            invalid_upsert_shape_at=1,
        )
        self._wire_archive_command_registry(bot, registry)

        with self.assertRaisesRegex(RuntimeError, "archive_command_publish_failed"):
            asyncio.run(
                composition._publish_conversation_archive_application_commands()
            )

        self.assertEqual(bot.http.request.await_count, 1)
        self.assertEqual(bot.http.delete_guild_command.await_count, 1)
        self.assertFalse(registry.guild)
        self.assertFalse(composition._conversation_archive_owned_commands)
        self.assertFalse(
            {command.name for command in registry.guild}.intersection(desired)
        )

    def test_archive_command_patch_response_loss_rolls_back_final_owned_id(
        self,
    ) -> None:
        composition, bot, _guild_id, _desired, globals_payload = (
            self._archive_command_fixture()
        )
        registry = FakeDiscordApplicationCommandRegistry(
            global_payloads=globals_payload,
            fail_edit_at=1,
            ambiguous_edit_failure=True,
        )
        self._wire_archive_command_registry(bot, registry)

        with self.assertRaisesRegex(RuntimeError, "archive_command_publish_failed"):
            asyncio.run(
                composition._publish_conversation_archive_application_commands()
            )

        self.assertFalse(registry.guild)
        self.assertFalse(composition._conversation_archive_owned_commands)
        self.assertEqual(bot.http.delete_guild_command.await_count, 1)

    def test_archive_command_201_invalid_body_requeries_temp_before_rollback(
        self,
    ) -> None:
        composition, bot, _guild_id, _desired, globals_payload = (
            self._archive_command_fixture()
        )
        registry = FakeDiscordApplicationCommandRegistry(
            global_payloads=globals_payload,
        )
        self._wire_archive_command_registry(bot, registry)
        original_post = registry.post

        async def invalid_body_after_201(route, **kwargs):
            result = await original_post(route, **kwargs)
            return {**result, "id": "invalid"}

        bot.http.request.side_effect = invalid_body_after_201

        with self.assertRaisesRegex(RuntimeError, "archive_command_publish_failed"):
            asyncio.run(
                composition._publish_conversation_archive_application_commands()
            )

        self.assertFalse(registry.guild)
        self.assertFalse(composition._conversation_archive_owned_commands)
        self.assertEqual(bot.http.delete_guild_command.await_count, 1)

    def test_archive_command_clear_delete_response_loss_converges(self) -> None:
        composition, bot, _guild_id, _desired, globals_payload = (
            self._archive_command_fixture()
        )
        registry = FakeDiscordApplicationCommandRegistry(
            global_payloads=globals_payload,
        )
        self._wire_archive_command_registry(bot, registry)
        asyncio.run(composition._publish_conversation_archive_application_commands())
        first = True

        async def ambiguous_delete(application_id, guild_id, command_id):
            nonlocal first
            await registry.delete(application_id, guild_id, command_id)
            if first:
                first = False
                raise RuntimeError("response lost after applied delete")

        bot.http.delete_guild_command.side_effect = ambiguous_delete
        asyncio.run(composition._clear_conversation_archive_application_commands())

        self.assertFalse(registry.guild)
        self.assertFalse(composition._conversation_archive_owned_commands)

    def test_archive_command_clear_success_without_delete_keeps_ownership_and_fails(
        self,
    ) -> None:
        composition, bot, _guild_id, _desired, globals_payload = (
            self._archive_command_fixture()
        )
        registry = FakeDiscordApplicationCommandRegistry(
            global_payloads=globals_payload,
        )
        self._wire_archive_command_registry(bot, registry)
        asyncio.run(composition._publish_conversation_archive_application_commands())
        owned_ids = set(composition._conversation_archive_owned_commands)
        bot.http.delete_guild_command.side_effect = None
        bot.http.delete_guild_command.return_value = None

        with self.assertRaisesRegex(
            RuntimeError,
            "archive_command_clear_verification_failed",
        ):
            asyncio.run(composition._clear_conversation_archive_application_commands())

        self.assertEqual(set(composition._conversation_archive_owned_commands), owned_ids)
        self.assertEqual({command.id for command in registry.guild}, owned_ids)

    def test_archive_command_ownership_ledger_is_atomic_and_cleared(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / "ownership.json"
            run_id = "a" * 32
            composition, bot, _guild_id, _desired, globals_payload = (
                self._archive_command_fixture()
            )
            composition.deps = DiscordAppCompositionDeps(
                events=replace(
                    composition.deps.events,
                    conversation_archive_command_ownership=(str(path), run_id),
                ),
                commands=composition.deps.commands,
            )
            registry = FakeDiscordApplicationCommandRegistry(
                global_payloads=globals_payload,
            )
            self._wire_archive_command_registry(bot, registry)

            asyncio.run(composition._publish_conversation_archive_application_commands())
            published = json.loads(path.read_text(encoding="utf-8"))
            self.assertEqual(
                published["schema"],
                "evelyn.discord-command-ownership.v2",
            )
            self.assertEqual(published["runId"], run_id)
            self.assertEqual(
                {entry["id"] for entry in published["commands"]},
                {str(command.id) for command in registry.guild},
            )
            self.assertTrue(
                all(
                    set(entry) == {"id", "shapes"}
                    and len(entry["shapes"]) == 1
                    for entry in published["commands"]
                )
            )
            self.assertFalse(list(path.parent.glob(".*.tmp")))

            asyncio.run(composition._clear_conversation_archive_application_commands())
            cleared = json.loads(path.read_text(encoding="utf-8"))
            self.assertEqual(cleared["commands"], [])

    def test_archive_command_restart_adopts_exact_v2_ledger_without_post(self) -> None:
        composition, bot, _guild_id, desired, globals_payload = (
            self._archive_command_fixture()
        )
        registry = FakeDiscordApplicationCommandRegistry(
            global_payloads=globals_payload,
        )
        self._wire_archive_command_registry(bot, registry)
        asyncio.run(composition._publish_conversation_archive_application_commands())
        owned_ids = set(composition._conversation_archive_owned_commands)

        restarted = DiscordAppComposition(composition.deps)
        restarted_bot = commands.Bot(
            command_prefix="!",
            intents=discord.Intents.none(),
            help_command=None,
            application_id=bot.application_id,
        )
        restarted.register(restarted_bot)
        self._wire_archive_command_registry(restarted_bot, registry)

        asyncio.run(
            restarted._publish_conversation_archive_application_commands()
        )

        restarted_bot.http.request.assert_not_awaited()
        restarted_bot.http.edit_guild_command.assert_not_awaited()
        self.assertEqual(
            set(restarted._conversation_archive_owned_commands),
            owned_ids,
        )
        self.assertEqual({command.name for command in registry.guild}, set(desired))

    def test_archive_command_partial_restart_is_recovery_only_no_republish(self) -> None:
        composition, bot, _guild_id, _desired, globals_payload = (
            self._archive_command_fixture()
        )
        registry = FakeDiscordApplicationCommandRegistry(
            global_payloads=globals_payload,
        )
        self._wire_archive_command_registry(bot, registry)
        asyncio.run(composition._publish_conversation_archive_application_commands())
        ownership_path = Path(
            composition.deps.events.conversation_archive_command_ownership[0]
        )
        ledger = json.loads(ownership_path.read_text(encoding="utf-8"))
        ledger["commands"] = ledger["commands"][:1]
        ownership_path.write_text(json.dumps(ledger), encoding="utf-8")
        registry.guild = registry.guild[:1]

        restarted = DiscordAppComposition(composition.deps)
        restarted_bot = commands.Bot(
            command_prefix="!",
            intents=discord.Intents.none(),
            help_command=None,
            application_id=bot.application_id,
        )
        restarted.register(restarted_bot)
        self._wire_archive_command_registry(restarted_bot, registry)

        with self.assertRaisesRegex(
            RuntimeError,
            "archive_command_publish_restart_incomplete",
        ):
            asyncio.run(
                restarted._publish_conversation_archive_application_commands()
            )

        restarted_bot.http.request.assert_not_awaited()
        restarted_bot.http.edit_guild_command.assert_not_awaited()
        restarted_bot.http.delete_guild_command.assert_not_awaited()
        self.assertTrue(
            restarted._conversation_archive_command_recovery_required
        )
        self.assertEqual(len(restarted._conversation_archive_owned_commands), 1)

    def test_archive_command_recovery_adopts_one_exact_run_temporary_shape(self) -> None:
        composition, bot, guild_id, _desired, globals_payload = (
            self._archive_command_fixture()
        )
        registry = FakeDiscordApplicationCommandRegistry(
            global_payloads=globals_payload,
        )
        self._wire_archive_command_registry(bot, registry)
        composition._persist_conversation_archive_command_ownership(bot, guild_id)
        _bot, _guild_id, payloads, desired_shapes = (
            composition._conversation_archive_command_context()
        )
        run_id = composition.deps.events.conversation_archive_command_ownership[1]
        temporary_payload, _temporary_shape = next(
            iter(
                composition._temporary_conversation_archive_command_payloads(
                    payloads,
                    desired_shapes,
                    run_id,
                ).values()
            )
        )
        temporary_id = 81234567890123456
        registry.guild.append(
            FakeRemoteApplicationCommand(temporary_id, temporary_payload)
        )

        restarted = DiscordAppComposition(composition.deps)
        restarted_bot = commands.Bot(
            command_prefix="!",
            intents=discord.Intents.none(),
            help_command=None,
            application_id=bot.application_id,
        )
        restarted.register(restarted_bot)
        self._wire_archive_command_registry(restarted_bot, registry)
        _bot, _guild_id, _payloads, restarted_shapes = (
            restarted._conversation_archive_command_context()
        )
        restarted._load_conversation_archive_command_ownership(
            restarted_bot,
            guild_id,
            list(registry.guild),
            restarted_shapes,
            adopt_stale_temporary=True,
        )

        self.assertEqual(set(restarted._conversation_archive_owned_commands), {temporary_id})
        self.assertTrue(restarted._conversation_archive_command_recovery_required)
        asyncio.run(restarted._clear_conversation_archive_application_commands())
        self.assertEqual(registry.guild, [])
        restarted_bot.http.request.assert_not_awaited()
        restarted_bot.http.edit_guild_command.assert_not_awaited()
        restarted_bot.http.delete_guild_command.assert_awaited_once()
        ledger = json.loads(
            Path(
                restarted.deps.events.conversation_archive_command_ownership[0]
            ).read_text(encoding="utf-8")
        )
        self.assertEqual(ledger["commands"], [])

    def test_archive_command_recovery_never_adopts_ambiguous_run_temporary_shape(self) -> None:
        composition, bot, guild_id, _desired, globals_payload = (
            self._archive_command_fixture()
        )
        registry = FakeDiscordApplicationCommandRegistry(
            global_payloads=globals_payload,
        )
        self._wire_archive_command_registry(bot, registry)
        composition._persist_conversation_archive_command_ownership(bot, guild_id)
        _bot, _guild_id, payloads, desired_shapes = (
            composition._conversation_archive_command_context()
        )
        run_id = composition.deps.events.conversation_archive_command_ownership[1]
        temporary_payload, _temporary_shape = next(
            iter(
                composition._temporary_conversation_archive_command_payloads(
                    payloads,
                    desired_shapes,
                    run_id,
                ).values()
            )
        )
        registry.guild.extend(
            [
                FakeRemoteApplicationCommand(81234567890123456, temporary_payload),
                FakeRemoteApplicationCommand(81234567890123457, temporary_payload),
            ]
        )
        restarted = DiscordAppComposition(composition.deps)
        restarted_bot = commands.Bot(
            command_prefix="!",
            intents=discord.Intents.none(),
            help_command=None,
            application_id=bot.application_id,
        )
        restarted.register(restarted_bot)
        self._wire_archive_command_registry(restarted_bot, registry)
        _bot, _guild_id, _payloads, restarted_shapes = (
            restarted._conversation_archive_command_context()
        )

        with self.assertRaisesRegex(
            RuntimeError,
            "archive_command_ownership_stale_ambiguous",
        ):
            restarted._load_conversation_archive_command_ownership(
                restarted_bot,
                guild_id,
                list(registry.guild),
                restarted_shapes,
                adopt_stale_temporary=True,
            )

        restarted_bot.http.delete_guild_command.assert_not_awaited()
        self.assertFalse(restarted._conversation_archive_owned_commands)

    def test_archive_command_ledger_write_failure_cleans_known_returned_id(self) -> None:
        for failure in (OSError("ledger unavailable"), KeyboardInterrupt()):
            with self.subTest(failure=type(failure).__name__):
                composition, bot, _guild_id, _desired, globals_payload = (
                    self._archive_command_fixture()
                )
                registry = FakeDiscordApplicationCommandRegistry(
                    global_payloads=globals_payload,
                )
                self._wire_archive_command_registry(bot, registry)
                calls = 0

                def flaky_writer(*_args, **_kwargs):
                    nonlocal calls
                    calls += 1
                    if calls == 2:
                        raise failure

                expected = type(failure) if isinstance(failure, BaseException) else RuntimeError
                with patch(
                    "evelyn_core.discord_app_composition_runtime.write_command_ownership_ledger",
                    side_effect=flaky_writer,
                ):
                    if isinstance(failure, KeyboardInterrupt):
                        with self.assertRaises(expected):
                            asyncio.run(
                                composition._publish_conversation_archive_application_commands()
                            )
                    else:
                        with self.assertRaisesRegex(RuntimeError, "archive_command_publish_failed"):
                            asyncio.run(
                                composition._publish_conversation_archive_application_commands()
                            )

                self.assertEqual(bot.http.request.await_count, 1)
                self.assertEqual(bot.http.delete_guild_command.await_count, 1)
                self.assertFalse(registry.guild)
                self.assertFalse(composition._conversation_archive_owned_commands)

    def test_archive_command_publish_ambiguous_partial_failure_rolls_back_new_names(
        self,
    ) -> None:
        composition, bot, _guild_id, desired, globals_payload = (
            self._archive_command_fixture()
        )
        foreign = {
            "type": 1,
            "name": "기존서버명령",
            "description": "must remain unchanged",
            "options": [],
        }
        concurrent_name = list(desired)[2]
        registry = FakeDiscordApplicationCommandRegistry(
            guild_payloads=[foreign],
            global_payloads=globals_payload,
            fail_upsert_at=2,
            ambiguous_upsert_failure=True,
            concurrent_on_upsert_failure=desired[concurrent_name],
        )
        self._wire_archive_command_registry(bot, registry)

        with self.assertRaisesRegex(
            RuntimeError,
            "archive_command_publish_rollback_failed",
        ):
            asyncio.run(
                composition._publish_conversation_archive_application_commands()
            )

        self.assertEqual(bot.http.request.await_count, 2)
        self.assertEqual(bot.http.delete_guild_command.await_count, 2)
        self.assertEqual(
            [command.to_dict() for command in registry.guild],
            [foreign, desired[concurrent_name]],
        )
        self.assertEqual(
            {command.name for command in registry.guild}.intersection(desired),
            {concurrent_name},
        )
        self.assertTrue(composition._conversation_archive_command_recovery_required)
        self.assertFalse(composition._conversation_archive_owned_commands)
        self._assert_no_bulk_or_global_command_mutation(bot)

    def test_archive_command_selective_clear_preserves_foreign_and_globals(self) -> None:
        composition, bot, _guild_id, desired, globals_payload = (
            self._archive_command_fixture()
        )
        foreign = {
            "type": 1,
            "name": "기존서버명령",
            "description": "must remain unchanged",
            "options": [],
        }
        registry = FakeDiscordApplicationCommandRegistry(
            guild_payloads=[foreign],
            global_payloads=globals_payload,
        )
        self._wire_archive_command_registry(bot, registry)

        asyncio.run(composition._publish_conversation_archive_application_commands())
        asyncio.run(composition._clear_conversation_archive_application_commands())

        self.assertEqual(bot.http.delete_guild_command.await_count, 5)
        self.assertEqual(bot.http.request.await_count, 5)
        self.assertEqual(
            [command.to_dict() for command in registry.guild],
            [foreign],
        )
        self.assertEqual(len(registry.globals), 51)
        self._assert_no_bulk_or_global_command_mutation(bot)

    def test_archive_command_selective_clear_drift_is_no_delete(self) -> None:
        composition, bot, _guild_id, desired, globals_payload = (
            self._archive_command_fixture()
        )
        payloads = list(desired.values())
        registry = FakeDiscordApplicationCommandRegistry(
            global_payloads=globals_payload,
        )
        self._wire_archive_command_registry(bot, registry)
        asyncio.run(composition._publish_conversation_archive_application_commands())
        registry.guild[0]._payload["description"] = "drifted managed command"

        with self.assertRaisesRegex(RuntimeError, "archive_command_clear_drift"):
            asyncio.run(
                composition._clear_conversation_archive_application_commands()
            )

        bot.http.delete_guild_command.assert_not_awaited()
        self.assertEqual(bot.http.request.await_count, 5)
        self._assert_no_bulk_or_global_command_mutation(bot)

    def test_archive_command_publish_global_snapshot_drift_rolls_back(self) -> None:
        composition, bot, _guild_id, desired, globals_payload = (
            self._archive_command_fixture()
        )
        registry = FakeDiscordApplicationCommandRegistry(
            global_payloads=globals_payload,
            drift_second_global_fetch=True,
        )
        self._wire_archive_command_registry(bot, registry)

        with self.assertRaisesRegex(
            RuntimeError,
            "archive_command_publish_verification_failed",
        ):
            asyncio.run(
                composition._publish_conversation_archive_application_commands()
            )

        self.assertEqual(bot.http.request.await_count, 5)
        self.assertEqual(bot.http.delete_guild_command.await_count, 5)
        self.assertFalse(
            {command.name for command in registry.guild}.intersection(desired)
        )
        self.assertEqual(len(registry.globals), 51)
        self._assert_no_bulk_or_global_command_mutation(bot)

    def test_archive_command_publish_fails_closed_without_exact_guild(self) -> None:
        composition = make_composition(
            events=make_event_deps(conversation_archive_enabled=True),
            commands_deps=make_command_deps(conversation_archive_enabled=True),
        )
        bot = commands.Bot(
            command_prefix="!",
            intents=discord.Intents.none(),
            help_command=None,
            application_id=987654321098765432,
        )
        composition.register(bot)
        bot.http.request = AsyncMock(return_value={})

        with self.assertRaisesRegex(
            RuntimeError,
            "archive_command_publish_configuration_invalid",
        ):
            asyncio.run(
                composition._publish_conversation_archive_application_commands()
            )
        bot.http.request.assert_not_awaited()

    def test_human_voice_state_reaches_archive_before_bot_only_return(self) -> None:
        observer = AsyncMock()
        consent = Mock(return_value=True)
        ensure = AsyncMock()
        guild = SimpleNamespace(id=7, voice_client=None)
        member = SimpleNamespace(id=3, guild=guild, bot=False)
        events = make_event_deps(
            conversation_archive_enabled=True,
            conversation_participation_observer=observer,
            conversation_consent_current=consent,
            conversation_shared_session_registry=active_shared_sessions(),
            ensure_listening_voice_client=ensure,
        )
        state = SimpleNamespace(
            channel=SimpleNamespace(id=9),
            self_mute=False,
            mute=False,
            suppress=False,
            self_deaf=False,
            deaf=False,
        )

        composition = make_composition(events=events)

        async def scenario() -> None:
            await composition.on_voice_state_update(
                member,
                SimpleNamespace(channel=None),
                state,
            )
            await composition.on_voice_state_update(
                member,
                state,
                SimpleNamespace(**{**state.__dict__, "self_mute": True}),
            )

        asyncio.run(scenario())

        self.assertEqual(observer.await_count, 2)
        update = observer.await_args_list[0].args[0]
        self.assertEqual((update.guild_id, update.user_id), (7, 3))
        self.assertTrue(update.snapshot.eligible)
        self.assertEqual({row.kind.value for row in update.opened}, {"presence", "eligible"})
        muted_update = observer.await_args_list[1].args[0]
        self.assertFalse(muted_update.snapshot.eligible)
        self.assertEqual([row.kind.value for row in muted_update.closed], ["eligible"])
        self.assertEqual(consent.call_count, 2)
        ensure.assert_not_awaited()

    def test_mock_archive_flag_does_not_track_human_voice_state(self) -> None:
        observer = AsyncMock()
        events = make_event_deps(
            conversation_archive_enabled=Mock(name="truthy_archive_flag"),
            conversation_participation_observer=observer,
        )
        member = SimpleNamespace(
            id=3,
            guild=SimpleNamespace(id=7, voice_client=None),
            bot=False,
        )

        asyncio.run(
            make_composition(events=events).on_voice_state_update(
                member,
                SimpleNamespace(channel=None),
                SimpleNamespace(channel=SimpleNamespace(id=9)),
            )
        )

        observer.assert_not_awaited()

    def test_archive_voice_clock_is_clamped_when_wall_clock_moves_back(self) -> None:
        observer = AsyncMock()
        guild = SimpleNamespace(id=7, voice_client=None)
        member = SimpleNamespace(id=3, guild=guild, bot=False)
        joined = SimpleNamespace(
            channel=SimpleNamespace(id=9),
            self_mute=False,
            mute=False,
            suppress=False,
            self_deaf=False,
            deaf=False,
        )
        muted = SimpleNamespace(**{**joined.__dict__, "self_mute": True})
        composition = make_composition(
            events=make_event_deps(
                conversation_archive_enabled=True,
                conversation_participation_observer=observer,
                conversation_consent_current=lambda **_kwargs: True,
                conversation_shared_session_registry=active_shared_sessions(),
            )
        )

        async def scenario() -> None:
            await composition.on_voice_state_update(
                member,
                SimpleNamespace(channel=None),
                joined,
            )
            await composition.on_voice_state_update(member, joined, muted)

        with patch(
            "evelyn_core.discord_app_composition_runtime.time.time",
            side_effect=[100.0, 99.0],
        ):
            asyncio.run(scenario())

        self.assertEqual(observer.await_count, 2)
        self.assertEqual(
            [item.args[0].observed_at for item in observer.await_args_list],
            [100.0, 100.0],
        )

    def test_ready_and_disconnect_do_not_restore_a_stale_shared_session(self) -> None:
        observer = AsyncMock()
        guild = SimpleNamespace(
            id=7,
            voice_client=None,
            voice_channels=[],
            stage_channels=[],
        )
        voice_state = SimpleNamespace(
            channel=SimpleNamespace(id=9),
            self_mute=False,
            mute=False,
            suppress=False,
            self_deaf=False,
            deaf=False,
        )
        member = SimpleNamespace(
            id=3,
            guild=guild,
            bot=False,
            voice=voice_state,
        )
        guild.voice_channels = [SimpleNamespace(id=9, members=[member])]
        events = make_event_deps(
            bot_guilds=lambda: [guild],
            voice_client_type=SimpleNamespace,
            conversation_archive_enabled=True,
            conversation_participation_observer=observer,
            conversation_consent_current=lambda **_kwargs: True,
            conversation_shared_session_registry=active_shared_sessions(),
        )
        composition = make_composition(events=events)
        # Command registration/publishing is outside this direct on_ready lifecycle test.
        composition._conversation_archive_commands_published = True

        async def scenario() -> None:
            await composition.on_ready()
            await composition.on_disconnect()

        asyncio.run(scenario())

        observer.assert_not_awaited()
        self.assertIsNone(
            events.conversation_shared_session_registry.peek(guild_id=7)
        )

    def test_ready_does_not_rebuild_suppressed_stage_member_without_fresh_join(self) -> None:
        observer = AsyncMock()
        guild = SimpleNamespace(
            id=7,
            voice_client=None,
            voice_channels=[],
            stage_channels=[],
        )
        voice_state = SimpleNamespace(
            channel=SimpleNamespace(id=11),
            self_mute=False,
            mute=False,
            suppress=True,
            self_deaf=False,
            deaf=False,
        )
        member = SimpleNamespace(id=3, guild=guild, bot=False, voice=voice_state)
        guild.stage_channels = [SimpleNamespace(id=11, members=[member])]
        composition = make_composition(
            events=make_event_deps(
                bot_guilds=lambda: [guild],
                voice_client_type=SimpleNamespace,
                conversation_archive_enabled=True,
                conversation_participation_observer=observer,
                conversation_consent_current=lambda **_kwargs: True,
                conversation_shared_session_registry=active_shared_sessions(
                    voice_channel_id=11
                ),
            )
        )
        # Command registration/publishing is outside this direct on_ready lifecycle test.
        composition._conversation_archive_commands_published = True

        asyncio.run(composition.on_ready())

        observer.assert_not_awaited()

    def test_authorized_join_and_rejoin_open_only_after_complete_korean_notice(self) -> None:
        class VoiceClient:
            def __init__(self, channel) -> None:
                self.channel = channel

            @staticmethod
            def stop_listening() -> None:
                return None

            async def disconnect(self, **_kwargs) -> None:
                return None

        async def scenario():
            sessions = DiscordSharedSessionRegistry(ttl_seconds=3600.0)
            sessions.begin_generation("boot-1")
            guild = SimpleNamespace(id=7, voice_client=None)
            voice_channel = SimpleNamespace(id=9, name="Voice", members=[])

            async def ensure(_guild, channel):
                client = VoiceClient(channel)
                guild.voice_client = client
                return client

            ensure_voice = AsyncMock(side_effect=ensure)
            open_lease = AsyncMock()
            close_lease = AsyncMock()
            ctx = SimpleNamespace(
                guild=guild,
                channel=SimpleNamespace(id=8),
                author=SimpleNamespace(
                    id=5,
                    voice=SimpleNamespace(channel=voice_channel),
                ),
                send=AsyncMock(),
            )
            composition = make_composition(
                events=make_event_deps(
                    conversation_archive_enabled=True,
                    conversation_shared_session_registry=sessions,
                    conversation_participation_observer=AsyncMock(),
                    conversation_shared_session_open=open_lease,
                    conversation_shared_session_close=close_lease,
                ),
                commands_deps=make_command_deps(
                    conversation_archive_enabled=True,
                    conversation_archive_operator_authorized=lambda value: (
                        value.author.id == 5
                    ),
                    ensure_listening_voice_client=ensure_voice,
                ),
            )

            await composition.join_voice(ctx)
            first = sessions.current(
                guild_id=7,
                generation="boot-1",
                operator_user_id=5,
                text_channel_id=8,
                voice_channel_id=9,
            )
            await composition.rejoin_voice(ctx)
            current = sessions.current(
                guild_id=7,
                generation="boot-1",
                operator_user_id=5,
                text_channel_id=8,
                voice_channel_id=9,
            )
            return ctx, ensure_voice, open_lease, close_lease, first, current

        (
            ctx,
            ensure_voice,
            open_lease,
            close_lease,
            first,
            current,
        ) = asyncio.run(scenario())

        self.assertIsNotNone(first)
        self.assertIsNotNone(current)
        self.assertEqual(ensure_voice.await_count, 2)
        self.assertEqual(open_lease.await_count, 2)
        close_lease.assert_awaited_once_with(first)
        self.assertIs(open_lease.await_args_list[0].args[0], first)
        self.assertIs(open_lease.await_args_list[1].args[0], current)
        sent = [str(item.args[0]) for item in ctx.send.await_args_list]
        notices = [text for text in sent if "기록·전사 중" in text]
        self.assertEqual(len(notices), 2)
        for notice in notices:
            for required in (
                "확정 음성 전사(final STT)",
                "Minecraft",
                "30일",
                "원본 음성(raw audio)은 저장하지 않아",
                "/기록열람",
                "/기록삭제",
                "/기록동의",
                "/기록철회",
            ):
                self.assertIn(required, notice)

    def test_unauthorized_join_cannot_open_or_close_an_existing_session(self) -> None:
        sessions = active_shared_sessions()
        existing = sessions.peek(guild_id=7)
        ensure_voice = AsyncMock()
        ctx = SimpleNamespace(
            guild=SimpleNamespace(id=7, voice_client=None),
            channel=SimpleNamespace(id=8),
            author=SimpleNamespace(
                id=6,
                voice=SimpleNamespace(
                    channel=SimpleNamespace(id=9, name="Voice")
                ),
            ),
            send=AsyncMock(),
        )
        composition = make_composition(
            events=make_event_deps(
                conversation_archive_enabled=True,
                conversation_shared_session_registry=sessions,
            ),
            commands_deps=make_command_deps(
                conversation_archive_enabled=True,
                conversation_archive_operator_authorized=lambda _ctx: False,
                ensure_listening_voice_client=ensure_voice,
            ),
        )

        asyncio.run(composition.join_voice(ctx))

        ensure_voice.assert_not_awaited()
        self.assertEqual(sessions.peek(guild_id=7), existing)
        self.assertNotIn(
            "기록·전사 중",
            " ".join(str(item.args[0]) for item in ctx.send.await_args_list),
        )

    def test_notice_failure_closes_previous_session_and_never_opens_successor(self) -> None:
        sessions = active_shared_sessions()
        voice_channel = SimpleNamespace(id=9, name="Voice", members=[])
        ctx = SimpleNamespace(
            guild=SimpleNamespace(id=7, voice_client=None),
            channel=SimpleNamespace(id=8),
            author=SimpleNamespace(
                id=5,
                voice=SimpleNamespace(channel=voice_channel),
            ),
            send=AsyncMock(side_effect=[OSError("send failed"), None]),
        )
        composition = make_composition(
            events=make_event_deps(
                conversation_archive_enabled=True,
                conversation_shared_session_registry=sessions,
                conversation_participation_observer=AsyncMock(),
            ),
            commands_deps=make_command_deps(
                conversation_archive_enabled=True,
                conversation_archive_operator_authorized=lambda _ctx: True,
                ensure_listening_voice_client=AsyncMock(return_value=object()),
            ),
        )

        asyncio.run(composition.join_voice(ctx))

        self.assertIsNone(sessions.peek(guild_id=7))

    def test_shared_session_ttl_and_explicit_leave_close_voice_admission(self) -> None:
        async def scenario():
            sessions = DiscordSharedSessionRegistry(ttl_seconds=0.02)
            sessions.begin_generation("boot-1")
            voice_channel = SimpleNamespace(id=9, name="Voice", members=[])

            class VoiceClient:
                channel = voice_channel

                @staticmethod
                def stop_listening() -> None:
                    return None

                async def disconnect(self, **_kwargs) -> None:
                    return None

            client = VoiceClient()
            guild = SimpleNamespace(id=7, voice_client=client)
            ctx = SimpleNamespace(
                guild=guild,
                channel=SimpleNamespace(id=8),
                author=SimpleNamespace(
                    id=5,
                    voice=SimpleNamespace(channel=voice_channel),
                ),
                send=AsyncMock(),
            )
            composition = make_composition(
                events=make_event_deps(
                    conversation_archive_enabled=True,
                    conversation_shared_session_registry=sessions,
                    conversation_participation_observer=AsyncMock(),
                ),
                commands_deps=make_command_deps(
                    conversation_archive_enabled=True,
                    conversation_archive_operator_authorized=lambda _ctx: True,
                    ensure_listening_voice_client=AsyncMock(return_value=client),
                    mark_voice_manual_disconnect=Mock(),
                ),
            )
            await composition.join_voice(ctx)
            self.assertIsNotNone(sessions.current(guild_id=7))
            await asyncio.sleep(0.06)
            expired = sessions.peek(guild_id=7)
            await composition.join_voice(ctx)
            self.assertIsNotNone(sessions.current(guild_id=7))
            await composition.leave_voice(ctx)
            return sessions, expired

        sessions, expired = asyncio.run(scenario())

        self.assertIsNone(expired)
        self.assertIsNone(sessions.peek(guild_id=7))

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
        self.assertIn(
            "ttl_seconds=CONVERSATION_ARCHIVE_SHARED_SESSION_TTL_SEC",
            main_source,
        )
        for callback in (
            "archive_user_text",
            "archive_final_transcript",
            "archive_assistant_text",
            "confirm_assistant_delivery",
            "open_shared_session_lease",
            "close_shared_session_lease",
            "archive_autonomy_grant",
            "archive_minecraft_command",
            "authorize_voice_capture",
            "observe_participation",
            "consent_current",
            "set_consent",
            "capture_feedback",
            "purge_feedback_targets",
            "begin_generation",
        ):
            self.assertIn(f"conversation_archive_gate.{callback}", main_source)
        self.assertIn("in ALLOWED_RESTART_USER_IDS", main_source)
        self.assertNotIn("@bot.event", main_source)
        self.assertNotIn("@bot.command", main_source)
        self.assertNotIn("globals()", runtime_source)


if __name__ == "__main__":
    unittest.main()
