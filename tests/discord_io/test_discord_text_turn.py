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

from evelyn_core.discord_text_turn import DiscordTextMessageHandlerDeps, handle_discord_text_message  # noqa: E402
from tests.continuity_test_support import (  # noqa: E402
    durable_continuity_status,
)


class AsyncTyping:
    async def __aenter__(self):
        return self

    async def __aexit__(self, exc_type, exc, tb):
        return False


class FakeChannel:
    def __init__(self, channel_id: int = 2) -> None:
        self.id = channel_id
        self.sent: list[str] = []
        self.typing_count = 0

    def typing(self) -> AsyncTyping:
        self.typing_count += 1
        return AsyncTyping()

    async def send(self, text: str) -> None:
        self.sent.append(text)


def make_message(
    *,
    content: str = "Evelyn hi",
    guild: object | None = None,
    channel: FakeChannel | None = None,
    author: object | None = None,
    message_id: int = 99,
):
    return SimpleNamespace(
        id=message_id,
        content=content,
        guild=guild,
        channel=channel or FakeChannel(),
        author=author or SimpleNamespace(id=3, bot=False, display_name="정훈"),
        attachments=[],
        reference=None,
    )


def make_deps(calls: list[tuple[str, object]], **overrides) -> DiscordTextMessageHandlerDeps:
    async def process_commands(message) -> None:
        calls.append(("process_commands", getattr(message, "content", "")))

    async def stream_text_reply(*args, **kwargs):
        calls.append(("stream", kwargs["user_text"] if "user_text" in kwargs else args[1]))
        return "<voice>answer</voice>", None, {"meta": {}}, None

    async def commit_session_continuity():
        calls.append(("commit_continuity", None))
        return durable_continuity_status(5)

    deps = dict(
        process_commands=process_commands,
        bot_user=SimpleNamespace(id=10),
        is_thread_parent=lambda _parent: True,
        remember_session_followup_target=lambda session_key, **kwargs: calls.append(("remember", (session_key, kwargs))),
        get_guild_command_prefix=lambda _guild_id: "!",
        get_guild_command_only_channel_ids=lambda _guild_id: set(),
        contains_wake_word=lambda text: str(text).lower().startswith("evelyn"),
        is_session_active_for_user=lambda _session_key, _user_id: False,
        strip_voice_wake_word=lambda text: str(text).replace("Evelyn", "", 1).strip(),
        empty_wake_text="empty wake",
        log_turn_event=lambda *args, **kwargs: calls.append(("log_turn", kwargs.get("reason"))),
        current_turn_id=lambda session_key: f"current:{session_key}",
        resolve_pending_proactive_question_for_turn=lambda *args, **kwargs: {"resolved": False},
        session_locks={},
        reply_slot_locks={},
        begin_user_text_turn=lambda session_key, user_text, **kwargs: SimpleNamespace(
            topic_id=f"topic:{user_text}",
            turn_id=f"turn:{session_key}",
        ),
        replace_room_turn_scope=lambda session_key, turn_scope: calls.append(("replace_scope", session_key)),
        attach_current_task=lambda turn_scope: "task",
        auto_join_voice=False,
        ensure_voice_client=lambda message: None,
        stream_text_reply=stream_text_reply,
        strip_omnivoice_tags=lambda text: text.replace("<voice>", "").replace("</voice>", ""),
        execute_voice_delivery_plan=lambda *args, **kwargs: None,
        detach_task=lambda turn_scope, task: calls.append(("detach", task)),
        clear_room_turn_scope=lambda session_key, turn_scope: calls.append(("clear_scope", session_key)),
        session_speculative_policies={},
        compute_runtime_mode=lambda metrics: "normal",
        record_context_pipeline_benchmark=lambda **kwargs: calls.append(("benchmark", kwargs["answer"])),
        schedule_memory_update=lambda *args, **kwargs: {"scheduled": True},
        should_force_search_followup=lambda *args, **kwargs: False,
        schedule_search_followup=lambda *args, **kwargs: calls.append(("search_followup", kwargs["source"])),
        session_state_snapshot=lambda session_key: {"awaiting_user_reply": False},
        finish_assistant_text_turn=lambda session_key, user_text, answer, **kwargs: calls.append(
            ("finish", (session_key, user_text, answer, kwargs["topic_id"]))
        ),
        commit_session_continuity=commit_session_continuity,
        log_voice_bottleneck_summary=lambda metrics, **kwargs: calls.append(("summary", kwargs["event_name"])),
        format_display_text=lambda text, **kwargs: text,
        log=lambda *args: calls.append(("log", args)),
    )
    deps.update(overrides)
    return DiscordTextMessageHandlerDeps(**deps)


class DiscordTextTurnHandlerTests(unittest.TestCase):
    def test_handler_ignores_bot_and_processes_dm_commands(self) -> None:
        calls: list[tuple[str, object]] = []
        deps = make_deps(calls)

        bot_message = make_message(author=SimpleNamespace(id=3, bot=True, display_name="Bot"))
        dm_message = make_message(guild=None)

        asyncio.run(handle_discord_text_message(bot_message, deps))
        asyncio.run(handle_discord_text_message(dm_message, deps))

        self.assertEqual(calls, [("process_commands", "Evelyn hi")])

    def test_handler_routes_prefixed_guild_message_to_commands(self) -> None:
        calls: list[tuple[str, object]] = []
        deps = make_deps(calls)
        message = make_message(content="!status", guild=SimpleNamespace(id=1, name="Guild"))

        asyncio.run(handle_discord_text_message(message, deps))

        self.assertEqual(calls[0][0], "remember")
        self.assertEqual(calls[-1], ("process_commands", "!status"))

    def test_handler_runs_full_text_turn_and_records_side_effects(self) -> None:
        calls: list[tuple[str, object]] = []
        deps = make_deps(calls)
        message = make_message(guild=SimpleNamespace(id=1, name="Guild"))

        asyncio.run(handle_discord_text_message(message, deps))

        self.assertIn(("stream", "hi"), calls)
        self.assertIn(("benchmark", "answer"), calls)
        self.assertIn(("search_followup", "search-followup-text"), calls)
        self.assertIn(
            (
                "finish",
                ("guild:1:text:2:user:3", "hi", "answer", "topic:hi"),
            ),
            calls,
        )
        self.assertIn(("commit_continuity", None), calls)
        self.assertIn(("summary", "text_turn_summary"), calls)
        self.assertEqual(calls[-1], ("process_commands", "Evelyn hi"))
        self.assertEqual(message.channel.typing_count, 1)

    def test_delivered_text_turn_is_committed_when_optional_voice_fails(self) -> None:
        calls: list[tuple[str, object]] = []

        async def stream_text_reply(*args, **kwargs):
            calls.append(("stream", args[1]))
            return (
                "<voice>answer</voice>",
                SimpleNamespace(id=77),
                {"meta": {}},
                SimpleNamespace(should_play_voice=True),
            )

        async def fail_voice(*args, **kwargs):
            calls.append(("voice", kwargs["turn_id"]))
            raise RuntimeError(
                "Bearer discord-secret C:\\Users\\Admin\\private.txt"
            )

        async def ensure_voice_client(_message):
            return SimpleNamespace(is_connected=lambda: True)

        deps = make_deps(
            calls,
            auto_join_voice=True,
            ensure_voice_client=ensure_voice_client,
            stream_text_reply=stream_text_reply,
            execute_voice_delivery_plan=fail_voice,
        )
        message = make_message(
            guild=SimpleNamespace(id=1, name="Guild")
        )

        asyncio.run(handle_discord_text_message(message, deps))

        finish_indexes = [
            index
            for index, call in enumerate(calls)
            if call[0] == "finish"
        ]
        voice_index = next(
            index
            for index, call in enumerate(calls)
            if call[0] == "voice"
        )
        commit_index = next(
            index
            for index, call in enumerate(calls)
            if call[0] == "commit_continuity"
        )
        self.assertEqual(len(finish_indexes), 1)
        self.assertLess(finish_indexes[0], commit_index)
        self.assertLess(commit_index, voice_index)
        self.assertEqual(message.channel.sent, [])
        self.assertNotIn(
            "discord-secret",
            " ".join(
                str(call)
                for call in calls
                if call[0] != "log"
            ),
        )
        self.assertIn(("summary", "text_turn_summary"), calls)
        self.assertEqual(
            calls[-1],
            ("process_commands", "Evelyn hi"),
        )

    def test_partial_commit_status_is_not_marked_durable(
        self,
    ) -> None:
        calls: list[tuple[str, object]] = []
        summaries: list[dict] = []
        private = (
            "Bearer text-continuity-secret "
            "https://internal.example/private"
        )

        async def partial_commit():
            calls.append(("commit_continuity", None))
            return {
                "state": "ready",
                "rollbackProtected": True,
                "privateMessage": private,
            }

        deps = make_deps(
            calls,
            commit_session_continuity=partial_commit,
            log_voice_bottleneck_summary=(
                lambda metrics, **_kwargs: summaries.append(
                    metrics
                )
            ),
        )
        message = make_message(
            guild=SimpleNamespace(id=1, name="Guild")
        )

        asyncio.run(handle_discord_text_message(message, deps))

        self.assertEqual(
            summaries[0]["meta"]["continuity_commit"],
            "failed",
        )
        self.assertEqual(
            summaries[0]["meta"]["continuity_error"],
            "conversation_continuity_commit_failed",
        )
        self.assertNotIn(private, str(summaries))

    def test_pre_delivery_failure_returns_fixed_public_message(self) -> None:
        calls: list[tuple[str, object]] = []

        async def fail_stream(*args, **kwargs):
            raise RuntimeError(
                "token=discord-secret http://internal:9820 C:\\private"
            )

        deps = make_deps(calls, stream_text_reply=fail_stream)
        message = make_message(
            guild=SimpleNamespace(id=1, name="Guild")
        )

        asyncio.run(handle_discord_text_message(message, deps))

        self.assertEqual(
            message.channel.sent,
            [
                "❌ 응답을 전달하지 못했어. 잠깐 뒤에 다시 시도해줘. "
                "(text_turn_failed)"
            ],
        )
        self.assertNotIn("discord-secret", message.channel.sent[0])
        self.assertFalse(
            any(call[0] == "finish" for call in calls)
        )


if __name__ == "__main__":
    unittest.main()
