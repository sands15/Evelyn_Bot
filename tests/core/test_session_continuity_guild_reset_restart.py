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
CRASH_EXIT_CODES = {
    "before_clear": 76,
    "after_clear": 77,
}

WRITER_PROCESS = textwrap.dedent(
    """
    import os
    import sys
    from pathlib import Path

    from evelyn_core.continuity_authenticity import ContinuityAuthenticity
    from evelyn_core.session_continuity import SessionContinuityCheckpoint
    from evelyn_core.session_memory_state import SessionStateStore

    root = Path(sys.argv[1])
    authenticity = ContinuityAuthenticity(
        key=Path(sys.argv[2]).read_bytes(),
        allow_unsigned_bootstrap=True,
        anchor_root=Path(sys.argv[3]),
    )
    phase = sys.argv[4]
    crash_code = int(sys.argv[5])
    store = SessionStateStore.create_empty()
    sessions = (
        (
            "guild:7:text:8:user:42",
            "reset guild private turn",
            "must not return",
            42,
        ),
        (
            "guild:9:text:10:user:43",
            "other guild retained turn",
            "must survive",
            43,
        ),
    )
    for session_key, user_text, reply_text, user_id in sessions:
        store.append_history(
            session_key,
            user_text,
            reply_text,
            system_prompt="old system prompt",
            max_history_items=12,
        )
        store.update_session_state(
            session_key,
            user_id=user_id,
            speaker="assistant",
            awaiting_user_reply=True,
            topic_id=f"topic-{user_id}",
            active_conversation_awaiting_reply_sec=300.0,
        )
    manager = SessionContinuityCheckpoint(
        store=store,
        checkpoint_path=root / "active.json",
        status_path=root / "status.json",
        system_prompt="old system prompt",
        authenticity=authenticity,
    )
    manager.flush(force=True)

    def crash_during_reset():
        if phase == "after_clear":
            prefix = "guild:7:"
            for mapping in (
                store.histories,
                store.followup_targets,
                store.active_until,
                store.active_user_ids,
                store.last_active_at,
                store.awaiting_user_reply,
                store.last_speaker,
                store.topic_ids,
                store.turn_ids,
            ):
                for key in list(mapping):
                    if key.startswith(prefix):
                        mapping.pop(key, None)
        os._exit(crash_code)

    manager.reset_guild(7, crash_during_reset)
    os._exit(78)
    """
)

RECOVERY_PROCESS = textwrap.dedent(
    """
    import json
    import sys
    from pathlib import Path

    from evelyn_core.continuity_authenticity import ContinuityAuthenticity
    from evelyn_core.session_continuity import SessionContinuityCheckpoint
    from evelyn_core.session_memory_state import SessionStateStore

    root = Path(sys.argv[1])
    authenticity = ContinuityAuthenticity(
        key=Path(sys.argv[2]).read_bytes(),
        anchor_root=Path(sys.argv[3]),
    )
    store = SessionStateStore.create_empty()
    manager = SessionContinuityCheckpoint(
        store=store,
        checkpoint_path=root / "active.json",
        status_path=root / "status.json",
        system_prompt="new system prompt",
        authenticity=authenticity,
    )
    status = manager.restore()
    print(
        json.dumps(
            {
                "status": status,
                "histories": store.histories,
                "activeUserIds": store.active_user_ids,
            },
            ensure_ascii=False,
        )
    )
    """
)


class SessionContinuityGuildResetRestartTests(unittest.TestCase):
    def subprocess_environment(self) -> dict[str, str]:
        environment = os.environ.copy()
        existing = environment.get("PYTHONPATH", "")
        environment["PYTHONPATH"] = os.pathsep.join(
            item
            for item in (str(RUNTIME_ROOT), existing)
            if item
        )
        return environment

    def test_reset_guild_never_returns_after_crash_at_either_boundary(
        self,
    ) -> None:
        for phase, exit_code in CRASH_EXIT_CODES.items():
            with self.subTest(phase=phase), tempfile.TemporaryDirectory() as temp_dir:
                base = Path(temp_dir)
                root = base / "continuity"
                key_path = base / "continuity-auth.key"
                key_path.write_bytes(
                    b"guild-reset-restart-auth-key-32-bytes"
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
                        phase,
                        str(exit_code),
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
                    exit_code,
                    writer.stderr + writer.stdout,
                )

                ledger_text = (
                    root / "guild_revocations.json"
                ).read_text(encoding="utf-8")
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

                self.assertEqual(result["status"]["state"], "restored")
                self.assertEqual(
                    result["status"]["guildRevocationsAuthenticity"],
                    "verified",
                )
                self.assertTrue(
                    result["status"][
                        "guildRevocationsReplayProtected"
                    ]
                )
                self.assertTrue(
                    result["status"]["externalReplayProtected"]
                )
                self.assertEqual(
                    result["status"]["restoredSessionCount"],
                    1,
                )
                self.assertNotIn(
                    "guild:7:text:8:user:42",
                    result["histories"],
                )
                self.assertNotIn(42, result["activeUserIds"].values())
                self.assertEqual(
                    result["histories"]["guild:9:text:10:user:43"],
                    [
                        {
                            "role": "system",
                            "content": "new system prompt",
                        },
                        {
                            "role": "user",
                            "content": "other guild retained turn",
                        },
                        {
                            "role": "assistant",
                            "content": "must survive",
                        },
                    ],
                )
                self.assertNotIn(
                    "reset guild private turn",
                    ledger_text,
                )
                self.assertNotIn(
                    "other guild retained turn",
                    ledger_text,
                )


if __name__ == "__main__":
    unittest.main()
