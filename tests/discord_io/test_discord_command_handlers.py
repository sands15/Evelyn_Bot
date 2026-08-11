from __future__ import annotations

import asyncio
import sys
import unittest
from pathlib import Path
from types import SimpleNamespace


REPO_ROOT = next(path for path in Path(__file__).resolve().parents if (path / "main.py").exists())
RUNTIME_ROOT = REPO_ROOT / "evelyn_core" / "runtime"
if str(RUNTIME_ROOT) not in sys.path:
    sys.path.insert(0, str(RUNTIME_ROOT))

from evelyn_core.discord_command_handlers import (  # noqa: E402
    handle_control_command_error,
    handle_autonomy_start_command,
    handle_autonomy_status_command,
    handle_autonomy_stop_command,
    handle_channel_setting_command,
    handle_evelyn_page_command,
    handle_join_voice_command,
    handle_leave_voice_command,
    handle_minecraft_connect_command,
    handle_minecraft_disconnect_command,
    handle_minecraft_goal_command,
    handle_minecraft_status_command,
    handle_prefix_command,
    handle_rejoin_voice_command,
    handle_reset_guild_memory_command,
    handle_restart_bot_command,
    make_control_command_authorized_checker,
    handle_shutdown_bot_command,
    handle_status_command,
)
from evelyn_core.autonomy_authorization import (  # noqa: E402
    ASSISTANT_AUTONOMY_ACTIONS,
)
from evelyn_core.minecraft_action_contract import (  # noqa: E402
    MINECRAFT_ROUTE_ACTIONS,
)


class FakeContext:
    def __init__(self, *, guild=None, voice_channel=None, content: str = "cmd") -> None:
        self.sent: list[str] = []
        self.guild = guild
        self.author = SimpleNamespace(id=3, voice=SimpleNamespace(channel=voice_channel) if voice_channel else None)
        self.message = SimpleNamespace(content=content)

    async def send(self, text: str) -> None:
        self.sent.append(text)


class FakeVoiceClient:
    def __init__(self) -> None:
        self.stopped = False
        self.disconnected: list[bool | None] = []

    def stop_listening(self) -> None:
        self.stopped = True

    async def disconnect(self, force: bool | None = None) -> None:
        self.disconnected.append(force)


class DiscordCommandHandlerTests(unittest.TestCase):
    def test_control_command_authorized_checker_accepts_allowlisted_or_admin_users(self) -> None:
        checker = make_control_command_authorized_checker(allowed_user_ids={7})
        allowed_ctx = FakeContext()
        allowed_ctx.author.id = 7
        self.assertTrue(checker(allowed_ctx))

        denied_ctx = FakeContext()
        denied_ctx.author.id = 8
        self.assertFalse(checker(denied_ctx))

        admin_ctx = FakeContext()
        admin_ctx.author = SimpleNamespace(id=9, guild_permissions=SimpleNamespace(administrator=True), voice=None)
        self.assertTrue(checker(admin_ctx))

    def test_control_command_error_handler_shows_message_for_check_failure(self) -> None:
        ctx = FakeContext()
        import evelyn_core.discord_command_handlers as handlers

        asyncio.run(handle_control_command_error(ctx, handlers.commands.CheckFailure("no")))
        self.assertEqual(ctx.sent, ["이 명령은 허용된 Discord ID이거나 서버 관리자 권한이 있어야 쓸 수 있어."])

        class _Error(Exception):
            pass

        with self.assertRaises(_Error):
            asyncio.run(handle_control_command_error(ctx, _Error("boom")))

    def test_join_voice_command_connects_or_reports_missing_channel(self) -> None:
        channel = SimpleNamespace(name="General")
        guild = SimpleNamespace(id=1)
        missing_ctx = FakeContext(guild=guild)
        ok_ctx = FakeContext(guild=guild, voice_channel=channel)
        calls: list[tuple[object, object]] = []

        async def ensure(guild_arg, channel_arg):
            calls.append((guild_arg, channel_arg))
            return object()

        asyncio.run(handle_join_voice_command(missing_ctx, ensure_listening_voice_client=ensure))
        asyncio.run(handle_join_voice_command(ok_ctx, ensure_listening_voice_client=ensure))

        self.assertEqual(missing_ctx.sent, ["먼저 음성 채널에 들어가줘."])
        self.assertEqual(ok_ctx.sent, ["🔊 General에 들어왔어. 이제 듣고 말할게."])
        self.assertEqual(calls, [(guild, channel)])

    def test_rejoin_voice_command_stops_existing_client_then_reconnects(self) -> None:
        channel = SimpleNamespace(name="General")
        vc = FakeVoiceClient()
        guild = SimpleNamespace(id=1, voice_client=vc)
        ctx = FakeContext(guild=guild, voice_channel=channel)

        async def ensure(_guild, _channel):
            return object()

        asyncio.run(handle_rejoin_voice_command(ctx, ensure_listening_voice_client=ensure))

        self.assertTrue(vc.stopped)
        self.assertEqual(vc.disconnected, [True])
        self.assertEqual(ctx.sent, ["🔄 다시 붙었어. 이제 계속 들을게."])

    def test_leave_voice_command_marks_manual_disconnect(self) -> None:
        vc = FakeVoiceClient()
        guild = SimpleNamespace(id=1, voice_client=vc)
        ctx = FakeContext(guild=guild)
        marks: list[tuple[object, str]] = []

        asyncio.run(handle_leave_voice_command(ctx, mark_manual_disconnect=lambda guild_arg, *, reason: marks.append((guild_arg, reason))))

        self.assertTrue(vc.stopped)
        self.assertEqual(vc.disconnected, [None])
        self.assertEqual(marks, [(guild, "leave_command")])
        self.assertEqual(ctx.sent, ["👋 나갔어."])

    def test_minecraft_connect_command_sends_reply(self) -> None:
        guild = SimpleNamespace(id=1)
        ctx = FakeContext(guild=guild, content="마크접속")

        async def enable(
            guild_id: int,
            *,
            issuer_ref: str,
            source: str,
        ):
            self.assertEqual(guild_id, 1)
            self.assertEqual(issuer_ref, "discord_user:3")
            self.assertEqual(source, "discord_command")
            return {
                "connected": True,
                "outcome_verified": True,
                "outcome_code": "minecraft_connected",
            }

        route_enabled: list[int] = []

        async def enable_route(guild_id: int) -> bool:
            route_enabled.append(guild_id)
            return True

        asyncio.run(
            handle_minecraft_connect_command(
                ctx,
                enable_minecraft_mode=enable,
                enable_minecraft_autonomy_route=enable_route,
                build_reply=lambda observed: f"connect:{observed['connected']}",
                guild_only_message=lambda: "guild only",
            )
        )

        self.assertEqual(ctx.sent, ["connect:True"])
        self.assertEqual(route_enabled, [1])

    def test_minecraft_disconnect_requires_verified_stop(self) -> None:
        guild = SimpleNamespace(id=1)
        success_ctx = FakeContext(guild=guild, content="마크종료")
        failure_ctx = FakeContext(guild=guild, content="마크종료")

        async def verified_stop(_guild_id: int):
            return {
                "running": False,
                "connected": False,
                "outcome_verified": True,
                "outcome_code": "minecraft_stopped",
            }

        async def unverified_stop(_guild_id: int):
            return {"running": False, "connected": False}

        kwargs = {
            "guild_only_message": lambda: "guild only",
            "disable_minecraft_autonomy_route": (
                lambda _guild_id: asyncio.sleep(0, result=True)
            ),
        }
        asyncio.run(
            handle_minecraft_disconnect_command(
                success_ctx,
                disable_minecraft_mode=verified_stop,
                **kwargs,
            )
        )
        asyncio.run(
            handle_minecraft_disconnect_command(
                failure_ctx,
                disable_minecraft_mode=unverified_stop,
                **kwargs,
            )
        )

        self.assertIn("중지했어", success_ctx.sent[0])
        self.assertEqual(
            failure_ctx.sent,
            [
                "❌ 마인크래프트 연결을 종료하지 못했어. 현재 상태를 다시 "
                "확인해줘. (minecraft_disconnect_failed)"
            ],
        )

    def test_minecraft_connect_does_not_enable_route_from_unverified_echo(
        self,
    ) -> None:
        ctx = FakeContext(guild=SimpleNamespace(id=1))
        route_calls: list[int] = []

        async def unverified_connect(*_args, **_kwargs):
            return {
                "connected": True,
                "goal": "find food",
            }

        async def enable_route(guild_id: int) -> bool:
            route_calls.append(guild_id)
            return True

        asyncio.run(
            handle_minecraft_connect_command(
                ctx,
                enable_minecraft_mode=unverified_connect,
                enable_minecraft_autonomy_route=enable_route,
                build_reply=lambda _observed: "observed",
                guild_only_message=lambda: "guild only",
            )
        )

        self.assertEqual(route_calls, [])
        self.assertEqual(ctx.sent, ["observed"])

    def test_minecraft_status_command_sends_failure_reply(self) -> None:
        guild = SimpleNamespace(id=1)
        ctx = FakeContext(guild=guild, content="마크상태")

        class Client:
            async def status(self):
                raise RuntimeError("down")

        asyncio.run(
            handle_minecraft_status_command(
                ctx,
                get_minecraft_client=lambda: Client(),
                get_minecraft_world_lease_status=lambda: {
                    "state": "authorization_required"
                },
                build_reply=lambda status: "status",
                guild_only_message=lambda: "guild only",
            )
        )

        expected = (
            "❌ 마인크래프트 상태를 확인하지 못했어. 잠깐 뒤에 다시 "
            "시도해줘. (minecraft_status_failed)"
        )
        self.assertEqual(ctx.sent, [expected])

    def test_minecraft_goal_command_handles_missing_and_updated_goal(self) -> None:
        guild = SimpleNamespace(id=1)
        missing_ctx = FakeContext(guild=guild, content="마크목표")
        goal_ctx = FakeContext(guild=guild, content="마크목표 diamond")

        async def set_goal(guild_id: int, goal: str):
            self.assertEqual(guild_id, 1)
            return {"stage": goal}

        kwargs = dict(
            set_minecraft_goal=set_goal,
            build_missing_reply=lambda: "missing goal",
            build_updated_reply=lambda goal, status: f"goal:{goal}:{status['stage']}",
            guild_only_message=lambda: "guild only",
        )
        asyncio.run(handle_minecraft_goal_command(missing_ctx, goal="", **kwargs))
        asyncio.run(handle_minecraft_goal_command(goal_ctx, goal=" diamond ", **kwargs))

        self.assertEqual(missing_ctx.sent, ["missing goal"])
        self.assertEqual(goal_ctx.sent, ["goal:diamond:diamond"])

    def test_prefix_command_reads_resets_and_saves_prefix(self) -> None:
        guild = SimpleNamespace(id=1)
        show_ctx = FakeContext(guild=guild)
        reset_ctx = FakeContext(guild=guild)
        save_ctx = FakeContext(guild=guild)
        saved: list[tuple[int, str]] = []

        kwargs = dict(
            default_prefix="!",
            get_guild_command_prefix=lambda guild_id: "?",
            save_guild_command_prefix=lambda guild_id, prefix: saved.append((guild_id, prefix)) or prefix,
            build_current_reply=lambda prefix: f"current:{prefix}",
            build_reset_reply=lambda prefix: f"reset:{prefix}",
            build_saved_reply=lambda prefix: f"saved:{prefix}",
            guild_only_message=lambda: "guild only",
        )
        asyncio.run(handle_prefix_command(show_ctx, None, **kwargs))
        asyncio.run(handle_prefix_command(reset_ctx, "default", **kwargs))
        asyncio.run(handle_prefix_command(save_ctx, "$", **kwargs))

        self.assertEqual(show_ctx.sent, ["current:?"])
        self.assertEqual(reset_ctx.sent, ["reset:!"])
        self.assertEqual(save_ctx.sent, ["saved:$"])
        self.assertEqual(saved, [(1, "!"), (1, "$")])

    def test_autonomy_commands_start_stop_and_report_status(self) -> None:
        guild = SimpleNamespace(id=1)
        start_ctx = FakeContext(guild=guild)
        stop_ctx = FakeContext(guild=guild)
        status_ctx = FakeContext(guild=guild)
        calls: list[str] = []

        class Engine:
            state = SimpleNamespace(status="running")

            async def start(self):
                calls.append("start")

            async def stop(self):
                calls.append("stop")

        class Router:
            def is_domain_enabled(self, name: str) -> bool:
                return name == "minecraft"

        engines = {1: Engine()}
        authorizations: list[tuple[object, ...]] = []
        asyncio.run(
            handle_autonomy_start_command(
                start_ctx,
                autonomy_enabled=True,
                get_or_create_autonomy_engine=lambda guild_id: engines[guild_id],
                is_minecraft_autonomy_route_enabled=lambda _guild_id: True,
                enable_minecraft_autonomy_route=(
                    lambda _guild_id: asyncio.sleep(
                        0,
                        result=(calls.append("route_enable") or True),
                    )
                ),
                grant_autonomy_authorization=(
                    lambda guild_id, issuer_ref, *, scopes: (
                        authorizations.append(
                            ("grant", guild_id, issuer_ref, tuple(scopes))
                        )
                        or {"ok": True}
                    )
                ),
                revoke_autonomy_authorization=(
                    lambda guild_id, reason_code: authorizations.append(
                        ("revoke", guild_id, reason_code)
                    )
                ),
                guild_only_message=lambda: "guild only",
            )
        )
        asyncio.run(
            handle_autonomy_stop_command(
                stop_ctx,
                autonomy_engines=engines,
                revoke_autonomy_authorization=(
                    lambda guild_id, reason_code: authorizations.append(
                        ("revoke", guild_id, reason_code)
                    )
                ),
                guild_only_message=lambda: "guild only",
            )
        )
        asyncio.run(
            handle_autonomy_status_command(
                status_ctx,
                autonomy_engines=engines,
                get_routed_autonomy_executor=lambda guild_id: Router(),
                get_autonomy_authorization_status=lambda: {
                    "state": "ready",
                    "auditReady": True,
                },
                build_reply=lambda state, *, minecraft_enabled, authorization, guild_id: (
                    f"status:{state.status}:{minecraft_enabled}:"
                    f"{authorization['state']}:{guild_id}"
                ),
                guild_only_message=lambda: "guild only",
            )
        )

        self.assertEqual(
            calls,
            ["stop", "route_enable", "start", "stop"],
        )
        self.assertEqual(
            authorizations,
            [
                (
                    "grant",
                    1,
                    "discord_user:3",
                    (*ASSISTANT_AUTONOMY_ACTIONS, *MINECRAFT_ROUTE_ACTIONS),
                ),
                ("revoke", 1, "explicit_autonomy_stop"),
            ],
        )
        self.assertEqual(start_ctx.sent, ["🤖 자율 행동 루프를 시작했어."])
        self.assertEqual(stop_ctx.sent, ["🛑 자율 행동 루프를 멈췄어."])
        self.assertEqual(
            status_ctx.sent,
            ["status:running:True:ready:1"],
        )

    def test_autonomy_start_respects_feature_flag(self) -> None:
        guild = SimpleNamespace(id=1)
        ctx = FakeContext(guild=guild)
        grants: list[int] = []

        asyncio.run(
            handle_autonomy_start_command(
                ctx,
                autonomy_enabled=False,
                get_or_create_autonomy_engine=lambda _guild_id: (
                    self.fail("engine must not be created")
                ),
                is_minecraft_autonomy_route_enabled=lambda _guild_id: False,
                enable_minecraft_autonomy_route=(
                    lambda _guild_id: asyncio.sleep(0, result=False)
                ),
                grant_autonomy_authorization=(
                    lambda guild_id, _issuer_ref, *, scopes: (
                        grants.append(guild_id) or {"ok": True}
                    )
                ),
                revoke_autonomy_authorization=lambda *_args, **_kwargs: None,
                guild_only_message=lambda: "guild only",
            )
        )

        self.assertEqual(
            ctx.sent,
            ["자율 행동 기능이 설정에서 비활성화되어 있어."],
        )
        self.assertEqual(grants, [])

    def test_autonomy_start_without_verified_minecraft_route_grants_assistant_only(
        self,
    ) -> None:
        ctx = FakeContext(guild=SimpleNamespace(id=9))
        granted_scopes: list[tuple[str, ...]] = []

        class Engine:
            state = SimpleNamespace(status="idle", enabled=False)

            async def stop(self) -> None:
                return None

            async def start(self) -> None:
                return None

        asyncio.run(
            handle_autonomy_start_command(
                ctx,
                autonomy_enabled=True,
                get_or_create_autonomy_engine=lambda _guild_id: Engine(),
                is_minecraft_autonomy_route_enabled=lambda _guild_id: False,
                enable_minecraft_autonomy_route=(
                    lambda _guild_id: asyncio.sleep(0, result=False)
                ),
                grant_autonomy_authorization=(
                    lambda _guild_id, _issuer_ref, *, scopes: (
                        granted_scopes.append(tuple(scopes))
                        or {"ok": True}
                    )
                ),
                revoke_autonomy_authorization=(
                    lambda *_args, **_kwargs: None
                ),
                guild_only_message=lambda: "guild only",
            )
        )

        self.assertEqual(granted_scopes, [ASSISTANT_AUTONOMY_ACTIONS])
        self.assertNotIn(
            "minecraft:retreat",
            granted_scopes[0],
        )
        self.assertEqual(ctx.sent, ["🤖 자율 행동 루프를 시작했어."])

    def test_autonomy_start_revalidates_sticky_minecraft_route_before_scope(
        self,
    ) -> None:
        ctx = FakeContext(guild=SimpleNamespace(id=11))
        granted_scopes: list[tuple[str, ...]] = []
        revalidated: list[int] = []

        class Engine:
            state = SimpleNamespace(status="idle", enabled=False)

            async def stop(self) -> None:
                return None

            async def start(self) -> None:
                return None

        async def revalidate(guild_id: int) -> bool:
            revalidated.append(guild_id)
            return False

        asyncio.run(
            handle_autonomy_start_command(
                ctx,
                autonomy_enabled=True,
                get_or_create_autonomy_engine=lambda _guild_id: Engine(),
                is_minecraft_autonomy_route_enabled=lambda _guild_id: True,
                enable_minecraft_autonomy_route=revalidate,
                grant_autonomy_authorization=(
                    lambda _guild_id, _issuer_ref, *, scopes: (
                        granted_scopes.append(tuple(scopes))
                        or {"ok": True}
                    )
                ),
                revoke_autonomy_authorization=lambda *_args, **_kwargs: None,
                guild_only_message=lambda: "guild only",
            )
        )

        self.assertEqual(revalidated, [11])
        self.assertEqual(granted_scopes, [ASSISTANT_AUTONOMY_ACTIONS])

    def test_autonomy_restart_finishes_old_cleanup_before_route_enable(self) -> None:
        ctx = FakeContext(guild=SimpleNamespace(id=12))
        events: list[str] = []

        class MinecraftChild:
            connected = False

        child = MinecraftChild()

        class Engine:
            state = SimpleNamespace(
                status="authorization_required",
                enabled=False,
            )
            old_task_alive = True

            async def stop(self) -> None:
                events.append("stop")
                self.old_task_alive = False
                child.connected = False

            async def start(self) -> None:
                events.append("start")
                if self.old_task_alive:
                    child.connected = False

        engine = Engine()

        async def enable_route(_guild_id: int) -> bool:
            events.append("route_enable")
            child.connected = True
            return True

        def grant(_guild_id: int, _issuer_ref: str, *, scopes) -> dict:
            del scopes
            events.append("grant")
            return {"ok": True}

        asyncio.run(
            handle_autonomy_start_command(
                ctx,
                autonomy_enabled=True,
                get_or_create_autonomy_engine=lambda _guild_id: engine,
                is_minecraft_autonomy_route_enabled=lambda _guild_id: True,
                enable_minecraft_autonomy_route=enable_route,
                grant_autonomy_authorization=grant,
                revoke_autonomy_authorization=lambda *_args, **_kwargs: None,
                guild_only_message=lambda: "guild only",
            )
        )

        self.assertEqual(events, ["stop", "route_enable", "grant", "start"])
        self.assertTrue(child.connected)
        self.assertEqual(ctx.sent, ["🤖 자율 행동 루프를 시작했어."])

    def test_autonomy_restart_stop_failure_aborts_before_route_enable(self) -> None:
        ctx = FakeContext(guild=SimpleNamespace(id=13))
        events: list[str] = []

        class Engine:
            state = SimpleNamespace(
                status="authorization_required",
                enabled=False,
            )

            async def stop(self) -> None:
                events.append("stop")
                raise RuntimeError("private cleanup failure")

            async def start(self) -> None:
                events.append("start")

        async def enable_route(_guild_id: int) -> bool:
            events.append("route_enable")
            return True

        def grant(_guild_id: int, _issuer_ref: str, *, scopes) -> dict:
            del scopes
            events.append("grant")
            return {"ok": True}

        def revoke(_guild_id: int, *, reason_code: str) -> None:
            events.append(f"revoke:{reason_code}")

        asyncio.run(
            handle_autonomy_start_command(
                ctx,
                autonomy_enabled=True,
                get_or_create_autonomy_engine=lambda _guild_id: Engine(),
                is_minecraft_autonomy_route_enabled=lambda _guild_id: True,
                enable_minecraft_autonomy_route=enable_route,
                grant_autonomy_authorization=grant,
                revoke_autonomy_authorization=revoke,
                guild_only_message=lambda: "guild only",
            )
        )

        self.assertEqual(events, ["stop", "revoke:start_failed"])
        self.assertEqual(
            ctx.sent,
            ["❌ 자율 행동 시작에 실패했고 승인은 폐기했어."],
        )

    def test_command_failures_do_not_expose_exception_text(self) -> None:
        secret = (
            "Bearer discord-secret http://internal:8765 "
            "C:\\Users\\Admin\\private.txt"
        )
        guild = SimpleNamespace(id=1, voice_client=None)
        voice_channel = SimpleNamespace(name="General")
        join_ctx = FakeContext(
            guild=guild,
            voice_channel=voice_channel,
        )
        autonomy_ctx = FakeContext(guild=guild)
        minecraft_ctx = FakeContext(
            guild=guild,
            content="마크접속",
        )
        logged: list[tuple[object, ...]] = []

        async def fail(*_args, **_kwargs):
            raise RuntimeError(secret)

        class Engine:
            async def stop(self):
                raise RuntimeError(secret)

        asyncio.run(
            handle_join_voice_command(
                join_ctx,
                ensure_listening_voice_client=fail,
                log=lambda *args: logged.append(args),
            )
        )
        asyncio.run(
            handle_autonomy_stop_command(
                autonomy_ctx,
                autonomy_engines={1: Engine()},
                revoke_autonomy_authorization=(
                    lambda *_args, **_kwargs: None
                ),
                guild_only_message=lambda: "guild only",
                log=lambda *args: logged.append(args),
            )
        )
        asyncio.run(
            handle_minecraft_connect_command(
                minecraft_ctx,
                enable_minecraft_mode=fail,
                enable_minecraft_autonomy_route=fail,
                build_reply=lambda _observed: "connected",
                guild_only_message=lambda: "guild only",
                log=lambda *args: logged.append(args),
            )
        )

        public_text = "\n".join(
            join_ctx.sent
            + autonomy_ctx.sent
            + minecraft_ctx.sent
        )
        self.assertNotIn("discord-secret", public_text)
        self.assertNotIn("internal:8765", public_text)
        self.assertNotIn("private.txt", public_text)
        self.assertIn("voice_connect_failed", public_text)
        self.assertIn("autonomy_stop_failed", public_text)
        self.assertIn("minecraft_connect_failed", public_text)
        self.assertTrue(logged)

    def test_autonomy_status_reports_authorization_without_engine(
        self,
    ) -> None:
        ctx = FakeContext(guild=SimpleNamespace(id=7))

        asyncio.run(
            handle_autonomy_status_command(
                ctx,
                autonomy_engines={},
                get_routed_autonomy_executor=lambda _guild_id: None,
                get_autonomy_authorization_status=lambda: {
                    "state": "authorization_required",
                    "auditReady": True,
                },
                build_reply=lambda state, **kwargs: (
                    f"{state}:{kwargs['authorization']['state']}:"
                    f"{kwargs['guild_id']}"
                ),
                guild_only_message=lambda: "guild only",
            )
        )

        self.assertEqual(
            ctx.sent,
            ["None:authorization_required:7"],
        )

    def test_channel_setting_command_lists_adds_removes_and_shows_usage(self) -> None:
        channel = SimpleNamespace(id=10, mention="#general")
        guild = SimpleNamespace(id=1, get_channel=lambda channel_id: channel if channel_id == 10 else None)
        list_ctx = FakeContext(guild=guild)
        add_ctx = FakeContext(guild=guild)
        remove_ctx = FakeContext(guild=guild)
        usage_ctx = FakeContext(guild=guild)
        added: list[tuple[int, str, int]] = []
        removed: list[tuple[int, str, int]] = []

        kwargs = dict(
            setting_key="observe_channel_ids",
            label="label",
            add_success="add:{channel.mention}:{count}",
            remove_success="remove:{channel.mention}:{count}",
            normalize_action=lambda action: str(action or "목록").strip().lower(),
            get_channel_ids=lambda guild_id: [10],
            add_channel_setting=lambda guild_id, key, channel_id: added.append((guild_id, key, channel_id)) or [10, 11],
            remove_channel_setting=lambda guild_id, key, channel_id: removed.append((guild_id, key, channel_id)) or [],
            get_guild_command_prefix=lambda guild_id: "!",
            build_list_reply=lambda **args: f"list:{args['label']}:{list(args['channel_ids'])}",
            build_usage_reply=lambda prefix: f"usage:{prefix}",
            guild_only_message=lambda: "guild only",
        )
        asyncio.run(handle_channel_setting_command(list_ctx, "list", None, **kwargs))
        asyncio.run(handle_channel_setting_command(add_ctx, "add", channel, **kwargs))
        asyncio.run(handle_channel_setting_command(remove_ctx, "remove", channel, **kwargs))
        asyncio.run(handle_channel_setting_command(usage_ctx, "bad", None, **kwargs))

        self.assertEqual(list_ctx.sent, ["list:label:[10]"])
        self.assertEqual(add_ctx.sent, ["add:#general:2"])
        self.assertEqual(remove_ctx.sent, ["remove:#general:0"])
        self.assertEqual(usage_ctx.sent, ["usage:!"])
        self.assertEqual(added, [(1, "observe_channel_ids", 10)])
        self.assertEqual(removed, [(1, "observe_channel_ids", 10)])

    def test_restart_and_shutdown_commands_schedule_process_tasks(self) -> None:
        ctx = FakeContext(guild=SimpleNamespace(id=1))
        shutdown_ctx = FakeContext(guild=SimpleNamespace(id=1))
        tasks: list[object] = []

        async def restart_process():
            return None

        async def shutdown_process():
            return None

        asyncio.run(handle_restart_bot_command(ctx, create_task=tasks.append, restart_bot_process=restart_process))
        asyncio.run(
            handle_shutdown_bot_command(
                shutdown_ctx,
                schedule_stack_shutdown=lambda: False,
                create_task=tasks.append,
                shutdown_bot_process=shutdown_process,
            )
        )

        self.assertEqual(ctx.sent, ["🔄 봇을 재시작할게. 잠깐만 기다려줘."])
        self.assertEqual(shutdown_ctx.sent, ["Full-stack shutdown helper failed, so only the bot process is stopping."])
        self.assertEqual(len(tasks), 2)
        for task in tasks:
            task.close()

    def test_status_and_page_commands_send_runtime_summaries(self) -> None:
        channel = SimpleNamespace(name="General")
        vc = SimpleNamespace(channel=channel, is_listening=lambda: True)
        ctx = FakeContext(guild=SimpleNamespace(id=1, voice_client=vc))
        page_ctx = FakeContext(guild=SimpleNamespace(id=1))

        asyncio.run(
            handle_status_command(
                ctx,
                build_reply=lambda **kwargs: f"status:{kwargs['voice_channel_name']}:{kwargs['listening']}:{kwargs['opus_runtime_value']}",
                model_name="main",
                router_model_name="router",
                summary_model_name="summary",
                stt_model_name="stt",
                voice_debug_save_audio=False,
                vad_enabled=True,
                vad_provider="silero",
                opus_runtime_value=True,
            )
        )
        asyncio.run(handle_evelyn_page_command(page_ctx, resolve_page_url=lambda: "https://example.test/evelyn"))

        self.assertEqual(ctx.sent, ["status:General:True:True"])
        self.assertEqual(page_ctx.sent, ["이블린 페이지: https://example.test/evelyn"])

    def test_reset_guild_memory_command_resets_runtime_and_removes_existing_dir(self) -> None:
        class FakeMemoryDir:
            def __init__(self, path: str) -> None:
                self.path = path

            def exists(self) -> bool:
                return True

            def __repr__(self) -> str:
                return self.path

        class FakeMemoryRoot:
            def __truediv__(self, child: str) -> FakeMemoryDir:
                return FakeMemoryDir(child)

        guild = SimpleNamespace(id=7, name="Home")
        ctx = FakeContext(guild=guild)
        reset: list[int] = []
        removed: list[str] = []

        asyncio.run(
            handle_reset_guild_memory_command(
                ctx,
                memory_root=FakeMemoryRoot(),
                reset_guild_runtime_state=reset.append,
                remove_tree=lambda path: removed.append(repr(path)),
                get_guild_command_prefix=lambda guild_id: "!",
                build_reply=lambda **kwargs: f"reset:{kwargs['guild_name']}:{kwargs['current_prefix']}",
                guild_only_message=lambda: "guild only",
            )
        )

        self.assertEqual(reset, [7])
        self.assertEqual(removed, ["guild_7"])
        self.assertEqual(ctx.sent, ["reset:Home:!"])

    def test_reset_guild_memory_reports_live_autonomy_refresh(self) -> None:
        ctx = FakeContext(guild=SimpleNamespace(id=7, name="Home"))
        removed: list[object] = []

        def blocked_reset(_guild_id: int) -> None:
            raise RuntimeError("autonomy_cognitive_refresh_inflight")

        asyncio.run(
            handle_reset_guild_memory_command(
                ctx,
                memory_root=Path("unused"),
                reset_guild_runtime_state=blocked_reset,
                remove_tree=removed.append,
                get_guild_command_prefix=lambda _guild_id: "!",
                build_reply=lambda **_kwargs: "reset",
                guild_only_message=lambda: "guild only",
            )
        )

        self.assertEqual(removed, [])
        self.assertEqual(
            ctx.sent,
            ["기억 정리 작업이 끝나는 중이야. 잠깐 뒤에 다시 시도해줘."],
        )

    def test_reset_guild_memory_requires_autonomy_stop(self) -> None:
        ctx = FakeContext(guild=SimpleNamespace(id=7, name="Home"))
        removed: list[object] = []

        def blocked_reset(_guild_id: int) -> None:
            raise RuntimeError("autonomy_runtime_active")

        asyncio.run(
            handle_reset_guild_memory_command(
                ctx,
                memory_root=Path("unused"),
                reset_guild_runtime_state=blocked_reset,
                remove_tree=removed.append,
                get_guild_command_prefix=lambda _guild_id: "!",
                build_reply=lambda **_kwargs: "reset",
                guild_only_message=lambda: "guild only",
            )
        )

        self.assertEqual(removed, [])
        self.assertEqual(
            ctx.sent,
            ["자율 행동을 먼저 끈 뒤 다시 시도해줘."],
        )


if __name__ == "__main__":
    unittest.main()
