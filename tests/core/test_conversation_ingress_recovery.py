from __future__ import annotations

import json
import math
import sys
import tempfile
import unittest
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

from evelyn_core import conversation_ingress_recovery as ingress_module  # noqa: E402
from evelyn_core.conversation_ingress_recovery import (  # noqa: E402
    CONVERSATION_INGRESS_RECOVERY_HEAD_SCHEMA,
    CONVERSATION_INGRESS_RECOVERY_RECEIPT_SCHEMA,
    CONVERSATION_INGRESS_RECOVERY_SCHEMA,
    ConversationIngressBindingMismatch,
    ConversationIngressRecoveryError,
    ConversationIngressRecoveryJournal,
)
from evelyn_core.conversation_memory_receipt import (  # noqa: E402
    not_used_memory_receipt_ref,
)


NOTE_ID = "concept-0123456789abcdef"
BOUND_RECEIPT_REF = {
    "schema": "conversation.memory-receipt-ref.v1",
    "state": "bound",
    "memoryVersion": 7,
    "suppliedNoteIds": [NOTE_ID],
    "suppliedNoteCount": 1,
    "contentFree": True,
}


class Clock:
    def __init__(self, value: float = 100.0) -> None:
        self.value = value

    def __call__(self) -> float:
        return self.value


class TurnIds:
    def __init__(self) -> None:
        self.value = 0

    def __call__(self) -> str:
        self.value += 1
        return f"turn-{self.value}"


class ConversationIngressRecoveryTests(unittest.TestCase):
    def make_journal(
        self,
        root: Path,
        *,
        clock: Clock | None = None,
        turn_ids: TurnIds | None = None,
        max_age_sec: float = 60.0,
        max_entries: int = 4,
    ) -> ConversationIngressRecoveryJournal:
        return ConversationIngressRecoveryJournal(
            path=root / "ingress.json",
            wall_time=clock or Clock(),
            turn_id_factory=turn_ids or TurnIds(),
            max_age_sec=max_age_sec,
            max_entries=max_entries,
        )

    @staticmethod
    def claim(
        journal: ConversationIngressRecoveryJournal,
        *,
        delivery_id: str = "message-1",
        text: str = "이블린, 듣고 있어?",
    ) -> dict:
        return journal.claim(
            surface="discord_text",
            scope="guild:1:text:2:user:3",
            source_delivery_id=delivery_id,
            accepted_text=text,
        )

    def complete(
        self,
        journal: ConversationIngressRecoveryJournal,
        receipt: dict,
        *,
        answer: str = "응, 듣고 있어.",
        memory_receipt_ref: dict | None = None,
        generation: int = 3,
    ) -> dict:
        memory_ref = (
            not_used_memory_receipt_ref()
            if memory_receipt_ref is None
            else memory_receipt_ref
        )
        journal.mark_response_ready(
            receipt["entryId"],
            assistant_text=answer,
            memory_receipt_ref=memory_ref,
        )
        journal.mark_delivery_inflight(receipt["entryId"])
        journal.mark_delivery_succeeded(receipt["entryId"])
        journal.begin_terminal_commit(
            receipt["entryId"],
            assistant_text=answer,
            memory_receipt_ref=memory_ref,
            continuity_generation=generation,
        )
        return journal.complete(
            receipt["entryId"],
            assistant_text=answer,
            memory_receipt_ref=memory_ref,
            continuity_generation=generation,
        )

    def test_fresh_owner_bootstraps_verified_empty_chain(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            journal = self.make_journal(root)
            status = journal.public_status()
            payload = json.loads(
                (root / "ingress.json").read_text(encoding="utf-8")
            )
            head = json.loads(
                (root / "ingress.head.json").read_text(
                    encoding="utf-8"
                )
            )

        self.assertEqual(status["state"], "ready")
        self.assertEqual(status["generation"], 1)
        self.assertEqual(status["entryCount"], 0)
        self.assertEqual(status["integrity"], "verified")
        self.assertEqual(status["headState"], "current")
        self.assertTrue(status["rollbackProtected"])
        self.assertEqual(payload["generation"], 1)
        self.assertEqual(
            payload["previousHash"],
            ingress_module.CONVERSATION_INGRESS_RECOVERY_CHAIN_GENESIS,
        )
        self.assertEqual(payload["entries"], [])
        self.assertEqual(head["generation"], 1)
        self.assertEqual(head["journalHash"], payload["journalHash"])

    def test_first_claim_advances_bootstrap_generation(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            journal = self.make_journal(root)
            bootstrap = json.loads(
                (root / "ingress.json").read_text(encoding="utf-8")
            )
            claimed = self.claim(journal)
            payload = json.loads(
                (root / "ingress.json").read_text(encoding="utf-8")
            )
            head = json.loads(
                (root / "ingress.head.json").read_text(
                    encoding="utf-8"
                )
            )

        self.assertTrue(claimed["shouldProcess"])
        self.assertEqual(claimed["journalGeneration"], 2)
        self.assertEqual(payload["generation"], 2)
        self.assertEqual(
            payload["previousHash"], bootstrap["journalHash"]
        )
        self.assertEqual(head["generation"], 2)
        self.assertEqual(head["journalHash"], payload["journalHash"])

    def test_bootstrap_head_failure_is_closed_until_restart_repairs_it(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            real_write = ingress_module.atomic_json_write

            def fail_head(
                path: Path,
                payload: dict,
                **kwargs: object,
            ) -> None:
                if Path(path).name == "ingress.head.json":
                    raise OSError("simulated bootstrap head failure")
                real_write(path, payload, **kwargs)

            with patch.object(
                ingress_module,
                "atomic_json_write",
                side_effect=fail_head,
            ):
                failed = self.make_journal(root)
                failed_status = failed.public_status()
                with self.assertRaises(
                    ConversationIngressRecoveryError
                ) as caught:
                    self.claim(failed)

            restored = self.make_journal(root)
            restored_status = restored.public_status()

        self.assertEqual(failed_status["state"], "error")
        self.assertEqual(failed_status["integrity"], "failed")
        self.assertEqual(failed_status["headState"], "write_failed")
        self.assertFalse(failed_status["rollbackProtected"])
        self.assertEqual(
            caught.exception.code,
            "conversation_ingress_recovery_unavailable",
        )
        self.assertEqual(restored_status["state"], "ready")
        self.assertEqual(restored_status["generation"], 1)
        self.assertTrue(restored_status["rollbackProtected"])

    def test_first_claim_is_durable_and_same_binding_never_reprocesses(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            journal = self.make_journal(root)

            first = self.claim(journal, text="이블린,   듣고 있어?")
            duplicate = self.claim(
                journal,
                text="이블린, 듣고 있어?",
            )
            payload = json.loads(
                (root / "ingress.json").read_text(encoding="utf-8")
            )
            head = json.loads(
                (root / "ingress.head.json").read_text(
                    encoding="utf-8"
                )
            )

        self.assertEqual(
            first["schema"],
            CONVERSATION_INGRESS_RECOVERY_RECEIPT_SCHEMA,
        )
        self.assertTrue(first["durable"])
        self.assertTrue(first["shouldProcess"])
        self.assertEqual(first["disposition"], "claimed")
        self.assertFalse(first["automaticReplay"])
        self.assertFalse(duplicate["shouldProcess"])
        self.assertEqual(duplicate["disposition"], "pending")
        self.assertEqual(first["entryId"], duplicate["entryId"])
        self.assertEqual(
            first["journalGeneration"],
            duplicate["journalGeneration"],
        )
        self.assertEqual(
            set(payload),
            {
                "schema",
                "generation",
                "previousHash",
                "journalHash",
                "updatedAt",
                "entries",
                "policy",
            },
        )
        self.assertEqual(
            payload["schema"],
            CONVERSATION_INGRESS_RECOVERY_SCHEMA,
        )
        self.assertFalse(payload["policy"]["rawAudio"])
        self.assertFalse(payload["policy"]["partialTranscript"])
        self.assertFalse(payload["policy"]["automaticReplay"])
        self.assertEqual(
            head["schema"],
            CONVERSATION_INGRESS_RECOVERY_HEAD_SCHEMA,
        )
        self.assertEqual(head["journalHash"], payload["journalHash"])

    def test_same_source_key_with_different_text_fails_closed(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            journal = self.make_journal(Path(temp_dir))
            first = self.claim(journal)

            with self.assertRaises(ConversationIngressBindingMismatch) as caught:
                self.claim(journal, text="완전히 다른 입력")

            existing = journal.receipt_for(first["entryId"])

        self.assertEqual(
            caught.exception.code,
            "conversation_ingress_binding_mismatch",
        )
        self.assertIsNotNone(existing)
        self.assertEqual(existing["textHash"], first["textHash"])
        self.assertFalse(existing["shouldProcess"])

    def test_terminal_receipt_is_durable_idempotent_and_replayable(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            journal = self.make_journal(root)
            claimed = self.claim(journal)
            terminal = self.complete(journal, claimed)
            duplicate_terminal = self.complete(journal, claimed)

            restored = self.make_journal(root)
            duplicate_claim = self.claim(restored)
            replay = restored.replay_record_for(claimed["entryId"])

        self.assertEqual(terminal["phase"], "completed")
        self.assertEqual(terminal["disposition"], "completed")
        self.assertTrue(terminal["durable"])
        self.assertTrue(terminal["replayable"])
        self.assertEqual(
            terminal["assistantBindingHash"],
            duplicate_terminal["assistantBindingHash"],
        )
        self.assertFalse(duplicate_claim["shouldProcess"])
        self.assertEqual(duplicate_claim["disposition"], "completed")
        self.assertEqual(replay["assistantText"], "응, 듣고 있어.")
        self.assertEqual(
            replay["memoryReceiptRef"]["state"],
            "not_used",
        )

    def test_missing_or_malformed_memory_receipt_is_unattributed_and_not_replayable(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            journal = self.make_journal(Path(temp_dir))
            claimed = self.claim(journal)
            answer = "기억에서 찾았다고 주장하는 답"
            ready = journal.mark_response_ready(
                claimed["entryId"],
                assistant_text=answer,
                memory_receipt_ref={"state": "bound"},
            )
            journal.mark_delivery_inflight(claimed["entryId"])
            journal.mark_delivery_succeeded(claimed["entryId"])
            journal.begin_terminal_commit(
                claimed["entryId"],
                assistant_text=answer,
                memory_receipt_ref=None,
                continuity_generation=2,
            )
            terminal = journal.complete(
                claimed["entryId"],
                assistant_text=answer,
                memory_receipt_ref=None,
                continuity_generation=2,
            )
            record = journal.record_for(claimed["entryId"])

            with self.assertRaises(ConversationIngressRecoveryError) as caught:
                journal.replay_record_for(claimed["entryId"])

        self.assertEqual(ready["memoryReceiptRef"]["state"], "unattributed")
        self.assertFalse(ready["replayable"])
        self.assertFalse(terminal["replayable"])
        self.assertFalse(record["replayable"])
        self.assertEqual(
            caught.exception.code,
            "conversation_ingress_replay_unattributed",
        )

    def test_ambiguous_or_undelivered_reply_cannot_become_replayable(
        self,
    ) -> None:
        memory_ref = not_used_memory_receipt_ref()
        answer = "전달 여부를 확인할 수 없는 답"
        with tempfile.TemporaryDirectory() as temp_dir:
            journal = self.make_journal(Path(temp_dir))
            claimed = self.claim(journal)
            journal.mark_response_ready(
                claimed["entryId"],
                assistant_text=answer,
                memory_receipt_ref=memory_ref,
            )

            with self.assertRaises(ConversationIngressRecoveryError) as ready:
                journal.begin_terminal_commit(
                    claimed["entryId"],
                    assistant_text=answer,
                    memory_receipt_ref=memory_ref,
                    continuity_generation=4,
                )

            journal.mark_delivery_inflight(claimed["entryId"])
            journal.mark_delivery_ambiguous(
                claimed["entryId"],
                error_code="conversation_ingress_delivery_timeout",
            )

            with self.assertRaises(
                ConversationIngressRecoveryError
            ) as ambiguous:
                journal.begin_terminal_commit(
                    claimed["entryId"],
                    assistant_text=answer,
                    memory_receipt_ref=memory_ref,
                    continuity_generation=4,
                )
            record = journal.record_for(claimed["entryId"])

            with self.assertRaises(ConversationIngressRecoveryError) as replay:
                journal.replay_record_for(claimed["entryId"])

        self.assertEqual(
            ready.exception.code,
            "conversation_ingress_transition_invalid",
        )
        self.assertEqual(
            ambiguous.exception.code,
            "conversation_ingress_transition_invalid",
        )
        self.assertEqual(record["phase"], "delivery_ambiguous")
        self.assertFalse(record["replayable"])
        self.assertEqual(
            replay.exception.code,
            "conversation_ingress_replay_not_terminal",
        )

    def test_bound_reply_preserves_exact_note_projection_for_deletion_guard(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            journal = self.make_journal(Path(temp_dir))
            claimed = self.claim(journal)
            terminal = self.complete(
                journal,
                claimed,
                memory_receipt_ref=BOUND_RECEIPT_REF,
            )
            record = journal.replay_record_for(claimed["entryId"])

            with self.assertRaises(ConversationIngressBindingMismatch):
                journal.complete(
                    claimed["entryId"],
                    assistant_text="응, 듣고 있어.",
                    memory_receipt_ref=not_used_memory_receipt_ref(),
                    continuity_generation=3,
                )

        self.assertTrue(terminal["replayable"])
        self.assertEqual(
            record["memoryReceiptRef"]["suppliedNoteIds"],
            [NOTE_ID],
        )
        self.assertEqual(record["memoryReceiptRef"]["state"], "bound")
        self.assertEqual(
            record["assistantBindingHash"],
            terminal["assistantBindingHash"],
        )

    def test_restart_marks_pending_and_inflight_as_non_replayable_work(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            first = self.make_journal(root)
            pending = self.claim(
                first,
                delivery_id="pending",
            )
            inflight = self.claim(
                first,
                delivery_id="inflight",
            )
            first.mark_response_ready(
                inflight["entryId"],
                assistant_text="전달 중이던 답",
                memory_receipt_ref=not_used_memory_receipt_ref(),
            )
            first.mark_delivery_inflight(inflight["entryId"])

            restored = self.make_journal(root)
            pending_receipt = restored.receipt_for(pending["entryId"])
            inflight_receipt = restored.receipt_for(inflight["entryId"])
            recovery = {
                row["sourceDeliveryId"]: row
                for row in restored.recovery_records()
            }

        self.assertEqual(pending_receipt["phase"], "accepted")
        self.assertTrue(pending_receipt["recovered"])
        self.assertFalse(pending_receipt["shouldProcess"])
        self.assertEqual(
            inflight_receipt["phase"],
            "delivery_ambiguous",
        )
        self.assertTrue(inflight_receipt["deliveryAmbiguous"])
        self.assertFalse(inflight_receipt["automaticReplay"])
        self.assertEqual(
            recovery["inflight"]["lastErrorCode"],
            "conversation_ingress_delivery_ambiguous_after_restart",
        )
        self.assertFalse(recovery["pending"]["automaticReplay"])

    def test_stream_can_mark_first_delta_before_final_response_binding(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            journal = self.make_journal(root)
            claimed = self.claim(
                journal,
                delivery_id="stream-first-delta",
            )
            inflight = journal.mark_stream_delivery_inflight(
                claimed["entryId"],
                delivery_ref="http-stream:7",
            )
            before_binding = journal.record_for(claimed["entryId"])

            with self.assertRaises(ConversationIngressRecoveryError) as caught:
                journal.mark_delivery_succeeded(claimed["entryId"])

            restored = self.make_journal(root)
            ambiguous = restored.record_for(claimed["entryId"])
            bound = restored.bind_response(
                claimed["entryId"],
                assistant_text="스트림으로 완성된 답",
                memory_receipt_ref=not_used_memory_receipt_ref(),
            )
            delivered = restored.mark_delivery_succeeded(
                claimed["entryId"],
                delivery_ref="http-stream:7",
            )

        self.assertEqual(inflight["phase"], "delivery_inflight")
        self.assertEqual(before_binding["assistantText"], "")
        self.assertFalse(before_binding["replayable"])
        self.assertEqual(
            caught.exception.code,
            "conversation_ingress_response_not_bound",
        )
        self.assertEqual(ambiguous["phase"], "delivery_ambiguous")
        self.assertEqual(ambiguous["assistantText"], "")
        self.assertFalse(ambiguous["automaticReplay"])
        self.assertEqual(bound["phase"], "delivery_ambiguous")
        self.assertEqual(delivered["phase"], "delivery_succeeded")
        self.assertFalse(delivered["shouldProcess"])

    def test_count_evicts_only_completed_and_ttl_allows_new_claim(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            clock = Clock(10.0)
            turn_ids = TurnIds()
            journal = self.make_journal(
                Path(temp_dir),
                clock=clock,
                turn_ids=turn_ids,
                max_age_sec=5.0,
                max_entries=2,
            )
            first = self.claim(journal, delivery_id="one")
            second = self.claim(journal, delivery_id="two")
            with self.assertRaises(ConversationIngressRecoveryError) as caught:
                self.claim(journal, delivery_id="three")
            self.assertEqual(
                caught.exception.code,
                "conversation_ingress_capacity_exhausted",
            )

            self.complete(journal, first, generation=1)
            third = self.claim(journal, delivery_id="three")
            self.assertIsNone(journal.receipt_for(first["entryId"]))
            self.assertIsNotNone(journal.receipt_for(second["entryId"]))
            self.assertTrue(third["shouldProcess"])

            clock.value = 16.0
            retried_second = self.claim(journal, delivery_id="two")

        self.assertTrue(retried_second["shouldProcess"])
        self.assertNotEqual(retried_second["turnId"], second["turnId"])

    def test_exact_schema_and_hash_tampering_fail_closed(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            journal = self.make_journal(root)
            self.claim(journal)
            path = root / "ingress.json"
            head_path = root / "ingress.head.json"
            payload = json.loads(path.read_text(encoding="utf-8"))
            payload["unknown"] = True
            payload["journalHash"] = ingress_module._journal_hash(payload)
            path.write_text(
                json.dumps(payload, ensure_ascii=False),
                encoding="utf-8",
            )
            head_path.unlink()

            restored = self.make_journal(root)
            status = restored.public_status()
            with self.assertRaises(ConversationIngressRecoveryError) as caught:
                self.claim(restored)

        self.assertEqual(status["state"], "corrupt")
        self.assertEqual(status["integrity"], "failed")
        self.assertEqual(
            caught.exception.code,
            "conversation_ingress_recovery_unavailable",
        )

    def test_all_persistent_writes_request_atomic_fsync(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            calls: list[tuple[Path, bool]] = []
            real_write = ingress_module.atomic_json_write

            def observed_write(path: Path, payload: dict, **kwargs: object) -> None:
                calls.append((Path(path), kwargs.get("durable") is True))
                real_write(path, payload, **kwargs)

            with patch.object(
                ingress_module,
                "atomic_json_write",
                side_effect=observed_write,
            ):
                journal = self.make_journal(Path(temp_dir))
                self.claim(journal)

        self.assertEqual(len(calls), 4)
        self.assertTrue(all(durable for _path, durable in calls))
        self.assertTrue(calls[0][0].name.endswith("ingress.json"))
        self.assertTrue(calls[1][0].name.endswith("ingress.head.json"))
        self.assertTrue(calls[2][0].name.endswith("ingress.json"))
        self.assertTrue(calls[3][0].name.endswith("ingress.head.json"))

    def test_configuration_has_finite_hard_bounds_and_closed_error_codes(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            with self.assertRaises(ConversationIngressRecoveryError):
                self.make_journal(
                    Path(temp_dir),
                    max_age_sec=math.nan,
                )
            bounded = self.make_journal(
                Path(temp_dir) / "bounded",
                max_age_sec=999_999.0,
            )
            self.assertEqual(
                bounded.public_status()["policy"]["maxAgeSec"],
                30 * 60.0,
            )
            claimed = self.claim(bounded)
            bounded.mark_response_ready(
                claimed["entryId"],
                assistant_text="답변",
                memory_receipt_ref=not_used_memory_receipt_ref(),
            )
            bounded.mark_delivery_inflight(claimed["entryId"])
            with self.assertRaises(ConversationIngressRecoveryError) as caught:
                bounded.mark_delivery_ambiguous(
                    claimed["entryId"],
                    error_code="arbitrary_exception_text",
                )

        self.assertEqual(
            caught.exception.code,
            "conversation_ingress_error_code_invalid",
        )


if __name__ == "__main__":
    unittest.main()
