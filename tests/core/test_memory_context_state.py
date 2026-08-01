import contextlib
import sys
import unittest
from pathlib import Path
from unittest.mock import patch


REPO_ROOT = next(path for path in Path(__file__).resolve().parents if (path / "main.py").exists())
RUNTIME_ROOT = REPO_ROOT / "evelyn_core" / "runtime"
if str(RUNTIME_ROOT) not in sys.path:
    sys.path.insert(0, str(RUNTIME_ROOT))

from evelyn_core.memory_context_state import (  # noqa: E402
    build_memory_context,
    build_memory_context_payload,
    format_memory_row_lines,
    merge_recent_memory_rows,
)
from evelyn_core.memory_content_free_ids import (  # noqa: E402
    memory_content_free_id,
)
from evelyn_core.memory_deletion_journal import MemoryDeletionPosition  # noqa: E402
from evelyn_core.memory_deletion_outbound import (  # noqa: E402
    current_memory_deletion_outbound_position,
    reset_memory_deletion_outbound_position,
)


class MemoryContextStateTests(unittest.TestCase):
    def setUp(self) -> None:
        self.deletion_position = MemoryDeletionPosition(
            schema="memory.deletion.position.v1",
            root_digest="a" * 64,
            sequence=7,
            position_digest="b" * 64,
        )

        @contextlib.contextmanager
        def fake_guard(*_args, **_kwargs):
            yield self.deletion_position

        self.deletion_guard = patch(
            "evelyn_core.memory_context_state.memory_deletion_journal_guard",
            side_effect=fake_guard,
        )
        self.deletion_guard.start()
        self.addCleanup(self.deletion_guard.stop)
        self.addCleanup(reset_memory_deletion_outbound_position)

    def test_merge_recent_memory_rows_sorts_and_limits(self) -> None:
        rows = merge_recent_memory_rows(
            [{"text": "old", "saved_at": 1}, {"text": "new", "saved_at": 3}],
            [{"text": "mid", "saved_at": 2}],
            limit=2,
        )

        self.assertEqual([row["text"] for row in rows], ["mid", "new"])

    def test_format_memory_row_lines_keeps_speaker_source_and_text(self) -> None:
        text = format_memory_row_lines(
            [
                {"speaker": "정훈", "source": "voice", "text": " 안녕 "},
                {"role": "assistant", "text": ""},
            ]
        )

        self.assertEqual(text, "- 정훈 (voice): 안녕")

    def test_build_memory_context_payload_renders_layered_sections(self) -> None:
        context = build_memory_context_payload(
            layers={
                "session": {
                    "label": "현재 세션 기억",
                    "summary": "작업 중",
                    "raw": [{"speaker": "user", "source": "text", "text": "main.py 분리", "saved_at": 2}],
                },
                "guild": {
                    "label": "서버 기억",
                    "summary": "이블린 프로젝트",
                    "raw": [{"speaker": "assistant", "source": "text", "text": "테스트 확인", "saved_at": 1}],
                },
            },
            state={
                "action": "ask",
                "user_intent": "분리 계속",
                "retrieved_context_ids": ["a", "b", "c", "d", "e"],
            },
            session_state={"last_speaker": "정훈", "awaiting_user_reply": True, "topic_id": "topic-1"},
            vault_context="vault hit",
            facts=[{"text": "주요 결정"}],
            questions=[{"text": "남은 후보"}],
            vault_raw_rows=[{"speaker": "user", "source": "vault", "text": "이전 기록", "saved_at": 3}],
        )

        self.assertIn("미확인 과거 작업 요약(확인 전용):", context)
        self.assertIn("미확인 현재 세션 과거 대화(확인 전용):", context)
        self.assertIn("미확인 방 과거 대화(확인 전용):", context)
        self.assertIn("현재 내부 상태(사용자 발화 아님):", context)
        self.assertIn("- 권장 행동: 질문하기", context)
        self.assertIn("미확인 Structured memory vault recall(확인 전용):\nvault hit", context)
        self.assertIn("미확인 장기 기억 후보(확인 전용):\n- 주요 결정", context)
        self.assertIn("미확인 열린 질문/가설(확인 전용):\n- 남은 후보", context)

    def test_build_memory_context_collects_sources_and_vault_recall(self) -> None:
        layers = {
            "guild": {
                "label": "서버 기억",
                "summary": "이블린 프로젝트",
                "raw": [{"speaker": "assistant", "source": "text", "text": "테스트 확인", "saved_at": 1}],
                "facts": [{"text": "작업은 작게 분리한다", "saved_at": 2}],
                "questions": [{"text": "다음 작업 후보 확인", "saved_at": 3}],
                "vault_raw": [{"speaker": "user", "source": "vault", "text": "작업 기록", "saved_at": 4}],
            }
        }

        with patch("evelyn_core.memory_context_state.collect_memory_layers", return_value=layers):
            with patch("evelyn_core.memory_context_state.build_memory_vault_context", return_value="vault ctx") as vault:
                context = build_memory_context(
                    123,
                    "작업 계속",
                    cognitive_state={"action": "answer", "user_intent": "작업 계속", "state_summary": "분리 중"},
                    session_key="session-1",
                    session_state={"topic_id": "topic-1", "last_speaker": "정훈"},
                )

        self.assertIn("미확인 Structured memory vault recall(확인 전용):\nvault ctx", context)
        self.assertIn("미확인 장기 기억 후보(확인 전용):\n- 작업은 작게 분리한다", context)
        self.assertIn("미확인 열린 질문/가설(확인 전용):\n- 다음 작업 후보 확인", context)
        self.assertIn("미확인 문서 보관함 과거 대화(확인 전용):\n- user (vault): 작업 기록", context)
        self.assertIn("- 현재 topic_id: topic-1", context)
        vault.assert_called_once()

    def test_build_memory_context_emits_content_free_grounding_receipt(self) -> None:
        layers = {
            "guild": {
                "label": "서버 기억",
                "summary": "private summary text",
                "raw": [],
                "facts": [],
                "questions": [],
                "vault_raw": [],
            }
        }

        def fake_vault_context(*_args, receipt=None, **_kwargs):
            receipt.update(
                {
                    "state": "provided",
                    "memoryVersion": 7,
                    "retrievalMode": "fts+vector",
                    "cacheHit": False,
                    "hotContextState": "provided",
                    "suppliedNoteIds": ["note-2", "note-1"],
                    "sourceTypeCounts": {"conversation": 2},
                }
            )
            return "private vault text"

        receipt = {}
        with patch("evelyn_core.memory_context_state.collect_memory_layers", return_value=layers):
            with patch(
                "evelyn_core.memory_context_state.build_memory_vault_context",
                side_effect=fake_vault_context,
            ):
                context = build_memory_context(
                    123,
                    "private user text",
                    cognitive_state={"action": "answer"},
                    receipt=receipt,
                )

        self.assertIn("private vault text", context)
        self.assertEqual(receipt["schema"], "memory.context-receipt.v1")
        self.assertEqual(receipt["state"], "provided")
        self.assertEqual(receipt["groundingState"], "partial")
        self.assertEqual(receipt["usePolicy"], "memory.context-use.v1")
        self.assertEqual(receipt["confirmOnlyItemCount"], 1)
        self.assertEqual(receipt["legacyConfirmOnlyItemCount"], 1)
        self.assertFalse(receipt["vaultConfirmOnly"])
        self.assertEqual(receipt["suppliedNoteIds"], ["note-1", "note-2"])
        self.assertEqual(receipt["legacyItemCount"], 1)
        self.assertTrue(receipt["contentFree"])
        self.assertEqual(
            receipt["deletionBoundary"],
            {
                "schema": "memory.deletion.position.v1",
                "state": "captured",
                "sequence": 7,
                "positionDigest": "b" * 64,
                "contentFree": True,
            },
        )
        self.assertIs(
            current_memory_deletion_outbound_position(),
            self.deletion_position,
        )
        self.assertNotIn("a" * 64, str(receipt))
        self.assertNotIn("private", str(receipt).lower())

    def test_new_raw_turn_rows_are_attributed_to_stable_evidence(self) -> None:
        layers = {
            "guild": {
                "label": "서버 기억",
                "summary": "",
                "raw": [
                    {
                        "role": "user",
                        "source": "voice",
                        "text": "PRIVATE_RAW_TEXT",
                        "saved_at": 1,
                        "evidence_id": "turn:abc123:user",
                        "source_turn_id": "abc123",
                        "evidence_kind": "conversation_turn",
                    }
                ],
                "facts": [],
                "questions": [],
                "vault_raw": [],
            }
        }

        def empty_vault(*_args, receipt=None, **_kwargs):
            receipt.update(
                {
                    "state": "empty",
                    "memoryVersion": 1,
                    "suppliedNoteIds": [],
                }
            )
            return ""

        receipt = {}
        with patch("evelyn_core.memory_context_state.collect_memory_layers", return_value=layers):
            with patch(
                "evelyn_core.memory_context_state.build_memory_vault_context",
                side_effect=empty_vault,
            ):
                context = build_memory_context(
                    123,
                    "raw",
                    cognitive_state={},
                    receipt=receipt,
                )

        self.assertIn("PRIVATE_RAW_TEXT", context)
        self.assertIn("근거 연결된 방 최근 대화:", context)
        self.assertNotIn("확인 전용", context)
        self.assertEqual(receipt["groundingState"], "attributed")
        self.assertEqual(receipt["confirmOnlyItemCount"], 0)
        self.assertEqual(receipt["legacyAttributedItemCount"], 1)
        self.assertEqual(receipt["legacyUnattributedItemCount"], 0)
        self.assertEqual(
            receipt["legacyEvidenceIds"],
            [
                memory_content_free_id(
                    "turn:abc123:user",
                    namespace="evidence",
                )
            ],
        )
        self.assertEqual(receipt["legacySourceEvidenceIds"], [])
        self.assertEqual(
            receipt["legacySourceTurnIds"],
            [
                memory_content_free_id(
                    "abc123",
                    namespace="turn",
                )
            ],
        )
        self.assertNotIn("PRIVATE_RAW_TEXT", str(receipt))

    def test_derived_legacy_items_report_content_free_input_lineage(self) -> None:
        layers = {
            "guild": {
                "label": "서버 기억",
                "summary": "PRIVATE_SUMMARY_TEXT",
                "summary_provenance": {
                    "evidence_id": "memory:summary:new",
                    "evidence_kind": "derived_summary",
                    "source_evidence_ids": ["turn:source-a:user"],
                    "source_turn_ids": ["source-a"],
                },
                "raw": [],
                "facts": [
                    {
                        "text": "PRIVATE_FACT_TEXT",
                        "saved_at": 2,
                        "evidence_id": "memory:fact:new",
                        "evidence_kind": "derived_fact",
                        "source_evidence_ids": ["memory:summary:new", "turn:source-b:user"],
                        "source_turn_ids": ["source-a", "source-b"],
                    }
                ],
                "questions": [
                    {
                        "text": "PRIVATE_QUESTION_TEXT",
                        "saved_at": 3,
                        "evidence_id": "memory:question:new",
                        "evidence_kind": "derived_question",
                        "source_evidence_ids": ["turn:source-b:assistant"],
                        "source_turn_ids": ["source-b"],
                    }
                ],
                "vault_raw": [],
            }
        }

        def empty_vault(*_args, receipt=None, **_kwargs):
            receipt.update({"state": "empty", "memoryVersion": 1, "suppliedNoteIds": []})
            return ""

        receipt = {}
        with patch("evelyn_core.memory_context_state.collect_memory_layers", return_value=layers):
            with patch(
                "evelyn_core.memory_context_state.build_memory_vault_context",
                side_effect=empty_vault,
            ):
                context = build_memory_context(
                    123,
                    "PRIVATE",
                    cognitive_state={},
                    receipt=receipt,
                )

        self.assertIn("PRIVATE_SUMMARY_TEXT", context)
        self.assertIn("근거 연결된 현재 작업 요약", context)
        self.assertIn("근거 연결된 장기 기억 후보", context)
        self.assertNotIn("확인 전용", context)
        self.assertEqual(receipt["groundingState"], "attributed")
        self.assertEqual(receipt["confirmOnlyItemCount"], 0)
        self.assertEqual(receipt["legacyAttributedItemCount"], 3)
        self.assertEqual(receipt["legacyUnattributedItemCount"], 0)
        self.assertEqual(
            receipt["legacyEvidenceIds"],
            sorted(
                memory_content_free_id(
                    value,
                    namespace="evidence",
                )
                for value in (
                    "memory:fact:new",
                    "memory:question:new",
                    "memory:summary:new",
                )
            ),
        )
        self.assertEqual(
            receipt["legacySourceEvidenceIds"],
            sorted(
                memory_content_free_id(
                    value,
                    namespace="evidence",
                )
                for value in (
                    "memory:summary:new",
                    "turn:source-a:user",
                    "turn:source-b:assistant",
                    "turn:source-b:user",
                )
            ),
        )
        self.assertEqual(
            receipt["legacySourceTurnIds"],
            sorted(
                memory_content_free_id(
                    value,
                    namespace="turn",
                )
                for value in ("source-a", "source-b")
            ),
        )
        self.assertNotIn("PRIVATE_", str(receipt))

    def test_invalid_derived_provenance_remains_unattributed(self) -> None:
        layers = {
            "guild": {
                "label": "서버 기억",
                "summary": "summary",
                "summary_provenance": {
                    "evidence_id": "memory:summary:invalid",
                    "evidence_kind": "derived_summary",
                    "source_evidence_ids": [],
                },
                "raw": [],
                "facts": [
                    {
                        "text": "fact",
                        "saved_at": 1,
                        "evidence_id": "not valid evidence",
                        "evidence_kind": "derived_fact",
                        "source_evidence_ids": ["turn:source:user"],
                    }
                ],
                "questions": [],
                "vault_raw": [],
            }
        }

        receipt = {}
        with patch("evelyn_core.memory_context_state.collect_memory_layers", return_value=layers):
            with patch("evelyn_core.memory_context_state.build_memory_vault_context", return_value=""):
                context = build_memory_context(123, "fact", cognitive_state={}, receipt=receipt)

        self.assertIn("미확인 과거 작업 요약(확인 전용):", context)
        self.assertIn("미확인 장기 기억 후보(확인 전용):", context)
        self.assertEqual(receipt["groundingState"], "unattributed")
        self.assertEqual(receipt["confirmOnlyItemCount"], 2)
        self.assertEqual(receipt["legacyConfirmOnlyItemCount"], 2)
        self.assertEqual(receipt["legacyAttributedItemCount"], 0)
        self.assertEqual(receipt["legacyUnattributedItemCount"], 2)
        self.assertEqual(receipt["legacyEvidenceIds"], [])
        self.assertEqual(receipt["legacySourceEvidenceIds"], [])

    def test_empty_payload_returns_empty_string(self) -> None:
        self.assertEqual(
            build_memory_context_payload(
                layers={},
                state={},
                session_state={},
                vault_context="",
                facts=[],
                questions=[],
                vault_raw_rows=[],
            ),
            "",
        )

    def test_context_guard_covers_legacy_and_vault_reads(self) -> None:
        active = False

        @contextlib.contextmanager
        def observing_guard(*_args, **_kwargs):
            nonlocal active
            active = True
            try:
                yield self.deletion_position
            finally:
                active = False

        def collect(*_args, **_kwargs):
            self.assertTrue(active)
            return {}

        def vault(*_args, receipt=None, **_kwargs):
            self.assertTrue(active)
            receipt.update({"state": "empty"})
            return ""

        receipt: dict[str, object] = {}
        with patch(
            "evelyn_core.memory_context_state.memory_deletion_journal_guard",
            side_effect=observing_guard,
        ):
            with patch(
                "evelyn_core.memory_context_state.collect_memory_layers",
                side_effect=collect,
            ):
                with patch(
                    "evelyn_core.memory_context_state.build_memory_vault_context",
                    side_effect=vault,
                ):
                    context = build_memory_context(
                        123,
                        "no memory",
                        cognitive_state={},
                        receipt=receipt,
                    )

        self.assertFalse(active)
        self.assertEqual(context, "")
        self.assertEqual(
            receipt["deletionBoundary"]["state"],
            "not_required",
        )
        self.assertIsNone(current_memory_deletion_outbound_position())


if __name__ == "__main__":
    unittest.main()
