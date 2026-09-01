from __future__ import annotations

import asyncio
import sys
import threading
import unittest
from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock


REPO_ROOT = next(path for path in Path(__file__).resolve().parents if (path / "main.py").exists())
RUNTIME_ROOT = REPO_ROOT / "evelyn_core" / "runtime"
if str(RUNTIME_ROOT) not in sys.path:
    sys.path.insert(0, str(RUNTIME_ROOT))

from evelyn_core.discord_command_handlers import (  # noqa: E402
    RecordDeletionConfirmationGuard,
    handle_control_command_error,
    handle_discord_command_error,
    handle_autonomy_start_command,
    handle_autonomy_status_command,
    handle_autonomy_stop_command,
    handle_channel_setting_command,
    handle_evelyn_page_command,
    handle_feedback_application_command,
    handle_join_voice_command,
    handle_leave_voice_command,
    handle_minecraft_connect_command,
    handle_minecraft_disconnect_command,
    handle_minecraft_goal_command,
    handle_minecraft_status_command,
    handle_prefix_command,
    handle_rejoin_voice_command,
    handle_record_consent_application_command,
    handle_record_delete_application_command,
    handle_record_view_application_command,
    handle_reset_guild_memory_command,
    handle_restart_bot_command,
    make_control_command_authorized_checker,
    handle_shutdown_bot_command,
    handle_status_command,
)
from evelyn_core.autonomy_authorization import (  # noqa: E402
    ASSISTANT_AUTONOMY_ACTIONS,
)
from evelyn_core.discord_command_session_runtime import (  # noqa: E402
    ContinuityRecordingCommandContext,
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
        self.typing_entries = 0

    async def send(self, text: str) -> None:
        self.sent.append(text)

    def typing(self):
        context = self

        class TypingContext:
            async def __aenter__(self):
                context.typing_entries += 1

            async def __aexit__(self, *_args):
                return None

        return TypingContext()


class FakeVoiceClient:
    def __init__(self) -> None:
        self.stopped = False
        self.disconnected: list[bool | None] = []

    def stop_listening(self) -> None:
        self.stopped = True

    async def disconnect(self, force: bool | None = None) -> None:
        self.disconnected.append(force)


class FakeInteraction:
    def __init__(
        self,
        *,
        guild_id=7,
        user_id=3,
        channel_id=8,
        voice_channel_id=9,
        interaction_id=1001,
    ) -> None:
        voice = None
        if voice_channel_id is not None:
            voice = SimpleNamespace(
                channel=SimpleNamespace(id=voice_channel_id),
                self_mute=False,
                mute=False,
                suppress=False,
                self_deaf=False,
                deaf=False,
            )
        self.guild_id = guild_id
        self.channel_id = channel_id
        self.channel = SimpleNamespace(id=channel_id)
        self.id = interaction_id
        self.guild = None if guild_id is None else SimpleNamespace(id=guild_id)
        self.user = SimpleNamespace(id=user_id, voice=voice)
        self.response = SimpleNamespace(
            defer=AsyncMock(),
            send_message=AsyncMock(),
        )
        self.edit_original_response = AsyncMock()
        self.delete_original_response = AsyncMock()


class DiscordCommandHandlerTests(unittest.TestCase):
    def test_autonomy_grant_is_archived_as_minecraft_lineage_root_before_start(self) -> None:
        events: list[str] = []
        archive = AsyncMock(side_effect=lambda **_kwargs: events.append("archive"))

        class Engine:
            async def stop(self) -> None:
                events.append("stop")

            async def start(self) -> None:
                events.append("start")

        ctx = SimpleNamespace(
            guild=SimpleNamespace(id=7),
            author=SimpleNamespace(id=3, display_name="정훈"),
            channel=SimpleNamespace(id=5),
            message=SimpleNamespace(
                id=11,
                content="!자율시작",
                created_at=datetime(2026, 8, 28, tzinfo=timezone.utc),
            ),
            send=AsyncMock(),
        )

        asyncio.run(
            handle_autonomy_start_command(
                ctx,
                autonomy_enabled=True,
                get_or_create_autonomy_engine=lambda _guild_id: Engine(),
                is_minecraft_autonomy_route_enabled=lambda _guild_id: True,
                enable_minecraft_autonomy_route=lambda _guild_id: asyncio.sleep(
                    0, result=True
                ),
                grant_autonomy_authorization=lambda *_args, **_kwargs: {
                    "ok": True,
                    "grant": {"grantId": "grant-1"},
                },
                revoke_autonomy_authorization=Mock(),
                guild_only_message=lambda: "guild only",
                archive_autonomy_grant=archive,
            )
        )

        self.assertLess(events.index("archive"), events.index("start"))
        archive.assert_awaited_once_with(
            guild_id=7,
            channel_id=5,
            user_id=3,
            owner_name="정훈",
            message_id=11,
            grant_id="grant-1",
            authored_at=datetime(2026, 8, 28, tzinfo=timezone.utc).timestamp(),
            text="!자율시작",
        )

    def test_record_view_is_deferred_ephemeral_exact_self_scope_and_deleted_after_180(self) -> None:
        interaction = FakeInteraction(guild_id=7, user_id=3)
        read_self = AsyncMock(
            return_value=SimpleNamespace(
                records=(
                    SimpleNamespace(
                        started_at=None,
                        record_type="user_text",
                        body="내 기록",
                    ),
                ),
                next_page_handle=None,
            )
        )
        sleeps: list[float] = []
        tasks: list[object] = []

        async def fake_sleep(seconds: float) -> None:
            sleeps.append(seconds)

        async def scenario() -> None:
            await handle_record_view_application_command(
                interaction,
                feature_enabled=True,
                read_self=read_self,
                create_task=tasks.append,
                sleep_fn=fake_sleep,
                started_at="2026-08-01T00:00+09:00",
                ended_at="2026-08-02T00:00+09:00",
            )
            await tasks.pop()

        asyncio.run(scenario())

        interaction.response.defer.assert_awaited_once_with(
            ephemeral=True,
            thinking=True,
        )
        read_self.assert_awaited_once()
        kwargs = read_self.await_args.kwargs
        self.assertEqual(kwargs["actor_external_id"], "3")
        self.assertEqual(kwargs["guild_id"], "7")
        self.assertEqual(kwargs["interaction_id"], "1001")
        self.assertIsNone(kwargs["page_handle"])
        self.assertNotIn("authorized", kwargs)
        self.assertNotIn("admin", kwargs)
        self.assertIn("내 기록", interaction.edit_original_response.await_args.kwargs["content"])
        self.assertEqual(sleeps, [180.0])
        interaction.delete_original_response.assert_awaited_once_with()

    def test_record_next_page_rebinds_current_interaction_and_invoker(self) -> None:
        first = FakeInteraction(interaction_id=1201)
        read_self = AsyncMock(
            side_effect=(
                SimpleNamespace(
                    records=(
                        SimpleNamespace(
                            started_at=None,
                            record_type="user_text",
                            body="첫 페이지",
                        ),
                    ),
                    next_page_handle="opaque-next-page",
                ),
                SimpleNamespace(
                    records=(
                        SimpleNamespace(
                            started_at=None,
                            record_type="evelyn_reply",
                            body="둘째 페이지",
                        ),
                    ),
                    next_page_handle=None,
                ),
            )
        )

        def close_task(coroutine) -> None:
            coroutine.close()

        wrong_user = FakeInteraction(user_id=4, interaction_id=1202)
        next_click = FakeInteraction(interaction_id=1203)

        async def scenario() -> None:
            await handle_record_view_application_command(
                first,
                feature_enabled=True,
                read_self=read_self,
                create_task=close_task,
            )
            view = first.edit_original_response.await_args.kwargs["view"]
            self.assertEqual(view.children[0].label, "다음 페이지")
            self.assertEqual(
                view.children[0].custom_id,
                "evelyn-archive-page:opaque-next-page",
            )
            await view.children[0].callback(wrong_user)
            await view.children[0].callback(next_click)

        asyncio.run(scenario())

        wrong_user.response.send_message.assert_awaited_once()
        wrong_user.response.defer.assert_not_awaited()
        next_click.response.defer.assert_awaited_once_with(
            ephemeral=True,
            thinking=True,
        )
        self.assertEqual(read_self.await_count, 2)
        next_kwargs = read_self.await_args.kwargs
        self.assertEqual(next_kwargs["actor_external_id"], "3")
        self.assertEqual(next_kwargs["guild_id"], "7")
        self.assertEqual(next_kwargs["interaction_id"], "1203")
        self.assertEqual(next_kwargs["page_handle"], "opaque-next-page")
        self.assertIn(
            "둘째 페이지",
            next_click.edit_original_response.await_args.kwargs["content"],
        )
        self.assertIsNone(
            next_click.edit_original_response.await_args.kwargs["view"]
        )

    def test_record_page_uses_ephemeral_memory_attachment_without_skipping_rows(
        self,
    ) -> None:
        interaction = FakeInteraction(interaction_id=1301)
        read_self = AsyncMock(
            return_value=SimpleNamespace(
                records=(
                    SimpleNamespace(
                        started_at=None,
                        record_type="user_text",
                        body="A" * 1800,
                    ),
                    SimpleNamespace(
                        started_at=None,
                        record_type="evelyn_reply",
                        body="TAIL-MUST-NOT-BE-SKIPPED",
                    ),
                ),
                next_page_handle=None,
            )
        )

        def close_task(coroutine) -> None:
            coroutine.close()

        asyncio.run(
            handle_record_view_application_command(
                interaction,
                feature_enabled=True,
                read_self=read_self,
                create_task=close_task,
            )
        )

        kwargs = interaction.edit_original_response.await_args.kwargs
        self.assertIn("임시 첨부 파일", kwargs["content"])
        self.assertEqual(len(kwargs["attachments"]), 1)
        attachment = kwargs["attachments"][0]
        attachment.fp.seek(0)
        attached_text = attachment.fp.read().decode("utf-8")
        self.assertIn("TAIL-MUST-NOT-BE-SKIPPED", attached_text)

    def test_record_commands_reject_dm_and_mock_feature_flag_without_backend_calls(self) -> None:
        for interaction, enabled in (
            (FakeInteraction(guild_id=None, user_id=3), True),
            (FakeInteraction(guild_id=7, user_id=3), Mock(name="truthy_feature_flag")),
        ):
            with self.subTest(guild_id=interaction.guild_id, enabled=enabled):
                read_self = AsyncMock()
                asyncio.run(
                    handle_record_view_application_command(
                        interaction,
                        feature_enabled=enabled,
                        read_self=read_self,
                    )
                )
                interaction.response.defer.assert_not_awaited()
                interaction.response.send_message.assert_awaited_once()
                self.assertTrue(
                    interaction.response.send_message.await_args.kwargs["ephemeral"]
                )
                read_self.assert_not_awaited()

    def test_delete_preview_is_60_second_one_use_and_bound_to_exact_guild_caller(self) -> None:
        clock = [100.0]
        guard = RecordDeletionConfirmationGuard(monotonic=lambda: clock[0])
        preview = SimpleNamespace(
            preview_id="opaque-preview",
            counts_by_guild={"7": 2},
            dependent_record_count=1,
            interval_count=1,
            all_guilds=True,
        )
        preview_delete = AsyncMock(return_value=preview)
        apply_delete = AsyncMock(
            return_value=SimpleNamespace(
                status="local_fully_purged",
                affected_records=2,
                dependent_records=1,
                affected_intervals=1,
            )
        )

        async def no_sleep(_seconds: float) -> None:
            return None

        def close_task(coroutine) -> None:
            coroutine.close()

        preview_interaction = FakeInteraction(
            guild_id=7, user_id=3, interaction_id=1101
        )
        wrong_guild = FakeInteraction(
            guild_id=8, user_id=3, interaction_id=1102
        )
        right_guild = FakeInteraction(
            guild_id=7, user_id=3, interaction_id=1103
        )

        async def scenario() -> None:
            await handle_record_delete_application_command(
                preview_interaction,
                feature_enabled=True,
                preview_delete=preview_delete,
                apply_delete=apply_delete,
                confirmation_guard=guard,
                create_task=close_task,
                sleep_fn=no_sleep,
            )
            view = preview_interaction.edit_original_response.await_args.kwargs["view"]
            self.assertEqual(len(view.children), 1)
            self.assertEqual(view.children[0].label, "삭제 확인")
            self.assertEqual(
                view.children[0].custom_id,
                "evelyn-archive-delete:opaque-preview",
            )
            await view.children[0].callback(wrong_guild)
            await view.children[0].callback(right_guild)

        asyncio.run(scenario())

        content = preview_interaction.edit_original_response.await_args.kwargs["content"]
        self.assertIn("삭제 확인 버튼", content)
        self.assertNotIn("opaque-preview", content)
        preview_delete.assert_awaited_once_with(
            actor_external_id="3",
            request_guild_id="7",
            interaction_id="1101",
            started_at=None,
            ended_at=None,
        )

        wrong_guild.response.send_message.assert_awaited_once()
        wrong_guild.response.defer.assert_not_awaited()
        right_guild.response.defer.assert_awaited_once_with()
        apply_delete.assert_awaited_once_with(
            preview_id="opaque-preview",
            actor_external_id="3",
            request_guild_id="7",
            interaction_id="1103",
        )

        guard.remember("fresh", guild_id=7, user_id=3)
        clock[0] += 60.0
        self.assertFalse(guard.consume("fresh", guild_id=7, user_id=3))

    def test_delete_confirmation_applies_once_for_exact_bound_caller(self) -> None:
        guard = RecordDeletionConfirmationGuard(monotonic=lambda: 10.0)
        preview_delete = AsyncMock(
            return_value=SimpleNamespace(
                preview_id="preview",
                counts_by_guild={"7": 1},
                dependent_record_count=0,
                interval_count=0,
                all_guilds=True,
            )
        )
        apply_delete = AsyncMock(
            return_value=SimpleNamespace(
                status="local_cleanup_pending",
                affected_records=1,
                dependent_records=0,
                affected_intervals=0,
            )
        )

        def close_task(coroutine) -> None:
            coroutine.close()

        preview_interaction = FakeInteraction(guild_id=7, user_id=3)

        async def scenario() -> None:
            await handle_record_delete_application_command(
                preview_interaction,
                feature_enabled=True,
                preview_delete=preview_delete,
                apply_delete=apply_delete,
                confirmation_guard=guard,
                create_task=close_task,
            )
            view = preview_interaction.edit_original_response.await_args.kwargs["view"]
            for _index in range(2):
                await view.children[0].callback(
                    FakeInteraction(guild_id=7, user_id=3)
                )

        asyncio.run(scenario())

        apply_delete.assert_awaited_once_with(
            preview_id="preview",
            actor_external_id="3",
            request_guild_id="7",
            interaction_id="1001",
        )

    def test_record_consent_uses_exact_invoker_guild_and_current_voice_flags(self) -> None:
        interaction = FakeInteraction(guild_id=7, user_id=3, voice_channel_id=9)
        interaction.user.voice.self_mute = True
        set_consent = AsyncMock()

        def close_task(coroutine) -> None:
            coroutine.close()

        asyncio.run(
            handle_record_consent_application_command(
                interaction,
                feature_enabled=True,
                set_consent=set_consent,
                consented=True,
                create_task=close_task,
            )
        )

        interaction.response.defer.assert_awaited_once_with(
            ephemeral=True,
            thinking=True,
        )
        set_consent.assert_awaited_once_with(
            guild_id="7",
            actor_external_id="3",
            owner_name="3",
            channel_id="9",
            consented=True,
            self_mute=True,
            server_mute=False,
            stage_suppress=False,
            self_deaf=False,
            server_deaf=False,
        )

    def test_record_consent_requires_voice_but_withdrawal_is_allowed_after_leave(self) -> None:
        set_consent = AsyncMock()

        def close_task(coroutine) -> None:
            coroutine.close()

        consent_interaction = FakeInteraction(
            guild_id=7,
            user_id=3,
            voice_channel_id=None,
        )
        asyncio.run(
            handle_record_consent_application_command(
                consent_interaction,
                feature_enabled=True,
                set_consent=set_consent,
                consented=True,
                create_task=close_task,
            )
        )
        set_consent.assert_not_awaited()

        withdraw_interaction = FakeInteraction(
            guild_id=7,
            user_id=3,
            voice_channel_id=None,
        )
        asyncio.run(
            handle_record_consent_application_command(
                withdraw_interaction,
                feature_enabled=True,
                set_consent=set_consent,
                consented=False,
                create_task=close_task,
            )
        )

        set_consent.assert_awaited_once_with(
            guild_id="7",
            actor_external_id="3",
            owner_name="3",
            channel_id=None,
            consented=False,
            self_mute=False,
            server_mute=False,
            stage_suppress=False,
            self_deaf=False,
            server_deaf=False,
        )

    def test_feedback_command_is_ephemeral_and_cannot_request_promotion(self) -> None:
        interaction = FakeInteraction(
            guild_id=7,
            user_id=3,
            channel_id=8,
            interaction_id=1401,
        )
        capture = AsyncMock(
            return_value=SimpleNamespace(
                route="human_engineering_required",
                actionable=False,
            )
        )

        def close_task(coroutine) -> None:
            coroutine.close()

        asyncio.run(
            handle_feedback_application_command(
                interaction,
                feature_enabled=True,
                capture_feedback=capture,
                source_surface="voice",
                category="tool_failure",
                correction="도구 구성을 바꿔야 해",
                requested_change_scope="tool",
                create_task=close_task,
            )
        )

        interaction.response.defer.assert_awaited_once_with(
            ephemeral=True,
            thinking=True,
        )
        capture.assert_awaited_once_with(
            guild_id=7,
            channel_id=8,
            user_id=3,
            owner_name="3",
            source_surface="voice",
            category="tool_failure",
            correction="도구 구성을 바꿔야 해",
            requested_change_scope="tool",
            feedback_nonce="1401",
        )
        content = interaction.edit_original_response.await_args.kwargs["content"]
        self.assertIn("사람의 설계·보안 검토", content)
        self.assertIn("자동으로 바꾸지 않아", content)
        self.assertNotIn("도구 구성을 바꿔야 해", content)
        submitted = capture.await_args.kwargs
        for forbidden in (
            "generalize",
            "evaluate",
            "approve",
            "activate",
            "promotion",
        ):
            self.assertNotIn(forbidden, submitted)

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

        ctx.prefix = "?"
        ctx.command = SimpleNamespace(name="마크접속")
        asyncio.run(
            handle_control_command_error(
                ctx,
                handlers.commands.TooManyArguments("extra"),
            )
        )
        self.assertEqual(
            ctx.sent[-1],
            "명령 형식이 맞지 않아. 사용법: `?마크접속`",
        )

        class _Error(Exception):
            pass

        with self.assertRaises(_Error):
            asyncio.run(handle_control_command_error(ctx, _Error("boom")))
        with self.assertRaises(_Error):
            asyncio.run(handle_discord_command_error(ctx, _Error("boom")))

    def test_minecraft_like_unknown_command_shows_prefix_usage(self) -> None:
        import evelyn_core.discord_command_handlers as handlers

        ctx = FakeContext(content="?minecraft connect")
        ctx.prefix = "?"
        ctx.invoked_with = "minecraft"
        asyncio.run(
            handle_discord_command_error(
                ctx,
                handlers.commands.CommandNotFound("minecraft"),
            )
        )
        self.assertEqual(
            ctx.sent,
            [
                "Minecraft 접속 명령은 띄어쓰지 않고 입력해줘. "
                "사용법: `?minecraft-connect` 또는 `?마크접속`"
            ],
        )

        ctx.invoked_with = "unknown"
        asyncio.run(
            handle_discord_command_error(
                ctx,
                handlers.commands.CommandNotFound("unknown"),
            )
        )
        self.assertEqual(len(ctx.sent), 1)

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

        self.assertEqual(
            ctx.sent,
            ["connect:True"],
        )
        self.assertEqual(ctx.typing_entries, 1)
        self.assertEqual(route_enabled, [1])

    def test_minecraft_connect_archives_exact_root_before_any_effect(self) -> None:
        ctx = FakeContext(guild=SimpleNamespace(id=7), content="!마크접속")
        ctx.channel = SimpleNamespace(id=8)
        ctx.author.display_name = "정훈"
        ctx.message.id = 10
        ctx.message.created_at = datetime(2026, 8, 28, tzinfo=timezone.utc)
        events: list[str] = []
        effect_payloads: list[dict] = []

        async def archive(**_kwargs):
            events.append("archive")
            return {"recordId": "minecraft-command-1"}

        async def enable(*_args, **kwargs):
            events.append("effect")
            effect_payloads.append(kwargs)
            return {
                "connected": True,
                "outcome_verified": True,
                "outcome_code": "minecraft_connected",
            }

        asyncio.run(
            handle_minecraft_connect_command(
                ctx,
                enable_minecraft_mode=enable,
                enable_minecraft_autonomy_route=AsyncMock(return_value=True),
                build_reply=lambda _observed: "connected",
                guild_only_message=lambda: "guild only",
                archive_minecraft_command=archive,
                archive_required=True,
            )
        )

        self.assertEqual(events, ["archive", "effect"])
        self.assertEqual(
            effect_payloads[0]["parent_record_ids"],
            ("minecraft-command-1",),
        )
        self.assertEqual(ctx.sent, ["connected"])

    def test_minecraft_commands_fail_closed_before_effect_when_root_is_unavailable(self) -> None:
        async def reject_archive(**_kwargs):
            raise RuntimeError("archive down")

        connect = AsyncMock()
        connect_ctx = FakeContext(guild=SimpleNamespace(id=7), content="!마크접속")
        connect_ctx.channel = SimpleNamespace(id=8)
        connect_ctx.message.id = 10
        goal = AsyncMock()
        goal_ctx = FakeContext(
            guild=SimpleNamespace(id=7),
            content="!마크목표 다이아몬드 찾기",
        )
        goal_ctx.channel = SimpleNamespace(id=8)
        goal_ctx.message.id = 11

        asyncio.run(
            handle_minecraft_connect_command(
                connect_ctx,
                enable_minecraft_mode=connect,
                enable_minecraft_autonomy_route=AsyncMock(),
                build_reply=lambda _observed: "connected",
                guild_only_message=lambda: "guild only",
                archive_minecraft_command=reject_archive,
                archive_required=True,
            )
        )
        asyncio.run(
            handle_minecraft_goal_command(
                goal_ctx,
                goal="다이아몬드 찾기",
                set_minecraft_goal=goal,
                build_missing_reply=lambda _prefix: "missing",
                build_updated_reply=lambda *_args: "updated",
                guild_only_message=lambda: "guild only",
                archive_minecraft_command=reject_archive,
                archive_required=True,
            )
        )

        connect.assert_not_awaited()
        goal.assert_not_awaited()
        self.assertIn("실행하지 않았어", connect_ctx.sent[0])
        self.assertIn("바꾸지 않았어", goal_ctx.sent[0])

    def test_minecraft_connect_ignores_typing_indicator_failure(self) -> None:
        ctx = FakeContext(guild=SimpleNamespace(id=1))
        ctx.typing = lambda: object()

        asyncio.run(
            handle_minecraft_connect_command(
                ctx,
                enable_minecraft_mode=AsyncMock(
                    return_value={"connected": False}
                ),
                enable_minecraft_autonomy_route=AsyncMock(),
                build_reply=lambda _observed: "not connected",
                guild_only_message=lambda: "guild only",
            )
        )

        self.assertEqual(ctx.sent, ["not connected"])

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

    def test_minecraft_disconnect_archives_root_before_route_and_verified_stop(
        self,
    ) -> None:
        ctx = FakeContext(
            guild=SimpleNamespace(id=7),
            content="!마크종료",
        )
        ctx.channel = SimpleNamespace(id=8)
        ctx.message.id = 13
        events: list[str] = []
        stop_parents: list[tuple[str, ...]] = []

        async def archive(**_payload):
            events.append("archive")
            return {"recordId": "minecraft-command-3"}

        async def disable_route(_guild_id: int):
            events.append("route")

        async def disable_mode(
            _guild_id: int,
            *,
            parent_record_ids: tuple[str, ...],
        ):
            events.append("stop")
            stop_parents.append(parent_record_ids)
            return {
                "running": False,
                "connected": False,
                "outcome_verified": True,
                "outcome_code": "minecraft_stopped",
            }

        asyncio.run(
            handle_minecraft_disconnect_command(
                ctx,
                disable_minecraft_mode=disable_mode,
                disable_minecraft_autonomy_route=disable_route,
                guild_only_message=lambda: "guild only",
                archive_minecraft_command=archive,
                archive_required=True,
            )
        )

        self.assertEqual(events, ["archive", "route", "stop"])
        self.assertEqual(stop_parents, [("minecraft-command-3",)])
        self.assertIn("중지했어", ctx.sent[0])

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
        self.assertEqual(ctx.typing_entries, 1)

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
            build_missing_reply=lambda prefix: f"missing goal:{prefix}",
            build_updated_reply=lambda goal, status: f"goal:{goal}:{status['stage']}",
            guild_only_message=lambda: "guild only",
        )
        asyncio.run(handle_minecraft_goal_command(missing_ctx, goal="", **kwargs))
        asyncio.run(handle_minecraft_goal_command(goal_ctx, goal=" diamond ", **kwargs))

        self.assertEqual(missing_ctx.sent, ["missing goal:!"])
        self.assertEqual(goal_ctx.sent, ["goal:diamond:diamond"])

    def test_minecraft_goal_archives_authored_command_before_goal_effect(self) -> None:
        ctx = FakeContext(
            guild=SimpleNamespace(id=7),
            content="!마크목표 다이아몬드 찾기",
        )
        ctx.channel = SimpleNamespace(id=8)
        ctx.author.display_name = "정훈"
        ctx.message.id = 12
        ctx.message.created_at = datetime(2026, 8, 28, tzinfo=timezone.utc)
        events: list[str] = []
        archived: list[dict] = []

        async def archive(**payload):
            events.append("archive")
            archived.append(payload)
            return {"recordId": "minecraft-command-2"}

        goal_parents: list[tuple[str, ...]] = []

        async def set_goal(
            _guild_id: int,
            _goal: str,
            *,
            parent_record_ids: tuple[str, ...],
        ):
            events.append("effect")
            goal_parents.append(parent_record_ids)
            return {"stage": "ready"}

        asyncio.run(
            handle_minecraft_goal_command(
                ctx,
                goal=" 다이아몬드 찾기 ",
                set_minecraft_goal=set_goal,
                build_missing_reply=lambda _prefix: "missing",
                build_updated_reply=lambda *_args: "updated",
                guild_only_message=lambda: "guild only",
                archive_minecraft_command=archive,
                archive_required=True,
            )
        )

        self.assertEqual(events, ["archive", "effect"])
        self.assertEqual(goal_parents, [("minecraft-command-2",)])
        self.assertEqual(
            archived,
            [
                {
                    "guild_id": 7,
                    "channel_id": 8,
                    "user_id": 3,
                    "owner_name": "정훈",
                    "message_id": 12,
                    "authored_at": datetime(
                        2026, 8, 28, tzinfo=timezone.utc
                    ).timestamp(),
                    "text": "!마크목표 다이아몬드 찾기",
                }
            ],
        )

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

    def test_autonomy_start_reply_failure_does_not_revoke_running_engine(self) -> None:
        ctx = FakeContext(guild=SimpleNamespace(id=1))
        events: list[str] = []
        observed: list[tuple[str, str]] = []
        revoked: list[str] = []

        async def send(_text: str) -> None:
            events.append("send")
            raise ConnectionError("private reply failure")

        class Engine:
            async def stop(self) -> None:
                events.append("stop")

            async def start(self) -> None:
                events.append("start")

        ctx.send = send
        with self.assertRaises(ConnectionError):
            asyncio.run(
                handle_autonomy_start_command(
                    ctx,
                    autonomy_enabled=True,
                    get_or_create_autonomy_engine=lambda _guild_id: Engine(),
                    is_minecraft_autonomy_route_enabled=lambda _guild_id: False,
                    enable_minecraft_autonomy_route=lambda _guild_id: None,
                    grant_autonomy_authorization=lambda *_args, **_kwargs: (
                        events.append("grant") or {"ok": True}
                    ),
                    revoke_autonomy_authorization=lambda *_args, **_kwargs: (
                        revoked.append("revoke")
                    ),
                    guild_only_message=lambda: "guild only",
                    record_runtime_error=lambda code, exc: observed.append(
                        (code, type(exc).__name__)
                    ),
                )
            )

        self.assertEqual(events, ["stop", "grant", "start", "send"])
        self.assertEqual(observed, [])
        self.assertEqual(revoked, [])

    def test_autonomy_stop_reply_failure_is_not_cleanup_failure(self) -> None:
        ctx = FakeContext(guild=SimpleNamespace(id=1))
        events: list[str] = []
        observed: list[tuple[str, str]] = []

        async def send(_text: str) -> None:
            events.append("send")
            raise ConnectionError("private reply failure")

        class Engine:
            async def stop(self) -> None:
                events.append("stop")

        ctx.send = send
        with self.assertRaises(ConnectionError):
            asyncio.run(
                handle_autonomy_stop_command(
                    ctx,
                    autonomy_engines={1: Engine()},
                    revoke_autonomy_authorization=(
                        lambda *_args, **_kwargs: events.append("revoke")
                    ),
                    guild_only_message=lambda: "guild only",
                    record_runtime_error=lambda code, exc: observed.append(
                        (code, type(exc).__name__)
                    ),
                )
            )

        self.assertEqual(events, ["revoke", "stop", "send"])
        self.assertEqual(observed, [])

    def test_autonomy_start_cancellation_revokes_grant_and_propagates(self) -> None:
        for cancelled_stage, expected_events in (
            ("stop", ["stop", "revoke:start_failed"]),
            ("route", ["stop", "route", "revoke:start_failed"]),
            ("start", ["stop", "grant", "start", "revoke:start_failed"]),
        ):
            with self.subTest(cancelled_stage=cancelled_stage):
                ctx = FakeContext(guild=SimpleNamespace(id=1))
                events: list[str] = []
                observed: list[str] = []

                class Engine:
                    async def stop(self) -> None:
                        events.append("stop")
                        if cancelled_stage == "stop":
                            raise asyncio.CancelledError

                    async def start(self) -> None:
                        events.append("start")
                        if cancelled_stage == "start":
                            raise asyncio.CancelledError

                async def enable_route(_guild_id: int) -> bool:
                    events.append("route")
                    if cancelled_stage == "route":
                        raise asyncio.CancelledError
                    return True

                with self.assertRaises(asyncio.CancelledError):
                    asyncio.run(
                        handle_autonomy_start_command(
                            ctx,
                            autonomy_enabled=True,
                            get_or_create_autonomy_engine=lambda _guild_id: Engine(),
                            is_minecraft_autonomy_route_enabled=lambda _guild_id: (
                                cancelled_stage == "route"
                            ),
                            enable_minecraft_autonomy_route=enable_route,
                            grant_autonomy_authorization=lambda *_args, **_kwargs: (
                                events.append("grant") or {"ok": True}
                            ),
                            revoke_autonomy_authorization=lambda *_args, **kwargs: (
                                events.append(f"revoke:{kwargs['reason_code']}")
                            ),
                            guild_only_message=lambda: "guild only",
                            record_runtime_error=lambda code, _exc: observed.append(code),
                        )
                    )

                self.assertEqual(events, expected_events)
                self.assertEqual(observed, [])
                self.assertEqual(ctx.sent, [])

    def test_autonomy_start_records_each_runtime_failure_stage(self) -> None:
        for failed_stage in ("get", "stop", "start"):
            with self.subTest(failed_stage=failed_stage):
                ctx = FakeContext(guild=SimpleNamespace(id=1))
                observed: list[tuple[str, str]] = []
                revoked: list[str] = []

                class Engine:
                    async def stop(self) -> None:
                        if failed_stage == "stop":
                            raise RuntimeError("private cleanup failure")

                    async def start(self) -> None:
                        if failed_stage == "start":
                            raise RuntimeError("private start failure")

                def get_engine(_guild_id: int) -> Engine:
                    if failed_stage == "get":
                        raise RuntimeError("private create failure")
                    return Engine()

                asyncio.run(
                    handle_autonomy_start_command(
                        ctx,
                        autonomy_enabled=True,
                        get_or_create_autonomy_engine=get_engine,
                        is_minecraft_autonomy_route_enabled=lambda _guild_id: False,
                        enable_minecraft_autonomy_route=lambda _guild_id: None,
                        grant_autonomy_authorization=lambda *_args, **_kwargs: {
                            "ok": True
                        },
                        revoke_autonomy_authorization=lambda *_args, **kwargs: (
                            revoked.append(kwargs["reason_code"])
                        ),
                        guild_only_message=lambda: "guild only",
                        record_runtime_error=lambda code, exc: observed.append(
                            (code, type(exc).__name__)
                        ),
                    )
                )

                self.assertEqual(
                    observed,
                    [("autonomy_start_failed", "RuntimeError")],
                )
                self.assertEqual(
                    revoked,
                    [] if failed_stage == "get" else ["start_failed"],
                )
                self.assertEqual(
                    ctx.sent,
                    [
                        "❌ 자율 행동 시작에 실패했어."
                        if failed_stage == "get"
                        else "❌ 자율 행동 시작에 실패했고 승인은 폐기했어."
                    ],
                )

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
        self.assertEqual(
            shutdown_ctx.sent,
            [
                "Evelyn shutdown requested. Supervisors, bot, LLM, TTS, Voyager, "
                "and Evelyn-owned WSL services will stop if the full-stack helper "
                "starts; otherwise this bot process will stop instead."
            ],
        )
        self.assertEqual(len(tasks), 2)
        for task in tasks:
            task.close()

    def test_restart_arms_after_delivery_before_continuity_record_returns(
        self,
    ) -> None:
        record_entered = threading.Event()
        release_record = threading.Event()
        terminal_armed = threading.Event()
        worker_errors: list[BaseException] = []
        tasks: list[object] = []

        original = FakeContext(guild=SimpleNamespace(id=1))

        def stalled_record(*_args: object) -> None:
            record_entered.set()
            release_record.wait()

        wrapped = ContinuityRecordingCommandContext(
            original,
            record_reply=stalled_record,
            log=lambda *_args, **_kwargs: None,
        )

        async def completed_work() -> None:
            return None

        def restart_process() -> object:
            terminal_armed.set()
            return completed_work()

        def run_handler() -> None:
            try:
                asyncio.run(
                    handle_restart_bot_command(
                        wrapped,
                        create_task=tasks.append,
                        restart_bot_process=restart_process,
                    )
                )
            except BaseException as exc:
                worker_errors.append(exc)

        worker = threading.Thread(target=run_handler, daemon=True)
        worker.start()
        try:
            self.assertTrue(record_entered.wait(timeout=1.0))
            armed_before_record_release = terminal_armed.wait(timeout=1.0)
        finally:
            release_record.set()
            worker.join(timeout=1.0)
            for task in tasks:
                task.close()

        self.assertFalse(worker.is_alive())
        self.assertEqual(worker_errors, [])
        self.assertTrue(armed_before_record_release)
        self.assertEqual(
            original.sent,
            ["🔄 봇을 재시작할게. 잠깐만 기다려줘."],
        )

    def test_shutdown_schedules_only_after_confirmation_delivery(self) -> None:
        async def scenario() -> tuple[bool, list[str]]:
            delivery_entered = asyncio.Event()
            release_delivery = asyncio.Event()
            scheduled = False

            class Context:
                sent: list[str] = []

                async def send(self, message: str) -> None:
                    self.sent.append(message)
                    delivery_entered.set()
                    await release_delivery.wait()

            ctx = Context()

            def schedule_stack_shutdown() -> bool:
                nonlocal scheduled
                scheduled = True
                return True

            task = asyncio.create_task(
                handle_shutdown_bot_command(
                    ctx,
                    schedule_stack_shutdown=schedule_stack_shutdown,
                    create_task=asyncio.create_task,
                    shutdown_bot_process=AsyncMock(),
                )
            )
            await delivery_entered.wait()
            scheduled_before_delivery = scheduled
            release_delivery.set()
            await task
            return scheduled_before_delivery, ctx.sent

        scheduled_before_delivery, sent = asyncio.run(scenario())

        self.assertFalse(scheduled_before_delivery)
        self.assertEqual(
            sent,
            [
                "Evelyn shutdown requested. Supervisors, bot, LLM, TTS, Voyager, "
                "and Evelyn-owned WSL services will stop if the full-stack helper "
                "starts; otherwise this bot process will stop instead."
            ],
        )

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

    def test_reset_guild_memory_command_replies_after_durable_reset(self) -> None:
        guild = SimpleNamespace(id=7, name="Home")
        ctx = FakeContext(guild=guild)
        reset: list[int] = []

        asyncio.run(
            handle_reset_guild_memory_command(
                ctx,
                reset_guild_runtime_state=reset.append,
                get_guild_command_prefix=lambda guild_id: "!",
                build_reply=lambda **kwargs: f"reset:{kwargs['guild_name']}:{kwargs['current_prefix']}",
                guild_only_message=lambda: "guild only",
            )
        )

        self.assertEqual(reset, [7])
        self.assertEqual(ctx.sent, ["reset:Home:!"])

    def test_reset_confirmation_explicitly_refreshes_command_epoch(self) -> None:
        events: list[object] = []

        class Context(FakeContext):
            def refresh_ingress_epoch(self) -> None:
                events.append("refresh_epoch")

            async def send(self, text: str) -> None:
                events.append(("send", text))

        ctx = Context(guild=SimpleNamespace(id=7, name="Home"))

        asyncio.run(
            handle_reset_guild_memory_command(
                ctx,
                reset_guild_runtime_state=(
                    lambda guild_id: events.append(("reset", guild_id))
                ),
                get_guild_command_prefix=lambda _guild_id: "!",
                build_reply=lambda **_kwargs: "reset confirmed",
                guild_only_message=lambda: "guild only",
            )
        )

        self.assertEqual(
            events,
            [("reset", 7), "refresh_epoch", ("send", "reset confirmed")],
        )

    def test_reset_guild_memory_reports_live_memory_work(self) -> None:
        for error_code in (
            "autonomy_cognitive_refresh_inflight",
            "memory_background_work_inflight",
            "search_background_work_inflight",
        ):
            with self.subTest(error_code=error_code):
                ctx = FakeContext(guild=SimpleNamespace(id=7, name="Home"))
                def blocked_reset(_guild_id: int) -> None:
                    raise RuntimeError(error_code)

                asyncio.run(
                    handle_reset_guild_memory_command(
                        ctx,
                        reset_guild_runtime_state=blocked_reset,
                        get_guild_command_prefix=lambda _guild_id: "!",
                        build_reply=lambda **_kwargs: "reset",
                        guild_only_message=lambda: "guild only",
                    )
                )

                self.assertEqual(
                    ctx.sent,
                    ["기억 정리 작업이 끝나는 중이야. 잠깐 뒤에 다시 시도해줘."],
                )

    def test_reset_guild_memory_requires_autonomy_stop(self) -> None:
        ctx = FakeContext(guild=SimpleNamespace(id=7, name="Home"))
        def blocked_reset(_guild_id: int) -> None:
            raise RuntimeError("autonomy_runtime_active")

        asyncio.run(
            handle_reset_guild_memory_command(
                ctx,
                reset_guild_runtime_state=blocked_reset,
                get_guild_command_prefix=lambda _guild_id: "!",
                build_reply=lambda **_kwargs: "reset",
                guild_only_message=lambda: "guild only",
            )
        )

        self.assertEqual(
            ctx.sent,
            ["자율 행동을 먼저 끈 뒤 다시 시도해줘."],
        )

    def test_reset_guild_memory_reports_safe_persistent_reset_failure(
        self,
    ) -> None:
        ctx = FakeContext(guild=SimpleNamespace(id=7, name="Home"))

        def blocked_reset(_guild_id: int) -> None:
            raise RuntimeError(
                "memory_guild_reset_legacy_scope_missing"
            )

        asyncio.run(
            handle_reset_guild_memory_command(
                ctx,
                reset_guild_runtime_state=blocked_reset,
                get_guild_command_prefix=lambda _guild_id: "!",
                build_reply=lambda **_kwargs: "reset",
                guild_only_message=lambda: "guild only",
            )
        )

        self.assertEqual(
            ctx.sent,
            [
                "기억을 안전하게 초기화하지 못했어. 잠시 뒤 다시 시도해줘. "
                "계속되면 기억 관리에서 예전 확인 기억을 직접 삭제한 뒤 다시 시도해줘."
            ],
        )

    def test_reset_guild_memory_hides_durable_reset_failure_detail(
        self,
    ) -> None:
        for error_code in (
            "search_followup_guild_reset_failed",
            "memory_guild_reset_durability_failed",
        ):
            with self.subTest(error_code=error_code):
                ctx = FakeContext(
                    guild=SimpleNamespace(id=7, name="Home")
                )
                private_canary = r"PRIVATE C:\secret\search-token"

                def blocked_reset(_guild_id: int) -> None:
                    try:
                        raise OSError(private_canary)
                    except OSError as exc:
                        raise RuntimeError(error_code) from exc

                asyncio.run(
                    handle_reset_guild_memory_command(
                        ctx,
                        reset_guild_runtime_state=blocked_reset,
                        get_guild_command_prefix=lambda _guild_id: "!",
                        build_reply=lambda **_kwargs: "reset",
                        guild_only_message=lambda: "guild only",
                    )
                )

                rendered = " ".join(ctx.sent)
                self.assertIn("다시 시도해줘", rendered)
                self.assertNotIn(private_canary, rendered)
                self.assertNotIn("search-token", rendered)

    def test_reset_guild_memory_reraises_unknown_failure(self) -> None:
        ctx = FakeContext(guild=SimpleNamespace(id=7, name="Home"))

        with self.assertRaisesRegex(RuntimeError, "^unknown_reset_failure$"):
            asyncio.run(
                handle_reset_guild_memory_command(
                    ctx,
                    reset_guild_runtime_state=(
                        lambda _guild_id: (_ for _ in ()).throw(
                            RuntimeError("unknown_reset_failure")
                        )
                    ),
                    get_guild_command_prefix=lambda _guild_id: "!",
                    build_reply=lambda **_kwargs: "reset",
                    guild_only_message=lambda: "guild only",
                )
            )

        self.assertEqual(ctx.sent, [])


if __name__ == "__main__":
    unittest.main()
