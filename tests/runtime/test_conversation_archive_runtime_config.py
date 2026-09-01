from __future__ import annotations

import json
import os
import subprocess
import sys
import unittest
from pathlib import Path


REPO_ROOT = next(
    path for path in Path(__file__).resolve().parents if (path / "main.py").exists()
)
RUNTIME_ROOT = REPO_ROOT / "evelyn_core" / "runtime"


def _config_snapshot(**environment: str) -> dict[str, object]:
    script = """
import json
from evelyn_core import main_runtime_config as config
print(json.dumps({
    "archive": config.CONVERSATION_ARCHIVE_ENABLED,
    "debugRequested": config.VOICE_DEBUG_SAVE_AUDIO_REQUESTED,
    "debugEnabled": config.VOICE_DEBUG_SAVE_AUDIO,
    "rejoinRequested": config.VOICE_REJOIN_ON_READY_REQUESTED,
    "rejoinEnabled": config.VOICE_REJOIN_ON_READY,
    "deleteSeconds": config.CONVERSATION_ARCHIVE_EPHEMERAL_DELETE_SEC,
    "commandGuildId": config.CONVERSATION_ARCHIVE_COMMAND_GUILD_ID,
    "ownershipFile": config.ARCHIVE_COMMAND_OWNERSHIP_FILE,
    "ownershipRunId": config.ARCHIVE_COMMAND_RUN_ID,
}))
"""
    child_environment = os.environ.copy()
    child_environment.update(environment)
    child_environment["PYTHONPATH"] = str(RUNTIME_ROOT)
    result = subprocess.run(
        [sys.executable, "-c", script],
        cwd=REPO_ROOT,
        env=child_environment,
        check=True,
        capture_output=True,
        text=True,
    )
    return json.loads(result.stdout)


class ConversationArchiveRuntimeConfigTests(unittest.TestCase):
    def test_archive_defaults_off_without_changing_existing_debug_or_rejoin(self) -> None:
        snapshot = _config_snapshot(
            EVELYN_CONVERSATION_ARCHIVE_ENABLED="false",
            VOICE_DEBUG_SAVE_AUDIO="true",
            VOICE_REJOIN_ON_READY="true",
        )

        self.assertFalse(snapshot["archive"])
        self.assertTrue(snapshot["debugRequested"])
        self.assertTrue(snapshot["debugEnabled"])
        self.assertTrue(snapshot["rejoinRequested"])
        self.assertTrue(snapshot["rejoinEnabled"])

    def test_archive_forces_raw_debug_audio_and_stale_rejoin_off(self) -> None:
        snapshot = _config_snapshot(
            EVELYN_CONVERSATION_ARCHIVE_ENABLED="true",
            VOICE_DEBUG_SAVE_AUDIO="true",
            VOICE_REJOIN_ON_READY="true",
        )

        self.assertTrue(snapshot["archive"])
        self.assertTrue(snapshot["debugRequested"])
        self.assertFalse(snapshot["debugEnabled"])
        self.assertTrue(snapshot["rejoinRequested"])
        self.assertFalse(snapshot["rejoinEnabled"])
        self.assertEqual(snapshot["deleteSeconds"], 180)

    def test_archive_command_guild_id_is_exact_decimal_snowflake(self) -> None:
        snapshot = _config_snapshot(
            EVELYN_CONVERSATION_ARCHIVE_COMMAND_GUILD_ID="123456789012345678",
        )

        self.assertEqual(snapshot["commandGuildId"], 123456789012345678)

    def test_archive_command_guild_id_rejects_non_snowflake_input(self) -> None:
        with self.assertRaises(subprocess.CalledProcessError):
            _config_snapshot(
                EVELYN_CONVERSATION_ARCHIVE_COMMAND_GUILD_ID="used-server",
            )

    def test_archive_command_ownership_requires_absolute_path_and_exact_run_id(self) -> None:
        snapshot = _config_snapshot(
            EVELYN_CONVERSATION_ARCHIVE_COMMAND_OWNERSHIP_LEDGER=(
                "/run/evelyn-command-guard/ownership.json"
            ),
            EVELYN_CONVERSATION_ARCHIVE_COMMAND_RUN_ID="a" * 32,
        )
        self.assertEqual(
            snapshot["ownershipFile"],
            "/run/evelyn-command-guard/ownership.json",
        )
        self.assertEqual(snapshot["ownershipRunId"], "a" * 32)

        for environment in (
            {"EVELYN_CONVERSATION_ARCHIVE_COMMAND_RUN_ID": "a" * 32},
            {
                "EVELYN_CONVERSATION_ARCHIVE_COMMAND_OWNERSHIP_LEDGER": "relative.json",
                "EVELYN_CONVERSATION_ARCHIVE_COMMAND_RUN_ID": "a" * 32,
            },
            {
                "EVELYN_CONVERSATION_ARCHIVE_COMMAND_OWNERSHIP_LEDGER": "/tmp/owner.json",
                "EVELYN_CONVERSATION_ARCHIVE_COMMAND_RUN_ID": "A" * 32,
            },
        ):
            with self.subTest(environment=environment):
                with self.assertRaises(subprocess.CalledProcessError):
                    _config_snapshot(**environment)


if __name__ == "__main__":
    unittest.main()
