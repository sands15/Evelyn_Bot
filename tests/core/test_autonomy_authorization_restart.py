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
CRASH_EXIT_CODE = 78

WRITER_PROCESS = textwrap.dedent(
    f"""
    import os
    import sys
    from pathlib import Path

    from evelyn_core.autonomy_authorization import (
        AutonomyAuthorizationManager,
    )

    root = Path(sys.argv[1])
    manager = AutonomyAuthorizationManager(
        status_path=root / "status.json",
        events_dir=root / "events",
    )
    manager.initialize()
    granted = manager.grant(
        guild_id=7,
        issuer_ref="discord_user:42",
        source="discord_command",
        scopes=["assistant:idle"],
    )
    if not granted.get("ok"):
        os._exit(79)
    decision = manager.authorize(7, "assistant:idle")
    if not decision.get("allowed"):
        os._exit(80)
    os._exit({CRASH_EXIT_CODE})
    """
)

RECOVERY_PROCESS = textwrap.dedent(
    """
    import json
    import sys
    from pathlib import Path

    from evelyn_core.autonomy_authorization import (
        AutonomyAuthorizationManager,
    )

    root = Path(sys.argv[1])
    manager = AutonomyAuthorizationManager(
        status_path=root / "status.json",
        events_dir=root / "events",
    )
    status = manager.initialize()
    decision = manager.authorize(7, "assistant:idle")
    print(
        json.dumps(
            {
                "status": status,
                "decision": decision,
            },
            ensure_ascii=False,
        )
    )
    """
)


class AutonomyAuthorizationRestartTests(unittest.TestCase):
    def subprocess_environment(self) -> dict[str, str]:
        environment = os.environ.copy()
        existing = environment.get("PYTHONPATH", "")
        environment["PYTHONPATH"] = os.pathsep.join(
            item
            for item in (str(RUNTIME_ROOT), existing)
            if item
        )
        return environment

    def test_crash_restart_never_restores_action_authority(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            writer = subprocess.run(
                [
                    sys.executable,
                    "-c",
                    WRITER_PROCESS,
                    str(root),
                ],
                cwd=REPO_ROOT,
                env=self.subprocess_environment(),
                text=True,
                capture_output=True,
                timeout=20,
                check=False,
            )
            self.assertEqual(
                writer.returncode,
                CRASH_EXIT_CODE,
                writer.stderr + writer.stdout,
            )

            recovery = subprocess.run(
                [
                    sys.executable,
                    "-c",
                    RECOVERY_PROCESS,
                    str(root),
                ],
                cwd=REPO_ROOT,
                env=self.subprocess_environment(),
                text=True,
                capture_output=True,
                timeout=20,
                check=False,
            )
            self.assertEqual(
                recovery.returncode,
                0,
                recovery.stderr + recovery.stdout,
            )
            result = json.loads(recovery.stdout)
            events = [
                json.loads(line)
                for path in sorted((root / "events").glob("*.jsonl"))
                for line in path.read_text(encoding="utf-8").splitlines()
                if line.strip()
            ]

            self.assertEqual(
                result["status"]["state"],
                "authorization_required",
            )
            self.assertEqual(
                result["status"]["activeGrantCount"],
                0,
            )
            self.assertFalse(result["decision"]["allowed"])
            self.assertEqual(
                result["decision"]["code"],
                "authorization_required",
            )
            process_nonces = {
                row["processNonce"]
                for row in events
                if row["event"] == "process_started"
            }
            self.assertEqual(len(process_nonces), 2)
            self.assertEqual(
                sum(row["event"] == "grant_issued" for row in events),
                1,
            )
            self.assertEqual(
                sum(row["event"] == "action_authorized" for row in events),
                1,
            )
            self.assertEqual(
                sum(row["event"] == "action_denied" for row in events),
                1,
            )


if __name__ == "__main__":
    unittest.main()
