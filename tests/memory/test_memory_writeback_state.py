from __future__ import annotations

import asyncio
import sys
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory


REPO_ROOT = next(path for path in Path(__file__).resolve().parents if (path / "main.py").exists())
RUNTIME_ROOT = REPO_ROOT / "evelyn_core" / "runtime"
if str(RUNTIME_ROOT) not in sys.path:
    sys.path.insert(0, str(RUNTIME_ROOT))

import evelyn_core.memory as memory  # noqa: E402
from evelyn_core.memory_writeback_state import apply_long_term_memory_result, run_long_term_memory_update  # noqa: E402
from evelyn_core.proactive_questions import load_proactive_questions  # noqa: E402


class TemporaryMemoryRoot:
    def __init__(self) -> None:
        self.tmp = TemporaryDirectory()
        self.old_root = memory.MEMORY_ROOT

    def __enter__(self) -> Path:
        memory.MEMORY_ROOT = Path(self.tmp.name)
        return memory.MEMORY_ROOT

    def __exit__(self, exc_type, exc, tb) -> None:
        memory.MEMORY_ROOT = self.old_root
        self.tmp.cleanup()


class MemoryWritebackStateTests(unittest.TestCase):
    def _layers(self) -> dict:
        return {
            "guild": {
                "label": "서버 기억",
                "summary": "기존 요약",
                "raw": [{"speaker": "정훈", "text": "이전 대화", "saved_at": 1}],
                "facts": [{"type": "decision", "text": "작게 분리", "saved_at": 2}],
                "questions": [{"type": "next", "text": "다음 후보", "saved_at": 3}],
            }
        }

    def test_apply_long_term_memory_result_writes_all_scopes_and_mirrors(self) -> None:
        result = {
            "summary_update": "  새 요약  ",
            "durable_facts": [{"type": "preference", "text": "작게 분리한다"}],
            "open_questions": [{"type": "next", "text": "다음 후보 확인 필요"}],
        }

        with TemporaryMemoryRoot():
            applied = apply_long_term_memory_result(
                123,
                result,
                room_key="room-1",
                person_key="person-1",
                session_memory_key="session-1",
                memory_fact_limit=20,
                memory_loop_limit=20,
            )

            scopes = [
                ("guild", None),
                ("room", "room-1"),
                ("person", "person-1"),
                ("session", "session-1"),
            ]
            summaries = [
                memory.read_text_file(memory.memory_summary_path(123, scope_type=scope_type, scope_key=scope_key))
                for scope_type, scope_key in scopes
            ]
            facts = memory.read_jsonl(memory.memory_facts_path(123))
            mirrored_facts = memory.read_jsonl(memory.vault_facts_path(123))
            questions = memory.read_jsonl(memory.memory_questions_path(123))
            mirrored_questions = memory.read_jsonl(memory.vault_questions_path(123))
            proactive = load_proactive_questions(123)

        self.assertEqual(applied["scope_count"], 4)
        self.assertEqual(applied["summary_written"], 4)
        self.assertEqual(applied["facts_written"], 4)
        self.assertEqual(applied["questions_written"], 4)
        self.assertEqual(summaries, ["새 요약"] * 4)
        self.assertEqual(facts[0]["text"], "작게 분리한다")
        self.assertEqual(mirrored_facts[0]["text"], "작게 분리한다")
        self.assertEqual(questions[0]["text"], "다음 후보 확인 필요")
        self.assertEqual(mirrored_questions[0]["text"], "다음 후보 확인 필요")
        self.assertEqual(len(proactive), 1)

    def test_apply_long_term_memory_result_ignores_invalid_sections(self) -> None:
        with TemporaryMemoryRoot():
            applied = apply_long_term_memory_result(
                123,
                {"summary_update": "", "durable_facts": "bad", "open_questions": [None, "bad"]},
                memory_fact_limit=20,
                memory_loop_limit=20,
            )

            summary = memory.read_text_file(memory.memory_summary_path(123))
            facts = memory.read_jsonl(memory.memory_facts_path(123))
            questions = memory.read_jsonl(memory.memory_questions_path(123))

        self.assertEqual(applied["summary_written"], 0)
        self.assertEqual(applied["facts_written"], 0)
        self.assertEqual(applied["questions_written"], 0)
        self.assertEqual(summary, "")
        self.assertEqual(facts, [])
        self.assertEqual(questions, [])

    def test_run_long_term_memory_update_builds_context_and_writes_result(self) -> None:
        calls = []
        logs = []
        ticks = iter([10.0, 10.25])

        async def fake_ask(messages, **kwargs):
            calls.append((messages, kwargs))
            return {
                "summary_update": "새 장기기억 요약",
                "durable_facts": [{"type": "decision", "text": "writebehind 모듈 분리"}],
                "open_questions": [],
            }

        with TemporaryMemoryRoot():
            outcome = asyncio.run(
                run_long_term_memory_update(
                    123,
                    "계속해",
                    "분리했어",
                    room_key="room-1",
                    collect_layers=lambda *args, **kwargs: self._layers(),
                    ask_summary_llm=fake_ask,
                    is_context_size_error=lambda exc: False,
                    should_log_latency=lambda ms: True,
                    memory_fact_limit=20,
                    memory_loop_limit=20,
                    raw_limit=8,
                    log=logs.append,
                    now=lambda: next(ticks),
                )
            )

            summary = memory.read_text_file(memory.memory_summary_path(123))
            room_summary = memory.read_text_file(memory.memory_summary_path(123, scope_type="room", scope_key="room-1"))
            facts = memory.read_jsonl(memory.memory_facts_path(123))

        self.assertTrue(outcome["ok"])
        self.assertEqual(outcome["applied"]["scope_count"], 2)
        self.assertEqual(summary, "새 장기기억 요약")
        self.assertEqual(room_summary, "새 장기기억 요약")
        self.assertEqual(facts[0]["text"], "writebehind 모듈 분리")
        self.assertEqual(calls[0][1]["purpose"], "memory_summary")
        self.assertIn("현재 layered_summary", calls[0][0][1]["content"])
        self.assertTrue(any("[MEMORY LATENCY] guild=123 scope=room-1 ms=250" in line for line in logs))

    def test_run_long_term_memory_update_uses_compact_retry_for_context_size_errors(self) -> None:
        class ContextTooLarge(RuntimeError):
            pass

        calls = []
        logs = []

        async def fake_ask(messages, **kwargs):
            calls.append((messages, kwargs))
            if len(calls) == 1:
                raise ContextTooLarge("too much context")
            return {"summary_update": "compact 요약", "durable_facts": [], "open_questions": []}

        with TemporaryMemoryRoot():
            outcome = asyncio.run(
                run_long_term_memory_update(
                    123,
                    "긴 입력",
                    "긴 답변",
                    collect_layers=lambda *args, **kwargs: self._layers(),
                    ask_summary_llm=fake_ask,
                    is_context_size_error=lambda exc: isinstance(exc, ContextTooLarge),
                    should_log_latency=lambda ms: False,
                    memory_fact_limit=20,
                    memory_loop_limit=20,
                    raw_limit=8,
                    log=logs.append,
                )
            )

            summary = memory.read_text_file(memory.memory_summary_path(123))

        self.assertTrue(outcome["ok"])
        self.assertEqual(len(calls), 2)
        self.assertEqual(calls[1][1]["max_tokens"], 220)
        self.assertIn("새 대화:", calls[1][0][1]["content"])
        self.assertEqual(summary, "compact 요약")
        self.assertIn("[MEMORY] compact retry 성공", logs)


if __name__ == "__main__":
    unittest.main()
