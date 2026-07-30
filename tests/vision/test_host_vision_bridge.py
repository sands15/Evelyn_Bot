from __future__ import annotations

import json
import os
from pathlib import Path
import sys
import tempfile
import unittest


REPO_ROOT = next(path for path in Path(__file__).resolve().parents if (path / "main.py").exists())
RUNTIME_ROOT = REPO_ROOT / "evelyn_core" / "runtime"
if str(RUNTIME_ROOT) not in sys.path:
    sys.path.insert(0, str(RUNTIME_ROOT))

from evelyn_core.host_vision_bridge import HostVisionBridge  # noqa: E402
from evelyn_core.host_vision_contract import HOST_VISION_REQUEST_SCHEMA  # noqa: E402
from evelyn_core.vision_runtime import VisionEvidence, record_vision_evidence  # noqa: E402


class FakeComposition:
    def __init__(self, evidence: VisionEvidence | None = None) -> None:
        self.calls: list[tuple[str, bool]] = []
        self.evidence = evidence or VisionEvidence(
            state="observed",
            reason_code="live_observation",
            evidence_available=True,
            scene_available=True,
            confidence="normal",
            actionable=True,
            freshness="live",
        )

    async def build_live_vision_context(
        self,
        user_text: str,
        *,
        metrics: dict | None = None,
        run_ocr: bool = True,
    ) -> str:
        self.calls.append((user_text, run_ocr))
        record_vision_evidence(metrics, self.evidence)
        if metrics is not None:
            metrics.setdefault("meta", {}).update(
                {
                    "vision_capture_deleted": True,
                    "vision_scene_chars": 12,
                    "vision_ocr_chars": 7 if run_ocr else 0,
                }
            )
        return "scene: Control Page\nocr_text: Evelyn"


def write_request(
    path: Path,
    *,
    request_id: str,
    now: float,
    extra: dict | None = None,
) -> None:
    payload = {
        "schema": HOST_VISION_REQUEST_SCHEMA,
        "requestId": request_id,
        "createdAt": now,
        "expiresAt": now + 120.0,
        "userText": "현재 화면을 봐줘",
        "runOcr": True,
    }
    if extra:
        payload.update(extra)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload), encoding="utf-8")


class HostVisionBridgeTests(unittest.IsolatedAsyncioTestCase):
    def test_default_composition_wires_read_only_accessibility_provider(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as temp_root:
            bridge = HostVisionBridge(
                session=object(),  # type: ignore[arg-type]
                artifacts_root=Path(temp_root),
            )

        self.assertIsNotNone(
            bridge.composition.deps.local_accessibility_provider
        )

    async def test_valid_request_is_claimed_analyzed_and_bounded(self) -> None:
        with tempfile.TemporaryDirectory() as temp_root:
            root = Path(temp_root)
            composition = FakeComposition()
            bridge = HostVisionBridge(
                session=object(),  # type: ignore[arg-type]
                artifacts_root=root,
                composition=composition,  # type: ignore[arg-type]
                now=lambda: 1000.0,
                monotonic=lambda: 20.0,
            )
            request_id = "a" * 32
            request_path = root / "host_vision" / "requests" / f"{request_id}.json"
            write_request(request_path, request_id=request_id, now=1000.0)

            processed = await bridge.process_pending()
            response_path = root / "host_vision" / "responses" / f"{request_id}.json"
            response = json.loads(response_path.read_text(encoding="utf-8"))

        self.assertEqual(processed, 1)
        self.assertEqual(composition.calls, [("현재 화면을 봐줘", True)])
        self.assertFalse(request_path.exists())
        self.assertEqual(response["schema"], "host_vision.response.v1")
        self.assertEqual(response["evidence"]["state"], "observed")
        self.assertTrue(response["screenshotDeleted"])
        self.assertEqual(response["sceneChars"], 12)
        self.assertNotIn("userText", response)
        self.assertNotIn("imagePath", response)

    async def test_arbitrary_path_or_command_fields_are_rejected_before_capture(self) -> None:
        with tempfile.TemporaryDirectory() as temp_root:
            root = Path(temp_root)
            composition = FakeComposition()
            bridge = HostVisionBridge(
                session=object(),  # type: ignore[arg-type]
                artifacts_root=root,
                composition=composition,  # type: ignore[arg-type]
                now=lambda: 1000.0,
            )
            request_id = "b" * 32
            request_path = root / "host_vision" / "requests" / f"{request_id}.json"
            write_request(
                request_path,
                request_id=request_id,
                now=1000.0,
                extra={"imagePath": "C:/private.txt", "command": "arbitrary"},
            )

            await bridge.process_pending()
            response_path = root / "host_vision" / "responses" / f"{request_id}.json"
            response = json.loads(response_path.read_text(encoding="utf-8"))

        self.assertEqual(composition.calls, [])
        self.assertEqual(response["errorCode"], "invalid_request")
        self.assertFalse(response["evidence"]["evidence_available"])

    async def test_expired_request_never_captures(self) -> None:
        with tempfile.TemporaryDirectory() as temp_root:
            root = Path(temp_root)
            composition = FakeComposition()
            bridge = HostVisionBridge(
                session=object(),  # type: ignore[arg-type]
                artifacts_root=root,
                composition=composition,  # type: ignore[arg-type]
                now=lambda: 1201.0,
            )
            request_id = "c" * 32
            request_path = root / "host_vision" / "requests" / f"{request_id}.json"
            write_request(request_path, request_id=request_id, now=1000.0)

            await bridge.process_pending()
            response_path = root / "host_vision" / "responses" / f"{request_id}.json"
            response = json.loads(response_path.read_text(encoding="utf-8"))

        self.assertEqual(composition.calls, [])
        self.assertEqual(response["errorCode"], "request_expired")
        self.assertEqual(bridge.expired_count, 1)

    async def test_stale_screenshot_and_response_are_removed(self) -> None:
        with tempfile.TemporaryDirectory() as temp_root:
            root = Path(temp_root)
            bridge = HostVisionBridge(
                session=object(),  # type: ignore[arg-type]
                artifacts_root=root,
                composition=FakeComposition(),  # type: ignore[arg-type]
                now=lambda: 1000.0,
            )
            bridge._ensure_directories()
            screenshot = bridge.screenshots_dir / "stale.png"
            response = bridge.responses_dir / f"{'d' * 32}.json"
            screenshot.write_bytes(b"image")
            response.write_text("{}", encoding="utf-8")
            os.utime(screenshot, (0.0, 0.0))
            os.utime(response, (0.0, 0.0))

            await bridge._cleanup_stale(force=True)

        self.assertFalse(screenshot.exists())
        self.assertFalse(response.exists())


if __name__ == "__main__":
    unittest.main()
