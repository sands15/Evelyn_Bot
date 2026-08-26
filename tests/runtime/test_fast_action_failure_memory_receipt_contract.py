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
try:
    import numpy as _numpy  # noqa: F401
except ImportError:
    sys.modules.setdefault("numpy", SimpleNamespace(ndarray=object))

from evelyn_core import fast_control_api as fast_api  # noqa: E402
from evelyn_core import memory_deletion_journal as journal  # noqa: E402
from evelyn_core import memory_exposure  # noqa: E402
from evelyn_core.conversation_memory_receipt import (  # noqa: E402
    not_used_memory_receipt_ref,
)
from evelyn_core.memory_exposure import (  # noqa: E402
    MEMORY_INDEX_DB_NAME,
    MemoryExposurePosition,
    capture_memory_exposure_position,
    reset_memory_exposure_position,
)
from evelyn_core.memory_integrity_authenticity import (  # noqa: E402
    MEMORY_INTEGRITY_ANCHOR_DIR_ENV,
    MEMORY_INTEGRITY_BOOTSTRAP_ENV,
    MEMORY_INTEGRITY_KEY_FILE_ENV,
)


NOTE_ID = "concept-0123456789abcdef"
CUSTOM_FAILURE_CANARY = "planner-history-derived-failure-canary"


class FastActionFailureMemoryReceiptContractTests(unittest.TestCase):
    """Red tests for the terminal background-action projection contract.

    A non-empty terminal reply is an outbound memory sink regardless of
    whether its task finished as ``completed`` or ``failed``.  Unknown
    attribution therefore fails closed.  Only the fixed, generic failure
    message may be labelled explicitly as ``not_used``.
    """

    def setUp(self) -> None:
        fast_api.CHAT_MESSAGES.clear()
        fast_api.ACTION_COORDINATOR.clear()
        fast_api.BACKGROUND_ACTION_TASKS.clear()
        reset_memory_exposure_position()

    def tearDown(self) -> None:
        fast_api.CHAT_MESSAGES.clear()
        fast_api.ACTION_COORDINATOR.clear()
        fast_api.BACKGROUND_ACTION_TASKS.clear()
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
    def write_memory_version(index_dir: Path, version: int) -> None:
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
    def replace_memory_version(index_dir: Path, version: int) -> None:
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
    def bound_position(index_dir: Path) -> MemoryExposurePosition:
        return MemoryExposurePosition(
            deletion_position=(
                journal.memory_deletion_journal_position(
                    index_dir
                )
            ),
            memory_version=1,
            supplied_note_ids=(NOTE_ID,),
        )

    @staticmethod
    async def wait_for_background_cleanup() -> None:
        # ``launch_background_action`` removes the completed task through a
        # done callback scheduled by the event loop.
        await asyncio.sleep(0)

    def test_custom_failed_reply_keeps_bound_receipt_and_becomes_stale(self) -> None:
        async def runner(_user_text: str, _source: str) -> str:
            raise fast_api.FastActionExecutionError(
                "executor_specific_failure",
                reply=CUSTOM_FAILURE_CANARY,
            )

        async def scenario(index_dir: Path) -> None:
            capture_memory_exposure_position(
                self.bound_position(index_dir)
            )
            task = fast_api.ACTION_COORDINATOR.start(
                kind="research_compare",
                source="control_page",
                user_text="memory-backed request",
                start_reply="started",
            )
            await fast_api.launch_background_action(task, runner)
            await self.wait_for_background_cleanup()

        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            index_dir = root / "memory_index"
            self.write_memory_version(index_dir, 1)
            with self.unconfigured_authenticity(), patch.object(
                fast_api,
                "MEMORY_ROOT",
                root,
            ), patch.object(
                memory_exposure,
                "MEMORY_ROOT",
                root,
            ), patch.object(
                fast_api,
                "commit_fast_control_action_followup",
            ), patch.object(
                fast_api,
                "queue_local_bridge_speech",
            ):
                asyncio.run(scenario(index_dir))

                internal = (
                    fast_api.ACTION_COORDINATOR
                    .internal_snapshot()
                )
                failed_task = internal["tasks"][0]
                failed_event = next(
                    event
                    for event in internal["events"]
                    if event["type"] == "failed"
                )
                chat_reply = fast_api.CHAT_MESSAGES[-1]

                for row in (failed_task, failed_event):
                    self.assertEqual(
                        row["_memoryReceiptRef"]["state"],
                        "bound",
                    )
                    self.assertEqual(
                        row["_memoryReceiptRef"][
                            "suppliedNoteIds"
                        ],
                        [NOTE_ID],
                    )
                self.assertEqual(
                    chat_reply["memoryReceiptRef"]["state"],
                    "bound",
                )

                current = fast_api._public_fast_action_snapshot(
                    memory_index_dir=index_dir
                )
                self.replace_memory_version(index_dir, 2)
                stale = fast_api._public_fast_action_snapshot(
                    memory_index_dir=index_dir
                )

        self.assertIn(
            CUSTOM_FAILURE_CANARY,
            json.dumps(current, ensure_ascii=False),
        )
        stale_encoded = json.dumps(stale, ensure_ascii=False)
        self.assertNotIn(CUSTOM_FAILURE_CANARY, stale_encoded)
        self.assertTrue(stale["tasks"][0]["replyRedacted"])
        failed_public_event = next(
            event
            for event in stale["events"]
            if event["type"] == "failed"
        )
        self.assertTrue(failed_public_event["replyRedacted"])

    def test_generic_failure_is_explicitly_not_used_on_all_terminal_rows(self) -> None:
        async def runner(_user_text: str, _source: str) -> str:
            raise RuntimeError("private executor detail")

        async def scenario() -> None:
            task = fast_api.ACTION_COORDINATOR.start(
                kind="runtime_investigation",
                source="control_page",
                user_text="deterministic failure request",
                start_reply="started",
            )
            await fast_api.launch_background_action(task, runner)
            await self.wait_for_background_cleanup()

        with patch.object(
            fast_api,
            "commit_fast_control_action_followup",
        ), patch.object(
            fast_api,
            "queue_local_bridge_speech",
        ):
            asyncio.run(scenario())

        internal = fast_api.ACTION_COORDINATOR.internal_snapshot()
        terminal_rows = [
            internal["tasks"][0],
            next(
                event
                for event in internal["events"]
                if event["type"] == "failed"
            ),
            fast_api.CHAT_MESSAGES[-1],
        ]
        receipt_keys = (
            "_memoryReceiptRef",
            "_memoryReceiptRef",
            "memoryReceiptRef",
        )
        for row, receipt_key in zip(terminal_rows, receipt_keys):
            self.assertEqual(
                row[receipt_key],
                not_used_memory_receipt_ref(),
            )

        with tempfile.TemporaryDirectory() as temporary:
            public = fast_api._public_fast_action_snapshot(
                memory_index_dir=Path(temporary) / "memory_index"
            )
        generic_reply = fast_api.public_failure_message(
            "background_action_failed"
        )
        self.assertEqual(public["tasks"][0]["finalReply"], generic_reply)
        failed_event = next(
            event
            for event in public["events"]
            if event["type"] == "failed"
        )
        self.assertEqual(failed_event["reply"], generic_reply)

    def test_unknown_failed_reply_is_redacted_for_task_and_event(self) -> None:
        task = fast_api.ACTION_COORDINATOR.start(
            kind="runtime_investigation",
            source="control_page",
            user_text="unknown attribution request",
            start_reply="started",
        )
        fast_api.ACTION_COORDINATOR.fail(
            task.task_id,
            "executor_specific_failure",
            reply=CUSTOM_FAILURE_CANARY,
        )

        with tempfile.TemporaryDirectory() as temporary:
            public = fast_api._public_fast_action_snapshot(
                memory_index_dir=Path(temporary) / "memory_index"
            )

        encoded = json.dumps(public, ensure_ascii=False)
        self.assertNotIn(CUSTOM_FAILURE_CANARY, encoded)
        self.assertTrue(public["tasks"][0]["replyRedacted"])
        failed_event = next(
            event
            for event in public["events"]
            if event["type"] == "failed"
        )
        self.assertTrue(failed_event["replyRedacted"])

    def test_completed_independent_reply_requires_explicit_not_used(self) -> None:
        unknown = fast_api.ACTION_COORDINATOR.start(
            kind="runtime_investigation",
            source="control_page",
            user_text="unknown success",
            start_reply="started",
        )
        fast_api.ACTION_COORDINATOR.complete(
            unknown.task_id,
            "unknown-success-canary",
        )
        independent = fast_api.ACTION_COORDINATOR.start(
            kind="runtime_investigation",
            source="control_page",
            user_text="independent success",
            start_reply="started",
        )
        fast_api.ACTION_COORDINATOR.complete(
            independent.task_id,
            "independent-success-result",
            memory_receipt=not_used_memory_receipt_ref(),
        )

        with tempfile.TemporaryDirectory() as temporary:
            public = fast_api._public_fast_action_snapshot(
                memory_index_dir=Path(temporary) / "memory_index"
            )

        encoded = json.dumps(public, ensure_ascii=False)
        self.assertNotIn("unknown-success-canary", encoded)
        self.assertIn("independent-success-result", encoded)


if __name__ == "__main__":
    unittest.main()
