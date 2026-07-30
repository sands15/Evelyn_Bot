from __future__ import annotations

import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch


REPO_ROOT = next(
    path for path in Path(__file__).resolve().parents if (path / "main.py").exists()
)
RUNTIME_ROOT = REPO_ROOT / "evelyn_core" / "runtime"
if str(RUNTIME_ROOT) not in sys.path:
    sys.path.insert(0, str(RUNTIME_ROOT))

from evelyn_core.windows_accessibility_invoke import (  # noqa: E402
    WINDOWS_UI_ACTION_RESULT_SCHEMA,
    WindowsAccessibilityInvoker,
)


class WindowsAccessibilityInvokerTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.script = self.root / "invoke.ps1"
        self.powershell = self.root / "powershell.exe"
        self.script.write_text("# fixed", encoding="utf-8")
        self.powershell.write_bytes(b"fixed")
        self.now = 1000.0
        self.calls = []

    def tearDown(self) -> None:
        self.temp.cleanup()

    def completed(self, *, payload: dict, returncode: int = 0):
        return subprocess.CompletedProcess(
            args=[],
            returncode=returncode,
            stdout=json.dumps(payload),
            stderr="",
        )

    def runtime(self, result):
        def run_process(*args, **kwargs):
            self.calls.append((args, kwargs))
            return result

        return WindowsAccessibilityInvoker(
            script_path=self.script,
            powershell_path=self.powershell,
            now=lambda: self.now,
            run_process=run_process,
        )

    def payload(self, **updates):
        payload = {
            "schema": WINDOWS_UI_ACTION_RESULT_SCHEMA,
            "ok": True,
            "errorCode": "",
            "completedAt": self.now,
            "executed": True,
            "action": "invoke",
            "elementId": "a" * 20,
            "windowDigest": "b" * 64,
        }
        payload.update(updates)
        return payload

    def test_invoker_uses_only_fixed_script_and_validated_identifiers(self) -> None:
        runtime = self.runtime(self.completed(payload=self.payload()))
        with patch.object(os, "name", "nt"):
            result = runtime.invoke_sync(
                action="invoke",
                element_id="a" * 20,
                window_digest="b" * 64,
            )

        self.assertTrue(result["executed"])
        args, kwargs = self.calls[0]
        command = args[0]
        self.assertEqual(command[0], str(self.powershell))
        self.assertIn(str(self.script), command)
        self.assertIn("a" * 20, command)
        self.assertIn("b" * 64, command)
        self.assertNotIn("cwd", kwargs)
        self.assertFalse(kwargs["check"])

    def test_arbitrary_action_or_identifier_is_rejected_before_process(self) -> None:
        runtime = self.runtime(self.completed(payload=self.payload()))

        for kwargs in (
            {
                "action": "click",
                "element_id": "a" * 20,
                "window_digest": "b" * 64,
            },
            {
                "action": "invoke",
                "element_id": "1; calc.exe",
                "window_digest": "b" * 64,
            },
            {
                "action": "invoke",
                "element_id": "a" * 20,
                "window_digest": "../private",
            },
        ):
            with self.subTest(kwargs=kwargs):
                with self.assertRaises(ValueError):
                    runtime.invoke_sync(**kwargs)

        self.assertEqual(self.calls, [])

    def test_stale_unknown_or_contradictory_response_is_rejected(self) -> None:
        cases = [
            self.payload(completedAt=self.now - 6.0),
            {**self.payload(), "extra": "field"},
            self.payload(ok=False, executed=True),
            self.payload(elementId="c" * 20),
        ]
        for payload in cases:
            with self.subTest(payload=payload):
                runtime = self.runtime(self.completed(payload=payload))
                with patch.object(os, "name", "nt"):
                    with self.assertRaises(RuntimeError):
                        runtime.invoke_sync(
                            action="invoke",
                            element_id="a" * 20,
                            window_digest="b" * 64,
                        )

    def test_script_contract_contains_no_coordinate_or_shell_execution(self) -> None:
        source = (
            REPO_ROOT
            / "evelyn_core"
            / "runtime"
            / "launchers"
            / "invoke_windows_accessibility_action.ps1"
        ).read_text(encoding="utf-8")

        self.assertIn("[ValidateSet('invoke')]", source)
        self.assertIn("InvokePattern", source)
        self.assertIn("$ExpectedWindowDigest", source)
        self.assertIn(
            "Clean-UiActionText -Value $runtimeId -MaxChars 160",
            source,
        )
        self.assertNotIn("SetCursorPos", source)
        self.assertNotIn("mouse_event", source)
        self.assertNotIn("Start-Process", source)
        self.assertNotIn("Invoke-Expression", source)


if __name__ == "__main__":
    unittest.main()
