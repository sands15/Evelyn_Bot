from __future__ import annotations

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

    async def test_accepted_memory_command_bypasses_llm_and_speaks_receipt(self) -> None:
        receipt = {
            "schema": "memory.user-confirmation.v1",
            "state": "stored",
            "noteId": "concept-discord-voice",
            "sourceRef": "turn:turn-voice-1:user",
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
        execute.assert_called_once_with(
            "기억해줘: 나는 비 오는 날 산책을 좋아해",
            action_id="turn-voice-1",
            evidence_turn_id="turn-voice-1",
            source="discord-user",
        )


if __name__ == "__main__":
    unittest.main()
