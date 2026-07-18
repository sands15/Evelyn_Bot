from __future__ import annotations

import sys
import unittest
from pathlib import Path
from typing import Any


REPO_ROOT = next(path for path in Path(__file__).resolve().parents if (path / "main.py").exists())
RUNTIME_ROOT = REPO_ROOT / "evelyn_core" / "runtime"
if str(RUNTIME_ROOT) not in sys.path:
    sys.path.insert(0, str(RUNTIME_ROOT))

from evelyn_core.startup_audio_runtime import (  # noqa: E402
    OpusStartupRuntimeDeps,
    SttWarmupRuntimeDeps,
    ensure_opus_loaded_from_runtime,
    warmup_stt_sync_from_runtime,
)


class StartupAudioRuntimeTests(unittest.TestCase):
    def test_ensure_opus_loaded_returns_when_already_loaded(self) -> None:
        marks: list[tuple[str, str, str]] = []
        logs: list[str] = []

        ensure_opus_loaded_from_runtime(
            deps=OpusStartupRuntimeDeps(
                opus_is_loaded=lambda: True,
                load_default_opus=lambda: (_ for _ in ()).throw(AssertionError("unexpected")),
                mark_startup_component=lambda key, status, detail="": marks.append((key, status, detail)),
                log=lambda message: logs.append(message),
            )
        )

        self.assertEqual(marks, [("opus", "done", "already loaded")])
        self.assertEqual(logs, ["[OPUS LOAD] already_loaded"])

    def test_ensure_opus_loaded_marks_failed_when_load_does_not_report_loaded(self) -> None:
        marks: list[tuple[str, str, str]] = []
        loaded = False

        with self.assertRaisesRegex(RuntimeError, "did not report loaded"):
            ensure_opus_loaded_from_runtime(
                deps=OpusStartupRuntimeDeps(
                    opus_is_loaded=lambda: loaded,
                    load_default_opus=lambda: None,
                    mark_startup_component=lambda key, status, detail="": marks.append((key, status, detail)),
                )
            )

        self.assertEqual(marks[-1], ("opus", "failed", "library did not report loaded"))

    def test_warmup_stt_sync_transcribes_silence_and_marks_done(self) -> None:
        marks: list[tuple[str, str, str]] = []
        logs: list[str] = []
        calls: list[dict[str, Any]] = []

        warmup_stt_sync_from_runtime(
            deps=SttWarmupRuntimeDeps(
                mark_startup_component=lambda key, status, detail="": marks.append((key, status, detail)),
                zeros=lambda size: [0.0] * size,
                transcribe_audio16k_sync=lambda audio, **kwargs: calls.append({"audio": audio, **kwargs}) or "",
                target_rate=16000,
                wake_max_tokens=12,
                log=lambda message: logs.append(message),
            )
        )

        self.assertEqual(marks[0], ("stt", "running", "STT model warmup"))
        self.assertEqual(marks[-1], ("stt", "done", ""))
        self.assertEqual(len(calls[0]["audio"]), 16000)
        self.assertEqual(calls[0]["max_new_tokens"], 12)
        self.assertEqual(calls[0]["sampling_rate"], 16000)
        self.assertEqual(calls[0]["stage"], "warmup")
        self.assertEqual(logs[-1], "[STARTUP] stt_warmup_done")

    def test_warmup_stt_sync_marks_failed_on_transcribe_error(self) -> None:
        marks: list[tuple[str, str, str]] = []

        def fail_transcribe(*_args: Any, **_kwargs: Any) -> str:
            raise ValueError("stt down")

        with self.assertRaisesRegex(RuntimeError, "STT warmup failed"):
            warmup_stt_sync_from_runtime(
                deps=SttWarmupRuntimeDeps(
                    mark_startup_component=lambda key, status, detail="": marks.append((key, status, detail)),
                    zeros=lambda size: [0.0] * size,
                    transcribe_audio16k_sync=fail_transcribe,
                    target_rate=16000,
                    wake_max_tokens=99,
                )
            )

        self.assertEqual(marks[-1], ("stt", "failed", "ValueError('stt down')"))


if __name__ == "__main__":
    unittest.main()
