from __future__ import annotations

import sys
import unittest
from pathlib import Path


REPO_ROOT = next(path for path in Path(__file__).resolve().parents if (path / "main.py").exists())
RUNTIME_ROOT = REPO_ROOT / "evelyn_core" / "runtime"
if str(RUNTIME_ROOT) not in sys.path:
    sys.path.insert(0, str(RUNTIME_ROOT))

from evelyn_core.memory_llm_context import (  # noqa: E402
    build_cognitive_state_messages,
    build_compact_cognitive_state_messages,
    build_compact_long_term_memory_messages,
    build_long_term_memory_messages,
    layered_summary_text,
    memory_scope_targets,
    recent_memory_groups,
)


class MemoryLlmContextTests(unittest.TestCase):
    def test_layered_summary_prefers_session_person_room_guild_order(self) -> None:
        summary = layered_summary_text(
            {
                "guild": {"label": "서버 기억", "summary": "guild"},
                "room": {"label": "방 기억", "summary": "room"},
                "person": {"label": "이 사람 기억", "summary": "person"},
                "session": {"label": "현재 세션 기억", "summary": "session"},
            }
        )

        self.assertEqual(
            summary.splitlines(),
            [
                "- 현재 세션 기억: session",
                "- 이 사람 기억: person",
                "- 방 기억: room",
                "- 서버 기억: guild",
            ],
        )

    def test_recent_memory_groups_dedupes_and_limits_rows(self) -> None:
        layers = {
            "guild": {
                "raw": [{"text": "old", "saved_at": 1}, {"text": "same", "saved_at": 2}],
                "facts": [{"text": "fact", "saved_at": 1}],
                "questions": [{"text": "q1", "saved_at": 1}],
            },
            "session": {
                "raw": [{"text": "same", "saved_at": 3}, {"text": "new", "saved_at": 4}],
                "facts": [{"text": "fact2", "saved_at": 2}],
                "questions": [{"text": "q2", "saved_at": 2}],
            },
        }

        groups = recent_memory_groups(layers, raw_limit=2, facts_limit=1, questions_limit=1)

        self.assertEqual([row["text"] for row in groups["raw"]], ["same", "new"])
        self.assertEqual([row["text"] for row in groups["facts"]], ["fact2"])
        self.assertEqual([row["text"] for row in groups["questions"]], ["q2"])

    def test_cognitive_messages_keep_json_contract_and_user_context(self) -> None:
        messages = build_cognitive_state_messages(
            current_state={"action": "answer"},
            current_summary="- 서버 기억: 이블린",
            recent_raw=[{"speaker": "정훈", "source": "text", "text": "main.py 계속 분리"}],
            recent_facts=[{"type": "decision", "text": "작게 분리"}],
            recent_questions=[{"type": "next", "text": "다음 후보"}],
            user_text="계속해",
            raw_limit=4,
        )

        self.assertEqual([message["role"] for message in messages], ["system", "user"])
        self.assertIn('"action": "answer|ask|wait|search_then_answer"', messages[0]["content"])
        self.assertIn("이전 cognitive_state", messages[1]["content"])
        self.assertIn("main.py 계속 분리", messages[1]["content"])
        self.assertIn("현재 사용자 입력:\n계속해", messages[1]["content"])

    def test_compact_messages_keep_same_system_contract(self) -> None:
        cognitive = build_compact_cognitive_state_messages(current_summary="summary", user_text="hello")
        longterm = build_compact_long_term_memory_messages(current_summary="summary", user_text="u", answer="a")

        self.assertIn("실시간 대화 조율자", cognitive[0]["content"])
        self.assertIn("현재 사용자 입력:\nhello", cognitive[1]["content"])
        self.assertIn("대화 장기기억 관리자", longterm[0]["content"])
        self.assertIn("새 대화:\n- user: u\n- assistant: a", longterm[1]["content"])

    def test_long_term_messages_and_scope_targets(self) -> None:
        messages = build_long_term_memory_messages(
            current_summary="summary",
            recent_raw=[],
            recent_facts=[],
            recent_questions=[],
            user_text="내 설정 기억해줘",
            answer="기억할게",
            raw_limit=8,
        )

        self.assertIn('"summary_update": string', messages[0]["content"])
        self.assertIn("새 대화:", messages[1]["content"])
        self.assertEqual(
            memory_scope_targets(room_key="room", person_key="person", session_memory_key="session"),
            [("guild", None), ("room", "room"), ("person", "person"), ("session", "session")],
        )


if __name__ == "__main__":
    unittest.main()
