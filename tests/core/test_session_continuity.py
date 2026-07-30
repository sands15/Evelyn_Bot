from __future__ import annotations

import asyncio
import json
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch


REPO_ROOT = next(
    path for path in Path(__file__).resolve().parents
    if (path / "main.py").exists()
)
RUNTIME_ROOT = REPO_ROOT / "evelyn_core" / "runtime"
if str(RUNTIME_ROOT) not in sys.path:
    sys.path.insert(0, str(RUNTIME_ROOT))

from evelyn_core.session_continuity import (  # noqa: E402
    SESSION_CONTINUITY_CHAIN_GENESIS,
    SESSION_CONTINUITY_CHECKPOINT_SCHEMA,
    SESSION_CONTINUITY_HEAD_SCHEMA,
    SESSION_CONTINUITY_LEGACY_CHECKPOINT_SCHEMA,
    SessionContinuityCheckpoint,
    _checkpoint_hash,
)
from evelyn_core.runtime_artifact_io import atomic_json_write  # noqa: E402
from evelyn_core.session_memory_state import SessionStateStore  # noqa: E402


class FakeClock:
    def __init__(self, wall: float, monotonic: float) -> None:
        self.wall = wall
        self.monotonic = monotonic

    def wall_time(self) -> float:
        return self.wall

    def monotonic_time(self) -> float:
        return self.monotonic


class SessionContinuityTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp_dir.cleanup)
        self.root = Path(self.temp_dir.name)
        self.checkpoint_path = self.root / "active.json"
        self.status_path = self.root / "status.json"

    def manager(
        self,
        store: SessionStateStore,
        clock: FakeClock,
        *,
        system_prompt: str = "current system prompt",
        max_age_sec: float = 900.0,
    ) -> SessionContinuityCheckpoint:
        return SessionContinuityCheckpoint(
            store=store,
            checkpoint_path=self.checkpoint_path,
            status_path=self.status_path,
            system_prompt=system_prompt,
            max_age_sec=max_age_sec,
            wall_time=clock.wall_time,
            monotonic=clock.monotonic_time,
        )

    def populated_store(self) -> SessionStateStore:
        store = SessionStateStore.create_empty()
        store.append_history(
            "guild:1:text:2:user:3",
            "내가 하던 이야기를 기억해",
            "응, 재시작 뒤에도 이어갈게.",
            system_prompt="private system prompt",
            max_history_items=12,
        )
        store.update_session_state(
            "guild:1:text:2:user:3",
            user_id=3,
            speaker="assistant",
            awaiting_user_reply=True,
            topic_id="topic-1",
            active_conversation_awaiting_reply_sec=300.0,
            now_monotonic=100.0,
        )
        store.remember_followup_target(
            "guild:1:text:2:user:3",
            channel_id=2,
            message_id=4,
        )
        store.partial_stt_text["guild:1:text:2:user:3"] = (
            "persist하면 안 되는 부분 transcript"
        )
        return store

    def add_other_guild(self, store: SessionStateStore) -> str:
        session_key = "guild:2:text:5:user:6"
        store.append_history(
            session_key,
            "다른 길드의 대화",
            "이 관계는 유지해야 해.",
            system_prompt="private system prompt",
            max_history_items=12,
        )
        store.update_session_state(
            session_key,
            user_id=6,
            speaker="assistant",
            awaiting_user_reply=True,
            topic_id="topic-2",
            active_conversation_awaiting_reply_sec=300.0,
            now_monotonic=100.0,
        )
        return session_key

    def test_completed_turn_and_active_followup_survive_fresh_restart(self) -> None:
        source_clock = FakeClock(wall=1000.0, monotonic=100.0)
        source = self.populated_store()
        first = self.manager(
            source,
            source_clock,
            system_prompt="private system prompt",
        )

        flushed = first.flush()

        self.assertEqual(flushed["state"], "ready")
        payload = json.loads(self.checkpoint_path.read_text(encoding="utf-8"))
        head = json.loads(
            (self.root / "checkpoint_head.json").read_text(
                encoding="utf-8"
            )
        )
        self.assertEqual(
            payload["schema"],
            SESSION_CONTINUITY_CHECKPOINT_SCHEMA,
        )
        self.assertEqual(payload["generation"], 1)
        self.assertEqual(
            payload["previousHash"],
            SESSION_CONTINUITY_CHAIN_GENESIS,
        )
        self.assertEqual(head["schema"], SESSION_CONTINUITY_HEAD_SCHEMA)
        self.assertEqual(head["state"], "active")
        self.assertEqual(head["generation"], 1)
        self.assertEqual(
            head["checkpointHash"],
            payload["checkpointHash"],
        )
        self.assertTrue(head["contentFree"])
        self.assertNotIn(
            "내가 하던 이야기를 기억해",
            json.dumps(head, ensure_ascii=False),
        )
        serialized = json.dumps(payload, ensure_ascii=False)
        self.assertNotIn("private system prompt", serialized)
        self.assertNotIn("persist하면 안 되는 부분", serialized)
        self.assertFalse(payload["policy"]["rawAudio"])
        self.assertFalse(payload["policy"]["partialTranscript"])

        restored_store = SessionStateStore.create_empty()
        restored_clock = FakeClock(wall=1006.0, monotonic=500.0)
        restored = self.manager(restored_store, restored_clock).restore()

        session_key = "guild:1:text:2:user:3"
        self.assertEqual(restored["state"], "restored")
        self.assertEqual(restored["restoredSessionCount"], 1)
        self.assertEqual(
            restored_store.histories[session_key],
            [
                {"role": "system", "content": "current system prompt"},
                {"role": "user", "content": "내가 하던 이야기를 기억해"},
                {
                    "role": "assistant",
                    "content": "응, 재시작 뒤에도 이어갈게.",
                },
            ],
        )
        self.assertTrue(restored_store.awaiting_user_reply[session_key])
        self.assertEqual(restored_store.active_user_ids[session_key], 3)
        self.assertAlmostEqual(
            restored_store.active_until[session_key],
            794.0,
        )
        self.assertEqual(
            restored_store.followup_targets[session_key],
            {"channel_id": 2, "message_id": 4},
        )
        self.assertNotIn(session_key, restored_store.partial_stt_text)
        self.assertEqual(
            restored["checkpointIntegrity"],
            "verified",
        )
        self.assertEqual(
            restored["checkpointHeadState"],
            "current",
        )
        self.assertTrue(restored["rollbackProtected"])

    def test_valid_json_content_tamper_is_rejected(
        self,
    ) -> None:
        clock = FakeClock(wall=1000.0, monotonic=100.0)
        self.manager(self.populated_store(), clock).flush()
        payload = json.loads(
            self.checkpoint_path.read_text(encoding="utf-8")
        )
        payload["sessions"][0]["history"][0]["content"] = (
            "변조된 대화문"
        )
        payload["checkpointHash"] = _checkpoint_hash(payload)
        self.checkpoint_path.write_text(
            json.dumps(payload, ensure_ascii=False),
            encoding="utf-8",
        )
        restored_store = SessionStateStore.create_empty()

        status = self.manager(
            restored_store,
            FakeClock(wall=1001.0, monotonic=500.0),
        ).restore()
        head = json.loads(
            (self.root / "checkpoint_head.json").read_text(
                encoding="utf-8"
            )
        )

        self.assertEqual(status["state"], "error")
        self.assertEqual(
            status["lastErrorCode"],
            "conversation_continuity_checkpoint_rejected",
        )
        self.assertEqual(status["checkpointIntegrity"], "failed")
        self.assertEqual(restored_store.histories, {})
        self.assertFalse(self.checkpoint_path.exists())
        self.assertEqual(head["state"], "empty")

    def test_rollback_to_older_generation_is_rejected(
        self,
    ) -> None:
        clock = FakeClock(wall=1000.0, monotonic=100.0)
        store = self.populated_store()
        manager = self.manager(store, clock)
        manager.flush()
        first_raw = self.checkpoint_path.read_text(
            encoding="utf-8"
        )
        store.histories["guild:1:text:2:user:3"].extend(
            [
                {"role": "user", "content": "두 번째 사용자 턴"},
                {"role": "assistant", "content": "두 번째 답변"},
            ]
        )
        clock.wall = 1001.0
        manager.flush()
        current_head = json.loads(
            (self.root / "checkpoint_head.json").read_text(
                encoding="utf-8"
            )
        )
        self.assertEqual(current_head["generation"], 2)
        self.checkpoint_path.write_text(
            first_raw,
            encoding="utf-8",
        )
        restored_store = SessionStateStore.create_empty()

        status = self.manager(
            restored_store,
            FakeClock(wall=1002.0, monotonic=500.0),
        ).restore()

        self.assertEqual(status["state"], "error")
        self.assertEqual(
            status["lastErrorCode"],
            "conversation_continuity_checkpoint_rejected",
        )
        self.assertEqual(restored_store.histories, {})
        self.assertFalse(self.checkpoint_path.exists())

    def test_checkpoint_deletion_behind_active_head_is_rejected(
        self,
    ) -> None:
        clock = FakeClock(wall=1000.0, monotonic=100.0)
        self.manager(self.populated_store(), clock).flush()
        self.checkpoint_path.unlink()
        restored_store = SessionStateStore.create_empty()

        status = self.manager(
            restored_store,
            FakeClock(wall=1001.0, monotonic=500.0),
        ).restore()
        head = json.loads(
            (self.root / "checkpoint_head.json").read_text(
                encoding="utf-8"
            )
        )

        self.assertEqual(status["state"], "error")
        self.assertEqual(
            status["lastErrorCode"],
            "conversation_continuity_checkpoint_rejected",
        )
        self.assertEqual(restored_store.histories, {})
        self.assertEqual(head["state"], "empty")

    def test_lagging_head_after_commit_crash_is_recovered(
        self,
    ) -> None:
        clock = FakeClock(wall=1000.0, monotonic=100.0)
        store = self.populated_store()
        manager = self.manager(store, clock)
        manager.flush()
        store.histories["guild:1:text:2:user:3"].extend(
            [
                {"role": "user", "content": "head crash 직전"},
                {"role": "assistant", "content": "복구할게"},
            ]
        )
        clock.wall = 1001.0
        original_write = atomic_json_write
        failed = False

        def fail_first_head(
            path: Path,
            payload: dict,
            **kwargs,
        ) -> None:
            nonlocal failed
            if Path(path) == manager.head_path and not failed:
                failed = True
                raise OSError("simulated head commit crash")
            original_write(path, payload, **kwargs)

        with patch(
            "evelyn_core.session_continuity.atomic_json_write",
            side_effect=fail_first_head,
        ):
            flushed = manager.flush()

        self.assertEqual(flushed["state"], "error")
        self.assertEqual(
            flushed["checkpointHeadState"],
            "lagging",
        )
        self.assertTrue(self.checkpoint_path.exists())
        lagging_head = json.loads(
            manager.head_path.read_text(encoding="utf-8")
        )
        self.assertEqual(lagging_head["generation"], 1)

        restored_store = SessionStateStore.create_empty()
        restored = self.manager(
            restored_store,
            FakeClock(wall=1002.0, monotonic=500.0),
        ).restore()
        head = json.loads(
            manager.head_path.read_text(encoding="utf-8")
        )

        self.assertEqual(restored["state"], "restored")
        self.assertEqual(
            restored["checkpointHeadState"],
            "current",
        )
        self.assertEqual(head["state"], "active")
        self.assertEqual(head["generation"], 2)
        self.assertIn(
            "guild:1:text:2:user:3",
            restored_store.histories,
        )

    def test_legacy_checkpoint_is_anchored_then_chained(
        self,
    ) -> None:
        source_clock = FakeClock(
            wall=1000.0,
            monotonic=100.0,
        )
        self.manager(
            self.populated_store(),
            source_clock,
        ).flush()
        legacy = json.loads(
            self.checkpoint_path.read_text(encoding="utf-8")
        )
        legacy["schema"] = (
            SESSION_CONTINUITY_LEGACY_CHECKPOINT_SCHEMA
        )
        legacy.pop("generation")
        legacy.pop("previousHash")
        legacy.pop("checkpointHash")
        self.checkpoint_path.write_text(
            json.dumps(
                legacy,
                ensure_ascii=False,
                indent=2,
                sort_keys=True,
            ),
            encoding="utf-8",
        )
        (self.root / "checkpoint_head.json").unlink()
        restored_store = SessionStateStore.create_empty()
        restored_clock = FakeClock(
            wall=1001.0,
            monotonic=500.0,
        )
        manager = self.manager(
            restored_store,
            restored_clock,
        )

        restored = manager.restore()
        legacy_head = json.loads(
            manager.head_path.read_text(encoding="utf-8")
        )

        self.assertEqual(restored["state"], "restored")
        self.assertEqual(
            restored["checkpointIntegrity"],
            "legacy_anchored",
        )
        self.assertEqual(legacy_head["generation"], 0)
        self.assertTrue(restored["rollbackProtected"])

        restored_store.histories[
            "guild:1:text:2:user:3"
        ].extend(
            [
                {"role": "user", "content": "마이그레이션 뒤 턴"},
                {"role": "assistant", "content": "이어갈게"},
            ]
        )
        restored_clock.wall = 1002.0
        migrated = manager.flush()
        current = json.loads(
            self.checkpoint_path.read_text(encoding="utf-8")
        )

        self.assertEqual(migrated["state"], "ready")
        self.assertEqual(
            current["schema"],
            SESSION_CONTINUITY_CHECKPOINT_SCHEMA,
        )
        self.assertEqual(current["generation"], 1)
        self.assertEqual(
            current["previousHash"],
            legacy_head["checkpointHash"],
        )

    def test_stale_checkpoint_does_not_restore_relationship_state(self) -> None:
        source_clock = FakeClock(wall=1000.0, monotonic=100.0)
        self.manager(
            self.populated_store(),
            source_clock,
            max_age_sec=60.0,
        ).flush()

        restored_store = SessionStateStore.create_empty()
        restored = self.manager(
            restored_store,
            FakeClock(wall=1061.0, monotonic=500.0),
            max_age_sec=60.0,
        ).restore()

        self.assertEqual(restored["state"], "stale")
        self.assertEqual(restored_store.histories, {})
        self.assertEqual(restored_store.awaiting_user_reply, {})
        self.assertFalse(self.checkpoint_path.exists())

    def test_corrupt_checkpoint_is_rejected_without_raw_error_text(self) -> None:
        self.checkpoint_path.write_text(
            "{C:\\private\\conversation",
            encoding="utf-8",
        )
        manager = self.manager(
            SessionStateStore.create_empty(),
            FakeClock(wall=1000.0, monotonic=100.0),
        )

        status = manager.restore()

        self.assertEqual(status["state"], "error")
        self.assertEqual(
            status["lastErrorCode"],
            "conversation_continuity_restore_failed",
        )
        serialized = json.dumps(status)
        self.assertNotIn("private", serialized)
        self.assertNotIn(str(self.root), serialized)
        self.assertFalse(self.checkpoint_path.exists())

    def test_empty_store_removes_completed_turn_checkpoint(self) -> None:
        clock = FakeClock(wall=1000.0, monotonic=100.0)
        store = self.populated_store()
        manager = self.manager(store, clock)
        manager.flush()
        self.assertTrue(self.checkpoint_path.exists())

        for mapping in (
            store.histories,
            store.followup_targets,
            store.active_until,
            store.active_user_ids,
            store.last_active_at,
            store.awaiting_user_reply,
            store.last_speaker,
            store.topic_ids,
            store.turn_ids,
        ):
            mapping.clear()
        clock.wall = 1001.0

        status = manager.flush()

        self.assertEqual(status["state"], "empty")
        self.assertFalse(self.checkpoint_path.exists())
        self.assertEqual(status["persistedSessionCount"], 0)

    def test_guild_reset_marker_filters_old_checkpoint_after_mid_reset_crash(
        self,
    ) -> None:
        clock = FakeClock(wall=1000.0, monotonic=100.0)
        store = self.populated_store()
        other_key = self.add_other_guild(store)
        manager = self.manager(store, clock)
        manager.flush()

        class SimulatedCrash(RuntimeError):
            pass

        with self.assertRaises(SimulatedCrash):
            manager.reset_guild(
                1,
                lambda: (_ for _ in ()).throw(
                    SimulatedCrash("process exited during reset")
                ),
            )

        restored_store = SessionStateStore.create_empty()
        restored = self.manager(
            restored_store,
            FakeClock(wall=1001.0, monotonic=500.0),
        ).restore()
        ledger = json.loads(
            (self.root / "guild_revocations.json").read_text(encoding="utf-8")
        )

        self.assertEqual(restored["state"], "restored")
        self.assertEqual(restored["restoredSessionCount"], 1)
        self.assertNotIn("guild:1:text:2:user:3", restored_store.histories)
        self.assertIn(other_key, restored_store.histories)
        self.assertEqual(ledger["guilds"], {"1": 1000.0})
        self.assertTrue(ledger["policy"]["contentFree"])
        serialized = json.dumps(ledger, ensure_ascii=False)
        self.assertNotIn("내가 하던 이야기를 기억해", serialized)
        self.assertNotIn("다른 길드의 대화", serialized)

    def test_successful_guild_reset_rewrites_checkpoint_before_unrevoking(
        self,
    ) -> None:
        clock = FakeClock(wall=1000.0, monotonic=100.0)
        store = self.populated_store()
        other_key = self.add_other_guild(store)
        manager = self.manager(store, clock)
        manager.flush()

        def clear_target() -> None:
            for mapping in (
                store.histories,
                store.followup_targets,
                store.active_until,
                store.active_user_ids,
                store.last_active_at,
                store.awaiting_user_reply,
                store.last_speaker,
                store.topic_ids,
                store.turn_ids,
            ):
                mapping.pop("guild:1:text:2:user:3", None)

        clock.wall = 1001.0
        result = manager.reset_guild(1, clear_target)
        checkpoint = json.loads(
            self.checkpoint_path.read_text(encoding="utf-8")
        )
        ledger = json.loads(
            (self.root / "guild_revocations.json").read_text(encoding="utf-8")
        )

        self.assertEqual(result["state"], "ready")
        self.assertEqual(
            [row["sessionKey"] for row in checkpoint["sessions"]],
            [other_key],
        )
        self.assertEqual(ledger["guilds"], {})
        self.assertEqual(result["guildRevocationCount"], 0)

    def test_guild_reset_does_not_run_if_revocation_marker_is_not_durable(
        self,
    ) -> None:
        clock = FakeClock(wall=1000.0, monotonic=100.0)
        store = self.populated_store()
        manager = self.manager(store, clock)
        manager.flush()
        reset_called = False

        def fail_revocation_write(path: Path, payload: dict, **kwargs) -> None:
            if Path(path).name == "guild_revocations.json":
                raise PermissionError("revocation path unavailable")
            atomic_json_write(path, payload, **kwargs)

        def reset() -> None:
            nonlocal reset_called
            reset_called = True

        with patch(
            "evelyn_core.session_continuity.atomic_json_write",
            side_effect=fail_revocation_write,
        ):
            with self.assertRaisesRegex(
                RuntimeError,
                "conversation_continuity_guild_reset_revoke_failed",
            ):
                manager.reset_guild(1, reset)

        self.assertFalse(reset_called)
        self.assertTrue(self.checkpoint_path.exists())

    def test_corrupt_guild_revocation_ledger_rejects_checkpoint(self) -> None:
        clock = FakeClock(wall=1000.0, monotonic=100.0)
        self.manager(self.populated_store(), clock).flush()
        (self.root / "guild_revocations.json").write_text(
            '{"schema":"wrong","guilds":{"1":1000}}',
            encoding="utf-8",
        )
        restored_store = SessionStateStore.create_empty()

        status = self.manager(
            restored_store,
            FakeClock(wall=1001.0, monotonic=500.0),
        ).restore()

        self.assertEqual(status["state"], "error")
        self.assertEqual(
            status["lastErrorCode"],
            "conversation_continuity_checkpoint_rejected",
        )
        self.assertEqual(restored_store.histories, {})
        self.assertFalse(self.checkpoint_path.exists())

    def test_expired_followup_restores_history_but_not_active_state(self) -> None:
        source = self.populated_store()
        source.active_until["guild:1:text:2:user:3"] = 101.0
        self.manager(
            source,
            FakeClock(wall=1000.0, monotonic=100.0),
        ).flush()

        restored_store = SessionStateStore.create_empty()
        self.manager(
            restored_store,
            FakeClock(wall=1002.0, monotonic=500.0),
        ).restore()

        session_key = "guild:1:text:2:user:3"
        self.assertIn(session_key, restored_store.histories)
        self.assertNotIn(session_key, restored_store.active_until)
        self.assertNotIn(session_key, restored_store.awaiting_user_reply)
        self.assertNotIn(session_key, restored_store.followup_targets)

    def test_flush_failure_discards_old_checkpoint_fail_closed(self) -> None:
        clock = FakeClock(wall=1000.0, monotonic=100.0)
        store = self.populated_store()
        manager = self.manager(store, clock)
        manager.flush()
        self.assertTrue(self.checkpoint_path.exists())
        store.histories["guild:1:text:2:user:3"].append(
            {"role": "user", "content": "새 민감한 턴"}
        )

        with patch(
            "evelyn_core.session_continuity.atomic_json_write",
            side_effect=PermissionError("private path"),
        ):
            status = manager.flush()

        self.assertEqual(status["state"], "error")
        self.assertEqual(
            status["lastErrorCode"],
            "conversation_continuity_flush_failed",
        )
        self.assertFalse(self.checkpoint_path.exists())
        self.assertNotIn("private", json.dumps(status))

    def test_revocation_marker_rejects_old_checkpoint_when_unlink_was_busy(
        self,
    ) -> None:
        clock = FakeClock(wall=1000.0, monotonic=100.0)
        store = self.populated_store()
        manager = self.manager(store, clock)
        manager.flush()
        old_payload = self.checkpoint_path.read_text(encoding="utf-8")
        store.histories["guild:1:text:2:user:3"].append(
            {"role": "user", "content": "저장 실패 직전 턴"}
        )
        clock.wall = 1001.0

        def fail_checkpoint_only(
            path: Path,
            payload: dict,
            **kwargs,
        ) -> None:
            if Path(path) == self.checkpoint_path:
                raise PermissionError("checkpoint busy")
            atomic_json_write(path, payload, **kwargs)

        with (
            patch(
                "evelyn_core.session_continuity.atomic_json_write",
                side_effect=fail_checkpoint_only,
            ),
            patch.object(manager, "_discard_checkpoint"),
        ):
            failed = manager.flush()

        self.assertEqual(failed["checkpointRevokedAt"], 1001.0)
        self.assertEqual(
            self.checkpoint_path.read_text(encoding="utf-8"),
            old_payload,
        )

        restored_store = SessionStateStore.create_empty()
        rejected = self.manager(
            restored_store,
            FakeClock(wall=1002.0, monotonic=500.0),
        ).restore()

        self.assertEqual(rejected["state"], "error")
        self.assertEqual(
            rejected["lastErrorCode"],
            "conversation_continuity_checkpoint_rejected",
        )
        self.assertEqual(restored_store.histories, {})
        self.assertFalse(self.checkpoint_path.exists())

    def test_oversized_checkpoint_is_rejected_and_removed(self) -> None:
        self.checkpoint_path.write_text("x" * 5000, encoding="utf-8")
        manager = SessionContinuityCheckpoint(
            store=SessionStateStore.create_empty(),
            checkpoint_path=self.checkpoint_path,
            status_path=self.status_path,
            system_prompt="system",
            max_file_bytes=4096,
            wall_time=lambda: 1000.0,
            monotonic=lambda: 100.0,
        )

        status = manager.restore()

        self.assertEqual(status["state"], "error")
        self.assertEqual(
            status["lastErrorCode"],
            "conversation_continuity_checkpoint_rejected",
        )
        self.assertFalse(self.checkpoint_path.exists())

    def test_flush_enforces_session_history_and_content_bounds(self) -> None:
        store = SessionStateStore.create_empty()
        for index, session_key in enumerate(("guild:1:text:1", "guild:2:text:2")):
            store.histories[session_key] = [
                {"role": "system", "content": "private"},
                {"role": "user", "content": "a" * 200},
                {"role": "assistant", "content": "b" * 200},
                {"role": "user", "content": "c" * 200},
                {"role": "assistant", "content": "d" * 200},
            ]
            store.last_active_at[session_key] = float(index)
        manager = SessionContinuityCheckpoint(
            store=store,
            checkpoint_path=self.checkpoint_path,
            status_path=self.status_path,
            system_prompt="system",
            max_sessions=1,
            max_history_items=2,
            max_content_chars=128,
            wall_time=lambda: 1000.0,
            monotonic=lambda: 100.0,
        )

        manager.flush()

        payload = json.loads(self.checkpoint_path.read_text(encoding="utf-8"))
        self.assertEqual(len(payload["sessions"]), 1)
        self.assertEqual(payload["sessions"][0]["sessionKey"], "guild:2:text:2")
        history = payload["sessions"][0]["history"]
        self.assertEqual([item["role"] for item in history], ["user", "assistant"])
        self.assertTrue(all(len(item["content"]) == 128 for item in history))
        self.assertNotIn("private", json.dumps(payload))


class SessionContinuityAsyncTests(unittest.IsolatedAsyncioTestCase):
    async def test_periodic_writer_is_single_flight_and_detects_direct_mutation(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            store = SessionStateStore.create_empty()
            manager = SessionContinuityCheckpoint(
                store=store,
                checkpoint_path=root / "active.json",
                status_path=root / "status.json",
                system_prompt="system",
                flush_interval_sec=0.25,
            )

            first = manager.ensure_started()
            second = manager.ensure_started()
            store.histories["guild:1:default"] = [
                {"role": "system", "content": "system"},
                {"role": "user", "content": "직접 변경"},
                {"role": "assistant", "content": "감지했어"},
            ]
            await asyncio.sleep(0.4)

            self.assertIs(first, second)
            self.assertTrue((root / "active.json").is_file())
            payload = json.loads(
                (root / "active.json").read_text(encoding="utf-8")
            )
            self.assertEqual(len(payload["sessions"]), 1)
            first.cancel()
            with self.assertRaises(asyncio.CancelledError):
                await first


if __name__ == "__main__":
    unittest.main()
