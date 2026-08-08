from __future__ import annotations

import sys
import unittest
from pathlib import Path
from types import SimpleNamespace


REPO_ROOT = next(path for path in Path(__file__).resolve().parents if (path / "main.py").exists())
RUNTIME_ROOT = REPO_ROOT / "evelyn_core" / "runtime"
if str(RUNTIME_ROOT) not in sys.path:
    sys.path.insert(0, str(RUNTIME_ROOT))

from evelyn_core.omnivoice_request_runtime import (  # noqa: E402
    OmniVoiceRequestRuntimeDeps,
    build_omnivoice_tts_request_bundle_from_runtime,
    build_omnivoice_tts_result_from_runtime,
    run_omnivoice_tts_with_fallback_from_runtime,
)


class OmniVoiceRequestRuntimeTests(unittest.TestCase):
    def build_deps(
        self,
        *,
        suffix: str = "abc123",
        speed: float = 1.25,
        language: str = "ko",
    ) -> OmniVoiceRequestRuntimeDeps:
        return OmniVoiceRequestRuntimeDeps(
            request_id_suffix=lambda: suffix,
            tts_synth_request_factory=lambda **kwargs: SimpleNamespace(**kwargs),
            tts_synth_result_factory=lambda **kwargs: SimpleNamespace(**kwargs),
            omnivoice_model="omni",
            omnivoice_pcm_rate=24000,
            omnivoice_stream=True,
            omnivoice_num_step=16,
            omnivoice_speed=speed,
            omnivoice_language=language,
        )

    def test_builds_clone_voice_request_and_payload(self) -> None:
        bundle = build_omnivoice_tts_request_bundle_from_runtime(
            text="안녕",
            voice_name="clone:evelyn",
            deps=self.build_deps(),
            turn_id="turn-1",
            chunk_index=2,
            session_key="session-1",
        )

        self.assertEqual(bundle.request.request_id, "turn-1:2:abc123")
        self.assertEqual(bundle.request.voice, "clone:evelyn")
        self.assertEqual(bundle.request.voice_profile, "evelyn")
        self.assertEqual(bundle.request.metadata, {"session_key": "session-1", "text_len": 2})
        self.assertEqual(
            bundle.payload,
            {
                "model": "omni",
                "input": "안녕",
                "voice": "clone:evelyn",
                "response_format": "pcm",
                "stream": True,
                "num_step": 16,
                "speed": 1.25,
                "language": "ko",
                "turn_id": "turn-1",
                "session_key": "session-1",
            },
        )

    def test_omits_optional_payload_fields_for_defaults(self) -> None:
        bundle = build_omnivoice_tts_request_bundle_from_runtime(
            text="hello",
            voice_name="auto",
            deps=self.build_deps(speed=1.0, language=""),
        )

        self.assertEqual(bundle.request.request_id, "turnless:0:abc123")
        self.assertIsNone(bundle.request.voice_profile)
        self.assertNotIn("speed", bundle.payload)
        self.assertNotIn("language", bundle.payload)
        self.assertNotIn("turn_id", bundle.payload)
        self.assertNotIn("session_key", bundle.payload)

    def test_builds_tts_result_from_request_contract(self) -> None:
        bundle = build_omnivoice_tts_request_bundle_from_runtime(
            text="안녕",
            voice_name="auto",
            deps=self.build_deps(),
            turn_id="turn-1",
            chunk_index=1,
            session_key="session-1",
        )

        result = build_omnivoice_tts_result_from_runtime(
            bundle.request,
            deps=self.build_deps(),
            ok=False,
            status_code=503,
            latency_ms=123.4,
            first_audio_ms=None,
            error_code="http_error",
            error_text="not ready",
        )

        self.assertEqual(result.request_id, "turn-1:1:abc123")
        self.assertEqual(result.turn_id, "turn-1")
        self.assertEqual(result.backend, "omnivoice_http")
        self.assertFalse(result.ok)
        self.assertEqual(result.response_format, "pcm")
        self.assertEqual(result.sample_rate_hz, 24000)
        self.assertEqual(result.profile_resolved, "auto")
        self.assertEqual(result.status_code, 503)
        self.assertEqual(result.error_code, "http_error")
        self.assertEqual(result.error_text, "not ready")
        self.assertEqual(result.metadata, {"session_key": "session-1", "text_len": 2})


class OmniVoiceFallbackRuntimeTests(unittest.IsolatedAsyncioTestCase):
    async def test_returns_primary_result_when_ok(self) -> None:
        calls: list[str] = []

        async def stream(voice: str):
            calls.append(voice)
            return SimpleNamespace(ok=True, error_text="")

        result = await run_omnivoice_tts_with_fallback_from_runtime(primary_voice="auto", stream_with_voice=stream)

        self.assertTrue(result.ok)
        self.assertEqual(calls, ["auto"])

    async def test_clone_failure_retries_auto_and_logs(self) -> None:
        calls: list[str] = []
        logs: list[str] = []
        private_error = "PRIVATE_TTS_UPSTREAM_BODY_CANARY"

        async def stream(voice: str):
            calls.append(voice)
            return SimpleNamespace(ok=voice == "auto", error_text=private_error)

        result = await run_omnivoice_tts_with_fallback_from_runtime(
            primary_voice="clone:evelyn",
            stream_with_voice=stream,
            log=lambda message: logs.append(message),
        )

        self.assertTrue(result.ok)
        self.assertEqual(calls, ["clone:evelyn", "auto"])
        self.assertIn("clone voice 실패", logs[0])
        self.assertIn("errorCode=tts_request_failed", logs[0])
        self.assertNotIn(private_error, logs[0])
        self.assertNotIn("clone:evelyn", logs[0])

    async def test_raises_when_final_result_fails(self) -> None:
        private_error = "PRIVATE_TTS_UPSTREAM_BODY_CANARY"

        async def stream(_voice: str):
            return SimpleNamespace(ok=False, error_text=private_error)

        with self.assertRaisesRegex(
            RuntimeError,
            "^omnivoice_request_failed$",
        ) as raised:
            await run_omnivoice_tts_with_fallback_from_runtime(
                primary_voice="auto",
                stream_with_voice=stream,
            )
        self.assertNotIn(private_error, repr(raised.exception))


if __name__ == "__main__":
    unittest.main()
