from __future__ import annotations

import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import AsyncMock, Mock, patch

from PIL import Image

REPO_ROOT = next(p for p in Path(__file__).resolve().parents if (p / "main.py").exists())
RUNTIME_ROOT = REPO_ROOT / "evelyn_core" / "runtime"
if str(RUNTIME_ROOT) not in sys.path:
    sys.path.insert(0, str(RUNTIME_ROOT))

from evelyn_core.vision_request_composition import (
    VisionRequestComposition,
    VisionRequestCompositionDeps,
)


class VisionRequestCompositionTests(unittest.IsolatedAsyncioTestCase):
    def build(self, screenshot_dir: Path, **overrides):
        values = dict(
            screenshot_dir=screenshot_dir,
            capture_all_screens=False,
            delete_request_images=True,
            auto_capture_enabled=False,
            analyze_timeout_sec=10.0,
            service_url="http://vision",
            build_vision_quality=Mock(return_value={"confidence": "high"}),
            vision_watch_scene_is_unreliable=Mock(return_value=False),
            get_http_session=AsyncMock(),
            client_timeout_factory=Mock(),
            clean_text=lambda value: str(value).strip(),
            to_thread=AsyncMock(),
            monotonic=Mock(return_value=1.0),
        )
        values.update(overrides)
        return VisionRequestComposition(VisionRequestCompositionDeps(**values))

    def test_delete_request_image_is_confined_to_screenshot_directory(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            screenshot_dir = root / "screens"
            screenshot_dir.mkdir()
            inside = screenshot_dir / "inside.png"
            outside = root / "outside.png"
            inside.write_bytes(b"inside")
            outside.write_bytes(b"outside")
            composition = self.build(screenshot_dir)

            self.assertTrue(composition.delete_request_vision_image(inside))
            self.assertFalse(composition.delete_request_vision_image(outside))
            self.assertTrue(outside.exists())

    async def test_disabled_auto_capture_returns_empty_context(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            composition = self.build(Path(temp_dir), auto_capture_enabled=False)
            self.assertIn(
                "automatic capture is disabled",
                await composition.build_live_vision_context("hello"),
            )

    def test_black_frame_is_deleted_before_capture_failure_escapes(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            screenshot_dir = Path(temp_dir)
            composition = self.build(screenshot_dir)
            black = Image.new("RGB", (8, 8), color=(0, 0, 0))

            with patch("PIL.ImageGrab.grab", return_value=black):
                with self.assertRaisesRegex(RuntimeError, "black frame"):
                    composition.capture_local_screen_sync()

            self.assertEqual(list(screenshot_dir.glob("*.png")), [])

    def test_main_uses_explicit_bindings(self) -> None:
        source = (REPO_ROOT / "main.py").read_text(encoding="utf-8")
        self.assertIn("vision_request_composition = VisionRequestComposition(", source)
        self.assertIn(
            "build_live_vision_context = vision_request_composition.build_live_vision_context",
            source,
        )
        self.assertIn(
            "delete_request_vision_image = vision_request_composition.delete_request_vision_image",
            source,
        )


if __name__ == "__main__":
    unittest.main()
