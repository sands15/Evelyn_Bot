from __future__ import annotations

import sys
import unittest
from pathlib import Path
from typing import Any


REPO_ROOT = next(path for path in Path(__file__).resolve().parents if (path / "main.py").exists())
RUNTIME_ROOT = REPO_ROOT / "evelyn_core" / "runtime"
if str(RUNTIME_ROOT) not in sys.path:
    sys.path.insert(0, str(RUNTIME_ROOT))

from evelyn_core.voice_reply_gate_runtime import (  # noqa: E402
    VoiceReplyGateRuntimeDeps,
    should_reply_to_voice_from_runtime,
)


def normalize(text: str) -> str:
    return " ".join(str(text or "").strip().lower().split())


class VoiceReplyGateRuntimeTests(unittest.TestCase):
    def make_deps(self, **overrides: Any) -> VoiceReplyGateRuntimeDeps:
        values = dict(
            session_state_snapshot=lambda _session_key: {"awaiting_user_reply": False, "last_stt_text": ""},
            room_state_snapshot=lambda _room_session_key: {"owner_user_id": None, "active_speaker_user_id": None},
            is_room_owner_active=lambda _room_session_key, _user_id: False,
            is_session_active_for_user=lambda _session_key, _user_id: False,
            tts_input_suppression_reason=lambda **_kwargs: None,
            room_last_voice_reply_at={},
            post_tts_ignore_sec=0.4,
            reply_cooldown_sec=2.0,
            normalize_voice_text=normalize,
            contains_wake_word=lambda text: "evelyn" in normalize(text),
            looks_like_brief_filler_text=lambda text: normalize(text) in {"um", "uh"},
            looks_like_repetitive_noise_text=lambda text: normalize(text) == "aaaa",
            is_similar=lambda left, right: normalize(left) == normalize(right),
            min_text_len=3,
            monotonic=lambda: 100.0,
        )
        values.update(overrides)
        return VoiceReplyGateRuntimeDeps(**values)

    def test_accepts_owner_followup_from_live_state(self) -> None:
        accepted, reason, gate_mode = should_reply_to_voice_from_runtime(
            guild_id=7,
            text="continue",
            session_key="session-1",
            room_session_key="room-1",
            user_id=42,
            deps=self.make_deps(
                session_state_snapshot=lambda _session_key: {"awaiting_user_reply": True, "last_stt_text": ""},
                room_state_snapshot=lambda _room_session_key: {"owner_user_id": 42, "active_speaker_user_id": 42},
                is_room_owner_active=lambda _room_session_key, _user_id: True,
                is_session_active_for_user=lambda _session_key, _user_id: True,
            ),
        )

        self.assertTrue(accepted)
        self.assertEqual(reason, "ok")
        self.assertEqual(gate_mode, "owner_followup")

    def test_tts_suppression_wins_unless_ignored(self) -> None:
        deps = self.make_deps(tts_input_suppression_reason=lambda **_kwargs: "bot_is_speaking")

        suppressed = should_reply_to_voice_from_runtime(
            guild_id=7,
            text="evelyn hello",
            user_id=42,
            deps=deps,
        )
        ignored = should_reply_to_voice_from_runtime(
            guild_id=7,
            text="evelyn hello",
            user_id=42,
            ignore_tts_suppression=True,
            deps=deps,
        )

        self.assertEqual(suppressed, (False, "bot_is_speaking", "bot_is_speaking"))
        self.assertEqual(ignored, (True, "ok", "wake_entry"))

    def test_cooldown_blocks_non_wake_turn(self) -> None:
        decision = should_reply_to_voice_from_runtime(
            guild_id=7,
            text="hello",
            session_key="session-1",
            room_session_key="room-1",
            user_id=42,
            deps=self.make_deps(
                room_last_voice_reply_at={"room-1": 99.0},
                room_state_snapshot=lambda _room_session_key: {"owner_user_id": None, "active_speaker_user_id": 42},
            ),
        )

        self.assertEqual(decision, (False, "no_wake_word", "no_wake_word"))

        wake_text_decision = should_reply_to_voice_from_runtime(
            guild_id=7,
            text="evelyn hello",
            session_key="session-1",
            room_session_key="room-1",
            user_id=42,
            deps=self.make_deps(room_last_voice_reply_at={"room-1": 99.0}),
        )
        wake_decision = should_reply_to_voice_from_runtime(
            guild_id=7,
            text="evelyn hello",
            wake_detected=True,
            session_key="session-1",
            room_session_key="room-1",
            user_id=42,
            deps=self.make_deps(room_last_voice_reply_at={"room-1": 99.0}),
        )
        self.assertEqual(wake_text_decision, (False, "cooldown", "cooldown"))
        self.assertEqual(wake_decision, (True, "ok", "wake_entry"))


if __name__ == "__main__":
    unittest.main()
