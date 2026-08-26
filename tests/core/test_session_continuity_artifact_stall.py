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

from evelyn_core.continuity_commit_contract import (  # noqa: E402
    require_durable_continuity_receipt,
)
from evelyn_core.conversation_ingress_recovery import (  # noqa: E402
    ConversationIngressRecoveryJournal,
)
from evelyn_core.conversation_memory_receipt import (  # noqa: E402
    not_used_memory_receipt_ref,
)
from evelyn_core.durable_artifact_process import (  # noqa: E402
    DurableArtifactProcess,
)
from evelyn_core.session_continuity import (  # noqa: E402
    SessionContinuityCheckpoint,
)
from evelyn_core.session_memory_state import (  # noqa: E402
    SessionStateStore,
)


FAULT_WORKER = (
    REPO_ROOT / "tests" / "fixtures" / "durable_artifact_fault_worker.py"
)
PYTHON_EXECUTABLE = str(
    getattr(sys, "_base_executable", "") or sys.executable
)


class _Clock:
    def __init__(self) -> None:
        self.wall = 1_000.0
        self.monotonic = 100.0

    def wall_time(self) -> float:
        return self.wall

    def monotonic_time(self) -> float:
        return self.monotonic


class SessionContinuityArtifactStallTests(unittest.TestCase):
    def _call_bounded(
        self,
        callback: Callable[[], Any],
        *,
        process: DurableArtifactProcess,
        timeout_sec: float = 5.0,
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
        if not done.wait(timeout_sec):
            child = process._process
            if child is not None and child.poll() is None:
                child.kill()
            done.wait(2.0)
            self.fail("completed-turn artifact stall was not bounded")
        thread.join(timeout=0.1)
        return values, errors, time.monotonic() - started_at

    def test_ingress_write_stall_releases_checkpoint_for_next_commit(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            journal_path = root / "ingress.json"
            fault_state_path = root / "fault-state.json"
            clock = _Clock()
            session_key = "guild:1:text:2:user:3"
            first_turn = "turn-1"
            answer = "첫 답변"
            memory_ref = not_used_memory_receipt_ref()

            store = SessionStateStore.create_empty()
            store.start_new_turn(
                session_key,
                turn_id=first_turn,
                now_monotonic=clock.monotonic,
            )
            store.append_history(
                session_key,
                "첫 요청",
                answer,
                system_prompt="system",
                max_history_items=12,
            )
            store.update_session_state(
                session_key,
                user_id=3,
                speaker="assistant",
                awaiting_user_reply=False,
                topic_id="topic-1",
                active_conversation_awaiting_reply_sec=300.0,
                now_monotonic=clock.monotonic,
            )
            journal = ConversationIngressRecoveryJournal(
                path=journal_path,
                wall_time=clock.wall_time,
                turn_id_factory=lambda: first_turn,
            )
            ingress = journal.claim(
                surface="discord_text",
                scope=session_key,
                source_delivery_id="message-1",
                accepted_text="첫 요청",
                turn_id=first_turn,
            )
            entry_id = str(ingress["entryId"])
            journal.mark_response_ready(
                entry_id,
                assistant_text=answer,
                memory_receipt_ref=memory_ref,
            )
            journal.mark_delivery_inflight(entry_id)
            journal.mark_delivery_succeeded(entry_id)
            journal_before = journal_path.read_bytes()

            process = DurableArtifactProcess(
                deadline_sec=0.8,
                start_timeout_sec=3.0,
                command=(
                    PYTHON_EXECUTABLE,
                    "-u",
                    str(FAULT_WORKER),
                    "--scenario",
                    "stall_before_replace_once",
                    "--state",
                    str(fault_state_path),
                    "--target",
                    str(journal_path),
                ),
            )
            self.addCleanup(process.close)
            manager = SessionContinuityCheckpoint(
                store=store,
                checkpoint_path=root / "checkpoint.json",
                status_path=root / "status.json",
                system_prompt="system",
                wall_time=clock.wall_time,
                monotonic=clock.monotonic_time,
                artifact_process=process,
                commit_artifact_deadline_sec=0.8,
            )
            abandoned: list[tuple[int, int | None]] = []
            original_abandon = process._abandon

            def record_abandon(child: Any) -> None:
                original_abandon(child)
                abandoned.append((int(child.pid), child.poll()))

            def begin_terminal_commit(generation: int) -> None:
                journal.begin_terminal_commit(
                    entry_id,
                    continuity_generation=generation,
                    assistant_text=answer,
                    memory_receipt_ref=memory_ref,
                )

            with patch.object(
                process,
                "_abandon",
                side_effect=record_abandon,
            ):
                first_values, first_errors, first_elapsed = self._call_bounded(
                    lambda: manager.commit_completed_turn(
                        session_key,
                        first_turn,
                        before_commit=begin_terminal_commit,
                    ),
                    process=process,
                )

                self.assertEqual(first_values, [])
                self.assertEqual(len(first_errors), 1)
                self.assertIsInstance(first_errors[0], RuntimeError)
                self.assertEqual(
                    str(first_errors[0]),
                    "conversation_continuity_commit_failed",
                )
                self.assertLess(first_elapsed, 5.0)
                fault_state = json.loads(
                    fault_state_path.read_text(encoding="utf-8")
                )
                old_pid = int(fault_state["pid"])
                self.assertEqual(fault_state["faultCount"], 1)
                self.assertEqual(fault_state["phase"], "before_replace")
                self.assertEqual(Path(fault_state["target"]), journal_path)
                self.assertEqual(len(abandoned), 1)
                self.assertEqual(abandoned[0][0], old_pid)
                self.assertIsNotNone(abandoned[0][1])

                clock.monotonic += 1.0
                store.start_new_turn(
                    session_key,
                    turn_id="turn-2",
                    now_monotonic=clock.monotonic,
                )
                store.append_history(
                    session_key,
                    "두 번째 요청",
                    "두 번째 답변",
                    system_prompt="system",
                    max_history_items=12,
                )
                store.update_session_state(
                    session_key,
                    user_id=3,
                    speaker="assistant",
                    awaiting_user_reply=False,
                    topic_id="topic-2",
                    active_conversation_awaiting_reply_sec=300.0,
                    now_monotonic=clock.monotonic,
                )
                second_values, second_errors, _ = self._call_bounded(
                    lambda: manager.commit_completed_turn(
                        session_key,
                        "turn-2",
                    ),
                    process=process,
                )

            self.assertEqual(second_errors, [])
            self.assertEqual(len(second_values), 1)
            require_durable_continuity_receipt(second_values[0])
            self.assertIsNotNone(process.pid)
            self.assertNotEqual(process.pid, old_pid)
            self.assertFalse(Path(fault_state["tempPath"]).exists())
            self.assertEqual(
                list(root.glob(f".{journal_path.name}.{old_pid}.*.tmp")),
                [],
            )
            time.sleep(0.1)
            self.assertEqual(journal_path.read_bytes(), journal_before)
            persisted_journal = json.loads(
                journal_path.read_text(encoding="utf-8")
            )
            persisted_entry = next(
                row
                for row in persisted_journal["entries"]
                if row["entryId"] == entry_id
            )
            self.assertEqual(persisted_entry["phase"], "delivery_succeeded")
            self.assertEqual(persisted_entry["continuityGeneration"], 0)
            checkpoint = json.loads(
                manager.checkpoint_path.read_text(encoding="utf-8")
            )
            self.assertEqual(
                checkpoint["sessions"][0]["state"]["turnId"],
                "turn-2",
            )
            process.close()


if __name__ == "__main__":
    unittest.main()
