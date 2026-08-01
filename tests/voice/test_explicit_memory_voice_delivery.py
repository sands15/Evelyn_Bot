from __future__ import annotations

import asyncio
import sys
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch


REPO_ROOT = next(
    path
    for path in Path(__file__).resolve().parents
    if (path / "main.py").exists()
)
RUNTIME_ROOT = REPO_ROOT / "evelyn_core" / "runtime"
if str(RUNTIME_ROOT) not in sys.path:
    sys.path.insert(0, str(RUNTIME_ROOT))

from evelyn_core.voice_orchestration import (  # noqa: E402
    deliver_voice_reply,
)


class ExplicitMemoryVoiceDeliveryTests(
    unittest.IsolatedAsyncioTestCase
):
    async def test_wake_only_reply_emits_typed_terminal_summary(self) -> None:
        metrics: dict = {"started_at": 1.0, "marks": {}, "meta": {}}
        summaries: list[dict] = []

        async def speak_answer(_vc, _answer: str, **kwargs) -> None:
            kwargs["metrics"]["meta"].update(
                {
                    "playback_started": True,
                    "playback_completed": True,
                    "playback_cancelled": False,
                }
            )

        result = await deliver_voice_reply(
            voice_reply=SimpleNamespace(
                wake_only_turn=True,
                history_user_text="이블린",
                prompt_user_text="unused",
                turn_type="wake_call",
                selected_path="canned_wake_reply",
            ),
            canned_wake_reply="응, 듣고 있어.",
            vc=object(),
            accepted_turn_id="turn-wake-1",
            session_key="session-1",
            guild_id=7,
            room_key="room-key",
            person_key="person-key",
            session_memory_key="session-memory",
            metrics=metrics,
            turn_scope=object(),
            on_final_answer=None,
            speak_answer=speak_answer,
            ask_llm_and_speak_streaming=lambda *_args, **_kwargs: None,
            record_voice_pipeline_failure=lambda *_args, **_kwargs: None,
            log_voice_stage=lambda *_args, **_kwargs: None,
            strip_omnivoice_tags=lambda value: value,
            report_delivery_error=lambda _exc: None,
            log_voice_bottleneck_summary=lambda _metrics, **payload: summaries.append(
                payload
            ),
        )

        self.assertIsNotNone(result)
        self.assertIs(metrics["meta"]["reply_started"], True)
        self.assertIs(metrics["meta"]["reply_final"], True)
        self.assertIs(metrics["meta"]["playback_completed"], True)
        self.assertEqual(len(summaries), 1)
        self.assertEqual(summaries[0]["event_name"], "voice_turn_summary")

    async def test_wake_only_cancel_emits_one_terminal_summary_and_reraises(
        self,
    ) -> None:
        metrics: dict = {"meta": {}}
        summaries: list[dict] = []

        async def cancel_speak(*_args, **_kwargs) -> None:
            raise asyncio.CancelledError()

        with self.assertRaises(asyncio.CancelledError):
            await deliver_voice_reply(
                voice_reply=SimpleNamespace(
                    wake_only_turn=True,
                    history_user_text="이블린",
                    prompt_user_text="unused",
                    turn_type="wake_call",
                    selected_path="canned_wake_reply",
                ),
                canned_wake_reply="응, 듣고 있어.",
                vc=object(),
                accepted_turn_id="turn-wake-cancel",
                session_key="session-1",
                guild_id=7,
                room_key="room-key",
                person_key="person-key",
                session_memory_key="session-memory",
                metrics=metrics,
                turn_scope=object(),
                on_final_answer=None,
                speak_answer=cancel_speak,
                ask_llm_and_speak_streaming=lambda *_args, **_kwargs: None,
                record_voice_pipeline_failure=lambda *_args, **_kwargs: None,
                log_voice_stage=lambda *_args, **_kwargs: None,
                strip_omnivoice_tags=lambda value: value,
                report_delivery_error=lambda _exc: None,
                log_voice_bottleneck_summary=(
                    lambda _metrics, **payload: summaries.append(payload)
                ),
            )

        self.assertIs(metrics["meta"]["reply_started"], True)
        self.assertIs(metrics["meta"]["reply_final"], True)
        self.assertIs(metrics["meta"]["playback_cancelled"], True)
        self.assertIs(metrics["meta"]["playback_completed"], False)
        self.assertEqual(metrics["meta"]["error"], "cancelled")
        self.assertEqual(len(summaries), 1)
        self.assertEqual(summaries[0]["event_name"], "voice_turn_summary")

    async def test_single_reply_on_final_cancel_uses_outer_summary_guard(self) -> None:
        metrics: dict = {"meta": {}}
        summaries: list[dict] = []
        speak_calls: list[str] = []

        async def cancel_final(_answer: str) -> None:
            raise asyncio.CancelledError()

        async def unexpected_speak(*_args, **_kwargs) -> None:
            speak_calls.append("called")

        with self.assertRaises(asyncio.CancelledError):
            await deliver_voice_reply(
                voice_reply=SimpleNamespace(
                    wake_only_turn=True,
                    history_user_text="이블린",
                    prompt_user_text="unused",
                    turn_type="wake_call",
                    selected_path="canned_wake_reply",
                ),
                canned_wake_reply="응, 듣고 있어.",
                vc=object(),
                accepted_turn_id="turn-final-cancel",
                session_key="session-1",
                guild_id=7,
                room_key="room-key",
                person_key="person-key",
                session_memory_key="session-memory",
                metrics=metrics,
                turn_scope=object(),
                on_final_answer=cancel_final,
                speak_answer=unexpected_speak,
                ask_llm_and_speak_streaming=lambda *_args, **_kwargs: None,
                record_voice_pipeline_failure=lambda *_args, **_kwargs: None,
                log_voice_stage=lambda *_args, **_kwargs: None,
                strip_omnivoice_tags=lambda value: value,
                report_delivery_error=lambda _exc: None,
                log_voice_bottleneck_summary=(
                    lambda _metrics, **payload: summaries.append(payload)
                ),
            )

        self.assertEqual(speak_calls, [])
        self.assertIs(metrics["meta"]["playback_cancelled"], True)
        self.assertEqual(len(summaries), 1)

    async def test_empty_streaming_answer_records_fixed_delivery_failure(
        self,
    ) -> None:
        failures: list[tuple[str, object, dict]] = []
        metrics: dict = {"meta": {}}

        async def empty_llm(*_args, **_kwargs) -> str:
            return ""

        result = await deliver_voice_reply(
            voice_reply=SimpleNamespace(
                wake_only_turn=False,
                history_user_text="답을 이어가줘",
                prompt_user_text="답을 이어가줘",
                turn_type="conversation",
                selected_path="main_llm",
            ),
            canned_wake_reply="응?",
            vc=object(),
            accepted_turn_id="turn-empty-1",
            session_key="session-1",
            guild_id=7,
            room_key="room-key",
            person_key="person-key",
            session_memory_key="session-memory",
            metrics=metrics,
            turn_scope=object(),
            on_final_answer=None,
            speak_answer=lambda *_args, **_kwargs: None,
            ask_llm_and_speak_streaming=empty_llm,
            record_voice_pipeline_failure=(
                lambda code, error, _metrics, **kwargs: failures.append(
                    (code, error, kwargs)
                )
            ),
            log_voice_stage=lambda *_args, **_kwargs: None,
            strip_omnivoice_tags=lambda value: value,
            report_delivery_error=lambda _exc: None,
        )

        self.assertIsNone(result)
        self.assertEqual(
            metrics["meta"]["voice_delivery_failure_code"],
            "voice_delivery_empty",
        )
        self.assertEqual(
            failures,
            [
                (
                    "voice_delivery_empty",
                    "voice_delivery_empty",
                    {"stage": "answer_finalize"},
                )
            ],
        )

    async def test_validation_bound_streaming_keeps_prompt_raw_but_redacts_debug_text(self) -> None:
        user_secret = "VOICE_PRIVACY_SENTINEL_LLM_USER_5d3a"
        answer_secret = "VOICE_PRIVACY_SENTINEL_LLM_REPLY_97f1"
        calls: list[tuple[str, dict]] = []
        stages: list[tuple[tuple, dict]] = []
        metrics: dict = {
            "meta": {
                "validation_session_id": "validation-private",
                "validation_step_id": "03-playback",
                "validation_attempt_id": "attempt-private",
            }
        }

        async def stream(_vc, prompt: str, **kwargs) -> str:
            calls.append((prompt, kwargs))
            return answer_secret

        result = await deliver_voice_reply(
            voice_reply=SimpleNamespace(
                wake_only_turn=False,
                history_user_text=user_secret,
                prompt_user_text=user_secret,
                turn_type="conversation",
                selected_path="main_llm",
            ),
            canned_wake_reply="unused",
            vc=object(),
            accepted_turn_id="turn-validation-1",
            session_key="session-1",
            guild_id=7,
            room_key="room-key",
            person_key="person-key",
            session_memory_key="session-memory",
            metrics=metrics,
            turn_scope=object(),
            on_final_answer=None,
            speak_answer=lambda *_args, **_kwargs: None,
            ask_llm_and_speak_streaming=stream,
            record_voice_pipeline_failure=lambda *_args, **_kwargs: None,
            log_voice_stage=lambda *args, **kwargs: stages.append((args, kwargs)),
            strip_omnivoice_tags=lambda value: value,
            report_delivery_error=lambda _exc: None,
        )

        self.assertIsNotNone(result)
        self.assertEqual(result.answer_text, answer_secret)
        self.assertEqual(calls[0][0], user_secret)
        self.assertNotIn(user_secret, calls[0][1]["debug_text"])
        self.assertIn("<validation-text chars=", calls[0][1]["debug_text"])
        rendered = repr(stages)
        self.assertNotIn(user_secret, rendered)
        self.assertNotIn(answer_secret, rendered)

    async def test_validation_bound_canned_reply_and_error_are_content_free_in_observability(self) -> None:
        answer_secret = "VOICE_PRIVACY_SENTINEL_CANNED_REPLY_5e72"
        error_secret = "VOICE_PRIVACY_SENTINEL_DELIVERY_ERROR_17a9"
        spoken: list[str] = []
        stages: list[tuple[tuple, dict]] = []
        failures: list[object] = []
        reported: list[Exception] = []
        metrics: dict = {"meta": {"validation_attempt_id": "attempt-private"}}

        async def fail_speak(_vc, answer: str, **_kwargs) -> None:
            spoken.append(answer)
            raise RuntimeError(error_secret)

        result = await deliver_voice_reply(
            voice_reply=SimpleNamespace(
                wake_only_turn=True,
                history_user_text="이블린",
                prompt_user_text="unused",
                turn_type="wake_call",
                selected_path="canned_wake_reply",
            ),
            canned_wake_reply=answer_secret,
            vc=object(),
            accepted_turn_id="turn-validation-error",
            session_key="session-1",
            guild_id=7,
            room_key=None,
            person_key=None,
            session_memory_key=None,
            metrics=metrics,
            turn_scope=object(),
            on_final_answer=None,
            speak_answer=fail_speak,
            ask_llm_and_speak_streaming=lambda *_args, **_kwargs: None,
            record_voice_pipeline_failure=(
                lambda _code, error, *_args, **_kwargs: failures.append(error)
            ),
            log_voice_stage=lambda *args, **kwargs: stages.append((args, kwargs)),
            strip_omnivoice_tags=lambda value: value,
            report_delivery_error=reported.append,
        )

        self.assertIsNone(result)
        self.assertEqual(spoken, [answer_secret])
        rendered = repr((stages, failures, reported))
        self.assertNotIn(answer_secret, rendered)
        self.assertNotIn(error_secret, rendered)
        self.assertIn("<validation-text chars=", repr(stages))
        self.assertIn("errorType=RuntimeError", str(reported[0]))

    async def test_accepted_memory_command_bypasses_llm_and_speaks_receipt(self) -> None:
        receipt = {
            "schema": "memory.user-confirmation.v1",
            "state": "stored",
            "noteId": "concept-1234567890abcdef",
            "sourceRef": (
                "turn:opaque-turn-" + ("b" * 64) + ":user"
            ),
            "confirmedAt": "2026-07-31T00:00:00+00:00",
            "contentFree": True,
        }
        spoken: list[tuple[str, dict]] = []
        final_answers: list[str] = []
        failures: list[str] = []
        metrics: dict = {"meta": {}}

        async def speak_answer(_vc, answer: str, **kwargs) -> None:
            spoken.append((answer, kwargs))

        async def unexpected_llm(*_args, **_kwargs):
            raise AssertionError("LLM must be bypassed")

        async def on_final_answer(answer: str) -> None:
            final_answers.append(answer)

        with patch(
            "evelyn_core.voice_orchestration."
            "execute_explicit_memory_confirmation",
            return_value=(
                True,
                "지금 요청을 근거로 새 기억에 저장했어.",
                receipt,
                "",
            ),
        ) as execute:
            result = await deliver_voice_reply(
                voice_reply=SimpleNamespace(
                    wake_only_turn=False,
                    history_user_text=(
                        "기억해줘: 나는 비 오는 날 산책을 좋아해"
                    ),
                    prompt_user_text="unused",
                    turn_type="statement",
                    selected_path="pipeline",
                ),
                canned_wake_reply="응?",
                vc=object(),
                accepted_turn_id="turn-voice-1",
                session_key="session-1",
                guild_id=7,
                room_key="room-key",
                person_key="person-key",
                session_memory_key="session-memory",
                metrics=metrics,
                turn_scope=object(),
                on_final_answer=on_final_answer,
                speak_answer=speak_answer,
                ask_llm_and_speak_streaming=unexpected_llm,
                record_voice_pipeline_failure=(
                    lambda code, *_args, **_kwargs: failures.append(
                        code
                    )
                ),
                log_voice_stage=lambda *_args, **_kwargs: None,
                strip_omnivoice_tags=lambda value: value,
                report_delivery_error=(
                    lambda exc: failures.append(type(exc).__name__)
                ),
            )

        self.assertIsNotNone(result)
        self.assertEqual(
            result.plain_answer_text,
            "지금 요청을 근거로 새 기억에 저장했어.",
        )
        self.assertFalse(result.used_wake_only_reply)
        self.assertEqual(final_answers, [result.answer_text])
        self.assertEqual(len(spoken), 1)
        self.assertEqual(
            spoken[0][1]["turn_id"],
            "turn-voice-1",
        )
        self.assertEqual(failures, [])
        self.assertEqual(
            metrics["meta"]["memory_write_receipt"],
            receipt,
        )
        self.assertEqual(
            metrics["meta"]["reply_source"],
            "explicit_memory_confirmation",
        )
        self.assertIs(metrics["meta"]["reply_started"], True)
        self.assertIs(metrics["meta"]["reply_final"], True)
        execute.assert_called_once_with(
            "기억해줘: 나는 비 오는 날 산책을 좋아해",
            action_id="turn-voice-1",
            evidence_turn_id="turn-voice-1",
            source="discord-user",
        )

    async def test_explicit_memory_cancel_emits_one_terminal_summary_and_reraises(
        self,
    ) -> None:
        metrics: dict = {"meta": {}}
        summaries: list[dict] = []

        async def cancel_speak(*_args, **_kwargs) -> None:
            raise asyncio.CancelledError()

        with patch(
            "evelyn_core.voice_orchestration.execute_explicit_memory_confirmation",
            return_value=(True, "기억에 저장했어.", None, ""),
        ), self.assertRaises(asyncio.CancelledError):
            await deliver_voice_reply(
                voice_reply=SimpleNamespace(
                    wake_only_turn=False,
                    history_user_text="기억해줘: 비를 좋아해",
                    prompt_user_text="unused",
                    turn_type="statement",
                    selected_path="pipeline",
                ),
                canned_wake_reply="응?",
                vc=object(),
                accepted_turn_id="turn-memory-cancel",
                session_key="session-1",
                guild_id=7,
                room_key="room-key",
                person_key="person-key",
                session_memory_key="session-memory",
                metrics=metrics,
                turn_scope=object(),
                on_final_answer=None,
                speak_answer=cancel_speak,
                ask_llm_and_speak_streaming=lambda *_args, **_kwargs: None,
                record_voice_pipeline_failure=lambda *_args, **_kwargs: None,
                log_voice_stage=lambda *_args, **_kwargs: None,
                strip_omnivoice_tags=lambda value: value,
                report_delivery_error=lambda _exc: None,
                log_voice_bottleneck_summary=(
                    lambda _metrics, **payload: summaries.append(payload)
                ),
            )

        self.assertIs(metrics["meta"]["reply_started"], True)
        self.assertIs(metrics["meta"]["reply_final"], True)
        self.assertIs(metrics["meta"]["playback_cancelled"], True)
        self.assertEqual(metrics["meta"]["error"], "cancelled")
        self.assertEqual(len(summaries), 1)


if __name__ == "__main__":
    unittest.main()
