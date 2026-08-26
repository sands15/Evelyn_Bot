from __future__ import annotations

import json
import os
import sqlite3
import sys
import tempfile
import types
import unittest
from contextlib import contextmanager
from pathlib import Path
from unittest.mock import patch


REPO_ROOT = next(
    path
    for path in Path(__file__).resolve().parents
    if (path / "main.py").exists()
)
RUNTIME_ROOT = REPO_ROOT / "evelyn_core" / "runtime"
if str(RUNTIME_ROOT) not in sys.path:
    sys.path.insert(0, str(RUNTIME_ROOT))

try:
    import numpy as _numpy  # noqa: F401
except ImportError:
    class _DummyNdArray:
        pass

    sys.modules["numpy"] = types.SimpleNamespace(
        ndarray=_DummyNdArray,
    )

from evelyn_core import memory_deletion_journal as journal  # noqa: E402
from evelyn_core import memory_exposure  # noqa: E402
from evelyn_core.conversation_memory_exposure import (  # noqa: E402
    filter_conversation_history_for_memory_exposure,
)
from evelyn_core.conversation_memory_receipt import (  # noqa: E402
    memory_receipt_ref_from_receipt,
    not_used_memory_receipt_ref,
    unattributed_memory_receipt_ref,
)
from evelyn_core.memory_integrity_authenticity import (  # noqa: E402
    MEMORY_INTEGRITY_ANCHOR_DIR_ENV,
    MEMORY_INTEGRITY_BOOTSTRAP_ENV,
    MEMORY_INTEGRITY_KEY_FILE_ENV,
)
from evelyn_core.skills.routing.voice_llm import (  # noqa: E402
    build_main_llm_payload,
)


CURRENT_NOTE = "concept-0123456789abcdef"
STALE_NOTE = "concept-1111111111111111"
TOMBSTONED_NOTE = "concept-fedcba9876543210"


def bound_receipt(note_id: str, *, memory_version: int) -> dict:
    return memory_receipt_ref_from_receipt(
        {
            "schema": "memory.context-receipt.v1",
            "state": "provided",
            "groundingState": "attributed",
            "memoryVersion": memory_version,
            "suppliedNoteIds": [note_id],
            "suppliedNoteCount": 1,
            "contentFree": True,
        }
    )


class ConversationMemoryExposureTests(unittest.TestCase):
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
    def write_memory_version(index_dir: Path, version: int) -> None:
        index_dir.mkdir(parents=True, exist_ok=True)
        connection = sqlite3.connect(
            str(index_dir / memory_exposure.MEMORY_INDEX_DB_NAME)
        )
        try:
            connection.execute(
                """
                CREATE TABLE metadata (
                    key TEXT PRIMARY KEY,
                    value NOT NULL
                )
                """
            )
            connection.execute(
                "INSERT INTO metadata(key, value) VALUES(?, ?)",
                ("memory_version", str(version)),
            )
            connection.commit()
        finally:
            connection.close()

    @staticmethod
    def tombstone(note_id: str) -> dict[str, object]:
        return {
            "schema": journal.MEMORY_DELETE_TOMBSTONE_V1_SCHEMA,
            "noteId": note_id,
            "noteType": "concept",
            "sourceType": "conversation",
            "reason": "privacy_request",
            "deletedAt": "2026-08-01T00:00:00Z",
        }

    def test_filter_preserves_only_memory_safe_history(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            index_dir = Path(temp_dir) / "memory_index"
            self.write_memory_version(index_dir, 7)
            with self.unconfigured_authenticity():
                journal.append_memory_deletion_tombstone(
                    index_dir,
                    self.tombstone(TOMBSTONED_NOTE),
                )
                messages = [
                    {
                        "role": "user",
                        "content": "USER_MUST_SURVIVE",
                        "memoryReceipt": {
                            "private": "USER_RECEIPT_CANARY"
                        },
                        "memoryReceiptRef": {
                            "private": "USER_REF_CANARY"
                        },
                    },
                    {
                        "role": "assistant",
                        "content": "NOT_USED_MUST_SURVIVE",
                        "memoryReceiptRef": (
                            not_used_memory_receipt_ref(
                                memory_version=7
                            )
                        ),
                    },
                    {
                        "role": "assistant",
                        "content": "MISSING_MUST_DROP",
                    },
                    {
                        "role": "assistant",
                        "content": "UNATTRIBUTED_MUST_DROP",
                        "memoryReceiptRef": (
                            unattributed_memory_receipt_ref(
                                memory_version=7
                            )
                        ),
                    },
                    {
                        "role": "assistant",
                        "content": "STALE_MUST_DROP",
                        "memoryReceiptRef": bound_receipt(
                            STALE_NOTE,
                            memory_version=6,
                        ),
                    },
                    {
                        "role": "assistant",
                        "content": "TOMBSTONED_MUST_DROP",
                        "memoryReceiptRef": bound_receipt(
                            TOMBSTONED_NOTE,
                            memory_version=7,
                        ),
                    },
                    {
                        "role": "assistant",
                        "content": "BOUND_CURRENT_MUST_SURVIVE",
                        "memoryReceiptRef": bound_receipt(
                            CURRENT_NOTE,
                            memory_version=7,
                        ),
                    },
                ]

                outcome = (
                    filter_conversation_history_for_memory_exposure(
                        messages,
                        memory_index_dir=index_dir,
                    )
                )

            self.assertEqual(
                [message["content"] for message in outcome.messages],
                [
                    "USER_MUST_SURVIVE",
                    "NOT_USED_MUST_SURVIVE",
                    "BOUND_CURRENT_MUST_SURVIVE",
                ],
            )
            user = outcome.messages[0]
            self.assertNotIn("memoryReceipt", user)
            self.assertNotIn("memoryReceiptRef", user)
            self.assertEqual(
                outcome.messages[1]["memoryReceiptRef"]["state"],
                "not_used",
            )
            self.assertEqual(
                outcome.messages[2]["memoryReceiptRef"]["state"],
                "bound",
            )
            self.assertEqual(outcome.dropped_missing_receipt_count, 1)
            self.assertEqual(outcome.dropped_unattributed_count, 1)
            self.assertEqual(outcome.dropped_stale_version_count, 1)
            self.assertEqual(outcome.dropped_tombstoned_count, 1)
            self.assertEqual(
                outcome.memory_receipt_ref["suppliedNoteIds"],
                [CURRENT_NOTE],
            )
            self.assertIsNotNone(outcome.memory_exposure_position)
            self.assertEqual(
                outcome.memory_exposure_position.memory_version,
                7,
            )
            self.assertEqual(
                outcome.memory_exposure_position.supplied_note_ids,
                (CURRENT_NOTE,),
            )

            payload = build_main_llm_payload(
                model_name="test-model",
                messages=[dict(message) for message in outcome.messages],
                final_user_text="CURRENT_USER_TURN",
                source="voice",
                stream=True,
            )
            serialized_payload = json.dumps(
                payload,
                ensure_ascii=False,
            )
            self.assertNotIn("memoryReceipt", serialized_payload)
            self.assertNotIn("memoryReceiptRef", serialized_payload)
            self.assertNotIn(CURRENT_NOTE, serialized_payload)
            self.assertNotIn(TOMBSTONED_NOTE, serialized_payload)
            self.assertIs(payload["timings_per_token"], True)
            self.assertEqual(
                sum(
                    "memoryReceipt" in message
                    or "memoryReceiptRef" in message
                    for message in payload["messages"]
                ),
                0,
            )

            public_status = json.dumps(
                outcome.public_status(),
                ensure_ascii=False,
            )
            self.assertNotIn(CURRENT_NOTE, public_status)
            self.assertNotIn(TOMBSTONED_NOTE, public_status)


if __name__ == "__main__":
    unittest.main()
