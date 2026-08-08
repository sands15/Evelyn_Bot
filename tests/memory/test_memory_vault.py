from __future__ import annotations

import hashlib
import json
import sqlite3
import sys
import tempfile
import unittest
from contextlib import closing
from pathlib import Path
from unittest.mock import patch


REPO_ROOT = next(path for path in Path(__file__).resolve().parents if (path / "main.py").exists())
RUNTIME_ROOT = REPO_ROOT / "evelyn_core" / "runtime"
if str(RUNTIME_ROOT) not in sys.path:
    sys.path.insert(0, str(RUNTIME_ROOT))

from evelyn_core.assistant_contracts import (  # noqa: E402
    MemoryRecallRequest,
    MemoryRecallResult,
)
from evelyn_core import memory_vault as memory_vault_module  # noqa: E402
from evelyn_core.memory_deletion_journal import (  # noqa: E402
    MemoryDeletionJournalBusyError,
)
from evelyn_core.conversation_memory_exposure import (  # noqa: E402
    filter_conversation_history_for_memory_exposure,
)
from evelyn_core.memory_prompt_policy import (  # noqa: E402
    prepare_memory_context_for_prompt,
    reconcile_memory_receipt_for_prompt,
    validated_memory_grounding_state,
)
from evelyn_core.memory_vault import (  # noqa: E402
    MEMORY_DELETE_TOMBSTONE_SCHEMA,
    MEMORY_PROVENANCE_SCHEMA,
    MemoryNoteDeletedError,
    activate_memory_vault_for_guild,
    append_turn_rows_to_memory_vault,
    bootstrap_memory_vault_source,
    build_memory_recall_receipt,
    build_memory_vault_context,
    consolidate_daily_memory_once,
    delete_memory_vault_user_note,
    export_memory_graph,
    mark_memory_note_superseded,
    memory_note_was_deleted,
    memory_provenance_backfill_preview,
    memory_vault_user_note,
    memory_vault_user_snapshot,
    memory_vault_root,
    parse_memory_note,
    preview_memory_vault_user_note_deletion,
    probe_sub_llm_dependency,
    read_memory_hot_context,
    recall_memory_vault,
    refresh_memory_hot_context,
    refresh_legacy_memory_mirror,
    refresh_legacy_memory_node_notes,
    run_memory_vault_maintenance_once,
    run_semantic_memory_consolidation_once,
    sync_memory_vault_index,
    update_memory_vault_user_note,
    write_memory_vault_note,
)


class MemoryVaultTests(unittest.TestCase):
    def test_recall_projects_busy_without_private_details(self) -> None:
        private_detail = "private recall lock path"
        request = MemoryRecallRequest(
            turn_id="turn-busy",
            session_key="session-busy",
            guild_id=None,
            user_text="fixed synthetic query",
            topic_id=None,
            source="test",
            max_items=1,
        )
        with tempfile.TemporaryDirectory() as tmp, patch.object(
            memory_vault_module,
            "sync_memory_vault_index",
            side_effect=MemoryDeletionJournalBusyError(private_detail),
        ):
            result = recall_memory_vault(request, root=Path(tmp))

        self.assertFalse(result.ok)
        self.assertEqual(
            result.error_text,
            "memory_deletion_journal_busy",
        )
        self.assertNotIn(private_detail, str(result))

    def test_recall_receipt_normalizes_private_retrieval_mode(
        self,
    ) -> None:
        private_canary = "PRIVATE retrieval-mode receipt canary"
        receipt = build_memory_recall_receipt(
            MemoryRecallResult(
                turn_id="turn-retrieval-mode",
                ok=True,
                context_text="grounded memory",
                metadata={
                    "retrieval_mode": private_canary,
                    "provenance": [],
                },
            )
        )

        self.assertEqual(receipt["retrievalMode"], "unknown")
        self.assertNotIn(private_canary, str(receipt))

    def test_recall_receipt_fails_closed_for_empty_or_malformed_sets(
        self,
    ) -> None:
        note_id = "concept-0123456789abcdef"
        minimal_valid = build_memory_recall_receipt(
            MemoryRecallResult(
                turn_id="turn-minimal-valid-provenance",
                ok=True,
                context_text="memory body",
                metadata={
                    "provenance": [
                        {
                            "schema": MEMORY_PROVENANCE_SCHEMA,
                            "noteId": note_id,
                            "source": "unknown",
                            "sourceType": "unknown",
                            "sourceRefs": [],
                            "derivedFrom": [],
                            "evidenceHashes": [],
                            "confidence": "",
                        }
                    ],
                    "rendered_note_ids": [note_id],
                },
            )
        )
        empty = build_memory_recall_receipt(
            MemoryRecallResult(
                turn_id="turn-empty-rendered-set",
                ok=True,
                context_text="",
                metadata={
                    "provenance": [{"noteId": note_id}],
                    "rendered_note_ids": [note_id],
                },
            )
        )
        malformed = build_memory_recall_receipt(
            MemoryRecallResult(
                turn_id="turn-malformed-provenance",
                ok=True,
                context_text="memory body",
                metadata={
                    "provenance": 7,
                    "rendered_note_ids": [note_id],
                },
            )
        )
        mismatched = build_memory_recall_receipt(
            MemoryRecallResult(
                turn_id="turn-mismatched-rendered-set",
                ok=True,
                context_text="memory body",
                metadata={
                    "provenance": [{"noteId": note_id}],
                    "rendered_note_ids": [],
                },
            )
        )
        incomplete_provenance = build_memory_recall_receipt(
            MemoryRecallResult(
                turn_id="turn-incomplete-provenance",
                ok=True,
                context_text="memory body",
                metadata={
                    "provenance": [{"noteId": note_id}],
                    "rendered_note_ids": [note_id],
                },
            )
        )
        oversized_note_ids = [
            f"concept-{index:016x}"
            for index in range(
                memory_vault_module.MEMORY_RECALL_MAX_RENDERED_NOTES + 1
            )
        ]
        oversized_set = build_memory_recall_receipt(
            MemoryRecallResult(
                turn_id="turn-oversized-provenance-set",
                ok=True,
                context_text="memory body",
                metadata={
                    "provenance": [
                        {
                            "schema": MEMORY_PROVENANCE_SCHEMA,
                            "noteId": item,
                            "source": "unknown",
                            "sourceType": "unknown",
                            "sourceRefs": [],
                            "derivedFrom": [],
                            "evidenceHashes": [],
                            "confidence": "",
                        }
                        for item in oversized_note_ids
                    ],
                    "rendered_note_ids": oversized_note_ids,
                },
            )
        )
        malformed_source = build_memory_recall_receipt(
            MemoryRecallResult(
                turn_id="turn-malformed-source-type",
                ok=True,
                context_text="memory body",
                metadata={
                    "memory_version": float("inf"),
                    "provenance": [
                        {
                            "noteId": note_id,
                            "sourceType": "x" * 1_000,
                        }
                    ],
                    "rendered_note_ids": [note_id],
                },
            )
        )
        duplicate = build_memory_recall_receipt(
            MemoryRecallResult(
                turn_id="turn-duplicate-provenance",
                ok=True,
                context_text="memory body",
                metadata={
                    "provenance": [
                        {"noteId": note_id},
                        {"noteId": note_id},
                    ],
                },
            )
        )
        missing_declared_set = build_memory_recall_receipt(
            MemoryRecallResult(
                turn_id="turn-missing-rendered-set",
                ok=True,
                context_text="memory body",
                metadata={
                    "provenance": [{"noteId": note_id}],
                },
            )
        )

        self.assertEqual(minimal_valid["groundingState"], "attributed")
        self.assertEqual(minimal_valid["noteIds"], [note_id])
        self.assertEqual(empty["state"], "empty")
        self.assertEqual(empty["groundingState"], "empty")
        self.assertEqual(empty["noteIds"], [])
        self.assertEqual(empty["provenanceCount"], 0)
        for receipt in (
            malformed,
            mismatched,
            incomplete_provenance,
            oversized_set,
            malformed_source,
            duplicate,
            missing_declared_set,
        ):
            self.assertEqual(receipt["state"], "provided")
            self.assertEqual(receipt["groundingState"], "unattributed")
            self.assertEqual(receipt["noteIds"], [])
            self.assertEqual(receipt["noteCount"], 0)
            self.assertEqual(receipt["provenanceCount"], 0)
        self.assertEqual(malformed_source["memoryVersion"], 0)
        self.assertFalse(
            memory_vault_module._retrieval_cache_payload_is_current(
                {
                    "schema": memory_vault_module.MEMORY_RETRIEVAL_CACHE_SCHEMA,
                    "context_text": "memory body",
                    "facts": ["memory body"],
                    "sources": ["memory source"],
                    "retrieval_mode": "scan",
                    "provenance": [{"noteId": note_id}],
                    "rendered_note_ids": [note_id],
                }
            )
        )

    def test_unattributed_recall_cannot_borrow_pinned_attribution(
        self,
    ) -> None:
        recall_note_id = "concept-0123456789abcdef"
        hot_note_id = "core-fedcba9876543210"
        private_canary = "UNTRUSTED_RECALL_CANARY"
        result = MemoryRecallResult(
            turn_id="turn-unattributed-mixed-context",
            ok=True,
            context_text=private_canary,
            metadata={
                "memory_version": 3,
                "provenance": [{"noteId": recall_note_id}],
                "rendered_note_ids": [recall_note_id],
            },
        )
        receipt: dict[str, object] = {}
        with tempfile.TemporaryDirectory() as tmp:
            with patch.object(
                memory_vault_module,
                "recall_memory_vault",
                return_value=result,
            ), patch.object(
                memory_vault_module,
                "_validated_memory_hot_context_payload",
                return_value=(
                    {
                        "content": "verified pinned context",
                        "note_ids": [hot_note_id],
                    },
                    "verified",
                ),
            ):
                context = build_memory_vault_context(
                    7,
                    "memory",
                    root=Path(tmp),
                    receipt=receipt,
                )

        self.assertIn(private_canary, context)
        self.assertEqual(receipt["groundingState"], "unattributed")
        self.assertEqual(receipt["recallNoteIds"], [])
        self.assertEqual(receipt["suppliedNoteIds"], [])
        boundary = prepare_memory_context_for_prompt(
            context,
            grounding_state=str(receipt["groundingState"]),
        )
        reconcile_memory_receipt_for_prompt(receipt, boundary)
        self.assertTrue(boundary.evidence_withheld)
        self.assertNotIn(private_canary, boundary.context)
        self.assertEqual(receipt["state"], "withheld")
        self.assertEqual(receipt["suppliedNoteIds"], [])

    def test_parse_front_matter_note(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "concept.md"
            path.write_text(
                "\n".join(
                    [
                        "---",
                        "id: concept-test",
                        "type: concept",
                        "title: Stone Tool Plan",
                        "tags: [minecraft, plan]",
                        "projects: [evelyn]",
                        "---",
                        "",
                        "# Stone Tool Plan",
                        "Collect logs, make a pickaxe, mine six stone.",
                    ]
                ),
                encoding="utf-8",
            )

            note = parse_memory_note(path)

        self.assertEqual(note.note_id, "concept-test")
        self.assertEqual(note.note_type, "concept")
        self.assertEqual(note.title, "Stone Tool Plan")
        self.assertIn("minecraft", note.tags)
        self.assertIn("evelyn", note.projects)

    def test_recall_indexes_markdown_and_uses_cache(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            concepts = memory_vault_root(root) / "concepts"
            concepts.mkdir(parents=True)
            (concepts / "stone-tools.md").write_text(
                "\n".join(
                    [
                        "---",
                        "id: stone-tools",
                        "type: concept",
                        "title: Minecraft Stone Tools",
                        "tags: [minecraft, tools]",
                        "projects: [evelyn]",
                        "---",
                        "",
                        "Collect three logs, craft a wooden pickaxe, mine exactly six stone.",
                    ]
                ),
                encoding="utf-8",
            )

            version = sync_memory_vault_index(root=root)
            request = MemoryRecallRequest(
                turn_id="turn-1",
                session_key="session",
                guild_id=None,
                user_text="minecraft stone pickaxe plan",
                topic_id=None,
                source="test",
                max_items=3,
                metadata={"active_project": "evelyn", "context_focus": ["minecraft"]},
            )
            first = recall_memory_vault(request, root=root)
            second = recall_memory_vault(request, root=root)

        self.assertGreaterEqual(version, 1)
        self.assertTrue(first.ok)
        self.assertIn("Stone Tools", first.context_text)
        self.assertFalse(first.metadata["cache_hit"])
        self.assertTrue(first.metadata["index_fresh"])
        self.assertFalse(first.metadata["read_only_fallback"])
        self.assertTrue(second.metadata["cache_hit"])
        self.assertTrue(second.metadata["index_fresh"])
        self.assertFalse(second.metadata["read_only_fallback"])

    def test_sync_purges_only_unreceipted_proactive_artifacts(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            session_dir = root / "guild_7" / "session_live"
            session_dir.mkdir(parents=True)
            proactive_path = session_dir / "proactive_questions.jsonl"
            pending_path = session_dir / "pending_proactive_question.json"
            questions_path = session_dir / "open_questions.jsonl"
            autonomy_path = (
                root
                / "guild_7"
                / "system_autonomy"
                / "cognitive_state.json"
            )
            autonomy_path.parent.mkdir(parents=True)
            for path in (
                proactive_path,
                pending_path,
                questions_path,
                autonomy_path,
            ):
                path.write_text("legacy private canary", encoding="utf-8")

            sync_memory_vault_index(root=root)

            self.assertFalse(proactive_path.exists())
            self.assertFalse(pending_path.exists())
            self.assertTrue(questions_path.exists())
            self.assertTrue(autonomy_path.exists())

    def test_sync_rejects_internal_scope_symlink_alias(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            guild_dir = root / "guild_7"
            target_dir = guild_dir / "safe_target"
            target_dir.mkdir(parents=True)
            proactive_path = target_dir / "proactive_questions.jsonl"
            proactive_path.write_text("must remain", encoding="utf-8")
            alias = guild_dir / "session_alias"
            try:
                alias.symlink_to(target_dir, target_is_directory=True)
            except OSError as exc:
                self.skipTest(f"directory symlink unavailable: {exc}")

            with self.assertRaisesRegex(
                OSError,
                "unsafe_memory_runtime_artifact_path",
            ):
                sync_memory_vault_index(root=root)

            self.assertTrue(proactive_path.exists())
            alias.unlink()
            alias.symlink_to(
                guild_dir / "missing_target",
                target_is_directory=True,
            )
            with self.assertRaisesRegex(
                OSError,
                "unsafe_memory_runtime_artifact_path",
            ):
                sync_memory_vault_index(root=root)

    def test_live_recall_withholds_unreceipted_conversation_and_legacy_notes(
        self,
    ) -> None:
        unsafe_summary = "삭제 현재성 없는 레거시 요약 표식"
        unsafe_answer = "삭제 현재성 없는 어시스턴트 답변 표식"
        unsafe_legacy_note = "source가 없는 레거시 노트 표식"
        safe_note = "사용자가 직접 저장한 안전 기억 표식"
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            guild_dir = root / "guild_7"
            guild_dir.mkdir(parents=True)
            (guild_dir / "rolling_summary.txt").write_text(
                unsafe_summary,
                encoding="utf-8",
            )
            daily_path = append_turn_rows_to_memory_vault(
                7,
                [
                    {
                        "role": "user",
                        "text": "오늘 대화를 기록해줘",
                    },
                    {
                        "role": "assistant",
                        "text": unsafe_answer,
                    },
                ],
                root=root,
            )
            legacy_path = refresh_legacy_memory_mirror(7, root=root)
            write_memory_vault_note(
                note_type="legacy",
                title="기존 레거시 기억",
                body=unsafe_legacy_note,
                root=root,
            )
            write_memory_vault_note(
                note_type="concept",
                title="직접 저장 기억",
                body=safe_note,
                source="control-page-user",
                root=root,
            )
            receipt: dict[str, object] = {}

            unsafe_context = build_memory_vault_context(
                7,
                f"{unsafe_summary} {unsafe_answer} {unsafe_legacy_note}",
                root=root,
                receipt=receipt,
            )
            safe_context = build_memory_vault_context(
                7,
                safe_note,
                root=root,
            )

            self.assertIsNotNone(daily_path)
            self.assertTrue(daily_path.exists())
            self.assertIsNotNone(legacy_path)
            self.assertTrue(legacy_path.exists())
            self.assertNotIn(unsafe_summary, unsafe_context)
            self.assertNotIn(unsafe_answer, unsafe_context)
            self.assertNotIn(unsafe_legacy_note, unsafe_context)
            self.assertFalse(
                {"conversation", "derived", "legacy"}
                & set(receipt.get("sourceTypeCounts") or {})
            )
            self.assertIn(safe_note, safe_context)

    def test_cache_hit_cannot_export_private_retrieval_mode(
        self,
    ) -> None:
        private_canary = "PRIVATE cached retrieval-mode canary"
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            concepts = memory_vault_root(root) / "concepts"
            concepts.mkdir(parents=True)
            (concepts / "cache-mode.md").write_text(
                "\n".join(
                    [
                        "---",
                        "id: cache-mode",
                        "type: concept",
                        "title: Cache Mode",
                        "---",
                        "",
                        "Grounded cache retrieval fixture.",
                    ]
                ),
                encoding="utf-8",
            )
            request = MemoryRecallRequest(
                turn_id="turn-cache-mode",
                session_key="session",
                guild_id=None,
                user_text="cache retrieval fixture",
                topic_id=None,
                source="test",
                max_items=3,
            )
            first = recall_memory_vault(request, root=root)
            self.assertTrue(first.ok)
            db_path = memory_vault_module.memory_index_db_path(root)
            # sqlite3's connection context manager commits or rolls back but
            # does not close.  Explicitly close before TemporaryDirectory
            # removes memory.sqlite on Windows.
            with closing(sqlite3.connect(db_path)) as conn:
                row = conn.execute(
                    "SELECT cache_key, payload FROM retrieval_cache"
                ).fetchone()
                assert row is not None
                cached_payload = json.loads(str(row[1]))
                cached_payload["retrieval_mode"] = private_canary
                conn.execute(
                    "UPDATE retrieval_cache SET payload = ? "
                    "WHERE cache_key = ?",
                    (
                        json.dumps(cached_payload),
                        str(row[0]),
                    ),
                )
                conn.commit()

            cached = recall_memory_vault(request, root=root)
            receipt = build_memory_recall_receipt(cached)

        self.assertTrue(cached.ok)
        self.assertTrue(cached.metadata["cache_hit"])
        self.assertEqual(
            cached.metadata["retrieval_mode"],
            "unknown",
        )
        self.assertEqual(receipt["retrievalMode"], "unknown")
        self.assertNotIn(private_canary, str(receipt))

    def test_recall_rejects_schema_less_legacy_cache_payload(
        self,
    ) -> None:
        private_canary = "LEGACY_UNTRACKED_PROCEDURE_CANARY"
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            write_memory_vault_note(
                note_type="concept",
                title="Current Cache Contract",
                body="current cache contract evidence",
                root=root,
            )
            version = sync_memory_vault_index(root=root)
            request = MemoryRecallRequest(
                turn_id="turn-legacy-cache-contract",
                session_key="session",
                guild_id=None,
                user_text="current cache contract evidence",
                topic_id=None,
                source="test",
                max_items=1,
            )
            cache_key = memory_vault_module._cache_key(request, version)
            legacy_payload = {
                "context_text": (
                    "[Procedural Memory]\n"
                    f"- {private_canary}"
                ),
                "facts": [],
                "sources": [],
                "retrieval_mode": "cache",
                "provenance": [],
            }
            with closing(
                sqlite3.connect(
                    memory_vault_module.memory_index_db_path(root)
                )
            ) as connection:
                connection.execute(
                    "INSERT OR REPLACE INTO retrieval_cache"
                    "(cache_key, created_at, memory_version, payload) "
                    "VALUES(?, ?, ?, ?)",
                    (
                        cache_key,
                        memory_vault_module.time.time(),
                        version,
                        json.dumps(legacy_payload),
                    ),
                )
                connection.commit()

            result = recall_memory_vault(request, root=root)
            with closing(
                sqlite3.connect(
                    memory_vault_module.memory_index_db_path(root)
                )
            ) as connection:
                rewritten_payload = json.loads(
                    connection.execute(
                        "SELECT payload FROM retrieval_cache "
                        "WHERE cache_key = ?",
                        (cache_key,),
                    ).fetchone()[0]
                )

        self.assertTrue(result.ok)
        self.assertFalse(result.metadata["cache_hit"])
        self.assertNotIn(private_canary, result.context_text)
        self.assertEqual(
            rewritten_payload["schema"],
            memory_vault_module.MEMORY_RETRIEVAL_CACHE_SCHEMA,
        )

    def test_cache_hit_closes_every_product_index_connection(
        self,
    ) -> None:
        opened: list[sqlite3.Connection] = []
        real_connect = sqlite3.connect

        class TrackingConnection(sqlite3.Connection):
            close_calls = 0

            def close(self) -> None:
                self.close_calls += 1
                super().close()

        def tracked_connect(*args: object, **kwargs: object) -> sqlite3.Connection:
            kwargs["factory"] = TrackingConnection
            connection = real_connect(*args, **kwargs)
            opened.append(connection)
            return connection

        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            concepts = memory_vault_root(root) / "concepts"
            concepts.mkdir(parents=True)
            (concepts / "cache-close.md").write_text(
                "\n".join(
                    [
                        "---",
                        "id: cache-close",
                        "type: concept",
                        "title: Cache Close",
                        "---",
                        "",
                        "Cache connection lifetime fixture.",
                    ]
                ),
                encoding="utf-8",
            )
            request = MemoryRecallRequest(
                turn_id="turn-cache-close",
                session_key="session",
                guild_id=None,
                user_text="cache connection lifetime fixture",
                topic_id=None,
                source="test",
                max_items=3,
            )
            first = recall_memory_vault(request, root=root)
            self.assertTrue(first.ok)

            with patch.object(
                memory_vault_module.sqlite3,
                "connect",
                side_effect=tracked_connect,
            ):
                cached = recall_memory_vault(request, root=root)

            self.assertTrue(cached.ok)
            self.assertTrue(cached.metadata["cache_hit"])
            self.assertGreaterEqual(len(opened), 2)
            self.assertTrue(
                all(
                    getattr(connection, "close_calls", 0) == 1
                    for connection in opened
                )
            )

    def test_index_setup_failure_closes_connection(
        self,
    ) -> None:
        class SetupFailingConnection:
            row_factory: object | None = None
            closed = False

            def execute(self, _statement: str) -> None:
                raise sqlite3.OperationalError("synthetic pragma failure")

            def close(self) -> None:
                self.closed = True

        connection = SetupFailingConnection()
        with tempfile.TemporaryDirectory() as tmp:
            db_path = Path(tmp) / "memory_index" / "memory.sqlite"
            with patch.object(
                memory_vault_module.sqlite3,
                "connect",
                return_value=connection,
            ):
                with self.assertRaisesRegex(
                    sqlite3.OperationalError,
                    "synthetic pragma failure",
                ):
                    with memory_vault_module._open_index(db_path):
                        self.fail("setup failure must prevent context entry")

        self.assertTrue(connection.closed)

    def test_append_turn_rows_creates_daily_markdown(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            path = append_turn_rows_to_memory_vault(
                123,
                [{"role": "user", "speaker": "user", "source": "test", "text": "remember this preference"}],
                root=root,
            )
            assert path is not None
            content = path.read_text(encoding="utf-8")

        self.assertIn("# 이블린 일일 메모", content)
        self.assertIn("> [!summary] 오늘 보기", content)
        self.assertIn("> [!example]- 대화 원문 보기", content)
        self.assertIn("remember this preference", content)
        self.assertIn("> - 정훈: remember this preference", content)
        self.assertIn("type: daily", content)
        self.assertIn("source: conversation-turn-log", content)
        self.assertIn("source_refs: [guild:123]", content)
        self.assertNotIn("/user/test:", content)

    def test_append_turn_rows_can_record_combined_scopes_once(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            path = append_turn_rows_to_memory_vault(
                123,
                [{"role": "user", "speaker": "user", "source": "test", "text": "single visible daily turn"}],
                scope_labels=[
                    "guild",
                    "room:text-1",
                    "person:user-2",
                    "session:guild-123-text-1-user-2",
                ],
                root=root,
            )
            assert path is not None
            content = path.read_text(encoding="utf-8")

        self.assertEqual(content.count("single visible daily turn"), 1)
        self.assertNotIn("scopes:", content)
        self.assertNotIn("room:text-1", content)

    def test_append_turn_rows_skips_short_call_noise_in_daily_view(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            path = append_turn_rows_to_memory_vault(
                123,
                [
                    {"role": "user", "speaker": "user", "source": "voice", "text": "이블린."},
                    {"role": "assistant", "speaker": "Evelyn", "source": "voice", "text": "응, 왜 불렀어?"},
                ],
                root=root,
            )

        self.assertIsNone(path)

    def test_graph_link_does_not_pull_procedure_into_general_recall(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            concepts = memory_vault_root(root) / "concepts"
            procedures = memory_vault_root(root) / "procedures"
            concepts.mkdir(parents=True)
            procedures.mkdir(parents=True)
            (concepts / "tts-latency.md").write_text(
                "\n".join(
                    [
                        "---",
                        "id: tts-latency",
                        "type: concept",
                        "title: TTS Latency",
                        "tags: [tts]",
                        "projects: [evelyn]",
                        "links: [test-evelyn-tts]",
                        "---",
                        "",
                        "Use prefetch and cache to reduce first audio latency.",
                    ]
                ),
                encoding="utf-8",
            )
            (procedures / "test-evelyn-tts.md").write_text(
                "\n".join(
                    [
                        "---",
                        "id: test-evelyn-tts",
                        "type: procedure",
                        "title: Test Evelyn TTS",
                        "tags: [verify]",
                        "projects: [evelyn]",
                        "---",
                        "",
                        "After tests, clean up launched TTS services.",
                    ]
                ),
                encoding="utf-8",
            )

            request = MemoryRecallRequest(
                turn_id="turn-graph",
                session_key=None,
                guild_id=None,
                user_text="tts latency cache",
                topic_id=None,
                source="test",
                max_items=2,
                metadata={"active_project": "evelyn"},
            )
            result = recall_memory_vault(request, root=root)

        self.assertTrue(result.ok)
        self.assertIn("TTS Latency", result.context_text)
        self.assertNotIn("Test Evelyn TTS", result.context_text)
        self.assertIn(result.metadata["retrieval_mode"], {"fts", "scan", "fts+vector", "scan+vector"})

    def test_memory_admin_query_can_recall_procedure_notes(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            write_memory_vault_note(
                note_type="procedure",
                title="Test Evelyn TTS",
                body="After tests, clean up launched TTS services.",
                tags=["verify"],
                projects=["evelyn"],
                root=root,
            )
            request = MemoryRecallRequest(
                turn_id="turn-admin-procedure",
                session_key=None,
                guild_id=None,
                user_text="memory vault maintenance test evelyn tts procedure",
                topic_id=None,
                source="test",
                max_items=3,
                metadata={"active_project": "evelyn"},
            )
            result = recall_memory_vault(request, root=root)

        self.assertTrue(result.ok)
        self.assertIn("Test Evelyn TTS", result.context_text)
        self.assertIn("[Procedural Memory]", result.context_text)

    def test_extra_procedure_uses_the_exact_rendered_note_set(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            concept_path = write_memory_vault_note(
                note_type="concept",
                title="Exact Set Concept",
                body="exactset alpha beta gamma concept evidence",
                importance=1.0,
                root=root,
            )
            procedure_path = write_memory_vault_note(
                note_type="procedure",
                title="Exact Set Procedure",
                body="exactset cleanup procedure evidence",
                importance=0.0,
                root=root,
            )
            concept_id = parse_memory_note(concept_path).note_id
            procedure_id = parse_memory_note(procedure_path).note_id
            request = MemoryRecallRequest(
                turn_id="turn-extra-procedure-exact-set",
                session_key=None,
                guild_id=None,
                user_text=(
                    "memory vault exactset alpha beta gamma"
                ),
                topic_id=None,
                source="test",
                max_items=1,
                metadata={"allow_internal_memory": True},
            )
            result = recall_memory_vault(request, root=root)
            cached = recall_memory_vault(request, root=root)
            receipt = build_memory_recall_receipt(result)

        expected_ids = sorted([concept_id, procedure_id])
        self.assertTrue(result.ok)
        self.assertIn("[Memory Vault Notes]", result.context_text)
        self.assertIn("[Procedural Memory]", result.context_text)
        self.assertEqual(len(result.facts), 2)
        self.assertTrue(
            all(
                result.context_text.count(snippet) == 1
                for snippet in result.facts
            )
        )
        self.assertEqual(len(result.sources), 2)
        self.assertEqual(len(result.metadata["provenance"]), 2)
        self.assertEqual(
            sorted(result.metadata["rendered_note_ids"]),
            expected_ids,
        )
        self.assertEqual(receipt["noteIds"], expected_ids)
        self.assertEqual(receipt["noteCount"], 2)
        self.assertEqual(receipt["provenanceCount"], 2)
        self.assertTrue(cached.metadata["cache_hit"])
        self.assertEqual(cached.context_text, result.context_text)
        self.assertEqual(cached.facts, result.facts)
        self.assertEqual(cached.sources, result.sources)
        self.assertEqual(
            cached.metadata["rendered_note_ids"],
            result.metadata["rendered_note_ids"],
        )
        self.assertEqual(
            cached.metadata["provenance"],
            result.metadata["provenance"],
        )

    def test_selected_procedure_is_deduplicated_from_procedure_extras(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            primary_path = write_memory_vault_note(
                note_type="procedure",
                title="Dominant Procedure",
                body="dominant alpha beta gamma operational steps",
                importance=1.0,
                root=root,
            )
            secondary_path = write_memory_vault_note(
                note_type="procedure",
                title="Secondary Procedure",
                body="dominant fallback operational steps",
                importance=0.0,
                root=root,
            )
            expected_ids = sorted(
                [
                    parse_memory_note(primary_path).note_id,
                    parse_memory_note(secondary_path).note_id,
                ]
            )
            result = recall_memory_vault(
                MemoryRecallRequest(
                    turn_id="turn-procedure-dedupe",
                    session_key=None,
                    guild_id=None,
                    user_text=(
                        "memory vault dominant alpha beta gamma"
                    ),
                    topic_id=None,
                    source="test",
                    max_items=1,
                    metadata={"allow_internal_memory": True},
                ),
                root=root,
            )
            receipt = build_memory_recall_receipt(result)

        self.assertTrue(result.ok)
        self.assertNotIn("[Memory Vault Notes]", result.context_text)
        self.assertIn("[Procedural Memory]", result.context_text)
        self.assertEqual(len(result.facts), 2)
        self.assertTrue(
            all(
                result.context_text.count(snippet) == 1
                for snippet in result.facts
            )
        )
        self.assertEqual(len(set(result.sources)), 2)
        self.assertEqual(
            len(result.metadata["rendered_note_ids"]),
            2,
        )
        self.assertEqual(
            len(set(result.metadata["rendered_note_ids"])),
            2,
        )
        self.assertEqual(receipt["noteIds"], expected_ids)
        self.assertEqual(receipt["noteCount"], len(result.facts))
        self.assertEqual(
            receipt["provenanceCount"],
            len(result.metadata["provenance"]),
        )

    def test_recall_normalizes_max_items_before_cache_and_fallback(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            write_memory_vault_note(
                note_type="concept",
                title="Normalized Recall Limit",
                body="normalized recall limit marker",
                root=root,
            )
            zero_request = MemoryRecallRequest(
                turn_id="turn-zero-max-items",
                session_key=None,
                guild_id=None,
                user_text="normalized recall limit marker",
                topic_id=None,
                source="test",
                max_items=0,
            )
            one_request = MemoryRecallRequest(
                turn_id="turn-one-max-items",
                session_key=None,
                guild_id=None,
                user_text="normalized recall limit marker",
                topic_id=None,
                source="test",
                max_items=1,
            )
            twelve_request = MemoryRecallRequest(
                turn_id="turn-twelve-max-items",
                session_key=None,
                guild_id=None,
                user_text="normalized recall limit marker",
                topic_id=None,
                source="test",
                max_items=12,
            )
            oversized_request = MemoryRecallRequest(
                turn_id="turn-oversized-max-items",
                session_key=None,
                guild_id=None,
                user_text="normalized recall limit marker",
                topic_id=None,
                source="test",
                max_items=999,
            )
            result = recall_memory_vault(zero_request, root=root)
            version = result.metadata["memory_version"]

        self.assertTrue(result.ok)
        self.assertEqual(len(result.facts), 1)
        self.assertEqual(
            memory_vault_module._cache_key(zero_request, version),
            memory_vault_module._cache_key(one_request, version),
        )
        self.assertEqual(
            memory_vault_module._cache_key(twelve_request, version),
            memory_vault_module._cache_key(
                oversized_request,
                version,
            ),
        )

    def test_procedural_alias_is_internal_and_uses_procedure_section(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            write_memory_vault_note(
                note_type="procedural",
                title="Procedural Alias Boundary",
                body="procedural alias boundary marker",
                root=root,
            )
            general = recall_memory_vault(
                MemoryRecallRequest(
                    turn_id="turn-general-procedural-alias",
                    session_key=None,
                    guild_id=None,
                    user_text="procedural alias boundary marker",
                    topic_id=None,
                    source="test",
                    max_items=1,
                ),
                root=root,
            )
            admin = recall_memory_vault(
                MemoryRecallRequest(
                    turn_id="turn-admin-procedural-alias",
                    session_key=None,
                    guild_id=None,
                    user_text=(
                        "memory vault procedural alias boundary marker"
                    ),
                    topic_id=None,
                    source="test",
                    max_items=1,
                ),
                root=root,
            )

        self.assertTrue(general.ok)
        self.assertNotIn("Procedural Alias Boundary", general.context_text)
        self.assertTrue(admin.ok)
        self.assertNotIn("[Memory Vault Notes]", admin.context_text)
        self.assertIn("[Procedural Memory]", admin.context_text)
        self.assertEqual(len(admin.facts), 1)
        self.assertEqual(
            admin.context_text.count(admin.facts[0]),
            1,
        )

    def test_general_recall_hides_runtime_management_notes(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            write_memory_vault_note(
                note_type="concept",
                title="Visible TTS Latency Note",
                body="Public memory says TTS latency uses prefetch and short chunks.",
                tags=["tts", "latency"],
                projects=["evelyn"],
                root=root,
            )
            for note_type in ("debug", "runtime", "tool", "internal", "system"):
                write_memory_vault_note(
                    note_type=note_type,
                    title=f"Hidden {note_type.title()} Diagnostic",
                    body="Internal diagnostic says restart private runtime probes before reporting.",
                    tags=["tts", "latency", "diagnostic"],
                    projects=["evelyn"],
                    root=root,
                )
            request = MemoryRecallRequest(
                turn_id="turn-general-runtime-hidden",
                session_key=None,
                guild_id=None,
                user_text="tts latency diagnostic",
                topic_id=None,
                source="test",
                max_items=6,
                metadata={"active_project": "evelyn"},
            )
            result = recall_memory_vault(request, root=root)

        self.assertTrue(result.ok)
        self.assertIn("Visible TTS Latency Note", result.context_text)
        self.assertNotIn("Hidden Debug Diagnostic", result.context_text)
        self.assertNotIn("Hidden Runtime Diagnostic", result.context_text)
        self.assertNotIn("Hidden Tool Diagnostic", result.context_text)
        self.assertNotIn("Hidden Internal Diagnostic", result.context_text)
        self.assertNotIn("Hidden System Diagnostic", result.context_text)

    def test_explicit_admin_recall_can_include_runtime_management_notes(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            write_memory_vault_note(
                note_type="runtime",
                title="Runtime Diagnostic Note",
                body="Internal diagnostic says restart private runtime probes before reporting.",
                tags=["tts", "latency", "diagnostic"],
                projects=["evelyn"],
                root=root,
            )
            request = MemoryRecallRequest(
                turn_id="turn-admin-runtime-visible",
                session_key=None,
                guild_id=None,
                user_text="tts latency diagnostic",
                topic_id=None,
                source="test",
                max_items=3,
                metadata={"active_project": "evelyn", "allow_internal_memory": True},
            )
            result = recall_memory_vault(request, root=root)

        self.assertTrue(result.ok)
        self.assertIn("Runtime Diagnostic Note", result.context_text)

    def test_user_note_detail_returns_full_edit_body(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            long_body = "\n".join(f"Line {index}: keep this editable detail." for index in range(80))
            write_memory_vault_note(
                note_type="daily",
                title="Long Editable Memory",
                body=long_body,
                root=root,
            )
            snapshot = memory_vault_user_snapshot(root=root)
            note_id = snapshot["cards"][0]["id"]
            detail = memory_vault_user_note(note_id, root=root)

        self.assertTrue(detail["ok"])
        self.assertEqual(
            snapshot["deletionIntegrity"]["schema"],
            "memory.deletion.integrity.v1",
        )
        self.assertFalse(
            snapshot["deletionIntegrity"]["rollbackProtected"]
        )
        self.assertTrue(
            snapshot["deletionIntegrity"]["contentFree"]
        )
        self.assertGreater(len(detail["card"]["body"]), len(detail["card"]["preview"]))
        self.assertIn("Line 79: keep this editable detail.", detail["card"]["body"])

    def test_export_memory_graph_includes_nodes_and_relationship_edges(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            write_memory_vault_note(
                note_type="core",
                title="Memory Graph Core",
                body="Core memory note for graph visualization.",
                tags=["memory", "graph"],
                links=["Memory Graph Procedure"],
                importance=0.9,
                root=root,
            )
            write_memory_vault_note(
                note_type="procedure",
                title="Memory Graph Procedure",
                body="Procedure note connected through explicit wiki style relationships and shared graph tags.",
                tags=["memory", "graph"],
                importance=0.7,
                root=root,
            )
            write_memory_vault_note(
                note_type="concept",
                title="Memory Graph Concept",
                body="Concept note connected through explicit wiki style relationships and shared graph tags.",
                tags=["memory", "graph"],
                links=["Memory Graph Core"],
                importance=0.75,
                root=root,
            )

            graph = export_memory_graph(root=root)

        self.assertTrue(graph["ok"])
        self.assertGreaterEqual(graph["stats"]["node_count"], 2)
        self.assertGreaterEqual(graph["stats"]["edge_count"], 1)
        node_titles = {node["title"] for node in graph["nodes"]}
        edge_types = {edge["type"] for edge in graph["edges"]}
        self.assertIn("Memory Graph Core", node_titles)
        self.assertIn("Memory Graph Concept", node_titles)
        self.assertNotIn("Memory Graph Procedure", node_titles)
        self.assertNotIn("procedure", graph["stats"]["type_counts"])
        self.assertTrue({"related", "shared_tag", "semantic_similarity"} & edge_types)

    def test_export_memory_graph_hides_internal_management_types_by_default(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            write_memory_vault_note(
                note_type="core",
                title="Visible User Memory",
                body="This note should remain visible in the public graph.",
                tags=["memory"],
                root=root,
            )
            for note_type in ("procedure", "internal", "system", "debug", "runtime", "tool"):
                write_memory_vault_note(
                    note_type=note_type,
                    title=f"Hidden {note_type.title()} Memory",
                    body="This operational note should stay out of the public memory graph.",
                    tags=["memory"],
                    root=root,
                )

            graph = export_memory_graph(root=root)

        node_types = {node["type"] for node in graph["nodes"]}
        node_titles = {node["title"] for node in graph["nodes"]}
        self.assertIn("Visible User Memory", node_titles)
        self.assertFalse({"procedure", "internal", "system", "debug", "runtime", "tool"} & node_types)
        self.assertFalse({"procedure", "internal", "system", "debug", "runtime", "tool"} & set(graph["stats"]["type_counts"]))
        self.assertFalse(graph["stats"]["include_internal"])
        self.assertIn("runtime", graph["stats"]["hidden_types"])

    def test_export_memory_graph_can_include_internal_management_types_explicitly(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            write_memory_vault_note(
                note_type="core",
                title="Visible User Memory",
                body="This note should remain visible in the public graph.",
                tags=["memory"],
                links=["Memory Procedure"],
                root=root,
            )
            write_memory_vault_note(
                note_type="procedure",
                title="Memory Procedure",
                body="This procedure should only appear in an explicit management graph.",
                tags=["memory"],
                root=root,
            )
            write_memory_vault_note(
                note_type="runtime",
                title="Runtime Diagnostic Note",
                body="This runtime note should only appear in an explicit management graph.",
                tags=["memory"],
                root=root,
            )

            graph = export_memory_graph(root=root, include_internal=True)

        node_types = {node["type"] for node in graph["nodes"]}
        node_titles = {node["title"] for node in graph["nodes"]}
        self.assertIn("Memory Procedure", node_titles)
        self.assertIn("Runtime Diagnostic Note", node_titles)
        self.assertIn("procedure", node_types)
        self.assertIn("runtime", node_types)
        self.assertTrue(graph["stats"]["include_internal"])
        self.assertEqual(graph["stats"]["hidden_types"], [])

    def test_write_and_supersede_note(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            path = write_memory_vault_note(
                note_type="concept",
                title="Old Memory Shape",
                body="This note should stop being recalled after supersession.",
                tags=["memory"],
                root=root,
            )
            note = parse_memory_note(path)
            self.assertTrue(mark_memory_note_superseded(note.note_id, root=root))
            request = MemoryRecallRequest(
                turn_id="turn-superseded",
                session_key=None,
                guild_id=None,
                user_text="old memory shape",
                topic_id=None,
                source="test",
                max_items=3,
            )
            result = recall_memory_vault(request, root=root)

        self.assertTrue(result.ok)
        self.assertNotIn("Old Memory Shape", result.context_text)

    def test_user_memory_snapshot_tracks_confirmation_state(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            write_memory_vault_note(
                note_type="concept",
                title="정훈 취향",
                body="정훈은 초코 웨이퍼 롤을 좋아한다.",
                tags=["preference"],
                root=root,
            )
            first = memory_vault_user_snapshot(root=root)
            note_id = first["cards"][0]["id"]
            confirmed = update_memory_vault_user_note(
                note_id,
                "confirm",
                expected_content_hash=first["cards"][0][
                    "sourceHash"
                ],
                root=root,
            )
            pinned = update_memory_vault_user_note(note_id, "pin", root=root)
            second = memory_vault_user_snapshot(root=root)
            hidden = update_memory_vault_user_note(note_id, "hide", root=root)
            third = memory_vault_user_snapshot(root=root)
            state_path = root / "memory_index" / "user_note_state.json"
            note_raw = Path(first["vaultPath"]) / first["cards"][0]["path"]
            state_exists = state_path.exists()
            state_payload = json.loads(
                state_path.read_text(encoding="utf-8")
            )
            raw_after_actions = note_raw.read_text(encoding="utf-8")

        self.assertEqual(first["counts"]["unconfirmed"], 1)
        self.assertIn("body", first["cards"][0])
        self.assertFalse(first["cards"][0]["body"].startswith("#"))
        self.assertTrue(first["cards"][0]["body"])
        self.assertTrue(confirmed["ok"])
        self.assertEqual(
            confirmed["schema"],
            "memory.user-review-confirmation.v1",
        )
        self.assertTrue(confirmed["confirmationContentBound"])
        self.assertTrue(pinned["ok"])
        self.assertTrue(second["cards"][0]["confirmed"])
        self.assertEqual(
            second["cards"][0]["confirmationState"],
            "confirmed",
        )
        self.assertTrue(
            second["cards"][0]["confirmationContentBound"]
        )
        self.assertTrue(second["cards"][0]["pinned"])
        self.assertTrue(hidden["ok"])
        self.assertEqual(third["counts"]["total"], 0)
        self.assertTrue(state_exists)
        self.assertEqual(
            state_payload["notes"][note_id][
                "confirmed_content_hash"
            ],
            first["cards"][0]["sourceHash"],
        )
        self.assertNotIn("confirmed_at", raw_after_actions)

    def test_user_review_confirmation_is_bound_to_exact_note_revision(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            write_memory_vault_note(
                note_type="concept",
                title="Revision-bound confirmation",
                body="The first reviewed body.",
                source="control-page-user",
                root=root,
            )
            first = memory_vault_user_snapshot(root=root)
            first_card = first["cards"][0]
            missing_hash = update_memory_vault_user_note(
                first_card["id"],
                "confirm",
                root=root,
            )
            changed_before_confirm = update_memory_vault_user_note(
                first_card["id"],
                "confirm",
                expected_content_hash="0" * 64,
                root=root,
            )
            confirmed = update_memory_vault_user_note(
                first_card["id"],
                "confirm",
                expected_content_hash=first_card["sourceHash"],
                root=root,
            )
            edited = update_memory_vault_user_note(
                first_card["id"],
                "edit",
                title="Revision-bound confirmation",
                body="The second reviewed body.",
                expected_content_hash=first_card["sourceHash"],
                root=root,
            )
            after_edit = memory_vault_user_snapshot(root=root)
            stale_card = after_edit["cards"][0]
            reconfirmed = update_memory_vault_user_note(
                stale_card["id"],
                "confirm",
                expected_content_hash=stale_card["sourceHash"],
                root=root,
            )
            final = memory_vault_user_snapshot(root=root)

        self.assertEqual(
            missing_hash["error"],
            "memory_confirm_content_hash_required",
        )
        self.assertEqual(
            changed_before_confirm["error"],
            "memory_note_changed_since_read",
        )
        self.assertTrue(confirmed["ok"])
        self.assertTrue(edited["ok"])
        self.assertFalse(stale_card["confirmed"])
        self.assertEqual(
            stale_card["confirmationState"],
            "stale",
        )
        self.assertFalse(
            stale_card["confirmationContentBound"]
        )
        self.assertEqual(
            stale_card["provenance"]["userConfirmationState"],
            "stale",
        )
        self.assertEqual(
            after_edit["counts"]["confirmationStale"],
            1,
        )
        self.assertTrue(reconfirmed["ok"])
        self.assertTrue(final["cards"][0]["confirmed"])
        self.assertEqual(
            final["cards"][0]["confirmationState"],
            "confirmed",
        )

    def test_hidden_and_internal_notes_cannot_be_user_confirmed(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            legacy_path = write_memory_vault_note(
                note_type="legacy",
                title="Hidden legacy note",
                body="The user cannot review this body in the public UI.",
                root=root,
            )
            internal_path = write_memory_vault_note(
                note_type="internal",
                title="Internal contract note",
                body="This note is outside the public mutation surface.",
                root=root,
            )
            invalid_path = write_memory_vault_note(
                note_type="concept",
                title="Damaged explicit confirmation",
                body="The explicit confirmation contract is incomplete.",
                tags=["user-confirmed"],
                source="control-page-user",
                root=root,
            )
            legacy_note = parse_memory_note(legacy_path)
            internal_note = parse_memory_note(internal_path)
            invalid_note = parse_memory_note(invalid_path)
            legacy_result = update_memory_vault_user_note(
                legacy_note.note_id,
                "confirm",
                expected_content_hash=legacy_note.source_hash,
                root=root,
            )
            internal_result = update_memory_vault_user_note(
                internal_note.note_id,
                "confirm",
                expected_content_hash=internal_note.source_hash,
                root=root,
            )
            invalid_result = update_memory_vault_user_note(
                invalid_note.note_id,
                "confirm",
                expected_content_hash=invalid_note.source_hash,
                root=root,
            )

        self.assertEqual(
            legacy_result["error"],
            "memory_confirmation_content_hidden",
        )
        self.assertEqual(
            internal_result["error"],
            "note_not_found",
        )
        self.assertEqual(
            invalid_result["error"],
            "memory_note_integrity_invalid",
        )

    def test_user_review_confirmation_write_failure_is_fail_closed(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            path = write_memory_vault_note(
                note_type="concept",
                title="Confirmation write failure",
                body="This must not be reported as confirmed.",
                root=root,
            )
            note = parse_memory_note(path)
            with patch.object(
                memory_vault_module,
                "_write_user_note_state",
                side_effect=OSError("disk unavailable"),
            ):
                result = update_memory_vault_user_note(
                    note.note_id,
                    "confirm",
                    expected_content_hash=note.source_hash,
                    root=root,
                )
            snapshot = memory_vault_user_snapshot(root=root)

        self.assertFalse(result["ok"])
        self.assertFalse(result["confirmed"])
        self.assertEqual(
            result["error"],
            "memory_confirmation_write_failed",
        )
        self.assertFalse(snapshot["cards"][0]["confirmed"])

    def test_user_note_state_uses_durable_atomic_write(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            with patch.object(
                memory_vault_module,
                "atomic_json_write",
            ) as writer:
                memory_vault_module._write_user_note_state(
                    {"note-1": {"confirmed_at": "now"}},
                    root,
                )

        self.assertEqual(writer.call_count, 1)
        self.assertTrue(writer.call_args.kwargs["durable"])

    def test_derived_provenance_is_exposed_in_cards_but_not_live_recall(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            write_memory_vault_note(
                note_type="concept",
                title="Verified Tea Preference",
                body="The user prefers warm barley tea in the evening.",
                source="sub-llm-semantic-consolidation",
                source_refs=[r"C:\private\conversation-evidence.txt"],
                derived_from=["daily-2026-07-29"],
                evidence_hashes=["evidence-sha256-123"],
                confidence="high",
                root=root,
            )
            snapshot = memory_vault_user_snapshot(root=root)
            card = next(
                item
                for item in snapshot["cards"]
                if item["title"] == "Verified Tea Preference"
            )
            request = MemoryRecallRequest(
                turn_id="turn-provenance",
                session_key="session",
                guild_id=None,
                user_text="warm barley tea preference evening",
                topic_id=None,
                source="test",
                max_items=2,
            )
            first = recall_memory_vault(request, root=root)
            second = recall_memory_vault(request, root=root)

        provenance = card["provenance"]
        self.assertEqual(provenance["schema"], MEMORY_PROVENANCE_SCHEMA)
        self.assertEqual(provenance["sourceType"], "derived")
        self.assertEqual(
            provenance["sourceRefs"],
            ["local:conversation-evidence.txt"],
        )
        self.assertEqual(provenance["derivedFrom"], ["daily-2026-07-29"])
        self.assertEqual(
            provenance["evidenceHashes"],
            ["evidence-sha256-123"],
        )
        self.assertEqual(first.context_text, "")
        self.assertEqual(first.metadata["provenance"], [])
        self.assertTrue(second.metadata["cache_hit"])
        self.assertEqual(
            second.metadata["provenance"],
            first.metadata["provenance"],
        )
        self.assertNotIn(r"C:\private", first.context_text)

    def test_user_edit_requires_current_hash_and_replaces_stale_provenance(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            path = write_memory_vault_note(
                note_type="concept",
                title="Generated Preference",
                body="The generated memory says tea.",
                source="sub-llm-semantic-consolidation",
                source_refs=["daily/2026-07-30"],
                derived_from=["daily-2026-07-30"],
                evidence_hashes=["old-derived-evidence"],
                confidence="medium",
                root=root,
            )
            original = parse_memory_note(path)
            missing_hash = update_memory_vault_user_note(
                original.note_id,
                "edit",
                title="Corrected Preference",
                body="The user directly corrected this memory to coffee.",
                root=root,
            )
            edited = update_memory_vault_user_note(
                original.note_id,
                "edit",
                title="Corrected Preference",
                body="The user directly corrected this memory to coffee.",
                expected_content_hash=original.source_hash,
                root=root,
            )
            detail = memory_vault_user_note(
                original.note_id,
                root=root,
            )
            recall = recall_memory_vault(
                MemoryRecallRequest(
                    turn_id="turn-user-edit-provenance",
                    session_key="session",
                    guild_id=None,
                    user_text="corrected preference coffee",
                    topic_id=None,
                    source="test",
                    max_items=2,
                ),
                root=root,
            )

        self.assertFalse(missing_hash["ok"])
        self.assertEqual(
            missing_hash["error"],
            "memory_edit_content_hash_required",
        )
        self.assertTrue(edited["ok"])
        self.assertEqual(edited["schema"], "memory.edit.result.v1")
        self.assertTrue(edited["edited"])
        self.assertNotEqual(
            edited["contentHash"],
            original.source_hash,
        )
        provenance = detail["card"]["provenance"]
        self.assertEqual(provenance["source"], "user-edit")
        self.assertEqual(provenance["sourceType"], "user")
        self.assertEqual(
            provenance["sourceRefs"],
            ["control-page-memory-editor"],
        )
        self.assertEqual(
            provenance["originSource"],
            "sub-llm-semantic-consolidation",
        )
        self.assertEqual(
            provenance["originSourceRefs"],
            ["daily/2026-07-30"],
        )
        self.assertEqual(provenance["derivedFrom"], [])
        self.assertEqual(
            provenance["originDerivedFrom"],
            ["daily-2026-07-30"],
        )
        self.assertEqual(provenance["revision"], 1)
        self.assertEqual(provenance["confidence"], "high")
        self.assertNotEqual(
            provenance["evidenceHashes"],
            ["old-derived-evidence"],
        )
        self.assertIn(
            "old-derived-evidence",
            provenance["revisedFromEvidenceHashes"],
        )
        recall_provenance = recall.metadata["provenance"][0]
        self.assertEqual(recall_provenance["source"], "user-edit")
        self.assertEqual(recall_provenance["revision"], 1)
        self.assertIn("revision=1", recall.context_text)

    def test_stale_user_edit_does_not_overwrite_newer_memory(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            path = write_memory_vault_note(
                note_type="concept",
                title="Concurrent Memory",
                body="original body",
                source="control-page-user",
                root=root,
            )
            original = parse_memory_note(path)
            first = update_memory_vault_user_note(
                original.note_id,
                "edit",
                title="Concurrent Memory",
                body="first accepted correction",
                expected_content_hash=original.source_hash,
                root=root,
            )
            stale = update_memory_vault_user_note(
                original.note_id,
                "edit",
                title="Concurrent Memory",
                body="stale overwrite attempt",
                expected_content_hash=original.source_hash,
                root=root,
            )
            detail = memory_vault_user_note(
                original.note_id,
                root=root,
            )

        self.assertTrue(first["ok"])
        self.assertFalse(stale["ok"])
        self.assertEqual(
            stale["error"],
            "memory_note_changed_since_read",
        )
        self.assertIn(
            "first accepted correction",
            detail["card"]["body"],
        )
        self.assertNotIn(
            "stale overwrite attempt",
            detail["card"]["body"],
        )

    def test_atomic_edit_failure_preserves_original_memory(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            path = write_memory_vault_note(
                note_type="concept",
                title="Atomic Memory",
                body="original atomic body",
                source="control-page-user",
                root=root,
            )
            original = parse_memory_note(path)
            with patch.object(
                memory_vault_module,
                "atomic_text_write",
                side_effect=OSError("disk unavailable"),
            ):
                result = update_memory_vault_user_note(
                    original.note_id,
                    "edit",
                    title="Atomic Memory",
                    body="must not partially replace",
                    expected_content_hash=original.source_hash,
                    root=root,
                )
            after = path.read_text(encoding="utf-8")

        self.assertFalse(result["ok"])
        self.assertEqual(result["error"], "memory_edit_failed")
        self.assertIn("original atomic body", after)
        self.assertNotIn("must not partially replace", after)

    def test_schema_v3_index_migrates_and_reindexes_provenance(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            vault = memory_vault_root(root)
            (vault / "concepts").mkdir(parents=True)
            (vault / "concepts" / "migration.md").write_text(
                "\n".join(
                    [
                        "---",
                        "id: provenance-migration",
                        "type: concept",
                        "title: Provenance Migration",
                        "source: control-page-user",
                        "source_refs: [user-request]",
                        "derived_from: [daily-source]",
                        "evidence_hashes: [evidence-hash]",
                        "---",
                        "",
                        "Durable source for a schema migration check.",
                    ]
                ),
                encoding="utf-8",
            )
            index_dir = root / "memory_index"
            index_dir.mkdir(parents=True)
            db_path = index_dir / "memory.sqlite"
            connection = sqlite3.connect(db_path)
            try:
                connection.executescript(
                    """
                    CREATE TABLE notes (
                        note_id TEXT PRIMARY KEY,
                        rel_path TEXT NOT NULL UNIQUE,
                        note_type TEXT NOT NULL,
                        title TEXT NOT NULL,
                        body TEXT NOT NULL,
                        tags TEXT NOT NULL DEFAULT '[]',
                        projects TEXT NOT NULL DEFAULT '[]',
                        links TEXT NOT NULL DEFAULT '[]',
                        status TEXT NOT NULL DEFAULT 'active',
                        updated_at TEXT NOT NULL DEFAULT '',
                        importance REAL NOT NULL DEFAULT 0.5,
                        confidence TEXT NOT NULL DEFAULT '',
                        mtime_ns INTEGER NOT NULL,
                        source_hash TEXT NOT NULL
                    );
                    CREATE TABLE metadata (
                        key TEXT PRIMARY KEY,
                        value TEXT NOT NULL
                    );
                    INSERT INTO metadata(key, value)
                    VALUES('schema_version', '3');
                    """
                )
                connection.commit()
            finally:
                connection.close()

            sync_memory_vault_index(root=root)
            connection = sqlite3.connect(db_path)
            try:
                columns = {
                    row[1]
                    for row in connection.execute(
                        "PRAGMA table_info(notes)"
                    ).fetchall()
                }
                row = connection.execute(
                    """
                    SELECT source, source_refs, derived_from, evidence_hashes
                    FROM notes
                    WHERE note_id = 'provenance-migration'
                    """
                ).fetchone()
                schema_version = connection.execute(
                    """
                    SELECT value
                    FROM metadata
                    WHERE key = 'schema_version'
                    """
                ).fetchone()[0]
            finally:
                connection.close()

        self.assertTrue(
            {
                "created_at",
                "source",
                "source_refs",
                "derived_from",
                "origin_derived_from",
                "evidence_hashes",
            }
            <= columns
        )
        self.assertEqual(schema_version, "6")
        self.assertEqual(row[0], "control-page-user")
        self.assertIn("user-request", row[1])
        self.assertIn("daily-source", row[2])
        self.assertIn("evidence-hash", row[3])

    def test_delete_projects_busy_without_private_details(self) -> None:
        private_detail = "private delete lock path"
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            path = write_memory_vault_note(
                note_type="concept",
                title="Busy Deletion Canary",
                body="synthetic deletion body",
                source="control-page-user",
                source_refs=["user-request"],
                root=root,
            )
            note = parse_memory_note(path)
            update_memory_vault_user_note(
                note.note_id,
                "confirm",
                expected_content_hash=note.source_hash,
                root=root,
            )
            preview = preview_memory_vault_user_note_deletion(
                note.note_id,
                reason="privacy_request",
                root=root,
                now=lambda: 100.0,
            )
            with patch.object(
                memory_vault_module,
                "_append_memory_deletion_tombstone",
                side_effect=MemoryDeletionJournalBusyError(
                    private_detail
                ),
            ):
                result = delete_memory_vault_user_note(
                    note.note_id,
                    str(preview["confirmToken"]),
                    reason="privacy_request",
                    root=root,
                    now=lambda: 101.0,
                )

        self.assertEqual(
            result,
            {"ok": False, "error": "memory_deletion_journal_busy"},
        )
        self.assertNotIn(private_detail, str(result))

    def test_permanent_delete_removes_source_index_cache_and_user_state(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            title = "Permanent Deletion Canary"
            body = "private deletion canary body that must not survive"
            path = write_memory_vault_note(
                note_type="concept",
                title=title,
                body=body,
                source="control-page-user",
                source_refs=["user-request"],
                root=root,
            )
            note = parse_memory_note(path)
            update_memory_vault_user_note(
                note.note_id,
                "confirm",
                expected_content_hash=note.source_hash,
                root=root,
            )
            request = MemoryRecallRequest(
                turn_id="turn-delete",
                session_key="delete-session",
                guild_id=None,
                user_text="permanent deletion canary private",
                topic_id=None,
                source="test",
                max_items=3,
            )
            before = recall_memory_vault(request, root=root)
            preview = preview_memory_vault_user_note_deletion(
                note.note_id,
                reason="privacy_request",
                root=root,
                now=lambda: 100.0,
            )
            session_dir = root / "guild_7" / "session_delete"
            session_dir.mkdir(parents=True)
            proactive_path = session_dir / "proactive_questions.jsonl"
            pending_path = session_dir / "pending_proactive_question.json"
            autonomy_path = (
                root
                / "guild_7"
                / "system_autonomy"
                / "cognitive_state.json"
            )
            autonomy_path.parent.mkdir(parents=True)
            proactive_path.write_text(body, encoding="utf-8")
            pending_path.write_text(body, encoding="utf-8")
            autonomy_path.write_text(body, encoding="utf-8")
            result = delete_memory_vault_user_note(
                preview["note"]["path"],
                preview["confirmToken"],
                reason="privacy_request",
                root=root,
                now=lambda: 101.0,
            )
            connection = sqlite3.connect(
                root / "memory_index" / "memory.sqlite"
            )
            try:
                note_index_count = connection.execute(
                    "SELECT COUNT(*) FROM notes WHERE note_id = ?",
                    (note.note_id,),
                ).fetchone()[0]
                vector_index_count = connection.execute(
                    "SELECT COUNT(*) FROM note_vectors WHERE note_id = ?",
                    (note.note_id,),
                ).fetchone()[0]
                retrieval_cache_count = connection.execute(
                    "SELECT COUNT(*) FROM retrieval_cache",
                ).fetchone()[0]
            finally:
                connection.close()
            after = recall_memory_vault(request, root=root)
            detail = memory_vault_user_note(note.note_id, root=root)
            state_raw = (
                root / "memory_index" / "user_note_state.json"
            ).read_text(encoding="utf-8")
            hot_raw = (
                root / "memory_index" / "hot_context.json"
            ).read_text(encoding="utf-8", errors="ignore")
            tombstone_raw = (
                root / "memory_index" / "memory_deletions.jsonl"
            ).read_text(encoding="utf-8")
            recreated_error = None
            try:
                write_memory_vault_note(
                    note_type="concept",
                    title=title,
                    body="attempted resurrection",
                    root=root,
                )
            except MemoryNoteDeletedError as exc:
                recreated_error = exc
            path_exists_after = path.exists()
            runtime_artifacts_exist_after = any(
                candidate.exists()
                for candidate in (
                    proactive_path,
                    pending_path,
                    autonomy_path,
                )
            )
            was_deleted = memory_note_was_deleted(note.note_id, root=root)
            reused = delete_memory_vault_user_note(
                note.note_id,
                preview["confirmToken"],
                root=root,
                now=lambda: 102.0,
            )

        self.assertTrue(before.ok)
        self.assertIn(title, before.context_text)
        self.assertTrue(preview["ok"])
        self.assertTrue(result["ok"])
        self.assertFalse(path_exists_after)
        self.assertFalse(runtime_artifacts_exist_after)
        self.assertEqual(note_index_count, 0)
        self.assertEqual(vector_index_count, 0)
        self.assertEqual(retrieval_cache_count, 0)
        self.assertFalse(detail["ok"])
        self.assertNotIn(title, after.context_text)
        self.assertNotIn(body, after.context_text)
        self.assertNotIn(note.note_id, state_raw)
        self.assertNotIn(title, hot_raw)
        self.assertNotIn(body, hot_raw)
        self.assertEqual(
            result["tombstone"]["schema"],
            MEMORY_DELETE_TOMBSTONE_SCHEMA,
        )
        self.assertNotIn("path", result["tombstone"])
        self.assertNotIn("contentHash", result["tombstone"])
        self.assertNotIn(title, tombstone_raw)
        self.assertNotIn(body, tombstone_raw)
        self.assertTrue(was_deleted)
        self.assertIsInstance(recreated_error, MemoryNoteDeletedError)
        self.assertEqual(reused["error"], "memory_delete_token_reused")

    def test_deleted_procedure_invalidates_prior_receipt_bound_history(
        self,
    ) -> None:
        assistant_canary = "ANSWER_DERIVED_FROM_DELETED_PROCEDURE"
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            write_memory_vault_note(
                note_type="concept",
                title="History Exact Set Concept",
                body="historyexact alpha beta gamma concept evidence",
                importance=1.0,
                root=root,
            )
            procedure_path = write_memory_vault_note(
                note_type="procedure",
                title="History Exact Set Procedure",
                body="historyexact cleanup procedure evidence",
                source="control-page-user",
                importance=0.0,
                root=root,
            )
            procedure = parse_memory_note(procedure_path)
            receipt: dict[str, object] = {}
            context = build_memory_vault_context(
                7,
                "memory vault historyexact alpha beta gamma",
                source="test",
                max_items=1,
                root=root,
                receipt=receipt,
            )
            grounding_state = validated_memory_grounding_state(
                receipt,
                has_context=bool(context),
            )
            boundary = prepare_memory_context_for_prompt(
                context,
                grounding_state=grounding_state,
            )
            reconcile_memory_receipt_for_prompt(receipt, boundary)
            prior_version = receipt["memoryVersion"]

            tombstone = memory_vault_module._append_memory_deletion_tombstone(
                {
                    "schema": MEMORY_DELETE_TOMBSTONE_SCHEMA,
                    "noteId": procedure.note_id,
                    "noteType": procedure.note_type,
                    "sourceType": "user",
                    "reason": "privacy_request",
                    "deletedAt": "2026-08-02T00:00:00Z",
                },
                root=root,
            )
            current_version = sync_memory_vault_index(root=root)
            outcome = filter_conversation_history_for_memory_exposure(
                [
                    {"role": "user", "text": "keep user turn"},
                    {
                        "role": "assistant",
                        "text": assistant_canary,
                        "memoryReceipt": receipt,
                    },
                ],
                memory_index_dir=root / "memory_index",
            )

        self.assertIn("History Exact Set Procedure", boundary.context)
        self.assertIn(procedure.note_id, receipt["suppliedNoteIds"])
        self.assertEqual(tombstone["noteId"], procedure.note_id)
        self.assertGreater(current_version, prior_version)
        self.assertEqual(
            outcome.messages,
            ({"role": "user", "text": "keep user turn"},),
        )
        self.assertEqual(outcome.dropped_stale_version_count, 1)
        self.assertIsNone(outcome.memory_exposure_position)
        self.assertNotIn(assistant_canary, str(outcome.messages))

    def test_delete_opaque_canonicalizes_natural_language_front_matter_id(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            private_id = "PRIVATE transcript canary full sentence"
            path = memory_vault_root(root) / "concepts" / "opaque-id.md"
            path.parent.mkdir(parents=True, exist_ok=True)
            original_text = "\n".join(
                [
                    "---",
                    f"id: {private_id}",
                    "type: concepts",
                    "title: Opaque deletion ID",
                    "status: active",
                    "source: control-page-user",
                    "---",
                    "# Opaque deletion ID",
                    "private body",
                ]
            )
            path.write_text(original_text, encoding="utf-8")
            note = parse_memory_note(path)
            preview = preview_memory_vault_user_note_deletion(
                note.note_id,
                reason="privacy_request",
                root=root,
            )
            with patch.object(
                Path,
                "unlink",
                side_effect=PermissionError("locked"),
            ):
                result = delete_memory_vault_user_note(
                    preview["note"]["path"],
                    preview["confirmToken"],
                    reason="privacy_request",
                    root=root,
                )
                redaction_stub = path.read_text(encoding="utf-8")
            journal_raw = (
                root / "memory_index" / "memory_deletions.jsonl"
            ).read_text(encoding="utf-8")
            head_raw = (
                root
                / "memory_index"
                / "memory_deletions_chain_head.json"
            ).read_text(encoding="utf-8")
            canonical_id = result["tombstone"]["noteId"]

            # A same-identity resurrection must be removed by reconciliation,
            # even though the application parser intentionally preserves the
            # original front-matter ID.
            path.write_text(original_text, encoding="utf-8")
            resurrected_note = parse_memory_note(path)
            sync_memory_vault_index(root=root)
            resurrected_exists = path.exists()
            private_id_was_deleted = memory_note_was_deleted(
                private_id,
                root=root,
            )

        self.assertEqual(note.note_id, private_id)
        self.assertEqual(note.note_type, "concepts")
        self.assertTrue(preview["ok"], preview)
        self.assertFalse(result["ok"], result)
        self.assertEqual(
            result["error"],
            "memory_delete_cleanup_required",
        )
        self.assertTrue(canonical_id.startswith("opaque-"), canonical_id)
        self.assertEqual(result["noteId"], canonical_id)
        self.assertEqual(
            result["tombstone"]["noteType"],
            "concept",
        )
        self.assertEqual(resurrected_note.note_id, private_id)
        self.assertFalse(resurrected_exists)
        self.assertTrue(private_id_was_deleted)
        self.assertNotIn(private_id, journal_raw)
        self.assertNotIn(private_id, head_raw)
        self.assertNotIn(private_id, redaction_stub)
        self.assertNotIn(
            private_id,
            json.dumps(result, ensure_ascii=False),
        )
        self.assertIn(canonical_id, redaction_stub)

    def test_delete_tombstone_hides_memory_before_cleanup_finishes(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            title = "Crash Safe Deletion Canary"
            body = "private crash window body"
            path = write_memory_vault_note(
                note_type="project",
                title=title,
                body=body,
                source="control-page-user",
                root=root,
            )
            note = parse_memory_note(path)
            refresh_memory_hot_context(root=root)
            preview = preview_memory_vault_user_note_deletion(
                note.note_id,
                reason="privacy_request",
                root=root,
                now=lambda: 500.0,
            )

            with (
                patch.object(
                    Path,
                    "unlink",
                    side_effect=PermissionError("locked"),
                ),
                patch.object(
                    memory_vault_module,
                    "refresh_memory_hot_context",
                    side_effect=RuntimeError("interrupted"),
                ),
            ):
                result = delete_memory_vault_user_note(
                    note.note_id,
                    preview["confirmToken"],
                    reason="privacy_request",
                    root=root,
                    now=lambda: 501.0,
                )
                source_still_exists = path.exists()
                redacted_source = path.read_text(
                    encoding="utf-8"
                )
                stale_hot_context = read_memory_hot_context(
                    root=root
                )
                snapshot = memory_vault_user_snapshot(root=root)

            sync_memory_vault_index(root=root)
            source_exists_after_reconcile = path.exists()
            hot_context_exists_after_reconcile = (
                root / "memory_index" / "hot_context.json"
            ).exists()
            prompt_block_exists_after_reconcile = (
                root
                / "memory_index"
                / "prompt_blocks"
                / "core_prompt.txt"
            ).exists()
            recall = recall_memory_vault(
                MemoryRecallRequest(
                    turn_id="turn-after-crash-safe-delete",
                    session_key="delete-session",
                    guild_id=None,
                    user_text=title,
                    topic_id=None,
                    source="test",
                    max_items=3,
                ),
                root=root,
            )

        self.assertFalse(result["ok"])
        self.assertEqual(
            result["error"],
            "memory_delete_cleanup_required",
        )
        self.assertTrue(result["tombstoned"])
        self.assertTrue(source_still_exists)
        self.assertNotIn(title, redacted_source)
        self.assertNotIn(body, redacted_source)
        self.assertIn(note.note_id, redacted_source)
        self.assertEqual(stale_hot_context, "")
        self.assertFalse(
            any(
                card["id"] == note.note_id
                for card in snapshot["cards"]
            )
        )
        self.assertFalse(source_exists_after_reconcile)
        self.assertFalse(hot_context_exists_after_reconcile)
        self.assertFalse(prompt_block_exists_after_reconcile)
        self.assertNotIn(title, recall.context_text)
        self.assertNotIn(body, recall.context_text)

    def test_deleted_daily_note_resumes_with_new_identity(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            path = append_turn_rows_to_memory_vault(
                7,
                [
                    {
                        "role": "user",
                        "text": "first private daily turn",
                    },
                    {
                        "role": "assistant",
                        "text": "first reply",
                    },
                ],
                root=root,
            )
            self.assertIsNotNone(path)
            original_note = parse_memory_note(path)
            preview = preview_memory_vault_user_note_deletion(
                original_note.note_id,
                reason="privacy_request",
                root=root,
            )
            deleted = delete_memory_vault_user_note(
                original_note.note_id,
                preview["confirmToken"],
                reason="privacy_request",
                root=root,
            )
            continued_path = append_turn_rows_to_memory_vault(
                7,
                [
                    {
                        "role": "user",
                        "text": "new conversation after deletion",
                    },
                    {
                        "role": "assistant",
                        "text": "new reply",
                    },
                ],
                root=root,
            )
            continued_note = parse_memory_note(continued_path)
            continued_raw = continued_path.read_text(
                encoding="utf-8"
            )

        self.assertTrue(deleted["ok"])
        self.assertNotEqual(
            continued_note.note_id,
            original_note.note_id,
        )
        self.assertTrue(
            continued_note.note_id.startswith(
                original_note.note_id + "-continuation-"
            )
        )
        self.assertNotIn("first private daily turn", continued_raw)
        self.assertIn("new conversation after deletion", continued_raw)

    def test_permanent_delete_rejects_expired_or_stale_preview(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            path = write_memory_vault_note(
                note_type="concept",
                title="Deletion Race Guard",
                body="initial guarded body",
                root=root,
            )
            note = parse_memory_note(path)
            expired_preview = preview_memory_vault_user_note_deletion(
                note.note_id,
                root=root,
                now=lambda: 10.0,
            )
            expired = delete_memory_vault_user_note(
                note.note_id,
                expired_preview["confirmToken"],
                root=root,
                now=lambda: 131.0,
            )
            stale_preview = preview_memory_vault_user_note_deletion(
                note.note_id,
                root=root,
                now=lambda: 200.0,
            )
            updated = update_memory_vault_user_note(
                note.note_id,
                "edit",
                title="Deletion Race Guard",
                body="changed after preview",
                expected_content_hash=note.source_hash,
                root=root,
            )
            stale = delete_memory_vault_user_note(
                note.note_id,
                stale_preview["confirmToken"],
                root=root,
                now=lambda: 201.0,
            )
            path_exists_after = path.exists()

        self.assertEqual(expired["error"], "memory_delete_token_expired")
        self.assertTrue(updated["ok"])
        self.assertEqual(
            stale["error"],
            "memory_note_changed_since_preview",
        )
        self.assertTrue(path_exists_after)

    def test_bootstrap_contract_memory_is_delete_protected(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            paths = bootstrap_memory_vault_source(root=root)
            note = parse_memory_note(paths[0])
            preview = preview_memory_vault_user_note_deletion(
                note.note_id,
                root=root,
            )

        self.assertFalse(preview["ok"])
        self.assertEqual(
            preview["error"],
            "memory_note_delete_protected",
        )
        self.assertEqual(preview["reason"], "bootstrap_contract_note")

    def test_user_memory_snapshot_hides_internal_management_notes_by_default(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            write_memory_vault_note(
                note_type="concept",
                title="Visible User Preference",
                body="Public memory card should remain visible.",
                tags=["memory"],
                root=root,
            )
            runtime_path = write_memory_vault_note(
                note_type="runtime",
                title="Runtime Diagnostic Card",
                body="Internal runtime diagnostic should not appear in public cards.",
                tags=["runtime"],
                root=root,
            )
            runtime_note = parse_memory_note(runtime_path)

            public_snapshot = memory_vault_user_snapshot(root=root)
            internal_snapshot = memory_vault_user_snapshot(root=root, include_internal=True)
            public_detail = memory_vault_user_note(runtime_note.note_id, root=root)
            internal_detail = memory_vault_user_note(runtime_note.note_id, root=root, include_internal=True)

        self.assertIn("Visible User Preference", {card["title"] for card in public_snapshot["cards"]})
        self.assertNotIn("Runtime Diagnostic Card", {card["title"] for card in public_snapshot["cards"]})
        self.assertIn("runtime", public_snapshot["hiddenTypes"])
        self.assertFalse(public_snapshot["includeInternal"])
        self.assertIn("Runtime Diagnostic Card", {card["title"] for card in internal_snapshot["cards"]})
        self.assertTrue(internal_snapshot["includeInternal"])
        self.assertEqual(internal_snapshot["hiddenTypes"], [])
        self.assertFalse(public_detail["ok"])
        self.assertEqual(public_detail["error"], "note_not_found")
        self.assertTrue(internal_detail["ok"])
        self.assertEqual(internal_detail["card"]["title"], "Runtime Diagnostic Card")
        self.assertFalse(internal_detail["card"]["canDelete"])

    def test_vector_index_metadata_and_retrieval_mode(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            write_memory_vault_note(
                note_type="concept",
                title="OmniVoice Cache Strategy",
                body="Cache clone conditioning and prefetch later chunks to reduce first audio latency.",
                tags=["tts", "cache"],
                root=root,
            )
            request = MemoryRecallRequest(
                turn_id="turn-vector",
                session_key=None,
                guild_id=None,
                user_text="clone conditioning cache latency",
                topic_id=None,
                source="test",
                max_items=3,
            )
            result = recall_memory_vault(request, root=root)

        self.assertTrue(result.ok)
        self.assertIn("OmniVoice Cache Strategy", result.context_text)
        self.assertIn("vector", result.metadata["retrieval_mode"])

    def test_daily_consolidation_creates_episode_note(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            rows = [
                {"role": "user", "speaker": "user", "source": "test", "text": f"important memory line {index}"}
                for index in range(40)
            ]
            daily_path = append_turn_rows_to_memory_vault(
                123,
                rows,
                root=root,
            )
            assert daily_path is not None
            path = consolidate_daily_memory_once(123, root=root, min_chars=100)
            assert path is not None
            content = path.read_text(encoding="utf-8")
            consolidated_note = parse_memory_note(path)
            daily_note = parse_memory_note(daily_path)
            result = run_memory_vault_maintenance_once(123, root=root)

        self.assertIn("type: episode", content)
        self.assertIn("important memory line", content)
        self.assertIn(
            daily_note.note_id,
            str(
                consolidated_note.metadata.get(
                    "derived_from"
                )
            ),
        )
        self.assertGreaterEqual(result["memory_version"], 1)

    def test_activation_bootstraps_hot_context_and_legacy_mirror(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            guild_dir = root / "guild_123"
            guild_dir.mkdir(parents=True)
            (guild_dir / "rolling_summary.txt").write_text("User prefers exact implementation.", encoding="utf-8")
            (guild_dir / "durable_facts.jsonl").write_text(
                '{"type":"preference","text":"Use Obsidian-compatible Markdown as durable memory."}\n',
                encoding="utf-8",
            )

            result = activate_memory_vault_for_guild(123, root=root)
            hot_context = read_memory_hot_context(root=root)
            request = MemoryRecallRequest(
                turn_id="turn-activation",
                session_key=None,
                guild_id=123,
                user_text="durable memory markdown source",
                topic_id=None,
                source="test",
                max_items=4,
            )
            recall = recall_memory_vault(request, root=root)
            legacy_content = Path(result["legacy_mirror"]).read_text(encoding="utf-8")

        self.assertTrue(result["bootstrap_notes"])
        self.assertTrue(result["legacy_mirror"])
        self.assertIn("Evelyn Memory Source Contract", hot_context)
        self.assertNotIn("Legacy Guild Memory", hot_context)
        self.assertTrue(recall.ok)
        self.assertIn("Obsidian-compatible Markdown", recall.context_text)

        self.assertIn("# 이블린 메모리", legacy_content)
        self.assertIn("> [!summary] 한눈에 보기", legacy_content)
        self.assertNotIn("## guild_123", legacy_content)
        self.assertNotIn("Legacy Guild Memory", legacy_content)

    def test_legacy_memory_node_notes_restore_graph_nodes(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            guild_dir = root / "guild_123"
            vault_dir = guild_dir / "vault"
            vault_dir.mkdir(parents=True)
            (guild_dir / "rolling_summary.txt").write_text("User prefers conservative progress reports.", encoding="utf-8")
            (vault_dir / "facts.jsonl").write_text(
                '{"type":"preference","text":"Keep Obsidian memory nodes visible."}\n',
                encoding="utf-8",
            )

            nodes = refresh_legacy_memory_node_notes(123, root=root)
            graph = export_memory_graph(root=root, max_nodes=80)
            snapshot = memory_vault_user_snapshot(root=root, limit=80)
            legacy_card = next(card for card in snapshot["cards"] if card["type"] == "legacy")
            detail = memory_vault_user_note(legacy_card["id"], root=root)
            edit = update_memory_vault_user_note(
                legacy_card["id"],
                "edit",
                title="Edited Legacy",
                body="This edit must not be written.",
                root=root,
            )

        self.assertGreaterEqual(len(nodes), 2)
        legacy_nodes = [node for node in graph["nodes"] if node["type"] == "legacy"]
        self.assertGreaterEqual(len(legacy_nodes), 2)
        self.assertTrue(all(node["title"] == "Archived memory" for node in legacy_nodes))
        self.assertTrue(all(node["locked"] for node in legacy_nodes))
        self.assertTrue(all(node["contentHidden"] for node in legacy_nodes))
        self.assertTrue(all(node["canEdit"] is False for node in legacy_nodes))
        self.assertTrue(all(node["snippet"] == "" for node in legacy_nodes))
        self.assertGreaterEqual(len([card for card in snapshot["cards"] if card["type"] == "legacy"]), 2)
        self.assertTrue(legacy_card["locked"])
        self.assertFalse(legacy_card["canEdit"])
        self.assertTrue(legacy_card["contentHidden"])
        self.assertEqual(legacy_card["body"], "")
        self.assertEqual(legacy_card["title"], "Archived memory")
        self.assertIn("Archived memory", legacy_card["preview"])
        self.assertNotIn("Legacy memory", legacy_card["preview"])
        self.assertNotIn("Keep Obsidian memory nodes visible.", legacy_card["preview"])
        self.assertTrue(detail["ok"])
        self.assertEqual(detail["card"]["title"], "Archived memory")
        self.assertEqual(detail["card"]["body"], "")
        self.assertIn("Archived memory", detail["card"]["preview"])
        self.assertNotIn("Legacy memory", detail["card"]["preview"])
        self.assertNotIn("Keep Obsidian memory nodes visible.", detail["card"]["preview"])
        self.assertFalse(edit["ok"])
        self.assertEqual(edit["error"], "locked_legacy_note")

    def test_context_builder_includes_pinned_hot_context(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            activate_memory_vault_for_guild(123, root=root)
            receipt = {}
            context = build_memory_vault_context(
                123,
                "what is the memory structure",
                source="test",
                max_items=3,
                root=root,
                receipt=receipt,
            )

        self.assertIn("[Pinned Memory Vault]", context)
        self.assertIn("Evelyn Memory Source Contract", context)
        self.assertEqual(receipt["schema"], "memory.vault-context-receipt.v1")
        self.assertEqual(receipt["state"], "provided")
        self.assertEqual(receipt["groundingState"], "attributed")
        self.assertGreater(receipt["suppliedNoteCount"], 0)
        self.assertEqual(receipt["suppliedNoteCount"], len(receipt["suppliedNoteIds"]))
        self.assertTrue(receipt["contentFree"])
        self.assertNotIn("Memory Source Contract", str(receipt))

    def test_content_free_receipt_and_audit_project_natural_note_ids(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            private_id = "PRIVATE receipt transcript canary sentence"
            source_label = "PRIVATE natural source label"
            source_path = (
                memory_vault_root(root)
                / "core"
                / "private-receipt-source.md"
            )
            source_path.parent.mkdir(parents=True, exist_ok=True)
            source_path.write_text(
                "\n".join(
                    [
                        "---",
                        f"id: {private_id}",
                        "type: core",
                        "title: Receipt Projection Source",
                        "status: active",
                        f"source: {source_label}",
                        "---",
                        "# Receipt Projection Source",
                        "receipt projection marker body",
                    ]
                ),
                encoding="utf-8",
            )
            source = parse_memory_note(source_path)
            source_ref = (
                source_path.relative_to(memory_vault_root(root))
                .with_suffix("")
                .as_posix()
            )
            source_digest = hashlib.sha1(
                source.body.encode("utf-8")
            ).hexdigest()[:12]
            write_memory_vault_note(
                note_type="episode",
                title="Receipt Projection Target",
                body="target body",
                source="legacy-sub-llm-semantic-consolidation",
                source_refs=[source_ref],
                evidence_hashes=[source_digest],
                root=root,
            )
            refresh_memory_hot_context(root=root)
            receipt: dict[str, object] = {}
            context = build_memory_vault_context(
                123,
                "receipt projection marker",
                source="test",
                max_items=5,
                root=root,
                receipt=receipt,
            )
            cached_receipt: dict[str, object] = {}
            cached_context = build_memory_vault_context(
                123,
                "receipt projection marker",
                source="test",
                max_items=5,
                root=root,
                receipt=cached_receipt,
            )
            memory_provenance_backfill_preview(root=root)
            audit_raw = (
                root
                / "memory_index"
                / "memory_provenance_backfill_audit.json"
            ).read_text(encoding="utf-8")
            audit_payload = json.loads(audit_raw)
            receipt_raw = json.dumps(receipt, ensure_ascii=False)

        self.assertEqual(source.note_id, private_id)
        self.assertIn("receipt projection marker body", context)
        self.assertTrue(receipt["contentFree"])
        self.assertIn("receipt projection marker body", cached_context)
        self.assertTrue(cached_receipt["cacheHit"])
        self.assertEqual(
            cached_receipt["suppliedNoteIds"],
            receipt["suppliedNoteIds"],
        )
        self.assertTrue(audit_payload["contentFree"])
        self.assertNotIn(private_id, receipt_raw)
        self.assertNotIn(private_id, str(cached_receipt))
        self.assertNotIn(source_label, receipt_raw)
        self.assertNotIn(private_id, audit_raw)
        self.assertTrue(
            any(
                str(note_id).startswith("opaque-")
                for note_id in receipt["suppliedNoteIds"]
            )
        )
        self.assertTrue(
            any(
                str(entry["candidateSourceIds"][0]).startswith(
                    "opaque-"
                )
                for entry in audit_payload["entries"]
                if entry.get("candidateSourceIds")
            )
        )

    def test_context_builder_rejects_hot_context_from_an_older_memory_version(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            note_path = write_memory_vault_note(
                note_type="core",
                title="Pinned Version Boundary",
                body="old pinned marker",
                source="user-edit",
                root=root,
            )
            hot_payload = refresh_memory_hot_context(root=root)
            old_version = int(hot_payload["memory_version"])
            self.assertIn(
                "old pinned marker",
                read_memory_hot_context(
                    root=root,
                    expected_memory_version=old_version,
                ),
            )

            note_path.write_text(
                note_path.read_text(encoding="utf-8").replace(
                    "old pinned marker",
                    "new indexed marker",
                ),
                encoding="utf-8",
            )
            new_version = sync_memory_vault_index(root=root)
            receipt = {}
            context = build_memory_vault_context(
                123,
                "new indexed marker",
                source="test",
                max_items=3,
                root=root,
                receipt=receipt,
            )

        self.assertGreater(new_version, old_version)
        self.assertNotIn("[Pinned Memory Vault]", context)
        self.assertNotIn("old pinned marker", context)
        self.assertIn("new indexed marker", context)
        self.assertEqual(receipt["hotContextState"], "stale_memory_version")
        self.assertEqual(receipt["memoryVersion"], new_version)

    def test_sub_llm_dependency_probe_reports_fallback_without_server(self) -> None:
        result = probe_sub_llm_dependency(summary_llm_url="http://127.0.0.1:9/v1/chat/completions", timeout_s=0.05)

        self.assertFalse(result["available"])
        self.assertEqual(result["name"], "sub_llm")
        self.assertEqual(result["fallback_mode"], "deterministic_memory_vault_maintenance")

    def test_maintenance_reports_sub_llm_dependency(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            result = run_memory_vault_maintenance_once(123, root=root)
            hot_context_path = root / "memory_index" / "hot_context.json"
            hot_context_exists = hot_context_path.exists()

        self.assertIn("sub_llm", result["dependencies"])
        self.assertIn("semantic_consolidation_enabled", result)
        self.assertTrue(hot_context_exists)

    def test_semantic_consolidation_skips_when_sub_llm_unavailable(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            rows = [
                {"role": "user", "speaker": "user", "source": "test", "text": f"semantic memory line {index}"}
                for index in range(40)
            ]
            daily_path = append_turn_rows_to_memory_vault(
                123,
                rows,
                root=root,
            )
            assert daily_path is not None
            result = run_semantic_memory_consolidation_once(
                123,
                root=root,
                sub_llm_health={"available": False},
                min_chars=100,
            )

        self.assertEqual(result["status"], "skipped_sub_llm_unavailable")
        self.assertEqual(result["created_notes"], [])

    def test_semantic_consolidation_creates_notes_from_sub_llm_json(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            rows = [
                {"role": "user", "speaker": "user", "source": "test", "text": f"Evelyn should remember structured memory architecture detail {index}"}
                for index in range(30)
            ]
            daily_path = append_turn_rows_to_memory_vault(
                123,
                rows,
                root=root,
            )
            assert daily_path is not None

            def fake_llm(_messages: list[dict]) -> dict:
                return {
                    "notes": [
                        {
                            "type": "concept",
                            "title": "Structured Memory Architecture",
                            "body": "Evelyn should treat Markdown vault notes as durable memory and generated indexes as rebuildable runtime acceleration.",
                            "tags": ["memory", "architecture"],
                            "links": ["Evelyn Memory Source Contract"],
                            "importance": 0.82,
                            "confidence": "high",
                        }
                    ]
                }

            result = run_semantic_memory_consolidation_once(
                123,
                root=root,
                sub_llm_health={"available": True},
                llm_client=fake_llm,
                min_chars=100,
            )
            request = MemoryRecallRequest(
                turn_id="turn-semantic",
                session_key=None,
                guild_id=None,
                user_text="structured memory architecture generated indexes",
                topic_id=None,
                source="test",
                max_items=4,
            )
            recall = recall_memory_vault(request, root=root)
            semantic_note = parse_memory_note(
                Path(result["created_notes"][0])
            )
            semantic_source = parse_memory_note(daily_path)

        self.assertEqual(result["status"], "created")
        self.assertTrue(result["created_notes"])
        self.assertIn(
            semantic_source.note_id,
            str(
                semantic_note.metadata.get("derived_from")
            ),
        )
        self.assertTrue(recall.ok)
        self.assertNotIn("Structured Memory Architecture", recall.context_text)


if __name__ == "__main__":
    unittest.main()
