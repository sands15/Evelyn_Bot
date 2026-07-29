from __future__ import annotations

import sqlite3
import sys
import tempfile
import unittest
from pathlib import Path


REPO_ROOT = next(path for path in Path(__file__).resolve().parents if (path / "main.py").exists())
RUNTIME_ROOT = REPO_ROOT / "evelyn_core" / "runtime"
if str(RUNTIME_ROOT) not in sys.path:
    sys.path.insert(0, str(RUNTIME_ROOT))

from evelyn_core.assistant_contracts import MemoryRecallRequest  # noqa: E402
from evelyn_core.memory_vault import (  # noqa: E402
    MEMORY_DELETE_TOMBSTONE_SCHEMA,
    MEMORY_PROVENANCE_SCHEMA,
    MemoryNoteDeletedError,
    activate_memory_vault_for_guild,
    append_turn_rows_to_memory_vault,
    bootstrap_memory_vault_source,
    build_memory_vault_context,
    consolidate_daily_memory_once,
    delete_memory_vault_user_note,
    export_memory_graph,
    mark_memory_note_superseded,
    memory_note_was_deleted,
    memory_vault_user_note,
    memory_vault_user_snapshot,
    memory_vault_root,
    parse_memory_note,
    preview_memory_vault_user_note_deletion,
    probe_sub_llm_dependency,
    read_memory_hot_context,
    recall_memory_vault,
    refresh_legacy_memory_node_notes,
    run_memory_vault_maintenance_once,
    run_semantic_memory_consolidation_once,
    sync_memory_vault_index,
    update_memory_vault_user_note,
    write_memory_vault_note,
)


class MemoryVaultTests(unittest.TestCase):
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
        self.assertTrue(second.metadata["cache_hit"])

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
            confirmed = update_memory_vault_user_note(note_id, "confirm", root=root)
            pinned = update_memory_vault_user_note(note_id, "pin", root=root)
            second = memory_vault_user_snapshot(root=root)
            hidden = update_memory_vault_user_note(note_id, "hide", root=root)
            third = memory_vault_user_snapshot(root=root)
            state_path = root / "memory_index" / "user_note_state.json"
            note_raw = Path(first["vaultPath"]) / first["cards"][0]["path"]
            state_exists = state_path.exists()
            raw_after_actions = note_raw.read_text(encoding="utf-8")

        self.assertEqual(first["counts"]["unconfirmed"], 1)
        self.assertIn("body", first["cards"][0])
        self.assertFalse(first["cards"][0]["body"].startswith("#"))
        self.assertTrue(first["cards"][0]["body"])
        self.assertTrue(confirmed["ok"])
        self.assertTrue(pinned["ok"])
        self.assertTrue(second["cards"][0]["confirmed"])
        self.assertTrue(second["cards"][0]["pinned"])
        self.assertTrue(hidden["ok"])
        self.assertEqual(third["counts"]["total"], 0)
        self.assertTrue(state_exists)
        self.assertNotIn("confirmed_at", raw_after_actions)

    def test_provenance_is_exposed_in_cards_and_cached_recall(self) -> None:
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
        self.assertIn("[Memory Provenance]", first.context_text)
        self.assertEqual(
            first.metadata["provenance"][0]["schema"],
            MEMORY_PROVENANCE_SCHEMA,
        )
        self.assertTrue(second.metadata["cache_hit"])
        self.assertEqual(
            second.metadata["provenance"],
            first.metadata["provenance"],
        )
        self.assertNotIn(r"C:\private", first.context_text)

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
            {"created_at", "source", "source_refs", "derived_from", "evidence_hashes"}
            <= columns
        )
        self.assertEqual(schema_version, "4")
        self.assertEqual(row[0], "control-page-user")
        self.assertIn("user-request", row[1])
        self.assertIn("daily-source", row[2])
        self.assertIn("evidence-hash", row[3])

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
            update_memory_vault_user_note(note.note_id, "confirm", root=root)
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
        self.assertNotIn(title, tombstone_raw)
        self.assertNotIn(body, tombstone_raw)
        self.assertTrue(was_deleted)
        self.assertIsInstance(recreated_error, MemoryNoteDeletedError)
        self.assertEqual(reused["error"], "memory_delete_token_reused")

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
            append_turn_rows_to_memory_vault(123, rows, root=root)
            path = consolidate_daily_memory_once(123, root=root, min_chars=100)
            assert path is not None
            content = path.read_text(encoding="utf-8")
            result = run_memory_vault_maintenance_once(123, root=root)

        self.assertIn("type: episode", content)
        self.assertIn("important memory line", content)
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
            context = build_memory_vault_context(
                123,
                "what is the memory structure",
                source="test",
                max_items=3,
                root=root,
            )

        self.assertIn("[Pinned Memory Vault]", context)
        self.assertIn("Evelyn Memory Source Contract", context)

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
            append_turn_rows_to_memory_vault(123, rows, root=root)
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
            append_turn_rows_to_memory_vault(123, rows, root=root)

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

        self.assertEqual(result["status"], "created")
        self.assertTrue(result["created_notes"])
        self.assertTrue(recall.ok)
        self.assertIn("Structured Memory Architecture", recall.context_text)


if __name__ == "__main__":
    unittest.main()
