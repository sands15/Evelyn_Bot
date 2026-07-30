from __future__ import annotations

import json
import os
from pathlib import Path
import subprocess
import tempfile
import unittest
from unittest.mock import patch

from PIL import Image

from evelyn_core.windows_native_ocr import (
    WINDOWS_OCR_OBSERVATION_SCHEMA,
    WINDOWS_OCR_RESULT_SCHEMA,
    WindowsNativeOcr,
    normalize_windows_ocr_text,
)


class WindowsNativeOcrTests(unittest.TestCase):
    def test_recognize_uses_only_fixed_script_and_bridge_owned_tiles(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            screenshot = root / "screen.png"
            script = root / "invoke_windows_ocr.ps1"
            powershell = root / "powershell.exe"
            Image.new("RGB", (3840, 2160), color="white").save(screenshot)
            script.write_text("# fixed", encoding="utf-8")
            powershell.write_bytes(b"fixed")
            calls: list[list[str]] = []

            def fake_run(arguments, **_kwargs):
                calls.append(list(arguments))
                return subprocess.CompletedProcess(
                    arguments,
                    0,
                    stdout=json.dumps(
                        {
                            "schema": WINDOWS_OCR_RESULT_SCHEMA,
                            "ok": True,
                            "language": "ko",
                            "text": "E.V.E.L.Y.N 전송",
                        },
                        ensure_ascii=False,
                    ),
                    stderr="",
                )

            runtime = WindowsNativeOcr(
                screenshot_root=root,
                script_path=script,
                powershell_path=powershell,
                run_process=fake_run,
            )
            with patch("evelyn_core.windows_native_ocr.os.name", "nt"):
                result = runtime.recognize_sync(screenshot)

            self.assertEqual(
                result,
                {
                    "schema": WINDOWS_OCR_OBSERVATION_SCHEMA,
                    "attempted": True,
                    "text": "E.V.E.L.Y.N 전송",
                },
            )
            self.assertEqual(len(calls), 6)
            self.assertTrue(
                all(
                    call[:8]
                    == [
                        str(powershell),
                        "-NoLogo",
                        "-NoProfile",
                        "-NonInteractive",
                        "-ExecutionPolicy",
                        "Bypass",
                        "-File",
                        str(script),
                    ]
                    for call in calls
                )
            )
            self.assertTrue(
                all(
                    Path(call[-1]).parent == root
                    and Path(call[-1]).name.startswith(".windows_ocr_")
                    for call in calls
                )
            )
            self.assertEqual(list(root.glob(".windows_ocr_*.png")), [])

    def test_korean_engine_rejects_han_noise_but_keeps_ui_text(self) -> None:
        self.assertEqual(
            normalize_windows_ocr_text(
                "荐鞭覆捧胸刘漂 墓檬内 农 繁瘤\nE.V.E.L.Y.N 전송 버튼",
                language="ko",
            ),
            "E.V.E.L.Y.N 전송 버튼",
        )

    def test_korean_engine_drops_a_line_with_even_one_han_character(self) -> None:
        self.assertEqual(
            normalize_windows_ocr_text(
                "E.V.E.L.Y.N 버튼 亏",
                language="ko",
            ),
            "",
        )

    def test_fragment_only_line_is_not_evidence(self) -> None:
        self.assertEqual(
            normalize_windows_ocr_text(
                "조 코 크 지 1 2 3",
                language="ko",
            ),
            "",
        )

    def test_image_outside_bridge_root_is_rejected_before_process_start(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            screenshot_root = root / "screens"
            screenshot_root.mkdir()
            outside = root / "outside.png"
            Image.new("RGB", (10, 10), color="white").save(outside)
            runtime = WindowsNativeOcr(
                screenshot_root=screenshot_root,
                run_process=lambda *_args, **_kwargs: self.fail(
                    "process must not start"
                ),
            )

            with self.assertRaisesRegex(
                RuntimeError,
                "outside_bridge_root",
            ):
                runtime.recognize_sync(outside)

    def test_powershell_contract_has_no_command_or_user_text_parameter(self) -> None:
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
            / "invoke_windows_ocr.ps1"
        ).read_text(encoding="utf-8")

        self.assertIn("[string]$ImagePath", source)
        self.assertNotIn("$Command", source)
        self.assertNotIn("$UserText", source)
        self.assertIn("windows_ocr_image_type_not_allowed", source)


if __name__ == "__main__":
    unittest.main()
