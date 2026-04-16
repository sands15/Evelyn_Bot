from __future__ import annotations

import wave
from pathlib import Path


class AudioSink:
    def write(self, user_id: int | None, pcm: bytes, sample_rate: int, channels: int) -> None:
        raise NotImplementedError

    def cleanup(self) -> None:
        pass


class NullSink(AudioSink):
    def write(self, user_id: int | None, pcm: bytes, sample_rate: int, channels: int) -> None:
        return


class WaveSink(AudioSink):
    def __init__(self, out_path: str | Path, sample_rate: int = 48000, channels: int = 2):
        self.out_path = Path(out_path)
        self.sample_rate = sample_rate
        self.channels = channels
        self._wf: wave.Wave_write | None = None

    def _ensure_open(self) -> None:
        if self._wf is not None:
            return

        self.out_path.parent.mkdir(parents=True, exist_ok=True)
        wf = wave.open(str(self.out_path), "wb")
        wf.setnchannels(self.channels)
        wf.setsampwidth(2)  # int16
        wf.setframerate(self.sample_rate)
        self._wf = wf

    def write(self, user_id: int | None, pcm: bytes, sample_rate: int, channels: int) -> None:
        if sample_rate != self.sample_rate or channels != self.channels:
            raise ValueError(
                f"WaveSink format mismatch: got {sample_rate}Hz/{channels}ch, "
                f"expected {self.sample_rate}Hz/{self.channels}ch"
            )
        self._ensure_open()
        assert self._wf is not None
        self._wf.writeframes(pcm)

    def cleanup(self) -> None:
        if self._wf is not None:
            self._wf.close()
            self._wf = None