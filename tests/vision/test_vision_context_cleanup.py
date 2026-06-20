from __future__ import annotations

import os
import sys
import tempfile
import time
import unittest
from pathlib import Path

from PIL import Image

REPO_ROOT = next(path for path in Path(__file__).resolve().parents if (path / "main.py").exists())
RUNTIME_ROOT = REPO_ROOT / "evelyn_core" / "runtime"
if str(RUNTIME_ROOT) not in sys.path:
    sys.path.insert(0, str(RUNTIME_ROOT))

from evelyn_core.vision_watch import (
    read_vision_watch_state,
    render_vision_watch_context,
    trim_vision_watch_dir,
    update_vision_watch_analysis,
    write_vision_watch_state,
)


MAIN_PY = REPO_ROOT / "main.py"
START_ENV = REPO_ROOT / "evelyn_core" / "start_env.bat"


class VisionContextCleanupTests(unittest.TestCase):
    def test_update_vision_watch_analysis_adds_ephemeral_ttls(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            state_path = Path(tmp) / "vision_watch_state.json"
            write_vision_watch_state({"captured_at": time.time()}, path=state_path)

            state = update_vision_watch_analysis(
                data={"scene": "화면에 설정 창이 보인다.", "ocr": "SECRET OCR"},
                run_ocr=True,
                state_path=state_path,
            )

            self.assertGreater(float(state.get("scene_expires_at") or 0.0), time.time())
            self.assertGreater(float(state.get("ocr_expires_at") or 0.0), time.time())
            self.assertEqual(read_vision_watch_state(state_path).get("ocr"), "SECRET OCR")

    def test_render_vision_watch_context_omits_expired_ocr_and_scene(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            state_path = Path(tmp) / "vision_watch_state.json"
            from evelyn_core import vision_watch

            original_path = vision_watch.VISION_WATCH_STATE_PATH
            try:
                vision_watch.VISION_WATCH_STATE_PATH = state_path
                write_vision_watch_state(
                    {
                        "captured_at": time.time(),
                        "changed": False,
                        "diff_score": 0.01,
                        "original_width": 100,
                        "original_height": 100,
                        "analysis_width": 100,
                        "analysis_height": 100,
                        "scene": "EXPIRED SCENE",
                        "ocr": "EXPIRED OCR",
                        "scene_expires_at": time.time() - 1,
                        "ocr_expires_at": time.time() - 1,
                    },
                    path=state_path,
                )

                rendered = render_vision_watch_context(max_age_sec=600)
            finally:
                vision_watch.VISION_WATCH_STATE_PATH = original_path

            self.assertNotIn("EXPIRED SCENE", rendered)
            self.assertNotIn("EXPIRED OCR", rendered)

    def test_trim_vision_watch_dir_limits_count_and_age(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            for index in range(6):
                path = root / f"watch_{index}.jpg"
                Image.new("RGB", (8, 8), color=(index, index, index)).save(path)
                old = time.time() - (3600 if index < 2 else index)
                os.utime(path, (old, old))

            cleanup = trim_vision_watch_dir(root, keep_files=3, max_age_sec=1800)
            retained = list(root.glob("watch_*"))

            self.assertGreaterEqual(cleanup["deleted"], 3)
            self.assertLessEqual(len(retained), 3)

    def test_main_contains_request_image_delete_and_memory_redaction_hooks(self) -> None:
        source = MAIN_PY.read_text(encoding="utf-8")
        memory_policy = (
            REPO_ROOT / "evelyn_core" / "runtime" / "evelyn_core" / "memory_update_policy.py"
        ).read_text(encoding="utf-8")

        self.assertIn("VISION_DELETE_REQUEST_IMAGES", source)
        self.assertIn("delete_request_vision_image", source)
        self.assertIn("redact_vision_text_for_memory", source)
        self.assertIn("VISION_MEMORY_WRITE_ENABLED", source)
        self.assertIn("def redact_vision_text_for_memory", memory_policy)
        self.assertIn("VISION_MEMORY_LINE_RE", memory_policy)
        self.assertIn("vision_confidence=", source)
        self.assertIn("vision_actionable=false", source)
        self.assertIn("vision_actionable=", source)
        self.assertIn('metrics.setdefault("meta", {})["vision_quality"] = dict(quality)', source)

    def test_start_env_declares_vision_cleanup_defaults(self) -> None:
        source = START_ENV.read_text(encoding="utf-8")

        self.assertIn('set "VISION_WATCH_KEEP_FILES=48"', source)
        self.assertIn('set "VISION_WATCH_MAX_FILE_AGE_SEC=1800"', source)
        self.assertIn('set "VISION_CONTEXT_SCENE_TTL_SEC=600"', source)
        self.assertIn('set "VISION_CONTEXT_OCR_TTL_SEC=180"', source)
        self.assertIn('set "VISION_DELETE_REQUEST_IMAGES=true"', source)
        self.assertIn('set "VISION_MEMORY_WRITE_ENABLED=false"', source)


if __name__ == "__main__":
    unittest.main()
