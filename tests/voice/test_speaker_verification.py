from __future__ import annotations

import sys
import tempfile
import unittest
import wave
from pathlib import Path
from unittest.mock import patch

import numpy as np


REPO_ROOT = next(path for path in Path(__file__).resolve().parents if (path / "main.py").exists())
RUNTIME_ROOT = REPO_ROOT / "evelyn_core" / "runtime"
if str(RUNTIME_ROOT) not in sys.path:
    sys.path.insert(0, str(RUNTIME_ROOT))

from evelyn_core.speaker_verification import (  # noqa: E402
    SpeakerVerificationConfig,
    SpeakerVerifier,
    speaker_verification_applies,
)
from evelyn_core import speaker_verification as speaker_verification_module  # noqa: E402


def write_wav(path: Path, audio: np.ndarray, *, rate: int = 16000) -> None:
    pcm = (np.clip(audio, -1.0, 1.0) * 32767.0).astype(np.int16)
    with wave.open(str(path), "wb") as wf:
        wf.setnchannels(1)
        wf.setsampwidth(2)
        wf.setframerate(rate)
        wf.writeframes(pcm.tobytes())


def sign_embedding(audio: np.ndarray, _sampling_rate: int) -> np.ndarray:
    return np.array([1.0, 0.0], dtype=np.float32) if float(np.mean(audio)) >= 0.0 else np.array([0.0, 1.0], dtype=np.float32)


class SpeakerVerificationTests(unittest.TestCase):
    def test_verifier_matches_enrolled_voiceprint(self) -> None:
        logs = []
        with tempfile.TemporaryDirectory() as tmp:
            enroll_dir = Path(tmp)
            write_wav(enroll_dir / "sample.wav", np.ones(16000, dtype=np.float32) * 0.2)
            verifier = SpeakerVerifier(
                SpeakerVerificationConfig(enabled=True, enroll_dir=enroll_dir, threshold=0.8),
                embedding_fn=sign_embedding,
                log=logs.append,
            )

            result = verifier.verify(np.ones(16000, dtype=np.float32) * 0.1, sampling_rate=16000)

        self.assertEqual(result.status, "verified")
        self.assertTrue(result.matched)
        self.assertEqual(result.sample_count, 1)
        self.assertEqual(logs, ["[SPEAKER VERIFY] enrolled samples=1"])
        self.assertNotIn(str(enroll_dir), repr(logs))

    def test_verifier_rejects_different_voiceprint(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            enroll_dir = Path(tmp)
            write_wav(enroll_dir / "sample.wav", np.ones(16000, dtype=np.float32) * 0.2)
            verifier = SpeakerVerifier(
                SpeakerVerificationConfig(enabled=True, enroll_dir=enroll_dir, threshold=0.8),
                embedding_fn=sign_embedding,
            )

            result = verifier.verify(np.ones(16000, dtype=np.float32) * -0.1, sampling_rate=16000)

        self.assertEqual(result.status, "rejected")
        self.assertFalse(result.matched)

    def test_verifier_allows_runtime_to_handle_missing_enrollment(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            verifier = SpeakerVerifier(
                SpeakerVerificationConfig(enabled=True, enroll_dir=Path(tmp)),
                embedding_fn=sign_embedding,
            )

            result = verifier.verify(np.ones(16000, dtype=np.float32) * 0.1, sampling_rate=16000)

        self.assertEqual(result.status, "not_enrolled")
        self.assertIsNone(result.matched)

    def test_apply_scope_defaults_to_local_mic_only(self) -> None:
        self.assertTrue(speaker_verification_applies(source="local_mic", apply_to="local_mic"))
        self.assertFalse(speaker_verification_applies(source="discord_voice", apply_to="local_mic"))
        self.assertTrue(speaker_verification_applies(source="discord_voice", apply_to="all"))

    def test_missing_optional_torch_degrades_verification_without_import_failure(self) -> None:
        logs = []
        with tempfile.TemporaryDirectory() as tmp:
            enroll_dir = Path(tmp)
            write_wav(enroll_dir / "sample.wav", np.ones(16000, dtype=np.float32) * 0.2)
            verifier = SpeakerVerifier(
                SpeakerVerificationConfig(enabled=True, enroll_dir=enroll_dir),
                log=logs.append,
            )

            with patch.object(speaker_verification_module, "torch", None):
                result = verifier.verify(
                    np.ones(16000, dtype=np.float32) * 0.1,
                    sampling_rate=16000,
                )

        self.assertEqual(result.status, "unavailable")
        self.assertEqual(result.detail, "no_valid_enrollment_wav")
        self.assertEqual(logs, ["[SPEAKER VERIFY] enrollment_skip errorType=RuntimeError"])
        self.assertNotIn(str(enroll_dir), repr(logs))

    def test_probe_failure_detail_is_content_free(self) -> None:
        private_error = "PRIVATE_SPEAKER_FAILURE C:/secret/voice-token"
        calls = 0

        def embed(_audio: np.ndarray, _sampling_rate: int) -> np.ndarray:
            nonlocal calls
            calls += 1
            if calls == 1:
                return np.array([1.0, 0.0], dtype=np.float32)
            raise RuntimeError(private_error)

        with tempfile.TemporaryDirectory() as tmp:
            enroll_dir = Path(tmp)
            write_wav(enroll_dir / "sample.wav", np.ones(16000, dtype=np.float32) * 0.2)
            verifier = SpeakerVerifier(
                SpeakerVerificationConfig(enabled=True, enroll_dir=enroll_dir),
                embedding_fn=embed,
            )

            result = verifier.verify(
                np.ones(16000, dtype=np.float32) * 0.1,
                sampling_rate=16000,
            )

        self.assertEqual(result.status, "error")
        self.assertIsNone(result.matched)
        self.assertEqual(result.detail, "speaker_verification_failed:RuntimeError")
        self.assertNotIn(private_error, repr(result.to_dict()))


if __name__ == "__main__":
    unittest.main()
