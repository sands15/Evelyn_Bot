from __future__ import annotations

import unittest
from pathlib import Path


REPO_ROOT = next(path for path in Path(__file__).resolve().parents if (path / "main.py").exists())
RUNTIME_ROOT = REPO_ROOT / "evelyn_core" / "runtime" / "evelyn_core"
STT_SERVICE = RUNTIME_ROOT / "stt_service.py"
STT_CLIENT = RUNTIME_ROOT / "stt_client.py"
STT_TRANSCRIPTION_RUNTIME = RUNTIME_ROOT / "stt_transcription_runtime.py"
MAIN = REPO_ROOT / "main.py"
COMPOSE = REPO_ROOT / "docker-compose.fast-control.yml"


class SttServiceContractTests(unittest.TestCase):
    def test_stt_service_exposes_health_and_transcribe_endpoint(self) -> None:
        source = STT_SERVICE.read_text(encoding="utf-8")

        self.assertIn('@app.get("/health")', source)
        self.assertIn('@app.post("/v1/stt/transcribe")', source)
        self.assertIn("audio_f32_base64", source)
        self.assertIn("Qwen3ASRModel.from_pretrained", source)

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

    def test_compose_declares_stt_profile(self) -> None:
        source = COMPOSE.read_text(encoding="utf-8")

        self.assertIn("container_name: evelyn-stt", source)
        self.assertIn('profiles: ["stt"]', source)
        self.assertIn("docker/Dockerfile.stt", source)
        self.assertIn("127.0.0.1:8892:8892", source)


if __name__ == "__main__":
    unittest.main()
