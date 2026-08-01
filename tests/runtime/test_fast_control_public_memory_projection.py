from __future__ import annotations

import asyncio
from contextlib import contextmanager
import json
import os
from pathlib import Path
import sqlite3
import sys
import tempfile
import unittest
from types import SimpleNamespace
from unittest.mock import patch


REPO_ROOT = next(
    path
    for path in Path(__file__).resolve().parents
    if (path / "main.py").exists()
)
RUNTIME_ROOT = REPO_ROOT / "evelyn_core" / "runtime"
if str(RUNTIME_ROOT) not in sys.path:
    sys.path.insert(0, str(RUNTIME_ROOT))
sys.modules.setdefault("numpy", SimpleNamespace(ndarray=object))

from evelyn_core import fast_control_api as fast_api  # noqa: E402
from evelyn_core import memory_deletion_journal as journal  # noqa: E402
from evelyn_core.conversation_memory_receipt import (  # noqa: E402
    CONVERSATION_MEMORY_RECEIPT_REF_SCHEMA,
    not_used_memory_receipt_ref,
    unattributed_memory_receipt_ref,
)
from evelyn_core.memory_exposure import MEMORY_INDEX_DB_NAME  # noqa: E402
from evelyn_core.memory_exposure import (  # noqa: E402
    reset_memory_exposure_position,
)
from evelyn_core.memory_integrity_authenticity import (  # noqa: E402
    MEMORY_INTEGRITY_ANCHOR_DIR_ENV,
    MEMORY_INTEGRITY_BOOTSTRAP_ENV,
    MEMORY_INTEGRITY_KEY_FILE_ENV,
)


NOTE_ID = "concept-0123456789abcdef"
CANARY = "deleted-memory-public-canary"


class FastControlPublicMemoryProjectionTests(unittest.TestCase):
    def setUp(self) -> None:
        fast_api.CHAT_MESSAGES.clear()
        fast_api.ACTION_COORDINATOR.clear()
        reset_memory_exposure_position()

    def tearDown(self) -> None:
        fast_api.CHAT_MESSAGES.clear()
        fast_api.ACTION_COORDINATOR.clear()
        reset_memory_exposure_position()

    @contextmanager
    def unconfigured_authenticity(self):
        with patch.dict(
            os.environ,
            {
                MEMORY_INTEGRITY_KEY_FILE_ENV: "",
                MEMORY_INTEGRITY_ANCHOR_DIR_ENV: "",
                MEMORY_INTEGRITY_BOOTSTRAP_ENV: "",
            },
        ):
            yield

    @staticmethod
    def write_memory_version(
        index_dir: Path,
        version: int,
    ) -> None:
        index_dir.mkdir(parents=True, exist_ok=True)
        connection = sqlite3.connect(
            str(index_dir / MEMORY_INDEX_DB_NAME)
        )
        try:
            connection.execute(
                "CREATE TABLE metadata "
                "(key TEXT PRIMARY KEY, value NOT NULL)"
            )
            connection.execute(
                "INSERT INTO metadata(key, value) VALUES(?, ?)",
                ("memory_version", str(version)),
            )
            connection.commit()
        finally:
            connection.close()

    @staticmethod
    def replace_memory_version(
        index_dir: Path,
        version: int,
    ) -> None:
        connection = sqlite3.connect(
            str(index_dir / MEMORY_INDEX_DB_NAME)
        )
        try:
            connection.execute(
                "UPDATE metadata SET value = ? WHERE key = ?",
                (str(version), "memory_version"),
            )
            connection.commit()
        finally:
            connection.close()

    @staticmethod
    def bound_receipt(version: int = 1) -> dict[str, object]:
        return {
            "schema": CONVERSATION_MEMORY_RECEIPT_REF_SCHEMA,
            "state": "bound",
            "memoryVersion": version,
            "suppliedNoteIds": [NOTE_ID],
            "suppliedNoteCount": 1,
            "contentFree": True,
        }

    @staticmethod
    def append_tombstone(index_dir: Path) -> None:
        journal.append_memory_deletion_tombstone(
            index_dir,
            {
                "schema": journal.MEMORY_DELETE_TOMBSTONE_V1_SCHEMA,
                "noteId": NOTE_ID,
                "noteType": "concept",
                "sourceType": "conversation",
                "reason": "privacy_request",
                "deletedAt": "2026-08-01T00:00:00Z",
            },
        )

    def test_chat_projection_drops_stale_bound_reply_and_hides_receipt(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            index_dir = Path(temporary) / "memory_index"
            self.write_memory_version(index_dir, 1)
            fast_api.append_chat_message(
                "user",
                "Tester",
                "question",
            )
            fast_api.append_chat_message(
                "assistant",
                "Evelyn",
                CANARY,
                memory_receipt=self.bound_receipt(),
            )

            with self.unconfigured_authenticity():
                current = fast_api.default_chat_messages(
                    memory_index_dir=index_dir
                )
                self.assertIn(CANARY, json.dumps(current))
                self.assertNotIn(NOTE_ID, json.dumps(current))
                self.assertNotIn("memoryReceipt", json.dumps(current))

                self.replace_memory_version(index_dir, 2)
                stale = fast_api.default_chat_messages(
                    memory_index_dir=index_dir
                )

            encoded = json.dumps(stale)
            self.assertNotIn(CANARY, encoded)
            self.assertNotIn(NOTE_ID, encoded)
            self.assertEqual(
                [row["text"] for row in stale],
                ["question"],
            )

    def test_action_state_and_endpoint_redact_tombstoned_reply(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            index_dir = root / "memory_index"
            self.write_memory_version(index_dir, 1)
            task = fast_api.ACTION_COORDINATOR.start(
                kind="research_compare",
                source="control_page",
                user_text="compare",
                start_reply="started",
            )
            fast_api.ACTION_COORDINATOR.complete(
                task.task_id,
                CANARY,
                memory_receipt=self.bound_receipt(),
            )

            with self.unconfigured_authenticity():
                current = fast_api._public_fast_action_snapshot(
                    memory_index_dir=index_dir
                )
                self.assertIn(CANARY, json.dumps(current))
                self.assertNotIn(NOTE_ID, json.dumps(current))

                self.replace_memory_version(index_dir, 2)
                stale = fast_api._public_fast_action_snapshot(
                    memory_index_dir=index_dir
                )
                self.replace_memory_version(index_dir, 1)
                self.append_tombstone(index_dir)
                redacted = fast_api._public_fast_action_snapshot(
                    memory_index_dir=index_dir
                )
                state = fast_api.build_control_state(
                    {"services": []},
                    memory_index_dir=index_dir,
                )
                with patch.object(fast_api, "MEMORY_ROOT", root):
                    response = asyncio.run(
                        fast_api.action_events_handler(
                            SimpleNamespace(query={"after": "0"})
                        )
                    )

            stale_encoded = json.dumps(
                stale,
                ensure_ascii=False,
            )
            self.assertNotIn(CANARY, stale_encoded)
            self.assertNotIn(NOTE_ID, stale_encoded)
            encoded = json.dumps(redacted, ensure_ascii=False)
            self.assertNotIn(CANARY, encoded)
            self.assertNotIn(NOTE_ID, encoded)
            state_encoded = json.dumps(state, ensure_ascii=False)
            self.assertNotIn(CANARY, state_encoded)
            self.assertNotIn(NOTE_ID, state_encoded)
            completed_task = redacted["tasks"][0]
            completed_event = next(
                event
                for event in redacted["events"]
                if event["type"] == "completed"
            )
            self.assertTrue(completed_task["replyRedacted"])
            self.assertTrue(completed_event["replyRedacted"])

            endpoint_payload = json.loads(response.text)
            endpoint_encoded = json.dumps(
                endpoint_payload,
                ensure_ascii=False,
            )
            self.assertNotIn(CANARY, endpoint_encoded)
            self.assertNotIn(NOTE_ID, endpoint_encoded)
            self.assertTrue(
                endpoint_payload["tasks"][0]["replyRedacted"]
            )

    def test_missing_and_unattributed_action_receipts_redact_but_not_used_survives(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            index_dir = Path(temporary) / "memory_index"
            receipts = (
                None,
                unattributed_memory_receipt_ref(),
                not_used_memory_receipt_ref(),
            )
            replies = (
                "missing-receipt-canary",
                "unattributed-receipt-canary",
                "memory-independent-result",
            )
            for index, (receipt, reply) in enumerate(
                zip(receipts, replies),
                start=1,
            ):
                task = fast_api.ACTION_COORDINATOR.start(
                    kind="runtime_investigation",
                    source="control_page",
                    user_text=f"request-{index}",
                    start_reply="started",
                )
                kwargs = (
                    {}
                    if receipt is None
                    else {"memory_receipt": receipt}
                )
                fast_api.ACTION_COORDINATOR.complete(
                    task.task_id,
                    reply,
                    **kwargs,
                )

            public = fast_api._public_fast_action_snapshot(
                memory_index_dir=index_dir
            )

        encoded = json.dumps(public, ensure_ascii=False)
        self.assertNotIn("missing-receipt-canary", encoded)
        self.assertNotIn("unattributed-receipt-canary", encoded)
        self.assertIn("memory-independent-result", encoded)
        self.assertNotIn("memoryReceipt", encoded)
        self.assertNotIn(NOTE_ID, encoded)

        direct_public = fast_api.ACTION_COORDINATOR.snapshot()
        self.assertNotIn(
            "memoryReceipt",
            json.dumps(direct_public),
        )
        internal = fast_api.ACTION_COORDINATOR.internal_snapshot()
        independent_refs = [
            task["_memoryReceiptRef"]
            for task in internal["tasks"]
            if task.get("_memoryReceiptRef", {}).get("state")
            == "not_used"
        ]
        self.assertEqual(len(independent_refs), 1)
        self.assertEqual(
            independent_refs[0]["suppliedNoteIds"],
            [],
        )


if __name__ == "__main__":
    unittest.main()
