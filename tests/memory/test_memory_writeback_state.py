from __future__ import annotations

import asyncio
import os
import sys
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch


REPO_ROOT = next(path for path in Path(__file__).resolve().parents if (path / "main.py").exists())
RUNTIME_ROOT = REPO_ROOT / "evelyn_core" / "runtime"
if str(RUNTIME_ROOT) not in sys.path:
    sys.path.insert(0, str(RUNTIME_ROOT))

import evelyn_core.memory as memory  # noqa: E402
from evelyn_core.memory_writeback_state import apply_long_term_memory_result, run_long_term_memory_update  # noqa: E402
from evelyn_core import memory_deletion_journal as deletion_journal  # noqa: E402
from evelyn_core.memory_integrity_authenticity import (  # noqa: E402
    MEMORY_INTEGRITY_ANCHOR_DIR_ENV,
    MEMORY_INTEGRITY_BOOTSTRAP_ENV,
    MEMORY_INTEGRITY_KEY_FILE_ENV,
)
from evelyn_core.proactive_questions import load_proactive_questions  # noqa: E402


class TemporaryMemoryRoot:
    def __init__(self) -> None:
        self.tmp = TemporaryDirectory()
        self.old_root = memory.MEMORY_ROOT
        self.auth_environment = patch.dict(
            os.environ,
            {
                MEMORY_INTEGRITY_KEY_FILE_ENV: "",
                MEMORY_INTEGRITY_ANCHOR_DIR_ENV: "",
                MEMORY_INTEGRITY_BOOTSTRAP_ENV: "",
            },
        )

    def __enter__(self) -> Path:
        self.auth_environment.start()
        memory.MEMORY_ROOT = Path(self.tmp.name)
        return memory.MEMORY_ROOT

    def __exit__(self, exc_type, exc, tb) -> None:
        memory.MEMORY_ROOT = self.old_root
        self.auth_environment.stop()
        self.tmp.cleanup()


class MemoryWritebackStateTests(unittest.TestCase):
    def _layers(self) -> dict:
        return {
            "guild": {
                "label": "서버 기억",
                "summary": "기존 요약",
                "summary_provenance": {
                    "evidence_id": "memory:summary:prior",
                    "evidence_kind": "derived_summary",
                    "source_evidence_ids": ["turn:summary-source:user"],
                    "source_turn_ids": ["summary-source"],
                },
                "raw": [
                    {
                        "speaker": "정훈",
                        "text": "이전 대화",
                        "saved_at": 1,
                        "evidence_id": "turn:raw-source:user",
                        "evidence_kind": "conversation_turn",
                        "source_turn_id": "raw-source",
                    }
                ],
                "facts": [
                    {
                        "type": "decision",
                        "text": "작게 분리",
                        "saved_at": 2,
                        "evidence_id": "memory:fact:prior",
                        "evidence_kind": "derived_fact",
                        "source_evidence_ids": ["turn:fact-source:user"],
                        "source_turn_ids": ["fact-source"],
                    }
                ],
                "questions": [
                    {
                        "type": "next",
                        "text": "다음 후보",
                        "saved_at": 3,
                        "evidence_id": "memory:question:prior",
                        "evidence_kind": "derived_question",
                        "source_evidence_ids": ["turn:question-source:user"],
                        "source_turn_ids": ["question-source"],
                    }
                ],
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
                source_evidence_ids=["turn:source:user", "turn:source:assistant"],
                source_turn_ids=["source"],
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
            summary_provenance = [
                memory.read_memory_summary_provenance(
                    123,
                    scope_type=scope_type,
                    scope_key=scope_key,
                )
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
        self.assertEqual(applied["source_evidence_count"], 2)
        self.assertEqual(summaries, ["새 요약"] * 4)
        self.assertEqual(
            {item["evidence_id"] for item in summary_provenance},
            {summary_provenance[0]["evidence_id"]},
        )
        self.assertEqual(summary_provenance[0]["evidence_kind"], "derived_summary")
        self.assertEqual(
            summary_provenance[0]["source_evidence_ids"],
            ["turn:source:user", "turn:source:assistant"],
        )
        self.assertEqual(summary_provenance[0]["source_turn_ids"], ["source"])
        self.assertEqual(facts[0]["text"], "작게 분리한다")
        self.assertEqual(facts[0]["evidence_kind"], "derived_fact")
        self.assertEqual(facts[0]["source_evidence_ids"], ["turn:source:user", "turn:source:assistant"])
        self.assertEqual(facts[0]["source_turn_ids"], ["source"])
        self.assertTrue(facts[0]["evidence_id"].startswith("memory:fact:"))
        self.assertEqual(facts[0]["evidence_id"], mirrored_facts[0]["evidence_id"])
        self.assertEqual(mirrored_facts[0]["text"], "작게 분리한다")
        self.assertEqual(questions[0]["text"], "다음 후보 확인 필요")
        self.assertEqual(questions[0]["evidence_kind"], "derived_question")
        self.assertEqual(questions[0]["source_turn_ids"], ["source"])
        self.assertTrue(questions[0]["evidence_id"].startswith("memory:question:"))
        self.assertEqual(questions[0]["evidence_id"], mirrored_questions[0]["evidence_id"])
        self.assertEqual(mirrored_questions[0]["text"], "다음 후보 확인 필요")
        self.assertEqual(proactive, [])

    def test_summary_provenance_fails_closed_after_summary_content_changes(self) -> None:
        with TemporaryMemoryRoot():
            memory.write_memory_summary_with_provenance(
                123,
                "원래 요약",
                evidence_id="memory:summary:bound",
                source_evidence_ids=["turn:source:user"],
                source_turn_ids=["source"],
            )
            self.assertEqual(
                memory.read_memory_summary_provenance(123)["evidence_id"],
                "memory:summary:bound",
            )

            memory.write_text_file(memory.memory_summary_path(123), "바뀐 요약")
            stale = memory.read_memory_summary_provenance(123)

        self.assertEqual(stale, {})

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
                    source_turn_id="turn-new",
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
            provenance = memory.read_memory_summary_provenance(123)

        self.assertTrue(outcome["ok"])
        self.assertEqual(outcome["applied"]["scope_count"], 2)
        self.assertEqual(summary, "새 장기기억 요약")
        self.assertEqual(room_summary, "새 장기기억 요약")
        self.assertEqual(facts[0]["text"], "writebehind 모듈 분리")
        self.assertEqual(
            provenance["source_evidence_ids"],
            [
                "memory:summary:prior",
                "turn:raw-source:user",
                "memory:fact:prior",
                "memory:question:prior",
                "turn:turn-new:user",
                "turn:turn-new:assistant",
            ],
        )
        self.assertEqual(
            provenance["source_turn_ids"],
            ["summary-source", "raw-source", "fact-source", "question-source", "turn-new"],
        )
        self.assertEqual(calls[0][1]["purpose"], "memory_summary")
        self.assertIsInstance(
            calls[0][1]["memory_deletion_position"],
            deletion_journal.MemoryDeletionPosition,
        )
        self.assertTrue(calls[0][1]["memory_boundary_required"])
        self.assertEqual(
            calls[0][1]["memory_deletion_index_dir"].name,
            "memory_index",
        )
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
                    source_turn_id="turn-compact",
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
            provenance = memory.read_memory_summary_provenance(123)

        self.assertTrue(outcome["ok"])
        self.assertEqual(len(calls), 2)
        self.assertEqual(calls[1][1]["max_tokens"], 220)
        self.assertEqual(
            calls[1][1]["memory_deletion_position"],
            calls[0][1]["memory_deletion_position"],
        )
        self.assertTrue(calls[1][1]["memory_boundary_required"])
        self.assertIn("새 대화:", calls[1][0][1]["content"])
        self.assertEqual(summary, "compact 요약")
        self.assertEqual(
            provenance["source_evidence_ids"],
            [
                "memory:summary:prior",
                "turn:turn-compact:user",
                "turn:turn-compact:assistant",
            ],
        )
        self.assertEqual(
            provenance["source_turn_ids"],
            ["summary-source", "turn-compact"],
        )
        self.assertNotIn("turn:raw-source:user", provenance["source_evidence_ids"])
        self.assertIn("[MEMORY] compact retry 성공", logs)

    def test_summary_retry_failure_logs_only_exception_type(self) -> None:
        private_error = "PRIVATE_SUMMARY_FAILURE C:/secret/memory-token"

        class ContextTooLarge(RuntimeError):
            pass

        calls = 0
        logs = []

        async def fake_ask(_messages, **_kwargs):
            nonlocal calls
            calls += 1
            if calls == 1:
                raise ContextTooLarge("context limit")
            raise RuntimeError(private_error)

        with TemporaryMemoryRoot():
            outcome = asyncio.run(
                run_long_term_memory_update(
                    123,
                    "긴 입력",
                    "긴 답변",
                    collect_layers=lambda *args, **kwargs: self._layers(),
                    ask_summary_llm=fake_ask,
                    is_context_size_error=lambda exc: isinstance(exc, ContextTooLarge),
                    should_log_latency=lambda _ms: False,
                    memory_fact_limit=20,
                    memory_loop_limit=20,
                    raw_limit=8,
                    log=logs.append,
                )
            )

        self.assertFalse(outcome["ok"])
        self.assertEqual(calls, 2)
        self.assertEqual(
            logs,
            [
                "[MEMORY] compact retry 실패: errorType=RuntimeError",
                "[MEMORY] 요약 업데이트 실패: errorType=RuntimeError",
            ],
        )
        self.assertNotIn(private_error, repr(logs))

    def test_current_exchange_without_turn_id_does_not_borrow_prior_evidence(self) -> None:
        async def fake_ask(messages, **kwargs):
            return {
                "summary_update": "근거 없는 현재 대화 요약",
                "durable_facts": [
                    {"type": "preference", "text": "새 선호"}
                ],
                "open_questions": [],
            }

        with TemporaryMemoryRoot():
            outcome = asyncio.run(
                run_long_term_memory_update(
                    123,
                    "현재 발화",
                    "현재 답변",
                    source_turn_id=None,
                    collect_layers=lambda *args, **kwargs: self._layers(),
                    ask_summary_llm=fake_ask,
                    is_context_size_error=lambda exc: False,
                    should_log_latency=lambda ms: False,
                    memory_fact_limit=20,
                    memory_loop_limit=20,
                    raw_limit=8,
                )
            )
            provenance = memory.read_memory_summary_provenance(123)
            facts = memory.read_jsonl(memory.memory_facts_path(123))

        self.assertTrue(outcome["ok"])
        self.assertEqual(outcome["applied"]["source_evidence_count"], 0)
        self.assertEqual(provenance, {})
        self.assertNotIn("source_evidence_ids", facts[0])
        self.assertNotIn("source_turn_ids", facts[0])

    def test_delete_after_summary_response_blocks_stale_memory_apply(self) -> None:
        with TemporaryMemoryRoot() as memory_root:
            index_dir = memory_root / "memory_index"

            async def fake_ask(_messages, **_kwargs):
                deletion_journal.append_memory_deletion_tombstone(
                    index_dir,
                    {
                        "schema": deletion_journal.MEMORY_DELETE_TOMBSTONE_V1_SCHEMA,
                        "noteId": "concept-0123456789abcdef",
                        "noteType": "concept",
                        "sourceType": "conversation",
                        "reason": "privacy_request",
                        "deletedAt": "2026-08-01T00:00:00Z",
                    },
                )
                return {
                    "summary_update": "PRIVATE stale derived summary",
                    "durable_facts": [
                        {"type": "preference", "text": "PRIVATE stale fact"}
                    ],
                    "open_questions": [],
                }

            async def run_in_background_task():
                return await asyncio.create_task(
                    run_long_term_memory_update(
                        123,
                        "current user turn",
                        "current answer",
                        source_turn_id="turn-post-gap",
                        collect_layers=lambda *_args, **_kwargs: self._layers(),
                        ask_summary_llm=fake_ask,
                        is_context_size_error=lambda _exc: False,
                        should_log_latency=lambda _ms: False,
                        memory_fact_limit=20,
                        memory_loop_limit=20,
                        raw_limit=8,
                        memory_index_dir=index_dir,
                    )
                )

            with self.assertRaises(
                deletion_journal.MemoryDeletionJournalIntegrityError
            ) as raised:
                asyncio.run(run_in_background_task())

            summary = memory.read_text_file(memory.memory_summary_path(123))
            facts = memory.read_jsonl(memory.memory_facts_path(123))

        self.assertEqual(
            str(raised.exception),
            deletion_journal.MEMORY_DELETION_JOURNAL_INTEGRITY_ERROR,
        )
        self.assertEqual(summary, "")
        self.assertEqual(facts, [])


if __name__ == "__main__":
    unittest.main()
