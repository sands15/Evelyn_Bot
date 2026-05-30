from __future__ import annotations

import sys
import tempfile
import unittest
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[1]
RUNTIME_ROOT = REPO_ROOT / "evelyn_core" / "runtime"
if str(RUNTIME_ROOT) not in sys.path:
    sys.path.insert(0, str(RUNTIME_ROOT))

from evelyn_core.assistant_contracts import MemoryRecallRequest  # noqa: E402
from evelyn_core.memory_vault import (  # noqa: E402
    activate_memory_vault_for_guild,
    append_turn_rows_to_memory_vault,
    build_memory_vault_context,
    consolidate_daily_memory_once,
    export_memory_graph,
    mark_memory_note_superseded,
    memory_vault_root,
    parse_memory_note,
    probe_sub_llm_dependency,
    read_memory_hot_context,
    recall_memory_vault,
    run_memory_vault_maintenance_once,
    run_semantic_memory_consolidation_once,
    sync_memory_vault_index,
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
                        "type: procedure",
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

        self.assertIn("type: daily", content)
        self.assertIn("remember this preference", content)
        self.assertIn("- 정훈: remember this preference", content)
        self.assertNotIn("guild:123", content)
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

    def test_graph_link_expands_related_note(self) -> None:
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
        self.assertIn("Test Evelyn TTS", result.context_text)
        self.assertIn(result.metadata["retrieval_mode"], {"fts", "scan", "fts+vector", "scan+vector"})

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

            graph = export_memory_graph(root=root)

        self.assertTrue(graph["ok"])
        self.assertGreaterEqual(graph["stats"]["node_count"], 2)
        self.assertGreaterEqual(graph["stats"]["edge_count"], 1)
        node_titles = {node["title"] for node in graph["nodes"]}
        edge_types = {edge["type"] for edge in graph["edges"]}
        self.assertIn("Memory Graph Core", node_titles)
        self.assertIn("Memory Graph Procedure", node_titles)
        self.assertTrue({"related", "shared_tag", "semantic_similarity"} & edge_types)

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

        self.assertTrue(result["bootstrap_notes"])
        self.assertTrue(result["legacy_mirror"])
        self.assertIn("Evelyn Memory Source Contract", hot_context)
        self.assertNotIn("Legacy Guild Memory", hot_context)
        self.assertTrue(recall.ok)
        self.assertIn("Obsidian-compatible Markdown", recall.context_text)

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
