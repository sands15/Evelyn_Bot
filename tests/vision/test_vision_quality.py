from __future__ import annotations

import sys
import unittest
from pathlib import Path


REPO_ROOT = next(path for path in Path(__file__).resolve().parents if (path / "main.py").exists())
RUNTIME_ROOT = REPO_ROOT / "evelyn_core" / "runtime"
if str(RUNTIME_ROOT) not in sys.path:
    sys.path.insert(0, str(RUNTIME_ROOT))

from evelyn_core.vision_quality import build_vision_quality, vision_text_looks_corrupt  # noqa: E402


class VisionQualityTests(unittest.TestCase):
    def test_repeated_scene_and_corrupt_ocr_are_not_usable_evidence(self) -> None:
        quality = build_vision_quality(
            {
                "scene": "아이콘 아이콘 아이콘 아이콘 아이콘 아이콘 아이콘 아이콘",
                "ocr": "\ub0b4 \ud654\uba74 bounds \u0b39\u0b47\u0b2c \ubd84\uc11d \uc694\uccad\u88c5 \uc774\uc0c1\ud55canimer",
            }
        )

        self.assertTrue(quality["scene_unreliable"])
        self.assertTrue(quality["ocr_corrupt"])
        self.assertTrue(quality["no_usable_evidence"])
        self.assertEqual(quality["confidence"], "none")
        self.assertFalse(quality["actionable"])
        self.assertIn("unusable", quality["guidance"])

    def test_normal_screen_summary_is_usable(self) -> None:
        quality = build_vision_quality(
            {
                "scene": "브라우저에서 OpenClaw 채팅 화면이 열려 있고 왼쪽에는 세션 목록이 보인다.",
                "ocr": "Main Session Eve Message Eve",
            }
        )

        self.assertFalse(quality["scene_unreliable"])
        self.assertFalse(quality["ocr_corrupt"])
        self.assertFalse(quality["no_usable_evidence"])
        self.assertEqual(quality["confidence"], "normal")
        self.assertTrue(quality["actionable"])

    def test_weak_evidence_is_context_only_not_actionable(self) -> None:
        quality = build_vision_quality(
            {
                "scene": "아이콘 아이콘 아이콘 아이콘 아이콘 아이콘",
                "ocr": "Settings General Display",
            }
        )

        self.assertTrue(quality["scene_unreliable"])
        self.assertFalse(quality["ocr_corrupt"])
        self.assertFalse(quality["no_usable_evidence"])
        self.assertTrue(quality["weak"])
        self.assertEqual(quality["confidence"], "low")
        self.assertFalse(quality["actionable"])
        self.assertIn("sole basis for actions", quality["guidance"])

    def test_replacement_character_marks_ocr_corrupt(self) -> None:
        self.assertTrue(vision_text_looks_corrupt("OpenClaw Ign�집random"))

    def test_unexpected_script_marks_ocr_corrupt(self) -> None:
        self.assertTrue(
            vision_text_looks_corrupt(
                "\ub0b4 \ud654\uba74 bounds \u0b39\u0b47\u0b2c \ubd84\uc11d \uc694\uccad\u88c5 \uc774\uc0c1\ud55canimer"
            )
        )


if __name__ == "__main__":
    unittest.main()
