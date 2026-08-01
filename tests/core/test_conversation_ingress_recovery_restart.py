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
CRASH_EXIT_CODE = 74
MID_COMMIT_EXIT_CODE = 75

COMMON = """
from pathlib import Path
import sys

from evelyn_core.conversation_ingress_recovery import (
    ConversationIngressRecoveryJournal,
    conversation_ingress_entry_id,
)
from evelyn_core.conversation_memory_receipt import (
    not_used_memory_receipt_ref,
)

root = Path(sys.argv[1])
path = root / "ingress.json"
claim_args = {
    "surface": "control_page",
    "scope": "owner:main:user:7",
    "source_delivery_id": "request-stable-1",
    "accepted_text": "재시작 경계 질문",
}
entry_id = conversation_ingress_entry_id(
    surface=claim_args["surface"],
    scope=claim_args["scope"],
    source_delivery_id=claim_args["source_delivery_id"],
)
"""

WRITE_ACCEPTED = textwrap.dedent(
    COMMON
    + f"""
import os
journal = ConversationIngressRecoveryJournal(path=path)
journal.claim(**claim_args)
os._exit({CRASH_EXIT_CODE})
"""
)

WRITE_INFLIGHT = textwrap.dedent(
    COMMON
    + f"""
import os
journal = ConversationIngressRecoveryJournal(path=path)
claimed = journal.claim(**claim_args)
journal.mark_response_ready(
    claimed["entryId"],
    assistant_text="전달을 시작한 답변",
    memory_receipt_ref=not_used_memory_receipt_ref(),
)
journal.mark_delivery_inflight(claimed["entryId"])
os._exit({CRASH_EXIT_CODE})
"""
)

WRITE_STREAM_FIRST_DELTA = textwrap.dedent(
    COMMON
    + f"""
import os
journal = ConversationIngressRecoveryJournal(path=path)
claimed = journal.claim(**claim_args)
journal.mark_stream_delivery_inflight(
    claimed["entryId"],
    delivery_ref="ndjson:request-stable-1",
)
os._exit({CRASH_EXIT_CODE})
"""
)

WRITE_COMPLETED = textwrap.dedent(
    COMMON
    + f"""
import os
journal = ConversationIngressRecoveryJournal(path=path)
claimed = journal.claim(**claim_args)
memory_ref = not_used_memory_receipt_ref()
journal.mark_response_ready(
    claimed["entryId"],
    assistant_text="완료된 답변",
    memory_receipt_ref=memory_ref,
)
journal.mark_delivery_inflight(claimed["entryId"])
journal.mark_delivery_succeeded(claimed["entryId"])
journal.begin_terminal_commit(
    claimed["entryId"],
    continuity_generation=9,
    assistant_text="완료된 답변",
    memory_receipt_ref=memory_ref,
)
journal.complete(
    claimed["entryId"],
    continuity_generation=9,
    assistant_text="완료된 답변",
    memory_receipt_ref=memory_ref,
)
os._exit({CRASH_EXIT_CODE})
"""
)

WRITE_JOURNAL_BEFORE_HEAD_CRASH = textwrap.dedent(
    COMMON
    + f"""
import os
from evelyn_core import conversation_ingress_recovery as module

real_write = module.atomic_json_write

def crash_after_journal(path_arg, payload, **kwargs):
    real_write(path_arg, payload, **kwargs)
    if Path(path_arg) == path:
        os._exit({MID_COMMIT_EXIT_CODE})

module.atomic_json_write = crash_after_journal
journal = ConversationIngressRecoveryJournal(path=path)
journal.claim(**claim_args)
raise AssertionError("crash hook did not run")
"""
)

READ_STATE = textwrap.dedent(
    COMMON
    + """
import json
journal = ConversationIngressRecoveryJournal(path=path)
receipt = journal.claim(**claim_args)
record = journal.record_for(entry_id)
replay = None
replay_error = ""
try:
    replay = journal.replay_record_for(entry_id)
except Exception as exc:
    replay_error = str(exc)
print(
    json.dumps(
        {
            "receipt": receipt,
            "record": record,
            "replay": replay,
            "replayError": replay_error,
            "recovery": journal.recovery_records(),
            "status": journal.public_status(),
        },
        ensure_ascii=False,
    )
)
"""
)


class ConversationIngressRecoveryRestartTests(unittest.TestCase):
    def environment(self) -> dict[str, str]:
        environment = os.environ.copy()
        existing = environment.get("PYTHONPATH", "")
        environment["PYTHONPATH"] = os.pathsep.join(
            part
            for part in (str(RUNTIME_ROOT), existing)
            if part
        )
        return environment

    def run_script(
        self,
        source: str,
        root: Path,
        *,
        expected_code: int,
    ) -> subprocess.CompletedProcess[str]:
        result = subprocess.run(
            [sys.executable, "-c", source, str(root)],
            cwd=REPO_ROOT,
            env=self.environment(),
            text=True,
            capture_output=True,
            timeout=30,
            check=False,
        )
        self.assertEqual(
            result.returncode,
            expected_code,
            result.stderr + result.stdout,
        )
        return result

    def recover(self, root: Path) -> dict:
        result = self.run_script(
            READ_STATE,
            root,
            expected_code=0,
        )
        return json.loads(result.stdout)

    def test_fresh_process_does_not_replay_accepted_claim(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            self.run_script(
                WRITE_ACCEPTED,
                root,
                expected_code=CRASH_EXIT_CODE,
            )
            result = self.recover(root)

        self.assertEqual(result["receipt"]["phase"], "accepted")
        self.assertFalse(result["receipt"]["shouldProcess"])
        self.assertTrue(result["receipt"]["recovered"])
        self.assertFalse(result["record"]["automaticReplay"])
        self.assertEqual(len(result["recovery"]), 1)
        self.assertEqual(
            result["replayError"],
            "conversation_ingress_replay_not_terminal",
        )

    def test_fresh_process_converts_inflight_to_delivery_ambiguous(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            self.run_script(
                WRITE_INFLIGHT,
                root,
                expected_code=CRASH_EXIT_CODE,
            )
            result = self.recover(root)

        self.assertEqual(
            result["receipt"]["phase"],
            "delivery_ambiguous",
        )
        self.assertFalse(result["receipt"]["shouldProcess"])
        self.assertTrue(result["receipt"]["deliveryAmbiguous"])
        self.assertEqual(
            result["record"]["lastErrorCode"],
            "conversation_ingress_delivery_ambiguous_after_restart",
        )
        self.assertFalse(result["record"]["automaticReplay"])

    def test_fresh_process_never_replays_assistantless_stream_inflight(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            self.run_script(
                WRITE_STREAM_FIRST_DELTA,
                root,
                expected_code=CRASH_EXIT_CODE,
            )
            result = self.recover(root)

        self.assertEqual(
            result["receipt"]["phase"],
            "delivery_ambiguous",
        )
        self.assertFalse(result["receipt"]["shouldProcess"])
        self.assertFalse(result["receipt"]["replayable"])
        self.assertEqual(result["record"]["assistantText"], "")
        self.assertEqual(result["record"]["assistantBindingHash"], "")
        self.assertEqual(
            result["replayError"],
            "conversation_ingress_replay_not_terminal",
        )
        self.assertFalse(result["record"]["automaticReplay"])

    def test_fresh_process_returns_terminal_receipt_without_reprocessing(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            self.run_script(
                WRITE_COMPLETED,
                root,
                expected_code=CRASH_EXIT_CODE,
            )
            result = self.recover(root)

        self.assertEqual(result["receipt"]["phase"], "completed")
        self.assertEqual(result["receipt"]["disposition"], "completed")
        self.assertFalse(result["receipt"]["shouldProcess"])
        self.assertTrue(result["receipt"]["replayable"])
        self.assertEqual(result["replay"]["assistantText"], "완료된 답변")
        self.assertEqual(result["recovery"], [])

    def test_fresh_process_repairs_bootstrap_head_write_crash(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            self.run_script(
                WRITE_JOURNAL_BEFORE_HEAD_CRASH,
                root,
                expected_code=MID_COMMIT_EXIT_CODE,
            )
            self.assertTrue((root / "ingress.json").is_file())
            self.assertFalse((root / "ingress.head.json").exists())

            result = self.recover(root)

        self.assertEqual(result["status"]["state"], "ready")
        self.assertEqual(result["status"]["integrity"], "verified")
        self.assertEqual(result["status"]["headState"], "current")
        self.assertTrue(result["status"]["rollbackProtected"])
        self.assertEqual(result["status"]["generation"], 2)
        self.assertTrue(result["receipt"]["shouldProcess"])
        self.assertFalse(result["receipt"]["recovered"])
        self.assertEqual(result["receipt"]["journalGeneration"], 2)


if __name__ == "__main__":
    unittest.main()
