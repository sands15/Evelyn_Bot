from __future__ import annotations

import asyncio
import json
import sys
import threading
import unittest
from pathlib import Path
from unittest.mock import patch

import numpy as np


REPO_ROOT = next(path for path in Path(__file__).resolve().parents if (path / "main.py").exists())
RUNTIME_ROOT = REPO_ROOT / "evelyn_core" / "runtime" / "evelyn_core"
RUNTIME_PACKAGE_ROOT = REPO_ROOT / "evelyn_core" / "runtime"
if str(RUNTIME_PACKAGE_ROOT) not in sys.path:
    sys.path.insert(0, str(RUNTIME_PACKAGE_ROOT))
STT_SERVICE = RUNTIME_ROOT / "stt_service.py"
STT_CLIENT = RUNTIME_ROOT / "stt_client.py"
STT_TRANSCRIPTION_RUNTIME = RUNTIME_ROOT / "stt_transcription_runtime.py"
MAIN = REPO_ROOT / "main.py"
COMPOSE = REPO_ROOT / "docker-compose.fast-control.yml"

from evelyn_core.stt_client import (  # noqa: E402
    MAX_STT_RESPONSE_BYTES,
    transcribe_audio16k_via_service,
    transcribe_completed_audio16k_via_service,
)


class SttServiceContractTests(unittest.TestCase):
    def test_stt_service_exposes_health_and_transcribe_endpoint(self) -> None:
        source = STT_SERVICE.read_text(encoding="utf-8")

        self.assertIn('@app.get("/health")', source)
        self.assertIn('@app.post("/v1/stt/transcribe")', source)
        self.assertIn('@app.post("/v1/stt/streams", status_code=201)', source)
        self.assertIn('@app.post("/v1/stt/streams/{stream_id}/chunks")', source)
        self.assertIn('@app.post("/v1/stt/streams/{stream_id}/finish")', source)
        self.assertIn('@app.delete("/v1/stt/streams/{stream_id}")', source)
        self.assertIn("audio_f32_base64", source)
        self.assertIn("Qwen3ASRModel.LLM", source)
        self.assertIn('"gpu_memory_utilization": STT_VLLM_GPU_MEMORY_UTILIZATION', source)
        self.assertIn("max_model_len=STT_VLLM_MAX_MODEL_LEN", source)
        self.assertIn("max_num_seqs=STT_VLLM_MAX_NUM_SEQS", source)
        self.assertIn(
            'limit_mm_per_prompt={"audio": STT_VLLM_AUDIO_PER_PROMPT}',
            source,
        )
        self.assertIn("_read_vllm_engine_configuration(candidate)", source)
        self.assertNotIn("from vllm import", source)

    def test_stt_health_exposes_safe_configuration_and_error_counters(self) -> None:
        source = STT_SERVICE.read_text(encoding="utf-8")

        self.assertIn("load_runtime_settings", source)
        self.assertIn("RuntimeErrorCounter", source)
        self.assertIn('"configuration": _STT_CONFIG.public_summary()', source)
        self.assertIn('"importErrorType":', source)
        self.assertNotIn('"importError": repr(', source)
        self.assertIn('_RUNTIME_ERRORS.record("stt_import_failed"', source)
        self.assertIn('_RUNTIME_ERRORS.record("stt_model_load_failed"', source)
        self.assertIn('_RUNTIME_ERRORS.record("stt_transcribe_failed"', source)
        self.assertIn('detail="invalid_audio_f32_base64"', source)

    def test_main_uses_remote_stt_when_url_is_configured(self) -> None:
        main_source = MAIN.read_text(encoding="utf-8")
        runtime_source = STT_TRANSCRIPTION_RUNTIME.read_text(encoding="utf-8")

        self.assertIn("stt_service_url=STT_SERVICE_URL", main_source)
        self.assertIn("transcribe_via_service=transcribe_audio16k_via_service", main_source)
        self.assertIn("[STT REMOTE DONE]", runtime_source)
        self.assertIn("stt_service_fallback_local", runtime_source)

    def test_stt_client_uses_compact_float32_base64_payload(self) -> None:
        source = STT_CLIENT.read_text(encoding="utf-8")

        self.assertIn("np.asarray(audio, dtype=np.float32)", source)
        self.assertIn("base64.b64encode(stt_audio.tobytes())", source)
        self.assertIn("/v1/stt/transcribe", source)

    def test_completed_discord_batch_validates_and_calls_service_once(self) -> None:
        with patch(
            "evelyn_core.stt_client.transcribe_audio16k_via_service",
            return_value={"text": "완료"},
        ) as transcribe:
            result = asyncio.run(
                transcribe_completed_audio16k_via_service(
                    np.zeros(16, dtype=np.float32),
                    service_url="http://stt",
                    timeout_sec=3.0,
                    sampling_rate=16000,
                    max_new_tokens=12,
                    language="Korean",
                )
            )
        self.assertEqual(result, {"text": "완료"})
        transcribe.assert_called_once()
        self.assertEqual(transcribe.call_args.kwargs["stage"], "discord-completed")

        with self.assertRaisesRegex(ValueError, "discord_stt_audio_invalid"):
            asyncio.run(
                transcribe_completed_audio16k_via_service(
                    np.zeros((1, 2), dtype=np.float32),
                    service_url="http://stt",
                    timeout_sec=3.0,
                    sampling_rate=16000,
                    max_new_tokens=12,
                )
            )

    def test_completed_discord_batch_cancellation_drains_physical_request(self) -> None:
        request_entered = threading.Event()
        allow_request_return = threading.Event()

        def blocked_transcribe(*_args, **_kwargs):
            request_entered.set()
            if not allow_request_return.wait(timeout=2.0):
                raise TimeoutError("test request was not released")
            return {"text": "late"}

        async def scenario() -> None:
            with patch(
                "evelyn_core.stt_client.transcribe_audio16k_via_service",
                side_effect=blocked_transcribe,
            ):
                task = asyncio.create_task(
                    transcribe_completed_audio16k_via_service(
                        np.zeros(16, dtype=np.float32),
                        service_url="http://stt",
                        timeout_sec=3.0,
                        sampling_rate=16000,
                        max_new_tokens=12,
                    )
                )
                try:
                    self.assertTrue(
                        await asyncio.to_thread(request_entered.wait, 1.0)
                    )
                    task.cancel()
                    done, _pending = await asyncio.wait(
                        {task},
                        timeout=0.1,
                    )
                    self.assertEqual(done, set())
                    allow_request_return.set()
                    with self.assertRaises(asyncio.CancelledError):
                        await asyncio.wait_for(task, timeout=2.0)
                finally:
                    allow_request_return.set()
                    await asyncio.gather(task, return_exceptions=True)

        asyncio.run(scenario())

    def test_validation_privacy_flag_reaches_service_without_binding_ids(self) -> None:
        requests = []

        class Response:
            def __enter__(self):
                return self

            def __exit__(self, *_args):
                return False

            def read(self, size: int) -> bytes:
                if size != MAX_STT_RESPONSE_BYTES + 1:
                    raise AssertionError("response read was not bounded")
                return b'{"text":"raw-result"}'

        def open_request(req, **_kwargs):
            requests.append(req)
            return Response()

        with patch(
            "evelyn_core.stt_client.request.urlopen",
            side_effect=open_request,
        ):
            result = transcribe_audio16k_via_service(
                np.zeros(16, dtype=np.float32),
                service_url="http://stt",
                timeout_sec=3.0,
                sampling_rate=16000,
                max_new_tokens=12,
                stage="full",
                validation_bound=True,
            )

        payload = json.loads(requests[0].data.decode("utf-8"))
        self.assertEqual(result["text"], "raw-result")
        self.assertIs(payload["validation_bound"], True)
        self.assertFalse(any("session" in key or "attempt" in key for key in payload))

    def test_stt_client_rejects_oversized_response_with_bounded_read(self) -> None:
        class Response:
            def __enter__(self):
                return self

            def __exit__(self, *_args):
                return False

            def read(self, size: int) -> bytes:
                if size != MAX_STT_RESPONSE_BYTES + 1:
                    raise AssertionError("response read was not bounded")
                return b"x" * size

        with patch(
            "evelyn_core.stt_client.request.urlopen",
            return_value=Response(),
        ), self.assertRaisesRegex(RuntimeError, "stt_response_too_large"):
            transcribe_audio16k_via_service(
                np.zeros(16, dtype=np.float32),
                service_url="http://stt",
                timeout_sec=3.0,
                sampling_rate=16_000,
                max_new_tokens=12,
                stage="validation-batch",
            )

    def test_stt_service_redacts_validation_transcript_print(self) -> None:
        source = STT_SERVICE.read_text(encoding="utf-8")

        self.assertIn("validation_bound: bool = False", source)
        self.assertIn("validation_text_for_log(text", source)
        self.assertNotIn("text={text!r}", source)

    def test_compose_declares_stt_profile(self) -> None:
        source = COMPOSE.read_text(encoding="utf-8")

        self.assertIn("container_name: evelyn-stt", source)
        self.assertIn('profiles: ["stt"]', source)
        self.assertIn("docker/Dockerfile.stt", source)
        self.assertIn("127.0.0.1:8892:8892", source)
        self.assertIn('STT_STREAMING_ENABLED: "${STT_STREAMING_ENABLED:-true}"', source)
        self.assertIn(
            'STT_FULL_RESCORING_ENABLED: "${STT_FULL_RESCORING_ENABLED:-false}"',
            source,
        )
        self.assertIn(
            'STT_VLLM_GPU_MEMORY_UTILIZATION: "${STT_VLLM_GPU_MEMORY_UTILIZATION:-0.35}"',
            source,
        )


if __name__ == "__main__":
    unittest.main()
