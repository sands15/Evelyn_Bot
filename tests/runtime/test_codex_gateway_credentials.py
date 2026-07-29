from __future__ import annotations

import json
import sys
import tempfile
import unittest
from pathlib import Path


REPO_ROOT = next(
    path for path in Path(__file__).resolve().parents
    if (path / "main.py").exists()
)
RUNTIME_ROOT = REPO_ROOT / "evelyn_core" / "runtime"
if str(RUNTIME_ROOT) not in sys.path:
    sys.path.insert(0, str(RUNTIME_ROOT))

from evelyn_core.codex_gateway_credentials import (  # noqa: E402
    EPHEMERAL_HOME_MARKER,
    prepare_codex_credentials,
    stage_codex_credentials,
)


class CodexGatewayCredentialTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp_dir.cleanup)
        self.root = Path(self.temp_dir.name)

    def test_only_known_files_are_staged_into_marked_ephemeral_home(self) -> None:
        source = self.root / "source"
        target = self.root / "target"
        source.mkdir()
        (source / "auth.json").write_text(
            json.dumps({"tokens": {"access_token": "secret"}}),
            encoding="utf-8",
        )
        (source / "config.toml").write_text("model = \"gpt-5.5\"\n", encoding="utf-8")
        (source / "unrelated.txt").write_text("do not copy", encoding="utf-8")

        result = stage_codex_credentials(source, target)

        self.assertTrue(result["ready"])
        self.assertTrue((target / "auth.json").is_file())
        self.assertTrue((target / "config.toml").is_file())
        self.assertTrue((target / EPHEMERAL_HOME_MARKER).is_file())
        self.assertFalse((target / "unrelated.txt").exists())
        self.assertNotIn("secret", json.dumps(result))
        self.assertNotIn(str(source), json.dumps(result))

    def test_non_ephemeral_existing_target_is_not_overwritten(self) -> None:
        source = self.root / "source"
        target = self.root / "target"
        source.mkdir()
        target.mkdir()
        (source / "auth.json").write_text("{}", encoding="utf-8")
        (target / "auth.json").write_text("existing", encoding="utf-8")

        with self.assertRaisesRegex(
            RuntimeError,
            "codex_credentials_target_not_ephemeral",
        ):
            stage_codex_credentials(source, target)

        self.assertEqual(
            (target / "auth.json").read_text(encoding="utf-8"),
            "existing",
        )

    def test_invalid_auth_returns_fixed_error_code_without_paths(self) -> None:
        source = self.root / "source"
        target = self.root / "target"
        source.mkdir()
        (source / "auth.json").write_text("not-json", encoding="utf-8")

        status = prepare_codex_credentials(
            {
                "EVELYN_CODEX_CREDENTIALS_DIR": str(source),
                "CODEX_HOME": str(target),
            }
        )

        self.assertFalse(status["ready"])
        self.assertEqual(
            status["errorCode"],
            "codex_credentials_auth_invalid",
        )
        self.assertNotIn(str(source), json.dumps(status))

    def test_existing_codex_home_is_not_accepted_without_dedicated_source(self) -> None:
        target = self.root / "live-home"
        target.mkdir()
        (target / "auth.json").write_text("{}", encoding="utf-8")

        status = prepare_codex_credentials({"CODEX_HOME": str(target)})

        self.assertFalse(status["ready"])
        self.assertEqual(status["mode"], "unconfigured")
        self.assertEqual(status["errorCode"], "codex_credentials_unconfigured")


if __name__ == "__main__":
    unittest.main()
