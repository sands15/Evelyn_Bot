from __future__ import annotations

import asyncio
import sys
import unittest
from pathlib import Path


REPO_ROOT = next(path for path in Path(__file__).resolve().parents if (path / "main.py").exists())
RUNTIME_ROOT = REPO_ROOT / "evelyn_core" / "runtime"
if str(RUNTIME_ROOT) not in sys.path:
    sys.path.insert(0, str(RUNTIME_ROOT))

from evelyn_core import local_tts_playback  # noqa: E402
from evelyn_core.local_tts_playback import (  # noqa: E402
    LocalTtsPlaybackManager,
    local_tts_tail_silence_bytes,
    normalize_output_device,
)


class FakeSource:
    def __init__(self, chunks: list[bytes]) -> None:
        self.chunks = list(chunks)
        self.cleanup_called = False
        self.error = None

    def read(self) -> bytes:
        if not self.chunks:
            return b""
        return self.chunks.pop(0)

    def cleanup(self) -> None:
        self.cleanup_called = True


class ObservingSource(FakeSource):
    def __init__(self, chunks: list[bytes]) -> None:
        super().__init__(chunks)
        self.read_count = 0
        self.writes_seen_before_second_read: list[bytes] = []

    def read(self) -> bytes:
        self.read_count += 1
        if self.read_count == 2:
            self.writes_seen_before_second_read = list(FakeRawOutputStream.writes)
        return super().read()


class FakeRawOutputStream:
    writes: list[bytes] = []

    def __init__(self, **kwargs) -> None:
        self.kwargs = kwargs

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, tb) -> None:
        return None

    def write(self, chunk: bytes) -> None:
        self.writes.append(bytes(chunk))


class FakeSoundDevice:
    RawOutputStream = FakeRawOutputStream


class LocalTtsPlaybackTests(unittest.TestCase):
    def test_normalize_output_device(self) -> None:
        self.assertIsNone(normalize_output_device(None))
        self.assertIsNone(normalize_output_device("default"))
        self.assertEqual(normalize_output_device("3"), 3)
        self.assertEqual(normalize_output_device("Speakers"), "Speakers")

    def test_manager_writes_source_chunks_to_sounddevice(self) -> None:
        original_sd = local_tts_playback.sd
        FakeRawOutputStream.writes = []
        local_tts_playback.sd = FakeSoundDevice()
        try:
            manager = LocalTtsPlaybackManager(enabled=True, device="default")
            source = FakeSource([b"\x01\x02", b"\x03\x04"])
            ok = asyncio.run(manager.play_source(source))
        finally:
            local_tts_playback.sd = original_sd

        self.assertTrue(ok)
        self.assertTrue(source.cleanup_called)
        self.assertEqual(FakeRawOutputStream.writes, [b"\x01\x02", b"\x03\x04", local_tts_tail_silence_bytes()])
        snapshot = manager.snapshot()
        self.assertEqual(snapshot["playCount"], 1)
        self.assertEqual(snapshot["playedBytes"], 4 + len(local_tts_tail_silence_bytes()))

    def test_manager_streams_first_chunk_before_reading_entire_source(self) -> None:
        original_sd = local_tts_playback.sd
        FakeRawOutputStream.writes = []
        local_tts_playback.sd = FakeSoundDevice()
        try:
            manager = LocalTtsPlaybackManager(enabled=True, device="default")
            source = ObservingSource([b"first", b"second"])
            ok = asyncio.run(manager.play_source(source))
        finally:
            local_tts_playback.sd = original_sd

        self.assertTrue(ok)
        self.assertEqual(source.writes_seen_before_second_read, [b"first"])
        self.assertEqual(FakeRawOutputStream.writes[:2], [b"first", b"second"])


if __name__ == "__main__":
    unittest.main()
