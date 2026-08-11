from __future__ import annotations

import json
import sys
import tempfile
import unittest
from pathlib import Path


REPO_ROOT = next(
    path for path in Path(__file__).resolve().parents
    if (path / "main.py").exists()
)
RUNTIME_ROOT = REPO_ROOT / "evelyn_core" / "runtime"
if str(RUNTIME_ROOT) not in sys.path:
    sys.path.insert(0, str(RUNTIME_ROOT))

from evelyn_core.cross_surface_continuity import (  # noqa: E402
    CrossSurfaceContinuityBridge,
    CrossSurfaceContinuityConfig,
    CrossSurfaceMergeOutcome,
    VerifiedContinuitySnapshot,
    merge_verified_recent_context,
    read_verified_continuity_snapshot,
    session_scope_matches,
)
from evelyn_core.session_continuity import (  # noqa: E402
    SessionContinuityCheckpoint,
    _checkpoint_hash,
)
from evelyn_core.session_memory_state import (  # noqa: E402
    SessionStateStore,
)
from evelyn_core.conversation_memory_receipt import (  # noqa: E402
    memory_receipt_ref_from_receipt,
    unattributed_memory_receipt_ref,
)


NOTE_A = "concept-0123456789abcdef"


class FakeClock:
    def __init__(self, wall: float = 1000.0) -> None:
        self.wall = wall
        self.mono = 100.0

    def wall_time(self) -> float:
        return self.wall

    def monotonic(self) -> float:
        return self.mono


class CrossSurfaceContinuityTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp_dir.cleanup)
        self.root = Path(self.temp_dir.name)
        self.clock = FakeClock()

    def write_checkpoint(
        self,
        *,
        sessions: list[tuple[str, str, str]] | None = None,
        root: Path | None = None,
        memory_receipt: dict | None = None,
        last_active_monotonic: dict[str, float] | None = None,
    ) -> None:
        target_root = root or self.root
        target_root.mkdir(parents=True, exist_ok=True)
        store = SessionStateStore.create_empty()
        for session_key, user_text, assistant_text in (
            sessions
            or [
                (
                    "guild:7:text:8:user:9",
                    "컨트롤 페이지에서 이어진 질문",
                    "검증된 답변",
                )
            ]
        ):
            store.append_history(
                session_key,
                user_text,
                assistant_text,
                system_prompt="secret system prompt",
                max_history_items=12,
                memory_receipt=memory_receipt,
            )
            store.mark_active(
                session_key,
                ttl_sec=900.0,
                speaker="assistant",
                awaiting_user_reply=False,
                topic_id="topic",
                answer_text=assistant_text,
                active_conversation_awaiting_reply_sec=900.0,
                now_monotonic=self.clock.mono,
            )
            if (
                last_active_monotonic is not None
                and session_key in last_active_monotonic
            ):
                store.last_active_at[session_key] = (
                    last_active_monotonic[session_key]
                )
        manager = SessionContinuityCheckpoint(
            store=store,
            checkpoint_path=target_root / "active.json",
            status_path=target_root / "status.json",
            system_prompt="secret system prompt",
            max_age_sec=900.0,
            wall_time=self.clock.wall_time,
            monotonic=self.clock.monotonic,
        )
        status = manager.flush()
        self.assertEqual(status["state"], "ready")

    def write_empty_boundary(
        self,
        *,
        root: Path,
    ) -> None:
        root.mkdir(parents=True, exist_ok=True)
        manager = SessionContinuityCheckpoint(
            store=SessionStateStore.create_empty(),
            checkpoint_path=root / "active.json",
            status_path=root / "status.json",
            system_prompt="secret system prompt",
            max_age_sec=900.0,
            wall_time=self.clock.wall_time,
            monotonic=self.clock.monotonic,
        )
        status = manager.flush()
        self.assertEqual(status["state"], "empty")

    def read(self, **kwargs):
        return read_verified_continuity_snapshot(
            self.root,
            source="main",
            wall_time=self.clock.wall_time,
            **kwargs,
        )

    def test_reads_only_current_hash_anchored_checkpoint(self) -> None:
        self.write_checkpoint()
        before = {
            path.name: (
                path.read_bytes(),
                path.stat().st_mtime_ns,
            )
            for path in self.root.iterdir()
        }

        snapshot = self.read(guild_id=7, user_id=9)

        self.assertTrue(snapshot.verified)
        self.assertEqual(snapshot.generation, 1)
        self.assertEqual(
            list(snapshot.messages),
            [
                {
                    "role": "user",
                    "content": "컨트롤 페이지에서 이어진 질문",
                },
                {
                    "role": "assistant",
                    "content": "검증된 답변",
                    "memoryReceiptRef": (
                        unattributed_memory_receipt_ref()
                    ),
                },
            ],
        )
        after = {
            path.name: (
                path.read_bytes(),
                path.stat().st_mtime_ns,
            )
            for path in self.root.iterdir()
        }
        self.assertEqual(after, before)
        self.assertNotIn(
            "검증된 답변",
            json.dumps(
                snapshot.public_status(),
                ensure_ascii=False,
            ),
        )

    def test_bound_receipt_survives_read_and_public_status_is_private(
        self,
    ) -> None:
        self.write_checkpoint(
            memory_receipt={
                "schema": "memory.context-receipt.v1",
                "state": "provided",
                "groundingState": "attributed",
                "memoryVersion": 11,
                "suppliedNoteIds": [NOTE_A],
                "suppliedNoteCount": 1,
                "contentFree": True,
            }
        )

        snapshot = self.read(guild_id=7, user_id=9)

        self.assertEqual(
            snapshot.messages[-1]["memoryReceiptRef"][
                "suppliedNoteIds"
            ],
            [NOTE_A],
        )
        self.assertNotIn(
            NOTE_A,
            json.dumps(
                snapshot.public_status(),
                ensure_ascii=False,
            ),
        )

    def test_cross_reader_drops_invalid_assistant_receipt_row(self) -> None:
        self.write_checkpoint()
        checkpoint_path = self.root / "active.json"
        head_path = self.root / "checkpoint_head.json"
        payload = json.loads(
            checkpoint_path.read_text(encoding="utf-8")
        )
        assistant = payload["sessions"][0]["history"][-1]
        assistant["memoryReceiptRef"]["privateText"] = (
            "invalid extra field"
        )
        payload["checkpointHash"] = _checkpoint_hash(payload)
        head = json.loads(head_path.read_text(encoding="utf-8"))
        head["checkpointHash"] = payload["checkpointHash"]
        checkpoint_path.write_text(
            json.dumps(payload, ensure_ascii=False),
            encoding="utf-8",
        )
        head_path.write_text(
            json.dumps(head, ensure_ascii=False),
            encoding="utf-8",
        )

        snapshot = self.read(guild_id=7, user_id=9)

        self.assertTrue(snapshot.verified)
        self.assertEqual(
            list(snapshot.messages),
            [
                {
                    "role": "user",
                    "content": "컨트롤 페이지에서 이어진 질문",
                }
            ],
        )

    def test_adjacent_dedupe_keeps_bound_over_not_used(self) -> None:
        bound = memory_receipt_ref_from_receipt(
            {
                "schema": "memory.context-receipt.v1",
                "state": "provided",
                "groundingState": "attributed",
                "memoryVersion": 2,
                "suppliedNoteIds": [NOTE_A],
                "suppliedNoteCount": 1,
                "contentFree": True,
            }
        )
        snapshot = VerifiedContinuitySnapshot(
            source="fast_control",
            state="verified",
            saved_at=1000.0,
            generation=1,
            messages=(
                {
                    "role": "assistant",
                    "content": "같은 답변",
                    "memoryReceiptRef": bound,
                },
            ),
        )

        merged = merge_verified_recent_context(
            [
                {
                    "role": "assistant",
                    "content": "같은 답변",
                    "memoryReceiptRef": (
                        memory_receipt_ref_from_receipt(None)
                    ),
                }
            ],
            snapshot,
            local_saved_at=999.0,
        )

        self.assertEqual(len(merged), 1)
        self.assertEqual(
            merged[0]["memoryReceiptRef"],
            bound,
        )

    def test_rejects_tamper_head_mismatch_and_symlink(self) -> None:
        self.write_checkpoint()
        checkpoint_path = self.root / "active.json"
        head_path = self.root / "checkpoint_head.json"
        payload = json.loads(
            checkpoint_path.read_text(encoding="utf-8")
        )
        payload["sessions"][0]["state"].pop("lastActiveAgoSec")
        payload["checkpointHash"] = _checkpoint_hash(payload)
        head = json.loads(head_path.read_text(encoding="utf-8"))
        head["checkpointHash"] = payload["checkpointHash"]
        checkpoint_path.write_text(json.dumps(payload), encoding="utf-8")
        head_path.write_text(json.dumps(head), encoding="utf-8")
        self.assertEqual(self.read().state, "rejected")

        checkpoint_path.unlink()
        head_path.unlink()
        self.write_checkpoint()
        payload = json.loads(
            checkpoint_path.read_text(encoding="utf-8")
        )
        payload["sessions"][0]["history"][0]["content"] = (
            "변조된 내용"
        )
        checkpoint_path.write_text(
            json.dumps(payload, ensure_ascii=False),
            encoding="utf-8",
        )
        self.assertEqual(self.read().state, "rejected")

        self.root.joinpath("active.json").unlink()
        self.root.joinpath("checkpoint_head.json").unlink()
        self.write_checkpoint()
        head = json.loads(head_path.read_text(encoding="utf-8"))
        head["generation"] += 1
        head_path.write_text(
            json.dumps(head),
            encoding="utf-8",
        )
        self.assertEqual(self.read().state, "rejected")

        try:
            checkpoint_path.unlink()
            checkpoint_path.symlink_to(
                self.root / "checkpoint_head.json"
            )
        except (OSError, NotImplementedError):
            self.skipTest("symlink creation unavailable")
        self.assertEqual(self.read().state, "rejected")

    def test_rejects_lagging_head_instead_of_repairing_it(self) -> None:
        self.write_checkpoint()
        head_path = self.root / "checkpoint_head.json"
        old_head = head_path.read_bytes()
        self.clock.wall += 1.0
        self.write_checkpoint()
        head_path.write_bytes(old_head)

        snapshot = self.read()

        self.assertEqual(snapshot.state, "rejected")
        self.assertEqual(head_path.read_bytes(), old_head)

    def test_stale_policy_and_revocation_fail_closed(self) -> None:
        self.write_checkpoint()
        self.clock.wall += 901.0
        self.assertEqual(self.read().state, "stale")

        self.clock.wall = 1300.0
        self.clock.mono = 300.0
        self.write_checkpoint(
            sessions=[
                (
                    "guild:7:text:8:user:9",
                    "철회 전 질문",
                    "철회 전 답",
                ),
                (
                    "guild:7:text:9:user:10",
                    "무관한 최신 질문",
                    "무관한 최신 답",
                ),
            ],
            last_active_monotonic={
                "guild:7:text:8:user:9": 100.0,
                "guild:7:text:9:user:10": 300.0,
            },
        )
        revocations = {
            "schema": "conversation_continuity.guild_revocations.v1",
            "updatedAt": 1150.0,
            "guilds": {"7": 1150.0},
            "policy": {
                "contentFree": True,
                "maxGuilds": 256,
            },
        }
        (self.root / "guild_revocations.json").write_text(
            json.dumps(revocations),
            encoding="utf-8",
        )
        revoked = self.read(guild_id=7, user_id=9)
        self.assertTrue(revoked.verified)
        self.assertEqual(revoked.session_count, 0)
        self.assertEqual(revoked.messages, ())
        self.assertEqual(revoked.selected_activity_at, 1150.0)

        (self.root / "guild_revocations.json").write_text(
            "{broken",
            encoding="utf-8",
        )
        self.assertEqual(self.read().state, "rejected")

    def test_empty_head_is_verified_as_content_free_reset_boundary(
        self,
    ) -> None:
        self.write_empty_boundary(root=self.root)

        snapshot = self.read()

        self.assertEqual(snapshot.state, "empty")
        self.assertEqual(snapshot.messages, ())
        self.assertEqual(snapshot.saved_at, 1000.0)

    def test_exact_guild_and_user_scope_prevents_cross_member_leak(self) -> None:
        self.write_checkpoint(
            sessions=[
                (
                    "guild:7:text:10:user:9",
                    "내 질문",
                    "내 답",
                ),
                (
                    "guild:7:text:11:user:12",
                    "다른 사람 질문",
                    "다른 사람 답",
                ),
                (
                    "guild:13:text:14:user:9",
                    "다른 서버 질문",
                    "다른 서버 답",
                ),
            ]
        )

        snapshot = self.read(guild_id=7, user_id=9)

        self.assertTrue(snapshot.verified)
        self.assertEqual(snapshot.session_count, 1)
        serialized = json.dumps(
            snapshot.messages,
            ensure_ascii=False,
        )
        self.assertIn("내 질문", serialized)
        self.assertNotIn("다른 사람", serialized)
        self.assertNotIn("다른 서버", serialized)
        one_session = self.read(
            guild_id=7,
            user_id=9,
            max_sessions=1,
        )
        self.assertEqual(one_session.session_count, 1)
        self.assertIn(
            "내 질문",
            json.dumps(
                one_session.messages,
                ensure_ascii=False,
            ),
        )

    def test_merge_orders_by_saved_at_preserves_system_and_dedupes(self) -> None:
        self.write_checkpoint()
        newer_cross = self.read(guild_id=7, user_id=9)
        local = [
            {"role": "system", "content": "trusted"},
            {"role": "user", "content": "예전 질문"},
            {"role": "assistant", "content": "예전 답"},
            {"role": "user", "content": "현재 질문"},
        ]

        merged = merge_verified_recent_context(
            local,
            newer_cross,
            local_saved_at=900.0,
            current_user_text="현재 질문",
            limit=8,
        )

        self.assertEqual(
            merged[0],
            {"role": "system", "content": "trusted"},
        )
        self.assertEqual(
            [row["content"] for row in merged[1:]],
            [
                "예전 질문",
                "예전 답",
                "컨트롤 페이지에서 이어진 질문",
                "검증된 답변",
            ],
        )
        older_cross = type(newer_cross)(
            **{
                **newer_cross.__dict__,
                "saved_at": 800.0,
                "selected_activity_at": 800.0,
                "messages": (
                    {
                        "role": "assistant",
                        "content": "예전 답",
                    },
                ),
            }
        )
        merged = merge_verified_recent_context(
            local[:-1],
            older_cross,
            local_saved_at=900.0,
            limit=8,
        )
        self.assertEqual(
            [row["content"] for row in merged[1:]],
            ["예전 답", "예전 질문", "예전 답"],
        )

    def test_unrelated_session_commit_does_not_reorder_selected_context(
        self,
    ) -> None:
        artifacts = self.root / "runtime_artifacts"
        main_root = artifacts / "conversation_continuity"
        fast_root = artifacts / "fast_control_continuity"
        self.clock.wall = 1200.0
        self.clock.mono = 200.0
        self.write_checkpoint(
            root=fast_root,
            sessions=[(
                "fast-control:control-page:owner",
                "Fast 새 질문",
                "Fast 새 답",
            )],
        )
        self.clock.wall = 1300.0
        self.clock.mono = 300.0
        self.write_checkpoint(
            root=main_root,
            sessions=[
                ("guild:7:text:8:user:9", "Main 예전 질문", "Main 예전 답"),
                ("guild:7:text:9:user:10", "다른 질문", "다른 답"),
            ],
            last_active_monotonic={
                "guild:7:text:8:user:9": 100.0,
                "guild:7:text:9:user:10": 300.0,
            },
        )
        bridge = CrossSurfaceContinuityBridge(
            artifacts_root=artifacts,
            config=CrossSurfaceContinuityConfig(
                enabled=True,
                guild_id=7,
                user_id=9,
                max_messages=2,
            ),
            wall_time=self.clock.wall_time,
        )
        main_snapshot = read_verified_continuity_snapshot(
            main_root,
            source="main",
            wall_time=self.clock.wall_time,
            guild_id=7,
            user_id=9,
        )
        for_fast = bridge.merge_for_fast_observed(
            [
                {"role": "user", "content": "Fast 새 질문"},
                {"role": "assistant", "content": "Fast 새 답"},
            ],
            current_user_text="후속 질문",
        )
        for_main = bridge.merge_for_main_observed(
            [
                {"role": "user", "content": "Main 예전 질문"},
                {"role": "assistant", "content": "Main 예전 답"},
            ],
            session_key="guild:7:text:8:user:9",
            current_user_text="후속 질문",
        )
        self.assertEqual(main_snapshot.saved_at, 1300.0)
        self.assertEqual(main_snapshot.selected_activity_at, 1100.0)
        self.assertEqual(
            read_verified_continuity_snapshot(
                main_root,
                source="main",
                wall_time=lambda: 2050.0,
                guild_id=7,
                user_id=9,
            ).state,
            "stale",
        )
        self.assertEqual(
            [row["content"] for row in for_fast.messages],
            [
                "Fast 새 질문",
                "Fast 새 답",
            ],
        )
        self.assertEqual(
            for_fast.evidence["ordering"],
            "cross_before_local",
        )
        self.assertEqual(
            [row["content"] for row in for_main.messages],
            [
                "Fast 새 질문",
                "Fast 새 답",
            ],
        )
        self.assertEqual(for_main.evidence["ordering"], "cross_after_local")

    def test_unavailable_cross_snapshot_preserves_local_context_exactly(
        self,
    ) -> None:
        local = [
            {"role": "system", "content": "trusted"},
            *[
                {
                    "role": (
                        "user"
                        if index % 2 == 0
                        else "assistant"
                    ),
                    "content": f"local-{index}",
                }
                for index in range(12)
            ],
        ]

        merged = merge_verified_recent_context(
            local,
            type(self.read())(
                source="main",
                state="rejected",
                error_code=(
                    "cross_surface_continuity_rejected"
                ),
            ),
            current_user_text="local-10",
            limit=8,
        )

        self.assertEqual(merged, local)

    def test_config_requires_explicit_personal_scope(self) -> None:
        missing = CrossSurfaceContinuityConfig.from_env(
            {
                "CROSS_SURFACE_CONTINUITY_ENABLED": "true",
            }
        )
        configured = CrossSurfaceContinuityConfig.from_env(
            {
                "CROSS_SURFACE_CONTINUITY_ENABLED": "true",
                "CROSS_SURFACE_CONTINUITY_GUILD_ID": "7",
                "CROSS_SURFACE_CONTINUITY_USER_ID": "9",
            }
        )

        self.assertFalse(missing.scope_ready)
        self.assertEqual(
            missing.public_status()["errorCode"],
            "cross_surface_scope_not_configured",
        )
        self.assertTrue(configured.scope_ready)
        self.assertTrue(
            session_scope_matches(
                "guild:7:voice:8:user:9",
                guild_id=7,
                user_id=9,
            )
        )
        self.assertFalse(
            session_scope_matches(
                "guild:7:voice:8:user:10",
                guild_id=7,
                user_id=9,
            )
        )

    def test_bridge_merges_both_directions_only_for_bound_scope(
        self,
    ) -> None:
        artifacts = self.root / "runtime_artifacts"
        main_root = artifacts / "conversation_continuity"
        fast_root = artifacts / "fast_control_continuity"
        self.write_checkpoint(
            root=main_root,
            sessions=[
                (
                    "guild:7:text:8:user:9",
                    "디스코드 질문",
                    "디스코드 답",
                )
            ],
        )
        self.clock.wall += 1.0
        self.write_checkpoint(
            root=fast_root,
            sessions=[
                (
                    "fast-control:control-page:owner",
                    "컨트롤 질문",
                    "컨트롤 답",
                )
            ],
        )
        bridge = CrossSurfaceContinuityBridge(
            artifacts_root=artifacts,
            config=CrossSurfaceContinuityConfig(
                enabled=True,
                guild_id=7,
                user_id=9,
            ),
            wall_time=self.clock.wall_time,
        )

        for_main = bridge.merge_for_main(
            [
                {"role": "system", "content": "system"},
                {"role": "user", "content": "디스코드 질문"},
                {"role": "assistant", "content": "디스코드 답"},
            ],
            session_key="guild:7:voice:8:user:9",
            current_user_text="후속 질문",
        )
        self.assertEqual(
            [row["content"] for row in for_main],
            [
                "system",
                "디스코드 질문",
                "디스코드 답",
                "컨트롤 질문",
                "컨트롤 답",
            ],
        )
        denied = bridge.merge_for_main(
            [{"role": "system", "content": "system"}],
            session_key="guild:7:voice:8:user:10",
            current_user_text="후속 질문",
        )
        self.assertEqual(
            denied,
            [{"role": "system", "content": "system"}],
        )
        fast_outcome = bridge.merge_for_fast_observed(
            [
                {"role": "user", "content": "컨트롤 질문"},
                {"role": "assistant", "content": "컨트롤 답"},
            ],
            current_user_text="후속 질문",
        )
        for_fast = list(fast_outcome.messages)
        self.assertEqual(
            [row["content"] for row in for_fast],
            [
                "디스코드 질문",
                "디스코드 답",
                "컨트롤 질문",
                "컨트롤 답",
            ],
        )
        fast_outcome.evidence["privateMessage"] = (
            "노출되면 안 되는 원문"
        )
        serialized_status = json.dumps(
            bridge.public_status(),
            ensure_ascii=False,
        )
        self.assertNotIn("디스코드 질문", serialized_status)
        self.assertNotIn("컨트롤 질문", serialized_status)
        self.assertNotIn("노출되면 안 되는 원문", serialized_status)
        last_merge = bridge.public_status()["lastMerge"]
        self.assertEqual(
            set(last_merge),
            {
                "schema",
                "state",
                "sourceSurface",
                "reasonCode",
                "localOwnerState",
                "crossOwnerState",
                "localGeneration",
                "crossGeneration",
                "localMessageCount",
                "crossMessageCount",
                "outputMessageCount",
                "ordering",
                "updatedAt",
                "latencyMs",
                "policy",
            },
        )
        self.assertEqual(
            set(last_merge["policy"]),
            {"contentFree", "persisted", "readOnly"},
        )
        self.assertEqual(
            last_merge["schema"],
            "cross_surface_continuity.merge.v1",
        )
        self.assertEqual(last_merge["state"], "merged")
        self.assertEqual(
            last_merge["sourceSurface"],
            "fast_control",
        )
        self.assertEqual(
            last_merge["ordering"],
            "cross_before_local",
        )
        self.assertEqual(
            last_merge["localMessageCount"],
            2,
        )
        self.assertEqual(
            last_merge["crossMessageCount"],
            2,
        )
        self.assertTrue(last_merge["policy"]["contentFree"])
        self.assertFalse(last_merge["policy"]["persisted"])
        self.assertTrue(last_merge["policy"]["readOnly"])

    def test_newer_empty_owner_boundary_does_not_resurrect_other_owner(
        self,
    ) -> None:
        artifacts = self.root / "runtime_artifacts"
        main_root = artifacts / "conversation_continuity"
        fast_root = artifacts / "fast_control_continuity"
        self.clock.wall += 1.0
        self.clock.mono += 1.0
        self.write_empty_boundary(root=fast_root)
        self.clock.wall += 1.0
        self.clock.mono += 1.0
        self.write_checkpoint(
            root=main_root,
            sessions=[
                (
                    "guild:7:text:8:user:9",
                    "삭제 전 질문",
                    "삭제 전 답",
                ),
                (
                    "guild:7:text:9:user:10",
                    "무관한 최신 질문",
                    "무관한 최신 답",
                ),
            ],
            last_active_monotonic={
                "guild:7:text:8:user:9": 100.0,
                "guild:7:text:9:user:10": 102.0,
            },
        )
        bridge = CrossSurfaceContinuityBridge(
            artifacts_root=artifacts,
            config=CrossSurfaceContinuityConfig(
                enabled=True,
                guild_id=7,
                user_id=9,
            ),
            wall_time=self.clock.wall_time,
        )

        outcome = bridge.merge_for_fast_observed(
            [],
            current_user_text="새 질문",
        )

        self.assertIsInstance(
            outcome,
            CrossSurfaceMergeOutcome,
        )
        self.assertEqual(list(outcome.messages), [])
        self.assertEqual(
            outcome.evidence["state"],
            "reset_boundary",
        )
        self.assertEqual(
            outcome.evidence["reasonCode"],
            "local_reset_boundary_newer",
        )

    def test_rejected_local_owner_blocks_valid_cross_context(
        self,
    ) -> None:
        artifacts = self.root / "runtime_artifacts"
        main_root = artifacts / "conversation_continuity"
        fast_root = artifacts / "fast_control_continuity"
        self.write_checkpoint(
            root=main_root,
            sessions=[
                (
                    "guild:7:text:8:user:9",
                    "로컬 질문",
                    "로컬 답",
                )
            ],
        )
        self.clock.wall += 1.0
        self.write_checkpoint(
            root=fast_root,
            sessions=[
                (
                    "fast-control:control-page:owner",
                    "주입되면 안 되는 질문",
                    "주입되면 안 되는 답",
                )
            ],
        )
        main_checkpoint = main_root / "active.json"
        payload = json.loads(
            main_checkpoint.read_text(encoding="utf-8")
        )
        payload["sessions"][0]["history"][0][
            "content"
        ] = "변조"
        main_checkpoint.write_text(
            json.dumps(payload, ensure_ascii=False),
            encoding="utf-8",
        )
        bridge = CrossSurfaceContinuityBridge(
            artifacts_root=artifacts,
            config=CrossSurfaceContinuityConfig(
                enabled=True,
                guild_id=7,
                user_id=9,
            ),
            wall_time=self.clock.wall_time,
        )
        local = [
            {"role": "system", "content": "system"},
            {"role": "user", "content": "현재 로컬"},
        ]

        outcome = bridge.merge_for_main_observed(
            local,
            session_key="guild:7:text:8:user:9",
            current_user_text="새 질문",
        )

        self.assertEqual(list(outcome.messages), local)
        self.assertEqual(
            outcome.evidence["state"],
            "rejected",
        )
        self.assertEqual(
            outcome.evidence["reasonCode"],
            "local_owner_rejected",
        )
        public = json.dumps(
            outcome.public_status(),
            ensure_ascii=False,
        )
        self.assertNotIn("주입되면 안 되는", public)
        self.assertNotIn("현재 로컬", public)


if __name__ == "__main__":
    unittest.main()
