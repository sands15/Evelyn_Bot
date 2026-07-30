from __future__ import annotations

import sys
import unittest
from pathlib import Path
from unittest.mock import patch

import numpy as np


REPO_ROOT = next(path for path in Path(__file__).resolve().parents if (path / "main.py").exists())
RUNTIME_ROOT = REPO_ROOT / "evelyn_core" / "runtime"
if str(RUNTIME_ROOT) not in sys.path:
    sys.path.insert(0, str(RUNTIME_ROOT))

from evelyn_core import audio as audio_module  # noqa: E402


class AudioOptionalTorchTests(unittest.TestCase):
    def test_numpy_resample_remains_available_without_torch_or_soxr(self) -> None:
        source = np.linspace(-0.5, 0.5, 8000, dtype=np.float32)

        with (
            patch.object(audio_module, "torch", None),
            patch.object(audio_module, "torchaudio_F", None),
            patch.object(audio_module, "soxr", None),
        ):
            result = audio_module.resample_audio_float(source, 8000, 16000)

        self.assertEqual(result.dtype, np.float32)
        self.assertEqual(result.size, 16000)
        self.assertTrue(np.isfinite(result).all())

    def test_silero_path_fails_cleanly_without_optional_torch(self) -> None:
        with patch.object(audio_module, "torch", None):
            with self.assertRaisesRegex(RuntimeError, "torch is not available"):
                audio_module.is_probably_silent_silero(
                    np.ones(16000, dtype=np.float32),
                    sampling_rate=16000,
                )


if __name__ == "__main__":
    unittest.main()
