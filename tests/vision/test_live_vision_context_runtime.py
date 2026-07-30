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
    VISION_EVIDENCE_SCHEMA,
    VisionEvidence,
    build_live_vision_context_from_runtime,
    vision_evidence_from_metrics,
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
        self.quality = {
            "confidence": "normal",
            "actionable": True,
            "scene_unreliable": False,
            "ocr_corrupt": False,
            "no_usable_evidence": False,
        }
        self.local_ocr_provider = None
        self.local_window_provider = None

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
            build_vision_quality=lambda _data: dict(self.quality),
            clean_text=lambda text: str(text).strip(),
            monotonic=lambda: next(self.times),
            local_ocr_provider=self.local_ocr_provider,
            local_window_provider=self.local_window_provider,
        )

    async def _get_session(self):
        return self.session

    async def test_disabled_returns_before_capture(self) -> None:
        metrics: dict = {}
        result = await build_live_vision_context_from_runtime(
            "화면",
            deps=self.build_deps(enabled=False),
            metrics=metrics,
        )

        self.assertIn("automatic capture is disabled", result)
        self.assertEqual(self.capture_calls, 0)
        self.assertEqual(
            metrics["meta"]["vision_evidence"],
            {
                "schema": VISION_EVIDENCE_SCHEMA,
                "state": "unavailable",
                "reason_code": "auto_capture_disabled",
                "evidence_available": False,
                "scene_available": False,
                "ocr_available": False,
                "confidence": "none",
                "actionable": False,
                "freshness": "unknown",
            },
        )

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
        self.assertEqual(metrics["meta"]["vision_evidence"]["state"], "failed")
        self.assertEqual(metrics["meta"]["vision_evidence"]["reason_code"], "black_frame")
        self.assertFalse(metrics["meta"]["vision_evidence"]["evidence_available"])

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
        self.assertEqual(metrics["meta"]["vision_evidence"]["state"], "failed")
        self.assertEqual(metrics["meta"]["vision_evidence"]["reason_code"], "analysis_failed")

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
        self.assertEqual(metrics["meta"]["vision_quality"]["confidence"], "normal")
        evidence = vision_evidence_from_metrics(metrics)
        self.assertEqual(evidence.state, "observed")
        self.assertTrue(evidence.evidence_available)
        self.assertTrue(evidence.scene_available)
        self.assertTrue(evidence.ocr_available)
        self.assertTrue(evidence.actionable)
        self.assertEqual(evidence.freshness, "live")
        self.assertTrue(evidence.satisfies_tool("vision_capture_or_watch"))
        self.assertTrue(evidence.satisfies_tool("vision_ocr"))

    async def test_scene_only_request_does_not_lazy_load_ocr(self) -> None:
        self.response = FakeResponse(data={"scene": "화면", "ocr": ""})
        self.session = FakeSession(self.response)
        metrics: dict = {}

        await build_live_vision_context_from_runtime(
            "화면을 봐줘",
            deps=self.build_deps(),
            metrics=metrics,
            run_ocr=False,
        )

        request = self.session.calls[0][1]
        self.assertFalse(request["json"]["run_ocr"])
        evidence = vision_evidence_from_metrics(metrics)
        self.assertTrue(evidence.scene_available)
        self.assertFalse(evidence.ocr_available)
        self.assertTrue(evidence.satisfies_tool("vision_capture_or_watch"))
        self.assertFalse(evidence.satisfies_tool("vision_ocr"))

    async def test_windows_native_ocr_satisfies_text_gate_without_docker_ocr(self) -> None:
        async def native_ocr(_path: Path) -> dict[str, object]:
            return {
                "schema": "windows_ocr.observation.v1",
                "attempted": True,
                "text": "E.V.E.L.Y.N 전송",
            }

        self.local_ocr_provider = native_ocr
        self.response = FakeResponse(data={"scene": "Evelyn.", "ocr": ""})
        self.session = FakeSession(self.response)
        metrics: dict = {}

        await build_live_vision_context_from_runtime(
            "화면 제목과 버튼을 읽어줘",
            deps=self.build_deps(),
            metrics=metrics,
            run_ocr=True,
        )

        request = self.session.calls[0][1]
        self.assertFalse(request["json"]["run_ocr"])
        self.assertEqual(metrics["meta"]["vision_ocr_source"], "windows_native")
        self.assertEqual(metrics["meta"]["vision_ocr_chars"], len("E.V.E.L.Y.N 전송"))
        self.assertTrue(vision_evidence_from_metrics(metrics).ocr_available)

    async def test_empty_native_ocr_does_not_trigger_hallucinatory_model_fallback(self) -> None:
        async def native_ocr(_path: Path) -> dict[str, object]:
            return {
                "schema": "windows_ocr.observation.v1",
                "attempted": True,
                "text": "",
            }

        self.local_ocr_provider = native_ocr
        self.response = FakeResponse(data={"scene": "게임 화면", "ocr": "fabricated"})
        self.session = FakeSession(self.response)
        metrics: dict = {}

        await build_live_vision_context_from_runtime(
            "화면 글자를 읽어줘",
            deps=self.build_deps(),
            metrics=metrics,
            run_ocr=True,
        )

        request = self.session.calls[0][1]
        self.assertFalse(request["json"]["run_ocr"])
        self.assertEqual(metrics["meta"]["vision_ocr_chars"], 0)
        self.assertFalse(vision_evidence_from_metrics(metrics).ocr_available)

    async def test_foreground_window_is_strong_scene_provenance(self) -> None:
        async def foreground_window() -> dict[str, object]:
            return {
                "schema": "windows_foreground.observation.v1",
                "available": True,
                "title": "Minecraft 26.2 - 싱글플레이",
                "className": "GLFW30",
            }

        self.local_window_provider = foreground_window
        self.response = FakeResponse(data={"scene": "Evelyn.", "ocr": ""})
        self.session = FakeSession(self.response)
        self.quality = {
            "confidence": "low",
            "actionable": False,
            "scene_unreliable": True,
            "ocr_corrupt": False,
            "no_usable_evidence": False,
        }
        metrics: dict = {}

        await build_live_vision_context_from_runtime(
            "무슨 앱이 보여?",
            deps=self.build_deps(),
            metrics=metrics,
            run_ocr=False,
        )

        evidence = vision_evidence_from_metrics(metrics)
        self.assertTrue(metrics["meta"]["vision_foreground_available"])
        self.assertTrue(evidence.scene_available)
        self.assertTrue(evidence.satisfies_tool("vision_capture_or_watch"))

    async def test_scene_that_only_echoes_request_is_not_visual_evidence(self) -> None:
        self.response = FakeResponse(
            data={
                "scene": "현재 화면의 앱과 가장 큰 제목을 근거만으로 설명해줘. 앱을 설명하는 화면입니다.",
                "ocr": "",
            }
        )
        self.session = FakeSession(self.response)
        metrics: dict = {}

        await build_live_vision_context_from_runtime(
            "현재 화면의 앱과 가장 큰 제목을 근거만으로 설명해줘",
            deps=self.build_deps(),
            metrics=metrics,
            run_ocr=False,
        )

        evidence = vision_evidence_from_metrics(metrics)
        self.assertEqual(evidence.state, "unreliable")
        self.assertFalse(evidence.evidence_available)
        self.assertFalse(evidence.scene_available)
        self.assertTrue(metrics["meta"]["vision_quality"]["scene_request_echo"])

    async def test_success_without_usable_scene_or_ocr_is_not_evidence(self) -> None:
        self.response = FakeResponse(data={"scene": "", "ocr": "���"})
        self.session = FakeSession(self.response)
        self.quality = {
            "confidence": "none",
            "actionable": False,
            "scene_unreliable": False,
            "ocr_corrupt": True,
            "no_usable_evidence": True,
        }
        metrics: dict = {}

        await build_live_vision_context_from_runtime(
            "읽어줘",
            deps=self.build_deps(),
            metrics=metrics,
        )

        evidence = vision_evidence_from_metrics(metrics)
        self.assertEqual(evidence.state, "unreliable")
        self.assertEqual(evidence.reason_code, "no_usable_visual_evidence")
        self.assertFalse(evidence.evidence_available)
        self.assertFalse(evidence.satisfies_tool("vision_capture_or_watch"))
        self.assertFalse(evidence.satisfies_tool("vision_ocr"))

    def test_ocr_tool_requires_usable_ocr_not_only_a_scene(self) -> None:
        evidence = VisionEvidence(
            state="observed",
            reason_code="live_observation",
            evidence_available=True,
            scene_available=True,
            ocr_available=False,
            confidence="low",
            freshness="live",
        )

        self.assertTrue(evidence.satisfies_tool("vision_capture_or_watch"))
        self.assertFalse(evidence.satisfies_tool("vision_ocr"))

    def test_unscored_ocr_is_supporting_context_not_required_tool_evidence(self) -> None:
        evidence = VisionEvidence(
            state="observed",
            reason_code="live_observation",
            evidence_available=True,
            scene_available=True,
            ocr_available=True,
            confidence="low",
            actionable=False,
            freshness="live",
        )

        self.assertTrue(evidence.satisfies_tool("vision_capture_or_watch"))
        self.assertFalse(evidence.satisfies_tool("vision_ocr"))

    def test_missing_or_invalid_metrics_fail_closed(self) -> None:
        self.assertEqual(vision_evidence_from_metrics(None).state, "unknown")
        self.assertFalse(vision_evidence_from_metrics({}).evidence_available)
        invalid = {"meta": {"vision_evidence": {"schema": "other", "state": "observed"}}}
        self.assertFalse(vision_evidence_from_metrics(invalid).evidence_available)
        contradictory = {
            "meta": {
                "vision_evidence": {
                    "schema": VISION_EVIDENCE_SCHEMA,
                    "state": "observed",
                    "evidence_available": True,
                    "scene_available": False,
                    "ocr_available": False,
                    "actionable": True,
                }
            }
        }
        evidence = vision_evidence_from_metrics(contradictory)
        self.assertEqual(evidence.state, "unknown")
        self.assertEqual(evidence.reason_code, "invalid_evidence_contract")
        self.assertFalse(evidence.evidence_available)
        self.assertFalse(evidence.actionable)

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
