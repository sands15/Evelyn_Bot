import unittest
from pathlib import Path


REPO_ROOT = next(path for path in Path(__file__).resolve().parents if (path / "main.py").exists())
RUNTIME_ROOT = REPO_ROOT / "evelyn_core" / "runtime"
import sys
from unittest.mock import patch

if str(RUNTIME_ROOT) not in sys.path:
    sys.path.insert(0, str(RUNTIME_ROOT))

import evelyn_core.cognitive_policy_state as cognitive_policy_state  # noqa: E402
from evelyn_core.cognitive_policy_state import (  # noqa: E402
    apply_ask_gating,
    build_cognitive_fallback_state,
    build_fast_cognitive_state,
    finalize_cognitive_state,
    policy_response_for_state,
    read_cached_cognitive_state,
    read_layered_cognitive_state,
)


class FastCognitiveStateSourceTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.main_py = (REPO_ROOT / "main.py").read_text(encoding="utf-8")
        cls.module_py = (
            REPO_ROOT / "evelyn_core" / "runtime" / "evelyn_core" / "cognitive_policy_state.py"
        ).read_text(encoding="utf-8")

    def test_fast_path_state_uses_current_user_text_for_summary(self) -> None:
        state = build_fast_cognitive_state(
            "지금 요청",
            action="answer",
            current_state={"state_summary": "오래된 요약"},
            now=100.0,
        )

        self.assertEqual(state["state_summary"], "지금 요청")
        self.assertIn('"state_summary": cleaned,', self.module_py)
        self.assertNotIn('"state_summary": base.get("state_summary") or cleaned,', self.module_py)

    def test_fast_path_hint_does_not_reuse_stale_cognitive_hint(self) -> None:
        state = build_fast_cognitive_state(
            "이어 말할게",
            action="wait",
            current_state={"main_prompt_hint": "오래된 힌트"},
            now=100.0,
        )

        self.assertEqual(state["main_prompt_hint"], "지금은 더 듣는 쪽이 자연스럽다. 아주 짧게 반응해라.")
        self.assertIn('"main_prompt_hint": hint,', self.module_py)
        self.assertNotIn('"main_prompt_hint": base.get("main_prompt_hint") or hint,', self.module_py)

    def test_conversation_state_does_not_surface_internal_response_hint(self) -> None:
        self.assertNotIn('state_lines.append(f"- 응답 힌트:', self.main_py)

    def test_ask_gating_falls_back_when_question_is_missing(self) -> None:
        text_state = apply_ask_gating({"action": "ask", "question_for_user": "", "confidence": 1.0}, source="text")
        voice_state = apply_ask_gating({"action": "ask", "question_for_user": "", "confidence": 1.0}, source="voice")

        self.assertEqual(text_state["action"], "answer")
        self.assertEqual(voice_state["action"], "wait")
        self.assertIn("ask_gated_text", text_state["reason_brief"])

    def test_policy_response_returns_short_circuit_text_without_echoing_user(self) -> None:
        self.assertEqual(policy_response_for_state({"action": "wait"}, source="voice"), "응, 계속 말해줘.")
        self.assertEqual(policy_response_for_state({"action": "wait"}, source="text"), "잠깐, 이어서 말해줘.")
        self.assertEqual(
            policy_response_for_state(
                {"action": "ask", "question_for_user": "어디로 갈까?", "confidence": 1.0},
                source="text",
                user_text="선택지는 뭐야?",
            ),
            "어디로 갈까?",
        )
        self.assertIsNone(
            policy_response_for_state(
                {"action": "ask", "question_for_user": "어디로 갈까?", "confidence": 1.0},
                source="text",
                user_text="어디로 갈까?",
            )
        )

    def test_cognitive_fallback_and_finalize_fill_safe_defaults(self) -> None:
        fallback = build_cognitive_fallback_state(user_text="새 요청", now=100.0)
        finalized = finalize_cognitive_state(
            {"action": "ask", "confidence": 0.8, "question_for_user": "확인할까?"},
            current_state={"state_summary": "기존 요약"},
            user_text="새 요청",
            now=101.0,
        )

        self.assertEqual(fallback["action"], "answer")
        self.assertEqual(fallback["state_summary"], "새 요청")
        self.assertEqual(fallback["updated_at"], 100)
        self.assertEqual(finalized["action"], "ask")
        self.assertEqual(finalized["state_summary"], "기존 요약")
        self.assertEqual(finalized["main_prompt_hint"], "짧고 자연스럽게 답해라.")
        self.assertEqual(finalized["updated_at"], 101)

    def test_layered_cognitive_state_prefers_session_person_room_then_guild(self) -> None:
        calls: list[tuple[str, str | None]] = []

        def fake_path(guild_id: int, *, scope_type: str = "guild", scope_key: str | None = None) -> tuple[str, str | None]:
            self.assertEqual(guild_id, 123)
            return (scope_type, scope_key)

        def fake_read(path: tuple[str, str | None]) -> dict:
            calls.append(path)
            scope_type, _scope_key = path
            if scope_type == "person":
                return {"action": "ask", "question_for_user": "확인할까?", "confidence": 1.0}
            return {}

        with patch.object(cognitive_policy_state, "cognitive_state_path", side_effect=fake_path):
            with patch.object(cognitive_policy_state, "read_json_file", side_effect=fake_read):
                state = read_layered_cognitive_state(
                    123,
                    room_key="room-1",
                    person_key="person-1",
                    session_memory_key="session-1",
                )

        self.assertEqual(state["action"], "ask")
        self.assertEqual(state["question_for_user"], "확인할까?")
        self.assertEqual(calls, [("session", "session-1"), ("person", "person-1")])

    def test_cached_cognitive_state_handles_missing_guild(self) -> None:
        self.assertIsNone(read_cached_cognitive_state(None))


if __name__ == "__main__":
    unittest.main()
