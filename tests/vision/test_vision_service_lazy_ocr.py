from __future__ import annotations

import unittest
from pathlib import Path


REPO_ROOT = next(path for path in Path(__file__).resolve().parents if (path / "main.py").exists())
VISION_SERVICE = REPO_ROOT / "evelyn_core" / "runtime" / "evelyn_core" / "vision_service.py"
START_VISION = REPO_ROOT / "evelyn_core" / "runtime" / "launchers" / "start_vision.ps1"
START_ENV = REPO_ROOT / "evelyn_core" / "start_env.bat"


class VisionServiceLazyOcrTests(unittest.TestCase):
    def test_vision_service_supports_lazy_ocr_load_and_unload(self) -> None:
        source = VISION_SERVICE.read_text(encoding="utf-8")

        self.assertIn("VISION_OCR_LAZY_LOAD", source)
        self.assertIn("VISION_OCR_IDLE_UNLOAD_SEC", source)
        self.assertIn("VISION_OCR_UNLOAD_AFTER_REQUEST", source)
        self.assertIn("def ensure_ocr_loaded()", source)
        self.assertIn("def unload_ocr(", source)
        self.assertIn('"/v1/vision/ocr/unload"', source)
        self.assertIn("def unload_ocr_endpoint()", source)
        self.assertIn("def start_ocr_idle_reaper()", source)
        self.assertIn("ensure_ocr_loaded()", source)
        self.assertIn("cleanup_ocr_after_request()", source)
        self.assertIn('"lazyLoad": VISION_OCR_LAZY_LOAD', source)
        self.assertIn('"lastUsedAt": _ocr_last_used_at', source)
        self.assertIn("EVELYN_HOST_PROJECT_ROOT", source)
        self.assertIn("EVELYN_CONTAINER_PROJECT_ROOT", source)
        self.assertIn("def map_host_project_path(", source)
        self.assertIn("map_host_project_path(image_path)", source)
        self.assertIn("from .vision_quality import build_vision_quality", source)
        self.assertIn('result["quality"] = build_vision_quality(result)', source)

    def test_vision_service_exposes_safe_configuration_and_error_counters(self) -> None:
        source = VISION_SERVICE.read_text(encoding="utf-8")

        self.assertIn("load_runtime_settings", source)
        self.assertIn("RuntimeErrorCounter", source)
        self.assertIn('"configuration": _VISION_CONFIG.public_summary()', source)
        self.assertIn('_RUNTIME_ERRORS.record("vision_model_load_failed"', source)
        self.assertIn('_RUNTIME_ERRORS.record("vision_describe_failed"', source)
        self.assertIn('_RUNTIME_ERRORS.record("vision_analyze_failed"', source)
        self.assertGreaterEqual(
            source.count('_RUNTIME_ERRORS.record("vision_ocr_generation_failed"'),
            2,
        )
        self.assertIn('detail="vision_ocr_generation_failed"', source)
        self.assertIn('result["ocr_error"] = "vision_ocr_generation_failed"', source)
        self.assertNotIn("Falcon-OCR generation failed: {exc}", source)

    def test_start_vision_passes_lazy_ocr_env_to_wsl_and_windows(self) -> None:
        source = START_VISION.read_text(encoding="utf-8")

        self.assertIn("$visionOcrLazyLoad", source)
        self.assertIn("export VISION_OCR_LAZY_LOAD='$visionOcrLazyLoad'", source)
        self.assertIn("export VISION_OCR_IDLE_UNLOAD_SEC='$visionOcrIdleUnloadSec'", source)
        self.assertIn("export VISION_OCR_UNLOAD_AFTER_REQUEST='$visionOcrUnloadAfterRequest'", source)
        self.assertIn("$env:VISION_OCR_LAZY_LOAD = $visionOcrLazyLoad", source)
        self.assertIn("$env:VISION_OCR_IDLE_UNLOAD_SEC = $visionOcrIdleUnloadSec", source)
        self.assertIn("$env:VISION_OCR_UNLOAD_AFTER_REQUEST = $visionOcrUnloadAfterRequest", source)

    def test_start_env_declares_lazy_ocr_defaults(self) -> None:
        source = START_ENV.read_text(encoding="utf-8")

        self.assertIn('if "%VISION_OCR_LAZY_LOAD%"=="" set "VISION_OCR_LAZY_LOAD=false"', source)
        self.assertIn('if "%VISION_OCR_IDLE_UNLOAD_SEC%"=="" set "VISION_OCR_IDLE_UNLOAD_SEC=600"', source)
        self.assertIn(
            'if "%VISION_OCR_UNLOAD_AFTER_REQUEST%"=="" set "VISION_OCR_UNLOAD_AFTER_REQUEST=false"',
            source,
        )


if __name__ == "__main__":
    unittest.main()
