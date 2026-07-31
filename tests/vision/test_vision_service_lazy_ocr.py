from __future__ import annotations

import unittest
from pathlib import Path


REPO_ROOT = next(path for path in Path(__file__).resolve().parents if (path / "main.py").exists())
VISION_SERVICE = REPO_ROOT / "evelyn_core" / "runtime" / "evelyn_core" / "vision_service.py"
START_VISION = REPO_ROOT / "evelyn_core" / "runtime" / "launchers" / "start_vision.ps1"
START_ENV = REPO_ROOT / "evelyn_core" / "start_env.bat"
VISION_DOCKERFILE = REPO_ROOT / "docker" / "Dockerfile.vision"
VISION_INGRESS_DOCKERFILE = (
    REPO_ROOT / "docker" / "Dockerfile.vision-ingress"
)
DOCKERIGNORE = REPO_ROOT / ".dockerignore"


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
        self.assertIn("def _falcon_ocr_file(", source)
        self.assertIn("from huggingface_hub import hf_hub_download", source)
        self.assertIn("def verify_falcon_ocr_snapshot()", source)
        self.assertIn("verify_remote_model_snapshot(", source)
        self.assertIn("verify_falcon_ocr_snapshot()", source)
        self.assertIn("revision=OCR_MODEL_REVISION", source)
        self.assertIn("local_files_only=VISION_OCR_LOCAL_FILES_ONLY", source)
        self.assertIn(
            'getattr(model.config, "_commit_hash", None) or ""',
            source,
        )
        self.assertIn('"supplyChain": ocr_supply_chain_status()', source)
        load_start = source.index("def load_falcon_ocr_model")
        self.assertLess(
            source.index("verify_falcon_ocr_snapshot()", load_start),
            source.index("AutoModelForCausalLM.from_pretrained", load_start),
        )
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

    def test_vision_image_includes_falcon_remote_code_runtime_dependency(self) -> None:
        dockerfile = VISION_DOCKERFILE.read_text(encoding="utf-8")

        self.assertIn("RUN pip install requests==2.34.2", dockerfile)
        self.assertIn("python3-dev", dockerfile)
        self.assertIn("COPY docker/falcon_ocr_snapshot.lock.json", dockerfile)
        self.assertIn("COPY tools/provision_falcon_ocr_snapshot.py", dockerfile)
        self.assertIn("USER 10001:10001", dockerfile)
        dockerignore = DOCKERIGNORE.read_text(encoding="utf-8")
        self.assertIn("!tools/provision_falcon_ocr_snapshot.py", dockerignore)

        ingress = VISION_INGRESS_DOCKERFILE.read_text(encoding="utf-8")
        self.assertIn("python:3.11-slim@sha256:", ingress)
        self.assertIn("COPY evelyn_core/runtime/evelyn_core/vision_ingress_proxy.py", ingress)
        self.assertIn("USER 65534:65534", ingress)
        self.assertNotIn("pip install", ingress)

    def test_start_vision_passes_lazy_ocr_env_to_wsl_and_windows(self) -> None:
        source = START_VISION.read_text(encoding="utf-8")

        self.assertIn("$visionOcrLazyLoad", source)
        self.assertIn("export VISION_OCR_REVISION='$visionOcrRevision'", source)
        self.assertIn(
            "export VISION_OCR_LOCAL_FILES_ONLY='$visionOcrLocalFilesOnly'",
            source,
        )
        self.assertIn("export VISION_OCR_LAZY_LOAD='$visionOcrLazyLoad'", source)
        self.assertIn("export VISION_OCR_IDLE_UNLOAD_SEC='$visionOcrIdleUnloadSec'", source)
        self.assertIn("export VISION_OCR_UNLOAD_AFTER_REQUEST='$visionOcrUnloadAfterRequest'", source)
        self.assertIn("$env:VISION_OCR_LAZY_LOAD = $visionOcrLazyLoad", source)
        self.assertIn("$env:VISION_OCR_IDLE_UNLOAD_SEC = $visionOcrIdleUnloadSec", source)
        self.assertIn("$env:VISION_OCR_UNLOAD_AFTER_REQUEST = $visionOcrUnloadAfterRequest", source)
        self.assertIn("$env:VISION_OCR_REVISION = $visionOcrRevision", source)
        self.assertIn("$env:VISION_OCR_LOCAL_FILES_ONLY = $visionOcrLocalFilesOnly", source)

    def test_start_env_declares_lazy_ocr_defaults(self) -> None:
        source = START_ENV.read_text(encoding="utf-8")

        self.assertIn('if "%VISION_OCR_LAZY_LOAD%"=="" set "VISION_OCR_LAZY_LOAD=false"', source)
        self.assertIn(
            'if "%VISION_OCR_REVISION%"=="" set "VISION_OCR_REVISION=42ec56b72a23984ac059e7c8a6d397a8529423fe"',
            source,
        )
        self.assertIn(
            'if "%VISION_OCR_LOCAL_FILES_ONLY%"=="" set "VISION_OCR_LOCAL_FILES_ONLY=true"',
            source,
        )
        self.assertIn('if "%VISION_OCR_IDLE_UNLOAD_SEC%"=="" set "VISION_OCR_IDLE_UNLOAD_SEC=600"', source)
        self.assertIn(
            'if "%VISION_OCR_UNLOAD_AFTER_REQUEST%"=="" set "VISION_OCR_UNLOAD_AFTER_REQUEST=false"',
            source,
        )


if __name__ == "__main__":
    unittest.main()
