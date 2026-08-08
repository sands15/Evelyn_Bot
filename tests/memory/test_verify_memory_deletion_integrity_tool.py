from __future__ import annotations

import json
import os
import subprocess
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

from evelyn_core import memory_deletion_journal as journal  # noqa: E402
from evelyn_core.memory_integrity_authenticity import (  # noqa: E402
    MEMORY_INTEGRITY_ANCHOR_DIR_ENV,
    MEMORY_INTEGRITY_BOOTSTRAP_ENV,
    MEMORY_INTEGRITY_KEY_FILE_ENV,
)


TOOL = REPO_ROOT / "tools" / "verify_memory_deletion_integrity.py"


class VerifyMemoryDeletionIntegrityToolTests(unittest.TestCase):
    def run_tool(
        self,
        scratch: Path,
        key: Path,
        anchor: Path,
    ) -> subprocess.CompletedProcess[str]:
        return subprocess.run(
            [
                sys.executable,
                str(TOOL),
                "--scratch-root",
                str(scratch),
                "--key-file",
                str(key),
                "--anchor-dir",
                str(anchor),
            ],
            cwd=REPO_ROOT,
            capture_output=True,
            text=True,
            timeout=60,
            check=False,
        )

    def test_disposable_replica_bootstrap_and_replay_contract(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            scratch = root / "scratch"
            key_dir = root / "key"
            anchor = root / "anchor"
            for path in (scratch, key_dir, anchor):
                path.mkdir()
            key = key_dir / "memory-integrity.key"
            key.write_bytes(b"replica-memory-integrity-key-material!" * 2)

            completed = self.run_tool(scratch, key, anchor)
            payload = json.loads(completed.stdout)
            output = completed.stdout + completed.stderr

            self.assertEqual(completed.returncode, 0)
            self.assertEqual(
                payload["schema"],
                "memory.deletion.integrity.replica-verification.v1",
            )
            self.assertTrue(payload["ok"])
            self.assertTrue(payload["replicaContractVerified"])
            self.assertTrue(payload["pathIsolationVerified"])
            self.assertTrue(payload["strictPreBootstrapRejected"])
            self.assertTrue(payload["oneShotBootstrapVerified"])
            self.assertTrue(payload["strictRestartVerified"])
            self.assertTrue(payload["pastPairReplayRejected"])
            self.assertTrue(payload["replicaRestored"])
            self.assertTrue(payload["rollbackProtected"])
            self.assertEqual(payload["sequence"], 2)
            self.assertEqual(
                payload["replayError"],
                journal.MEMORY_DELETION_JOURNAL_INTEGRITY_ERROR,
            )
            self.assertEqual(payload["permissionState"], "not_verified")
            self.assertFalse(payload["operationallyVerified"])
            self.assertTrue(payload["contentFree"])
            self.assertNotIn(str(scratch), output)
            self.assertNotIn(str(key), output)
            self.assertNotIn(str(anchor), output)
            self.assertNotIn("replica-integrity-canary", output)

            env = {
                MEMORY_INTEGRITY_KEY_FILE_ENV: str(key),
                MEMORY_INTEGRITY_ANCHOR_DIR_ENV: str(anchor),
                MEMORY_INTEGRITY_BOOTSTRAP_ENV: "false",
            }
            with patch.dict(os.environ, env):
                index_dir = scratch / "memory_root" / "memory_index"
                rows = journal.read_memory_deletion_tombstones(index_dir)
                status = journal.memory_deletion_journal_status(index_dir)
            self.assertEqual(len(rows), 2)
            self.assertEqual(rows[-1]["sequence"], 2)
            self.assertTrue(status["rollbackProtected"])

    def test_nonempty_anchor_is_rejected_without_mutation(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            scratch = root / "scratch"
            key_dir = root / "key"
            anchor = root / "anchor"
            for path in (scratch, key_dir, anchor):
                path.mkdir()
            key = key_dir / "memory-integrity.key"
            key.write_bytes(b"replica-memory-integrity-key-material!" * 2)
            sentinel = anchor / "operator-owned.txt"
            sentinel.write_text("unchanged", encoding="utf-8")

            completed = self.run_tool(scratch, key, anchor)
            payload = json.loads(completed.stdout)

            self.assertEqual(completed.returncode, 2)
            self.assertFalse(payload["ok"])
            self.assertEqual(
                payload["error"],
                "memory_deletion_integrity_replica_verification_failed",
            )
            self.assertFalse(payload["replicaContractVerified"])
            self.assertEqual(payload["permissionState"], "not_verified")
            self.assertFalse(payload["operationallyVerified"])
            self.assertTrue(payload["contentFree"])
            self.assertEqual(sentinel.read_text(encoding="utf-8"), "unchanged")
            self.assertEqual(list(scratch.iterdir()), [])

    def test_hidden_child_requires_disposable_replica_marker(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            scratch = Path(tmp) / "scratch"
            scratch.mkdir()
            completed = subprocess.run(
                [
                    sys.executable,
                    str(TOOL),
                    "--_phase",
                    "seed",
                    "--scratch-root",
                    str(scratch),
                ],
                cwd=REPO_ROOT,
                capture_output=True,
                text=True,
                timeout=30,
                check=False,
            )
            payload = json.loads(completed.stdout)

            self.assertEqual(completed.returncode, 2)
            self.assertFalse(payload["ok"])
            self.assertEqual(
                payload["error"],
                "memory_deletion_integrity_replica_verification_failed",
            )
            self.assertFalse((scratch / "memory_root").exists())


if __name__ == "__main__":
    unittest.main()
