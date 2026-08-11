from __future__ import annotations

import sys
import unittest
from pathlib import Path
from unittest.mock import AsyncMock, Mock, patch


REPO_ROOT = next(path for path in Path(__file__).resolve().parents if (path / "main.py").exists())
RUNTIME_ROOT = REPO_ROOT / "evelyn_core" / "runtime"
if str(RUNTIME_ROOT) not in sys.path:
    sys.path.insert(0, str(RUNTIME_ROOT))

from evelyn_core.voice_io_composition_runtime import VoiceIoComposition, VoiceIoCompositionDeps


class VoiceIoCompositionRuntimeTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self) -> None:
        self.tokens = {name: object() for name in VoiceIoCompositionDeps.__dataclass_fields__}
        self.composition = VoiceIoComposition(
            VoiceIoCompositionDeps(
                **{name: (lambda token=token: token) for name, token in self.tokens.items()}
            )
        )

    async def test_discord_tts_adapter_uses_typed_dependency_factory(self) -> None:
        runtime = AsyncMock(return_value=None)
        with patch(
            "evelyn_core.voice_io_composition_runtime.speak_answer_from_runtime",
            runtime,
        ):
            await self.composition.speak_answer(
                "voice-client",
                "answer",
                turn_id="turn-1",
                session_key="session-1",
                turn_scope="scope",
                metrics={"turn": 1},
            )

        runtime.assert_awaited_once_with(
            "voice-client",
            "answer",
            deps=self.tokens["discord_tts_single"],
            turn_id="turn-1",
            session_key="session-1",
            turn_scope="scope",
            metrics={"turn": 1},
        )

    async def test_response_adapter_preserves_memory_scope(self) -> None:
        runtime = AsyncMock(return_value=("payload", "first", {"route": "main"}))
        with patch(
            "evelyn_core.voice_io_composition_runtime.build_first_response_from_runtime",
            runtime,
        ):
            result = await self.composition.build_first_response(
                "hello",
                guild_id=7,
                session_key="session",
                room_key="room",
                person_key="person",
                session_memory_key="memory",
                source="voice",
                debug_text="debug",
                metrics={"m": 1},
            )

        self.assertEqual(result, ("payload", "first", {"route": "main"}))
        runtime.assert_awaited_once_with(
            "hello",
            deps=self.tokens["response"],
            guild_id=7,
            session_key="session",
            room_key="room",
            person_key="person",
            session_memory_key="memory",
            source="voice",
            debug_text="debug",
            metrics={"m": 1},
        )

    async def test_delivery_adapter_preserves_turn_scope(self) -> None:
        runtime = AsyncMock(return_value=3)
        with patch(
            "evelyn_core.voice_io_composition_runtime.execute_voice_delivery_plan_from_runtime",
            runtime,
        ):
            result = await self.composition.execute_voice_delivery_plan(
                "voice-client",
                "plan",
                metrics={"m": 1},
                turn_id="turn",
                session_key="session",
                turn_scope="scope",
            )

        self.assertEqual(result, 3)
        runtime.assert_awaited_once_with(
            "voice-client",
            "plan",
            deps=self.tokens["delivery"],
            metrics={"m": 1},
            turn_id="turn",
            session_key="session",
            turn_scope="scope",
        )

    async def test_member_audio_adapter_preserves_ingress_metadata(self) -> None:
        runtime = AsyncMock(return_value=None)
        with patch(
            "evelyn_core.voice_io_composition_runtime.process_member_audio_pipeline_from_runtime",
            runtime,
        ):
            await self.composition.process_member_audio_impl(
                "member",
                b"pcm",
                {"source": "local_mic"},
                session_key="session",
                room_session_key="room-session",
                room_key="room",
                person_key="person",
                session_memory_key="memory",
                turn_id="turn",
                segment_id=9,
                ingress_during_reply=True,
                owner_user_id_on_ingress=42,
                voice_listener_binding="listener-binding",
            )

        runtime.assert_awaited_once_with(
            "member",
            b"pcm",
            {"source": "local_mic"},
            session_key="session",
            room_session_key="room-session",
            room_key="room",
            person_key="person",
            session_memory_key="memory",
            turn_id="turn",
            segment_id=9,
            ingress_during_reply=True,
            owner_user_id_on_ingress=42,
            voice_listener_binding="listener-binding",
            deps=self.tokens["member_audio_pipeline"],
        )

    def test_reply_side_effect_adapter_forwards_delivery_failure(self) -> None:
        runtime = Mock(return_value=None)
        with patch(
            "evelyn_core.voice_io_composition_runtime."
            "finalize_voice_reply_side_effects_from_runtime",
            runtime,
        ):
            self.composition.finalize_voice_reply_side_effects(
                guild_id=7,
                member="member",
                session_key="session",
                room_session_key="room-session",
                room_key="room",
                person_key="person",
                session_memory_key="memory",
                voice_reply="reply",
                plain_answer="",
                metrics={"meta": {}},
                turn_scope="scope",
                accepted_turn_id="turn",
                segment_id=9,
                delivery_succeeded=False,
                failure_code="voice_delivery_failed",
            )

        runtime.assert_called_once_with(
            guild_id=7,
            member="member",
            session_key="session",
            room_session_key="room-session",
            room_key="room",
            person_key="person",
            session_memory_key="memory",
            voice_reply="reply",
            plain_answer="",
            metrics={"meta": {}},
            turn_scope="scope",
            accepted_turn_id="turn",
            segment_id=9,
            delivery_succeeded=False,
            failure_code="voice_delivery_failed",
            deps=self.tokens["reply_side_effects"],
        )

    def test_stateless_helpers_do_not_build_runtime_dependencies(self) -> None:
        with patch(
            "evelyn_core.voice_io_composition_runtime.normalize_compare_text",
            Mock(return_value="normalized"),
        ) as normalize:
            self.assertEqual(self.composition.normalize_compare_text("raw"), "normalized")
        normalize.assert_called_once_with("raw")

    def test_main_uses_explicit_composition_bindings(self) -> None:
        source = (REPO_ROOT / "main.py").read_text(encoding="utf-8")
        runtime_source = (
            RUNTIME_ROOT / "evelyn_core" / "voice_io_composition_runtime.py"
        ).read_text(encoding="utf-8")

        self.assertIn("voice_io_composition = VoiceIoComposition(", source)
        self.assertIn("process_member_audio = voice_io_composition.process_member_audio", source)
        self.assertIn("stream_text_reply = voice_io_composition.stream_text_reply", source)
        self.assertNotIn("globals()", runtime_source)


if __name__ == "__main__":
    unittest.main()
