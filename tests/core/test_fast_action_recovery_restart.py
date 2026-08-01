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

WRITER_PROCESS = textwrap.dedent(
    f"""
    import os
    import sys
    from pathlib import Path

    from evelyn_core.continuity_authenticity import ContinuityAuthenticity
    from evelyn_core.fast_action_recovery import (
        FastActionRecoveryJournal,
    )
    from evelyn_core.fast_control_continuity import (
        FastControlContinuityOwner,
    )

    root = Path(sys.argv[1])
    authenticity = ContinuityAuthenticity(
        key=Path(sys.argv[2]).read_bytes(),
        allow_unsigned_bootstrap=True,
        anchor_root=Path(sys.argv[3]),
    )
    owner = FastControlContinuityOwner(
        artifacts_root=root,
        enabled=True,
        authenticity=authenticity,
        log=lambda *_args, **_kwargs: None,
    )
    owner.record_completed_turn(
        "private restart request",
        "private start reply",
    )
    journal = FastActionRecoveryJournal(
        path=(
            root
            / "fast_control_actions"
            / "recovery.json"
        ),
        enabled=True,
        authenticity=authenticity,
    )
    journal.begin("fast-action-1")
    os._exit({CRASH_EXIT_CODE})
    """
)

RECOVERY_PROCESS = textwrap.dedent(
    """
    import json
    import sys
    from pathlib import Path

    from evelyn_core.continuity_authenticity import ContinuityAuthenticity
    from evelyn_core.fast_action_recovery import (
        FAST_ACTION_RECOVERY_NOTICE,
        FastActionRecoveryJournal,
    )
    from evelyn_core.fast_control_continuity import (
        FastControlContinuityOwner,
    )

    root = Path(sys.argv[1])
    authenticity = ContinuityAuthenticity(
        key=Path(sys.argv[2]).read_bytes(),
        anchor_root=Path(sys.argv[3]),
    )
    owner = FastControlContinuityOwner(
        artifacts_root=root,
        enabled=True,
        authenticity=authenticity,
        log=lambda *_args, **_kwargs: None,
    )
    journal = FastActionRecoveryJournal(
        path=(
            root
            / "fast_control_actions"
            / "recovery.json"
        ),
        enabled=True,
        authenticity=authenticity,
    )
    owner_status = owner.status()
    decision = journal.recovery_decision(
        continuity_generation=owner_status["generation"],
        continuity_ready=owner_status["durableReady"],
    )
    if decision["noticeRequired"]:
        owner.record_assistant_followup(
            FAST_ACTION_RECOVERY_NOTICE
        )
        journal.acknowledge_recovery(
            recovered_count=decision["pendingCount"],
            error_code=decision["reasonCode"],
        )
    print(
        json.dumps(
            {
                "decision": decision,
                "journal": journal.public_status(),
                "messages": owner.restored_chat_messages(),
                "generation": owner.status()["generation"],
            },
            ensure_ascii=False,
        )
    )
    """
)


class FastActionRecoveryRestartTests(unittest.TestCase):
    def subprocess_environment(self) -> dict[str, str]:
        environment = os.environ.copy()
        existing = environment.get("PYTHONPATH", "")
        environment["PYTHONPATH"] = os.pathsep.join(
            item
            for item in (str(RUNTIME_ROOT), existing)
            if item
        )
        return environment

    def test_ungraceful_exit_emits_one_durable_no_retry_notice(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            base = Path(temp_dir)
            root = base / "artifacts"
            key_path = base / "continuity-auth.key"
            key_path.write_bytes(
                b"fast-action-restart-auth-key-32-bytes"
            )
            anchor_root = base / "continuity-anchor"
            anchor_root.mkdir()
            writer = subprocess.run(
                [
                    sys.executable,
                    "-c",
                    WRITER_PROCESS,
                    str(root),
                    str(key_path),
                    str(anchor_root),
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
            journal_text = (
                root
                / "fast_control_actions"
                / "recovery.json"
            ).read_text(encoding="utf-8")
            self.assertNotIn(
                "private restart request",
                journal_text,
            )
            self.assertNotIn(
                "private start reply",
                journal_text,
            )

            recovery = subprocess.run(
                [
                    sys.executable,
                    "-c",
                    RECOVERY_PROCESS,
                    str(root),
                    str(key_path),
                    str(anchor_root),
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

            second_recovery = subprocess.run(
                [
                    sys.executable,
                    "-c",
                    RECOVERY_PROCESS,
                    str(root),
                    str(key_path),
                    str(anchor_root),
                ],
                cwd=REPO_ROOT,
                env=self.subprocess_environment(),
                text=True,
                capture_output=True,
                timeout=20,
                check=False,
            )
            self.assertEqual(
                second_recovery.returncode,
                0,
                second_recovery.stderr
                + second_recovery.stdout,
            )
            second = json.loads(second_recovery.stdout)

        self.assertTrue(
            result["decision"]["noticeRequired"]
        )
        self.assertEqual(
            result["decision"]["reasonCode"],
            "fast_action_recovery_interrupted",
        )
        self.assertEqual(result["journal"]["state"], "recovered")
        self.assertTrue(result["journal"]["tamperEvident"])
        self.assertTrue(
            result["journal"]["externalReplayProtected"]
        )
        # Bootstrap generation 1 + original turn + recovery notice.
        self.assertEqual(result["generation"], 3)
        notice = result["messages"][-1]["text"]
        self.assertIn("자동으로 다시 시도하지 않았어", notice)
        self.assertEqual(
            sum(
                message["text"] == notice
                for message in second["messages"]
            ),
            1,
        )
        self.assertFalse(
            second["decision"]["noticeRequired"]
        )


if __name__ == "__main__":
    unittest.main()
