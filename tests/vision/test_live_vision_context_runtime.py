from __future__ import annotations

import sys
import unittest
from pathlib import Path


REPO_ROOT = next(path for path in Path(__file__).resolve().parents if (path / "main.py").exists())
RUNTIME_ROOT = REPO_ROOT / "evelyn_core" / "runtime"
if str(RUNTIME_ROOT) not in sys.path:
    sys.path.insert(0, str(RUNTIME_ROOT))

from evelyn_core.vision_runtime import (  # noqa: E402
    LiveVisionContextRuntimeDeps,
    build_live_vision_context_from_runtime,
)


class FakeResponse:
    def __init__(self, *, status: int = 200, data=None, text: str = "") -> None:
        self.status = status
        self.data = data or {}
        self.text_value = text

    async def __aenter__(self):
        return self

    async def __aexit__(self, exc_type, exc, traceback) -> None:
        return None

    async def json(self):
        return self.data

    async def text(self) -> str:
        return self.text_value


class FakeSession:
    def __init__(self, response: FakeResponse) -> None:
        self.response = response
        self.calls: list[tuple[str, dict]] = []

    def post(self, url: str, **kwargs):
        self.calls.append((url, kwargs))
        return self.response


class LiveVisionContextRuntimeTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self) -> None:
        self.capture_calls = 0
        self.capture_error: Exception | None = None
        self.deleted = True
        self.delete_calls: list[Path] = []
        self.response = FakeResponse(data={"scene": "화면", "ocr": "문자"})
        self.session = FakeSession(self.response)
        self.times = iter([10.0, 10.25])

    async def capture(self):
        self.capture_calls += 1
        if self.capture_error is not None:
            raise self.capture_error
        return Path("capture.png"), (1920, 1080)

    def delete(self, path: Path) -> bool:
        self.delete_calls.append(path)
        return self.deleted

    def build_deps(self, *, enabled: bool = True) -> LiveVisionContextRuntimeDeps:
        return LiveVisionContextRuntimeDeps(
            auto_capture_enabled=enabled,
            analyze_timeout_sec=120.0,
            service_url="http://vision/",
            capture_local_screen=self.capture,
            build_observation_prompt=lambda text: f"prompt:{text}",
            get_http_session=lambda: self._get_session(),
            client_timeout_factory=lambda **kwargs: kwargs,
            delete_request_image=self.delete,
            format_observation=lambda **kwargs: f"observation:{kwargs['image_deleted']}",
            build_vision_quality=lambda _data: {"confidence": "high", "actionable": True},
            clean_text=lambda text: str(text).strip(),
            monotonic=lambda: next(self.times),
        )

    async def _get_session(self):
        return self.session

    async def test_disabled_returns_before_capture(self) -> None:
        result = await build_live_vision_context_from_runtime(
            "화면",
            deps=self.build_deps(enabled=False),
        )

        self.assertIn("automatic capture is disabled", result)
        self.assertEqual(self.capture_calls, 0)

    async def test_black_frame_capture_failure_records_error_and_forbids_claim(self) -> None:
        self.capture_error = RuntimeError("black frame detected")
        metrics: dict = {}

        result = await build_live_vision_context_from_runtime(
            "화면",
            deps=self.build_deps(),
            metrics=metrics,
        )

        self.assertIn("black frame", result)
        self.assertIn("Do not claim", result)
        self.assertIn("black frame detected", metrics["meta"]["vision_capture_error"])

    async def test_analysis_failure_deletes_capture_and_records_metrics(self) -> None:
        self.response = FakeResponse(status=503, text="offline")
        self.session = FakeSession(self.response)
        metrics: dict = {}

        result = await build_live_vision_context_from_runtime(
            "무슨 화면이야",
            deps=self.build_deps(),
            metrics=metrics,
        )

        self.assertIn("discarded after vision analysis failed", result)
        self.assertEqual(self.delete_calls, [Path("capture.png")])
        self.assertEqual(metrics["meta"]["vision_capture_path"], "")
        self.assertTrue(metrics["meta"]["vision_capture_deleted"])

    async def test_success_formats_observation_and_records_quality_metrics(self) -> None:
        metrics: dict = {}

        result = await build_live_vision_context_from_runtime(
            "읽어줘",
            deps=self.build_deps(),
            metrics=metrics,
        )

        self.assertEqual(result, "observation:True")
        url, request = self.session.calls[0]
        self.assertEqual(url, "http://vision/v1/vision/analyze")
        self.assertEqual(request["json"]["prompt"], "prompt:읽어줘")
        self.assertEqual(request["json"]["max_new_tokens"], 128)
        self.assertEqual(metrics["marks"]["vision_ready"], 250.0)
        self.assertEqual(metrics["meta"]["vision_ocr_chars"], 2)
        self.assertEqual(metrics["meta"]["vision_scene_chars"], 2)
        self.assertEqual(metrics["meta"]["vision_quality"]["confidence"], "high")

    def test_main_delegates_live_vision_context_to_runtime_module(self) -> None:
        source = (
            REPO_ROOT / "evelyn_core" / "runtime" / "evelyn_core" / "vision_request_composition.py"
        ).read_text(encoding="utf-8")
        start = source.index("async def build_live_vision_context(")
        end = source.index("def build_vision_watch_prompt", start)
        function_source = source[start:end]

        self.assertIn("build_live_vision_context_from_runtime(", function_source)
        self.assertNotIn("session.post(", function_source)
        self.assertNotIn("capture_local_screen()", function_source)


if __name__ == "__main__":
    unittest.main()
