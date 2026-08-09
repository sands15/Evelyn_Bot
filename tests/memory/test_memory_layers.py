from __future__ import annotations

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
from evelyn_core.memory_context_state import (  # noqa: E402
    build_memory_context_payload,
)
from evelyn_core.memory_layers import collect_memory_layers  # noqa: E402
from evelyn_core.memory_llm_context import (  # noqa: E402
    build_cognitive_state_messages,
    build_long_term_memory_messages,
    recent_memory_groups,
)


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


class MemoryLayersTests(unittest.TestCase):
    def test_raw_writer_redacts_daily_mirror_failure(self) -> None:
        private_error = "PRIVATE_TOKEN=C:/private/memory-note.md"
        with (
            TemporaryMemoryRoot(),
            patch.object(
                memory,
                "append_turn_rows_to_memory_vault",
                side_effect=RuntimeError(private_error),
            ),
            patch("builtins.print") as log,
        ):
            memory.append_raw_transcript_rows(
                123,
                [{"role": "user", "text": "hello"}],
            )

        log.assert_called_once_with(
            "[MEMORY VAULT] daily mirror failed: errorType=RuntimeError"
        )
        self.assertNotIn(private_error, str(log.call_args))

    def test_collect_memory_layers_reads_all_requested_scopes(self) -> None:
        with TemporaryMemoryRoot():
            scopes = [
                ("guild", None, "공용 방 기억", "guild summary"),
                ("room", "room-1", "방 기억", "room summary"),
                ("person", "person-1", "이 사람 기억", "person summary"),
                ("session", "session-1", "현재 세션 기억", "session summary"),
            ]
            for scope_type, scope_key, _label, summary in scopes:
                if scope_type == "guild":
                    memory.write_memory_summary_with_provenance(
                        123,
                        summary,
                        evidence_id="memory:summary:guild",
                        source_evidence_ids=["turn:guild-turn:user"],
                        source_turn_ids=["guild-turn"],
                        scope_type=scope_type,
                        scope_key=scope_key,
                    )
                else:
                    memory.write_text_file(
                        memory.memory_summary_path(123, scope_type=scope_type, scope_key=scope_key),
                        summary,
                    )
                memory.append_raw_transcript_rows(
                    123,
                    [
                        {
                            "role": "user",
                            "speaker": scope_type,
                            "source": "test",
                            "text": f"{scope_type} raw",
                            "evidence_id": f"turn:{scope_type}-turn:user",
                            "source_turn_id": f"{scope_type}-turn",
                            "evidence_kind": "conversation_turn",
                            "private_metadata": "must-not-survive",
                        },
                        {
                            "role": "assistant",
                            "speaker": "Evelyn",
                            "source": "test",
                            "text": f"{scope_type} assistant raw",
                            "evidence_id": f"turn:{scope_type}-turn:assistant",
                            "source_turn_id": f"{scope_type}-turn",
                            "evidence_kind": "conversation_turn",
                        },
                    ],
                    scope_type=scope_type,
                    scope_key=scope_key,
                )
                memory.append_unique_memory_rows(
                    memory.memory_facts_path(123, scope_type=scope_type, scope_key=scope_key),
                    [{"type": "fact", "text": f"{scope_type} fact"}],
                    20,
                    mirror_path=memory.vault_facts_path(123, scope_type=scope_type, scope_key=scope_key),
                )
                memory.append_unique_memory_rows(
                    memory.memory_questions_path(123, scope_type=scope_type, scope_key=scope_key),
                    [
                        {
                            "type": "question",
                            "text": f"{scope_type} question",
                            "evidence_id": f"memory:question:{scope_type}",
                            "evidence_kind": "derived_question",
                            "source_evidence_ids": [f"turn:{scope_type}-turn:user"],
                            "source_turn_ids": [f"{scope_type}-turn"],
                        }
                    ],
                    20,
                    mirror_path=memory.vault_questions_path(123, scope_type=scope_type, scope_key=scope_key),
                )

            layers = collect_memory_layers(
                123,
                room_key="room-1",
                person_key="person-1",
                session_memory_key="session-1",
            )
            stored_questions = {
                scope_type: (
                    memory.read_jsonl(
                        memory.memory_questions_path(
                            123,
                            scope_type=scope_type,
                            scope_key=scope_key,
                        )
                    ),
                    memory.read_jsonl(
                        memory.vault_questions_path(
                            123,
                            scope_type=scope_type,
                            scope_key=scope_key,
                        )
                    ),
                )
                for scope_type, scope_key, _label, _summary in scopes
            }

        self.assertEqual(list(layers), ["guild", "room", "person", "session"])
        for key, (_scope_type, scope_key, label, summary) in zip(layers, scopes):
            layer = layers[key]
            self.assertEqual(layer["label"], label)
            self.assertEqual(layer["scope_key"], scope_key)
            self.assertEqual(layer["summary"], "")
            self.assertEqual(layer["summary_provenance"], {})
            self.assertEqual(len(layer["raw"]), 1)
            self.assertEqual(len(layer["vault_raw"]), 1)
            self.assertEqual(layer["raw"][0]["text"], f"{key} raw")
            self.assertEqual(layer["vault_raw"][0]["text"], f"{key} raw")
            self.assertEqual(layer["raw"][0]["evidence_id"], f"turn:{key}-turn:user")
            self.assertEqual(layer["vault_raw"][0]["source_turn_id"], f"{key}-turn")
            self.assertNotIn("private_metadata", layer["raw"][0])
            self.assertEqual(layer["facts"], [])
            self.assertEqual(layer["questions"], [])
            hot_questions, mirrored_questions = stored_questions[key]
            self.assertEqual(hot_questions[0]["text"], f"{key} question")
            self.assertEqual(mirrored_questions[0]["text"], f"{key} question")

    def test_unreceipted_derived_layers_are_absent_from_prompts(self) -> None:
        stale_summary = "PRIVATE_STALE_SUMMARY"
        stale_answer = "PRIVATE_STALE_ASSISTANT_ANSWER"
        stale_fact = "PRIVATE_STALE_FACT"
        stale_question = "PRIVATE_STALE_QUESTION"
        safe_user = "CURRENT_USER_EVIDENCE"
        current_question = "DIRECT_CURRENT_TURN_QUESTION?"
        with TemporaryMemoryRoot():
            memory.write_memory_summary_with_provenance(
                123,
                stale_summary,
                evidence_id="memory:summary:stale",
                source_evidence_ids=["turn:deleted-source:assistant"],
                source_turn_ids=["deleted-source"],
            )
            memory.append_raw_transcript_rows(
                123,
                [
                    {
                        "role": "user",
                        "speaker": "user",
                        "source": "text",
                        "text": safe_user,
                        "evidence_id": "turn:current-source:user",
                        "source_turn_id": "current-source",
                        "evidence_kind": "conversation_turn",
                    },
                    {
                        "role": "assistant",
                        "speaker": "Evelyn",
                        "source": "text",
                        "text": stale_answer,
                        "evidence_id": "turn:deleted-source:assistant",
                        "source_turn_id": "deleted-source",
                        "evidence_kind": "conversation_turn",
                    },
                ],
            )
            memory.append_unique_memory_rows(
                memory.memory_facts_path(123),
                [
                    {
                        "type": "fact",
                        "text": stale_fact,
                        "evidence_id": "memory:fact:stale",
                        "evidence_kind": "derived_fact",
                        "source_evidence_ids": ["turn:deleted-source:assistant"],
                        "source_turn_ids": ["deleted-source"],
                    }
                ],
                20,
                mirror_path=memory.vault_facts_path(123),
            )
            memory.append_unique_memory_rows(
                memory.memory_questions_path(123),
                [
                    {
                        "type": "question",
                        "text": stale_question,
                        "evidence_id": "memory:question:stale",
                        "evidence_kind": "derived_question",
                        "source_evidence_ids": ["turn:deleted-source:assistant"],
                        "source_turn_ids": ["deleted-source"],
                    }
                ],
                20,
                mirror_path=memory.vault_questions_path(123),
            )
            layers = collect_memory_layers(123)

        recent = recent_memory_groups(
            layers,
            raw_limit=4,
            facts_limit=4,
            questions_limit=4,
        )
        context = build_memory_context_payload(
            layers=layers,
            state={},
            session_state={},
            vault_context="",
            facts=recent["facts"],
            questions=recent["questions"],
            vault_raw_rows=[],
        )
        cognitive_prompt = build_cognitive_state_messages(
            current_state={},
            current_summary="",
            recent_raw=recent["raw"],
            recent_facts=recent["facts"],
            recent_questions=recent["questions"],
            user_text="current user turn",
            raw_limit=4,
        )
        writeback_prompt = build_long_term_memory_messages(
            current_summary="",
            recent_raw=recent["raw"],
            recent_facts=recent["facts"],
            recent_questions=recent["questions"],
            user_text="current user turn",
            answer=current_question,
            raw_limit=4,
        )

        self.assertEqual(layers["guild"]["summary"], "")
        self.assertEqual(layers["guild"]["facts"], [])
        self.assertEqual(layers["guild"]["questions"], [])
        self.assertEqual(
            [row["text"] for row in layers["guild"]["raw"]],
            [safe_user],
        )
        self.assertEqual(recent["questions"], [])
        for stale in (
            stale_summary,
            stale_answer,
            stale_fact,
            stale_question,
        ):
            self.assertNotIn(stale, context)
            self.assertNotIn(stale, str(cognitive_prompt))
            self.assertNotIn(stale, str(writeback_prompt))
        self.assertIn(safe_user, str(cognitive_prompt))
        self.assertIn(current_question, str(writeback_prompt))

    def test_collect_memory_layers_only_includes_requested_optional_scopes(self) -> None:
        with TemporaryMemoryRoot():
            layers = collect_memory_layers(123)

        self.assertEqual(list(layers), ["guild"])
        self.assertEqual(layers["guild"]["label"], "공용 방 기억")
        self.assertEqual(layers["guild"]["raw"], [])
        self.assertEqual(layers["guild"]["summary_provenance"], {})

    def test_collect_memory_layers_withholds_stored_summary(self) -> None:
        with TemporaryMemoryRoot():
            memory.write_memory_summary_with_provenance(
                123,
                "해시로 묶인 요약",
                evidence_id="memory:summary:bound",
                source_evidence_ids=["turn:source:user"],
                source_turn_ids=["source"],
            )
            memory.write_text_file(memory.memory_summary_path(123), "다른 요약")

            layers = collect_memory_layers(123)

        self.assertEqual(layers["guild"]["summary"], "")
        self.assertEqual(layers["guild"]["summary_provenance"], {})

    def test_raw_writer_drops_mismatched_turn_evidence_tuple(self) -> None:
        with TemporaryMemoryRoot():
            memory.append_raw_transcript_rows(
                123,
                [
                    {
                        "role": "assistant",
                        "speaker": "이블린",
                        "source": "text",
                        "text": "응답",
                        "evidence_id": "turn:turn-1:user",
                        "source_turn_id": "turn-1",
                        "evidence_kind": "conversation_turn",
                    }
                ],
            )

            row = memory.read_jsonl(memory.memory_raw_path(123))[0]

        self.assertNotIn("evidence_id", row)
        self.assertNotIn("source_turn_id", row)
        self.assertNotIn("evidence_kind", row)


if __name__ == "__main__":
    unittest.main()
