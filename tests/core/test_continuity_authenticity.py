from __future__ import annotations

import json
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch


REPO_ROOT = next(
    path
    for path in Path(__file__).resolve().parents
    if (path / "main.py").exists()
)
RUNTIME_ROOT = REPO_ROOT / "evelyn_core" / "runtime"
if str(RUNTIME_ROOT) not in sys.path:
    sys.path.insert(0, str(RUNTIME_ROOT))

from evelyn_core.continuity_authenticity import (  # noqa: E402
    CONTINUITY_AUTH_ANCHOR_SLOT_FAST_ACTION_HEAD,
    CONTINUITY_AUTH_ANCHOR_SLOT_FAST_CONTROL_HEAD,
    CONTINUITY_AUTH_ANCHOR_SLOT_GUILD_REVOCATIONS,
    CONTINUITY_AUTH_ANCHOR_SLOT_MAIN_HEAD,
    CONTINUITY_AUTH_SCOPE_FAST_CONTROL,
    CONTINUITY_AUTH_SCOPE_MAIN,
    CONTINUITY_HEAD_SCHEMA_V1,
    CONTINUITY_HEAD_SCHEMA_V2,
    ContinuityAuthenticity,
    ContinuityAuthenticityError,
    load_continuity_authenticity,
)
from evelyn_core.cross_surface_continuity import (  # noqa: E402
    read_verified_continuity_snapshot,
)
from evelyn_core.continuity_commit_contract import (  # noqa: E402
    ConversationContinuityCommitError,
    require_durable_continuity_receipt,
)
from evelyn_core.fast_control_continuity import (  # noqa: E402
    FastControlContinuityOwner,
)
from evelyn_core.fast_action_recovery import (  # noqa: E402
    FAST_ACTION_RECOVERY_AUTHENTICATED_HEAD_SCHEMA,
    FastActionRecoveryJournal,
)
from evelyn_core import fast_action_recovery as action_recovery  # noqa: E402
from evelyn_core.session_continuity import (  # noqa: E402
    SESSION_CONTINUITY_ANCHORED_REVOCATIONS_SCHEMA,
    SESSION_CONTINUITY_AUTHENTICATED_REVOCATIONS_SCHEMA,
    SessionContinuityCheckpoint,
    _checkpoint_hash,
)
from evelyn_core.session_memory_state import (  # noqa: E402
    SessionStateStore,
)


class FakeClock:
    def __init__(self) -> None:
        self.wall = 1000.0
        self.mono = 100.0

    def wall_time(self) -> float:
        return self.wall

    def monotonic(self) -> float:
        return self.mono


class ContinuityAuthenticityTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp_dir.cleanup)
        self.root = Path(self.temp_dir.name)
        self.artifacts = self.root / "runtime_artifacts"
        self.owner_root = (
            self.artifacts / "conversation_continuity"
        )
        self.key_path = self.root / "continuity-auth.key"
        self.key_path.write_bytes(b"k" * 32)
        self.anchor_root = self.root / "continuity-anchor"
        self.anchor_root.mkdir()
        self.clock = FakeClock()

    def authenticity(
        self,
        *,
        bootstrap: bool = False,
        anchor: bool = False,
        key_path: Path | None = None,
    ) -> ContinuityAuthenticity:
        return load_continuity_authenticity(
            protected_root=self.artifacts,
            environ={
                "EVELYN_CONTINUITY_AUTH_KEY_FILE": str(
                    key_path or self.key_path
                ),
                "EVELYN_CONTINUITY_AUTH_BOOTSTRAP": (
                    "true" if bootstrap else "false"
                ),
                "EVELYN_CONTINUITY_AUTH_ANCHOR_DIR": (
                    str(self.anchor_root) if anchor else ""
                ),
            },
        )

    def store(self) -> SessionStateStore:
        store = SessionStateStore.create_empty()
        session_key = "guild:7:text:8:user:9"
        store.append_history(
            session_key,
            "이전 이야기를 이어줘",
            "검증된 상태에서 이어갈게.",
            system_prompt="private prompt",
            max_history_items=12,
        )
        store.mark_active(
            session_key,
            ttl_sec=900.0,
            speaker="assistant",
            awaiting_user_reply=False,
            topic_id="topic-auth",
            answer_text="검증된 상태에서 이어갈게.",
            active_conversation_awaiting_reply_sec=900.0,
            now_monotonic=self.clock.mono,
        )
        return store

    def manager(
        self,
        *,
        store: SessionStateStore | None = None,
        authenticity: ContinuityAuthenticity | None = None,
    ) -> SessionContinuityCheckpoint:
        return SessionContinuityCheckpoint(
            store=(store or SessionStateStore.create_empty()),
            checkpoint_path=self.owner_root / "active.json",
            status_path=self.owner_root / "status.json",
            system_prompt="current prompt",
            max_age_sec=900.0,
            wall_time=self.clock.wall_time,
            monotonic=self.clock.monotonic,
            authenticity=authenticity,
        )

    def test_key_file_must_be_external_and_bootstrap_needs_key(
        self,
    ) -> None:
        internal = self.artifacts / "secrets" / "key"
        internal.parent.mkdir(parents=True)
        internal.write_bytes(b"i" * 32)

        with self.assertRaises(ContinuityAuthenticityError) as caught:
            self.authenticity(key_path=internal)
        self.assertEqual(
            caught.exception.code,
            "continuity_auth_key_file_rejected",
        )
        with self.assertRaises(ContinuityAuthenticityError) as caught:
            load_continuity_authenticity(
                protected_root=self.root / "separate-repository",
                additional_protected_roots=(self.artifacts,),
                environ={
                    "EVELYN_CONTINUITY_AUTH_KEY_FILE": str(
                        internal
                    ),
                },
            )
        self.assertEqual(
            caught.exception.code,
            "continuity_auth_key_file_rejected",
        )
        with self.assertRaises(ContinuityAuthenticityError) as caught:
            load_continuity_authenticity(
                protected_root=self.artifacts,
                environ={
                    "EVELYN_CONTINUITY_AUTH_BOOTSTRAP": "true",
                },
            )
        self.assertEqual(
            caught.exception.code,
            "continuity_auth_bootstrap_without_key",
        )
        missing = self.root / "private" / "missing.key"
        with self.assertRaises(ContinuityAuthenticityError) as caught:
            self.authenticity(key_path=missing)
        self.assertEqual(
            caught.exception.code,
            "continuity_auth_key_unavailable",
        )
        self.assertNotIn(str(missing), str(caught.exception))

    def test_signed_head_round_trip_and_cross_surface_verification(
        self,
    ) -> None:
        authenticity = self.authenticity()
        written = self.manager(
            store=self.store(),
            authenticity=authenticity,
        ).flush()
        head = json.loads(
            (self.owner_root / "checkpoint_head.json").read_text(
                encoding="utf-8"
            )
        )

        self.assertEqual(written["state"], "ready")
        self.assertTrue(written["keyedAuthenticity"])
        self.assertTrue(written["tamperEvident"])
        self.assertEqual(
            written["checkpointHeadAuthenticity"],
            "verified",
        )
        self.assertEqual(head["schema"], CONTINUITY_HEAD_SCHEMA_V2)
        self.assertEqual(head["authAlgorithm"], "hmac-sha256")
        self.assertEqual(
            head["authScope"],
            CONTINUITY_AUTH_SCOPE_MAIN,
        )
        self.assertEqual(len(head["authTag"]), 64)
        self.assertNotIn(
            "이전 이야기를 이어줘",
            json.dumps(head, ensure_ascii=False),
        )

        restored = self.manager(
            authenticity=authenticity,
        ).restore()
        self.assertEqual(restored["state"], "restored")
        verified = read_verified_continuity_snapshot(
            self.owner_root,
            source="main",
            wall_time=self.clock.wall_time,
            guild_id=7,
            user_id=9,
            authenticity=authenticity,
        )
        self.assertTrue(verified.verified)
        rejected = read_verified_continuity_snapshot(
            self.owner_root,
            source="main",
            wall_time=self.clock.wall_time,
            guild_id=7,
            user_id=9,
        )
        self.assertEqual(rejected.state, "rejected")
        wrong_key_path = self.root / "wrong.key"
        wrong_key_path.write_bytes(b"w" * 32)
        wrong_key = read_verified_continuity_snapshot(
            self.owner_root,
            source="main",
            wall_time=self.clock.wall_time,
            guild_id=7,
            user_id=9,
            authenticity=self.authenticity(
                key_path=wrong_key_path
            ),
        )
        self.assertEqual(wrong_key.state, "rejected")
        wrong_scope = read_verified_continuity_snapshot(
            self.owner_root,
            source="fast_control",
            wall_time=self.clock.wall_time,
            authenticity=authenticity,
            authenticity_scope=(
                CONTINUITY_AUTH_SCOPE_FAST_CONTROL
            ),
        )
        self.assertEqual(wrong_scope.state, "rejected")

    def test_external_anchor_requires_explicit_bootstrap_and_external_root(
        self,
    ) -> None:
        authenticity = self.authenticity(anchor=True)
        with self.assertRaises(ContinuityAuthenticityError) as caught:
            authenticity.reconcile_external_anchor(
                CONTINUITY_AUTH_ANCHOR_SLOT_MAIN_HEAD,
                generation=0,
                artifact_hash="0" * 64,
                updated_at=self.clock.wall,
            )
        self.assertEqual(
            caught.exception.code,
            "continuity_anchor_bootstrap_required",
        )

        bootstrapped = self.authenticity(
            anchor=True,
            bootstrap=True,
        )
        self.assertEqual(
            bootstrapped.reconcile_external_anchor(
                CONTINUITY_AUTH_ANCHOR_SLOT_MAIN_HEAD,
                generation=0,
                artifact_hash="0" * 64,
                updated_at=self.clock.wall,
            ),
            "bootstrapped",
        )
        self.assertEqual(
            authenticity.verify_external_anchor(
                CONTINUITY_AUTH_ANCHOR_SLOT_MAIN_HEAD,
                generation=0,
                artifact_hash="0" * 64,
            ),
            "verified",
        )
        anchor_path = next(self.anchor_root.glob("*.json"))
        anchor_text = anchor_path.read_text(encoding="utf-8")
        self.assertNotIn("이전 이야기를 이어줘", anchor_text)
        anchor_payload = json.loads(anchor_text)
        anchor_payload["generation"] = 1
        anchor_path.write_text(
            json.dumps(anchor_payload),
            encoding="utf-8",
        )
        with self.assertRaises(ContinuityAuthenticityError) as caught:
            authenticity.verify_external_anchor(
                CONTINUITY_AUTH_ANCHOR_SLOT_MAIN_HEAD,
                generation=0,
                artifact_hash="0" * 64,
            )
        self.assertEqual(
            caught.exception.code,
            "continuity_anchor_auth_failed",
        )
        anchor_path.write_text(anchor_text, encoding="utf-8")

        internal_anchor = self.artifacts / "protected-anchor"
        internal_anchor.mkdir(parents=True)
        with self.assertRaises(ContinuityAuthenticityError) as caught:
            load_continuity_authenticity(
                protected_root=self.artifacts,
                environ={
                    "EVELYN_CONTINUITY_AUTH_KEY_FILE": str(
                        self.key_path
                    ),
                    "EVELYN_CONTINUITY_AUTH_ANCHOR_DIR": str(
                        internal_anchor
                    ),
                },
            )
        self.assertEqual(
            caught.exception.code,
            "continuity_anchor_directory_rejected",
        )
        with self.assertRaises(ContinuityAuthenticityError) as caught:
            load_continuity_authenticity(
                protected_root=self.artifacts,
                environ={
                    "EVELYN_CONTINUITY_AUTH_KEY_FILE": str(
                        self.key_path
                    ),
                    "EVELYN_CONTINUITY_AUTH_ANCHOR_DIR": str(
                        self.root / "missing-anchor"
                    ),
                },
            )
        self.assertEqual(
            caught.exception.code,
            "continuity_anchor_unavailable",
        )

    def test_external_anchor_rejects_signed_checkpoint_replay_and_deletion(
        self,
    ) -> None:
        bootstrap = self.authenticity(
            anchor=True,
            bootstrap=True,
        )
        store = self.store()
        manager = self.manager(
            store=store,
            authenticity=bootstrap,
        )
        first = manager.flush(force=True)
        checkpoint_path = self.owner_root / "active.json"
        head_path = self.owner_root / "checkpoint_head.json"
        old_checkpoint = checkpoint_path.read_bytes()
        old_head = head_path.read_bytes()
        old_generation = first["checkpointGeneration"]

        store.append_history(
            "guild:7:text:8:user:9",
            "두 번째 질문",
            "두 번째 답변",
            system_prompt="private prompt",
            max_history_items=12,
        )
        self.clock.wall += 1.0
        current = manager.flush(force=True)
        self.assertGreater(
            current["checkpointGeneration"],
            old_generation,
        )
        self.assertTrue(current["externalReplayProtected"])
        verified_current = read_verified_continuity_snapshot(
            self.owner_root,
            source="main",
            wall_time=self.clock.wall_time,
            guild_id=7,
            user_id=9,
            authenticity=self.authenticity(anchor=True),
        )
        self.assertTrue(verified_current.verified)

        checkpoint_path.write_bytes(old_checkpoint)
        head_path.write_bytes(old_head)
        replayed_checkpoint = checkpoint_path.read_bytes()
        replayed_head = head_path.read_bytes()
        authenticity = self.authenticity(anchor=True)
        rejected = self.manager(
            authenticity=authenticity,
        ).restore()
        cross = read_verified_continuity_snapshot(
            self.owner_root,
            source="main",
            wall_time=self.clock.wall_time,
            guild_id=7,
            user_id=9,
            authenticity=authenticity,
        )
        self.assertEqual(rejected["state"], "error")
        self.assertEqual(
            rejected["lastErrorCode"],
            "continuity_anchor_replay_detected",
        )
        self.assertEqual(
            rejected["checkpointAnchorState"],
            "replay_detected",
        )
        self.assertEqual(checkpoint_path.read_bytes(), replayed_checkpoint)
        self.assertEqual(head_path.read_bytes(), replayed_head)
        self.assertEqual(cross.state, "rejected")

        checkpoint_path.unlink()
        head_path.unlink()
        deleted = self.manager(
            authenticity=authenticity,
        ).restore()
        self.assertEqual(
            deleted["lastErrorCode"],
            "continuity_anchor_replay_detected",
        )
        self.assertFalse(checkpoint_path.exists())
        self.assertFalse(head_path.exists())

    def test_external_anchor_recovers_one_checkpoint_commit_lag(self) -> None:
        bootstrap = self.authenticity(
            anchor=True,
            bootstrap=True,
        )
        store = self.store()
        manager = self.manager(
            store=store,
            authenticity=bootstrap,
        )
        manager.flush(force=True)
        anchor_path = next(
            path
            for path in self.anchor_root.glob("*.json")
            if "checkpoint" in path.name
            and "fast-control" not in path.name
        )
        lagging_anchor = anchor_path.read_bytes()
        store.append_history(
            "guild:7:text:8:user:9",
            "앵커 crash 질문",
            "앵커 crash 답변",
            system_prompt="private prompt",
            max_history_items=12,
        )
        self.clock.wall += 1.0
        advanced = manager.flush(force=True)
        anchor_path.write_bytes(lagging_anchor)

        restored = self.manager(
            authenticity=self.authenticity(anchor=True),
        ).restore()
        position = bootstrap.external_anchor_position(
            CONTINUITY_AUTH_ANCHOR_SLOT_MAIN_HEAD
        )
        self.assertEqual(restored["state"], "restored")
        self.assertEqual(
            position[0] if position else -1,
            advanced["checkpointGeneration"],
        )
        self.assertTrue(restored["externalReplayProtected"])

    def test_checkpoint_anchor_write_failure_recovers_next_owner(self) -> None:
        bootstrap = self.authenticity(
            anchor=True,
            bootstrap=True,
        )
        store = self.store()
        manager = self.manager(
            store=store,
            authenticity=bootstrap,
        )
        manager.flush(force=True)
        store.append_history(
            "guild:7:text:8:user:9",
            "앵커 저장 실패 질문",
            "앵커 저장 실패 답변",
            system_prompt="private prompt",
            max_history_items=12,
        )
        self.clock.wall += 1.0
        with patch.object(
            ContinuityAuthenticity,
            "commit_external_anchor",
            side_effect=ContinuityAuthenticityError(
                "continuity_anchor_unavailable"
            ),
        ):
            failed = manager.flush(force=True)

        self.assertEqual(failed["state"], "error")
        self.assertEqual(
            failed["lastErrorCode"],
            "continuity_anchor_unavailable",
        )
        restored = self.manager(
            authenticity=self.authenticity(anchor=True),
        ).restore()
        self.assertEqual(restored["state"], "restored")
        self.assertTrue(restored["externalReplayProtected"])

    def test_fast_control_receipt_requires_verified_keyed_head(
        self,
    ) -> None:
        owner = FastControlContinuityOwner(
            artifacts_root=self.artifacts,
            enabled=True,
            wall_time=self.clock.wall_time,
            monotonic=self.clock.monotonic,
            authenticity=self.authenticity(),
        )

        raw = owner.record_completed_turn(
            "컨트롤 질문",
            "컨트롤 답변",
        )
        receipt = require_durable_continuity_receipt(raw)

        self.assertTrue(raw["keyedAuthenticity"])
        self.assertTrue(raw["tamperEvident"])
        self.assertTrue(receipt["durable"])
        fast_head = json.loads(
            (
                self.artifacts
                / "fast_control_continuity"
                / "checkpoint_head.json"
            ).read_text(encoding="utf-8")
        )
        self.assertEqual(
            fast_head["authScope"],
            CONTINUITY_AUTH_SCOPE_FAST_CONTROL,
        )
        forged_status = {
            **raw,
            "tamperEvident": False,
        }
        with self.assertRaises(
            ConversationContinuityCommitError
        ):
            require_durable_continuity_receipt(forged_status)

    def test_fast_control_uses_independent_external_anchor_slot(
        self,
    ) -> None:
        authenticity = self.authenticity(
            anchor=True,
            bootstrap=True,
        )
        owner = FastControlContinuityOwner(
            artifacts_root=self.artifacts,
            enabled=True,
            wall_time=self.clock.wall_time,
            monotonic=self.clock.monotonic,
            authenticity=authenticity,
        )

        raw = owner.record_completed_turn(
            "앵커 컨트롤 질문",
            "앵커 컨트롤 답변",
        )
        receipt = require_durable_continuity_receipt(raw)

        self.assertTrue(raw["externalAnchorConfigured"])
        self.assertTrue(raw["externalReplayProtected"])
        self.assertTrue(receipt["durable"])
        with self.assertRaises(
            ConversationContinuityCommitError
        ):
            require_durable_continuity_receipt(
                {**raw, "externalReplayProtected": False}
            )
        self.assertIsNotNone(
            authenticity.external_anchor_position(
                CONTINUITY_AUTH_ANCHOR_SLOT_FAST_CONTROL_HEAD
            )
        )
        self.assertIsNone(
            authenticity.external_anchor_position(
                CONTINUITY_AUTH_ANCHOR_SLOT_MAIN_HEAD
            )
        )
        self.assertIsNone(
            authenticity.external_anchor_position(
                CONTINUITY_AUTH_ANCHOR_SLOT_GUILD_REVOCATIONS
            )
        )

    def test_fast_action_rewrite_is_auth_blocked_without_overwrite(
        self,
    ) -> None:
        path = self.artifacts / "fast_control_actions" / "recovery.json"
        journal = FastActionRecoveryJournal(
            path=path,
            enabled=True,
            wall_time=self.clock.wall_time,
            authenticity=self.authenticity(),
        )
        journal.begin("fast-action-1")
        head_path = journal.head_path
        payload = json.loads(path.read_text(encoding="utf-8"))
        payload["actions"][0]["startedAt"] = 1001.0
        payload["journalHash"] = action_recovery._journal_hash(
            payload
        )
        path.write_text(
            json.dumps(payload, ensure_ascii=False),
            encoding="utf-8",
        )
        head = json.loads(head_path.read_text(encoding="utf-8"))
        head["journalHash"] = payload["journalHash"]
        head_path.write_text(
            json.dumps(head, ensure_ascii=False),
            encoding="utf-8",
        )
        malicious_journal = path.read_bytes()
        malicious_head = head_path.read_bytes()

        restored = FastActionRecoveryJournal(
            path=path,
            enabled=True,
            wall_time=self.clock.wall_time,
            authenticity=self.authenticity(),
        )
        status = restored.public_status()
        decision = restored.recovery_decision(
            continuity_generation=0
        )

        self.assertEqual(status["state"], "auth_error")
        self.assertEqual(
            status["lastErrorCode"],
            "continuity_auth_failed",
        )
        self.assertFalse(status["rollbackProtected"])
        self.assertFalse(status["tamperEvident"])
        self.assertEqual(decision["state"], "unavailable")
        self.assertFalse(decision["noticeRequired"])
        with self.assertRaises(RuntimeError):
            restored.begin("fast-action-2")
        with self.assertRaises(RuntimeError):
            restored.acknowledge_recovery(
                recovered_count=1,
                error_code="fast_action_recovery_journal_corrupt",
            )
        self.assertEqual(path.read_bytes(), malicious_journal)
        self.assertEqual(head_path.read_bytes(), malicious_head)

    def test_fast_action_unsigned_head_requires_bootstrap(
        self,
    ) -> None:
        path = self.artifacts / "fast_control_actions" / "recovery.json"
        unsigned = FastActionRecoveryJournal(
            path=path,
            enabled=True,
            wall_time=self.clock.wall_time,
        )
        unsigned.begin("fast-action-1")
        head_path = unsigned.head_path
        unsigned_head = head_path.read_bytes()

        rejected = FastActionRecoveryJournal(
            path=path,
            enabled=True,
            wall_time=self.clock.wall_time,
            authenticity=self.authenticity(),
        )
        self.assertEqual(
            rejected.public_status()["state"],
            "auth_error",
        )
        self.assertEqual(head_path.read_bytes(), unsigned_head)

        adopted = FastActionRecoveryJournal(
            path=path,
            enabled=True,
            wall_time=self.clock.wall_time,
            authenticity=self.authenticity(bootstrap=True),
        )
        signed_head = json.loads(
            head_path.read_text(encoding="utf-8")
        )
        self.assertEqual(adopted.public_status()["state"], "pending")
        self.assertTrue(adopted.public_status()["tamperEvident"])
        self.assertEqual(
            signed_head["schema"],
            FAST_ACTION_RECOVERY_AUTHENTICATED_HEAD_SCHEMA,
        )
        signed_bytes = head_path.read_bytes()
        missing_key = FastActionRecoveryJournal(
            path=path,
            enabled=True,
            wall_time=self.clock.wall_time,
        )
        self.assertEqual(
            missing_key.public_status()["state"],
            "auth_error",
        )
        self.assertEqual(
            missing_key.public_status()["lastErrorCode"],
            "continuity_auth_key_required",
        )
        self.assertEqual(head_path.read_bytes(), signed_bytes)

    def test_external_anchor_rejects_fast_action_replay_and_deletion(
        self,
    ) -> None:
        bootstrap = self.authenticity(
            anchor=True,
            bootstrap=True,
        )
        path = self.artifacts / "fast_control_actions" / "recovery.json"
        journal = FastActionRecoveryJournal(
            path=path,
            enabled=True,
            wall_time=self.clock.wall_time,
            authenticity=bootstrap,
        )
        old_journal = path.read_bytes()
        old_head = journal.head_path.read_bytes()
        anchor_path = self.anchor_root / (
            "fast-control-action-recovery.json"
        )
        lagging_anchor = anchor_path.read_bytes()
        journal.begin("fast-action-1")
        self.assertTrue(
            journal.public_status()["externalReplayProtected"]
        )
        anchor_path.write_bytes(lagging_anchor)
        recovered = FastActionRecoveryJournal(
            path=path,
            enabled=True,
            wall_time=self.clock.wall_time,
            authenticity=self.authenticity(anchor=True),
        )
        self.assertEqual(recovered.public_status()["state"], "pending")
        self.assertTrue(
            recovered.public_status()["externalReplayProtected"]
        )

        path.write_bytes(old_journal)
        journal.head_path.write_bytes(old_head)
        replay = FastActionRecoveryJournal(
            path=path,
            enabled=True,
            wall_time=self.clock.wall_time,
            authenticity=self.authenticity(anchor=True),
        )
        self.assertEqual(replay.public_status()["state"], "auth_error")
        self.assertEqual(
            replay.public_status()["lastErrorCode"],
            "continuity_anchor_replay_detected",
        )
        self.assertEqual(
            replay.public_status()["anchorState"],
            "replay_detected",
        )

        path.unlink()
        journal.head_path.unlink()
        deleted = FastActionRecoveryJournal(
            path=path,
            enabled=True,
            wall_time=self.clock.wall_time,
            authenticity=self.authenticity(anchor=True),
        )
        self.assertEqual(
            deleted.public_status()["lastErrorCode"],
            "continuity_anchor_replay_detected",
        )
        self.assertFalse(path.exists())
        self.assertFalse(journal.head_path.exists())
        self.assertEqual(
            bootstrap.external_anchor_position(
                CONTINUITY_AUTH_ANCHOR_SLOT_FAST_ACTION_HEAD
            )[0],
            2,
        )

    def test_fast_action_anchor_write_failure_recovers_pending_marker(
        self,
    ) -> None:
        bootstrap = self.authenticity(
            anchor=True,
            bootstrap=True,
        )
        path = self.artifacts / "fast_control_actions" / "recovery.json"
        journal = FastActionRecoveryJournal(
            path=path,
            enabled=True,
            wall_time=self.clock.wall_time,
            authenticity=bootstrap,
        )
        with patch.object(
            ContinuityAuthenticity,
            "commit_external_anchor",
            side_effect=ContinuityAuthenticityError(
                "continuity_anchor_unavailable"
            ),
        ):
            with self.assertRaises(ContinuityAuthenticityError):
                journal.begin("fast-action-1")
        self.assertEqual(
            journal.public_status()["state"],
            "auth_error",
        )

        restored = FastActionRecoveryJournal(
            path=path,
            enabled=True,
            wall_time=self.clock.wall_time,
            authenticity=self.authenticity(anchor=True),
        )
        self.assertEqual(restored.public_status()["state"], "pending")
        self.assertEqual(restored.public_status()["pendingCount"], 1)
        self.assertTrue(
            restored.public_status()["externalReplayProtected"]
        )

    def test_signed_guild_revocations_reject_rewrite(
        self,
    ) -> None:
        authenticity = self.authenticity()
        manager = self.manager(
            store=self.store(),
            authenticity=authenticity,
        )
        manager.flush()
        manager._write_guild_revocations({7: 1000.0})
        ledger_path = self.owner_root / "guild_revocations.json"
        ledger = json.loads(
            ledger_path.read_text(encoding="utf-8")
        )
        self.assertEqual(
            ledger["schema"],
            SESSION_CONTINUITY_AUTHENTICATED_REVOCATIONS_SCHEMA,
        )
        self.assertTrue(
            manager.status()["guildRevocationsTamperEvident"]
        )
        ledger["guilds"]["7"] = 1001.0
        ledger_path.write_text(
            json.dumps(ledger, ensure_ascii=False),
            encoding="utf-8",
        )
        malicious_ledger = ledger_path.read_bytes()

        restored = self.manager(
            authenticity=authenticity,
        ).restore()
        cross = read_verified_continuity_snapshot(
            self.owner_root,
            source="main",
            wall_time=self.clock.wall_time,
            guild_id=7,
            user_id=9,
            authenticity=authenticity,
        )

        self.assertEqual(restored["state"], "error")
        self.assertEqual(
            restored["lastErrorCode"],
            "continuity_auth_failed",
        )
        self.assertTrue((self.owner_root / "active.json").exists())
        self.assertEqual(ledger_path.read_bytes(), malicious_ledger)
        self.assertEqual(cross.state, "rejected")

    def test_unsigned_guild_revocations_require_bootstrap(
        self,
    ) -> None:
        unsigned = self.manager()
        unsigned._write_guild_revocations({7: 1000.0})
        ledger_path = self.owner_root / "guild_revocations.json"
        before = ledger_path.read_bytes()

        rejected = self.manager(
            authenticity=self.authenticity(),
        )
        with self.assertRaises(ContinuityAuthenticityError):
            rejected._load_guild_revocations()
        self.assertEqual(ledger_path.read_bytes(), before)

        adopted = self.manager(
            authenticity=self.authenticity(bootstrap=True),
        )
        self.assertEqual(
            adopted._load_guild_revocations(),
            {7: 1000.0},
        )
        signed = json.loads(
            ledger_path.read_text(encoding="utf-8")
        )
        self.assertEqual(
            signed["schema"],
            SESSION_CONTINUITY_AUTHENTICATED_REVOCATIONS_SCHEMA,
        )
        self.assertEqual(
            adopted.status()["guildRevocationsAuthenticity"],
            "verified",
        )
        without_key = self.manager()
        with self.assertRaises(ContinuityAuthenticityError) as caught:
            without_key._load_guild_revocations()
        self.assertEqual(
            caught.exception.code,
            "continuity_auth_key_required",
        )

    def test_external_anchor_rejects_revocation_replay_and_deletion(
        self,
    ) -> None:
        bootstrap = self.authenticity(
            anchor=True,
            bootstrap=True,
        )
        manager = self.manager(
            store=self.store(),
            authenticity=bootstrap,
        )
        manager.flush(force=True)
        self.assertEqual(manager._load_guild_revocations(), {})
        manager._write_guild_revocations({7: 1000.0})
        ledger_path = self.owner_root / "guild_revocations.json"
        old_ledger = ledger_path.read_bytes()
        anchor_path = self.anchor_root / (
            "conversation-continuity-guild-revocations.json"
        )
        lagging_anchor = anchor_path.read_bytes()
        manager._write_guild_revocations({7: 1001.0})
        current = json.loads(ledger_path.read_text(encoding="utf-8"))
        self.assertEqual(
            current["schema"],
            SESSION_CONTINUITY_ANCHORED_REVOCATIONS_SCHEMA,
        )
        self.assertEqual(current["generation"], 2)
        anchor_path.write_bytes(lagging_anchor)
        recovered = self.manager(
            authenticity=self.authenticity(anchor=True)
        )
        self.assertEqual(
            recovered._load_guild_revocations(),
            {7: 1001.0},
        )

        ledger_path.write_bytes(old_ledger)
        replay = self.manager(
            authenticity=self.authenticity(anchor=True)
        )
        with self.assertRaises(ContinuityAuthenticityError) as caught:
            replay._load_guild_revocations()
        self.assertEqual(
            caught.exception.code,
            "continuity_anchor_replay_detected",
        )
        self.assertEqual(
            replay.status()["guildRevocationsAnchorState"],
            "replay_detected",
        )
        cross = read_verified_continuity_snapshot(
            self.owner_root,
            source="main",
            wall_time=self.clock.wall_time,
            guild_id=7,
            user_id=9,
            authenticity=self.authenticity(anchor=True),
        )
        self.assertEqual(cross.state, "rejected")

        ledger_path.unlink()
        deleted = self.manager(
            authenticity=self.authenticity(anchor=True)
        )
        with self.assertRaises(ContinuityAuthenticityError) as caught:
            deleted._load_guild_revocations()
        self.assertEqual(
            caught.exception.code,
            "continuity_anchor_replay_detected",
        )
        self.assertFalse(ledger_path.exists())
        self.assertEqual(
            bootstrap.external_anchor_position(
                CONTINUITY_AUTH_ANCHOR_SLOT_GUILD_REVOCATIONS
            )[0],
            2,
        )

    def test_rewritten_checkpoint_and_head_fail_without_deletion(
        self,
    ) -> None:
        authenticity = self.authenticity()
        self.manager(
            store=self.store(),
            authenticity=authenticity,
        ).flush()
        checkpoint_path = self.owner_root / "active.json"
        head_path = self.owner_root / "checkpoint_head.json"
        checkpoint = json.loads(
            checkpoint_path.read_text(encoding="utf-8")
        )
        checkpoint["sessions"][0]["history"][1]["content"] = (
            "관리자가 바꾼 거짓 답변"
        )
        checkpoint["checkpointHash"] = _checkpoint_hash(checkpoint)
        checkpoint_path.write_text(
            json.dumps(checkpoint, ensure_ascii=False),
            encoding="utf-8",
        )
        head = json.loads(head_path.read_text(encoding="utf-8"))
        head["checkpointHash"] = checkpoint["checkpointHash"]
        head_path.write_text(
            json.dumps(head, ensure_ascii=False),
            encoding="utf-8",
        )
        malicious_checkpoint = checkpoint_path.read_bytes()
        malicious_head = head_path.read_bytes()

        status = self.manager(
            authenticity=authenticity,
        ).restore()

        self.assertEqual(status["state"], "error")
        self.assertEqual(
            status["lastErrorCode"],
            "continuity_auth_failed",
        )
        self.assertEqual(
            status["checkpointHeadAuthenticity"],
            "failed",
        )
        self.assertEqual(
            checkpoint_path.read_bytes(),
            malicious_checkpoint,
        )
        self.assertEqual(head_path.read_bytes(), malicious_head)

    def test_unsigned_state_requires_explicit_bootstrap(self) -> None:
        unsigned = self.manager(store=self.store()).flush()
        self.assertEqual(unsigned["state"], "ready")
        head_path = self.owner_root / "checkpoint_head.json"
        self.assertEqual(
            json.loads(head_path.read_text(encoding="utf-8"))[
                "schema"
            ],
            CONTINUITY_HEAD_SCHEMA_V1,
        )
        before = head_path.read_bytes()

        rejected = self.manager(
            authenticity=self.authenticity(),
        ).restore()
        self.assertEqual(rejected["state"], "error")
        self.assertEqual(
            rejected["lastErrorCode"],
            "continuity_auth_bootstrap_required",
        )
        self.assertEqual(head_path.read_bytes(), before)

        restored = self.manager(
            authenticity=self.authenticity(bootstrap=True),
        ).restore()
        signed_head = json.loads(
            head_path.read_text(encoding="utf-8")
        )
        self.assertEqual(restored["state"], "restored")
        self.assertTrue(restored["tamperEvident"])
        self.assertEqual(
            signed_head["schema"],
            CONTINUITY_HEAD_SCHEMA_V2,
        )

    def test_signed_state_without_key_is_preserved_and_rejected(
        self,
    ) -> None:
        self.manager(
            store=self.store(),
            authenticity=self.authenticity(),
        ).flush()
        checkpoint_path = self.owner_root / "active.json"
        head_path = self.owner_root / "checkpoint_head.json"
        before_checkpoint = checkpoint_path.read_bytes()
        before_head = head_path.read_bytes()

        status = self.manager().restore()

        self.assertEqual(status["state"], "error")
        self.assertEqual(
            status["lastErrorCode"],
            "continuity_auth_key_required",
        )
        self.assertEqual(checkpoint_path.read_bytes(), before_checkpoint)
        self.assertEqual(head_path.read_bytes(), before_head)


if __name__ == "__main__":
    unittest.main()
