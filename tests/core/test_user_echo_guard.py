from __future__ import annotations

import sys
import unittest
from pathlib import Path
from typing import Any


REPO_ROOT = next(path for path in Path(__file__).resolve().parents if (path / "main.py").exists())
RUNTIME_ROOT = REPO_ROOT / "evelyn_core" / "runtime"
if str(RUNTIME_ROOT) not in sys.path:
    sys.path.insert(0, str(RUNTIME_ROOT))

from evelyn_core.skills import conversation, delivery  # noqa: E402
from evelyn_core.skills.base import SkillContext  # noqa: E402
from evelyn_core.text import is_user_echo_answer  # noqa: E402
from evelyn_core.voice_pipeline import build_answer_payload_from_text, build_delivery_plan  # noqa: E402


def split_test_tts_chunks(text: str, *, force: bool = False) -> tuple[list[str], str]:
    return [text] if text else [], ""


class UserEchoGuardTests(unittest.IsolatedAsyncioTestCase):
    def test_exact_cleaned_echo_is_detected(self) -> None:
        self.assertTrue(is_user_echo_answer("  이블린 지금 상태 어때? ", "이블린 지금 상태 어때?"))
        self.assertFalse(is_user_echo_answer("이거 해줘", "바로 처리할게."))
        self.assertFalse(is_user_echo_answer("", ""))

    async def test_delivery_does_not_fallback_to_user_text(self) -> None:
        result = await delivery.execute(
            SkillContext(
                source="control_page",
                extras={
                    "user_text": "그냥 내 말을 반복하지 마",
                    "build_answer_payload_from_text_fn": build_answer_payload_from_text,
                    "build_delivery_plan_fn": build_delivery_plan,
                    "split_tts_sentences_fn": split_test_tts_chunks,
                },
            )
        )

        self.assertFalse(result.handled)
        self.assertFalse(result.should_emit)
        self.assertEqual(result.display_text, "")
        self.assertEqual(result.answer_text, "")

    async def test_conversation_echo_preface_falls_through_to_main_llm(self) -> None:
        events: list[str] = []

        async def execute_main_llm_once(**kwargs: Any) -> tuple[str, str]:
            events.append("main_llm")
            return "메인 답변", "answer"

        result = await conversation.execute(
            SkillContext(
                source="text",
                extras={
                    "route": "policy_short_circuit",
                    "user_text": "내 말 반복하지 마",
                    "prompt_text": "내 말 반복하지 마",
                    "user_visible_preface": "내 말 반복하지 마",
                    "messages": [],
                    "model_name": "test-model",
                    "voice_llm_max_tokens": 32,
                    "build_main_response_guidance_fn": lambda *args, **kwargs: "",
                    "build_main_llm_payload_fn": lambda **kwargs: kwargs,
                    "execute_main_llm_once_fn": execute_main_llm_once,
                },
            )
        )

        self.assertEqual(result.answer_text, "메인 답변")
        self.assertEqual(events, ["main_llm"])


if __name__ == "__main__":
    unittest.main()
