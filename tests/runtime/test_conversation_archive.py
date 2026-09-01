from __future__ import annotations

import json
import sqlite3
import sys
import tempfile
import unittest
from contextlib import closing
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import patch


REPO_ROOT = next(
    path for path in Path(__file__).resolve().parents if (path / "main.py").exists()
)
RUNTIME_ROOT = REPO_ROOT / "evelyn_core" / "runtime"
if str(RUNTIME_ROOT) not in sys.path:
    sys.path.insert(0, str(RUNTIME_ROOT))

from evelyn_core import conversation_archive as archive_module  # noqa: E402
from evelyn_core.conversation_archive import (  # noqa: E402
    ARCHIVE_DELETION_AUDIT_TEXT,
    ARCHIVE_ADMIN_DELETION_AUDIT_TEXT,
    ARCHIVE_ADMIN_TOMBSTONE_TEXT,
    ARCHIVE_DEPENDENT_REDACTION_TEXT,
    ARCHIVE_RETENTION_TOMBSTONE_TEXT,
    ARCHIVE_TOMBSTONE_TEXT,
    ArchiveAuthorizationError,
    ArchiveIntegrityError,
    ArchivePreviewConflict,
    ArchivePreviewConsumed,
    ArchivePreviewExpired,
    ArchiveStaleEvent,
    ArchiveUnavailableError,
    ArchiveValidationError,
    ConversationArchive,
    archive_lineage_handle,
)


BASE = datetime(2026, 8, 1, 12, 0, tzinfo=timezone.utc)


class MutableClock:
    def __init__(self, value: datetime) -> None:
        self.value = value

    def __call__(self) -> datetime:
        return self.value


class ConversationArchiveTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        root = Path(self.temporary.name)
        self.primary = root / "primary" / "conversation.sqlite3"
        self.replica = root / "replica" / "conversation.sqlite3"
        self.anchor = root / "anchor" / "head.json"
        self.clock = MutableClock(BASE)
        self.record_counter = 0
        self.archive = ConversationArchive(
            primary_path=self.primary,
            replica_path=self.replica,
            anchor_path=self.anchor,
            integrity_key=b"archive-test-key-material-32-bytes-minimum",
            clock=self.clock,
            writer_lock_wait_seconds=0,
        ).open()

    def tearDown(self) -> None:
        self.archive.close()
        self.temporary.cleanup()

    def append_user(
        self,
        actor: str,
        body: str,
        *,
        guild: str = "guild-1",
        channel: str = "text-1",
        at: datetime = BASE,
        parent_ids: tuple[str, ...] = (),
        owner_name: str | None = None,
        record_type: str = "user_text",
    ):
        self.record_counter += 1
        return self.archive.append_record(
            mode="discord_shared",
            surface=(
                "minecraft" if record_type == "minecraft_command" else "discord"
            ),
            record_type=record_type,
            body=body,
            actor_external_id=actor,
            owner_name=owner_name or f"{actor}-display",
            guild_id=guild,
            channel_id=channel,
            started_at=at,
            ended_at=at,
            parent_ids=parent_ids,
            idempotency_key=f"record-{self.record_counter}",
            now=self.clock.value,
        )

    def append_reply(self, parent_id: str, body: str, *, guild: str = "guild-1"):
        self.record_counter += 1
        return self.archive.append_record(
            mode="discord_shared",
            surface="discord",
            record_type="evelyn_reply",
            body=body,
            guild_id=guild,
            channel_id="text-1",
            started_at=BASE + timedelta(seconds=1),
            ended_at=BASE + timedelta(seconds=1),
            parent_ids=(parent_id,),
            idempotency_key=f"record-{self.record_counter}",
            now=self.clock.value,
        )

    def activate_voice(self) -> None:
        self.archive.begin_ingest_generation(
            source_id="discord_gateway",
            generation="boot-a",
            activated_at=BASE,
            now=self.clock.value,
        )

    def voice_state(
        self,
        *,
        sequence: int,
        at: datetime,
        actor: str = "user-a",
        present: bool = True,
        consent: bool = True,
        self_mute: bool = False,
        server_mute: bool = False,
        self_deaf: bool = False,
        server_deaf: bool = False,
        suppressed: bool = False,
        known: bool = True,
        owner_name: str | None = None,
        idempotency_key: str | None = None,
        generation: str = "boot-a",
    ) -> bool:
        return self.archive.apply_voice_state(
            source_id="discord_gateway",
            generation=generation,
            event_sequence=sequence,
            idempotency_key=idempotency_key or f"event-{sequence}",
            actor_external_id=actor,
            owner_name=owner_name or f"{actor}-display",
            guild_id="guild-1",
            channel_id="voice-1",
            event_at=at,
            present=present,
            consent_current=consent,
            self_mute=self_mute,
            server_mute=server_mute,
            self_deaf=self_deaf,
            server_deaf=server_deaf,
            suppressed=suppressed,
            gateway_known=known,
            now=self.clock.value,
        )

    def test_self_view_is_exact_owner_plus_ownerless_descendants(self) -> None:
        self.activate_voice()
        self.voice_state(sequence=1, at=BASE)
        utterance = self.archive.append_record(
            mode="discord_shared",
            surface="discord",
            record_type="final_stt",
            body="final words",
            actor_external_id="user-a",
            owner_name="user-a-display",
            guild_id="guild-1",
            channel_id="voice-1",
            started_at=BASE + timedelta(seconds=1),
            ended_at=BASE + timedelta(seconds=2),
            idempotency_key="final-stt-1",
            now=self.clock.value,
        )
        reply = self.append_reply(utterance.record_id, "direct answer")
        other = self.append_user("user-b", "independent statement")

        visible_a = self.archive.read_self(
            actor_external_id="user-a", guild_id="guild-1"
        )
        visible_b = self.archive.read_self(
            actor_external_id="user-b", guild_id="guild-1"
        )

        self.assertEqual(
            {row.record_id for row in visible_a}, {utterance.record_id, reply.record_id}
        )
        self.assertEqual([row.record_id for row in visible_b], [other.record_id])
        self.assertNotIn("independent statement", {row.body for row in visible_a})

    def test_feedback_binding_accepts_only_exact_voice_root_and_ancestor_lineage(
        self,
    ) -> None:
        self.activate_voice()
        self.voice_state(sequence=1, at=BASE)
        task_id = "voice-turn-1"
        session_id = "voice-session-1"
        utterance = self.archive.append_record(
            mode="discord_shared",
            surface="discord",
            record_type="final_stt",
            body="final words",
            actor_external_id="user-a",
            owner_name="user-a-display",
            guild_id="guild-1",
            channel_id="voice-1",
            started_at=BASE + timedelta(seconds=1),
            ended_at=BASE + timedelta(seconds=2),
            lineage={"turn": (task_id,), "session": (session_id,)},
            idempotency_key="feedback-final-stt-1",
            now=self.clock.value,
        )
        reply = self.archive.append_record(
            mode="discord_shared",
            surface="discord",
            record_type="evelyn_reply",
            body="voice answer",
            guild_id="guild-1",
            channel_id="voice-1",
            started_at=BASE + timedelta(seconds=3),
            ended_at=BASE + timedelta(seconds=3),
            parent_ids=(utterance.record_id,),
            lineage={"turn": (task_id,)},
            idempotency_key="feedback-voice-reply-1",
            now=self.clock.value,
        )

        binding = self.archive.feedback_source_binding(
            authorized=True,
            source_record_id=reply.record_id,
            identity_surface="discord",
            actor_external_id="user-a",
            task_id=task_id,
            session_id=session_id,
            guild_id="guild-1",
            channel_id="voice-1",
            feedback_surface="voice",
        )
        self.assertEqual(binding.record_id, reply.record_id)
        for mismatch in (
            {"task_id": "voice-turn-other"},
            {"session_id": "voice-session-other"},
            {"feedback_surface": "discord"},
        ):
            with self.assertRaises(ArchiveAuthorizationError):
                self.archive.feedback_source_binding(
                    authorized=True,
                    source_record_id=reply.record_id,
                    identity_surface="discord",
                    actor_external_id="user-a",
                    task_id=mismatch.get("task_id", task_id),
                    session_id=mismatch.get("session_id", session_id),
                    guild_id="guild-1",
                    channel_id="voice-1",
                    feedback_surface=mismatch.get("feedback_surface", "voice"),
                )

    def test_mute_deaf_and_unknown_close_eligibility_but_text_is_allowed(self) -> None:
        self.activate_voice()
        self.voice_state(sequence=1, at=BASE)
        self.voice_state(sequence=2, at=BASE + timedelta(seconds=5), self_mute=True)
        self.assertTrue(
            self.archive.is_voice_capture_eligible(
                actor_external_id="user-a",
                guild_id="guild-1",
                channel_id="voice-1",
                at=BASE + timedelta(seconds=4),
            )
        )
        self.assertFalse(
            self.archive.is_voice_capture_eligible(
                actor_external_id="user-a",
                guild_id="guild-1",
                channel_id="voice-1",
                at=BASE + timedelta(seconds=5),
            )
        )
        with self.assertRaises(ArchiveAuthorizationError):
            self.archive.append_record(
                mode="discord_shared",
                surface="discord",
                record_type="final_stt",
                body="must not be admitted",
                actor_external_id="user-a",
                owner_name="user-a-display",
                guild_id="guild-1",
                channel_id="voice-1",
                started_at=BASE + timedelta(seconds=6),
                ended_at=BASE + timedelta(seconds=7),
                idempotency_key="ineligible-stt",
                now=self.clock.value,
            )
        chat = self.append_user(
            "user-a", "chat while muted", at=BASE + timedelta(seconds=6)
        )
        child = self.append_reply(chat.record_id, "chat child")
        self.voice_state(
            sequence=3,
            at=BASE + timedelta(seconds=10),
            self_mute=False,
            self_deaf=True,
        )
        self.voice_state(
            sequence=4,
            at=BASE + timedelta(seconds=15),
            self_deaf=False,
        )
        self.voice_state(
            sequence=5,
            at=BASE + timedelta(seconds=20),
            known=False,
        )

        intervals = self.archive.read_participation_admin(authorized=True)
        self.assertEqual({row.owner_name for row in intervals}, {"user-a-display"})
        presence = [row for row in intervals if row.interval_kind == "presence"]
        eligible = [row for row in intervals if row.interval_kind == "eligible"]
        self.assertEqual(
            [(row.started_at, row.ended_at) for row in presence],
            [(BASE, BASE + timedelta(seconds=20))],
        )
        self.assertEqual(
            [(row.started_at, row.ended_at) for row in eligible],
            [
                (BASE, BASE + timedelta(seconds=5)),
                (BASE + timedelta(seconds=15), BASE + timedelta(seconds=20)),
            ],
        )
        visible = self.archive.read_self(
            actor_external_id="user-a", guild_id="guild-1"
        )
        self.assertEqual({row.record_id for row in visible}, {chat.record_id, child.record_id})

    def test_voice_transitions_are_durable_admin_only_and_paginated(self) -> None:
        self.activate_voice()
        self.voice_state(sequence=1, at=BASE)
        self.voice_state(
            sequence=2,
            at=BASE + timedelta(seconds=1),
            consent=False,
            self_mute=True,
            server_mute=True,
            self_deaf=True,
            server_deaf=True,
            suppressed=True,
            known=False,
        )
        self.voice_state(
            sequence=3,
            at=BASE + timedelta(seconds=2),
            present=False,
        )

        with self.assertRaises(ArchiveAuthorizationError):
            self.archive.read_voice_state_transitions_admin_page(
                authorized=False
            )
        first = self.archive.read_voice_state_transitions_admin_page(
            authorized=True,
            guild_id="guild-1",
            limit=2,
        )
        self.assertEqual(len(first.transitions), 2)
        assert first.next_cursor is not None
        second = self.archive.read_voice_state_transitions_admin_page(
            authorized=True,
            guild_id="guild-1",
            cursor=first.next_cursor,
            limit=2,
        )
        self.assertEqual(len(second.transitions), 1)
        self.assertIsNone(second.next_cursor)
        muted = first.transitions[1]
        self.assertEqual(
            (
                muted.present,
                muted.consent_current,
                muted.self_mute,
                muted.server_mute,
                muted.self_deaf,
                muted.server_deaf,
                muted.suppressed,
                muted.gateway_known,
            ),
            (True, False, True, True, True, True, True, False),
        )
        with closing(sqlite3.connect(self.primary)) as connection:
            columns = {
                row[1]
                for row in connection.execute(
                    "PRAGMA table_info(voice_state_transitions)"
                )
            }
        self.assertNotIn("body", columns)
        self.assertNotIn("audio", columns)
        self.assertNotIn("text", columns)

    def test_admin_list_cursor_binds_kind_filter_generation_and_auth_tag(self) -> None:
        self.activate_voice()
        self.voice_state(sequence=1, at=BASE)
        self.voice_state(sequence=2, at=BASE + timedelta(seconds=1))
        transition_page = self.archive.read_voice_state_transitions_admin_page(
            authorized=True,
            guild_id="guild-1",
            limit=1,
        )
        assert transition_page.next_cursor is not None
        for read in (
            lambda: self.archive.read_participation_admin_page(
                authorized=True,
                cursor=transition_page.next_cursor,
                limit=1,
            ),
            lambda: self.archive.read_voice_state_transitions_admin_page(
                authorized=True,
                guild_id="guild-2",
                cursor=transition_page.next_cursor,
                limit=1,
            ),
        ):
            with self.assertRaises(ArchiveValidationError) as mismatch:
                read()
            self.assertEqual(
                mismatch.exception.code,
                "archive_admin_list_cursor_scope_mismatch",
            )
        middle = len(transition_page.next_cursor) // 2
        replacement = (
            "A" if transition_page.next_cursor[middle] != "A" else "B"
        )
        tampered = (
            transition_page.next_cursor[:middle]
            + replacement
            + transition_page.next_cursor[middle + 1 :]
        )
        with self.assertRaises(ArchiveValidationError):
            self.archive.read_voice_state_transitions_admin_page(
                authorized=True,
                guild_id="guild-1",
                cursor=tampered,
                limit=1,
            )
        self.append_user("user-b", "generation change")
        with self.assertRaises(ArchiveStaleEvent) as stale:
            self.archive.read_voice_state_transitions_admin_page(
                authorized=True,
                guild_id="guild-1",
                cursor=transition_page.next_cursor,
                limit=1,
            )
        self.assertEqual(
            stale.exception.code,
            "archive_admin_list_cursor_stale",
        )

    def test_participation_and_legal_pages_cover_every_row(self) -> None:
        self.activate_voice()
        self.voice_state(sequence=1, at=BASE, owner_name="Alice")
        self.voice_state(
            sequence=2,
            at=BASE + timedelta(seconds=1),
            self_mute=True,
            owner_name="Alice",
        )
        self.voice_state(
            sequence=3,
            at=BASE + timedelta(seconds=2),
            owner_name="Alice",
        )
        first = self.archive.read_participation_admin_page(
            authorized=True,
            limit=2,
        )
        assert first.next_cursor is not None
        second = self.archive.read_participation_admin_page(
            authorized=True,
            cursor=first.next_cursor,
            limit=2,
        )
        self.assertEqual(len(first.intervals) + len(second.intervals), 3)

        preview = self.archive.preview_user_deletion(
            actor_external_id="user-a",
            request_guild_id="guild-1",
            now=BASE,
        )
        self.archive.apply_user_deletion(
            preview_id=preview.preview_id,
            actor_external_id="user-a",
            now=BASE + timedelta(seconds=1),
        )
        events = []
        cursor = None
        while True:
            page = self.archive.read_legal_minimal_events_page(
                authorized=True,
                cursor=cursor,
                limit=2,
            )
            events.extend(page.events)
            cursor = page.next_cursor
            if cursor is None:
                break
        self.assertEqual(len(events), 6)

    def test_record_append_reconciles_same_retry_and_rejects_payload_change(self) -> None:
        first = self.archive.append_record(
            mode="discord_shared",
            surface="discord",
            record_type="user_text",
            body="committed response was lost",
            actor_external_id="user-a",
            owner_name="user-a-display",
            guild_id="guild-1",
            channel_id="text-1",
            started_at=BASE,
            ended_at=BASE,
            idempotency_key="retry-key",
            now=BASE,
        )
        generation_after_first = self.archive.generation
        retried = self.archive.append_record(
            mode="discord_shared",
            surface="discord",
            record_type="user_text",
            body="committed response was lost",
            actor_external_id="user-a",
            owner_name="user-a-display",
            guild_id="guild-1",
            channel_id="text-1",
            started_at=BASE,
            ended_at=BASE,
            idempotency_key="retry-key",
            record_id="a-different-client-retry-id",
            now=BASE,
        )
        self.assertEqual(retried, first)
        self.assertEqual(self.archive.generation, generation_after_first)
        with self.assertRaises(ArchiveStaleEvent) as captured:
            self.archive.append_record(
                mode="discord_shared",
                surface="discord",
                record_type="user_text",
                body="changed retry payload",
                actor_external_id="user-a",
                owner_name="user-a-display",
                guild_id="guild-1",
                channel_id="text-1",
                started_at=BASE,
                ended_at=BASE,
                idempotency_key="retry-key",
                now=BASE,
            )
        self.assertEqual(captured.exception.code, "archive_idempotency_conflict")

        with self.assertRaises(ArchiveStaleEvent) as renamed:
            self.archive.append_record(
                mode="discord_shared",
                surface="discord",
                record_type="user_text",
                body="committed response was lost",
                actor_external_id="user-a",
                owner_name="renamed-user-a",
                guild_id="guild-1",
                channel_id="text-1",
                started_at=BASE,
                ended_at=BASE,
                idempotency_key="retry-key",
                now=BASE,
            )
        self.assertEqual(renamed.exception.code, "archive_idempotency_conflict")

    def test_owner_names_are_normalized_snapshotted_and_hidden_from_self(self) -> None:
        first = self.append_user(
            "user-a",
            "first",
            owner_name="  E\u0301velyn  ",
        )
        second = self.append_user(
            "user-a",
            "second",
            at=BASE + timedelta(seconds=2),
            owner_name="Latest Name",
        )
        stale = self.append_user(
            "user-a",
            "late-arriving older event",
            at=BASE + timedelta(seconds=1),
            owner_name="Stale Name",
        )

        admin = {row.record_id: row for row in self.archive.read_admin(authorized=True)}
        self.assertEqual(admin[first.record_id].owner_name, "Évelyn")
        self.assertEqual(admin[second.record_id].owner_name, "Latest Name")
        self.assertEqual(admin[stale.record_id].owner_name, "Stale Name")
        self.assertTrue(
            all(
                row.owner_name is None
                for row in self.archive.read_self(
                    actor_external_id="user-a", guild_id="guild-1"
                )
            )
        )
        with closing(sqlite3.connect(self.primary)) as connection:
            current_name = connection.execute(
                "SELECT current_display_name FROM principals"
            ).fetchone()[0]
        self.assertEqual(current_name, "Latest Name")

        with self.assertRaises(ArchiveValidationError) as missing:
            self.archive.append_record(
                mode="discord_shared",
                surface="discord",
                record_type="user_text",
                body="missing display name",
                actor_external_id="user-b",
                guild_id="guild-1",
                channel_id="text-1",
                started_at=BASE,
                ended_at=BASE,
                idempotency_key="missing-owner-name",
            )
        self.assertEqual(missing.exception.code, "archive_owner_name_invalid")

    def test_admin_pages_expose_next_cursor_without_silent_truncation(self) -> None:
        records = tuple(
            self.append_user("user-a", f"row-{index}") for index in range(3)
        )

        first = self.archive.read_admin_page(authorized=True, limit=2)
        self.assertEqual(
            [row.record_id for row in first.records],
            [records[0].record_id, records[1].record_id],
        )
        self.assertIsNotNone(first.next_cursor)
        self.assertEqual(first.snapshot_generation, self.archive.generation)
        second = self.archive.read_admin_page(
            authorized=True,
            cursor=first.next_cursor,
            limit=2,
        )
        self.assertEqual(
            [row.record_id for row in second.records],
            [records[2].record_id],
        )
        self.assertIsNone(second.next_cursor)
        self.assertEqual(second.snapshot_generation, first.snapshot_generation)
        with self.assertRaises(ArchiveAuthorizationError):
            self.archive.read_admin_page(authorized=False)

    def test_admin_page_cursor_is_generation_bound_and_authenticated(self) -> None:
        self.append_user("user-a", "first")
        self.append_user("user-a", "second")
        page = self.archive.read_admin_page(authorized=True, limit=1)
        assert page.next_cursor is not None

        self.append_user("user-b", "generation changes")
        with self.assertRaises(ArchiveStaleEvent) as stale:
            self.archive.read_admin_page(
                authorized=True,
                cursor=page.next_cursor,
                limit=1,
            )
        self.assertEqual(stale.exception.code, "archive_admin_cursor_stale")

        middle = len(page.next_cursor) // 2
        replacement = "A" if page.next_cursor[middle] != "A" else "B"
        tampered = page.next_cursor[:middle] + replacement + page.next_cursor[middle + 1 :]
        with self.assertRaises(ArchiveValidationError) as invalid:
            self.archive.read_admin_page(
                authorized=True,
                cursor=tampered,
                limit=1,
            )
        self.assertEqual(invalid.exception.code, "archive_admin_cursor_invalid")

    def test_self_page_cursor_is_caller_guild_query_and_generation_bound(self) -> None:
        records = tuple(
            self.append_user("user-a", f"self-row-{index}") for index in range(3)
        )
        self.append_user("user-b", "other user")
        self.append_user("user-a", "other guild", guild="guild-2")
        first = self.archive.read_self_page(
            actor_external_id="user-a",
            guild_id="guild-1",
            limit=2,
        )
        self.assertEqual(
            [row.record_id for row in first.records],
            [records[0].record_id, records[1].record_id],
        )
        assert first.next_cursor is not None

        for actor, guild, start, end in (
            ("user-b", "guild-1", None, None),
            ("user-a", "guild-2", None, None),
            ("user-a", "guild-1", BASE - timedelta(seconds=1), BASE),
        ):
            with self.subTest(actor=actor, guild=guild, start=start):
                with self.assertRaises(ArchiveAuthorizationError) as mismatch:
                    self.archive.read_self_page(
                        actor_external_id=actor,
                        guild_id=guild,
                        started_at=start,
                        ended_at=end,
                        cursor=first.next_cursor,
                        limit=1,
                    )
                self.assertEqual(
                    mismatch.exception.code,
                    "archive_self_cursor_scope_mismatch",
                )

        self.append_user("user-b", "changes generation")
        with self.assertRaises(ArchiveStaleEvent) as stale:
            self.archive.read_self_page(
                actor_external_id="user-a",
                guild_id="guild-1",
                cursor=first.next_cursor,
                limit=1,
            )
        self.assertEqual(stale.exception.code, "archive_self_cursor_stale")

        middle = len(first.next_cursor) // 2
        replacement = "A" if first.next_cursor[middle] != "A" else "B"
        tampered = (
            first.next_cursor[:middle]
            + replacement
            + first.next_cursor[middle + 1 :]
        )
        with self.assertRaises(ArchiveValidationError) as invalid:
            self.archive.read_self_page(
                actor_external_id="user-a",
                guild_id="guild-1",
                cursor=tampered,
                limit=1,
            )
        self.assertEqual(invalid.exception.code, "archive_self_cursor_invalid")

    def test_multi_owner_derived_row_requires_every_parent_in_self_scope(self) -> None:
        owned_a = self.append_user("user-a", "question a")
        owned_b = self.append_user("user-b", "question b")
        shared = self.archive.append_record(
            mode="discord_shared",
            surface="discord",
            record_type="evelyn_reply",
            body="combined answer",
            guild_id="guild-1",
            channel_id="text-1",
            started_at=BASE + timedelta(seconds=1),
            ended_at=BASE + timedelta(seconds=1),
            parent_ids=(owned_a.record_id, owned_b.record_id),
            idempotency_key="combined-answer",
        )
        for actor, owned in (("user-a", owned_a), ("user-b", owned_b)):
            visible = self.archive.read_self(
                actor_external_id=actor, guild_id="guild-1"
            )
            self.assertIn(owned.record_id, {row.record_id for row in visible})
            self.assertNotIn(shared.record_id, {row.record_id for row in visible})

    def test_derived_append_infers_exact_parent_scope_and_rejects_ambiguity(self) -> None:
        first = self.append_user(
            "user-a",
            "minecraft request",
            channel="text-7",
            record_type="minecraft_command",
        )
        second = self.append_user(
            "user-a",
            "follow-up",
            channel="text-7",
            record_type="minecraft_command",
        )
        result = self.archive.append_derived_record(
            surface="minecraft",
            record_type="minecraft_result",
            body="action completed",
            started_at=BASE + timedelta(seconds=1),
            ended_at=BASE + timedelta(seconds=2),
            parent_ids=(first.record_id, second.record_id),
            idempotency_key="minecraft-result-1",
        )
        self.assertEqual(result.mode, "discord_shared")
        self.assertEqual(result.guild_id, "guild-1")
        self.assertEqual(result.channel_id, "text-7")
        self.assertIsNone(result.owner_name)

        ordinary_text = self.append_user(
            "user-a", "not an authorized command", channel="text-7"
        )
        with self.assertRaises(ArchiveValidationError) as wrong_parent:
            self.archive.append_derived_record(
                surface="minecraft",
                record_type="minecraft_result",
                body="must not attach to ordinary text",
                started_at=BASE,
                ended_at=BASE,
                parent_ids=(ordinary_text.record_id,),
                idempotency_key="minecraft-wrong-parent",
            )
        self.assertEqual(
            wrong_parent.exception.code,
            "archive_minecraft_parent_invalid",
        )

        other_channel = self.append_user(
            "user-a", "different channel", channel="text-8"
        )
        with self.assertRaises(ArchiveValidationError) as ambiguous_channel:
            self.archive.append_derived_record(
                surface="discord",
                record_type="task_result",
                body="must not guess a channel",
                started_at=BASE,
                ended_at=BASE,
                parent_ids=(first.record_id, other_channel.record_id),
                idempotency_key="ambiguous-channel",
            )
        self.assertEqual(
            ambiguous_channel.exception.code,
            "archive_parent_scope_ambiguous",
        )

        other_guild = self.append_user(
            "user-a", "different guild", guild="guild-2", channel="text-7"
        )
        with self.assertRaises(ArchiveValidationError) as ambiguous_guild:
            self.archive.append_derived_record(
                surface="discord",
                record_type="task_result",
                body="must not guess a guild",
                started_at=BASE,
                ended_at=BASE,
                parent_ids=(first.record_id, other_guild.record_id),
                idempotency_key="ambiguous-guild",
            )
        self.assertEqual(
            ambiguous_guild.exception.code,
            "archive_parent_scope_ambiguous",
        )

    def test_voice_events_are_generation_ordered_and_idempotent(self) -> None:
        self.activate_voice()
        self.assertTrue(self.voice_state(sequence=1, at=BASE, idempotency_key="same"))
        self.assertFalse(self.voice_state(sequence=1, at=BASE, idempotency_key="same"))
        with self.assertRaises(ArchiveStaleEvent):
            self.voice_state(
                sequence=1,
                at=BASE,
                idempotency_key="same",
                self_mute=True,
            )
        self.archive.begin_ingest_generation(
            source_id="discord_gateway",
            generation="boot-b",
            activated_at=BASE + timedelta(seconds=30),
            now=self.clock.value,
        )
        with self.assertRaises(ArchiveStaleEvent):
            self.voice_state(
                sequence=2,
                at=BASE + timedelta(seconds=31),
                generation="boot-a",
            )

    def test_unapproved_capture_types_and_async_stale_commit_are_rejected(self) -> None:
        with self.assertRaises(ArchiveValidationError):
            self.archive.append_record(
                mode="discord_shared",
                surface="discord",
                record_type="partial_stt",
                body="partial",
                actor_external_id="user-a",
                guild_id="guild-1",
                channel_id="voice-1",
                started_at=BASE,
                ended_at=BASE,
                idempotency_key="partial-stt",
            )
        captured_generation = self.archive.generation
        self.append_user("user-b", "intervening event")
        with self.assertRaises(ArchiveStaleEvent):
            self.archive.append_record(
                mode="discord_shared",
                surface="discord",
                record_type="user_text",
                body="late callback",
                actor_external_id="user-a",
                owner_name="user-a-display",
                guild_id="guild-1",
                channel_id="text-1",
                started_at=BASE,
                ended_at=BASE,
                idempotency_key="late-callback",
                expected_generation=captured_generation,
            )

    def test_all_period_deletion_scrubs_primary_replica_and_lineage(self) -> None:
        secret_one = "unique-delete-secret-one"
        secret_two = "unique-delete-secret-two"
        first = self.append_user("user-a", secret_one, guild="guild-1")
        self.append_reply(first.record_id, "answer repeating unique-delete-secret-one")
        second = self.append_user("user-a", secret_two, guild="guild-2")
        dependent_other = self.append_user(
            "user-b",
            "quoted unique-delete-secret-one",
            parent_ids=(first.record_id,),
        )
        independent_other = self.append_user("user-b", "independent survives")
        preview = self.archive.preview_user_deletion(
            actor_external_id="user-a",
            request_guild_id="guild-1",
            now=BASE,
        )
        self.assertTrue(preview.all_guilds)
        self.assertEqual(preview.counts_by_guild, {"guild-1": 1, "guild-2": 1})
        self.assertEqual(preview.owned_record_count, 2)
        self.assertEqual(preview.dependent_record_count, 2)

        result = self.archive.apply_user_deletion(
            preview_id=preview.preview_id,
            actor_external_id="user-a",
            now=BASE + timedelta(seconds=1),
        )

        self.assertEqual(result.status, "local_fully_purged")
        self.assertEqual(result.display_text, ARCHIVE_DELETION_AUDIT_TEXT)
        self.assertEqual(self.archive.read_self(actor_external_id="user-a", guild_id="guild-1"), ())
        visible_b = self.archive.read_self(
            actor_external_id="user-b", guild_id="guild-1"
        )
        self.assertIn(independent_other.record_id, {row.record_id for row in visible_b})
        self.assertNotIn(dependent_other.record_id, {row.record_id for row in visible_b})
        self.assertIn(ARCHIVE_TOMBSTONE_TEXT, {row.body for row in visible_b})
        admin = self.archive.read_admin(authorized=True, include_quarantined=True)
        self.assertEqual(
            sum(row.body == ARCHIVE_TOMBSTONE_TEXT for row in admin), 2
        )
        self.assertEqual(
            sum(row.body == ARCHIVE_DEPENDENT_REDACTION_TEXT for row in admin), 2
        )
        self.assertIn("independent survives", {row.body for row in admin})
        for path in (self.primary, self.replica):
            raw = path.read_bytes()
            self.assertNotIn(secret_one.encode(), raw)
            self.assertNotIn(secret_two.encode(), raw)
            self.assertNotIn(b"quoted unique-delete-secret-one", raw)
        with closing(sqlite3.connect(self.primary)) as connection:
            audit = connection.execute(
                "SELECT display_text, status, primary_status, replica_status FROM deletion_audits"
            ).fetchone()
            self.assertEqual(
                audit,
                (
                    ARCHIVE_DELETION_AUDIT_TEXT,
                    "local_fully_purged",
                    "local_fully_purged",
                    "deleted_verified",
                ),
            )
            principal_rows = connection.execute("SELECT COUNT(*) FROM principals").fetchone()[0]
            self.assertEqual(principal_rows, 1)
        with self.assertRaises(ArchivePreviewConsumed):
            self.archive.apply_user_deletion(
                preview_id=preview.preview_id,
                actor_external_id="user-a",
                now=BASE + timedelta(seconds=2),
            )

    def test_deleted_record_receipts_block_late_reappearance_after_restart(self) -> None:
        secret = "late replay must stay deleted"
        record = self.append_user("user-a", secret)
        preview = self.archive.preview_user_deletion(
            actor_external_id="user-a", request_guild_id="guild-1", now=BASE
        )
        self.archive.apply_user_deletion(
            preview_id=preview.preview_id,
            actor_external_id="user-a",
            now=BASE + timedelta(seconds=1),
        )

        for idempotency_key, record_id in (
            ("record-1", record.record_id),
            ("different-retry-key", record.record_id),
        ):
            with self.subTest(idempotency_key=idempotency_key):
                with self.assertRaises(ArchiveStaleEvent) as captured:
                    self.archive.append_record(
                        mode="discord_shared",
                        surface="discord",
                        record_type="user_text",
                        body=secret,
                        actor_external_id="user-a",
                        owner_name="user-a-display",
                        guild_id="guild-1",
                        channel_id="text-1",
                        started_at=BASE,
                        ended_at=BASE,
                        idempotency_key=idempotency_key,
                        record_id=record_id,
                        now=BASE + timedelta(seconds=2),
                    )
                self.assertEqual(
                    captured.exception.code, "archive_idempotency_retired"
                )

        self.archive.close()
        self.archive.open()
        with self.assertRaises(ArchiveStaleEvent) as restarted:
            self.archive.append_record(
                mode="discord_shared",
                surface="discord",
                record_type="user_text",
                body=secret,
                actor_external_id="user-a",
                owner_name="user-a-display",
                guild_id="guild-1",
                channel_id="text-1",
                started_at=BASE,
                ended_at=BASE,
                idempotency_key="record-1",
                now=BASE + timedelta(seconds=3),
            )
        self.assertEqual(restarted.exception.code, "archive_idempotency_retired")
        for path in (self.primary, self.replica):
            self.assertNotIn(secret.encode(), path.read_bytes())

    def test_deleted_voice_receipt_blocks_late_reappearance(self) -> None:
        self.activate_voice()
        self.voice_state(
            sequence=1,
            at=BASE,
            idempotency_key="voice-event-to-retire",
        )
        preview = self.archive.preview_user_deletion(
            actor_external_id="user-a", request_guild_id="guild-1", now=BASE
        )
        self.archive.apply_user_deletion(
            preview_id=preview.preview_id,
            actor_external_id="user-a",
            now=BASE + timedelta(seconds=1),
        )

        with self.assertRaises(ArchiveStaleEvent) as captured:
            self.voice_state(
                sequence=1,
                at=BASE,
                idempotency_key="voice-event-to-retire",
            )
        self.assertEqual(captured.exception.code, "archive_idempotency_retired")
        self.assertEqual(self.archive.read_participation_admin(authorized=True), ())

    def test_deletion_keeps_placeholder_anonymous_and_legal_minimum_admin_only(self) -> None:
        event_at = BASE + timedelta(seconds=47)
        owned = self.append_user(
            "user-a",
            "private record body",
            at=event_at,
            owner_name="Alice",
        )
        self.append_user(
            "user-b",
            "shared dependent body",
            at=event_at,
            parent_ids=(owned.record_id,),
            owner_name="Bob",
        )
        preview = self.archive.preview_user_deletion(
            actor_external_id="user-a", request_guild_id="guild-1", now=BASE
        )
        self.archive.apply_user_deletion(
            preview_id=preview.preview_id,
            actor_external_id="user-a",
            now=BASE + timedelta(seconds=1),
        )

        tombstone = next(
            row
            for row in self.archive.read_admin(
                authorized=True, include_quarantined=True
            )
            if row.created_sequence == owned.created_sequence
        )
        self.assertEqual(tombstone.body, ARCHIVE_TOMBSTONE_TEXT)
        self.assertIsNone(tombstone.owner_name)
        self.assertEqual(tombstone.started_at, BASE)
        self.assertEqual(tombstone.ended_at, BASE)
        self.assertIsNone(tombstone.owner_principal_id)
        self.assertIsNone(tombstone.guild_id)
        self.assertIsNone(tombstone.channel_id)
        visible_b = self.archive.read_self(
            actor_external_id="user-b", guild_id="guild-1"
        )
        marker = next(row for row in visible_b if row.body == ARCHIVE_TOMBSTONE_TEXT)
        self.assertIsNone(marker.owner_name)
        remnants = self.archive.read_legal_minimal_events(authorized=True)
        self.assertEqual(
            {(event.owner_name, event.occurred_at) for event in remnants},
            {("Alice", event_at), ("Bob", event_at)},
        )
        self.assertNotIn("Alice", self.anchor.read_text(encoding="utf-8"))

    def test_deleted_voice_intervals_leave_admin_only_name_and_time(self) -> None:
        self.activate_voice()
        self.voice_state(
            sequence=1,
            at=BASE + timedelta(seconds=37),
            owner_name="Alice",
        )
        preview = self.archive.preview_user_deletion(
            actor_external_id="user-a", request_guild_id="guild-1", now=BASE
        )
        self.assertEqual(preview.interval_count, 2)
        self.archive.apply_user_deletion(
            preview_id=preview.preview_id,
            actor_external_id="user-a",
            now=BASE + timedelta(seconds=1),
        )

        self.assertEqual(self.archive.read_participation_admin(authorized=True), ())
        with self.assertRaises(ArchiveAuthorizationError):
            self.archive.read_legal_minimal_events(authorized=False)
        with self.assertRaises(ArchiveAuthorizationError):
            self.archive.read_legal_minimal_events(authorized="yes")  # type: ignore[arg-type]
        remnants = self.archive.read_legal_minimal_events(authorized=True)
        self.assertEqual(len(remnants), 3)
        self.assertEqual({event.owner_name for event in remnants}, {"Alice"})
        self.assertEqual(
            {event.occurred_at for event in remnants},
            {BASE + timedelta(seconds=37)},
        )
        with closing(sqlite3.connect(self.primary)) as connection:
            columns = {
                row[1] for row in connection.execute("PRAGMA table_info(legal_minimal_events)")
            }
        self.assertEqual(columns, {"event_id", "owner_name", "occurred_at_us"})
        self.assertNotIn("Alice", self.anchor.read_text(encoding="utf-8"))

    def test_preview_is_one_minute_and_target_generation_bound(self) -> None:
        self.append_user("user-a", "delete me")
        expired = self.archive.preview_user_deletion(
            actor_external_id="user-a", request_guild_id="guild-1", now=BASE
        )
        with self.assertRaises(ArchivePreviewExpired):
            self.archive.apply_user_deletion(
                preview_id=expired.preview_id,
                actor_external_id="user-a",
                now=BASE + timedelta(seconds=60),
            )
        preview = self.archive.preview_user_deletion(
            actor_external_id="user-a",
            request_guild_id="guild-1",
            now=BASE + timedelta(seconds=61),
        )
        self.append_user("user-b", "unrelated generation change")
        with self.assertRaises(ArchivePreviewConflict):
            self.archive.apply_user_deletion(
                preview_id=preview.preview_id,
                actor_external_id="user-a",
                now=BASE + timedelta(seconds=62),
            )
        with self.assertRaises(ArchivePreviewConsumed):
            self.archive.apply_user_deletion(
                preview_id=preview.preview_id,
                actor_external_id="user-a",
                now=BASE + timedelta(seconds=63),
            )

    def test_partial_deletion_splits_voice_intervals(self) -> None:
        self.activate_voice()
        self.voice_state(sequence=1, at=BASE)
        preview = self.archive.preview_user_deletion(
            actor_external_id="user-a",
            request_guild_id="guild-1",
            started_at=BASE + timedelta(seconds=5),
            ended_at=BASE + timedelta(seconds=10),
            now=BASE,
        )
        self.assertFalse(preview.all_guilds)
        self.assertEqual(preview.interval_count, 2)
        result = self.archive.apply_user_deletion(
            preview_id=preview.preview_id,
            actor_external_id="user-a",
            now=BASE + timedelta(seconds=1),
        )
        self.assertEqual(result.status, "local_fully_purged")
        intervals = self.archive.read_participation_admin(authorized=True)
        self.assertEqual(len(intervals), 4)
        self.assertEqual(
            {(row.started_at, row.ended_at) for row in intervals},
            {
                (BASE, BASE + timedelta(seconds=5)),
                (BASE + timedelta(seconds=10), None),
            },
        )

    def test_partial_and_full_deletion_remove_exact_voice_transitions(self) -> None:
        self.activate_voice()
        self.voice_state(sequence=1, at=BASE, owner_name="Alice")
        self.voice_state(
            sequence=2,
            at=BASE + timedelta(seconds=5),
            self_mute=True,
            owner_name="Alice",
        )
        self.voice_state(
            sequence=3,
            at=BASE + timedelta(seconds=10),
            owner_name="Alice",
        )
        preview = self.archive.preview_user_deletion(
            actor_external_id="user-a",
            request_guild_id="guild-1",
            started_at=BASE + timedelta(seconds=4),
            ended_at=BASE + timedelta(seconds=9),
            now=BASE,
        )
        self.archive.apply_user_deletion(
            preview_id=preview.preview_id,
            actor_external_id="user-a",
            now=BASE + timedelta(seconds=1),
        )

        page = self.archive.read_voice_state_transitions_admin_page(
            authorized=True
        )
        self.assertEqual(
            [row.event_at for row in page.transitions],
            [BASE, BASE + timedelta(seconds=10)],
        )
        full = self.archive.preview_user_deletion(
            actor_external_id="user-a",
            request_guild_id="guild-1",
            now=BASE + timedelta(seconds=2),
        )
        self.archive.apply_user_deletion(
            preview_id=full.preview_id,
            actor_external_id="user-a",
            now=BASE + timedelta(seconds=3),
        )
        self.assertEqual(
            self.archive.read_voice_state_transitions_admin_page(
                authorized=True
            ).transitions,
            (),
        )
        with closing(sqlite3.connect(self.primary)) as connection:
            legal_columns = {
                row[1]
                for row in connection.execute(
                    "PRAGMA table_info(legal_minimal_events)"
                )
            }
        self.assertEqual(
            legal_columns,
            {"event_id", "owner_name", "occurred_at_us"},
        )

    def test_retention_prunes_oldest_first_through_tombstone_path(self) -> None:
        old = self.append_user("user-a", "expired body", at=BASE)
        newer_time = BASE + timedelta(days=2)
        newer = self.append_user("user-b", "not yet expired", at=newer_time)
        prune_now = BASE + timedelta(days=31)

        result = self.archive.prune_expired(now=prune_now, batch_size=1)

        self.assertIsNotNone(result)
        assert result is not None
        self.assertEqual(result.affected_records, 1)
        admin = self.archive.read_admin(authorized=True)
        by_sequence = {row.created_sequence: row for row in admin}
        self.assertEqual(by_sequence[old.created_sequence].body, ARCHIVE_RETENTION_TOMBSTONE_TEXT)
        self.assertEqual(by_sequence[newer.created_sequence].body, "not yet expired")
        self.assertNotIn(b"expired body", self.primary.read_bytes())
        self.assertNotIn(b"expired body", self.replica.read_bytes())

    def test_retention_prunes_oldest_voice_transition_and_keeps_open_interval(
        self,
    ) -> None:
        old = BASE - timedelta(days=31, seconds=17)
        current = BASE - timedelta(days=29)
        self.archive.begin_ingest_generation(
            source_id="discord_gateway",
            generation="boot-a",
            activated_at=old,
            now=BASE,
        )
        self.voice_state(sequence=1, at=old, owner_name="Alice")
        self.voice_state(sequence=2, at=current, owner_name="Alice")

        result = self.archive.prune_expired(now=BASE, batch_size=1)

        self.assertIsNotNone(result)
        transitions = self.archive.read_voice_state_transitions_admin_page(
            authorized=True
        ).transitions
        self.assertEqual([row.event_at for row in transitions], [current])
        intervals = self.archive.read_participation_admin(authorized=True)
        self.assertEqual(len(intervals), 2)
        self.assertTrue(all(row.ended_at is None for row in intervals))
        legal = self.archive.read_legal_minimal_events_page(
            authorized=True
        ).events
        self.assertEqual(legal, ())

    def test_legal_minimal_expires_at_occurrence_plus_thirty_days(self) -> None:
        owner_name = "Thirty Day Owner"
        occurred_at = BASE + timedelta(seconds=59)
        self.append_user(
            "user-retention",
            "deleted before retention",
            at=occurred_at,
            owner_name=owner_name,
        )
        preview = self.archive.preview_user_deletion(
            actor_external_id="user-retention",
            request_guild_id="guild-1",
            now=occurred_at + timedelta(seconds=1),
        )
        self.archive.apply_user_deletion(
            preview_id=preview.preview_id,
            actor_external_id="user-retention",
            now=occurred_at + timedelta(seconds=2),
        )
        self.assertEqual(
            [event.owner_name for event in self.archive.read_legal_minimal_events(authorized=True)],
            [owner_name],
        )
        self.assertEqual(
            self.archive.read_legal_minimal_events(authorized=True)[0].occurred_at,
            occurred_at,
        )

        before_cutoff = self.archive.prune_expired(
            now=occurred_at + timedelta(days=30) - timedelta(microseconds=1),
        )
        self.assertIsNone(before_cutoff)
        self.assertEqual(
            [event.owner_name for event in self.archive.read_legal_minimal_events(authorized=True)],
            [owner_name],
        )

        at_cutoff = self.archive.prune_expired(
            now=occurred_at + timedelta(days=30)
        )
        self.assertIsNotNone(at_cutoff)
        self.assertEqual(self.archive.read_legal_minimal_events(authorized=True), ())
        for path in (self.primary, self.replica):
            self.assertNotIn(owner_name.encode(), path.read_bytes())

    def test_legal_minimal_retention_is_oldest_first_and_invalidates_old_page(self) -> None:
        for actor, owner_name, occurred_at in (
            ("user-old", "Older Legal Owner", BASE),
            ("user-new", "Newer Legal Owner", BASE + timedelta(days=1)),
        ):
            self.append_user(
                actor,
                f"delete {actor}",
                at=occurred_at,
                owner_name=owner_name,
            )
            preview = self.archive.preview_user_deletion(
                actor_external_id=actor,
                request_guild_id="guild-1",
                now=occurred_at + timedelta(seconds=1),
            )
            self.archive.apply_user_deletion(
                preview_id=preview.preview_id,
                actor_external_id=actor,
                now=occurred_at + timedelta(seconds=2),
            )

        first_page = self.archive.read_legal_minimal_events_page(
            authorized=True,
            limit=1,
        )
        self.assertEqual([event.owner_name for event in first_page.events], ["Older Legal Owner"])
        assert first_page.next_cursor is not None
        second_page = self.archive.read_legal_minimal_events_page(
            authorized=True,
            cursor=first_page.next_cursor,
            limit=1,
        )
        self.assertEqual([event.owner_name for event in second_page.events], ["Newer Legal Owner"])

        result = self.archive.prune_expired(
            now=BASE + timedelta(days=40),
            batch_size=1,
        )
        self.assertIsNotNone(result)
        self.assertEqual(
            [event.owner_name for event in self.archive.read_legal_minimal_events(authorized=True)],
            ["Newer Legal Owner"],
        )
        with self.assertRaises(ArchiveStaleEvent):
            self.archive.read_legal_minimal_events_page(
                authorized=True,
                cursor=first_page.next_cursor,
                limit=1,
            )

    def test_legal_minimal_compaction_failure_retries_after_restart(self) -> None:
        owner_name = "Legal Compaction Retry Secret"
        self.append_user(
            "user-retry",
            "delete before legal retention",
            at=BASE,
            owner_name=owner_name,
        )
        preview = self.archive.preview_user_deletion(
            actor_external_id="user-retry",
            request_guild_id="guild-1",
            now=BASE + timedelta(seconds=1),
        )
        self.archive.apply_user_deletion(
            preview_id=preview.preview_id,
            actor_external_id="user-retry",
            now=BASE + timedelta(seconds=2),
        )

        with patch.object(
            self.archive,
            "_compact_primary",
            side_effect=OSError("simulated legal compaction failure"),
        ):
            pending = self.archive.prune_expired(now=BASE + timedelta(days=30))
        self.assertIsNotNone(pending)
        assert pending is not None
        self.assertEqual(pending.status, "local_cleanup_pending")
        self.assertEqual(self.archive.read_legal_minimal_events(authorized=True), ())
        self.clock.value = BASE + timedelta(days=30)
        unrelated = self.append_user(
            "user-unrelated-during-legal-retry",
            "unrelated write remains allowed",
            at=self.clock.value,
        )
        self.assertEqual(
            unrelated.owner_name,
            "user-unrelated-during-legal-retry-display",
        )

        self.archive.close()
        self.archive = ConversationArchive(
            primary_path=self.primary,
            replica_path=self.replica,
            anchor_path=self.anchor,
            integrity_key=b"archive-test-key-material-32-bytes-minimum",
            clock=self.clock,
            writer_lock_wait_seconds=0,
        ).open()
        self.assertEqual(
            self.archive.health(now=BASE + timedelta(days=30)).status,
            "local_cleanup_pending",
        )
        health = self.archive.reconcile_replica(
            now=BASE + timedelta(days=30, seconds=1)
        )
        self.assertEqual(health.status, "healthy")
        for path in (self.primary, self.replica):
            self.assertNotIn(owner_name.encode(), path.read_bytes())
        with closing(sqlite3.connect(self.primary)) as connection:
            audit = connection.execute(
                "SELECT display_text, status, primary_status, replica_status, purge_scope_json "
                "FROM deletion_audits WHERE reason = 'retention_expired'"
            ).fetchone()
        self.assertEqual(audit[1:4], ("local_fully_purged", "local_fully_purged", "deleted_verified"))
        self.assertNotIn(owner_name, audit[0])
        self.assertNotIn(owner_name, audit[4])

    def test_retention_cascade_does_not_project_newer_descendant_legal_row(self) -> None:
        old = self.append_user(
            "user-old-parent",
            "expired parent body",
            at=BASE,
            owner_name="Expired Parent Owner",
        )
        self.append_user(
            "user-new-descendant",
            "newer dependent body",
            at=BASE + timedelta(days=29),
            parent_ids=(old.record_id,),
            owner_name="Newer Descendant Owner",
        )

        result = self.archive.prune_expired(
            now=BASE + timedelta(days=31),
            batch_size=1,
        )

        self.assertIsNotNone(result)
        assert result is not None
        self.assertEqual(result.affected_records, 1)
        self.assertEqual(result.dependent_records, 1)
        self.assertEqual(self.archive.read_legal_minimal_events(authorized=True), ())
        for path in (self.primary, self.replica):
            raw = path.read_bytes()
            self.assertNotIn(b"Expired Parent Owner", raw)
            self.assertNotIn(b"Newer Descendant Owner", raw)

    def test_legal_only_retention_does_not_require_unrelated_external_sinks(self) -> None:
        owner_name = "Legal Only Sink Owner"
        self.append_user(
            "user-legal-only",
            "delete before legal only retention",
            at=BASE,
            owner_name=owner_name,
        )
        preview = self.archive.preview_user_deletion(
            actor_external_id="user-legal-only",
            request_guild_id="guild-1",
            now=BASE + timedelta(seconds=1),
        )
        self.archive.apply_user_deletion(
            preview_id=preview.preview_id,
            actor_external_id="user-legal-only",
            now=BASE + timedelta(seconds=2),
        )
        self.archive.close()
        self.archive = ConversationArchive(
            primary_path=self.primary,
            replica_path=self.replica,
            anchor_path=self.anchor,
            integrity_key=b"archive-test-key-material-32-bytes-minimum",
            clock=self.clock,
            writer_lock_wait_seconds=0,
            required_purge_sinks=("voice_debug_audio",),
        ).open()

        result = self.archive.prune_expired(now=BASE + timedelta(days=30))

        self.assertIsNotNone(result)
        assert result is not None
        self.assertEqual(result.status, "local_fully_purged")
        self.assertEqual(self.archive.pending_purge_work_orders(), ())
        with closing(sqlite3.connect(self.primary)) as connection:
            audit = connection.execute(
                "SELECT status, required_sinks_json FROM deletion_audits "
                "WHERE reason = 'retention_expired'"
            ).fetchone()
        self.assertEqual(audit, ("local_fully_purged", "[]"))

    def test_direct_deletion_does_not_create_already_expired_legal_minimum(self) -> None:
        owner_name = "Already Expired Legal Owner"
        self.append_user(
            "user-already-expired",
            "old direct deletion",
            at=BASE,
            owner_name=owner_name,
        )
        deletion_time = BASE + timedelta(days=31)
        preview = self.archive.preview_user_deletion(
            actor_external_id="user-already-expired",
            request_guild_id="guild-1",
            now=deletion_time,
        )

        result = self.archive.apply_user_deletion(
            preview_id=preview.preview_id,
            actor_external_id="user-already-expired",
            now=deletion_time + timedelta(seconds=1),
        )

        self.assertEqual(result.status, "local_fully_purged")
        self.assertEqual(self.archive.read_legal_minimal_events(authorized=True), ())
        for path in (self.primary, self.replica):
            self.assertNotIn(owner_name.encode(), path.read_bytes())

    def test_backup_grace_persists_and_reconcile_restores_health(self) -> None:
        with patch.object(
            self.archive,
            "_copy_primary_to_replica",
            side_effect=OSError("simulated backup outage"),
        ):
            self.append_user("user-a", "first during outage")
            pending = self.archive.health(now=BASE)
            self.assertEqual(pending.status, "backup_pending")
            first_pending_at = pending.backup_pending_since
            self.clock.value = BASE + timedelta(seconds=599)
            self.append_user("user-a", "still inside grace", at=self.clock.value)
            self.assertEqual(
                self.archive.health(now=self.clock.value).backup_pending_since,
                first_pending_at,
            )
            self.clock.value = BASE + timedelta(seconds=600)
            with self.assertRaises(ArchiveUnavailableError) as captured:
                self.append_user("user-a", "must be blocked", at=self.clock.value)
            self.assertEqual(captured.exception.code, "backup_grace_expired")

        self.clock.value = BASE + timedelta(seconds=601)
        health = self.archive.reconcile_replica(now=self.clock.value)
        self.assertEqual(health.status, "healthy")
        self.assertTrue(health.writes_allowed)
        with closing(sqlite3.connect(self.primary)) as primary_connection, closing(
            sqlite3.connect(self.replica)
        ) as replica_connection:
            primary_state = dict(primary_connection.execute("SELECT key, value FROM metadata"))
            replica_state = dict(replica_connection.execute("SELECT key, value FROM metadata"))
        self.assertEqual(primary_state["generation"], replica_state["generation"])
        self.assertEqual(primary_state["state_tag"], replica_state["state_tag"])

    def test_external_sink_receipts_are_required_before_full_purge(self) -> None:
        self.archive.close()
        self.archive = ConversationArchive(
            primary_path=self.primary,
            replica_path=self.replica,
            anchor_path=self.anchor,
            integrity_key=b"archive-test-key-material-32-bytes-minimum",
            clock=self.clock,
            writer_lock_wait_seconds=0,
            required_purge_sinks=("voice_debug_audio", "bot_memory"),
        ).open()
        self.append_user("user-a", "external sink deletion body")
        preview = self.archive.preview_user_deletion(
            actor_external_id="user-a", request_guild_id="guild-1", now=BASE
        )
        result = self.archive.apply_user_deletion(
            preview_id=preview.preview_id,
            actor_external_id="user-a",
            now=BASE + timedelta(seconds=1),
        )

        self.assertEqual(result.status, "local_cleanup_pending")
        self.assertEqual(result.primary_status, "local_fully_purged")
        self.assertEqual(result.replica_status, "deleted_verified")
        self.assertTrue(self.archive.health().writes_allowed)
        with closing(sqlite3.connect(self.primary)) as connection:
            request_id, deletion_generation = connection.execute(
                "SELECT request_id, deletion_generation FROM deletion_audits"
            ).fetchone()

        def receipt(sink: str, *, generation: int = deletion_generation):
            return {
                "sink": sink,
                "deletionGeneration": generation,
                "contentFree": True,
                "complete": True,
                "remainingCopies": 0,
                "manualReviewCount": 0,
            }

        with self.assertRaises(ArchiveValidationError) as incomplete:
            self.archive.submit_purge_receipts(
                request_id=request_id,
                receipts=(receipt("bot_memory"),),
                now=BASE + timedelta(seconds=2),
            )
        self.assertEqual(
            incomplete.exception.code, "archive_purge_receipts_incomplete"
        )
        with self.assertRaises(ArchiveValidationError) as stale:
            self.archive.submit_purge_receipts(
                request_id=request_id,
                receipts=(
                    receipt("bot_memory", generation=deletion_generation - 1),
                    receipt(
                        "voice_debug_audio",
                        generation=deletion_generation - 1,
                    ),
                ),
                now=BASE + timedelta(seconds=2),
            )
        self.assertEqual(stale.exception.code, "archive_purge_receipt_invalid")

        completed = self.archive.submit_purge_receipts(
            request_id=request_id,
            receipts=(receipt("bot_memory"), receipt("voice_debug_audio")),
            now=BASE + timedelta(seconds=3),
        )

        self.assertTrue(completed)
        self.assertTrue(self.archive.health().writes_allowed)
        with closing(sqlite3.connect(self.primary)) as connection:
            audit = connection.execute(
                "SELECT status, required_sinks_json, completed_sinks_json "
                "FROM deletion_audits WHERE request_id = ?",
                (request_id,),
            ).fetchone()
        self.assertEqual(audit, ("local_fully_purged", "[]", "[]"))

    def test_pending_purge_work_orders_supports_safe_keyset_pagination(
        self,
    ) -> None:
        self.archive.close()
        self.archive = ConversationArchive(
            primary_path=self.primary,
            replica_path=self.replica,
            anchor_path=self.anchor,
            integrity_key=b"archive-test-key-material-32-bytes-minimum",
            clock=self.clock,
            writer_lock_wait_seconds=0,
            required_purge_sinks=("bot_memory",),
        ).open()
        for index in range(3):
            actor = f"page-user-{index}"
            self.append_user(actor, f"page body {index}")
            preview = self.archive.preview_user_deletion(
                actor_external_id=actor,
                request_guild_id="guild-1",
                now=BASE,
            )
            self.archive.apply_user_deletion(
                preview_id=preview.preview_id,
                actor_external_id=actor,
                now=BASE + timedelta(seconds=1),
            )

        all_rows = self.archive.pending_purge_work_orders()
        first = self.archive.pending_purge_work_orders(limit=2)
        second = self.archive.pending_purge_work_orders(
            limit=2,
            after=(first[-1].requested_at, first[-1].request_id),
        )

        self.assertEqual(len(all_rows), 3)
        self.assertEqual(
            [row.request_id for row in (*first, *second)],
            [row.request_id for row in all_rows],
        )
        self.assertEqual(
            {row.requested_at for row in all_rows},
            {BASE + timedelta(seconds=1)},
        )
        for invalid in (
            (BASE,),
            (BASE.replace(tzinfo=None), first[0].request_id),
            (BASE, ""),
        ):
            with self.subTest(invalid=invalid):
                with self.assertRaises(ArchiveValidationError) as raised:
                    self.archive.pending_purge_work_orders(
                        after=invalid,  # type: ignore[arg-type]
                    )
                self.assertEqual(
                    raised.exception.code,
                    "archive_purge_cursor_invalid",
                )

    def test_explicit_lineage_key_is_bound_and_separate_from_integrity_key(
        self,
    ) -> None:
        root = Path(self.temporary.name) / "separate-lineage"
        integrity_key = b"archive-integrity-domain-key-32-bytes"
        lineage_key = b"archive-purge-routing-key-32-bytes-x"
        separate = ConversationArchive(
            primary_path=root / "primary.sqlite3",
            replica_path=root / "replica.sqlite3",
            anchor_path=root / "anchor.json",
            integrity_key=integrity_key,
            lineage_key=lineage_key,
            clock=self.clock,
            required_purge_sinks=("bot_memory",),
        ).open()
        try:
            separate.append_record(
                mode="discord_shared",
                surface="discord",
                record_type="user_text",
                body="lineage key separation",
                actor_external_id="lineage-user",
                owner_name="Lineage User",
                guild_id="guild-1",
                channel_id="text-1",
                started_at=BASE,
                ended_at=BASE,
                lineage={
                    "turn": ("turn-one",),
                    "session": ("session-one",),
                },
                idempotency_key="lineage-key-record",
                now=BASE,
            )
            preview = separate.preview_user_deletion(
                actor_external_id="lineage-user",
                request_guild_id="guild-1",
                now=BASE + timedelta(seconds=1),
            )
            separate.apply_user_deletion(
                preview_id=preview.preview_id,
                actor_external_id="lineage-user",
                now=BASE + timedelta(seconds=2),
            )
            work_order = separate.pending_purge_work_orders()[0]
            self.assertIn(
                (
                    "turn",
                    archive_lineage_handle(lineage_key, "turn", "turn-one"),
                ),
                work_order.lineage_handles,
            )
            self.assertNotIn(
                (
                    "turn",
                    archive_lineage_handle(
                        integrity_key,
                        "turn",
                        "turn-one",
                    ),
                ),
                work_order.lineage_handles,
            )
        finally:
            separate.close()

        with self.assertRaises(ArchiveIntegrityError) as mismatch:
            ConversationArchive(
                primary_path=root / "primary.sqlite3",
                replica_path=root / "replica.sqlite3",
                anchor_path=root / "anchor.json",
                integrity_key=integrity_key,
                lineage_key=b"wrong-purge-routing-key-32-bytes-xx",
                clock=self.clock,
            ).open()
        self.assertEqual(
            mismatch.exception.code,
            "archive_lineage_key_mismatch",
        )

    def test_committed_retry_is_readable_after_backup_grace_expires(self) -> None:
        arguments = {
            "mode": "discord_shared",
            "surface": "discord",
            "record_type": "user_text",
            "body": "retry survives degraded write closure",
            "actor_external_id": "user-a",
            "owner_name": "user-a-display",
            "guild_id": "guild-1",
            "channel_id": "text-1",
            "started_at": BASE,
            "ended_at": BASE,
            "idempotency_key": "degraded-retry",
        }
        with patch.object(
            self.archive,
            "_copy_primary_to_replica",
            side_effect=OSError("simulated backup outage"),
        ):
            first = self.archive.append_record(**arguments, now=BASE)
            self.clock.value = BASE + timedelta(seconds=600)
            retried = self.archive.append_record(**arguments, now=self.clock.value)
        self.assertEqual(retried, first)

    def test_backup_grace_deadline_survives_process_restart(self) -> None:
        with patch.object(
            self.archive,
            "_copy_primary_to_replica",
            side_effect=OSError("simulated backup outage"),
        ):
            self.append_user("user-a", "persist the degraded deadline")
        pending_since = self.archive.health(now=BASE).backup_pending_since
        self.archive.close()
        self.clock.value = BASE + timedelta(seconds=599)
        restarted = ConversationArchive(
            primary_path=self.primary,
            replica_path=self.replica,
            anchor_path=self.anchor,
            integrity_key=b"archive-test-key-material-32-bytes-minimum",
            clock=self.clock,
            writer_lock_wait_seconds=0,
        )
        with patch.object(
            ConversationArchive,
            "_copy_primary_to_replica",
            side_effect=OSError("backup still unavailable"),
        ):
            restarted.open()
            self.assertEqual(
                restarted.health(now=self.clock.value).backup_pending_since,
                pending_since,
            )
            self.clock.value = BASE + timedelta(seconds=600)
            with self.assertRaises(ArchiveUnavailableError) as captured:
                restarted.append_record(
                    mode="discord_shared",
                    surface="discord",
                    record_type="user_text",
                    body="blocked after restarted deadline",
                    actor_external_id="user-a",
                    owner_name="user-a-display",
                    guild_id="guild-1",
                    channel_id="text-1",
                    started_at=self.clock.value,
                    ended_at=self.clock.value,
                    idempotency_key="post-restart-block",
                    now=self.clock.value,
                )
            self.assertEqual(captured.exception.code, "backup_grace_expired")
        self.archive = restarted

    def test_corrupt_replica_is_quarantined_without_blocking_verified_read(self) -> None:
        record = self.append_user("user-a", "primary remains independently readable")
        with closing(sqlite3.connect(self.replica)) as connection:
            connection.execute("UPDATE records SET body = 'replica tamper'")
            connection.commit()

        health = self.archive.reconcile_replica(now=BASE)

        self.assertEqual(health.status, "backup_integrity_blocked")
        visible = self.archive.read_self(
            actor_external_id="user-a", guild_id="guild-1"
        )
        self.assertEqual([row.record_id for row in visible], [record.record_id])
        with self.assertRaises(ArchiveUnavailableError) as captured:
            self.append_user("user-b", "write is blocked by replica integrity")
        self.assertEqual(captured.exception.code, "backup_integrity_blocked")

    def test_compaction_failure_stays_pending_until_verified_reconcile(self) -> None:
        secret = "compaction-retry-secret"
        self.append_user("user-a", secret)
        preview = self.archive.preview_user_deletion(
            actor_external_id="user-a", request_guild_id="guild-1", now=BASE
        )
        with patch.object(
            self.archive,
            "_compact_primary",
            side_effect=OSError("simulated compact failure"),
        ):
            result = self.archive.apply_user_deletion(
                preview_id=preview.preview_id,
                actor_external_id="user-a",
                now=BASE + timedelta(seconds=1),
            )
        self.assertEqual(result.status, "local_cleanup_pending")
        self.assertEqual(result.primary_status, "local_cleanup_pending")
        self.assertEqual(
            self.archive.health(now=BASE + timedelta(seconds=1)).status,
            "local_cleanup_pending",
        )
        with self.assertRaises(ArchiveUnavailableError) as target_blocked:
            self.append_user("user-a", "blocked target late commit")
        self.assertEqual(
            target_blocked.exception.code,
            "archive_target_cleanup_pending",
        )
        unrelated = self.append_user(
            "user-b", "unrelated write while cleanup is pending"
        )
        self.assertEqual(unrelated.owner_name, "user-b-display")

        health = self.archive.reconcile_replica(now=BASE + timedelta(seconds=2))
        self.assertEqual(health.status, "healthy")
        for path in (self.primary, self.replica):
            self.assertNotIn(secret.encode(), path.read_bytes())
        with closing(sqlite3.connect(self.primary)) as connection:
            self.assertEqual(
                connection.execute(
                    "SELECT status, primary_status, replica_status FROM deletion_audits"
                ).fetchone(),
                (
                    "local_fully_purged",
                    "local_fully_purged",
                    "deleted_verified",
                ),
            )

    def test_second_writer_is_rejected_with_stable_fault(self) -> None:
        contender = ConversationArchive(
            primary_path=self.primary,
            replica_path=self.replica,
            anchor_path=self.anchor,
            integrity_key=b"archive-test-key-material-32-bytes-minimum",
            clock=self.clock,
            writer_lock_wait_seconds=0,
        )
        with self.assertRaises(ArchiveUnavailableError) as captured:
            contender.open()
        self.assertEqual(captured.exception.code, "writer_lease_lost")
        contender.close()

    def test_anchor_is_mandatory_unless_test_mode_is_explicit(self) -> None:
        root = Path(self.temporary.name) / "unanchored"
        with self.assertRaises(ArchiveIntegrityError) as captured:
            ConversationArchive(
                primary_path=root / "primary.sqlite3",
                replica_path=root / "replica.sqlite3",
                anchor_path=None,
                integrity_key=b"archive-test-key-material-32-bytes-minimum",
            )
        self.assertEqual(captured.exception.code, "archive_anchor_required")
        test_only = ConversationArchive(
            primary_path=root / "test-primary.sqlite3",
            replica_path=root / "test-replica.sqlite3",
            anchor_path=None,
            integrity_key=b"archive-test-key-material-32-bytes-minimum",
            allow_unanchored_test_mode=True,
        ).open()
        test_only.close()

    def test_valid_stale_anchor_replay_blocks_read_and_write(self) -> None:
        stale_anchor = self.anchor.read_bytes()
        self.append_user("user-a", "newer than stale anchor")
        self.anchor.write_bytes(stale_anchor)

        with self.assertRaises(ArchiveIntegrityError) as read_failure:
            self.archive.read_self(actor_external_id="user-a", guild_id="guild-1")
        self.assertEqual(read_failure.exception.code, "anchor_stale_commit_unknown")
        with self.assertRaises(ArchiveIntegrityError):
            self.append_user("user-b", "must not write past stale anchor")

    def test_deletion_advances_content_free_cutover_anchor(self) -> None:
        secret = "anchor-must-not-retain-this-secret"
        self.append_user("user-a", secret)
        before = json.loads(self.anchor.read_text(encoding="utf-8"))
        preview = self.archive.preview_user_deletion(
            actor_external_id="user-a", request_guild_id="guild-1", now=BASE
        )
        self.archive.apply_user_deletion(
            preview_id=preview.preview_id,
            actor_external_id="user-a",
            now=BASE + timedelta(seconds=1),
        )
        after_raw = self.anchor.read_text(encoding="utf-8")
        after = json.loads(after_raw)
        witness = after["cutoverWitness"]
        self.assertGreater(after["generation"], before["generation"])
        self.assertGreater(
            after["minimumRestorableGeneration"], before["generation"]
        )
        self.assertEqual(
            witness["generation"], after["minimumRestorableGeneration"]
        )
        self.assertTrue(witness["contentFree"])
        self.assertTrue(witness["nonce"])
        self.assertNotIn(secret, after_raw)
        with closing(sqlite3.connect(self.primary)) as connection:
            metadata = dict(connection.execute("SELECT key, value FROM metadata"))
        self.assertEqual(int(metadata["generation"]), after["generation"])
        self.assertEqual(metadata["chain_head"], after["chainHead"])
        self.assertEqual(metadata["state_tag"], after["stateTag"])
        self.assertFalse(
            any(
                path.name.startswith(f".{self.anchor.name}.staging-")
                for path in self.anchor.parent.iterdir()
            )
        )

    def test_admin_principal_delete_uses_separate_preview_domain(self) -> None:
        owned = self.append_user("user-a", "admin target")
        self.append_reply(owned.record_id, "dependent admin target")
        survivor = self.append_user("user-b", "admin-independent survivor")
        principal_id = next(
            row.owner_principal_id
            for row in self.archive.read_admin(authorized=True)
            if row.record_id == owned.record_id
        )
        assert principal_id is not None
        with self.assertRaises(ArchiveAuthorizationError):
            self.archive.preview_admin_deletion(
                authorized=False, target_principal_id=principal_id
            )
        preview = self.archive.preview_admin_deletion(
            authorized=True,
            target_principal_id=principal_id,
            now=BASE,
        )
        with self.assertRaises(ArchiveAuthorizationError) as confused:
            self.archive.apply_user_deletion(
                preview_id=preview.preview_id,
                actor_external_id="user-a",
                now=BASE + timedelta(seconds=1),
            )
        self.assertEqual(confused.exception.code, "archive_preview_domain_mismatch")
        result = self.archive.apply_admin_deletion(
            authorized=True,
            preview_id=preview.preview_id,
            now=BASE + timedelta(seconds=1),
        )
        self.assertEqual(result.display_text, ARCHIVE_ADMIN_DELETION_AUDIT_TEXT)
        self.assertEqual(result.status, "local_fully_purged")
        admin = self.archive.read_admin(authorized=True, include_quarantined=True)
        self.assertIn(ARCHIVE_ADMIN_TOMBSTONE_TEXT, {row.body for row in admin})
        self.assertIn(survivor.record_id, {row.record_id for row in admin})
        self.assertEqual(
            self.archive.read_self(actor_external_id="user-a", guild_id="guild-1"),
            (),
        )

    def test_admin_exact_record_delete_does_not_expand_to_same_owner(self) -> None:
        first = self.append_user("user-a", "first exact admin target")
        second = self.append_user(
            "user-a", "same owner survives", guild="guild-2"
        )
        preview = self.archive.preview_admin_deletion(
            authorized=True,
            record_ids=(first.record_id,),
            now=BASE,
        )
        self.assertEqual(preview.owned_record_count, 1)
        self.archive.apply_admin_deletion(
            authorized=True,
            preview_id=preview.preview_id,
            now=BASE + timedelta(seconds=1),
        )
        visible = self.archive.read_self(
            actor_external_id="user-a", guild_id="guild-2"
        )
        self.assertEqual([row.record_id for row in visible], [second.record_id])

    def test_tamper_is_detected_before_body_exposure(self) -> None:
        self.append_user("user-a", "authentic body")
        with closing(sqlite3.connect(self.primary)) as connection:
            connection.execute(
                "UPDATE records SET body = 'tampered' WHERE status = 'active'"
            )
            connection.commit()
        with self.assertRaises(ArchiveIntegrityError):
            self.archive.read_self(actor_external_id="user-a", guild_id="guild-1")

    def test_pre_delete_restore_candidate_is_rejected(self) -> None:
        self.append_user("user-a", "restore replay secret")
        old_snapshot = self.primary.parent.parent / "old.sqlite3"
        with closing(sqlite3.connect(self.primary)) as source, closing(
            sqlite3.connect(old_snapshot)
        ) as target:
            source.backup(target)
        preview = self.archive.preview_user_deletion(
            actor_external_id="user-a", request_guild_id="guild-1", now=BASE
        )
        self.archive.apply_user_deletion(
            preview_id=preview.preview_id,
            actor_external_id="user-a",
            now=BASE + timedelta(seconds=1),
        )

        with self.assertRaises(ArchiveIntegrityError) as captured:
            self.archive.assert_restore_candidate(old_snapshot)
        self.assertEqual(captured.exception.code, "archive_restore_generation_rejected")

    def test_restore_from_exact_replica_recovers_corrupt_primary(self) -> None:
        self.append_user("user-a", "replica recovery body")
        expected_generation = self.archive.generation
        with closing(sqlite3.connect(self.primary)) as connection:
            connection.execute(
                "UPDATE records SET body = 'corrupt primary body' WHERE status = 'active'"
            )
            connection.commit()

        with self.assertRaises(ArchiveIntegrityError):
            self.archive.read_self(
                actor_external_id="user-a", guild_id="guild-1"
            )
        restored_generation, _ = self.archive.restore_from_replica()

        self.assertEqual(restored_generation, expected_generation)
        visible = self.archive.read_self(
            actor_external_id="user-a", guild_id="guild-1"
        )
        self.assertEqual([row.body for row in visible], ["replica recovery body"])

    def test_restore_rejects_pre_delete_replica_without_touching_primary(self) -> None:
        self.append_user("user-a", "must not reappear")
        old_replica = self.primary.parent.parent / "pre-delete-replica.sqlite3"
        with closing(sqlite3.connect(self.replica)) as source, closing(
            sqlite3.connect(old_replica)
        ) as target:
            source.backup(target)
        preview = self.archive.preview_user_deletion(
            actor_external_id="user-a", request_guild_id="guild-1", now=BASE
        )
        self.archive.apply_user_deletion(
            preview_id=preview.preview_id,
            actor_external_id="user-a",
            now=BASE + timedelta(seconds=1),
        )
        current_primary = self.primary.read_bytes()
        self.replica.unlink()
        old_replica.replace(self.replica)

        with self.assertRaises(ArchiveIntegrityError) as captured:
            self.archive.restore_from_replica()
        self.assertEqual(
            captured.exception.code, "archive_restore_generation_rejected"
        )
        self.assertEqual(self.primary.read_bytes(), current_primary)
        self.assertNotIn(b"must not reappear", current_primary)

    def test_restore_rejects_future_divergent_and_foreign_replicas(self) -> None:
        self.append_user("user-a", "canonical restore state")
        saved = self.primary.parent.parent / "saved-replica.sqlite3"
        with closing(sqlite3.connect(self.replica)) as source, closing(
            sqlite3.connect(saved)
        ) as target:
            source.backup(target)

        def install_saved() -> None:
            self.replica.unlink(missing_ok=True)
            with closing(sqlite3.connect(saved)) as source, closing(
                sqlite3.connect(self.replica)
            ) as target:
                source.backup(target)

        install_saved()
        with closing(sqlite3.connect(self.replica)) as connection:
            connection.row_factory = sqlite3.Row
            generation = self.archive._metadata_int(connection, "generation") + 1
            self.archive._set_metadata(connection, "generation", generation)
            self.archive._set_metadata(
                connection,
                "chain_head",
                self.archive._expected_chain_head(connection),
            )
            self.archive._set_metadata(
                connection,
                "state_tag",
                self.archive._expected_state_tag(connection),
            )
            connection.commit()
        with self.assertRaises(ArchiveIntegrityError) as future:
            self.archive.restore_from_replica()
        self.assertEqual(future.exception.code, "archive_restore_generation_rejected")

        install_saved()
        with closing(sqlite3.connect(self.replica)) as connection:
            connection.row_factory = sqlite3.Row
            connection.execute(
                "UPDATE records SET body = 'validly signed divergent body'"
            )
            self.archive._set_metadata(
                connection,
                "state_tag",
                self.archive._expected_state_tag(connection),
            )
            connection.commit()
        with self.assertRaises(ArchiveIntegrityError) as divergent:
            self.archive.restore_from_replica()
        self.assertEqual(divergent.exception.code, "archive_restore_state_rejected")

        foreign_root = self.primary.parent.parent / "foreign"
        foreign = ConversationArchive(
            primary_path=foreign_root / "primary.sqlite3",
            replica_path=foreign_root / "replica.sqlite3",
            anchor_path=foreign_root / "anchor.json",
            integrity_key=b"foreign-archive-key-material-32-bytes-minimum",
            clock=self.clock,
        ).open()
        try:
            foreign.append_record(
                mode="local_private",
                surface="local",
                record_type="user_text",
                body="foreign body",
                actor_external_id="local-owner",
                owner_name="Local Owner",
                started_at=BASE,
                ended_at=BASE,
                idempotency_key="foreign-1",
                now=BASE,
            )
        finally:
            foreign.close()
        self.replica.unlink(missing_ok=True)
        with closing(sqlite3.connect(foreign_root / "replica.sqlite3")) as source, closing(
            sqlite3.connect(self.replica)
        ) as target:
            source.backup(target)
        with self.assertRaises(ArchiveIntegrityError) as foreign_error:
            self.archive.restore_from_replica()
        self.assertEqual(
            foreign_error.exception.code, "archive_integrity_key_mismatch"
        )

    def test_same_generation_divergent_restore_candidate_is_rejected(self) -> None:
        self.append_user("user-a", "current canonical state")
        candidate = self.primary.parent.parent / "divergent.sqlite3"
        with closing(sqlite3.connect(self.primary)) as source, closing(
            sqlite3.connect(candidate)
        ) as target:
            source.backup(target)
        with closing(sqlite3.connect(candidate)) as connection:
            connection.row_factory = sqlite3.Row
            connection.execute(
                "UPDATE records SET body = 'different but validly signed state'"
            )
            state_tag = self.archive._expected_state_tag(connection)
            connection.execute(
                "UPDATE metadata SET value = ? WHERE key = 'state_tag'",
                (state_tag,),
            )
            connection.commit()

        with self.assertRaises(ArchiveIntegrityError) as captured:
            self.archive.assert_restore_candidate(candidate)
        self.assertEqual(captured.exception.code, "archive_restore_state_rejected")

        forged_cutover = self.primary.parent.parent / "forged-cutover.sqlite3"
        with closing(sqlite3.connect(self.primary)) as source, closing(
            sqlite3.connect(forged_cutover)
        ) as target:
            source.backup(target)
        with closing(sqlite3.connect(forged_cutover)) as connection:
            connection.row_factory = sqlite3.Row
            connection.execute(
                "UPDATE metadata SET value = 'forged' WHERE key = 'cutover_nonce'"
            )
            state_tag = self.archive._expected_state_tag(connection)
            connection.execute(
                "UPDATE metadata SET value = ? WHERE key = 'state_tag'",
                (state_tag,),
            )
            connection.commit()
        with self.assertRaises(ArchiveIntegrityError) as cutover:
            self.archive.assert_restore_candidate(forged_cutover)
        self.assertEqual(cutover.exception.code, "archive_restore_cutover_rejected")

    def test_v1_archive_and_replica_migrate_after_legacy_integrity_check(
        self,
    ) -> None:
        root = self.primary.parent.parent / "legacy"
        primary = root / "primary.sqlite3"
        replica = root / "replica.sqlite3"
        anchor = root / "anchor.json"
        key = b"legacy-archive-key-material-32-bytes-minimum"
        legacy = ConversationArchive(
            primary_path=primary,
            replica_path=replica,
            anchor_path=anchor,
            integrity_key=key,
            clock=self.clock,
        ).open()
        legacy.append_record(
            mode="local_private",
            surface="local",
            record_type="user_text",
            body="preserved across migration",
            actor_external_id="local-owner",
            owner_name="Local Owner",
            started_at=BASE,
            ended_at=BASE,
            idempotency_key="legacy-record",
            now=BASE,
        )
        legacy.close()
        for path in (primary, replica):
            with closing(sqlite3.connect(path)) as connection:
                connection.row_factory = sqlite3.Row
                connection.execute("DROP INDEX voice_transitions_owner_time_idx")
                connection.execute("DROP INDEX voice_transitions_expiry_idx")
                connection.execute("DROP TABLE voice_state_transitions")
                connection.execute("PRAGMA user_version=1")
                connection.execute(
                    "UPDATE metadata SET value = '1' WHERE key = 'schema_version'"
                )
                state_tag = legacy._hmac(
                    archive_module._STATE_DOMAIN,
                    legacy._state_payload_for_columns(
                        connection,
                        archive_module._LEGACY_STATE_TABLE_COLUMNS,
                    ),
                )
                connection.execute(
                    "UPDATE metadata SET value = ? WHERE key = 'state_tag'",
                    (state_tag,),
                )
                if path == primary:
                    unsigned = legacy._anchor_unsigned_from_connection(connection)
                    anchor_payload = {
                        **unsigned,
                        "authTag": legacy._hmac(
                            archive_module._ANCHOR_DOMAIN,
                            unsigned,
                        ),
                    }
                connection.commit()
            if path == primary:
                anchor.write_bytes(
                    archive_module._canonical_json(anchor_payload) + b"\n"
                )

        migrated = ConversationArchive(
            primary_path=primary,
            replica_path=replica,
            anchor_path=anchor,
            integrity_key=key,
            clock=self.clock,
        ).open()
        try:
            self.assertEqual(
                migrated.read_admin(authorized=True)[0].body,
                "preserved across migration",
            )
            for path in (primary, replica):
                with closing(sqlite3.connect(path)) as connection:
                    self.assertEqual(
                        connection.execute("PRAGMA user_version").fetchone()[0],
                        archive_module.ARCHIVE_SCHEMA_VERSION,
                    )
                    self.assertIsNotNone(
                        connection.execute(
                            "SELECT 1 FROM sqlite_master "
                            "WHERE type = 'table' AND name = 'voice_state_transitions'"
                        ).fetchone()
                    )
        finally:
            migrated.close()


if __name__ == "__main__":
    unittest.main()
