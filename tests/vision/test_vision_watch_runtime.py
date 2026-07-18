from __future__ import annotations

import sys
import unittest
from pathlib import Path


REPO_ROOT = next(path for path in Path(__file__).resolve().parents if (path / "main.py").exists())
RUNTIME_ROOT = REPO_ROOT / "evelyn_core" / "runtime"
if str(RUNTIME_ROOT) not in sys.path:
    sys.path.insert(0, str(RUNTIME_ROOT))

from evelyn_core.vision_runtime import (  # noqa: E402
    VisionRuntimeDeps,
    build_vision_observation_prompt_from_runtime,
    build_vision_watch_prompt_from_runtime,
    format_vision_observation_from_runtime,
    vision_watch_scene_looks_bad_from_runtime,
)


def _normalize(text: str) -> str:
    return " ".join(text.split()).strip()


def _fake_quality(data: dict) -> dict:
    scene = str(data.get("scene") or "")
    ocr = str(data.get("ocr") or "")
    return {
        "scene_unreliable": not scene,
        "ocr_corrupt": False,
        "weak": False,
        "no_usable_evidence": False,
        "confidence": "normal",
        "actionable": bool(ocr.strip()),
    }


class VisionWatchRuntimeTests(unittest.TestCase):
    def setUp(self) -> None:
        self.deps = VisionRuntimeDeps(
            clean_text=_normalize,
            build_vision_quality=_fake_quality,
            vision_watch_scene_is_unreliable=lambda text: text.endswith("unreliable"),
        )

    def test_build_vision_observation_prompt(self) -> None:
        prompt = build_vision_observation_prompt_from_runtime("  화면에 뭐가 보이냐?  ", deps=self.deps)
        self.assertIn("User request: 화면에 뭐가 보이냐?", prompt)
        self.assertLessEqual(len(prompt), 280)

    def test_build_vision_watch_prompt(self) -> None:
        self.assertIn("lightweight background screen observer", build_vision_watch_prompt_from_runtime())

    def test_format_vision_observation(self) -> None:
        text = format_vision_observation_from_runtime(
            image_path=Path("sample.png"),
            image_size=(1920, 1080),
            data={"scene": "브라우저 화면", "ocr": "OpenClaw 테스트", "ocr_error": ""},
            deps=self.deps,
        )
        self.assertIn("captured_image=sample.png", text)
        self.assertIn("scene: 브라우저 화면", text)
        self.assertIn("ocr_text: OpenClaw 테스트", text)

    def test_vision_watch_scene_looks_bad(self) -> None:
        self.assertTrue(vision_watch_scene_looks_bad_from_runtime("   ", deps=self.deps))
        self.assertTrue(vision_watch_scene_looks_bad_from_runtime("123456789012345678901234567890", deps=self.deps))
        self.assertTrue(vision_watch_scene_looks_bad_from_runtime("reliable unreliable", deps=self.deps))
        self.assertFalse(vision_watch_scene_looks_bad_from_runtime("마인크래프트 창이 열려 있음", deps=self.deps))


if __name__ == "__main__":
    unittest.main()
