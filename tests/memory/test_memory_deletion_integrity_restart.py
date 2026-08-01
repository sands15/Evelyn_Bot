from __future__ import annotations

import hashlib
import json
import os
import subprocess
import sys
import tempfile
import textwrap
import threading
import unittest
from pathlib import Path
from typing import Any
from unittest.mock import patch


REPO_ROOT = next(
    path
    for path in Path(__file__).resolve().parents
    if (path / "main.py").exists()
)
RUNTIME_ROOT = REPO_ROOT / "evelyn_core" / "runtime"
if str(RUNTIME_ROOT) not in sys.path:
    sys.path.insert(0, str(RUNTIME_ROOT))

from evelyn_core.assistant_contracts import MemoryRecallRequest  # noqa: E402
from evelyn_core.memory_deletion_journal import (  # noqa: E402
    MEMORY_DELETE_TOMBSTONE_CHAIN_HEAD_NAME,
    MEMORY_DELETE_TOMBSTONE_JOURNAL_NAME,
    MemoryDeletionJournalIntegrityError,
)
from evelyn_core import memory_deletion_journal as deletion_journal  # noqa: E402
from evelyn_core import memory_vault  # noqa: E402
from evelyn_core.memory_vault import (  # noqa: E402
    build_memory_vault_context,
    delete_memory_vault_user_note,
    memory_index_db_path,
    parse_memory_note,
    preview_memory_vault_user_note_deletion,
    recall_memory_vault,
    refresh_memory_hot_context,
    run_semantic_memory_consolidation_once,
    update_memory_vault_user_note,
    write_memory_vault_note,
)


INTEGRITY_ERROR = "memory_deletion_journal_integrity_failed"

FAIL_CLOSED_WORKER = textwrap.dedent(
    """
    import hashlib
    import json
    import sys
    from pathlib import Path

    from evelyn_core.assistant_contracts import MemoryRecallRequest
    from evelyn_core import memory_vault

    root = Path(sys.argv[1])
    note_id = sys.argv[2]
    title = sys.argv[3]
    source_path = Path(sys.argv[4])

    def normalized(value):
        if hasattr(value, "context_text") and hasattr(value, "ok"):
            return {
                "ok": bool(value.ok),
                "context": str(value.context_text or ""),
                "error": str(value.error_text or ""),
                "facts": list(value.facts or ()),
                "sources": list(value.sources or ()),
            }
        if isinstance(value, Path):
            return {"path": str(value)}
        return value

    def capture(callback):
        try:
            return {"kind": "value", "value": normalized(callback())}
        except Exception as exc:
            return {
                "kind": "exception",
                "type": type(exc).__name__,
                "error": str(exc),
            }

    outcomes = {
        "detail": capture(
            lambda: memory_vault.memory_vault_user_note(
                note_id,
                root=root,
            )
        ),
        "snapshot": capture(
            lambda: memory_vault.memory_vault_user_snapshot(root=root)
        ),
        "graph": capture(
            lambda: memory_vault.export_memory_graph(root=root)
        ),
        "recall": capture(
            lambda: memory_vault.recall_memory_vault(
                MemoryRecallRequest(
                    turn_id="integrity-restart-recall",
                    session_key="integrity-restart-session",
                    guild_id=None,
                    user_text=title,
                    topic_id=None,
                    source="test",
                    max_items=3,
                ),
                root=root,
            )
        ),
        "hotContext": capture(
            lambda: memory_vault.read_memory_hot_context(root=root)
        ),
        "sameIdWrite": capture(
            lambda: memory_vault.write_memory_vault_note(
                note_type="project",
                title=title,
                body="attempted resurrection body",
                source="control-page-user",
                root=root,
            )
        ),
    }
    outcomes["sourceExists"] = source_path.exists()
    outcomes["sourceHash"] = (
        hashlib.sha256(source_path.read_bytes()).hexdigest()
        if source_path.exists()
        else ""
    )
    print(json.dumps(outcomes, ensure_ascii=False, sort_keys=True))
    """
)

RECOVERY_WORKER = textwrap.dedent(
    """
    import json
    import sys
    from pathlib import Path

    from evelyn_core.assistant_contracts import MemoryRecallRequest
    from evelyn_core import memory_vault

    root = Path(sys.argv[1])
    first_note_id = sys.argv[2]
    second_note_id = sys.argv[3]

    try:
        version = memory_vault.sync_memory_vault_index(root=root)
        first_deleted = memory_vault.memory_note_was_deleted(
            first_note_id,
            root=root,
        )
        second_deleted = memory_vault.memory_note_was_deleted(
            second_note_id,
            root=root,
        )
        snapshot = memory_vault.memory_vault_user_snapshot(root=root)
        recall = memory_vault.recall_memory_vault(
            MemoryRecallRequest(
                turn_id="journal-lag-recovery",
                session_key="journal-lag-recovery",
                guild_id=None,
                user_text="deleted integrity recovery canary",
                topic_id=None,
                source="test",
                max_items=3,
            ),
            root=root,
        )
        result = {
            "ok": True,
            "version": version,
            "firstDeleted": first_deleted,
            "secondDeleted": second_deleted,
            "snapshotIds": [
                item.get("id") for item in snapshot.get("cards", [])
            ],
            "recallOk": recall.ok,
            "recallError": recall.error_text or "",
            "recallContext": recall.context_text or "",
        }
    except Exception as exc:
        result = {
            "ok": False,
            "type": type(exc).__name__,
            "error": str(exc),
        }
    print(json.dumps(result, ensure_ascii=False, sort_keys=True))
    """
)


class MemoryDeletionIntegrityRestartTests(unittest.TestCase):
    def subprocess_environment(self) -> dict[str, str]:
        environment = os.environ.copy()
        existing = environment.get("PYTHONPATH", "")
        environment["PYTHONPATH"] = os.pathsep.join(
            item
            for item in (str(RUNTIME_ROOT), existing)
            if item
        )
        return environment

    def run_worker(
        self,
        worker: str,
        *arguments: object,
    ) -> dict[str, Any]:
        completed = subprocess.run(
            [
                sys.executable,
                "-c",
                worker,
                *(str(item) for item in arguments),
            ],
            cwd=REPO_ROOT,
            env=self.subprocess_environment(),
            text=True,
            capture_output=True,
            timeout=30,
            check=False,
        )
        self.assertEqual(
            completed.returncode,
            0,
            completed.stderr + completed.stdout,
        )
        lines = [
            line
            for line in completed.stdout.splitlines()
            if line.strip()
        ]
        self.assertTrue(lines, completed.stderr)
        return json.loads(lines[-1])

    def write_confirmed_note(
        self,
        root: Path,
        *,
        suffix: str,
    ) -> tuple[Path, Any, str, str]:
        title = f"Deletion Integrity Canary {suffix}"
        body = f"private deletion integrity body {suffix}"
        path = write_memory_vault_note(
            note_type="project",
            title=title,
            body=body,
            links=[f"integrity-neighbor-{suffix}"],
            source="control-page-user",
            root=root,
        )
        note = parse_memory_note(path)
        update_memory_vault_user_note(
            note.note_id,
            "confirm",
            expected_content_hash=note.source_hash,
            root=root,
        )
        return path, note, title, body

    def delete_note(
        self,
        root: Path,
        note_id: str,
    ) -> dict[str, Any]:
        preview = preview_memory_vault_user_note_deletion(
            note_id,
            reason="privacy_request",
            root=root,
        )
        self.assertTrue(preview.get("ok"), preview)
        result = delete_memory_vault_user_note(
            note_id,
            preview["confirmToken"],
            reason="privacy_request",
            root=root,
        )
        self.assertTrue(result.get("ok"), result)
        return result

    def seed_deleted_note_with_stale_derived_state(
        self,
        root: Path,
        *,
        suffix: str,
    ) -> dict[str, Any]:
        path, note, title, body = self.write_confirmed_note(
            root,
            suffix=suffix,
        )
        hot_payload = refresh_memory_hot_context(root=root)
        self.assertIn(body, str(hot_payload.get("content") or ""))
        request = MemoryRecallRequest(
            turn_id=f"seed-{suffix}",
            session_key="integrity-seed-session",
            guild_id=None,
            user_text=title,
            topic_id=None,
            source="test",
            max_items=3,
        )
        first_recall = recall_memory_vault(request, root=root)
        cached_recall = recall_memory_vault(request, root=root)
        self.assertTrue(first_recall.ok, first_recall.error_text)
        self.assertIn(body, first_recall.context_text)
        self.assertTrue(cached_recall.ok, cached_recall.error_text)
        self.assertTrue(cached_recall.metadata.get("cache_hit"))

        index_path = memory_index_db_path(root)
        hot_path = root / "memory_index" / "hot_context.json"
        prompt_path = (
            root / "memory_index" / "prompt_blocks" / "core_prompt.txt"
        )
        stale_files = {
            index_path: index_path.read_bytes(),
            hot_path: hot_path.read_bytes(),
            prompt_path: prompt_path.read_bytes(),
        }
        source_bytes = path.read_bytes()
        source_hash = hashlib.sha256(source_bytes).hexdigest()

        self.delete_note(root, note.note_id)

        # Restore only rebuildable/stale derived state and the source file. This
        # models a crash or backup race after the durable tombstone committed.
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(source_bytes)
        for stale_path, stale_bytes in stale_files.items():
            stale_path.parent.mkdir(parents=True, exist_ok=True)
            stale_path.write_bytes(stale_bytes)

        index_dir = root / "memory_index"
        journal_path = index_dir / MEMORY_DELETE_TOMBSTONE_JOURNAL_NAME
        head_path = index_dir / MEMORY_DELETE_TOMBSTONE_CHAIN_HEAD_NAME
        self.assertTrue(journal_path.exists())
        self.assertTrue(head_path.exists())
        self.assertIn(note.note_id, journal_path.read_text(encoding="utf-8"))
        return {
            "path": path,
            "noteId": note.note_id,
            "title": title,
            "body": body,
            "sourceHash": source_hash,
            "journalPath": journal_path,
            "headPath": head_path,
        }

    def assert_integrity_failure(
        self,
        outcome: dict[str, Any],
        *,
        state: dict[str, Any],
    ) -> None:
        for surface in (
            "detail",
            "snapshot",
            "graph",
            "hotContext",
            "sameIdWrite",
        ):
            item = outcome[surface]
            if item["kind"] == "exception":
                self.assertEqual(item["error"], INTEGRITY_ERROR, item)
                self.assertEqual(
                    item["type"],
                    "MemoryDeletionJournalIntegrityError",
                    item,
                )
                continue
            value = item.get("value")
            self.assertIsInstance(value, dict, item)
            self.assertFalse(value.get("ok"), item)
            self.assertEqual(value.get("error"), INTEGRITY_ERROR, item)

        recall_item = outcome["recall"]
        self.assertEqual(recall_item["kind"], "value", recall_item)
        recall = recall_item["value"]
        self.assertFalse(recall["ok"], recall)
        self.assertEqual(recall["error"], INTEGRITY_ERROR, recall)
        self.assertEqual(recall["context"], "", recall)
        self.assertEqual(recall["facts"], [], recall)
        self.assertEqual(recall["sources"], [], recall)

        self.assertTrue(outcome["sourceExists"])
        self.assertEqual(outcome["sourceHash"], state["sourceHash"])
        serialized = json.dumps(outcome, ensure_ascii=False, sort_keys=True)
        for secret in (
            state["title"],
            state["body"],
            str(state["path"]),
            str(state["path"].parent),
            str(state["journalPath"]),
        ):
            self.assertNotIn(secret, serialized)
        self.assertNotIn(
            "attempted resurrection body",
            state["path"].read_text(encoding="utf-8"),
        )

    def test_cached_recall_holds_deletion_boundary_until_return(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            _path, note, title, body = self.write_confirmed_note(
                root,
                suffix="recall-linearization",
            )
            preview = preview_memory_vault_user_note_deletion(
                note.note_id,
                reason="privacy_request",
                root=root,
            )
            self.assertTrue(preview.get("ok"), preview)
            request = MemoryRecallRequest(
                turn_id="recall-linearization",
                session_key="recall-linearization",
                guild_id=None,
                user_text=title,
                topic_id=None,
                source="test",
                max_items=3,
            )
            seeded = recall_memory_vault(request, root=root)
            cached = recall_memory_vault(request, root=root)
            self.assertTrue(seeded.ok, seeded.error_text)
            self.assertIn(body, seeded.context_text)
            self.assertTrue(cached.metadata.get("cache_hit"), cached)

            entered = threading.Event()
            release = threading.Event()
            outcome: dict[str, Any] = {}
            original_read_cache = memory_vault._read_retrieval_cache

            def paused_read_cache(*args: Any, **kwargs: Any) -> Any:
                result = original_read_cache(*args, **kwargs)
                entered.set()
                release.wait(timeout=5)
                return result

            def recall_worker() -> None:
                outcome["recall"] = recall_memory_vault(
                    request,
                    root=root,
                )

            worker = threading.Thread(target=recall_worker)
            try:
                with patch.object(
                    memory_vault,
                    "_read_retrieval_cache",
                    side_effect=paused_read_cache,
                ):
                    worker.start()
                    self.assertTrue(entered.wait(timeout=5))
                    with self.assertRaises(
                        MemoryDeletionJournalIntegrityError
                    ) as blocked:
                        delete_memory_vault_user_note(
                            note.note_id,
                            preview["confirmToken"],
                            reason="privacy_request",
                            root=root,
                        )
                    self.assertEqual(str(blocked.exception), INTEGRITY_ERROR)
            finally:
                release.set()
                worker.join(timeout=5)
            self.assertFalse(worker.is_alive())
            in_flight = outcome["recall"]
            self.assertTrue(in_flight.ok, in_flight.error_text)
            self.assertIn(body, in_flight.context_text)

            deleted = delete_memory_vault_user_note(
                note.note_id,
                preview["confirmToken"],
                reason="privacy_request",
                root=root,
            )
            self.assertTrue(deleted.get("ok"), deleted)
            after = recall_memory_vault(request, root=root)
            self.assertTrue(after.ok, after.error_text)
            self.assertNotIn(body, after.context_text)

    def test_snapshot_rejects_valid_out_of_band_position_change(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            _path, note, _title, body = self.write_confirmed_note(
                root,
                suffix="out-of-band-position",
            )
            entered = threading.Event()
            release = threading.Event()
            outcome: dict[str, Any] = {}
            original_card = memory_vault._memory_vault_user_card

            def paused_card(*args: Any, **kwargs: Any) -> Any:
                result = original_card(*args, **kwargs)
                entered.set()
                release.wait(timeout=5)
                return result

            def snapshot_worker() -> None:
                try:
                    outcome["result"] = (
                        memory_vault.memory_vault_user_snapshot(
                            root=root
                        )
                    )
                except Exception as exc:
                    outcome["exception"] = (
                        type(exc).__name__,
                        str(exc),
                    )

            worker = threading.Thread(target=snapshot_worker)
            try:
                with patch.object(
                    memory_vault,
                    "_memory_vault_user_card",
                    side_effect=paused_card,
                ):
                    worker.start()
                    self.assertTrue(entered.wait(timeout=5))
                    index_dir = root / "memory_index"
                    event: dict[str, Any] = {
                        "schema": (
                            deletion_journal.MEMORY_DELETE_TOMBSTONE_V2_SCHEMA
                        ),
                        "noteId": note.note_id,
                        "noteType": note.note_type,
                        "sourceType": "user",
                        "reason": "privacy_request",
                        "deletedAt": "2026-08-01T00:00:00Z",
                        "contentFree": True,
                        "sequence": 1,
                        "previousHash": (
                            deletion_journal.MEMORY_DELETE_TOMBSTONE_CHAIN_GENESIS
                        ),
                    }
                    event["eventHash"] = deletion_journal._event_hash(
                        event
                    )
                    (index_dir / MEMORY_DELETE_TOMBSTONE_JOURNAL_NAME).write_bytes(
                        deletion_journal._canonical_json(event) + b"\n"
                    )
                    head = {
                        "schema": (
                            deletion_journal.MEMORY_DELETE_TOMBSTONE_CHAIN_HEAD_SCHEMA
                        ),
                        "sequence": 1,
                        "eventHash": event["eventHash"],
                        "previousHash": event["previousHash"],
                        "legacyPrefixHash": (
                            deletion_journal.MEMORY_DELETE_TOMBSTONE_CHAIN_GENESIS
                        ),
                        "updatedAt": "2026-08-01T00:00:00Z",
                        "contentFree": True,
                    }
                    (
                        index_dir / MEMORY_DELETE_TOMBSTONE_CHAIN_HEAD_NAME
                    ).write_text(
                        json.dumps(head, ensure_ascii=False),
                        encoding="utf-8",
                    )
            finally:
                release.set()
                worker.join(timeout=5)

        self.assertFalse(worker.is_alive())
        self.assertNotIn("result", outcome)
        self.assertEqual(
            outcome.get("exception"),
            ("MemoryDeletionJournalIntegrityError", INTEGRITY_ERROR),
        )
        self.assertNotIn(body, json.dumps(outcome, ensure_ascii=False))

    def test_build_context_holds_deletion_boundary_until_return(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            _path, note, title, body = self.write_confirmed_note(
                root,
                suffix="context-linearization",
            )
            refresh_memory_hot_context(root=root)
            preview = preview_memory_vault_user_note_deletion(
                note.note_id,
                reason="privacy_request",
                root=root,
            )
            self.assertTrue(preview.get("ok"), preview)

            entered = threading.Event()
            release = threading.Event()
            outcome: dict[str, Any] = {}
            original_validate = (
                memory_vault._validated_memory_hot_context_payload
            )

            def paused_validate(*args: Any, **kwargs: Any) -> Any:
                result = original_validate(*args, **kwargs)
                entered.set()
                release.wait(timeout=5)
                return result

            def context_worker() -> None:
                outcome["context"] = build_memory_vault_context(
                    1,
                    title,
                    root=root,
                )

            worker = threading.Thread(target=context_worker)
            try:
                with patch.object(
                    memory_vault,
                    "_validated_memory_hot_context_payload",
                    side_effect=paused_validate,
                ):
                    worker.start()
                    self.assertTrue(entered.wait(timeout=5))
                    with self.assertRaises(
                        MemoryDeletionJournalIntegrityError
                    ) as blocked:
                        delete_memory_vault_user_note(
                            note.note_id,
                            preview["confirmToken"],
                            reason="privacy_request",
                            root=root,
                        )
                    self.assertEqual(str(blocked.exception), INTEGRITY_ERROR)
            finally:
                release.set()
                worker.join(timeout=5)
            self.assertFalse(worker.is_alive())
            self.assertIn(body, outcome["context"])

            deleted = delete_memory_vault_user_note(
                note.note_id,
                preview["confirmToken"],
                reason="privacy_request",
                root=root,
            )
            self.assertTrue(deleted.get("ok"), deleted)
            self.assertNotIn(
                body,
                build_memory_vault_context(1, title, root=root),
            )

    def test_semantic_llm_boundary_blocks_concurrent_delete(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            day = "2026-08-01"
            secret = "semantic outbound deletion canary"
            source_path = write_memory_vault_note(
                note_type="daily",
                title="Semantic deletion boundary",
                body=(secret + " ") * 12,
                storage_key=day,
                source="conversation-turn-log",
                root=root,
            )
            source_bytes = source_path.read_bytes()
            source_note = parse_memory_note(source_path)
            preview = preview_memory_vault_user_note_deletion(
                source_note.note_id,
                reason="privacy_request",
                root=root,
            )
            self.assertTrue(preview.get("ok"), preview)

            entered = threading.Event()
            release = threading.Event()
            captured: dict[str, Any] = {}
            outcome: dict[str, Any] = {}

            def paused_llm(messages: list[dict[str, Any]]) -> dict[str, Any]:
                captured["messages"] = messages
                entered.set()
                release.wait(timeout=5)
                return {"notes": []}

            def semantic_worker() -> None:
                outcome["semantic"] = (
                    run_semantic_memory_consolidation_once(
                        1,
                        root=root,
                        day_key=day,
                        sub_llm_health={"available": True},
                        llm_client=paused_llm,
                        min_chars=1,
                    )
                )

            worker = threading.Thread(target=semantic_worker)
            try:
                worker.start()
                self.assertTrue(entered.wait(timeout=5))
                with self.assertRaises(
                    MemoryDeletionJournalIntegrityError
                ) as blocked:
                    delete_memory_vault_user_note(
                        source_note.note_id,
                        preview["confirmToken"],
                        reason="privacy_request",
                        root=root,
                    )
                self.assertEqual(str(blocked.exception), INTEGRITY_ERROR)
            finally:
                release.set()
                worker.join(timeout=5)
            self.assertFalse(worker.is_alive())
            self.assertIn(
                secret,
                json.dumps(captured["messages"], ensure_ascii=False),
            )
            self.assertEqual(
                outcome["semantic"].get("status"),
                "no_notes_created",
                outcome["semantic"],
            )

            deleted = delete_memory_vault_user_note(
                source_note.note_id,
                preview["confirmToken"],
                reason="privacy_request",
                root=root,
            )
            self.assertTrue(deleted.get("ok"), deleted)
            source_path.parent.mkdir(parents=True, exist_ok=True)
            source_path.write_bytes(source_bytes)
            calls_after_delete: list[list[dict[str, Any]]] = []

            def forbidden_llm(
                messages: list[dict[str, Any]],
            ) -> dict[str, Any]:
                calls_after_delete.append(messages)
                return {"notes": []}

            after = run_semantic_memory_consolidation_once(
                1,
                root=root,
                day_key=day,
                sub_llm_health={"available": True},
                llm_client=forbidden_llm,
                min_chars=1,
            )
            self.assertEqual(
                after.get("status"),
                "skipped_source_deleted_or_changed",
                after,
            )
            self.assertEqual(calls_after_delete, [])

    def test_torn_malformed_journal_fails_closed_after_restart(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            state = self.seed_deleted_note_with_stale_derived_state(
                root,
                suffix="torn-journal",
            )
            with state["journalPath"].open("ab") as handle:
                handle.write(b'{"schema":"memory.deletion.tombstone.v2"')
                handle.flush()
                os.fsync(handle.fileno())

            outcome = self.run_worker(
                FAIL_CLOSED_WORKER,
                root,
                state["noteId"],
                state["title"],
                state["path"],
            )
            self.assert_integrity_failure(outcome, state=state)

    def test_missing_journal_with_chain_head_fails_closed_after_restart(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            state = self.seed_deleted_note_with_stale_derived_state(
                root,
                suffix="missing-journal",
            )
            self.assertTrue(state["headPath"].exists())
            state["journalPath"].unlink()

            outcome = self.run_worker(
                FAIL_CLOSED_WORKER,
                root,
                state["noteId"],
                state["title"],
                state["path"],
            )
            self.assert_integrity_failure(outcome, state=state)

    def test_one_event_journal_ahead_of_head_recovers_after_restart(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            first_path, first_note, first_title, first_body = (
                self.write_confirmed_note(root, suffix="lag-first")
            )
            self.delete_note(root, first_note.note_id)
            index_dir = root / "memory_index"
            head_path = (
                index_dir / MEMORY_DELETE_TOMBSTONE_CHAIN_HEAD_NAME
            )
            journal_path = (
                index_dir / MEMORY_DELETE_TOMBSTONE_JOURNAL_NAME
            )
            head_after_first = head_path.read_bytes()
            first_head_payload = json.loads(
                head_after_first.decode("utf-8")
            )

            second_path, second_note, second_title, second_body = (
                self.write_confirmed_note(root, suffix="lag-second")
            )
            self.delete_note(root, second_note.note_id)
            committed_head_payload = json.loads(
                head_path.read_text(encoding="utf-8")
            )
            self.assertEqual(
                int(committed_head_payload["sequence"]),
                int(first_head_payload["sequence"]) + 1,
            )

            # Simulate the sole recoverable crash window: journal event fsync
            # completed, but the atomic head replacement still names event N-1.
            head_path.write_bytes(head_after_first)
            lagging_head_payload = json.loads(
                head_path.read_text(encoding="utf-8")
            )
            self.assertEqual(lagging_head_payload, first_head_payload)

            outcome = self.run_worker(
                RECOVERY_WORKER,
                root,
                first_note.note_id,
                second_note.note_id,
            )
            recovered_head_payload = json.loads(
                head_path.read_text(encoding="utf-8")
            )
            journal_raw = journal_path.read_text(encoding="utf-8")

            self.assertTrue(outcome["ok"], outcome)
            self.assertTrue(outcome["firstDeleted"])
            self.assertTrue(outcome["secondDeleted"])
            self.assertNotIn(first_note.note_id, outcome["snapshotIds"])
            self.assertNotIn(second_note.note_id, outcome["snapshotIds"])
            self.assertTrue(outcome["recallOk"], outcome)
            self.assertEqual(outcome["recallError"], "")
            self.assertNotIn(first_title, outcome["recallContext"])
            self.assertNotIn(second_title, outcome["recallContext"])
            for key in ("sequence", "eventHash", "legacyPrefixHash"):
                if key in committed_head_payload:
                    self.assertEqual(
                        recovered_head_payload.get(key),
                        committed_head_payload[key],
                    )
            self.assertNotEqual(
                recovered_head_payload,
                lagging_head_payload,
            )
            self.assertFalse(first_path.exists())
            self.assertFalse(second_path.exists())
            for secret in (
                first_title,
                first_body,
                second_title,
                second_body,
            ):
                self.assertNotIn(secret, journal_raw)
                self.assertNotIn(
                    secret,
                    json.dumps(recovered_head_payload, ensure_ascii=False),
                )


if __name__ == "__main__":
    unittest.main()
