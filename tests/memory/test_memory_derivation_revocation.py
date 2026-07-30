from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
import textwrap
import unittest
from pathlib import Path


REPO_ROOT = next(
    path
    for path in Path(__file__).resolve().parents
    if (path / "main.py").exists()
)
RUNTIME_ROOT = REPO_ROOT / "evelyn_core" / "runtime"
if str(RUNTIME_ROOT) not in sys.path:
    sys.path.insert(0, str(RUNTIME_ROOT))

from evelyn_core.assistant_contracts import MemoryRecallRequest  # noqa: E402
from evelyn_core.memory_derivation_revocation import (  # noqa: E402
    DerivationNode,
    resolve_derivation_states,
)
from evelyn_core.memory_vault import (  # noqa: E402
    delete_memory_vault_user_note,
    export_memory_graph,
    memory_note_was_deleted,
    memory_vault_user_snapshot,
    parse_memory_note,
    preview_memory_vault_user_note_deletion,
    read_memory_hot_context,
    recall_memory_vault,
    refresh_memory_hot_context,
    run_memory_derivation_recomposition_once,
    update_memory_vault_user_note,
    write_memory_vault_note,
)


RECOVERY_WORKER = textwrap.dedent(
    r"""
    import json
    import sys
    from pathlib import Path

    from evelyn_core.assistant_contracts import MemoryRecallRequest
    from evelyn_core.memory_vault import (
        export_memory_graph,
        memory_vault_user_snapshot,
        recall_memory_vault,
    )

    root = Path(sys.argv[1])
    multi_id = sys.argv[2]
    downstream_id = sys.argv[3]
    request = MemoryRecallRequest(
        turn_id="restart-derivation-recall",
        session_key="restart-derivation",
        guild_id=None,
        user_text="apricot blueberry derived",
        topic_id=None,
        source="restart-test",
        max_items=8,
    )
    recall = recall_memory_vault(request, root=root)
    snapshot = memory_vault_user_snapshot(root=root)
    graph = export_memory_graph(root=root)
    cards = {item["id"]: item for item in snapshot["cards"]}
    graph_ids = {item["id"] for item in graph["nodes"]}
    print(json.dumps({
        "recallOk": recall.ok,
        "recallContext": recall.context_text,
        "multiQuarantined": cards[multi_id]["quarantined"],
        "downstreamQuarantined": cards[downstream_id]["quarantined"],
        "multiInGraph": multi_id in graph_ids,
        "downstreamInGraph": downstream_id in graph_ids,
    }, ensure_ascii=False))
    """
)

DERIVATION_CRASH_EXIT_CODE = 79
DERIVATION_CRASH_WORKER = textwrap.dedent(
    r"""
    import os
    import sys
    from pathlib import Path

    from evelyn_core import memory_vault

    root = Path(sys.argv[1])
    note_id = sys.argv[2]
    preview = memory_vault.preview_memory_vault_user_note_deletion(
        note_id,
        reason="privacy_request",
        root=root,
    )
    original_append = (
        memory_vault._append_memory_deletion_tombstone
    )

    def append_then_crash(payload, *, root=None):
        original_append(payload, root=root)
        os._exit(79)

    memory_vault._append_memory_deletion_tombstone = (
        append_then_crash
    )
    memory_vault.delete_memory_vault_user_note(
        note_id,
        preview["confirmToken"],
        reason="privacy_request",
        root=root,
    )
    raise SystemExit(97)
    """
)


def _request(text: str) -> MemoryRecallRequest:
    return MemoryRecallRequest(
        turn_id="turn-derivation-revocation",
        session_key="derivation-session",
        guild_id=None,
        user_text=text,
        topic_id=None,
        source="test",
        max_items=8,
    )


class DerivationGraphContractTests(unittest.TestCase):
    def node(
        self,
        note_id: str,
        *dependencies: str,
    ) -> DerivationNode:
        return DerivationNode(
            note_id=note_id,
            title=note_id,
            note_type="concept",
            source_hash=f"hash-{note_id}",
            derived_from=tuple(dependencies),
        )

    def test_single_source_chain_is_cascade_deleted(self) -> None:
        nodes = {
            "source": self.node("source"),
            "summary": self.node("summary", "source"),
            "concept": self.node("concept", "summary"),
        }

        result = resolve_derivation_states(
            nodes,
            deleted_note_ids={"source"},
        )

        self.assertEqual(
            result.deleted_note_ids,
            frozenset({"source", "summary", "concept"}),
        )
        self.assertEqual(
            result.quarantined_note_ids,
            frozenset(),
        )

    def test_multi_source_and_downstream_are_quarantined(self) -> None:
        nodes = {
            "source-a": self.node("source-a"),
            "source-b": self.node("source-b"),
            "merged": self.node(
                "merged",
                "source-a",
                "source-b",
            ),
            "downstream": self.node(
                "downstream",
                "merged",
            ),
        }

        result = resolve_derivation_states(
            nodes,
            deleted_note_ids={"source-a"},
        )

        self.assertEqual(
            result.deleted_note_ids,
            frozenset({"source-a"}),
        )
        self.assertEqual(
            result.quarantined_note_ids,
            frozenset({"merged", "downstream"}),
        )
        self.assertEqual(
            result.reasons["merged"].remaining_source_ids,
            ("source-b",),
        )
        self.assertEqual(
            result.reasons["downstream"].blocked_source_ids,
            ("merged",),
        )

    def test_seeded_quarantine_stays_fail_closed(self) -> None:
        nodes = {
            "source": self.node("source"),
            "merged": self.node("merged", "source"),
            "downstream": self.node(
                "downstream",
                "merged",
            ),
        }

        result = resolve_derivation_states(
            nodes,
            seeded_quarantine_ids={"merged"},
        )

        self.assertEqual(
            result.quarantined_note_ids,
            frozenset({"merged", "downstream"}),
        )


class MemoryDerivationRevocationIntegrationTests(
    unittest.TestCase
):
    def create_fixture(
        self,
        root: Path,
    ) -> dict[str, object]:
        source_a_path = write_memory_vault_note(
            note_type="concept",
            title="Sensitive Source A",
            body="revoked apricot secret from source A",
            source="control-page-user",
            root=root,
        )
        source_b_path = write_memory_vault_note(
            note_type="concept",
            title="Live Source B",
            body="live blueberry evidence from source B",
            source="control-page-user",
            root=root,
        )
        source_a = parse_memory_note(source_a_path)
        source_b = parse_memory_note(source_b_path)
        single_path = write_memory_vault_note(
            note_type="episode",
            title="Single Source Derived",
            body="single derived apricot secret",
            source="sub-llm-semantic-consolidation",
            derived_from=[source_a.note_id],
            evidence_hashes=[source_a.source_hash],
            root=root,
        )
        multi_path = write_memory_vault_note(
            note_type="concept",
            title="Multi Source Derived",
            body=(
                "old merged apricot secret and blueberry "
                "evidence"
            ),
            source="sub-llm-semantic-consolidation",
            derived_from=[
                source_a.note_id,
                source_b.note_id,
            ],
            evidence_hashes=[
                source_a.source_hash,
                source_b.source_hash,
            ],
            root=root,
        )
        single = parse_memory_note(single_path)
        multi = parse_memory_note(multi_path)
        downstream_path = write_memory_vault_note(
            note_type="project",
            title="Downstream Derived",
            body="old downstream apricot conclusion",
            source="sub-llm-semantic-consolidation",
            derived_from=[multi.note_id],
            evidence_hashes=[multi.source_hash],
            root=root,
        )
        downstream = parse_memory_note(downstream_path)
        return {
            "source_a_path": source_a_path,
            "source_b_path": source_b_path,
            "single_path": single_path,
            "multi_path": multi_path,
            "downstream_path": downstream_path,
            "source_a": source_a,
            "source_b": source_b,
            "single": single,
            "multi": multi,
            "downstream": downstream,
        }

    def delete_source_a(
        self,
        fixture: dict[str, object],
        root: Path,
    ) -> tuple[dict[str, object], dict[str, object]]:
        source_a = fixture["source_a"]
        preview = preview_memory_vault_user_note_deletion(
            source_a.note_id,
            reason="privacy_request",
            root=root,
            now=lambda: 100.0,
        )
        applied = delete_memory_vault_user_note(
            source_a.note_id,
            preview["confirmToken"],
            reason="privacy_request",
            root=root,
            now=lambda: 101.0,
        )
        return preview, applied

    def test_preview_apply_cascades_and_quarantines_fail_closed(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            fixture = self.create_fixture(root)
            hot_before = refresh_memory_hot_context(root=root)
            preview, applied = self.delete_source_a(
                fixture,
                root,
            )
            multi = fixture["multi"]
            downstream = fixture["downstream"]
            single = fixture["single"]
            recall = recall_memory_vault(
                _request("apricot blueberry derived"),
                root=root,
            )
            snapshot = memory_vault_user_snapshot(root=root)
            graph = export_memory_graph(root=root)
            confirm = update_memory_vault_user_note(
                multi.note_id,
                "confirm",
                root=root,
            )
            state_path = (
                root
                / "memory_index"
                / "memory_derivation_revocations.json"
            )
            state_raw = state_path.read_text(encoding="utf-8")
            state_payload = json.loads(state_raw)
            tombstone_raw = (
                root
                / "memory_index"
                / "memory_deletions.jsonl"
            ).read_text(encoding="utf-8")
            source_a_exists = fixture[
                "source_a_path"
            ].exists()
            single_exists = fixture["single_path"].exists()
            multi_exists = fixture["multi_path"].exists()
            downstream_exists = fixture[
                "downstream_path"
            ].exists()
            single_was_deleted = memory_note_was_deleted(
                single.note_id,
                root=root,
            )
            hot_after = read_memory_hot_context(root=root)

        impact = preview["derivationImpact"]
        self.assertEqual(impact["cascadeDeleteCount"], 1)
        self.assertEqual(impact["quarantineCount"], 2)
        self.assertEqual(
            {item["id"] for item in impact["cascadeDelete"]},
            {single.note_id},
        )
        self.assertEqual(
            {item["id"] for item in impact["quarantine"]},
            {multi.note_id, downstream.note_id},
        )
        self.assertTrue(applied["ok"])
        self.assertFalse(source_a_exists)
        self.assertFalse(single_exists)
        self.assertTrue(multi_exists)
        self.assertTrue(downstream_exists)
        self.assertTrue(single_was_deleted)
        self.assertIn(
            "old downstream apricot conclusion",
            hot_before["content"],
        )
        self.assertNotIn(
            "old downstream apricot conclusion",
            hot_after,
        )
        self.assertNotIn("old merged apricot", recall.context_text)
        self.assertNotIn(
            "old downstream apricot",
            recall.context_text,
        )
        cards = {card["id"]: card for card in snapshot["cards"]}
        self.assertTrue(cards[multi.note_id]["quarantined"])
        self.assertFalse(cards[multi.note_id]["recallEligible"])
        self.assertFalse(cards[multi.note_id]["canConfirm"])
        self.assertTrue(
            cards[downstream.note_id]["quarantined"]
        )
        graph_ids = {node["id"] for node in graph["nodes"]}
        self.assertNotIn(multi.note_id, graph_ids)
        self.assertNotIn(downstream.note_id, graph_ids)
        self.assertEqual(
            confirm["error"],
            "memory_note_quarantined",
        )
        self.assertEqual(
            set(state_payload["entries"]),
            {multi.note_id, downstream.note_id},
        )
        for private_text in (
            "Sensitive Source A",
            "revoked apricot secret",
            "old merged apricot",
            "old downstream apricot",
        ):
            self.assertNotIn(private_text, state_raw)
            self.assertNotIn(private_text, tombstone_raw)

    def test_recomposition_uses_only_live_sources(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            fixture = self.create_fixture(root)
            self.delete_source_a(fixture, root)
            prompts: list[str] = []

            def fake_llm(
                messages: list[dict[str, str]],
            ) -> dict[str, object]:
                prompts.append(
                    json.dumps(messages, ensure_ascii=False)
                )
                return {
                    "note": {
                        "title": "Recomposed Live Memory",
                        "body": (
                            "Only live blueberry evidence remains."
                        ),
                        "tags": ["recomposed"],
                        "links": [],
                        "confidence": "high",
                    }
                }

            result = run_memory_derivation_recomposition_once(
                root=root,
                sub_llm_health={"available": True},
                llm_client=fake_llm,
                max_notes=4,
            )
            multi = parse_memory_note(
                fixture["multi_path"]
            )
            multi_raw = fixture["multi_path"].read_text(
                encoding="utf-8"
            )
            downstream = parse_memory_note(
                fixture["downstream_path"]
            )
            recall = recall_memory_vault(
                _request("blueberry live recomposed"),
                root=root,
            )
            state_payload = json.loads(
                (
                    root
                    / "memory_index"
                    / "memory_derivation_revocations.json"
                ).read_text(encoding="utf-8")
            )

        self.assertEqual(result["status"], "recomposed")
        self.assertEqual(result["pendingNoteIds"], [])
        self.assertEqual(len(result["recomposedNoteIds"]), 2)
        self.assertEqual(
            list(multi.metadata["derived_from"].strip("[]").split(",")),
            [fixture["source_b"].note_id],
        )
        self.assertEqual(
            multi.metadata["source"],
            "sub-llm-partial-recomposition",
        )
        self.assertNotIn(
            fixture["source_a"].source_hash,
            multi_raw,
        )
        self.assertEqual(
            downstream.metadata["source"],
            "sub-llm-partial-recomposition",
        )
        joined_prompts = "\n".join(prompts)
        self.assertNotIn(
            "revoked apricot secret from source A",
            joined_prompts,
        )
        self.assertNotIn(
            "old merged apricot secret",
            joined_prompts,
        )
        self.assertNotIn(
            "old downstream apricot conclusion",
            joined_prompts,
        )
        self.assertIn(
            "live blueberry evidence from source B",
            joined_prompts,
        )
        self.assertIn(
            "Only live blueberry evidence remains.",
            recall.context_text,
        )
        self.assertNotIn("apricot", recall.context_text)
        self.assertEqual(state_payload["entries"], {})

    def test_unavailable_sub_llm_keeps_quarantine(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            fixture = self.create_fixture(root)
            self.delete_source_a(fixture, root)
            original_multi = fixture["multi_path"].read_text(
                encoding="utf-8"
            )
            result = run_memory_derivation_recomposition_once(
                root=root,
                sub_llm_health={"available": False},
            )
            current_multi = fixture["multi_path"].read_text(
                encoding="utf-8"
            )

        self.assertEqual(
            result["status"],
            "skipped_sub_llm_unavailable",
        )
        self.assertEqual(original_multi, current_multi)
        self.assertIn(
            fixture["multi"].note_id,
            result["pendingNoteIds"],
        )

    def test_apply_rejects_changed_derivation_impact(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            source_path = write_memory_vault_note(
                note_type="concept",
                title="Impact Source",
                body="impact source body",
                root=root,
            )
            source = parse_memory_note(source_path)
            preview = preview_memory_vault_user_note_deletion(
                source.note_id,
                root=root,
                now=lambda: 300.0,
            )
            write_memory_vault_note(
                note_type="episode",
                title="Late Derived Note",
                body="created after deletion preview",
                source="sub-llm-semantic-consolidation",
                derived_from=[source.note_id],
                root=root,
            )
            applied = delete_memory_vault_user_note(
                source.note_id,
                preview["confirmToken"],
                root=root,
                now=lambda: 301.0,
            )
            source_exists = source_path.exists()

        self.assertEqual(
            applied["error"],
            "memory_derivation_impact_changed_since_preview",
        )
        self.assertTrue(source_exists)

    def test_user_edit_rebuilds_hot_context_without_old_body(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            path = write_memory_vault_note(
                note_type="project",
                title="Editable Hot Project",
                body="old hot context pear secret",
                source="control-page-user",
                root=root,
            )
            note = parse_memory_note(path)
            before = refresh_memory_hot_context(root=root)
            edited = update_memory_vault_user_note(
                note.note_id,
                "edit",
                title="Corrected Hot Project",
                body="new hot context plum truth",
                expected_content_hash=note.source_hash,
                root=root,
            )
            after = read_memory_hot_context(root=root)

        self.assertIn(
            "old hot context pear secret",
            before["content"],
        )
        self.assertTrue(edited["ok"])
        self.assertIn("new hot context plum truth", after)
        self.assertNotIn("old hot context pear secret", after)

    def test_quarantine_survives_fresh_process_restart(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            fixture = self.create_fixture(root)
            self.delete_source_a(fixture, root)
            environment = os.environ.copy()
            environment["PYTHONPATH"] = os.pathsep.join(
                item
                for item in (
                    str(RUNTIME_ROOT),
                    environment.get("PYTHONPATH", ""),
                )
                if item
            )
            recovered = subprocess.run(
                [
                    sys.executable,
                    "-c",
                    RECOVERY_WORKER,
                    str(root),
                    fixture["multi"].note_id,
                    fixture["downstream"].note_id,
                ],
                cwd=REPO_ROOT,
                env=environment,
                text=True,
                capture_output=True,
                timeout=20,
                check=False,
            )
            state_raw = (
                root
                / "memory_index"
                / "memory_derivation_revocations.json"
            ).read_text(encoding="utf-8")

        self.assertEqual(
            recovered.returncode,
            0,
            recovered.stderr + recovered.stdout,
        )
        payload = json.loads(recovered.stdout)
        self.assertTrue(payload["recallOk"])
        self.assertTrue(payload["multiQuarantined"])
        self.assertTrue(
            payload["downstreamQuarantined"]
        )
        self.assertFalse(payload["multiInGraph"])
        self.assertFalse(payload["downstreamInGraph"])
        self.assertNotIn(
            "old merged apricot",
            payload["recallContext"],
        )
        self.assertNotIn(
            "old downstream apricot",
            payload["recallContext"],
        )
        self.assertNotIn(
            "revoked apricot secret",
            state_raw,
        )

    def test_root_tombstone_crash_recovers_derived_graph(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            fixture = self.create_fixture(root)
            environment = os.environ.copy()
            environment["PYTHONPATH"] = os.pathsep.join(
                item
                for item in (
                    str(RUNTIME_ROOT),
                    environment.get("PYTHONPATH", ""),
                )
                if item
            )
            crashed = subprocess.run(
                [
                    sys.executable,
                    "-c",
                    DERIVATION_CRASH_WORKER,
                    str(root),
                    fixture["source_a"].note_id,
                ],
                cwd=REPO_ROOT,
                env=environment,
                text=True,
                capture_output=True,
                timeout=20,
                check=False,
            )
            journal_path = (
                root
                / "memory_index"
                / "memory_deletions.jsonl"
            )
            journal_after_crash = journal_path.read_text(
                encoding="utf-8"
            )
            revocation_path = (
                root
                / "memory_index"
                / "memory_derivation_revocations.json"
            )
            sources_exist_after_crash = {
                name: fixture[name].exists()
                for name in (
                    "source_a_path",
                    "single_path",
                    "multi_path",
                    "downstream_path",
                )
            }
            revocation_exists_after_crash = (
                revocation_path.exists()
            )

            recovered = subprocess.run(
                [
                    sys.executable,
                    "-c",
                    RECOVERY_WORKER,
                    str(root),
                    fixture["multi"].note_id,
                    fixture["downstream"].note_id,
                ],
                cwd=REPO_ROOT,
                env=environment,
                text=True,
                capture_output=True,
                timeout=20,
                check=False,
            )
            journal_after_recovery = journal_path.read_text(
                encoding="utf-8"
            )
            sources_exist_after_recovery = {
                name: fixture[name].exists()
                for name in (
                    "source_a_path",
                    "single_path",
                    "multi_path",
                    "downstream_path",
                )
            }
            revocation_payload = json.loads(
                revocation_path.read_text(encoding="utf-8")
            )

        self.assertEqual(
            crashed.returncode,
            DERIVATION_CRASH_EXIT_CODE,
            crashed.stderr + crashed.stdout,
        )
        self.assertTrue(
            all(sources_exist_after_crash.values())
        )
        self.assertFalse(revocation_exists_after_crash)
        self.assertIn(
            fixture["source_a"].note_id,
            journal_after_crash,
        )
        self.assertNotIn(
            fixture["single"].note_id,
            journal_after_crash,
        )
        self.assertEqual(
            recovered.returncode,
            0,
            recovered.stderr + recovered.stdout,
        )
        self.assertFalse(
            sources_exist_after_recovery["source_a_path"]
        )
        self.assertFalse(
            sources_exist_after_recovery["single_path"]
        )
        self.assertTrue(
            sources_exist_after_recovery["multi_path"]
        )
        self.assertTrue(
            sources_exist_after_recovery["downstream_path"]
        )
        self.assertIn(
            fixture["single"].note_id,
            journal_after_recovery,
        )
        self.assertEqual(
            set(revocation_payload["entries"]),
            {
                fixture["multi"].note_id,
                fixture["downstream"].note_id,
            },
        )


if __name__ == "__main__":
    unittest.main()
