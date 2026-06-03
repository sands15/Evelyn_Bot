import sys
import unittest
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[1]
RUNTIME_ROOT = REPO_ROOT / "evelyn_core" / "runtime"
if str(RUNTIME_ROOT) not in sys.path:
    sys.path.insert(0, str(RUNTIME_ROOT))

from evelyn_core.discord_ingress import (  # noqa: E402
    build_text_ingress_context,
    build_text_turn_user_text,
    build_voice_ingress_context,
    make_person_memory_key,
    make_room_memory_key,
    make_session_memory_key,
    make_text_reply_slot_key,
    make_text_session_key,
    make_voice_room_session_key,
    make_voice_session_key,
    normalize_voice_debug_meta,
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


if __name__ == "__main__":
    unittest.main()
