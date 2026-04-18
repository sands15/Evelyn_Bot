from __future__ import annotations

import sys
import time
import wave
from pathlib import Path

import main as evelyn_main


def read_wav_pcm16(path: Path) -> bytes:
    with wave.open(str(path), "rb") as wf:
        sample_width = wf.getsampwidth()
        pcm = wf.readframes(wf.getnframes())
    if sample_width != 2:
        raise ValueError(f"Only 16-bit PCM WAV is supported, got sample_width={sample_width}")
    return pcm


def transcribe_file(wav_path: Path) -> str:
    pcm = read_wav_pcm16(wav_path)
    audio16k = evelyn_main.prepare_stt_audio(pcm)
    return evelyn_main.transcribe_audio16k_sync(audio16k)


def main() -> int:
    base_dir = Path(__file__).resolve().parent
    wav_path = base_dir / "test.wav"
    if len(sys.argv) > 1:
        wav_path = Path(sys.argv[1]).expanduser().resolve()

    if not wav_path.exists():
        print(f"missing wav: {wav_path}")
        return 1

    print(f"loading STT via Evelyn main.py: {evelyn_main.STT_MODEL_NAME}")
    evelyn_main.get_stt_model()
    print("ready")
    print("enter = transcribe current wav, q = quit")

    while True:
        try:
            command = input("> ").strip().lower()
        except (EOFError, KeyboardInterrupt):
            print()
            break

        if command in {"q", "quit", "exit"}:
            break

        try:
            started_at = time.perf_counter()
            print("transcribing...")
            text = transcribe_file(wav_path)
            elapsed_ms = (time.perf_counter() - started_at) * 1000.0
            print(text)
            print(f"done ({elapsed_ms:.0f} ms)")
        except Exception as e:
            print(f"error: {e}")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
