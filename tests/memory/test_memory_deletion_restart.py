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
from evelyn_core.memory_vault import (  # noqa: E402
    memory_index_db_path,
    parse_memory_note,
    recall_memory_vault,
    refresh_memory_hot_context,
    update_memory_vault_user_note,
    write_memory_vault_note,
)


CRASH_EXIT_CODE = 73

CRASH_WORKER = textwrap.dedent(
    f"""
    import os
    import sys
    from pathlib import Path

    from evelyn_core import memory_vault

    root = Path(sys.argv[1])
    note_id = sys.argv[2]
    original_append = memory_vault._append_memory_deletion_tombstone

    def append_then_crash(payload, *, root=None):
        original_append(payload, root=root)
        os._exit({CRASH_EXIT_CODE})

    preview = memory_vault.preview_memory_vault_user_note_deletion(
        note_id,
        reason="privacy_request",
        root=root,
    )
    if not preview.get("ok"):
        raise SystemExit(70)
    memory_vault._append_memory_deletion_tombstone = append_then_crash
    memory_vault.delete_memory_vault_user_note(
        note_id,
        preview["confirmToken"],
        reason="privacy_request",
        root=root,
    )
    raise SystemExit(71)
    """
)

RECOVERY_WORKER = textwrap.dedent(
    """
    import json
    import sqlite3
    import sys
    from pathlib import Path

    from evelyn_core.assistant_contracts import MemoryRecallRequest
    from evelyn_core import memory_vault

    root = Path(sys.argv[1])
    note_id = sys.argv[2]
    title = sys.argv[3]
    body = sys.argv[4]
    source_path = Path(sys.argv[5])
    index_path = memory_vault.memory_index_db_path(root)
    hot_path = root / "memory_index" / "hot_context.json"
    prompt_path = (
        root
        / "memory_index"
        / "prompt_blocks"
        / "core_prompt.txt"
    )
    state_path = root / "memory_index" / "user_note_state.json"

    def scalar(conn, query, params=()):
        return int(conn.execute(query, params).fetchone()[0])

    source_exists_before = source_path.exists()
    hot_file_exists_before = hot_path.exists()
    prompt_file_exists_before = prompt_path.exists()
    hot_context_before = memory_vault.read_memory_hot_context(root=root)
    detail_before = memory_vault.memory_vault_user_note(
        note_id,
        root=root,
    )
    try:
        user_state_before = json.loads(
            state_path.read_text(encoding="utf-8")
        )
    except (FileNotFoundError, json.JSONDecodeError):
        user_state_before = {}
    state_notes_before = user_state_before.get(
        "notes",
        user_state_before,
    )
    if not isinstance(state_notes_before, dict):
        state_notes_before = {}
    with sqlite3.connect(index_path) as conn:
        note_rows_before = scalar(
            conn,
            "SELECT COUNT(*) FROM notes WHERE note_id = ?",
            (note_id,),
        )
        vector_rows_before = scalar(
            conn,
            "SELECT COUNT(*) FROM note_vectors WHERE note_id = ?",
            (note_id,),
        )
        graph_rows_before = scalar(
            conn,
            "SELECT COUNT(*) FROM graph_links WHERE src_note_id = ?",
            (note_id,),
        )
        cache_rows_before = scalar(
            conn,
            "SELECT COUNT(*) FROM retrieval_cache",
        )
        try:
            fts_rows_before = scalar(
                conn,
                "SELECT COUNT(*) FROM notes_fts WHERE note_id = ?",
                (note_id,),
            )
        except sqlite3.OperationalError:
            fts_rows_before = 0

    snapshot = memory_vault.memory_vault_user_snapshot(root=root)
    with sqlite3.connect(index_path) as conn:
        note_rows_after = scalar(
            conn,
            "SELECT COUNT(*) FROM notes WHERE note_id = ?",
            (note_id,),
        )
        vector_rows_after = scalar(
            conn,
            "SELECT COUNT(*) FROM note_vectors WHERE note_id = ?",
            (note_id,),
        )
        graph_rows_after = scalar(
            conn,
            "SELECT COUNT(*) FROM graph_links WHERE src_note_id = ?",
            (note_id,),
        )
        cache_rows_after = scalar(
            conn,
            "SELECT COUNT(*) FROM retrieval_cache",
        )
        try:
            fts_rows_after = scalar(
                conn,
                "SELECT COUNT(*) FROM notes_fts WHERE note_id = ?",
                (note_id,),
            )
        except sqlite3.OperationalError:
            fts_rows_after = 0

    recall = memory_vault.recall_memory_vault(
        MemoryRecallRequest(
            turn_id="restart-recall",
            session_key="restart-session",
            guild_id=None,
            user_text=title,
            topic_id=None,
            source="test",
            max_items=3,
        ),
        root=root,
    )
    try:
        memory_vault.write_memory_vault_note(
            note_type="project",
            title=title,
            body="attempted resurrection",
            source="control-page-user",
            root=root,
        )
    except memory_vault.MemoryNoteDeletedError:
        recreation_blocked = True
    else:
        recreation_blocked = False

    try:
        user_state = json.loads(state_path.read_text(encoding="utf-8"))
    except (FileNotFoundError, json.JSONDecodeError):
        user_state = {}
    state_notes = user_state.get("notes", user_state)
    if not isinstance(state_notes, dict):
        state_notes = {}

    result = {
        "sourceExistsBefore": source_exists_before,
        "hotFileExistsBefore": hot_file_exists_before,
        "promptFileExistsBefore": prompt_file_exists_before,
        "hotContextBefore": hot_context_before,
        "detailBefore": detail_before,
        "userStatePresentBefore": note_id in state_notes_before,
        "noteRowsBefore": note_rows_before,
        "vectorRowsBefore": vector_rows_before,
        "graphRowsBefore": graph_rows_before,
        "ftsRowsBefore": fts_rows_before,
        "cacheRowsBefore": cache_rows_before,
        "snapshotContainsDeleted": any(
            card.get("id") == note_id
            for card in snapshot.get("cards", [])
        ),
        "sourceExistsAfter": source_path.exists(),
        "hotFileExistsAfter": hot_path.exists(),
        "promptFileExistsAfter": prompt_path.exists(),
        "noteRowsAfter": note_rows_after,
        "vectorRowsAfter": vector_rows_after,
        "graphRowsAfter": graph_rows_after,
        "ftsRowsAfter": fts_rows_after,
        "cacheRowsAfter": cache_rows_after,
        "recallOk": recall.ok,
        "recallContext": recall.context_text,
        "recreationBlocked": recreation_blocked,
        "userStateRemoved": note_id not in state_notes,
    }
    print(json.dumps(result, ensure_ascii=False))
    """
)


class MemoryDeletionRestartTests(unittest.TestCase):
    def subprocess_environment(self) -> dict[str, str]:
        environment = os.environ.copy()
        existing = environment.get("PYTHONPATH", "")
        environment["PYTHONPATH"] = os.pathsep.join(
            item
            for item in (str(RUNTIME_ROOT), existing)
            if item
        )
        return environment

    def test_tombstone_survives_process_crash_and_recovers_fail_closed(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            title = "Process Crash Deletion Canary"
            body = "private process crash deletion body"
            source_path = write_memory_vault_note(
                note_type="project",
                title=title,
                body=body,
                links=["crash-evidence-neighbor"],
                source="control-page-user",
                root=root,
            )
            note = parse_memory_note(source_path)
            update_memory_vault_user_note(
                note.note_id,
                "confirm",
                root=root,
            )
            refresh_memory_hot_context(root=root)
            before = recall_memory_vault(
                MemoryRecallRequest(
                    turn_id="before-process-crash",
                    session_key="restart-session",
                    guild_id=None,
                    user_text=title,
                    topic_id=None,
                    source="test",
                    max_items=3,
                ),
                root=root,
            )
            self.assertIn(body, before.context_text)

            crashed = subprocess.run(
                [
                    sys.executable,
                    "-c",
                    CRASH_WORKER,
                    str(root),
                    note.note_id,
                ],
                cwd=REPO_ROOT,
                env=self.subprocess_environment(),
                text=True,
                capture_output=True,
                timeout=20,
                check=False,
            )
            tombstone_path = (
                root / "memory_index" / "memory_deletions.jsonl"
            )
            tombstone_raw = tombstone_path.read_text(encoding="utf-8")

            self.assertEqual(
                crashed.returncode,
                CRASH_EXIT_CODE,
                crashed.stderr + crashed.stdout,
            )
            self.assertTrue(source_path.exists())
            self.assertIn(note.note_id, tombstone_raw)
            self.assertNotIn(title, tombstone_raw)
            self.assertNotIn(body, tombstone_raw)
            self.assertNotIn("contentHash", tombstone_raw)
            self.assertNotIn('"path"', tombstone_raw)

            recovered = subprocess.run(
                [
                    sys.executable,
                    "-c",
                    RECOVERY_WORKER,
                    str(root),
                    note.note_id,
                    title,
                    body,
                    str(source_path),
                ],
                cwd=REPO_ROOT,
                env=self.subprocess_environment(),
                text=True,
                capture_output=True,
                timeout=20,
                check=False,
            )
            self.assertEqual(
                recovered.returncode,
                0,
                recovered.stderr + recovered.stdout,
            )
            result = json.loads(recovered.stdout)

        self.assertTrue(result["sourceExistsBefore"])
        self.assertTrue(result["hotFileExistsBefore"])
        self.assertTrue(result["promptFileExistsBefore"])
        self.assertEqual(result["hotContextBefore"], "")
        self.assertFalse(result["detailBefore"]["ok"])
        self.assertTrue(result["userStatePresentBefore"])
        self.assertEqual(result["noteRowsBefore"], 1)
        self.assertEqual(result["vectorRowsBefore"], 1)
        self.assertEqual(result["graphRowsBefore"], 1)
        self.assertEqual(result["ftsRowsBefore"], 1)
        self.assertGreaterEqual(result["cacheRowsBefore"], 1)
        self.assertFalse(result["snapshotContainsDeleted"])
        self.assertFalse(result["sourceExistsAfter"])
        self.assertFalse(result["hotFileExistsAfter"])
        self.assertFalse(result["promptFileExistsAfter"])
        self.assertEqual(result["noteRowsAfter"], 0)
        self.assertEqual(result["vectorRowsAfter"], 0)
        self.assertEqual(result["graphRowsAfter"], 0)
        self.assertEqual(result["ftsRowsAfter"], 0)
        self.assertEqual(result["cacheRowsAfter"], 0)
        self.assertTrue(result["recallOk"])
        self.assertNotIn(title, result["recallContext"])
        self.assertNotIn(body, result["recallContext"])
        self.assertTrue(result["recreationBlocked"])
        self.assertTrue(result["userStateRemoved"])


if __name__ == "__main__":
    unittest.main()
