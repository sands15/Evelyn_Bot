from __future__ import annotations

import os
import subprocess
import sys
import unittest
from pathlib import Path


REPO_ROOT = next(
    path for path in Path(__file__).resolve().parents if (path / "main.py").exists()
)
RUNTIME_ROOT = REPO_ROOT / "evelyn_core" / "runtime"
if str(RUNTIME_ROOT) not in sys.path:
    sys.path.insert(0, str(RUNTIME_ROOT))

from evelyn_core.process_identity import process_birth_identity  # noqa: E402
from evelyn_core.windows_process_job import (  # noqa: E402
    JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE,
    KillOnCloseProcessOwner,
)


class WindowsProcessJobTests(unittest.TestCase):
    def test_contract_uses_kill_on_job_close_flag(self):
        self.assertEqual(JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE, 0x00002000)

    @unittest.skipUnless(os.name == "nt", "Windows Job Object integration")
    def test_closing_owner_terminates_exact_assigned_child(self):
        owner = KillOnCloseProcessOwner()
        child = subprocess.Popen(
            [sys.executable, "-c", "import time; time.sleep(30)"],
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
        )
        try:
            birth_identity = process_birth_identity(int(child.pid))
            self.assertIsNotNone(birth_identity)
            self.assertTrue(owner.assign(child, str(birth_identity)))

            owner.close()

            child.wait(timeout=5)
            self.assertIsNotNone(child.returncode)
        finally:
            owner.close()
            if child.poll() is None:
                child.terminate()
                child.wait(timeout=5)


if __name__ == "__main__":
    unittest.main()
