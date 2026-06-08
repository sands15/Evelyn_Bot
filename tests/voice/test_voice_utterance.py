import json
import sys
import unittest
from pathlib import Path


REPO_ROOT = next(path for path in Path(__file__).resolve().parents if (path / "main.py").exists())
RUNTIME_ROOT = REPO_ROOT / "evelyn_core" / "runtime"
if str(RUNTIME_ROOT) not in sys.path:
    sys.path.insert(0, str(RUNTIME_ROOT))

from evelyn_core.voice_utterance import (  # noqa: E402
    discord_pcm_seconds,
    merge_debug_meta,
    merge_discord_pcm_segments,
)


class VoiceUtteranceTests(unittest.TestCase):
    def test_merge_inserts_short_silence_between_segments(self) -> None:
        left = b"\x01\x00" * (48000 * 2)
        right = b"\x02\x00" * (48000 * 2)

        merged = merge_discord_pcm_segments([left, right], pad_ms=180)

        self.assertGreater(len(merged), len(left) + len(right))
        self.assertAlmostEqual(discord_pcm_seconds(merged), 2.18, places=2)

    def test_single_segment_is_unchanged(self) -> None:
        segment = b"\x01\x00" * 128

        self.assertEqual(merge_discord_pcm_segments([segment], pad_ms=180), segment)

    def test_debug_meta_marks_assembled_utterance(self) -> None:
        meta = merge_debug_meta({"source": "local_mic"}, segment_count=2, added_pad_ms=180, total_audio_sec=2.18)

        encoded = json.dumps(meta)

        self.assertIn("utterance_assembly", encoded)
        self.assertTrue(meta["assembled_utterance"])
        self.assertEqual(meta["utterance_assembly"]["segment_count"], 2)
        self.assertEqual(meta["utterance_assembly"]["added_pad_ms"], 180)


if __name__ == "__main__":
    unittest.main()
