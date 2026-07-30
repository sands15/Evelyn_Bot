from __future__ import annotations

import re
import sys
import unittest
from pathlib import Path


REPO_ROOT = next(
    path
    for path in Path(__file__).resolve().parents
    if (path / "main.py").exists()
)
RUNTIME_ROOT = REPO_ROOT / "evelyn_core" / "runtime"
if str(RUNTIME_ROOT) not in sys.path:
    sys.path.insert(0, str(RUNTIME_ROOT))

from evelyn_core.public_error_contract import (  # noqa: E402
    PUBLIC_FAILURE_MESSAGES,
    public_failure_message,
)


class PublicErrorContractTests(unittest.TestCase):
    def test_known_errors_are_fixed_and_safe(self) -> None:
        for code, message in PUBLIC_FAILURE_MESSAGES.items():
            with self.subTest(code=code):
                self.assertRegex(
                    code,
                    re.compile(r"^[a-z][a-z0-9_]{2,63}$"),
                )
                self.assertEqual(
                    public_failure_message(code),
                    f"{message} ({code})",
                )
                self.assertNotIn("\\", message)
                self.assertNotIn("http://", message)
                self.assertNotIn("https://", message)

    def test_unknown_or_malicious_code_uses_fixed_fallback(self) -> None:
        secret = "Bearer secret C:\\private http://internal"
        rendered = public_failure_message(secret)

        self.assertEqual(
            rendered,
            f"{PUBLIC_FAILURE_MESSAGES['operation_failed']} "
            "(operation_failed)",
        )
        self.assertNotIn("secret", rendered)
        self.assertNotIn("private", rendered)
        self.assertNotIn("internal", rendered)

    def test_every_public_message_uses_only_fixed_text(self) -> None:
        for code in (
            "open_memory_vault_failed",
            "repair_launch_failed",
            "local_restart_failed",
            "local_shutdown_failed",
            "mic_control_failed",
            "minecraft_snapshot_unavailable",
        ):
            with self.subTest(code=code):
                rendered = public_failure_message(code)
                self.assertIn(f"({code})", rendered)
                self.assertNotIn("\\", rendered)
                self.assertNotIn("http://", rendered)


if __name__ == "__main__":
    unittest.main()
