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

WRITER = textwrap.dedent(
    f"""
    import os
    import sys
    from pathlib import Path

    from evelyn_core.fast_control_continuity import (
        FastControlContinuityOwner,
    )

    owner = FastControlContinuityOwner(
        artifacts_root=Path(sys.argv[1]),
        enabled=True,
        log=lambda *_args, **_kwargs: None,
    )
    owner.record_completed_turn(
        "재시작 전에 실패한 질문",
        "고정 실패 응답",
    )
    os._exit({CRASH_EXIT_CODE})
    """
)

RECOVER = textwrap.dedent(
    """
    import json
    import sys
    from pathlib import Path

    from evelyn_core.fast_control_continuity import (
        FastControlContinuityOwner,
    )

    owner = FastControlContinuityOwner(
        artifacts_root=Path(sys.argv[1]),
        enabled=True,
        log=lambda *_args, **_kwargs: None,
    )
    print(
        json.dumps(
            {
                "restore": owner.restore_status,
                "messages": owner.restored_chat_messages(),
                "status": owner.status(),
            },
            ensure_ascii=False,
        )
    )
    """
)


class FastControlContinuityRestartTests(unittest.TestCase):
    def environment(self) -> dict[str, str]:
        environment = os.environ.copy()
        existing = environment.get("PYTHONPATH", "")
        environment["PYTHONPATH"] = os.pathsep.join(
            part
            for part in (str(RUNTIME_ROOT), existing)
            if part
        )
        return environment

    def test_fresh_process_restores_delivered_failure_turn(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            writer = subprocess.run(
                [
                    sys.executable,
                    "-c",
                    WRITER,
                    temp_dir,
                ],
                cwd=REPO_ROOT,
                env=self.environment(),
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
                    RECOVER,
                    temp_dir,
                ],
                cwd=REPO_ROOT,
                env=self.environment(),
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
            status_text = (
                Path(temp_dir)
                / "fast_control_continuity"
                / "status.json"
            ).read_text(encoding="utf-8")

        self.assertEqual(
            result["restore"]["state"],
            "restored",
        )
        self.assertEqual(
            result["restore"]["checkpointIntegrity"],
            "verified",
        )
        self.assertTrue(
            result["restore"]["rollbackProtected"]
        )
        self.assertEqual(
            [
                (item["role"], item["text"])
                for item in result["messages"]
            ],
            [
                ("user", "재시작 전에 실패한 질문"),
                ("assistant", "고정 실패 응답"),
            ],
        )
        self.assertTrue(result["status"]["durableReady"])
        self.assertNotIn("재시작 전에 실패한 질문", status_text)
        self.assertNotIn("고정 실패 응답", status_text)


if __name__ == "__main__":
    unittest.main()
