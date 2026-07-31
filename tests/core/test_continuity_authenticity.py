from __future__ import annotations

import json
import sys
import tempfile
import unittest
from pathlib import Path


REPO_ROOT = next(
    path
    for path in Path(__file__).resolve().parents
    if (path / "main.py").exists()
)
RUNTIME_ROOT = REPO_ROOT / "evelyn_core" / "runtime"
if str(RUNTIME_ROOT) not in sys.path:
    sys.path.insert(0, str(RUNTIME_ROOT))

from evelyn_core.continuity_authenticity import (  # noqa: E402
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
from evelyn_core.session_continuity import (  # noqa: E402
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
        self.clock = FakeClock()

    def authenticity(
        self,
        *,
        bootstrap: bool = False,
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
