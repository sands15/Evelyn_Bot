from __future__ import annotations

import asyncio
import sys
import unittest
from pathlib import Path
from unittest.mock import AsyncMock, Mock

REPO_ROOT = next(p for p in Path(__file__).resolve().parents if (p / "main.py").exists())
RUNTIME_ROOT = REPO_ROOT / "evelyn_core" / "runtime"
if str(RUNTIME_ROOT) not in sys.path:
    sys.path.insert(0, str(RUNTIME_ROOT))

from evelyn_core.vision_watch_composition import VisionWatchComposition, VisionWatchCompositionDeps


class VisionWatchCompositionTests(unittest.IsolatedAsyncioTestCase):
    def build(self, **overrides):
        values = dict(
            enabled=True, interval_sec=1.0, thumbnail_size=640, max_image_dim=1280,
            diff_threshold=0.2, capture_all_screens=False, analyze_cooldown_sec=60.0,
            run_ocr=True, ocr_interval_sec=120.0, analyze_timeout_sec=10.0,
            vision_service_url="http://vision", capture_frame=Mock(return_value={"capture_black": True}),
            scene_looks_bad=Mock(return_value=False), build_prompt=Mock(return_value="prompt"),
            get_http_session=AsyncMock(), client_timeout_factory=Mock(), update_analysis=Mock(),
            mark_startup_component=Mock(), to_thread=AsyncMock(), sleep=AsyncMock(),
            create_task=Mock(), now=Mock(return_value=1000.0), log=Mock(),
        )
        values.update(overrides)
        deps = VisionWatchCompositionDeps(**values)
        return VisionWatchComposition(deps), deps

    async def test_black_capture_returns_without_analysis(self):
        async def to_thread(callback, **kwargs):
            return callback(**kwargs)
        composition, deps = self.build(to_thread=to_thread)
        result = await composition.run_vision_watch_once()
        self.assertTrue(result["capture_black"])
        deps.get_http_session.assert_not_awaited()

    def test_start_is_single_flight_and_stop_cancels(self):
        task = Mock(); task.done.return_value = False
        composition, deps = self.build(create_task=Mock(return_value=task))
        composition.vision_watch_loop = Mock(return_value=object())
        composition.ensure_vision_watch_started(); composition.ensure_vision_watch_started()
        deps.create_task.assert_called_once()
        composition.stop_vision_watch_task()
        task.cancel.assert_called_once_with()
        self.assertIsNone(composition.task)

    def test_disabled_start_does_nothing(self):
        composition, deps = self.build(enabled=False)
        composition.ensure_vision_watch_started()
        deps.create_task.assert_not_called()

    def test_main_uses_explicit_bindings(self):
        source = (REPO_ROOT / "main.py").read_text(encoding="utf-8")
        self.assertIn("vision_watch_composition = VisionWatchComposition(", source)
        self.assertIn("run_vision_watch_once = vision_watch_composition.run_vision_watch_once", source)
        self.assertNotIn("vision_watch_task: Optional[asyncio.Task]", source)


if __name__ == "__main__":
    unittest.main()
