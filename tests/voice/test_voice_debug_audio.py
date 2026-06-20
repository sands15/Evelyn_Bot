from __future__ import annotations

import json
import os
import sys
import tempfile
import time
import unittest
import wave
from pathlib import Path

import numpy as np


REPO_ROOT = next(path for path in Path(__file__).resolve().parents if (path / "main.py").exists())
RUNTIME_ROOT = REPO_ROOT / "evelyn_core" / "runtime"
if str(RUNTIME_ROOT) not in sys.path:
    sys.path.insert(0, str(RUNTIME_ROOT))

from evelyn_core.voice_debug_audio import (  # noqa: E402
    build_voice_debug_audio_item,
    sanitize_debug_label,
    save_voice_debug_audio_now,
    trim_voice_debug_dir,
    voice_debug_drop_message,
)


class VoiceDebugAudioTests(unittest.TestCase):
    def test_sanitize_debug_label_keeps_filename_safe_text(self) -> None:
        self.assertEqual(sanitize_debug_label(" 정훈 / mic? "), "정훈_mic")
        self.assertEqual(sanitize_debug_label("***"), "unknown")

    def test_build_item_copies_audio_and_metadata(self) -> None:
        audio = np.array([0.1, 0.2], dtype=np.float32)
        item = build_voice_debug_audio_item(
            guild_id=1,
            speaker="user",
            pcm_bytes=b"1234",
            audio16k=audio,
            debug_meta={"turn_id": "a"},
            stt_meta={"ok": True},
        )
        audio[0] = 0.9

        self.assertAlmostEqual(float(item["audio16k"][0]), 0.1, places=6)
        self.assertEqual(item["debug_meta"], {"turn_id": "a"})
        self.assertEqual(item["stt_meta"], {"ok": True})

    def test_save_voice_debug_audio_now_writes_wavs_and_meta(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            logs: list[str] = []
            counts: dict[int, int] = {}
            stems: dict[tuple[int, str, str, str], str] = {}
            pcm = (np.zeros(480, dtype=np.int16)).tobytes()
            audio16k = np.array([0.0, 0.5, -0.5], dtype=np.float32)

            save_voice_debug_audio_now(
                project_root=root,
                configured_dir="debug_audio",
                max_files_per_guild=20,
                raw_channels=2,
                raw_rate=48000,
                stt_rate=16000,
                counts=counts,
                stems=stems,
                log=logs.append,
                guild_id=42,
                speaker="정훈/mic",
                pcm_bytes=pcm,
                audio16k=audio16k,
                final_text="hello",
                debug_meta={"turn_id": "turn-1", "segment_id": "7"},
                stt_meta={"backend": "test"},
                session_key="session",
                stage_label="final",
            )

            guild_dir = root / "debug_audio" / "42"
            raw_path = next(guild_dir.glob("*_raw48k.wav"))
            stt_path = next(guild_dir.glob("*_stt16k.wav"))
            meta_path = next(guild_dir.glob("*.json"))
            meta = json.loads(meta_path.read_text(encoding="utf-8"))

            with wave.open(str(raw_path), "rb") as raw_wav:
                self.assertEqual(raw_wav.getnchannels(), 2)
                self.assertEqual(raw_wav.getframerate(), 48000)
            with wave.open(str(stt_path), "rb") as stt_wav:
                self.assertEqual(stt_wav.getnchannels(), 1)
                self.assertEqual(stt_wav.getframerate(), 16000)
            self.assertEqual(meta["final_text"], "hello")
            self.assertEqual(meta["turn_id"], "turn-1")
            self.assertEqual(meta["segment_id"], 7)
            self.assertIn("[VOICE DEBUG SAVE]", logs[-1])

    def test_trim_voice_debug_dir_removes_oldest_wavs_only(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            guild_dir = Path(temp_dir)
            for idx in range(4):
                path = guild_dir / f"{idx}.wav"
                path.write_bytes(b"x")
                old_time = time.time() - (10 - idx)
                path.touch()
                os.utime(path, (old_time, old_time))
            keep_json = guild_dir / "meta.json"
            keep_json.write_text("{}", encoding="utf-8")

            trim_voice_debug_dir(guild_dir, max_files=2)

            self.assertEqual(sorted(path.name for path in guild_dir.glob("*.wav")), ["2.wav", "3.wav"])
            self.assertTrue(keep_json.exists())

    def test_drop_message_is_stable(self) -> None:
        self.assertEqual(
            voice_debug_drop_message(speaker="user", stage_label="drop"),
            "[VOICE DEBUG DROP] speaker=user stage=drop reason=queue_full",
        )


if __name__ == "__main__":
    unittest.main()
