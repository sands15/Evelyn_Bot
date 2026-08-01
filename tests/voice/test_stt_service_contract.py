from __future__ import annotations

import json
import sys
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

from evelyn_core.stt_client import transcribe_audio16k_via_service  # noqa: E402


class SttServiceContractTests(unittest.TestCase):
    def test_stt_service_exposes_health_and_transcribe_endpoint(self) -> None:
        source = STT_SERVICE.read_text(encoding="utf-8")

        self.assertIn('@app.get("/health")', source)
        self.assertIn('@app.post("/v1/stt/transcribe")', source)
        self.assertIn("audio_f32_base64", source)
        self.assertIn("Qwen3ASRModel.from_pretrained", source)

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

    def test_validation_privacy_flag_reaches_service_without_binding_ids(self) -> None:
        requests = []

        class Response:
            def __enter__(self):
                return self

            def __exit__(self, *_args):
                return False

            def read(self) -> bytes:
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


if __name__ == "__main__":
    unittest.main()
