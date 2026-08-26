from __future__ import annotations

import json
import sys
import tempfile
import threading
import time
import unittest
from pathlib import Path
from typing import Any, Callable
from unittest.mock import patch


REPO_ROOT = next(
    path
    for path in Path(__file__).resolve().parents
    if (path / "main.py").exists()
)
RUNTIME_ROOT = REPO_ROOT / "evelyn_core" / "runtime"
if str(RUNTIME_ROOT) not in sys.path:
    sys.path.insert(0, str(RUNTIME_ROOT))

from evelyn_core.conversation_ingress_recovery import (  # noqa: E402
    ConversationIngressRecoveryError,
    ConversationIngressRecoveryJournal,
)
from evelyn_core.continuity_authenticity import (  # noqa: E402
    CONTINUITY_AUTH_ANCHOR_SLOT_FAST_ACTION_HEAD,
    ContinuityAuthenticity,
    ContinuityAuthenticityError,
)
from evelyn_core.conversation_memory_receipt import (  # noqa: E402
    not_used_memory_receipt_ref,
)
from evelyn_core.durable_artifact_process import (  # noqa: E402
    DurableArtifactProcess,
    DurableArtifactProcessTimeout,
)
from evelyn_core.fast_action_recovery import (  # noqa: E402
    FastActionRecoveryJournal,
)


FAULT_WORKER = (
    REPO_ROOT / "tests" / "fixtures" / "durable_artifact_fault_worker.py"
)
PYTHON_EXECUTABLE = str(
    getattr(sys, "_base_executable", "") or sys.executable
)


class TerminalJournalArtifactStallTests(unittest.TestCase):
    def _fault_process(
        self,
        *,
        state_path: Path,
        target: Path,
        scenario: str = "stall_before_replace_once",
    ) -> DurableArtifactProcess:
        process = DurableArtifactProcess(
            deadline_sec=0.8,
            start_timeout_sec=3.0,
            command=(
                PYTHON_EXECUTABLE,
                "-u",
                str(FAULT_WORKER),
                "--scenario",
                scenario,
                "--state",
                str(state_path),
                "--target",
                str(target),
            ),
        )
        self.addCleanup(process.close)
        return process

    def _call_bounded(
        self,
        callback: Callable[[], Any],
        *,
        process: DurableArtifactProcess,
    ) -> tuple[list[Any], list[Exception], float]:
        values: list[Any] = []
        errors: list[Exception] = []
        done = threading.Event()

        def invoke() -> None:
            try:
                values.append(callback())
            except Exception as exc:
                errors.append(exc)
            finally:
                done.set()

        started_at = time.monotonic()
        thread = threading.Thread(target=invoke, daemon=True)
        thread.start()
        if not done.wait(5.0):
            child = process._process
            if child is not None and child.poll() is None:
                child.kill()
            done.wait(2.0)
            self.fail("terminal journal artifact stall was not bounded")
        thread.join(timeout=0.1)
        return values, errors, time.monotonic() - started_at

    @staticmethod
    def _record_abandon(
        process: DurableArtifactProcess,
        abandoned: list[tuple[int, int | None]],
    ) -> Callable[[Any], None]:
        original = process._abandon

        def record(child: Any) -> None:
            original(child)
            abandoned.append((int(child.pid), child.poll()))

        return record

    def test_ingress_complete_stall_is_reaped_and_retry_completes(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            journal_path = root / "ingress.json"
            state_path = root / "fault-state.json"
            answer = "완료 답변"
            memory_ref = not_used_memory_receipt_ref()
            journal = ConversationIngressRecoveryJournal(path=journal_path)
            claimed = journal.claim(
                surface="discord_text",
                scope="guild:1:text:2:user:3",
                source_delivery_id="message-1",
                accepted_text="완료해줘",
            )
            entry_id = str(claimed["entryId"])
            journal.mark_response_ready(
                entry_id,
                assistant_text=answer,
                memory_receipt_ref=memory_ref,
            )
            journal.mark_delivery_inflight(entry_id)
            journal.mark_delivery_succeeded(entry_id)
            journal.begin_terminal_commit(
                entry_id,
                continuity_generation=7,
                assistant_text=answer,
                memory_receipt_ref=memory_ref,
            )
            before_entry = dict(journal._entries[entry_id])
            before_journal = journal_path.read_bytes()
            before_head = journal.head_path.read_bytes()
            process = self._fault_process(
                state_path=state_path,
                target=journal_path,
            )
            journal.artifact_process = process
            journal.artifact_deadline_sec = 0.8
            abandoned: list[tuple[int, int | None]] = []

            with patch.object(
                process,
                "_abandon",
                side_effect=self._record_abandon(process, abandoned),
            ):
                values, errors, elapsed = self._call_bounded(
                    lambda: journal.complete(
                        entry_id,
                        continuity_generation=7,
                        assistant_text=answer,
                        memory_receipt_ref=memory_ref,
                    ),
                    process=process,
                )

                self.assertEqual(values, [])
                self.assertEqual(len(errors), 1)
                self.assertIsInstance(errors[0], DurableArtifactProcessTimeout)
                self.assertLess(elapsed, 5.0)
                fault = json.loads(state_path.read_text(encoding="utf-8"))
                old_pid = int(fault["pid"])
                self.assertEqual(len(abandoned), 1)
                self.assertEqual(abandoned[0][0], old_pid)
                self.assertIsNotNone(abandoned[0][1])
                self.assertFalse(Path(fault["tempPath"]).exists())
                self.assertEqual(
                    list(root.glob(f".{journal_path.name}.{old_pid}.*.tmp")),
                    [],
                )
                self.assertEqual(journal_path.read_bytes(), before_journal)
                self.assertEqual(journal.head_path.read_bytes(), before_head)
                self.assertEqual(journal._entries[entry_id], before_entry)
                self.assertEqual(
                    journal.public_status()["phases"]["terminal_committing"],
                    1,
                )

                original_read = process.read_text
                read_stalled = False

                def stall_replacement_read(
                    path: Path,
                    **kwargs: Any,
                ) -> str | None:
                    nonlocal read_stalled
                    if Path(path) == journal_path and not read_stalled:
                        read_stalled = True
                        raise DurableArtifactProcessTimeout()
                    return original_read(path, **kwargs)

                with patch.object(
                    process,
                    "read_text",
                    side_effect=stall_replacement_read,
                ):
                    stalled_values, stalled_errors, _ = self._call_bounded(
                        lambda: journal.complete(
                            entry_id,
                            continuity_generation=7,
                            assistant_text=answer,
                            memory_receipt_ref=memory_ref,
                        ),
                        process=process,
                    )

                self.assertTrue(read_stalled)
                self.assertEqual(stalled_values, [])
                self.assertEqual(len(stalled_errors), 1)
                self.assertIsInstance(
                    stalled_errors[0],
                    ConversationIngressRecoveryError,
                )
                self.assertEqual(journal.public_status()["state"], "error")
                self.assertEqual(journal._entries[entry_id], before_entry)

                retry_values, retry_errors, _ = self._call_bounded(
                    lambda: journal.complete(
                        entry_id,
                        continuity_generation=7,
                        assistant_text=answer,
                        memory_receipt_ref=memory_ref,
                    ),
                    process=process,
                )

            self.assertEqual(retry_errors, [])
            self.assertEqual(retry_values[0]["phase"], "completed")
            self.assertNotEqual(process.pid, old_pid)
            time.sleep(0.1)
            persisted = json.loads(journal_path.read_text(encoding="utf-8"))
            entry = next(
                row for row in persisted["entries"] if row["entryId"] == entry_id
            )
            self.assertEqual(entry["phase"], "completed")
            self.assertEqual(entry["continuityGeneration"], 7)

    def _assert_fast_action_stall_then_retry(self, method_name: str) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            journal_path = root / "fast-action.json"
            state_path = root / "fault-state.json"
            action_id = "fast-action-1"
            journal = FastActionRecoveryJournal(
                path=journal_path,
                enabled=True,
            )
            journal.begin(action_id, continuity_generation=1)
            journal.prepare_terminal(action_id, expected_generation=2)
            before_action = dict(journal._actions[action_id])
            before_journal = journal_path.read_bytes()
            before_head = journal.head_path.read_bytes()
            process = self._fault_process(
                state_path=state_path,
                target=journal_path,
            )
            journal.artifact_process = process
            journal.artifact_deadline_sec = 0.8
            abandoned: list[tuple[int, int | None]] = []
            mutation = getattr(journal, method_name)

            with patch.object(
                process,
                "_abandon",
                side_effect=self._record_abandon(process, abandoned),
            ):
                values, errors, elapsed = self._call_bounded(
                    lambda: mutation(action_id),
                    process=process,
                )

                self.assertEqual(values, [])
                self.assertEqual(len(errors), 1)
                self.assertIsInstance(errors[0], DurableArtifactProcessTimeout)
                self.assertLess(elapsed, 5.0)
                fault = json.loads(state_path.read_text(encoding="utf-8"))
                old_pid = int(fault["pid"])
                self.assertEqual(len(abandoned), 1)
                self.assertEqual(abandoned[0][0], old_pid)
                self.assertIsNotNone(abandoned[0][1])
                self.assertFalse(Path(fault["tempPath"]).exists())
                self.assertEqual(
                    list(root.glob(f".{journal_path.name}.{old_pid}.*.tmp")),
                    [],
                )
                self.assertEqual(journal_path.read_bytes(), before_journal)
                self.assertEqual(journal.head_path.read_bytes(), before_head)
                self.assertEqual(journal._actions[action_id], before_action)
                self.assertEqual(journal.public_status()["pendingCount"], 1)

                retry_values, retry_errors, _ = self._call_bounded(
                    lambda: mutation(action_id),
                    process=process,
                )

            self.assertEqual(retry_errors, [])
            self.assertEqual(len(retry_values), 1)
            self.assertNotEqual(process.pid, old_pid)
            persisted = json.loads(journal_path.read_text(encoding="utf-8"))
            if method_name == "finish":
                self.assertEqual(retry_values[0]["pendingCount"], 0)
                self.assertEqual(persisted["actions"], [])
            else:
                self.assertEqual(retry_values[0]["pendingCount"], 1)
                self.assertEqual(persisted["actions"][0]["state"], "running")
                self.assertEqual(
                    persisted["actions"][0]["expectedGeneration"],
                    0,
                )

    def test_fast_action_finish_stall_is_reaped_and_retry_finishes(self) -> None:
        self._assert_fast_action_stall_then_retry("finish")

    def test_fast_action_interrupted_stall_is_reaped_and_retry_marks_running(
        self,
    ) -> None:
        self._assert_fast_action_stall_then_retry("mark_interrupted")

    def test_fast_action_post_replace_stall_reconciles_without_retry(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            journal_path = root / "fast-action.json"
            state_path = root / "fault-state.json"
            action_id = "fast-action-1"
            journal = FastActionRecoveryJournal(
                path=journal_path,
                enabled=True,
            )
            journal.begin(action_id, continuity_generation=1)
            journal.prepare_terminal(action_id, expected_generation=2)
            process = self._fault_process(
                state_path=state_path,
                target=journal_path,
                scenario="stall_after_replace_once",
            )
            journal.artifact_process = process
            journal.artifact_deadline_sec = 0.8
            abandoned: list[tuple[int, int | None]] = []

            with patch.object(
                process,
                "_abandon",
                side_effect=self._record_abandon(process, abandoned),
            ):
                values, errors, elapsed = self._call_bounded(
                    lambda: journal.finish(action_id),
                    process=process,
                )

            self.assertEqual(errors, [])
            self.assertEqual(values[0]["pendingCount"], 0)
            self.assertLess(elapsed, 5.0)
            fault = json.loads(state_path.read_text(encoding="utf-8"))
            old_pid = int(fault["pid"])
            self.assertEqual(fault["phase"], "after_replace")
            self.assertEqual(len(abandoned), 1)
            self.assertEqual(abandoned[0][0], old_pid)
            self.assertIsNotNone(abandoned[0][1])
            self.assertNotEqual(process.pid, old_pid)
            payload = json.loads(journal_path.read_text(encoding="utf-8"))
            head = json.loads(journal.head_path.read_text(encoding="utf-8"))
            self.assertEqual(payload["actions"], [])
            self.assertEqual(head["generation"], payload["generation"])
            self.assertEqual(head["journalHash"], payload["journalHash"])

    def test_fast_action_head_lag_is_reloaded_before_finish_retry(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            journal_path = root / "fast-action.json"
            state_path = root / "fault-state.json"
            action_id = "fast-action-1"
            journal = FastActionRecoveryJournal(
                path=journal_path,
                enabled=True,
            )
            journal.begin(action_id, continuity_generation=1)
            journal.prepare_terminal(action_id, expected_generation=2)
            before_action = dict(journal._actions[action_id])
            before_head = json.loads(
                journal.head_path.read_text(encoding="utf-8")
            )
            process = self._fault_process(
                state_path=state_path,
                target=journal.head_path,
            )
            journal.artifact_process = process
            journal.artifact_deadline_sec = 0.8

            values, errors, _ = self._call_bounded(
                lambda: journal.finish(action_id),
                process=process,
            )

            self.assertEqual(values, [])
            self.assertEqual(len(errors), 1)
            self.assertIsInstance(errors[0], DurableArtifactProcessTimeout)
            self.assertEqual(journal._actions[action_id], before_action)
            advanced = json.loads(journal_path.read_text(encoding="utf-8"))
            lagging_head = json.loads(
                journal.head_path.read_text(encoding="utf-8")
            )
            self.assertEqual(advanced["actions"], [])
            self.assertEqual(
                advanced["generation"],
                before_head["generation"] + 1,
            )
            self.assertEqual(lagging_head, before_head)

            retry_values, retry_errors, _ = self._call_bounded(
                lambda: journal.finish(action_id),
                process=process,
            )

            self.assertEqual(retry_errors, [])
            self.assertEqual(retry_values[0]["pendingCount"], 0)
            repaired_head = json.loads(
                journal.head_path.read_text(encoding="utf-8")
            )
            self.assertEqual(
                repaired_head["generation"],
                advanced["generation"],
            )
            self.assertEqual(
                repaired_head["journalHash"],
                advanced["journalHash"],
            )

    def test_fast_action_anchor_lag_is_reloaded_before_finish_retry(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            anchor_root = root / "anchors"
            anchor_root.mkdir()
            authenticity = ContinuityAuthenticity(
                key=b"fast-action-artifact-stall-key-32-bytes",
                allow_unsigned_bootstrap=True,
                anchor_root=anchor_root,
            )
            journal_path = root / "fast-action.json"
            state_path = root / "fault-state.json"
            action_id = "fast-action-1"
            journal = FastActionRecoveryJournal(
                path=journal_path,
                enabled=True,
                authenticity=authenticity,
            )
            journal.begin(action_id, continuity_generation=1)
            journal.prepare_terminal(action_id, expected_generation=2)
            before_action = dict(journal._actions[action_id])
            before_anchor = authenticity.external_anchor_position(
                CONTINUITY_AUTH_ANCHOR_SLOT_FAST_ACTION_HEAD
            )
            anchor_path = anchor_root / "fast-control-action-recovery.json"
            process = self._fault_process(
                state_path=state_path,
                target=anchor_path,
            )
            journal.artifact_process = process
            journal.artifact_deadline_sec = 0.8

            values, errors, _ = self._call_bounded(
                lambda: journal.finish(action_id),
                process=process,
            )

            self.assertEqual(values, [])
            self.assertEqual(len(errors), 1)
            self.assertIsInstance(errors[0], ContinuityAuthenticityError)
            self.assertEqual(
                errors[0].code,
                "continuity_anchor_unavailable",
            )
            self.assertEqual(journal._actions[action_id], before_action)
            advanced = json.loads(journal_path.read_text(encoding="utf-8"))
            advanced_head = json.loads(
                journal.head_path.read_text(encoding="utf-8")
            )
            self.assertEqual(advanced["actions"], [])
            self.assertEqual(
                advanced_head["journalHash"],
                advanced["journalHash"],
            )
            self.assertEqual(
                authenticity.external_anchor_position(
                    CONTINUITY_AUTH_ANCHOR_SLOT_FAST_ACTION_HEAD
                ),
                before_anchor,
            )

            retry_values, retry_errors, _ = self._call_bounded(
                lambda: journal.finish(action_id),
                process=process,
            )

            self.assertEqual(retry_errors, [])
            self.assertEqual(retry_values[0]["pendingCount"], 0)
            self.assertEqual(
                authenticity.external_anchor_position(
                    CONTINUITY_AUTH_ANCHOR_SLOT_FAST_ACTION_HEAD
                ),
                (advanced["generation"], advanced["journalHash"]),
            )


if __name__ == "__main__":
    unittest.main()
