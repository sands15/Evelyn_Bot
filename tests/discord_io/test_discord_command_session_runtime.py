import unittest
import sys
import asyncio
from pathlib import Path
from types import SimpleNamespace

REPO_ROOT = Path(__file__).resolve().parents[2]
RUNTIME_ROOT = REPO_ROOT / "evelyn_core" / "runtime"
if str(RUNTIME_ROOT) not in sys.path:
    sys.path.insert(0, str(RUNTIME_ROOT))

from evelyn_core.discord_command_session_runtime import (
    ContinuityRecordingCommandContext,
    DiscordCommandSessionRuntimeDeps,
    mark_text_session_from_command_runtime,
)
from tests.continuity_test_support import (
    durable_continuity_status,
)


class DiscordCommandSessionRuntimeTests(unittest.TestCase):
    def test_recording_context_commits_only_after_successful_text_delivery(
        self,
    ) -> None:
        order: list[object] = []
        delivered = object()

        class Context:
            guild = SimpleNamespace(id=1)
            message = SimpleNamespace(content="!상태")

            async def send(self, content=None, *args, **kwargs):
                order.append(("send", content, args, kwargs))
                return delivered

        original = Context()
        wrapped = ContinuityRecordingCommandContext(
            original,
            record_reply=(
                lambda ctx, user, answer: order.append(
                    ("record", ctx, user, answer)
                )
            ),
            log=lambda *_args, **_kwargs: None,
        )

        result = asyncio.run(wrapped.send("정상", silent=True))

        self.assertIs(result, delivered)
        self.assertEqual(order[0], ("send", "정상", (), {"silent": True}))
        self.assertEqual(
            order[1],
            ("record", original, "!상태", "정상"),
        )

    def test_recording_context_does_not_commit_failed_or_non_text_delivery(
        self,
    ) -> None:
        records: list[object] = []

        class Context:
            message = SimpleNamespace(content="!상태")

            async def send(self, content=None, *args, **kwargs):
                if content == "실패":
                    raise RuntimeError("delivery_failed")
                return "sent"

        wrapped = ContinuityRecordingCommandContext(
            Context(),
            record_reply=lambda *args: records.append(args),
            log=lambda *_args, **_kwargs: None,
        )

        self.assertEqual(asyncio.run(wrapped.send(None)), "sent")
        with self.assertRaisesRegex(RuntimeError, "delivery_failed"):
            asyncio.run(wrapped.send("실패"))
        self.assertEqual(records, [])

    def test_recording_context_contains_record_failure_after_delivery(
        self,
    ) -> None:
        logs: list[tuple[object, ...]] = []

        class Context:
            message = SimpleNamespace(content="")

            async def send(self, content=None):
                return "sent"

        wrapped = ContinuityRecordingCommandContext(
            Context(),
            record_reply=lambda *_args: (_ for _ in ()).throw(
                RuntimeError("private")
            ),
            log=lambda *args, **_kwargs: logs.append(args),
        )

        self.assertEqual(asyncio.run(wrapped.send("정상")), "sent")
        self.assertIn(
            "command_continuity_record_failed",
            str(logs),
        )
        self.assertIn("RuntimeError", str(logs))
        self.assertNotIn("private", str(logs))

    def test_mark_text_session_from_command_records_turn_with_message_context(self) -> None:
        calls: list[tuple] = []
        commits: list[tuple[object, ...]] = []
        thread_checks: list[object] = []

        def resolve_text_thread_id(channel, *, is_thread_parent):
            thread_checks.append(is_thread_parent(channel.parent))
            return 77

        def make_text_session_key(guild_id, channel_id, user_id, *, thread_id=None):
            return f"{guild_id}:{channel_id}:{user_id}:{thread_id}"

        deps = DiscordCommandSessionRuntimeDeps(
            resolve_text_thread_id=resolve_text_thread_id,
            is_text_thread_parent=lambda parent: getattr(parent, "is_text_channel", False),
            make_text_session_key=make_text_session_key,
            start_new_turn=lambda session_key: (
                f"command-turn:{session_key}"
            ),
            record_command_assistant_turn=lambda *args, **kwargs: calls.append((args, kwargs)),
            system_prompt="system",
            max_history_items=12,
            normal_ttl_sec=30.0,
            question_ttl_sec=45.0,
            commit_session_continuity=lambda *args: (
                commits.append(args)
                or durable_continuity_status(3)
            ),
            log=lambda *args, **kwargs: None,
        )
        ctx = SimpleNamespace(
            guild=SimpleNamespace(id=1),
            channel=SimpleNamespace(id=2, parent=SimpleNamespace(is_text_channel=True)),
            author=SimpleNamespace(id=3),
            message=SimpleNamespace(id=4),
        )

        mark_text_session_from_command_runtime(
            ctx,
            "user",
            "answer",
            awaiting_user_reply=True,
            deps=deps,
        )

        self.assertEqual(thread_checks, [True])
        self.assertEqual(len(calls), 1)
        self.assertEqual(
            commits,
            [("1:2:3:77", "command-turn:1:2:3:77")],
        )
        args, kwargs = calls[0]
        self.assertEqual(args, ("1:2:3:77", "user", "answer"))
        self.assertEqual(
            kwargs,
            {
                "system_prompt": "system",
                "max_history_items": 12,
                "guild_id": 1,
                "user_id": 3,
                "channel_id": 2,
                "message_id": 4,
                "awaiting_user_reply": True,
                "normal_ttl_sec": 30.0,
                "question_ttl_sec": 45.0,
            },
        )

    def test_mark_text_session_from_command_ignores_dm_context(self) -> None:
        calls: list[object] = []
        deps = DiscordCommandSessionRuntimeDeps(
            resolve_text_thread_id=lambda *args, **kwargs: 1,
            is_text_thread_parent=lambda parent: True,
            make_text_session_key=lambda *args, **kwargs: "session",
            start_new_turn=lambda session_key: f"turn:{session_key}",
            record_command_assistant_turn=lambda *args, **kwargs: calls.append((args, kwargs)),
            system_prompt="system",
            max_history_items=12,
            normal_ttl_sec=30.0,
            question_ttl_sec=45.0,
            commit_session_continuity=(
                lambda *_args: durable_continuity_status(1)
            ),
            log=lambda *args, **kwargs: None,
        )

        mark_text_session_from_command_runtime(
            SimpleNamespace(guild=None),
            "user",
            "answer",
            deps=deps,
        )

        self.assertEqual(calls, [])

    def test_partial_commit_status_is_logged_as_fixed_failure(
        self,
    ) -> None:
        logs: list[tuple] = []
        private = (
            "Bearer command-continuity-secret "
            r"C:\Users\Admin\checkpoint.json"
        )
        deps = DiscordCommandSessionRuntimeDeps(
            resolve_text_thread_id=lambda *_args, **_kwargs: None,
            is_text_thread_parent=lambda _parent: False,
            make_text_session_key=(
                lambda *_args, **_kwargs: "session"
            ),
            start_new_turn=(
                lambda session_key: f"turn:{session_key}"
            ),
            record_command_assistant_turn=(
                lambda *_args, **_kwargs: None
            ),
            system_prompt="system",
            max_history_items=12,
            normal_ttl_sec=30.0,
            question_ttl_sec=45.0,
            commit_session_continuity=lambda *_args: {
                "state": "ready",
                "privateMessage": private,
            },
            log=lambda *args, **_kwargs: logs.append(args),
        )
        ctx = SimpleNamespace(
            guild=SimpleNamespace(id=1),
            channel=SimpleNamespace(id=2),
            author=SimpleNamespace(id=3),
            message=SimpleNamespace(id=4),
        )

        mark_text_session_from_command_runtime(
            ctx,
            "user",
            "answer",
            deps=deps,
        )

        rendered = str(logs)
        self.assertIn(
            "ConversationContinuityCommitError",
            rendered,
        )
        self.assertNotIn("command-continuity-secret", rendered)
        self.assertNotIn("Users", rendered)


if __name__ == "__main__":
    unittest.main()
