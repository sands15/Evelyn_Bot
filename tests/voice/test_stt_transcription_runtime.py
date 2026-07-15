from __future__ import annotations

import sys
import unittest
from pathlib import Path
from types import SimpleNamespace


REPO_ROOT = next(path for path in Path(__file__).resolve().parents if (path / "main.py").exists())
RUNTIME_ROOT = REPO_ROOT / "evelyn_core" / "runtime"
if str(RUNTIME_ROOT) not in sys.path:
    sys.path.insert(0, str(RUNTIME_ROOT))

from evelyn_core.stt_transcription_runtime import (  # noqa: E402
    SttTranscriptionRuntimeDeps,
    transcribe_audio16k_from_runtime,
)


class FakeAudio:
    def __init__(self, size: int) -> None:
        self.size = size


class FakeModel:
    def __init__(self, results) -> None:
        self.results = results
        self.calls: list[dict] = []

    def transcribe(self, **kwargs):
        self.calls.append(kwargs)
        return self.results


class SttTranscriptionRuntimeTests(unittest.TestCase):
    def setUp(self) -> None:
        self.logs: list[str] = []
        self.remote_calls: list[tuple[object, dict]] = []
        self.resample_calls: list[tuple[object, int, int]] = []
        self.model = FakeModel([SimpleNamespace(text=" 로컬 결과 ")])
        self.remote_result = {"text": " 원격 결과 "}
        self.remote_error: Exception | None = None

    def transcribe_remote(self, audio, **kwargs):
        self.remote_calls.append((audio, kwargs))
        if self.remote_error is not None:
            raise self.remote_error
        return self.remote_result

    def build_deps(self, *, service_url: str = "http://stt", fallback: bool = True) -> SttTranscriptionRuntimeDeps:
        return SttTranscriptionRuntimeDeps(
            stt_service_url=service_url,
            stt_service_timeout_sec=4.5,
            stt_service_fallback_local=fallback,
            stt_language="ko",
            stt_force_language=True,
            target_rate=16000,
            normalize_stt_language=lambda _value, **_kwargs: "Korean",
            transcribe_via_service=self.transcribe_remote,
            get_stt_model=lambda: ("qwen", None, self.model),
            as_float32_array=lambda audio: audio,
            resample_audio_float=self.resample,
            clean_text=lambda text: text.strip(),
            log=self.logs.append,
        )

    def resample(self, audio, source_rate: int, target_rate: int):
        self.resample_calls.append((audio, source_rate, target_rate))
        return FakeAudio(16000)

    def test_empty_audio_returns_before_remote_or_model(self) -> None:
        result = transcribe_audio16k_from_runtime(
            FakeAudio(0),
            deps=self.build_deps(),
            sampling_rate=16000,
        )

        self.assertEqual(result, "")
        self.assertEqual(self.remote_calls, [])
        self.assertEqual(self.model.calls, [])

    def test_remote_transcription_preserves_request_contract(self) -> None:
        audio = FakeAudio(32000)

        result = transcribe_audio16k_from_runtime(
            audio,
            333,
            deps=self.build_deps(),
            sampling_rate=16000,
            stage="probe",
        )

        self.assertEqual(result, "원격 결과")
        kwargs = self.remote_calls[0][1]
        self.assertEqual(kwargs["service_url"], "http://stt")
        self.assertEqual(kwargs["timeout_sec"], 4.5)
        self.assertEqual(kwargs["max_new_tokens"], 333)
        self.assertEqual(kwargs["stage"], "probe")
        self.assertEqual(kwargs["language"], "Korean")
        self.assertEqual(self.model.calls, [])

    def test_remote_failure_raises_when_local_fallback_disabled(self) -> None:
        self.remote_error = RuntimeError("remote down")

        with self.assertRaisesRegex(RuntimeError, "remote down"):
            transcribe_audio16k_from_runtime(
                FakeAudio(16000),
                deps=self.build_deps(fallback=False),
                sampling_rate=16000,
            )

        self.assertEqual(self.model.calls, [])
        self.assertTrue(any("STT REMOTE FAIL" in line for line in self.logs))

    def test_local_fallback_resamples_and_transcribes(self) -> None:
        self.remote_error = RuntimeError("remote down")
        audio = FakeAudio(8000)

        result = transcribe_audio16k_from_runtime(
            audio,
            deps=self.build_deps(),
            sampling_rate=8000,
            stage="full",
        )

        self.assertEqual(result, "로컬 결과")
        self.assertEqual(self.resample_calls, [(audio, 8000, 16000)])
        call = self.model.calls[0]
        self.assertEqual(call["audio"][1], 16000)
        self.assertEqual(call["language"], "Korean")
        self.assertFalse(call["return_time_stamps"])

    def test_main_delegates_sync_transcription_to_runtime_module(self) -> None:
        source = (REPO_ROOT / "main.py").read_text(encoding="utf-8")
        start = source.index("def transcribe_audio16k_sync(")
        end = source.index("def build_partial_stt_window", start)
        function_source = source[start:end]

        self.assertIn("transcribe_audio16k_from_runtime(", function_source)
        self.assertNotIn("model.transcribe(", function_source)
        self.assertNotIn("transcribe_audio16k_via_service(", function_source)


if __name__ == "__main__":
    unittest.main()
