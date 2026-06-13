from __future__ import annotations

import unittest
import sys
from pathlib import Path


REPO_ROOT = next(path for path in Path(__file__).resolve().parents if (path / "main.py").exists())
RUNTIME_ROOT = REPO_ROOT / "evelyn_core" / "runtime"
if str(RUNTIME_ROOT) not in sys.path:
    sys.path.insert(0, str(RUNTIME_ROOT))

from evelyn_core.context_pipeline import build_conversation_state_context  # noqa: E402


class InternalStateLeakGuardTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.main_py = (REPO_ROOT / "main.py").read_text(encoding="utf-8")

    def test_local_what_are_you_doing_query_is_not_fast_path_masked(self) -> None:
        self.assertNotIn('return "지금은 네 말 보고 있어."', self.main_py)
        self.assertNotIn('{"뭐해", "뭐하냐", "뭐하고있어"', self.main_py)

    def test_main_response_guidance_does_not_surface_internal_drafts(self) -> None:
        self.assertNotIn('parts.append(f"되물을 말: {state[', self.main_py)
        self.assertNotIn('parts.append(f"응답 추가 힌트: {state[', self.main_py)
        self.assertNotIn('내부 질문 초안: {state[', self.main_py)
        self.assertNotIn("guided_user_text = question_for_user", self.main_py)

    def test_conversation_state_context_hides_internal_summaries_and_hints(self) -> None:
        context = build_conversation_state_context(
            cognitive_state={
                "action": "answer",
                "user_intent": "근황 확인",
                "state_summary": "지금은 입력/출력 흐름과 주요 서비스만 먼저 보자.",
                "question_for_user": "내부 질문 초안",
                "main_prompt_hint": "쓸데없는 출력은 줄여야 해.",
            },
            session_state={},
            route="main_direct",
        )

        self.assertIn("action: answer", context)
        self.assertIn("user_intent: 근황 확인", context)
        self.assertNotIn("입력/출력 흐름", context)
        self.assertNotIn("내부 질문 초안", context)
        self.assertNotIn("쓸데없는 출력", context)


if __name__ == "__main__":
    unittest.main()
