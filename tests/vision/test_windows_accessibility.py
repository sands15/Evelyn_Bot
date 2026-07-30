from __future__ import annotations

import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

REPO_ROOT = next(
    path
    for path in Path(__file__).resolve().parents
    if (path / "main.py").exists()
)
RUNTIME_ROOT = REPO_ROOT / "evelyn_core" / "runtime"
if str(RUNTIME_ROOT) not in sys.path:
    sys.path.insert(0, str(RUNTIME_ROOT))

from evelyn_core.windows_accessibility import (
    WINDOWS_ACCESSIBILITY_OBSERVATION_SCHEMA,
    WINDOWS_ACCESSIBILITY_RESULT_SCHEMA,
    WindowsAccessibility,
    accessibility_supports_request,
    accessibility_window_matches_foreground,
)


def result_payload(*, captured_at: float = 999.0) -> dict[str, object]:
    return {
        "schema": WINDOWS_ACCESSIBILITY_RESULT_SCHEMA,
        "ok": True,
        "errorCode": "",
        "capturedAt": captured_at,
        "available": True,
        "windowTitle": "E.V.E.L.Y.N",
        "windowClass": "Chrome_WidgetWin_1",
        "truncated": False,
        "elements": [
            {
                "runtimeId": "42.7.9",
                "name": "전송",
                "automationId": "send-button",
                "controlType": "Button",
                "isEnabled": True,
                "bounds": {
                    "x": 120.0,
                    "y": 240.0,
                    "width": 80.0,
                    "height": 32.0,
                },
            },
            {
                "runtimeId": "42.7.10",
                "name": "대화",
                "automationId": "",
                "controlType": "Header",
                "isEnabled": True,
                "bounds": {
                    "x": 20.0,
                    "y": 40.0,
                    "width": 180.0,
                    "height": 44.0,
                },
            },
        ],
    }


class WindowsAccessibilityTests(unittest.TestCase):
    def build_runtime(self, payload: dict[str, object]):
        temp_dir = tempfile.TemporaryDirectory()
        root = Path(temp_dir.name)
        script = root / "invoke_windows_accessibility.ps1"
        powershell = root / "powershell.exe"
        script.write_text("# fixed", encoding="utf-8")
        powershell.write_bytes(b"fixed")
        calls: list[list[str]] = []

        def fake_run(arguments, **_kwargs):
            calls.append(list(arguments))
            return subprocess.CompletedProcess(
                arguments,
                0,
                stdout=json.dumps(payload, ensure_ascii=False),
                stderr="",
            )

        runtime = WindowsAccessibility(
            script_path=script,
            powershell_path=powershell,
            now=lambda: 1000.0,
            run_process=fake_run,
        )
        return temp_dir, runtime, calls

    def test_fixed_script_returns_bounded_hashed_elements(self) -> None:
        temp_dir, runtime, calls = self.build_runtime(result_payload())
        with temp_dir:
            with patch(
                "evelyn_core.windows_accessibility.os.name",
                "nt",
            ):
                observation = runtime.read_sync()

        self.assertEqual(
            observation["schema"],
            WINDOWS_ACCESSIBILITY_OBSERVATION_SCHEMA,
        )
        self.assertTrue(observation["available"])
        self.assertEqual(observation["windowTitle"], "E.V.E.L.Y.N")
        self.assertEqual(
            observation["elements"][0]["controlType"],
            "Button",
        )
        self.assertEqual(
            len(observation["elements"][0]["elementId"]),
            20,
        )
        self.assertNotIn("runtimeId", observation["elements"][0])
        self.assertIn("Button: 전송", observation["text"])
        self.assertEqual(
            calls[0][:8],
            [
                str(runtime.powershell_path),
                "-NoLogo",
                "-NoProfile",
                "-NonInteractive",
                "-ExecutionPolicy",
                "Bypass",
                "-File",
                str(runtime.script_path),
            ],
        )
        self.assertEqual(calls[0][-2:], ["-MaxElements", "120"])

    def test_request_sufficiency_requires_named_control_type(self) -> None:
        temp_dir, runtime, _calls = self.build_runtime(result_payload())
        with temp_dir:
            with patch(
                "evelyn_core.windows_accessibility.os.name",
                "nt",
            ):
                observation = runtime.read_sync()

        self.assertTrue(
            accessibility_supports_request(
                "제목과 버튼 하나를 말해줘",
                observation,
            )
        )
        without_button = {
            **observation,
            "elements": [
                item
                for item in observation["elements"]
                if item["controlType"] != "Button"
            ],
        }
        self.assertFalse(
            accessibility_supports_request(
                "제목과 버튼 하나를 말해줘",
                without_button,
            )
        )

    def test_foreground_binding_rejects_changed_window(self) -> None:
        observation = {
            "windowTitle": "E.V.E.L.Y.N",
            "windowClass": "Chrome_WidgetWin_1",
        }
        self.assertTrue(
            accessibility_window_matches_foreground(
                observation,
                {
                    "title": "E.V.E.L.Y.N",
                    "className": "Chrome_WidgetWin_1",
                },
            )
        )
        self.assertFalse(
            accessibility_window_matches_foreground(
                observation,
                {
                    "title": "다른 창",
                    "className": "Chrome_WidgetWin_1",
                },
            )
        )

    def test_unknown_control_type_is_rejected(self) -> None:
        payload = result_payload()
        payload["elements"][0]["controlType"] = "Edit"
        temp_dir, runtime, _calls = self.build_runtime(payload)
        with temp_dir:
            with patch(
                "evelyn_core.windows_accessibility.os.name",
                "nt",
            ):
                with self.assertRaisesRegex(
                    RuntimeError,
                    "control_type_not_allowed",
                ):
                    runtime.read_sync()

    def test_stale_observation_is_rejected(self) -> None:
        temp_dir, runtime, _calls = self.build_runtime(
            result_payload(captured_at=900.0)
        )
        with temp_dir:
            with patch(
                "evelyn_core.windows_accessibility.os.name",
                "nt",
            ):
                with self.assertRaisesRegex(
                    RuntimeError,
                    "accessibility_stale",
                ):
                    runtime.read_sync()

    def test_script_contract_is_read_only_and_has_no_user_text(self) -> None:
        repo_root = next(
            path
            for path in Path(__file__).resolve().parents
            if (path / "main.py").exists()
        )
        source = (
            repo_root
            / "evelyn_core"
            / "runtime"
            / "launchers"
            / "invoke_windows_accessibility.ps1"
        ).read_text(encoding="utf-8")

        self.assertIn("[ValidateRange(1, 160)]", source)
        self.assertIn("ControlViewWalker", source)
        self.assertIn("$allowedTypes", source)
        self.assertNotIn("$UserText", source)
        self.assertNotIn("$Command", source)
        self.assertNotIn("InvokePattern", source)
        self.assertNotIn("ValuePattern", source)
        self.assertNotIn("SetFocus", source)
        self.assertNotIn("ProcessId", source)


if __name__ == "__main__":
    unittest.main()
