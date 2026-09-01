import asyncio
import hashlib
import hmac
import json
import sys
import unittest
from dataclasses import replace
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock


REPO_ROOT = next(path for path in Path(__file__).resolve().parents if (path / "main.py").exists())
RUNTIME_ROOT = REPO_ROOT / "evelyn_core" / "runtime"
if str(RUNTIME_ROOT) not in sys.path:
    sys.path.insert(0, str(RUNTIME_ROOT))

from evelyn_core.discord_conversation_archive_runtime import (  # noqa: E402
    DiscordConversationArchiveClient,
    ConversationArchiveTransportError,
    DiscordArchiveCandidate,
    DiscordArchiveRecordKind,
    DiscordInteractionContext,
    DiscordParticipationTracker,
    DiscordSharedArchiveGate,
    DiscordSharedSessionRegistry,
    DiscordVoiceStateSnapshot,
    EphemeralDeleteOutcome,
    IntervalKind,
    ParticipationInterval,
    RecordCommandRejected,
    attempt_ephemeral_response_delete,
    classify_discord_ephemeral_delete_error,
    build_record_command_policy,
    build_text_archive_candidate,
    build_voice_transcript_archive_candidate,
    select_self_scoped_records,
    voice_state_snapshot_from_discord,
)


class _FakeArchiveResponse:
    def __init__(self, payload, *, status=200) -> None:
        self.status = status
        self._raw = json.dumps(payload).encode("utf-8")

    async def __aenter__(self):
        return self

    async def __aexit__(self, *_args):
        return None

    async def read(self) -> bytes:
        return self._raw


class _FakeArchiveSession:
    def __init__(self, responses) -> None:
        self.responses = list(responses)
        self.requests: list[dict[str, object]] = []

    def request(self, method, url, **kwargs):
        self.requests.append({"method": method, "url": url, **kwargs})
        response = self.responses.pop(0)
        if isinstance(response, BaseException):
            raise response
        return response


class DiscordParticipationTrackerTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tracker = DiscordParticipationTracker()
        self.ready = DiscordVoiceStateSnapshot(channel_id=10, consent_current=True)

    def observe(self, at: float, state: DiscordVoiceStateSnapshot):
        return self.tracker.observe(
            guild_id=1,
            user_id=2,
            observed_at=at,
            snapshot=state,
        )

    def test_presence_stays_open_while_each_mute_or_deaf_state_closes_eligibility(self) -> None:
        joined = self.observe(10.0, self.ready)

        self.assertEqual([row.kind for row in joined.opened], [IntervalKind.PRESENCE, IntervalKind.ELIGIBLE])
        for index, field in enumerate(
            ("self_mute", "server_mute", "stage_suppress", "self_deaf", "server_deaf"),
            start=1,
        ):
            blocked_at = 10.0 + index * 10.0
            blocked = self.observe(blocked_at, replace(self.ready, **{field: True}))
            self.assertEqual([row.kind for row in blocked.closed], [IntervalKind.ELIGIBLE])
            self.assertEqual(blocked.closed[0].ended_at, blocked_at)
            self.assertEqual(blocked.snapshot.ineligible_reason, field)
            self.assertEqual(blocked.opened, ())

            resumed = self.observe(blocked_at + 1.0, self.ready)
            self.assertEqual([row.kind for row in resumed.opened], [IntervalKind.ELIGIBLE])
            self.assertEqual(resumed.closed, ())

        left = self.observe(70.0, replace(self.ready, channel_id=None))
        self.assertEqual(
            {row.kind for row in left.closed},
            {IntervalKind.PRESENCE, IntervalKind.ELIGIBLE},
        )
        presence = next(row for row in left.closed if row.kind is IntervalKind.PRESENCE)
        self.assertEqual((presence.started_at, presence.ended_at), (10.0, 70.0))

    def test_gateway_unknown_closes_intervals_and_reconnect_starts_new_ones(self) -> None:
        self.observe(1.0, self.ready)

        updates = self.tracker.mark_gateway_unknown(observed_at=2.0)

        self.assertEqual(len(updates), 1)
        self.assertFalse(updates[0].snapshot.present)
        self.assertEqual(
            {row.kind for row in updates[0].closed},
            {IntervalKind.PRESENCE, IntervalKind.ELIGIBLE},
        )
        reconnected = self.observe(3.0, self.ready)
        self.assertEqual(
            {row.kind for row in reconnected.opened},
            {IntervalKind.PRESENCE, IntervalKind.ELIGIBLE},
        )

    def test_channel_move_closes_old_half_open_intervals_and_opens_new(self) -> None:
        self.observe(1.0, self.ready)

        moved = self.observe(5.0, replace(self.ready, channel_id=20))

        self.assertEqual({row.channel_id for row in moved.closed}, {10})
        self.assertEqual({row.channel_id for row in moved.opened}, {20})
        self.assertTrue(all(row.ended_at == 5.0 for row in moved.closed))
        self.assertTrue(all(row.started_at == 5.0 for row in moved.opened))

    def test_consent_is_required_and_out_of_order_events_fail_closed(self) -> None:
        joined = self.observe(10.0, replace(self.ready, consent_current=False))
        self.assertEqual([row.kind for row in joined.opened], [IntervalKind.PRESENCE])
        self.assertEqual(joined.snapshot.ineligible_reason, "consent_not_current")

        consented = self.observe(11.0, self.ready)
        self.assertEqual([row.kind for row in consented.opened], [IntervalKind.ELIGIBLE])
        with self.assertRaisesRegex(ValueError, "out_of_order"):
            self.observe(10.5, self.ready)

    def test_gateway_unknown_rejects_out_of_order_batch_before_mutating_any_user(self) -> None:
        self.observe(10.0, self.ready)
        self.tracker.observe(
            guild_id=1,
            user_id=3,
            observed_at=20.0,
            snapshot=self.ready,
        )

        with self.assertRaisesRegex(ValueError, "out_of_order"):
            self.tracker.mark_gateway_unknown(observed_at=15.0)

        first = self.observe(16.0, replace(self.ready, self_mute=True))
        self.assertEqual([row.kind for row in first.closed], [IntervalKind.ELIGIBLE])

    def test_discord_voice_state_projection_maps_server_and_stage_flags(self) -> None:
        class Obj:
            def __init__(self, **kwargs) -> None:
                self.__dict__.update(kwargs)

        state = Obj(
            channel=Obj(id=99),
            self_mute=False,
            mute=True,
            suppress=True,
            self_deaf=False,
            deaf=True,
        )

        snapshot = voice_state_snapshot_from_discord(state, consent_current=True)

        self.assertEqual(snapshot.channel_id, 99)
        self.assertTrue(snapshot.server_mute)
        self.assertTrue(snapshot.stage_suppress)
        self.assertTrue(snapshot.server_deaf)
        self.assertFalse(snapshot.eligible)


class DiscordSelfScopeTests(unittest.TestCase):
    @staticmethod
    def row(
        record_id: str,
        kind: DiscordArchiveRecordKind,
        *,
        user_id: int | None,
        start: float,
        end: float | None = None,
        guild_id: int = 1,
        channel_id: int = 10,
        parents: tuple[str, ...] = (),
    ) -> DiscordArchiveCandidate:
        return DiscordArchiveCandidate(
            record_id=record_id,
            guild_id=guild_id,
            channel_id=channel_id,
            kind=kind,
            started_at=start,
            ended_at=start if end is None else end,
            source_user_id=user_id,
            parent_record_ids=parents,
            body=f"body:{record_id}",
        )

    def test_exact_chat_is_visible_while_muted_but_does_not_open_time_access(self) -> None:
        records = (
            self.row("chat", DiscordArchiveRecordKind.USER_TEXT, user_id=2, start=20.0),
            self.row(
                "reply",
                DiscordArchiveRecordKind.EVELYN_REPLY,
                user_id=None,
                start=21.0,
                parents=("chat",),
            ),
            self.row(
                "own_muted_voice",
                DiscordArchiveRecordKind.FINAL_STT,
                user_id=2,
                start=20.0,
                end=21.0,
            ),
            self.row("other_voice", DiscordArchiveRecordKind.FINAL_STT, user_id=3, start=20.0, end=21.0),
            self.row(
                "unrelated",
                DiscordArchiveRecordKind.EVELYN_REPLY,
                user_id=None,
                start=21.0,
                parents=("other_voice",),
            ),
        )

        selected = select_self_scoped_records(
            records,
            caller_user_id=2,
            current_guild_id=1,
            eligible_intervals=(),
        )

        self.assertEqual([row.record_id for row in selected], ["chat", "reply"])

    def test_final_stt_requires_exact_eligible_interval_and_lineage(self) -> None:
        records = (
            self.row("spoken", DiscordArchiveRecordKind.FINAL_STT, user_id=2, start=10.0, end=11.0),
            self.row("after_leave", DiscordArchiveRecordKind.FINAL_STT, user_id=2, start=12.0, end=13.0),
            self.row(
                "reply",
                DiscordArchiveRecordKind.EVELYN_REPLY,
                user_id=None,
                start=11.0,
                parents=("spoken",),
            ),
            self.row(
                "task",
                DiscordArchiveRecordKind.TASK_RESULT,
                user_id=None,
                start=11.5,
                parents=("reply",),
            ),
            self.row(
                "minecraft",
                DiscordArchiveRecordKind.MINECRAFT_RESULT,
                user_id=None,
                start=12.0,
                parents=("task",),
            ),
        )
        eligible = (
            ParticipationInterval(
                kind=IntervalKind.ELIGIBLE,
                guild_id=1,
                channel_id=10,
                user_id=2,
                started_at=10.0,
                ended_at=12.0,
            ),
        )

        selected = select_self_scoped_records(
            records,
            caller_user_id=2,
            current_guild_id=1,
            eligible_intervals=eligible,
        )

        self.assertEqual(
            [row.record_id for row in selected],
            ["spoken", "reply", "task", "minecraft"],
        )

    def test_cross_user_cross_guild_and_mixed_parent_rows_are_not_visible(self) -> None:
        records = (
            self.row("mine", DiscordArchiveRecordKind.USER_TEXT, user_id=2, start=1.0),
            self.row("theirs", DiscordArchiveRecordKind.USER_TEXT, user_id=3, start=1.0),
            self.row(
                "mixed",
                DiscordArchiveRecordKind.EVELYN_REPLY,
                user_id=None,
                start=2.0,
                parents=("mine", "theirs"),
            ),
            self.row(
                "other_guild",
                DiscordArchiveRecordKind.USER_TEXT,
                user_id=2,
                start=1.0,
                guild_id=2,
            ),
        )

        selected = select_self_scoped_records(
            records,
            caller_user_id=2,
            current_guild_id=1,
            eligible_intervals=(),
        )

        self.assertEqual([row.record_id for row in selected], ["mine"])

    def test_partial_stt_is_dropped_and_only_final_is_constructed(self) -> None:
        common = dict(
            record_id="voice",
            guild_id=1,
            channel_id=10,
            user_id=2,
            started_at=1.0,
            ended_at=2.0,
            text="private transcript",
        )

        self.assertIsNone(build_voice_transcript_archive_candidate(stage="partial", **common))
        final = build_voice_transcript_archive_candidate(stage="final", **common)

        self.assertIsNotNone(final)
        self.assertEqual(final.kind, DiscordArchiveRecordKind.FINAL_STT)
        with self.assertRaisesRegex(ValueError, "unsupported_transcript_stage"):
            build_voice_transcript_archive_candidate(stage="speculative", **common)

    def test_text_builder_creates_chat_only_authorization_root(self) -> None:
        row = build_text_archive_candidate(
            record_id="chat",
            guild_id=1,
            channel_id=10,
            user_id=2,
            authored_at=5.0,
            text="exact chat",
        )

        self.assertEqual(row.kind, DiscordArchiveRecordKind.USER_TEXT)
        self.assertEqual(row.source_user_id, 2)


class DiscordRecordCommandPolicyTests(unittest.TestCase):
    def test_guild_command_is_exact_invoker_ephemeral_and_never_admin(self) -> None:
        policy = build_record_command_policy(
            context=DiscordInteractionContext.GUILD,
            guild_id=1,
            invoker_user_id=2,
        )

        self.assertEqual(policy.audience_user_id, 2)
        self.assertEqual(policy.capability, "memory.user_view")
        self.assertTrue(policy.ephemeral)
        self.assertEqual(policy.delete_after_seconds, 180.0)
        self.assertEqual(policy.ack_deadline_seconds, 3.0)
        self.assertFalse(policy.opens_admin_session)
        self.assertFalse(policy.dm_fallback)

    def test_dm_private_and_cross_principal_requests_are_rejected(self) -> None:
        for context in (
            DiscordInteractionContext.BOT_DM,
            DiscordInteractionContext.PRIVATE_CHANNEL,
        ):
            with self.subTest(context=context):
                with self.assertRaisesRegex(RecordCommandRejected, "guild_interaction_required"):
                    build_record_command_policy(
                        context=context,
                        guild_id=None,
                        invoker_user_id=2,
                    )

        with self.assertRaisesRegex(RecordCommandRejected, "cross_principal"):
            build_record_command_policy(
                context="GUILD",
                guild_id=1,
                invoker_user_id=2,
                requested_user_id=3,
            )

    def test_ephemeral_delete_attempt_waits_180_seconds_and_reports_outcome(self) -> None:
        sleeps: list[float] = []
        deletes: list[str] = []

        async def fake_sleep(seconds: float) -> None:
            sleeps.append(seconds)

        async def delete() -> None:
            deletes.append("called")

        outcome = asyncio.run(
            attempt_ephemeral_response_delete(delete, sleep_fn=fake_sleep)
        )

        self.assertEqual(outcome, EphemeralDeleteOutcome.REMOVED)
        self.assertEqual(sleeps, [180.0])
        self.assertEqual(deletes, ["called"])

    def test_ephemeral_delete_error_uses_adapter_classification_without_raising(self) -> None:
        async def fake_sleep(_seconds: float) -> None:
            return None

        async def delete() -> None:
            raise RuntimeError("expired")

        outcome = asyncio.run(
            attempt_ephemeral_response_delete(
                delete,
                sleep_fn=fake_sleep,
                classify_error=lambda _exc: EphemeralDeleteOutcome.TOKEN_EXPIRED,
            )
        )

        self.assertEqual(outcome, EphemeralDeleteOutcome.TOKEN_EXPIRED)

    def test_unknown_delete_result_fails_closed(self) -> None:
        async def fake_sleep(_seconds: float) -> None:
            return None

        async def delete() -> str:
            return "unexpected-status"

        outcome = asyncio.run(
            attempt_ephemeral_response_delete(delete, sleep_fn=fake_sleep)
        )

        self.assertEqual(outcome, EphemeralDeleteOutcome.NOT_CONTROLLABLE)

    def test_discord_delete_errors_have_exact_content_free_outcomes(self) -> None:
        expired = type("DiscordExpired", (Exception,), {"code": 10015, "status": 404})()
        missing = type("DiscordMissing", (Exception,), {"code": 10008, "status": 404})()
        forbidden = type("DiscordForbidden", (Exception,), {"code": 50013, "status": 403})()

        self.assertEqual(
            classify_discord_ephemeral_delete_error(expired),
            EphemeralDeleteOutcome.TOKEN_EXPIRED,
        )
        self.assertEqual(
            classify_discord_ephemeral_delete_error(missing),
            EphemeralDeleteOutcome.NOT_FOUND,
        )
        self.assertEqual(
            classify_discord_ephemeral_delete_error(forbidden),
            EphemeralDeleteOutcome.NOT_CONTROLLABLE,
        )


class DiscordSharedArchiveGateTests(unittest.TestCase):
    @staticmethod
    def client() -> SimpleNamespace:
        client = SimpleNamespace(generation="boot-1")
        for name in (
            "archive_user_text",
            "archive_final_transcript",
            "archive_assistant_text",
            "archive_autonomy_grant",
            "archive_minecraft_command",
            "capture_feedback",
            "observe_participation",
            "set_consent",
            "open_shared_session_lease",
            "close_shared_session_lease",
        ):
            setattr(client, name, AsyncMock(return_value={"ok": True}))
        client.authorize_voice_capture = AsyncMock(return_value=True)
        client.consent_current = Mock(return_value=True)
        client.begin_generation = AsyncMock(return_value="boot-2")
        return client

    def test_registry_binds_operator_channels_generation_and_monotonic_ttl(self) -> None:
        now = [10.0]
        sessions = DiscordSharedSessionRegistry(
            ttl_seconds=60.0,
            monotonic=lambda: now[0],
        )
        sessions.begin_generation("boot-1")
        opened = sessions.open(
            operator_user_id=5,
            guild_id=7,
            text_channel_id=8,
            voice_channel_id=9,
        )

        self.assertEqual(
            sessions.current(
                guild_id=7,
                generation="boot-1",
                operator_user_id=5,
                text_channel_id=8,
                voice_channel_id=9,
            ),
            opened,
        )
        self.assertIsNone(sessions.current(guild_id=7, operator_user_id=6))
        self.assertIsNone(sessions.current(guild_id=7, text_channel_id=10))
        self.assertIsNone(sessions.current(guild_id=7, voice_channel_id=10))
        self.assertIsNone(sessions.current(guild_id=7, generation="old-boot"))

        now[0] = 70.0
        self.assertIsNone(sessions.current(guild_id=7))
        self.assertEqual(sessions.peek(guild_id=7), opened)
        sessions.begin_generation("boot-2")
        self.assertIsNone(sessions.peek(guild_id=7))

    def test_every_ingest_root_uses_the_same_current_session(self) -> None:
        client = self.client()
        sessions = DiscordSharedSessionRegistry(ttl_seconds=60.0)
        sessions.begin_generation("boot-1")
        opened = sessions.open(
            operator_user_id=5,
            guild_id=7,
            text_channel_id=8,
            voice_channel_id=9,
        )
        gate = DiscordSharedArchiveGate(client=client, sessions=sessions)
        update = DiscordParticipationTracker().observe(
            guild_id=7,
            user_id=6,
            observed_at=1.0,
            snapshot=DiscordVoiceStateSnapshot(
                channel_id=9,
                consent_current=False,
            ),
        )

        async def scenario() -> None:
            await gate.archive_user_text(
                guild_id=7,
                channel_id=8,
                user_id=6,
            )
            await gate.archive_final_transcript(
                guild_id=7,
                channel_id=9,
                user_id=6,
            )
            await gate.archive_assistant_text(guild_id=7, channel_id=8)
            await gate.archive_autonomy_grant(
                guild_id=7,
                channel_id=8,
                user_id=5,
            )
            await gate.archive_minecraft_command(
                guild_id=7,
                channel_id=8,
                user_id=5,
            )
            self.assertTrue(
                await gate.authorize_voice_capture(
                    guild_id=7,
                    channel_id=9,
                    user_id=6,
                )
            )
            await gate.observe_participation(update)
            await gate.set_consent(
                guild_id=7,
                actor_external_id=6,
                channel_id=9,
                consented=True,
            )
            self.assertTrue(
                gate.consent_current(guild_id=7, channel_id=9, user_id=6)
            )

        asyncio.run(scenario())

        client.archive_user_text.assert_awaited_once()
        client.archive_final_transcript.assert_awaited_once()
        client.archive_assistant_text.assert_awaited_once()
        client.archive_autonomy_grant.assert_awaited_once()
        client.archive_minecraft_command.assert_awaited_once()
        client.authorize_voice_capture.assert_awaited_once()
        client.observe_participation.assert_awaited_once_with(update)
        client.set_consent.assert_awaited_once()
        sessions.open(
            operator_user_id=5,
            guild_id=7,
            text_channel_id=8,
            voice_channel_id=9,
        )
        self.assertFalse(
            gate.consent_current(guild_id=7, channel_id=9, user_id=6)
        )

    def test_wrong_scope_unauthorized_operator_and_generation_change_fail_closed(self) -> None:
        client = self.client()
        sessions = DiscordSharedSessionRegistry(ttl_seconds=60.0)
        sessions.begin_generation("boot-1")
        sessions.open(
            operator_user_id=5,
            guild_id=7,
            text_channel_id=8,
            voice_channel_id=9,
        )
        gate = DiscordSharedArchiveGate(client=client, sessions=sessions)

        async def rejected_calls() -> None:
            for call in (
                gate.archive_user_text(guild_id=7, channel_id=10),
                gate.archive_final_transcript(guild_id=7, channel_id=8),
                gate.archive_assistant_text(guild_id=7, channel_id=10),
                gate.archive_autonomy_grant(
                    guild_id=7,
                    channel_id=8,
                    user_id=6,
                ),
                gate.archive_minecraft_command(
                    guild_id=7,
                    channel_id=8,
                    user_id=6,
                ),
                gate.set_consent(
                    guild_id=7,
                    channel_id=None,
                    consented=True,
                ),
            ):
                with self.assertRaisesRegex(
                    ConversationArchiveTransportError,
                    "archive_shared_session_inactive",
                ):
                    await call
            await gate.begin_generation()
            with self.assertRaises(ConversationArchiveTransportError):
                await gate.authorize_voice_capture(guild_id=7, channel_id=9)

        asyncio.run(rejected_calls())

        self.assertIsNone(sessions.peek(guild_id=7))
        self.assertEqual(sessions.boot_generation, "boot-2")
        client.archive_user_text.assert_not_awaited()
        client.archive_autonomy_grant.assert_not_awaited()
        client.archive_minecraft_command.assert_not_awaited()

    def test_minecraft_root_does_not_admit_effect_after_session_closes_in_transport(self) -> None:
        client = self.client()
        sessions = DiscordSharedSessionRegistry(ttl_seconds=60.0)
        sessions.begin_generation("boot-1")
        opened = sessions.open(
            operator_user_id=5,
            guild_id=7,
            text_channel_id=8,
            voice_channel_id=9,
        )

        async def close_during_archive(**_payload):
            sessions.close(guild_id=7, expected=opened)
            return {"ok": True, "recordId": "minecraft-command-1"}

        client.archive_minecraft_command = AsyncMock(
            side_effect=close_during_archive
        )
        gate = DiscordSharedArchiveGate(client=client, sessions=sessions)

        with self.assertRaisesRegex(
            ConversationArchiveTransportError,
            "archive_shared_session_inactive",
        ):
            asyncio.run(
                gate.archive_minecraft_command(
                    guild_id=7,
                    channel_id=8,
                    user_id=5,
                )
            )

        client.archive_minecraft_command.assert_awaited_once()

    def test_feedback_target_is_exact_caller_surface_and_current_session(self) -> None:
        client = self.client()
        client.archive_assistant_text = AsyncMock(
            side_effect=(
                {"ok": True, "recordId": "text-reply-1"},
                {"ok": True, "recordId": "voice-reply-1"},
            )
        )
        client.capture_feedback = AsyncMock(
            return_value=SimpleNamespace(
                route="review_only",
                actionable=False,
            )
        )
        sessions = DiscordSharedSessionRegistry(ttl_seconds=60.0)
        sessions.begin_generation("boot-1")
        opened = sessions.open(
            operator_user_id=5,
            guild_id=7,
            text_channel_id=8,
            voice_channel_id=9,
        )
        gate = DiscordSharedArchiveGate(client=client, sessions=sessions)

        async def scenario() -> None:
            await gate.archive_assistant_text(
                guild_id=7,
                channel_id=8,
                user_id=6,
                turn_id="text-turn-1",
            )
            with self.assertRaisesRegex(
                ConversationArchiveTransportError,
                "archive_feedback_target_missing",
            ):
                await gate.capture_feedback(
                    guild_id=7,
                    channel_id=8,
                    user_id=6,
                    owner_name="참여자",
                    source_surface="discord",
                    category="answer_quality",
                    correction="아직 전달되지 않은 답변",
                    requested_change_scope="none",
                    feedback_nonce="interaction-undelivered",
                )
            self.assertTrue(
                await gate.confirm_assistant_delivery(
                    guild_id=7,
                    channel_id=8,
                    user_id=6,
                    turn_id="text-turn-1",
                )
            )
            await gate.archive_assistant_text(
                guild_id=7,
                channel_id=9,
                user_id=6,
                turn_id="voice-turn-1",
            )
            self.assertTrue(
                await gate.confirm_assistant_delivery(
                    guild_id=7,
                    channel_id=9,
                    user_id=6,
                    turn_id="voice-turn-1",
                )
            )
            await gate.capture_feedback(
                guild_id=7,
                channel_id=8,
                user_id=6,
                owner_name="참여자",
                source_surface="voice",
                category="answer_quality",
                correction="근거를 먼저 말해줘",
                requested_change_scope="none",
                feedback_nonce="interaction-1",
            )
            with self.assertRaisesRegex(
                ConversationArchiveTransportError,
                "archive_feedback_target_missing",
            ):
                await gate.capture_feedback(
                    guild_id=7,
                    channel_id=8,
                    user_id=10,
                    owner_name="다른 참여자",
                    source_surface="voice",
                    category="answer_quality",
                    correction="다른 사람 답변 교정",
                    requested_change_scope="none",
                    feedback_nonce="interaction-2",
                )
            sessions.open(
                operator_user_id=5,
                guild_id=7,
                text_channel_id=8,
                voice_channel_id=9,
            )
            with self.assertRaisesRegex(
                ConversationArchiveTransportError,
                "archive_feedback_target_missing",
            ):
                await gate.capture_feedback(
                    guild_id=7,
                    channel_id=8,
                    user_id=6,
                    owner_name="참여자",
                    source_surface="voice",
                    category="answer_quality",
                    correction="stale",
                    requested_change_scope="none",
                    feedback_nonce="interaction-3",
                )

        asyncio.run(scenario())

        client.capture_feedback.assert_awaited_once_with(
            task_id="voice-turn-1",
            source_record_id="voice-reply-1",
            guild_id=7,
            request_channel_id=8,
            source_channel_id=9,
            user_id=6,
            owner_name="참여자",
            session_id="guild:7:voice:9:user:6",
            surface="voice",
            category="answer_quality",
            correction="근거를 먼저 말해줘",
            requested_change_scope="none",
            feedback_nonce="interaction-1",
            shared_session_lease_id=opened.lease_id,
        )
        for forbidden in (
            "promotion",
            "promote",
            "generalize",
            "eval",
            "evaluate",
            "approve",
            "approval",
            "activate",
        ):
            self.assertFalse(hasattr(gate, f"{forbidden}_feedback"))

    def test_feedback_target_is_not_registered_after_session_closes_mid_archive(
        self,
    ) -> None:
        client = self.client()
        sessions = DiscordSharedSessionRegistry(ttl_seconds=60.0)
        sessions.begin_generation("boot-1")
        opened = sessions.open(
            operator_user_id=5,
            guild_id=7,
            text_channel_id=8,
            voice_channel_id=9,
        )

        async def close_during_archive(**_payload):
            sessions.close(guild_id=7, expected=opened)
            return {"ok": True, "recordId": "text-reply-1"}

        client.archive_assistant_text = AsyncMock(side_effect=close_during_archive)
        client.capture_feedback = AsyncMock()
        gate = DiscordSharedArchiveGate(client=client, sessions=sessions)

        async def scenario() -> None:
            with self.assertRaisesRegex(
                ConversationArchiveTransportError,
                "archive_shared_session_inactive",
            ):
                await gate.archive_assistant_text(
                    guild_id=7,
                    channel_id=8,
                    user_id=6,
                    turn_id="text-turn-1",
                )
            sessions.open(
                operator_user_id=5,
                guild_id=7,
                text_channel_id=8,
                voice_channel_id=9,
            )
            with self.assertRaisesRegex(
                ConversationArchiveTransportError,
                "archive_feedback_target_missing",
            ):
                await gate.capture_feedback(
                    guild_id=7,
                    channel_id=8,
                    user_id=6,
                    owner_name="참여자",
                    source_surface="discord",
                    category="answer_quality",
                    correction="근거를 먼저 말해줘",
                    requested_change_scope="none",
                    feedback_nonce="interaction-1",
                )

        asyncio.run(scenario())
        client.capture_feedback.assert_not_awaited()

    def test_feedback_target_purge_uses_exact_lineage_mapping(self) -> None:
        client = self.client()
        client.archive_assistant_text = AsyncMock(
            side_effect=(
                {"ok": True, "recordId": "text-reply-1"},
                {"ok": True, "recordId": "voice-reply-1"},
            )
        )
        client.capture_feedback = AsyncMock(
            return_value=SimpleNamespace(route="review_only", actionable=False)
        )
        sessions = DiscordSharedSessionRegistry(ttl_seconds=60.0)
        sessions.begin_generation("boot-1")
        sessions.open(
            operator_user_id=5,
            guild_id=7,
            text_channel_id=8,
            voice_channel_id=9,
        )
        gate = DiscordSharedArchiveGate(client=client, sessions=sessions)

        async def scenario() -> None:
            await gate.archive_assistant_text(
                guild_id=7,
                channel_id=8,
                user_id=6,
                turn_id="text-turn-1",
            )
            await gate.confirm_assistant_delivery(
                guild_id=7,
                channel_id=8,
                user_id=6,
                turn_id="text-turn-1",
            )
            await gate.archive_assistant_text(
                guild_id=7,
                channel_id=9,
                user_id=6,
                turn_id="voice-turn-1",
            )
            await gate.confirm_assistant_delivery(
                guild_id=7,
                channel_id=9,
                user_id=6,
                turn_id="voice-turn-1",
            )
            self.assertEqual(
                gate.purge_feedback_targets(
                    lambda target: target["turn_id"] == "text-turn-1"
                ),
                (1, 0, 0),
            )
            with self.assertRaisesRegex(
                ConversationArchiveTransportError,
                "archive_feedback_target_missing",
            ):
                await gate.capture_feedback(
                    guild_id=7,
                    channel_id=8,
                    user_id=6,
                    owner_name="참여자",
                    source_surface="discord",
                    category="answer_quality",
                    correction="text correction",
                    requested_change_scope="none",
                    feedback_nonce="interaction-text",
                )
            await gate.capture_feedback(
                guild_id=7,
                channel_id=8,
                user_id=6,
                owner_name="참여자",
                source_surface="voice",
                category="answer_quality",
                correction="voice correction",
                requested_change_scope="none",
                feedback_nonce="interaction-voice",
            )
            self.assertEqual(
                gate.purge_feedback_targets(
                    lambda target: target["session_key"]
                    == "guild:7:voice:9:user:6"
                ),
                (1, 0, 0),
            )

        asyncio.run(scenario())
        client.capture_feedback.assert_awaited_once()

    def test_expired_session_accepts_only_its_exact_terminal_participation_close(self) -> None:
        now = [0.0]
        client = self.client()
        sessions = DiscordSharedSessionRegistry(
            ttl_seconds=10.0,
            monotonic=lambda: now[0],
        )
        sessions.begin_generation("boot-1")
        sessions.open(
            operator_user_id=5,
            guild_id=7,
            text_channel_id=8,
            voice_channel_id=9,
        )
        gate = DiscordSharedArchiveGate(client=client, sessions=sessions)
        tracker = DiscordParticipationTracker()
        tracker.observe(
            guild_id=7,
            user_id=6,
            observed_at=1.0,
            snapshot=DiscordVoiceStateSnapshot(
                channel_id=9,
                consent_current=True,
            ),
        )
        now[0] = 10.0
        closure = tracker.mark_gateway_unknown(observed_at=11.0)[0]

        asyncio.run(gate.observe_participation(closure))

        client.observe_participation.assert_awaited_once_with(closure)
        with self.assertRaises(ConversationArchiveTransportError):
            asyncio.run(
                gate.observe_participation(
                    DiscordParticipationTracker().observe(
                        guild_id=7,
                        user_id=6,
                        observed_at=12.0,
                        snapshot=DiscordVoiceStateSnapshot(
                            channel_id=9,
                            consent_current=True,
                        ),
                    )
                )
            )


class DiscordConversationArchiveClientTests(unittest.TestCase):
    @staticmethod
    def stable(domain: str, value: str) -> str:
        return hmac.new(
            b"k" * 32,
            f"evelyn.private-conversation-archive.{domain}.v1\n{value}".encode(),
            hashlib.sha256,
        ).hexdigest()[:32]

    def build_client(self, responses):
        session = _FakeArchiveSession(responses)

        async def get_session():
            return session

        nonces = iter((f"{index:032x}" for index in range(1, 100)))
        client = DiscordConversationArchiveClient(
            base_url="http://bot-api:8798",
            master_key=b"k" * 32,
            user_view_master_key=b"u" * 32,
            get_http_session=get_session,
            clock=lambda: 1000.0,
            nonce_factory=lambda _size: next(nonces),
            generation_factory=lambda _size: "discord-generation-1",
        )
        return client, session

    def test_active_task_guidance_is_exact_signed_and_digest_verified(self) -> None:
        guidance = "근거가 없는 결론은 완료로 표시하지 않는다."
        binding = {
            "schema": "evelyn.task-planner-guidance-binding.v1",
            "versionId": "guidance-v2",
            "guidance": guidance,
            "guidanceDigest": hashlib.sha256(
                guidance.encode("utf-8")
            ).hexdigest(),
            "sourceFree": True,
            "active": True,
            "canaryRunId": None,
        }
        client, session = self.build_client(
            (_FakeArchiveResponse({"ok": True, "binding": binding}),)
        )

        self.assertEqual(asyncio.run(client.active_task_guidance()), binding)
        request = session.requests[0]
        self.assertEqual(request["method"], "POST")
        self.assertTrue(
            str(request["url"]).endswith(
                "/internal/conversation-archive/task-guidance"
            )
        )
        self.assertEqual(json.loads(request["data"]), {})

        forged = dict(binding, guidanceDigest="0" * 64)
        bad_client, _ = self.build_client(
            (_FakeArchiveResponse({"ok": True, "binding": forged}),)
        )
        with self.assertRaisesRegex(
            ConversationArchiveTransportError,
            "archive_task_guidance_receipt_invalid",
        ):
            asyncio.run(bad_client.active_task_guidance())

    def test_text_and_reply_use_generation_lineage_and_valid_hmac(self) -> None:
        root_id = self.stable("record", "discord-text:7:10")
        reply_id = self.stable(
            "record",
            f"discord-reply:7:turn-1:{root_id}",
        )
        client, session = self.build_client(
            (
                _FakeArchiveResponse(
                    {
                        "ok": True,
                        "generation": "discord-generation-1",
                        "activated": True,
                    }
                ),
                _FakeArchiveResponse({"ok": True, "recordId": root_id}),
                _FakeArchiveResponse({"ok": True, "recordId": reply_id}),
            )
        )

        async def scenario() -> None:
            root = await client.archive_user_text(
                guild_id=7,
                channel_id=8,
                user_id=9,
                owner_name="정훈",
                message_id=10,
                turn_id="turn-1",
                authored_at=20.0,
                text="원문  ",
            )
            first_body = json.loads(session.requests[1]["data"])
            await client.archive_assistant_text(
                guild_id=7,
                channel_id=8,
                turn_id="turn-1",
                parent_record_id=first_body["recordId"],
                text="답변",
            )
            self.assertTrue(root["ok"])

        asyncio.run(scenario())

        self.assertEqual(
            [request["url"].removeprefix("http://bot-api:8798") for request in session.requests],
            [
                "/internal/conversation-archive/generation",
                "/internal/conversation-archive/record",
                "/internal/conversation-archive/record",
            ],
        )
        user = json.loads(session.requests[1]["data"])
        reply = json.loads(session.requests[2]["data"])
        self.assertEqual(
            (user["generation"], user["sequence"]),
            ("discord-generation-1", 1),
        )
        self.assertEqual(
            (reply["generation"], reply["sequence"]),
            ("discord-generation-1", 2),
        )
        self.assertEqual(user["body"], "원문  ")
        self.assertEqual(user["ownerName"], "정훈")
        self.assertEqual(reply["parentRecordIds"], [user["recordId"]])
        self.assertIsNone(reply["sourceUserId"])

        request = session.requests[1]
        headers = request["headers"]
        purpose_key = hmac.new(
            b"k" * 32,
            b"evelyn.private-conversation-archive.transport-key.v1\ningest",
            hashlib.sha256,
        ).digest()
        canonical = "\n".join(
            (
                "ingest",
                "POST",
                "/internal/conversation-archive/record",
                headers["X-Evelyn-Archive-Timestamp"],
                headers["X-Evelyn-Archive-Nonce"],
                hashlib.sha256(request["data"]).hexdigest(),
            )
        ).encode()
        self.assertTrue(
            hmac.compare_digest(
                headers["X-Evelyn-Archive-Signature"],
                hmac.new(purpose_key, canonical, hashlib.sha256).hexdigest(),
            )
        )

    def test_voice_reply_uses_exact_final_stt_parent_and_admission_fails_closed(self) -> None:
        transcript_id = self.stable(
            "record",
            "discord-final-stt:1:3:voice-turn:4",
        )
        reply_id = self.stable(
            "record",
            f"discord-reply:1:voice-turn:{transcript_id}",
        )
        client, session = self.build_client(
            (
                _FakeArchiveResponse(
                    {
                        "ok": True,
                        "generation": "discord-generation-1",
                        "activated": True,
                    }
                ),
                _FakeArchiveResponse({"ok": True, "recordId": transcript_id}),
                _FakeArchiveResponse({"ok": True, "recordId": reply_id}),
                _FakeArchiveResponse({"allowed": False}),
            )
        )

        async def scenario() -> bool:
            await client.archive_final_transcript(
                guild_id=1,
                channel_id=2,
                user_id=3,
                owner_name="참여자",
                turn_id="voice-turn",
                segment_id=4,
                started_at=10.0,
                ended_at=11.0,
                text="최종 전사",
            )
            await client.archive_assistant_text(
                guild_id=1,
                channel_id=2,
                user_id=3,
                turn_id="voice-turn",
                text="최종 답변",
            )
            return await client.authorize_voice_capture(
                guild_id=1,
                channel_id=2,
                user_id=3,
                voice_ingress_epoch=5,
            )

        allowed = asyncio.run(scenario())

        self.assertFalse(allowed)
        transcript = json.loads(session.requests[1]["data"])
        reply = json.loads(session.requests[2]["data"])
        self.assertEqual(reply["parentRecordIds"], [transcript["recordId"]])
        self.assertEqual(transcript["kind"], "final_stt")
        self.assertEqual(transcript["ownerName"], "참여자")

    def test_shared_session_lease_uses_the_signed_ingest_sequence(self) -> None:
        client, session = self.build_client(
            (
                _FakeArchiveResponse(
                    {
                        "ok": True,
                        "generation": "discord-generation-1",
                        "activated": True,
                    }
                ),
                _FakeArchiveResponse(
                    {
                        "ok": True,
                        "state": "open",
                        "guildId": "7",
                        "leaseId": "lease-1",
                    }
                ),
                _FakeArchiveResponse(
                    {
                        "ok": True,
                        "state": "closed",
                        "guildId": "7",
                        "leaseId": "lease-1",
                    }
                ),
            )
        )

        async def scenario() -> None:
            await client.open_shared_session_lease(
                operator_user_id=5,
                guild_id=7,
                text_channel_id=8,
                voice_channel_id=9,
                lease_id="lease-1",
            )
            await client.close_shared_session_lease(
                guild_id=7,
                lease_id="lease-1",
            )

        asyncio.run(scenario())

        opened = json.loads(session.requests[1]["data"])
        closed = json.loads(session.requests[2]["data"])
        self.assertEqual(opened["generation"], closed["generation"])
        self.assertEqual((opened["sequence"], closed["sequence"]), (1, 2))
        self.assertEqual(opened["leaseId"], "lease-1")
        self.assertEqual(closed["leaseId"], "lease-1")
        self.assertTrue(
            session.requests[1]["url"].endswith(
                "/internal/conversation-archive/shared-session/open"
            )
        )
        self.assertTrue(
            session.requests[2]["url"].endswith(
                "/internal/conversation-archive/shared-session/close"
            )
        )

    def test_feedback_client_exposes_only_signed_review_capture(self) -> None:
        client, session = self.build_client(
            (
                _FakeArchiveResponse(
                    {
                        "ok": True,
                        "generation": "discord-generation-1",
                        "activated": True,
                    }
                ),
                _FakeArchiveResponse(
                    {
                        "ok": True,
                        "workflow": {
                            "schema": "evelyn.feedback-workflow-public.v1",
                            "workflowId": "fb-" + "a" * 48,
                            "state": "review_only",
                            "category": "answer_quality",
                            "route": "review_only",
                            "actionable": False,
                            "sourceRecordId": "reply-record-1",
                            "versionId": None,
                            "activeVersionId": "base",
                            "deletionStates": [],
                            "contentFree": True,
                        },
                    }
                ),
            )
        )

        result = asyncio.run(
            client.capture_feedback(
                task_id="turn-1",
                source_record_id="reply-record-1",
                guild_id=7,
                request_channel_id=8,
                source_channel_id=9,
                user_id=6,
                owner_name="참여자",
                session_id="guild:7:voice:9:user:6",
                surface="voice",
                category="answer_quality",
                correction="근거를 먼저 말해줘",
                requested_change_scope="none",
                feedback_nonce="interaction-1",
                shared_session_lease_id="lease-1",
            )
        )

        self.assertEqual(result.route, "review_only")
        request = session.requests[1]
        self.assertEqual(
            request["url"].removeprefix("http://bot-api:8798"),
            "/internal/conversation-archive/feedback/capture",
        )
        body = json.loads(request["data"])
        self.assertEqual(body["callerUserId"], "6")
        self.assertEqual(body["sourceRecordId"], "reply-record-1")
        self.assertEqual(body["taskId"], "turn-1")
        self.assertEqual(body["requestChannelId"], "8")
        self.assertEqual(body["sessionId"], "guild:7:voice:9:user:6")
        self.assertEqual(body["surface"], "voice")
        self.assertEqual(body["sharedSessionLeaseId"], "lease-1")
        self.assertEqual(body["requestedChangeScope"], "none")
        self.assertNotIn("adminAuthorized", body)
        self.assertNotIn("actionable", body)
        headers = request["headers"]
        purpose_key = hmac.new(
            b"k" * 32,
            b"evelyn.private-conversation-archive.transport-key.v1\ningest",
            hashlib.sha256,
        ).digest()
        canonical = "\n".join(
            (
                "ingest",
                "POST",
                "/internal/conversation-archive/feedback/capture",
                headers["X-Evelyn-Archive-Timestamp"],
                headers["X-Evelyn-Archive-Nonce"],
                hashlib.sha256(request["data"]).hexdigest(),
            )
        ).encode()
        self.assertTrue(
            hmac.compare_digest(
                headers["X-Evelyn-Archive-Signature"],
                hmac.new(purpose_key, canonical, hashlib.sha256).hexdigest(),
            )
        )
        for forbidden in (
            "promotion",
            "promote",
            "generalize",
            "eval",
            "evaluate",
            "approve",
            "approval",
            "activate",
        ):
            self.assertFalse(hasattr(client, f"{forbidden}_feedback"))

    def test_feedback_client_rejects_nonterminal_or_versioned_receipt(self) -> None:
        workflow = {
            "schema": "evelyn.feedback-workflow-public.v1",
            "workflowId": "fb-" + "a" * 48,
            "state": "review_only",
            "category": "answer_quality",
            "route": "review_only",
            "actionable": False,
            "sourceRecordId": "reply-record-1",
            "versionId": None,
            "activeVersionId": "base",
            "deletionStates": [],
            "contentFree": True,
        }
        for forged in (
            {**workflow, "state": "active"},
            {**workflow, "versionId": "candidate-1"},
        ):
            client, _ = self.build_client(
                (
                    _FakeArchiveResponse(
                        {
                            "ok": True,
                            "generation": "discord-generation-1",
                            "activated": True,
                        }
                    ),
                    _FakeArchiveResponse({"ok": True, "workflow": forged}),
                )
            )
            with self.assertRaisesRegex(
                ConversationArchiveTransportError,
                "archive_feedback_receipt_invalid",
            ):
                asyncio.run(
                    client.capture_feedback(
                        task_id="turn-1",
                        source_record_id="reply-record-1",
                        guild_id=7,
                        request_channel_id=8,
                        source_channel_id=9,
                        user_id=6,
                        owner_name="참여자",
                        session_id="guild:7:voice:9:user:6",
                        surface="voice",
                        category="answer_quality",
                        correction="근거를 먼저 말해줘",
                        requested_change_scope="none",
                        feedback_nonce="interaction-1",
                        shared_session_lease_id="lease-1",
                    )
                )

    def test_autonomy_grant_id_is_the_exact_archived_lineage_root(self) -> None:
        client, session = self.build_client(
            (
                _FakeArchiveResponse(
                    {
                        "ok": True,
                        "generation": "discord-generation-1",
                        "activated": True,
                    }
                ),
                _FakeArchiveResponse({"ok": True, "recordId": "grant-1"}),
            )
        )

        asyncio.run(
            client.archive_autonomy_grant(
                guild_id=7,
                channel_id=8,
                user_id=9,
                owner_name="정훈",
                message_id=10,
                grant_id="grant-1",
                authored_at=20.0,
                text="!자율시작",
            )
        )

        body = json.loads(session.requests[1]["data"])
        self.assertEqual(body["recordId"], "grant-1")
        self.assertEqual(body["kind"], "minecraft_command")

    def test_explicit_minecraft_command_is_a_stable_owner_lineage_root(self) -> None:
        record_id = self.stable(
            "record",
            "discord-minecraft-command:7:10",
        )
        client, session = self.build_client(
            (
                _FakeArchiveResponse(
                    {
                        "ok": True,
                        "generation": "discord-generation-1",
                        "activated": True,
                    }
                ),
                _FakeArchiveResponse({"ok": True, "recordId": record_id}),
            )
        )

        receipt = asyncio.run(
            client.archive_minecraft_command(
                guild_id=7,
                channel_id=8,
                user_id=9,
                owner_name="정훈",
                message_id=10,
                authored_at=20.0,
                text="!마크목표 다이아몬드 찾기",
            )
        )

        body = json.loads(session.requests[1]["data"])
        self.assertEqual(receipt["recordId"], record_id)
        self.assertEqual(body["recordId"], record_id)
        self.assertEqual(body["kind"], "minecraft_command")
        self.assertEqual(body["parentRecordIds"], [])
        self.assertEqual(body["sourceUserId"], "9")
        self.assertEqual(body["ownerName"], "정훈")

    def test_consent_withdrawal_after_leave_uses_last_exact_channel(self) -> None:
        client, session = self.build_client(
            (
                _FakeArchiveResponse(
                    {
                        "ok": True,
                        "generation": "discord-generation-1",
                        "activated": True,
                    }
                ),
                _FakeArchiveResponse({"ok": True}),
                _FakeArchiveResponse({"ok": True}),
            )
        )
        update = DiscordParticipationTracker().observe(
            guild_id=7,
            user_id=9,
            observed_at=1.0,
            snapshot=DiscordVoiceStateSnapshot(
                channel_id=12,
                consent_current=False,
            ),
        )

        async def scenario() -> None:
            await client.observe_participation(update)
            await client.set_consent(
                guild_id=7,
                actor_external_id=9,
                owner_name="참여자",
                channel_id=None,
                consented=False,
                self_mute=False,
                server_mute=False,
                stage_suppress=False,
                self_deaf=False,
                server_deaf=False,
            )

        asyncio.run(scenario())

        withdrawal = json.loads(session.requests[2]["data"])
        self.assertIsNone(withdrawal["snapshot"]["channelId"])
        self.assertFalse(withdrawal["snapshot"]["present"])
        self.assertFalse(withdrawal["snapshot"]["consentCurrent"])
        self.assertNotIn("consentCurrent", withdrawal)
        self.assertFalse(client.consent_current(guild_id=7, channel_id=12, user_id=9))

    def test_transport_retry_reuses_body_with_fresh_nonce(self) -> None:
        client, session = self.build_client(
            (
                OSError("transient"),
                _FakeArchiveResponse(
                    {
                        "ok": True,
                        "generation": "discord-generation-1",
                        "activated": True,
                    }
                ),
            )
        )

        generation = asyncio.run(client.begin_generation())

        self.assertEqual(generation, "discord-generation-1")
        self.assertEqual(session.requests[0]["data"], session.requests[1]["data"])
        self.assertNotEqual(
            session.requests[0]["headers"]["X-Evelyn-Archive-Nonce"],
            session.requests[1]["headers"]["X-Evelyn-Archive-Nonce"],
        )

    def test_client_payloads_match_exact_bot_api_contract(self) -> None:
        client, session = self.build_client(
            (
                _FakeArchiveResponse(
                    {
                        "ok": True,
                        "generation": "discord-generation-1",
                        "activated": True,
                    }
                ),
                _FakeArchiveResponse({"ok": True, "applied": True}),
                _FakeArchiveResponse({"ok": True, "applied": True}),
                _FakeArchiveResponse({"ok": True, "allowed": True}),
                _FakeArchiveResponse({"ok": True, "handle": "read-handle"}),
                _FakeArchiveResponse(
                    {
                        "ok": True,
                        "records": [],
                        "snapshotGeneration": 4,
                        "nextPageHandle": None,
                    }
                ),
                _FakeArchiveResponse({"ok": True, "handle": "preview-handle"}),
                _FakeArchiveResponse(
                    {
                        "ok": True,
                        "previewId": "preview-1",
                        "countsByGuild": {"7": 1},
                        "dependentRecordCount": 0,
                        "intervalCount": 1,
                        "allGuilds": True,
                    }
                ),
                _FakeArchiveResponse({"ok": True, "handle": "apply-handle"}),
                _FakeArchiveResponse(
                    {
                        "ok": True,
                        "state": "local_fully_purged",
                        "affectedRecords": 1,
                        "dependentRecords": 0,
                        "affectedIntervals": 1,
                    }
                ),
            )
        )
        update = DiscordParticipationTracker().observe(
            guild_id=7,
            user_id=9,
            owner_name="참여자",
            observed_at=1.0,
            snapshot=DiscordVoiceStateSnapshot(
                channel_id=12,
                consent_current=True,
                stage_suppress=True,
            ),
        )

        async def scenario() -> None:
            await client.observe_participation(update)
            await client.set_consent(
                guild_id=7,
                actor_external_id=9,
                owner_name="참여자",
                channel_id=12,
                consented=True,
                self_mute=False,
                server_mute=False,
                stage_suppress=False,
                self_deaf=False,
                server_deaf=False,
            )
            self.assertTrue(
                await client.authorize_voice_capture(
                    guild_id=7,
                    channel_id=12,
                    user_id=9,
                    voice_ingress_epoch=99,
                )
            )
            await client.read_self(
                actor_external_id="9",
                guild_id="7",
                interaction_id="1001",
                started_at=None,
                ended_at=None,
            )
            preview = await client.preview_user_deletion(
                actor_external_id="9",
                request_guild_id="7",
                interaction_id="1002",
                started_at=None,
                ended_at=None,
            )
            result = await client.apply_user_deletion(
                preview_id=preview.preview_id,
                actor_external_id="9",
                request_guild_id="7",
                interaction_id="1003",
            )
            self.assertEqual(result.status, "local_fully_purged")

        asyncio.run(scenario())

        payloads = [json.loads(request["data"]) for request in session.requests]
        self.assertEqual(payloads[0], {"generation": "discord-generation-1"})
        for payload in payloads[1:3]:
            self.assertEqual(
                set(payload),
                {
                    "generation",
                    "sequence",
                    "idempotencyKey",
                    "guildId",
                    "userId",
                    "ownerName",
                    "observedAt",
                    "snapshot",
                },
            )
            self.assertEqual(
                set(payload["snapshot"]),
                {
                    "channelId",
                    "present",
                    "consentCurrent",
                    "gatewayKnown",
                    "selfMute",
                    "serverMute",
                    "selfDeaf",
                    "serverDeaf",
                    "suppressed",
                },
            )
        self.assertEqual(
            payloads[3], {"guildId": "7", "channelId": "12", "userId": "9"}
        )
        self.assertEqual(
            payloads[4],
            {
                "context": "GUILD",
                "interactionId": "1001",
                "callerUserId": "9",
                "guildId": "7",
                "action": "records",
            },
        )
        self.assertEqual(
            payloads[5],
            {
                "context": "GUILD",
                "interactionId": "1001",
                "callerUserId": "9",
                "guildId": "7",
                "handle": "read-handle",
            },
        )
        self.assertEqual(payloads[6]["action"], "delete-preview")
        self.assertEqual(payloads[7]["handle"], "preview-handle")
        self.assertEqual(
            payloads[8],
            {
                "context": "GUILD",
                "interactionId": "1003",
                "callerUserId": "9",
                "guildId": "7",
                "action": "delete-apply",
                "previewId": "preview-1",
            },
        )
        self.assertEqual(payloads[9]["handle"], "apply-handle")

        request = session.requests[4]
        headers = request["headers"]
        purpose_key = hmac.new(
            b"u" * 32,
            b"evelyn.private-conversation-archive.transport-key.v1\nuser-view-issue",
            hashlib.sha256,
        ).digest()
        canonical = "\n".join(
            (
                "user-view-issue",
                "POST",
                "/internal/conversation-archive/self/authorize",
                headers["X-Evelyn-Archive-Timestamp"],
                headers["X-Evelyn-Archive-Nonce"],
                hashlib.sha256(request["data"]).hexdigest(),
            )
        ).encode()
        self.assertTrue(
            hmac.compare_digest(
                headers["X-Evelyn-Archive-Signature"],
                hmac.new(purpose_key, canonical, hashlib.sha256).hexdigest(),
            )
        )

    def test_self_next_page_uses_new_interaction_and_opaque_page_handle(self) -> None:
        client, session = self.build_client(
            (
                _FakeArchiveResponse({"ok": True, "handle": "action-handle"}),
                _FakeArchiveResponse(
                    {
                        "ok": True,
                        "records": [
                            {
                                "recordId": "record-2",
                                "createdAt": "2026-08-28T00:00:00+00:00",
                                "kind": "evelyn_reply",
                                "body": "next",
                            }
                        ],
                        "snapshotGeneration": 12,
                        "nextPageHandle": None,
                    }
                ),
            )
        )

        page = asyncio.run(
            client.read_self(
                actor_external_id="9",
                guild_id="7",
                interaction_id="2001",
                started_at=None,
                ended_at=None,
                page_handle="opaque-page",
            )
        )

        self.assertEqual(page.records[0].body, "next")
        self.assertIsNone(page.next_page_handle)
        payloads = [json.loads(request["data"]) for request in session.requests]
        self.assertEqual(
            payloads[0],
            {
                "context": "GUILD",
                "interactionId": "2001",
                "callerUserId": "9",
                "guildId": "7",
                "action": "records",
                "pageHandle": "opaque-page",
            },
        )
        self.assertEqual(payloads[1]["handle"], "action-handle")

    def test_user_view_transport_key_must_be_distinct(self) -> None:
        with self.assertRaisesRegex(ValueError, "archive_user_view_key_invalid"):
            DiscordConversationArchiveClient(
                base_url="http://bot-api:8798",
                master_key=b"k" * 32,
                user_view_master_key=b"k" * 32,
                get_http_session=AsyncMock(),
            )

    def test_purge_owner_poll_ack_and_lineage_use_separate_strict_domains(
        self,
    ) -> None:
        scope_digest = "a" * 64
        work = {
            "requestId": "1" * 32,
            "deletionGeneration": 7,
            "scopeDigest": scope_digest,
            "reason": "user_requested",
            "requestedAt": "2026-08-28T00:00:00+00:00",
            "scopeAll": False,
            "guildId": "7",
            "startedAt": "2026-08-27T00:00:00+00:00",
            "endedAt": "2026-08-28T00:00:00+00:00",
            "lineageHandles": [
                {"kind": "turn", "digest": "b" * 64}
            ],
            "lineageComplete": True,
            "remainingSinks": [
                "continuity_checkpoint",
                "prompt_tool_cache",
            ],
            "contentFree": True,
        }
        client, session = self.build_client(
            (
                _FakeArchiveResponse(
                    {
                        "ok": True,
                        "workOrders": [work],
                        "contentFree": True,
                    }
                ),
                _FakeArchiveResponse(
                    {
                        "ok": True,
                        "state": "manual_review",
                        "archiveCompleted": False,
                        "contentFree": True,
                    }
                ),
            )
        )

        async def scenario() -> None:
            rows = await client.poll_purge_owner_work()
            self.assertEqual(rows, (work,))
            receipt = await client.acknowledge_purge_owner_receipt(
                request_id=work["requestId"],
                deletion_generation=work["deletionGeneration"],
                scope_digest=scope_digest,
                sink="continuity_checkpoint",
            )
            self.assertFalse(receipt["archiveCompleted"])

        asyncio.run(scenario())

        self.assertEqual(
            [
                request["url"].removeprefix("http://bot-api:8798")
                for request in session.requests
            ],
            [
                "/internal/conversation-archive/purge-owner/poll",
                "/internal/conversation-archive/purge-owner/ack",
            ],
        )
        ack_payload = json.loads(session.requests[1]["data"])
        self.assertEqual(
            ack_payload,
            {
                "requestId": "1" * 32,
                "deletionGeneration": 7,
                "scopeDigest": scope_digest,
                "sink": "continuity_checkpoint",
                "contentFree": True,
                "complete": True,
                "remainingCopies": 0,
                "manualReviewCount": 0,
            },
        )
        headers = session.requests[0]["headers"]
        purge_key = hmac.new(
            b"k" * 32,
            b"evelyn.private-conversation-archive.transport-key.v1\npurge-owner",
            hashlib.sha256,
        ).digest()
        canonical = "\n".join(
            (
                "purge-owner",
                "POST",
                "/internal/conversation-archive/purge-owner/poll",
                headers["X-Evelyn-Archive-Timestamp"],
                headers["X-Evelyn-Archive-Nonce"],
                hashlib.sha256(session.requests[0]["data"]).hexdigest(),
            )
        ).encode()
        self.assertTrue(
            hmac.compare_digest(
                headers["X-Evelyn-Archive-Signature"],
                hmac.new(purge_key, canonical, hashlib.sha256).hexdigest(),
            )
        )
        lineage_key = hmac.new(
            b"k" * 32,
            b"evelyn.private-conversation-archive.purge-lineage-key.v1\n",
            hashlib.sha256,
        ).digest()
        lineage = hmac.new(lineage_key, digestmod=hashlib.sha256)
        lineage.update(
            b"evelyn.private-conversation-archive.lineage.v1\nturn\n"
        )
        lineage.update(b'{"value":"turn-private-1"}')
        self.assertEqual(
            client.purge_lineage_handle("turn", " turn-private-1 "),
            lineage.hexdigest(),
        )
        self.assertNotEqual(purge_key, lineage_key)

    def test_purge_owner_client_rejects_unexpected_or_false_receipts(self) -> None:
        invalid_work = {
            "requestId": "1" * 32,
            "deletionGeneration": 7,
            "scopeDigest": "a" * 64,
            "reason": "user_requested",
            "requestedAt": "2026-08-28T00:00:00+00:00",
            "scopeAll": True,
            "guildId": None,
            "startedAt": None,
            "endedAt": None,
            "lineageHandles": [],
            "lineageComplete": False,
            "remainingSinks": ["continuity_checkpoint"],
            "contentFree": True,
            "rawActorId": "must-not-cross",
        }
        client, _session = self.build_client(
            (
                _FakeArchiveResponse(
                    {
                        "ok": True,
                        "workOrders": [invalid_work],
                        "contentFree": True,
                    }
                ),
            )
        )
        with self.assertRaisesRegex(
            ConversationArchiveTransportError,
            "archive_purge_owner_work_invalid",
        ):
            asyncio.run(client.poll_purge_owner_work())

        client, _session = self.build_client(
            (
                _FakeArchiveResponse(
                    {
                        "ok": True,
                        "state": "manual_review",
                        "archiveCompleted": True,
                        "contentFree": True,
                    }
                ),
            )
        )
        with self.assertRaisesRegex(
            ConversationArchiveTransportError,
            "archive_purge_owner_receipt_invalid",
        ):
            asyncio.run(
                client.acknowledge_purge_owner_receipt(
                    request_id="1" * 32,
                    deletion_generation=7,
                    scope_digest="a" * 64,
                    sink="continuity_checkpoint",
                )
            )


if __name__ == "__main__":
    unittest.main()
