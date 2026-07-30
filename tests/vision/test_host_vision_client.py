from __future__ import annotations

import asyncio
import json
from pathlib import Path
import sys
import tempfile
import time
import unittest


REPO_ROOT = next(path for path in Path(__file__).resolve().parents if (path / "main.py").exists())
RUNTIME_ROOT = REPO_ROOT / "evelyn_core" / "runtime"
if str(RUNTIME_ROOT) not in sys.path:
    sys.path.insert(0, str(RUNTIME_ROOT))

from evelyn_core.host_vision_client import request_host_vision  # noqa: E402
from evelyn_core.runtime_artifact_io import atomic_json_write  # noqa: E402
from evelyn_core.vision_runtime import VisionEvidence  # noqa: E402


class HostVisionClientTests(unittest.IsolatedAsyncioTestCase):
    async def test_response_is_validated_and_consumed(self) -> None:
        with tempfile.TemporaryDirectory() as temp_root:
            root = Path(temp_root)

            async def fake_host() -> None:
                requests = root / "host_vision" / "requests"
                for _ in range(100):
                    candidates = list(requests.glob("*.json")) if requests.exists() else []
                    if candidates:
                        break
                    await asyncio.sleep(0.005)
                request_path = candidates[0]
                request = json.loads(request_path.read_text(encoding="utf-8"))
                request_id = request["requestId"]
                evidence = VisionEvidence(
                    state="observed",
                    reason_code="live_observation",
                    evidence_available=True,
                    scene_available=True,
                    ocr_available=True,
                    confidence="normal",
                    actionable=True,
                    freshness="live",
                )
                response = {
                    "schema": "host_vision.response.v1",
                    "requestId": request_id,
                    "createdAt": time.time(),
                    "expiresAt": time.time() + 60.0,
                    "observation": "scene: Control Page\nocr_text: Start",
                    "evidence": evidence.to_dict(),
                    "errorCode": "",
                    "latencyMs": 14.0,
                    "screenshotDeleted": True,
                    "sceneChars": 12,
                    "ocrChars": 5,
                }
                atomic_json_write(
                    root / "host_vision" / "responses" / f"{request_id}.json",
                    response,
                )

            host_task = asyncio.create_task(fake_host())
            result = await request_host_vision(
                "화면 글자를 읽어줘",
                run_ocr=True,
                artifacts_root=root,
                timeout_sec=1.0,
                poll_interval_sec=0.005,
            )
            await host_task
            request_files = list((root / "host_vision" / "requests").glob("*.json"))
            response_files = list((root / "host_vision" / "responses").glob("*.json"))

        self.assertTrue(result.evidence.evidence_available)
        self.assertTrue(result.evidence.ocr_available)
        self.assertIn("Control Page", result.observation)
        self.assertEqual(result.latency_ms, 14.0)
        self.assertEqual(request_files, [])
        self.assertEqual(response_files, [])

    async def test_timeout_fails_closed_and_removes_request(self) -> None:
        with tempfile.TemporaryDirectory() as temp_root:
            root = Path(temp_root)
            result = await request_host_vision(
                "화면을 봐줘",
                run_ocr=False,
                artifacts_root=root,
                timeout_sec=0.05,
                poll_interval_sec=0.01,
            )
            request_files = list((root / "host_vision" / "requests").glob("*.json"))

        self.assertEqual(result.evidence.state, "unavailable")
        self.assertFalse(result.evidence.evidence_available)
        self.assertEqual(result.error_code, "host_vision_timeout")
        self.assertEqual(request_files, [])

    async def test_malformed_observed_contract_is_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as temp_root:
            root = Path(temp_root)

            async def fake_host() -> None:
                requests = root / "host_vision" / "requests"
                while True:
                    candidates = list(requests.glob("*.json")) if requests.exists() else []
                    if candidates:
                        break
                    await asyncio.sleep(0.005)
                request = json.loads(candidates[0].read_text(encoding="utf-8"))
                request_id = request["requestId"]
                response = {
                    "schema": "host_vision.response.v1",
                    "requestId": request_id,
                    "createdAt": time.time(),
                    "expiresAt": time.time() + 60.0,
                    "observation": "",
                    "evidence": {
                        "schema": "vision.evidence.v1",
                        "state": "observed",
                        "reason_code": "forged",
                        "evidence_available": True,
                        "scene_available": True,
                        "ocr_available": False,
                        "confidence": "normal",
                        "actionable": True,
                        "freshness": "live",
                    },
                    "errorCode": "",
                    "latencyMs": 1.0,
                    "screenshotDeleted": True,
                    "sceneChars": 20,
                    "ocrChars": 0,
                }
                atomic_json_write(
                    root / "host_vision" / "responses" / f"{request_id}.json",
                    response,
                )

            host_task = asyncio.create_task(fake_host())
            result = await request_host_vision(
                "화면을 봐줘",
                run_ocr=False,
                artifacts_root=root,
                timeout_sec=1.0,
                poll_interval_sec=0.005,
            )
            await host_task

        self.assertEqual(result.evidence.state, "failed")
        self.assertEqual(result.error_code, "invalid_evidence_contract")


if __name__ == "__main__":
    unittest.main()
