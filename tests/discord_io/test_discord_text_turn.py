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
        self.assertIn(("summary", "text_turn_summary"), calls)
        self.assertEqual(calls[-1], ("process_commands", "Evelyn hi"))
        self.assertEqual(message.channel.typing_count, 1)


if __name__ == "__main__":
    unittest.main()
