from __future__ import annotations

import asyncio
import sys
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch


REPO_ROOT = next(
    path for path in Path(__file__).resolve().parents if (path / "main.py").exists()
)
RUNTIME_ROOT = REPO_ROOT / "evelyn_core" / "runtime"
if str(RUNTIME_ROOT) not in sys.path:
    sys.path.insert(0, str(RUNTIME_ROOT))

import evelyn_core.voice_orchestration as orchestration  # noqa: E402


def _context(*, channel_id: int | None = 22) -> SimpleNamespace:
    channel = None if channel_id is None else SimpleNamespace(id=channel_id)
    member = SimpleNamespace(
        id=33,
        display_name="speaker",
        voice=SimpleNamespace(channel=channel),
    )
    return SimpleNamespace(
        guild_id=11,
        transcript=SimpleNamespace(final_text="final transcript"),
        voice_segment=object(),
        session_key="session",
        room_session_key="room",
        owner_user_id=33,
        source_turn_id="turn-1",
        segment_id=7,
        voiced_ms=500.0,
        raw_seconds=1.0,
        rms=0.1,
        wake_detected=True,
        reply_in_progress=False,
        metrics={},
        session_topic_seed="topic",
        now_monotonic=1.0,
        ingress_source="discord",
        queue_wait_ms=0.0,
        active_conversation_awaiting_reply_sec=30.0,
        active_conversation_voice_sec=30.0,
        member=member,
        canned_wake_reply="응",
        room_key="room-key",
        person_key="person-key",
        session_memory_key="memory-key",
        voice_ingress_is_current=lambda: True,
        release_ingress_worker=None,
    )


class VoiceArchiveAnswerBoundaryTests(unittest.TestCase):
    def test_final_voice_answer_is_archived_with_exact_stt_turn_lineage(self) -> None:
        calls: list[dict[str, object]] = []

        async def archive_assistant_text(**payload):
            calls.append(payload)

        async def fake_process_voice_reply_from_transcript(**_kwargs):
            runtime = orchestration.prepare_voice_reply_delivery_runtime(
                accepted_execution=SimpleNamespace(
                    accepted_turn_id="accepted",
                    turn_scope=object(),
                    turn_task=object(),
                ),
                room_session_key="room",
                session_locks={},
                speaker_display_name="speaker",
                visible_text=str,
                print_fn=lambda *_args: None,
            )
            await runtime.on_final_answer("exact final answer")
            return "delivered"

        deps = Mock()
        deps.archive_assistant_text = archive_assistant_text
        deps.confirm_archive_assistant_delivery = Mock()
        with patch.object(
            orchestration,
            "process_voice_reply_from_transcript",
            fake_process_voice_reply_from_transcript,
        ):
            result = asyncio.run(
                orchestration.process_voice_reply_from_transcript_context(
                    context=_context(),
                    deps=deps,
                )
            )

        self.assertEqual(result, "delivered")
        self.assertEqual(
            calls,
            [
                {
                    "guild_id": 11,
                    "channel_id": 22,
                    "user_id": 33,
                    "turn_id": "turn-1",
                    "text": "exact final answer",
                }
            ],
        )
        deps.confirm_archive_assistant_delivery.assert_not_called()

    def test_voice_feedback_target_is_confirmed_only_after_playback_success(
        self,
    ) -> None:
        order: list[str] = []

        async def archive_assistant_text(**_payload):
            order.append("archived")

        async def confirm_delivery(**_payload):
            order.append("confirmed")

        async def fake_deliver(**kwargs):
            await kwargs["on_final_answer"]("exact final answer")
            order.append("playback_completed")
            return orchestration.VoiceReplyDeliveryResult(
                answer_text="exact final answer",
                plain_answer_text="",
                used_wake_only_reply=False,
            )

        delivery_runtime = orchestration.prepare_voice_reply_delivery_runtime(
            accepted_execution=SimpleNamespace(
                accepted_turn_id="accepted",
                turn_scope=object(),
                turn_task=object(),
            ),
            room_session_key="room",
            session_locks={},
            speaker_display_name="speaker",
            visible_text=str,
            print_fn=lambda *_args: None,
        )
        archive_context = orchestration._VoiceArchiveAnswerContext(
            callback=archive_assistant_text,
            confirm_callback=confirm_delivery,
            guild_id=11,
            channel_id=22,
            user_id=33,
            source_turn_id="turn-1",
        )
        token = orchestration._voice_archive_answer_context.set(archive_context)
        try:
            with patch.object(
                orchestration,
                "deliver_voice_reply",
                fake_deliver,
            ):
                result = asyncio.run(
                    orchestration.run_locked_voice_reply_delivery(
                        room_session_key="room",
                        lock=asyncio.Lock(),
                        get_voice_client=lambda: SimpleNamespace(channel=None),
                        member=SimpleNamespace(display_name="speaker"),
                        voice_reply=SimpleNamespace(history_user_text="질문"),
                        canned_wake_reply="응",
                        accepted_turn_id="accepted",
                        session_key="session",
                        guild_id=11,
                        room_key="room-key",
                        person_key="person-key",
                        session_memory_key="memory-key",
                        metrics={"meta": {}},
                        turn_scope=object(),
                        segment_id=7,
                        gate_mode="wake",
                        on_final_answer=delivery_runtime.on_final_answer,
                        speak_answer=Mock(),
                        ask_llm_and_speak_streaming=Mock(),
                        record_voice_pipeline_failure=Mock(),
                        finalize_voice_reply_side_effects=Mock(),
                        log_voice_stage=Mock(),
                        strip_omnivoice_tags=str,
                        report_waiting_on_lock=None,
                        report_delivery_error=Mock(),
                    )
                )
        finally:
            orchestration._voice_archive_answer_context.reset(token)

        self.assertIsNotNone(result)
        self.assertEqual(order, ["archived", "playback_completed", "confirmed"])

    def test_voice_feedback_target_is_not_confirmed_after_playback_failure(
        self,
    ) -> None:
        order: list[str] = []

        async def archive_assistant_text(**_payload):
            order.append("archived")

        async def confirm_delivery(**_payload):
            order.append("confirmed")

        async def fake_failed_delivery(**kwargs):
            await kwargs["on_final_answer"]("undelivered answer")
            kwargs["metrics"].setdefault("meta", {})[
                "voice_delivery_failure_code"
            ] = "voice_delivery_failed"
            return None

        delivery_runtime = orchestration.prepare_voice_reply_delivery_runtime(
            accepted_execution=SimpleNamespace(
                accepted_turn_id="accepted",
                turn_scope=object(),
                turn_task=object(),
            ),
            room_session_key="room",
            session_locks={},
            speaker_display_name="speaker",
            visible_text=str,
            print_fn=lambda *_args: None,
        )
        archive_context = orchestration._VoiceArchiveAnswerContext(
            callback=archive_assistant_text,
            confirm_callback=confirm_delivery,
            guild_id=11,
            channel_id=22,
            user_id=33,
            source_turn_id="turn-1",
        )
        token = orchestration._voice_archive_answer_context.set(archive_context)
        try:
            with patch.object(
                orchestration,
                "deliver_voice_reply",
                fake_failed_delivery,
            ):
                result = asyncio.run(
                    orchestration.run_locked_voice_reply_delivery(
                        room_session_key="room",
                        lock=asyncio.Lock(),
                        get_voice_client=lambda: SimpleNamespace(channel=None),
                        member=SimpleNamespace(display_name="speaker"),
                        voice_reply=SimpleNamespace(history_user_text="질문"),
                        canned_wake_reply="응",
                        accepted_turn_id="accepted",
                        session_key="session",
                        guild_id=11,
                        room_key="room-key",
                        person_key="person-key",
                        session_memory_key="memory-key",
                        metrics={"meta": {}},
                        turn_scope=object(),
                        segment_id=7,
                        gate_mode="wake",
                        on_final_answer=delivery_runtime.on_final_answer,
                        speak_answer=Mock(),
                        ask_llm_and_speak_streaming=Mock(),
                        record_voice_pipeline_failure=Mock(),
                        finalize_voice_reply_side_effects=Mock(),
                        log_voice_stage=Mock(),
                        strip_omnivoice_tags=str,
                        report_waiting_on_lock=None,
                        report_delivery_error=Mock(),
                    )
                )
        finally:
            orchestration._voice_archive_answer_context.reset(token)

        self.assertIsNone(result)
        self.assertEqual(order, ["archived"])

    def test_archive_enabled_voice_reply_rejects_missing_channel_context(self) -> None:
        deps = Mock()
        deps.archive_assistant_text = Mock()

        with self.assertRaisesRegex(
            RuntimeError,
            "conversation_archive_voice_channel_unavailable",
        ):
            asyncio.run(
                orchestration.process_voice_reply_from_transcript_context(
                    context=_context(channel_id=None),
                    deps=deps,
                )
            )


if __name__ == "__main__":
    unittest.main()
