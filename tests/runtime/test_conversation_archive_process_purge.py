from __future__ import annotations

import sys
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock


REPO_ROOT = next(
    path for path in Path(__file__).resolve().parents if (path / "main.py").exists()
)
RUNTIME_ROOT = REPO_ROOT / "evelyn_core" / "runtime"
if str(RUNTIME_ROOT) not in sys.path:
    sys.path.insert(0, str(RUNTIME_ROOT))

from evelyn_core.conversation_archive_process_purge import (  # noqa: E402
    ConversationArchiveProcessPurgeError,
    ConversationArchiveProcessPurgeFence,
    ConversationArchiveProcessPurgeRunner,
    conversation_archive_process_target_values,
    purge_exact_process_caches,
)
from evelyn_core.conversation_archive_process_composition import (  # noqa: E402
    ConversationArchiveProcessComposition,
)
from evelyn_core.discord_conversation_archive_runtime import (  # noqa: E402
    DiscordConversationArchiveClient,
)


async def _unused_session():
    raise AssertionError("network must not be used")


class ConversationArchiveProcessPurgeFenceTests(unittest.TestCase):
    def setUp(self) -> None:
        self.client = DiscordConversationArchiveClient(
            base_url="http://127.0.0.1:8000",
            master_key=b"ingest-master-key-material-32-bytes-minimum",
            user_view_master_key=(
                b"user-view-master-key-material-32-bytes-minimum"
            ),
            get_http_session=_unused_session,
        )
        self.fence = ConversationArchiveProcessPurgeFence(
            lineage_handle=self.client.purge_lineage_handle,
        )

    def test_voice_task_target_uses_exact_member_guild(self) -> None:
        class Guild:
            id = 321

        class Member:
            guild = Guild()

        values = conversation_archive_process_target_values(
            {
                "member": Member(),
                "turn_id": "turn-one",
                "session_key": "session-one",
                "person_key": "user:7",
            }
        )

        self.assertEqual(values["guild_id"], 321)
        self.assertEqual(values["turn_id"], "turn-one")
        self.assertEqual(values["session_key"], "session-one")
        self.assertEqual(values["person_key"], "user:7")

    def test_process_cache_purge_is_exact_and_flags_unattributed_rows(self) -> None:
        speculative = {"target-session": {"text": "private"}, "other": {}}
        questions = {"target-session": {"turns": [1]}, "global": {}}
        dispatches = {"targeted": 1.0, "unattributed": 2.0, "other": 3.0}
        targets = {
            "targeted": {"turn_id": "target-turn"},
            "other": {"turn_id": "other-turn"},
        }

        result = purge_exact_process_caches(
            session_caches=(speculative, questions),
            targeted_cache=dispatches,
            target_metadata=targets,
            session_matches=lambda key: key == "target-session",
            target_matches=lambda target: target.get("turn_id") == "target-turn",
            unattributed_session_keys=("global",),
        )

        self.assertEqual(result, (3, 0, 2))
        self.assertEqual(speculative, {"other": {}})
        self.assertEqual(questions, {"global": {}})
        self.assertEqual(dispatches, {"unattributed": 2.0, "other": 3.0})
        self.assertEqual(targets, {"other": {"turn_id": "other-turn"}})

    def test_feedback_state_purge_includes_ephemeral_source_targets(self) -> None:
        cleanup_identity = Mock(return_value=(2, 0, 0))

        def purge_feedback_targets(target_matches):
            self.assertTrue(
                target_matches(
                    {
                        "guild_id": 7,
                        "turn_id": "turn-one",
                        "session_key": "session-one",
                    }
                )
            )
            return (1, 0, 0)

        composition = ConversationArchiveProcessComposition(enabled=True)
        composition._deps = SimpleNamespace(
            cleanup_identity_review_artifacts=cleanup_identity,
            purge_feedback_targets=purge_feedback_targets,
            identity_review_export_dir=Path("identity-review"),
            runtime_artifacts_root=Path("runtime-artifacts"),
        )
        composition._fence = SimpleNamespace(
            matches=lambda _work, lineage: lineage.get("turn") == ("turn-one",)
        )

        result = composition._purge_feedback_and_exports(
            {"scopeAll": True, "lineageHandles": []}
        )

        self.assertEqual(result, (3, 0, 0))
        cleanup_identity.assert_called_once()

    def work(self, *, digest: str = "a" * 64, complete: bool = True):
        return {
            "requestId": "request-one",
            "scopeDigest": digest,
            "lineageComplete": complete,
            "lineageHandles": [
                {
                    "kind": "turn",
                    "digest": self.client.purge_lineage_handle(
                        "turn", "turn-one"
                    ),
                },
                {
                    "kind": "session",
                    "digest": self.client.purge_lineage_handle(
                        "session", "session-one"
                    ),
                },
            ],
        }

    def test_freeze_and_retire_keep_matching_writers_blocked(self) -> None:
        work = self.work()
        matching = {
            "turn": ("turn-one",),
            "session": ("session-one",),
        }
        unrelated = {"turn": ("turn-two",)}

        self.assertTrue(self.fence.target_is_current(matching))
        self.fence.freeze(work)
        self.fence.freeze(work)

        self.assertTrue(self.fence.matches(work, matching))
        self.assertFalse(self.fence.target_is_current(matching))
        self.assertTrue(self.fence.target_is_current(unrelated))
        self.assertEqual(self.fence.snapshot().frozen_requests, 1)

        self.fence.retire(work)

        snapshot = self.fence.snapshot()
        self.assertEqual(snapshot.frozen_requests, 0)
        self.assertEqual(snapshot.retired_handles, 2)
        self.assertFalse(self.fence.target_is_current(matching))

    def test_changed_work_order_and_invalid_lineage_fail_closed(self) -> None:
        self.fence.freeze(self.work())
        with self.assertRaises(ConversationArchiveProcessPurgeError) as changed:
            self.fence.freeze(self.work(digest="b" * 64))
        self.assertEqual(
            changed.exception.code,
            "archive_process_purge_work_changed",
        )
        self.assertFalse(self.fence.target_is_current({}))
        self.assertFalse(
            self.fence.target_is_current({"turn": ("",)})
        )

    def test_only_complete_nonempty_lineage_is_exact(self) -> None:
        self.assertTrue(self.fence.work_is_exact(self.work()))
        self.assertFalse(
            self.fence.work_is_exact(self.work(complete=False))
        )
        empty = self.work()
        empty["lineageHandles"] = []
        self.assertFalse(self.fence.work_is_exact(empty))

    def test_release_completed_retires_the_exact_fence(self) -> None:
        work = self.work()
        matching = {"turn": ("turn-one",)}
        self.fence.freeze(work)

        self.fence.release_completed(work)

        self.assertFalse(self.fence.target_is_current(matching))
        self.assertEqual(self.fence.snapshot().frozen_requests, 0)
        self.assertEqual(self.fence.snapshot().retired_handles, 2)


class ConversationArchiveProcessPurgeRunnerTests(
    unittest.IsolatedAsyncioTestCase
):
    async def asyncSetUp(self) -> None:
        self.client = DiscordConversationArchiveClient(
            base_url="http://127.0.0.1:8000",
            master_key=b"ingest-master-key-material-32-bytes-minimum",
            user_view_master_key=(
                b"user-view-master-key-material-32-bytes-minimum"
            ),
            get_http_session=_unused_session,
        )
        self.fence = ConversationArchiveProcessPurgeFence(
            lineage_handle=self.client.purge_lineage_handle,
        )

    def work(self, *, complete: bool = True) -> dict:
        return {
            "requestId": "request-runner",
            "scopeDigest": "c" * 64,
            "lineageComplete": complete,
            "lineageHandles": [
                {
                    "kind": "turn",
                    "digest": self.client.purge_lineage_handle(
                        "turn", "turn-runner"
                    ),
                }
            ],
            "remainingSinks": ["continuity", "stt_buffer"],
        }

    async def test_ackable_only_after_every_owner_proves_zero(self) -> None:
        calls: list[str] = []

        async def stt_owner(_work):
            calls.append("stt_buffer")
            return (2, 0, 0)

        runner = ConversationArchiveProcessPurgeRunner(
            fence=self.fence,
            owners={
                "continuity": lambda _work: (1, 0, 0),
                "stt_buffer": stt_owner,
            },
        )

        ackable = await runner.purge(self.work())

        self.assertEqual(ackable, ("continuity", "stt_buffer"))
        self.assertEqual(calls, ["stt_buffer"])
        self.assertFalse(
            self.fence.target_is_current({"turn": ("turn-runner",)})
        )
        runner.release_completed(self.work())
        self.assertFalse(
            self.fence.target_is_current({"turn": ("turn-runner",)})
        )
        self.assertEqual(self.fence.snapshot().retired_handles, 1)

    async def test_one_manual_owner_acks_nothing_and_keeps_fence(self) -> None:
        runner = ConversationArchiveProcessPurgeRunner(
            fence=self.fence,
            owners={
                "continuity": lambda _work: (0, 0, 0),
                "stt_buffer": lambda _work: (0, 0, 1),
            },
        )

        self.assertEqual(await runner.purge(self.work()), ())
        self.assertFalse(
            self.fence.target_is_current({"turn": ("turn-runner",)})
        )

    async def test_incomplete_lineage_calls_no_owner(self) -> None:
        calls = 0

        def owner(_work):
            nonlocal calls
            calls += 1
            return (0, 0, 0)

        runner = ConversationArchiveProcessPurgeRunner(
            fence=self.fence,
            owners={"continuity": owner, "stt_buffer": owner},
        )

        self.assertEqual(
            await runner.purge(self.work(complete=False)),
            (),
        )
        self.assertEqual(calls, 0)


if __name__ == "__main__":
    unittest.main()
