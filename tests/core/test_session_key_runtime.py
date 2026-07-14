import unittest
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
RUNTIME_ROOT = REPO_ROOT / "evelyn_core" / "runtime"
if str(RUNTIME_ROOT) not in sys.path:
    sys.path.insert(0, str(RUNTIME_ROOT))

from evelyn_core.session_key_runtime import (
    SessionKeyRuntimeDeps,
    build_session_key_runtime_deps,
    make_person_memory_key_from_runtime,
    make_room_memory_key_from_runtime,
    make_session_memory_key_from_runtime,
    make_text_reply_slot_key_from_runtime,
    make_text_session_key_from_runtime,
    make_voice_room_session_key_from_runtime,
    make_voice_session_key_from_runtime,
    runtime_session_key_from_runtime,
)


class SessionKeyRuntimeTests(unittest.TestCase):
    def test_build_session_key_runtime_deps_defaults(self) -> None:
        deps = build_session_key_runtime_deps()
        self.assertEqual(deps.resolve_runtime_session_key(guild_id=42), "guild:42:default")
        self.assertEqual(
            make_text_session_key_from_runtime(
                1,
                2,
                3,
                thread_id=4,
                deps=deps,
            ),
            "guild:1:text:2:thread:4:user:3",
        )

    def test_runtime_delegates_to_injected_functions(self) -> None:
        seen: list[tuple] = []

        def resolve_runtime_session_key(session_key=None, guild_id=None):
            seen.append(("resolve", session_key, guild_id))
            return "resolved"

        def make_text_session_key(guild_id, channel_id, user_id, thread_id=None):
            seen.append(("text", guild_id, channel_id, user_id, thread_id))
            return f"{guild_id}:{channel_id}:{user_id}:{thread_id}"

        def make_text_reply_slot_key(guild_id, channel_id, thread_id=None):
            seen.append(("reply_slot", guild_id, channel_id, thread_id))
            return f"slot:{guild_id}:{channel_id}:{thread_id}"

        def make_voice_room_session_key(guild_id, channel_id):
            seen.append(("voice_room", guild_id, channel_id))
            return f"vroom:{guild_id}:{channel_id}"

        def make_voice_session_key(guild_id, channel_id, user_id):
            seen.append(("voice", guild_id, channel_id, user_id))
            return f"voice:{guild_id}:{channel_id}:{user_id}"

        def make_room_memory_key(kind, room_id):
            seen.append(("room", kind, room_id))
            return f"{kind}:{room_id}"

        def make_person_memory_key(user_id):
            seen.append(("person", user_id))
            return f"user:{user_id}" if user_id is not None else None

        def make_session_memory_key(session_key, user_id):
            seen.append(("session", session_key, user_id))
            return f"{session_key}:user:{user_id}"

        deps = SessionKeyRuntimeDeps(
            resolve_runtime_session_key=resolve_runtime_session_key,
            make_text_session_key_fn=make_text_session_key,
            make_text_reply_slot_key_fn=make_text_reply_slot_key,
            make_voice_room_session_key_fn=make_voice_room_session_key,
            make_voice_session_key_fn=make_voice_session_key,
            make_room_memory_key_fn=make_room_memory_key,
            make_person_memory_key_fn=make_person_memory_key,
            make_session_memory_key_fn=make_session_memory_key,
        )

        self.assertEqual(runtime_session_key_from_runtime(guild_id=7, deps=deps), "resolved")
        self.assertEqual(
            make_text_session_key_from_runtime(
                11,
                22,
                33,
                thread_id=44,
                deps=deps,
            ),
            "11:22:33:44",
        )
        self.assertEqual(make_text_reply_slot_key_from_runtime(1, 2, thread_id=3, deps=deps), "slot:1:2:3")
        self.assertEqual(make_voice_room_session_key_from_runtime(1, 2, deps=deps), "vroom:1:2")
        self.assertEqual(make_voice_session_key_from_runtime(1, 2, 3, deps=deps), "voice:1:2:3")
        self.assertEqual(make_room_memory_key_from_runtime("text", 9, deps=deps), "text:9")
        self.assertEqual(make_person_memory_key_from_runtime(3, deps=deps), "user:3")
        self.assertIsNone(make_person_memory_key_from_runtime(None, deps=deps))
        self.assertEqual(make_session_memory_key_from_runtime("session", 3, deps=deps), "session:user:3")

        self.assertEqual(
            seen,
            [
                ("resolve", None, 7),
                ("text", 11, 22, 33, 44),
                ("reply_slot", 1, 2, 3),
                ("voice_room", 1, 2),
                ("voice", 1, 2, 3),
                ("room", "text", 9),
                ("person", 3),
                ("person", None),
                ("session", "session", 3),
            ],
        )


if __name__ == "__main__":
    unittest.main()
