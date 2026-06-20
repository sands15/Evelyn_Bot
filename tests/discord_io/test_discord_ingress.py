import sys
import asyncio
import unittest
from pathlib import Path


REPO_ROOT = next(path for path in Path(__file__).resolve().parents if (path / "main.py").exists())
RUNTIME_ROOT = REPO_ROOT / "evelyn_core" / "runtime"
if str(RUNTIME_ROOT) not in sys.path:
    sys.path.insert(0, str(RUNTIME_ROOT))

from evelyn_core.discord_ingress import (  # noqa: E402
    build_discord_attachment_context,
    build_text_ingress_context,
    build_text_ingress_context_from_message,
    build_text_turn_decision,
    build_text_turn_user_text,
    build_voice_ingress_context,
    decide_text_message_precheck,
    is_reply_to_target_user,
    make_person_memory_key,
    make_room_memory_key,
    make_session_memory_key,
    make_text_reply_slot_key,
    make_text_session_key,
    make_voice_room_session_key,
    make_voice_session_key,
    normalize_voice_debug_meta,
    resolve_text_thread_id,
    should_accept_text_turn,
    voice_ingress_source,
)


class DiscordIngressTests(unittest.TestCase):
    def test_text_key_helpers_preserve_current_shapes(self) -> None:
        self.assertEqual(make_text_session_key(1, 2, 3), "guild:1:text:2:user:3")
        self.assertEqual(make_text_session_key(1, 2, 3, thread_id=4), "guild:1:text:2:thread:4:user:3")
        self.assertEqual(make_text_reply_slot_key(1, 2, thread_id=4), "guild:1:reply:text:2:thread:4")
        self.assertEqual(make_room_memory_key("text", 2), "text:2")
        self.assertEqual(make_room_memory_key("text", None), "text:none")
        self.assertEqual(make_person_memory_key(3), "user:3")
        self.assertIsNone(make_person_memory_key(None))
        self.assertEqual(make_session_memory_key("session", 3), "session:user:3")
        self.assertEqual(make_voice_room_session_key(1, 9), "guild:1:voice:9")
        self.assertEqual(make_voice_room_session_key(1, None), "guild:1:voice:none")
        self.assertEqual(make_voice_session_key(1, 9, 3), "guild:1:voice:9:user:3")

    def test_build_text_ingress_context_groups_text_ids(self) -> None:
        context = build_text_ingress_context(guild_id=1, channel_id=2, user_id=3, thread_id=4)

        self.assertEqual(context.session_key, "guild:1:text:2:thread:4:user:3")
        self.assertEqual(context.room_key, "text:2")
        self.assertEqual(context.person_key, "user:3")
        self.assertEqual(context.session_memory_key, "guild:1:text:2:thread:4:user:3:user:3")
        self.assertEqual(context.reply_slot_key, "guild:1:reply:text:2:thread:4")

    def test_build_text_ingress_context_from_message_resolves_thread_id(self) -> None:
        class Obj:
            def __init__(self, **kwargs) -> None:
                self.__dict__.update(kwargs)

        text_parent = Obj(id=20, kind="text")
        voice_parent = Obj(id=30, kind="voice")
        thread_channel = Obj(id=4, parent=text_parent)
        non_thread_channel = Obj(id=5, parent=voice_parent)
        message = Obj(guild=Obj(id=1), channel=thread_channel, author=Obj(id=3))

        context = build_text_ingress_context_from_message(
            message,
            is_thread_parent=lambda parent: getattr(parent, "kind", "") == "text",
        )

        self.assertEqual(context.thread_id, 4)
        self.assertEqual(context.session_key, "guild:1:text:4:thread:4:user:3")
        self.assertIsNone(resolve_text_thread_id(non_thread_channel, is_thread_parent=lambda parent: getattr(parent, "kind", "") == "text"))

    def test_build_voice_ingress_context_groups_voice_ids(self) -> None:
        context = build_voice_ingress_context(guild_id=1, voice_channel_id=9, user_id=3)

        self.assertEqual(context.room_session_key, "guild:1:voice:9")
        self.assertEqual(context.session_key, "guild:1:voice:9:user:3")
        self.assertEqual(context.room_key, "voice:9")
        self.assertEqual(context.person_key, "user:3")
        self.assertEqual(context.session_memory_key, "guild:1:voice:9:user:3:user:3")

    def test_voice_debug_meta_helpers_normalize_source(self) -> None:
        original = {"source": "local_mic", "duration_sec": 1.2}

        normalized = normalize_voice_debug_meta(original)
        normalized["source"] = "changed"

        self.assertEqual(original["source"], "local_mic")
        self.assertEqual(voice_ingress_source(original), "local_mic")
        self.assertEqual(voice_ingress_source({}), "discord_voice")
        self.assertEqual(voice_ingress_source(None), "discord_voice")

    def test_should_accept_text_turn_matches_gate_logic(self) -> None:
        self.assertFalse(should_accept_text_turn(is_wake_word=False, is_reply=False, is_active_session=False))
        self.assertTrue(should_accept_text_turn(is_wake_word=True, is_reply=False, is_active_session=False))
        self.assertTrue(should_accept_text_turn(is_wake_word=False, is_reply=True, is_active_session=False))
        self.assertTrue(should_accept_text_turn(is_wake_word=False, is_reply=False, is_active_session=True))

    def test_text_message_precheck_routes_commands_and_command_only_channels(self) -> None:
        command = decide_text_message_precheck(content="!status", prefix="!", channel_id=1, command_only_channel_ids=[])
        ignored = decide_text_message_precheck(content="hello", prefix="!", channel_id=2, command_only_channel_ids=[2])
        continued = decide_text_message_precheck(content="hello", prefix="!", channel_id=3, command_only_channel_ids=[2])

        self.assertEqual(command.action, "process_commands")
        self.assertEqual(command.reason, "command_prefix")
        self.assertEqual(ignored.action, "ignore")
        self.assertEqual(ignored.reason, "command_only_channel")
        self.assertEqual(continued.action, "continue")

    def test_build_text_turn_decision_drops_or_accepts_with_prompt_and_attachments(self) -> None:
        dropped = build_text_turn_decision(
            "hello",
            is_wake_word=False,
            is_reply=False,
            is_active_session=False,
            strip_wake_word=lambda text: text,
            empty_wake_text="empty",
        )
        accepted = build_text_turn_decision(
            "Evelyn",
            is_wake_word=True,
            is_reply=False,
            is_active_session=False,
            strip_wake_word=lambda _text: "",
            empty_wake_text="empty",
            attachment_context="- image: url",
        )

        self.assertFalse(dropped.accepted)
        self.assertEqual(dropped.reason, "text_gate_not_open")
        self.assertTrue(accepted.accepted)
        self.assertEqual(accepted.user_text, "empty\n\n[Attached Visual Inputs]\n- image: url")

    def test_build_text_turn_user_text_strips_wake_and_appends_attachments(self) -> None:
        user_text = build_text_turn_user_text(
            "Evelyn hello",
            is_wake_word=True,
            strip_wake_word=lambda text: text.replace("Evelyn", "", 1).strip(),
            empty_wake_text="empty",
            attachment_context="- image: url",
        )

        self.assertEqual(user_text, "hello\n\n[Attached Visual Inputs]\n- image: url")

    def test_build_text_turn_user_text_uses_empty_prompt(self) -> None:
        user_text = build_text_turn_user_text(
            "Evelyn",
            is_wake_word=True,
            strip_wake_word=lambda _text: "",
            empty_wake_text="empty",
        )

        self.assertEqual(user_text, "empty")

    def test_build_discord_attachment_context_labels_images_and_files(self) -> None:
        class Attachment:
            def __init__(self, **kwargs) -> None:
                self.__dict__.update(kwargs)

        class Message:
            attachments = [
                Attachment(
                    content_type="image/png",
                    filename="screen.png",
                    url="https://example.test/screen.png",
                    width=1280,
                    height=720,
                ),
                Attachment(
                    content_type="application/pdf",
                    filename="notes.pdf",
                    url="https://example.test/notes.pdf",
                ),
            ]

        context = build_discord_attachment_context(Message())

        self.assertIn("- image: filename=screen.png; meta=1280x720, image/png; url=https://example.test/screen.png", context)
        self.assertIn("- attachment: filename=notes.pdf; meta=application/pdf; url=https://example.test/notes.pdf", context)

    def test_is_reply_to_target_user_matches_fetched_message_author(self) -> None:
        class Obj:
            def __init__(self, **kwargs) -> None:
                self.__dict__.update(kwargs)

        target = Obj(id=1)

        class Channel:
            async def fetch_message(self, message_id: int):
                self.message_id = message_id
                return Obj(author=target)

        message = Obj(reference=Obj(message_id=123), channel=Channel())

        self.assertTrue(asyncio.run(is_reply_to_target_user(message, target)))

    def test_is_reply_to_target_user_logs_fetch_failure(self) -> None:
        class Obj:
            def __init__(self, **kwargs) -> None:
                self.__dict__.update(kwargs)

        class Channel:
            async def fetch_message(self, _message_id: int):
                raise RuntimeError("missing")

        logs: list[str] = []
        message = Obj(reference=Obj(message_id=123), channel=Channel())

        self.assertFalse(asyncio.run(is_reply_to_target_user(message, Obj(id=1), log=logs.append)))
        self.assertIn("답장 확인 오류:", logs[0])


if __name__ == "__main__":
    unittest.main()
