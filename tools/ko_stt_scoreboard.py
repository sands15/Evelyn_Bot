from __future__ import annotations

import argparse
import gc
import json
import os
import re
import shlex
import subprocess
import sys
import time
import unicodedata
import wave
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any


try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass

REPO_ROOT = Path(__file__).resolve().parents[1]
RUNTIME_ROOT = REPO_ROOT / "evelyn_core" / "runtime"
if str(RUNTIME_ROOT) not in sys.path:
    sys.path.insert(0, str(RUNTIME_ROOT))

DEFAULT_GEMMA_MODEL_ID = "ciocan/gemma-4-E4B-it-W4A16"
DEFAULT_GEMMA_PROCESSOR_ID = "google/gemma-4-E4B-it"
DEFAULT_GEMMA_PROMPT = (
    "Transcribe the following speech segment in Korean into Korean text. "
    "Only output the transcription, with no extra explanation."
)
DEFAULT_OUT_DIR = REPO_ROOT / "tmp" / "ko_stt_scoreboard"
DEFAULT_GEMMA_PYTHON = REPO_ROOT / "tools" / "probes" / ".venv-gemma4-probe" / "Scripts" / "python.exe"
GEMMA_PROBE_SCRIPT = REPO_ROOT / "tools" / "probes" / "probe_gemma4_audio.py"
WSL_GEMMA_OVERLAY = "/home/sands12/tmp/openclaw-spikes/gemma4-audio-wsl/overlay"
WSL_GEMMA_NINJA_BIN = "/home/sands12/venvs/gemma4-gptq/bin"
WSL_GEMMA_PROBE_SCRIPT = "/mnt/c/Evelyn/tools/probes/probe_gemma4_audio.py"
WSL_GEMMA_WORKER_SCRIPT = "/mnt/c/Evelyn/tools/probes/gemma4_audio_worker.py"
WORKER_READY_PREFIX = "__READY__ "
WORKER_RESULT_PREFIX = "__RESULT__ "
WORKER_ERROR_PREFIX = "__ERROR__ "


@dataclass
class Score:
    distance: int
    reference_units: int
    error_rate: float
    accuracy: float


@dataclass
class TranscriptResult:
    label: str
    text: str
    elapsed_sec: float
    error: str | None = None


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Record Korean speech and compare current Evelyn STT with ciocan Gemma 4 E4B GPTQ W4A16."
    )
    parser.add_argument("--seconds", type=float, default=5.0, help="Recording length per trial")
    parser.add_argument("--sample-rate", type=int, default=16000, help="Microphone sample rate")
    parser.add_argument("--device", help="sounddevice input device index or name")
    parser.add_argument("--list-devices", action="store_true", help="List sounddevice devices and exit")
    parser.add_argument("--out-dir", default=str(DEFAULT_OUT_DIR), help="Directory for WAV and JSONL results")
    parser.add_argument("--cuda-visible-devices", default="0", help="CUDA_VISIBLE_DEVICES to set before Torch import")
    parser.add_argument("--skip-current-stt", action="store_true", help="Skip current Evelyn STT")
    parser.add_argument("--skip-gemma", action="store_true", help="Skip ciocan Gemma 4 E4B GPTQ W4A16")
    parser.add_argument("--gemma-model-id", default=DEFAULT_GEMMA_MODEL_ID)
    parser.add_argument("--gemma-processor-id", default=DEFAULT_GEMMA_PROCESSOR_ID)
    parser.add_argument("--gemma-prompt", default=DEFAULT_GEMMA_PROMPT)
    parser.add_argument("--gemma-dtype", default="auto", choices=["auto", "bfloat16", "float16", "float32"])
    parser.add_argument("--gemma-runner", default="wsl-overlay", choices=["wsl-overlay", "windows-venv"])
    parser.add_argument("--gemma-python", default=str(DEFAULT_GEMMA_PYTHON), help="Python executable for ciocan Gemma 4 E4B GPTQ W4A16")
    parser.add_argument("--max-new-tokens", type=int, default=256)
    parser.add_argument("--unload-between-models", action="store_true", help="Free model memory after each recognizer")
    parser.add_argument("--score-self-test", action="store_true", help="Run scoring self-test and exit")
    return parser.parse_args()


def normalize_device(device: str | None) -> str | int | None:
    if device is None:
        return None
    token = device.strip()
    if not token:
        return None
    if token.lstrip("-").isdigit():
        return int(token)
    return token


def windows_path_to_wsl(path: Path) -> str:
    resolved = str(path.expanduser().resolve())
    if len(resolved) >= 3 and resolved[1] == ":":
        drive = resolved[0].lower()
        rest = resolved[2:].replace("\\", "/").lstrip("/")
        return f"/mnt/{drive}/{rest}"
    return resolved.replace("\\", "/")


def now_stamp() -> str:
    return datetime.now().strftime("%Y%m%d_%H%M%S")


def normalize_korean_text(text: str, *, keep_spaces: bool) -> str:
    value = unicodedata.normalize("NFKC", text or "").lower()
    value = re.sub(r"[\u200b-\u200f\ufeff]", "", value)
    value = re.sub(r"[^0-9a-z가-힣\s]", "", value)
    value = re.sub(r"\s+", " ", value).strip()
    if not keep_spaces:
        value = value.replace(" ", "")
    return value


def levenshtein(left: list[str] | str, right: list[str] | str) -> int:
    a = list(left)
    b = list(right)
    if not a:
        return len(b)
    if not b:
        return len(a)
    previous = list(range(len(b) + 1))
    for i, ca in enumerate(a, start=1):
        current = [i]
        for j, cb in enumerate(b, start=1):
            current.append(
                min(
                    previous[j] + 1,
                    current[j - 1] + 1,
                    previous[j - 1] + (0 if ca == cb else 1),
                )
            )
        previous = current
    return previous[-1]


def score_units(reference: list[str] | str, hypothesis: list[str] | str) -> Score:
    ref_len = len(reference)
    dist = levenshtein(reference, hypothesis)
    if ref_len <= 0:
        err = 0.0 if len(hypothesis) == 0 else 1.0
    else:
        err = dist / float(ref_len)
    acc = max(0.0, 1.0 - err)
    return Score(distance=dist, reference_units=ref_len, error_rate=err, accuracy=acc)


def score_transcript(gold: str, hypothesis: str) -> dict[str, Any]:
    gold_chars = normalize_korean_text(gold, keep_spaces=False)
    hyp_chars = normalize_korean_text(hypothesis, keep_spaces=False)
    gold_words = normalize_korean_text(gold, keep_spaces=True).split()
    hyp_words = normalize_korean_text(hypothesis, keep_spaces=True).split()
    cer = score_units(gold_chars, hyp_chars)
    wer = score_units(gold_words, hyp_words)
    return {
        "gold_chars": gold_chars,
        "hyp_chars": hyp_chars,
        "gold_words": gold_words,
        "hyp_words": hyp_words,
        "cer": cer.error_rate,
        "wer": wer.error_rate,
        "char_score": round(cer.accuracy * 100.0, 2),
        "word_score": round(wer.accuracy * 100.0, 2),
        "char_distance": cer.distance,
        "word_distance": wer.distance,
        "char_reference_len": cer.reference_units,
        "word_reference_len": wer.reference_units,
    }


def write_wav(path: Path, audio, sample_rate: int) -> None:
    import numpy as np

    path.parent.mkdir(parents=True, exist_ok=True)
    clipped = np.clip(audio, -1.0, 1.0)
    pcm16 = (clipped * 32767.0).astype(np.int16)
    with wave.open(str(path), "wb") as wf:
        wf.setnchannels(1)
        wf.setsampwidth(2)
        wf.setframerate(sample_rate)
        wf.writeframes(pcm16.tobytes())


def record_audio(seconds: float, sample_rate: int, device: str | int | None):
    import numpy as np
    import sounddevice as sd

    frames = max(1, int(round(seconds * sample_rate)))
    print(f"\n[REC] {seconds:.1f}s 녹음 시작. 지금 말하면 된다.", flush=True)
    audio = sd.rec(frames, samplerate=sample_rate, channels=1, dtype="float32", device=device)
    sd.wait()
    audio = np.asarray(audio[:, 0], dtype=np.float32)
    rms = float(np.sqrt(np.mean(np.square(audio)))) if audio.size else 0.0
    peak = float(np.max(np.abs(audio))) if audio.size else 0.0
    print(f"[REC] 완료 rms={rms:.5f} peak={peak:.5f}", flush=True)
    return audio, {"rms": rms, "peak": peak, "seconds": round(audio.size / float(sample_rate), 3)}


def resolve_torch_dtype(name: str):
    import torch

    if name == "bfloat16":
        return torch.bfloat16
    if name == "float16":
        return torch.float16
    if name == "float32":
        return torch.float32
    return "auto"


class CurrentSttRecognizer:
    label = "current_stt"

    def __init__(self, *, max_new_tokens: int):
        self.max_new_tokens = max_new_tokens
        self.model = None
        self.model_name = None

    def load(self) -> None:
        if self.model is not None:
            return
        import torch
        from qwen_asr import Qwen3ASRModel

        from evelyn_core.config import STT_COMPUTE_TYPE, STT_MODEL_NAME

        dtype_map = {
            "float16": torch.float16,
            "fp16": torch.float16,
            "half": torch.float16,
            "bfloat16": torch.bfloat16,
            "bf16": torch.bfloat16,
            "float32": torch.float32,
            "fp32": torch.float32,
            "float": torch.float32,
        }
        dtype = dtype_map.get(str(STT_COMPUTE_TYPE).strip().lower(), torch.float32)
        device = "cuda:0" if torch.cuda.is_available() else "cpu"
        token = os.getenv("HF_TOKEN")
        kwargs: dict[str, Any] = {
            "dtype": dtype,
            "device_map": device,
            "max_inference_batch_size": 1,
            "max_new_tokens": max(self.max_new_tokens, 256),
        }
        if token:
            kwargs["token"] = token
        print(f"[LOAD] current STT model={STT_MODEL_NAME} device={device} dtype={dtype}", flush=True)
        self.model = Qwen3ASRModel.from_pretrained(STT_MODEL_NAME, **kwargs)
        self.model_name = STT_MODEL_NAME
        print("[LOAD] current STT ready", flush=True)

    def unload(self) -> None:
        self.model = None
        gc.collect()
        try:
            import torch

            if torch.cuda.is_available():
                torch.cuda.empty_cache()
        except Exception:
            pass

    def transcribe(self, audio16k, sample_rate: int) -> TranscriptResult:
        import numpy as np

        from evelyn_core.audio import apply_light_denoise, resample_audio_float
        from evelyn_core.config import STT_FORCE_LANGUAGE, STT_LANGUAGE, TARGET_RATE
        from evelyn_core.text import clean_text

        t0 = time.perf_counter()
        try:
            self.load()
            assert self.model is not None
            effective_rate = max(1, int(sample_rate))
            stt_audio = np.asarray(audio16k, dtype=np.float32)
            if effective_rate != TARGET_RATE:
                stt_audio = resample_audio_float(stt_audio, effective_rate, TARGET_RATE)
                effective_rate = TARGET_RATE
            stt_audio = apply_light_denoise(stt_audio, sampling_rate=effective_rate)
            language = "Korean" if STT_FORCE_LANGUAGE and str(STT_LANGUAGE).lower() in {"ko", "kr", "kor", "korean"} else None
            results = self.model.transcribe(
                audio=(stt_audio, effective_rate),
                language=language,
                return_time_stamps=False,
            )
            text = clean_text(getattr(results[0], "text", "") or "") if results else ""
            return TranscriptResult(self.label, text, time.perf_counter() - t0)
        except Exception as exc:
            return TranscriptResult(self.label, "", time.perf_counter() - t0, error=repr(exc))


class GemmaW4A16Recognizer:
    label = "ciocan_gemma4_e4b_gptq_w4a16"

    def __init__(
        self,
        *,
        model_id: str,
        processor_id: str,
        prompt: str,
        dtype_name: str,
        max_new_tokens: int,
        python_exe: str,
        runner: str,
    ):
        self.model_id = model_id
        self.processor_id = processor_id
        self.prompt = prompt
        self.dtype_name = dtype_name
        self.max_new_tokens = max_new_tokens
        self.python_exe = Path(python_exe).expanduser().resolve()
        self.runner = runner
        self.proc: subprocess.Popen[str] | None = None
        self.request_index = 0

    def load(self) -> None:
        if self.runner == "wsl-overlay":
            if self.proc is not None and self.proc.poll() is None:
                return
            env = os.environ.copy()
            env.setdefault("PYTHONUTF8", "1")
            cuda_visible = env.get("CUDA_VISIBLE_DEVICES") or "0"
            shell_command = (
                f"export PYTHONUNBUFFERED=1; "
                f"export PYTHONPATH={shlex.quote(WSL_GEMMA_OVERLAY)}; "
                f"export PATH={shlex.quote(WSL_GEMMA_NINJA_BIN)}:{shlex.quote(WSL_GEMMA_OVERLAY + '/bin')}:$PATH; "
                f"export CUDA_VISIBLE_DEVICES={shlex.quote(cuda_visible)}; "
                f"python3 {shlex.quote(WSL_GEMMA_WORKER_SCRIPT)} "
                f"--model-id {shlex.quote(self.model_id)} "
                f"--processor-id {shlex.quote(self.processor_id)} "
                f"--prompt {shlex.quote(self.prompt)} "
                f"--dtype {shlex.quote(self.dtype_name)} "
                f"--max-new-tokens {int(self.max_new_tokens)}"
            )
            command = ["wsl.exe", "-e", "bash", "-lc", shell_command]
            print(f"[GEMMA] persistent_worker={' '.join(command)}", flush=True)
            self.proc = subprocess.Popen(
                command,
                cwd=str(REPO_ROOT),
                env=env,
                text=True,
                encoding="utf-8",
                errors="replace",
                stdin=subprocess.PIPE,
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                bufsize=1,
            )
            assert self.proc.stdout is not None
            lines: list[str] = []
            while True:
                line = self.proc.stdout.readline()
                if line == "":
                    return_code = self.proc.poll()
                    tail = "\n".join(lines[-12:])
                    self.proc = None
                    raise RuntimeError(f"Gemma worker exited before ready code={return_code}\n{tail}")
                clean = line.rstrip("\r\n")
                lines.append(clean)
                print(clean, flush=True)
                if clean.startswith(WORKER_READY_PREFIX):
                    payload = json.loads(clean[len(WORKER_READY_PREFIX):])
                    if not payload.get("ok"):
                        raise RuntimeError(f"Gemma worker failed to become ready: {payload}")
                    return

        if not self.python_exe.exists():
            raise FileNotFoundError(f"Gemma probe Python not found: {self.python_exe}")
        if not GEMMA_PROBE_SCRIPT.exists():
            raise FileNotFoundError(f"Gemma probe script not found: {GEMMA_PROBE_SCRIPT}")

    def unload(self) -> None:
        if self.proc is None:
            return
        proc = self.proc
        self.proc = None
        try:
            if proc.poll() is None and proc.stdin is not None:
                shutdown_id = f"shutdown-{int(time.time() * 1000)}"
                proc.stdin.write(json.dumps({"id": shutdown_id, "cmd": "shutdown"}) + "\n")
                proc.stdin.flush()
                proc.wait(timeout=8)
        except Exception:
            try:
                proc.terminate()
                proc.wait(timeout=5)
            except Exception:
                try:
                    proc.kill()
                except Exception:
                    pass

    def transcribe(self, audio_path: Path) -> TranscriptResult:
        t0 = time.perf_counter()
        try:
            self.load()
            if self.runner == "wsl-overlay":
                if self.proc is None or self.proc.poll() is not None:
                    raise RuntimeError("Gemma worker is not running")
                if self.proc.stdin is None or self.proc.stdout is None:
                    raise RuntimeError("Gemma worker pipe is not available")
                self.request_index += 1
                request_id = f"gemma-{self.request_index:06d}"
                audio_arg = windows_path_to_wsl(audio_path)
                request = {
                    "id": request_id,
                    "audio": audio_arg,
                    "prompt": self.prompt,
                    "max_new_tokens": self.max_new_tokens,
                }
                self.proc.stdin.write(json.dumps(request, ensure_ascii=False) + "\n")
                self.proc.stdin.flush()
                lines: list[str] = []
                while True:
                    raw_line = self.proc.stdout.readline()
                    if raw_line == "":
                        return_code = self.proc.poll()
                        tail = "\n".join(lines[-12:])
                        self.proc = None
                        return TranscriptResult(
                            self.label,
                            "",
                            time.perf_counter() - t0,
                            error=f"Gemma worker exited code={return_code}\n{tail}",
                        )
                    line = raw_line.rstrip("\r\n")
                    lines.append(line)
                    print(line, flush=True)
                    if line.startswith(WORKER_RESULT_PREFIX):
                        payload = json.loads(line[len(WORKER_RESULT_PREFIX):])
                        if str(payload.get("id")) != request_id:
                            continue
                        if not payload.get("ok"):
                            return TranscriptResult(
                                self.label,
                                "",
                                time.perf_counter() - t0,
                                error=str(payload.get("error") or payload),
                            )
                        return TranscriptResult(
                            self.label,
                            str(payload.get("text") or "").strip(),
                            time.perf_counter() - t0,
                        )
                    if line.startswith(WORKER_ERROR_PREFIX):
                        payload = json.loads(line[len(WORKER_ERROR_PREFIX):])
                        if str(payload.get("id")) != request_id:
                            continue
                        return TranscriptResult(
                            self.label,
                            "",
                            time.perf_counter() - t0,
                            error=str(payload.get("error") or payload),
                        )

            env = os.environ.copy()
            env.setdefault("PYTHONUTF8", "1")
            command = [
                str(self.python_exe),
                str(GEMMA_PROBE_SCRIPT),
                "--audio",
                str(audio_path),
                "--model-id",
                self.model_id,
                "--processor-id",
                self.processor_id,
                "--prompt",
                self.prompt,
                "--dtype",
                self.dtype_name,
                "--max-new-tokens",
                str(self.max_new_tokens),
            ]
            print(f"[GEMMA] subprocess={' '.join(command)}", flush=True)
            proc = subprocess.Popen(
                command,
                cwd=str(REPO_ROOT),
                env=env,
                text=True,
                encoding="utf-8",
                errors="replace",
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
            )
            assert proc.stdout is not None
            lines: list[str] = []
            capture_text = False
            text_lines: list[str] = []
            for raw_line in proc.stdout:
                line = raw_line.rstrip("\r\n")
                lines.append(line)
                print(line, flush=True)
                if line == "[TEXT]":
                    capture_text = True
                    continue
                if capture_text and line.startswith("[END]"):
                    capture_text = False
                    continue
                if capture_text:
                    text_lines.append(line)
            return_code = proc.wait()
            if return_code != 0:
                tail = "\n".join(lines[-12:])
                return TranscriptResult(self.label, "", time.perf_counter() - t0, error=f"Gemma probe exit={return_code}\n{tail}")
            text = "\n".join(text_lines).strip()
            return TranscriptResult(self.label, text, time.perf_counter() - t0)
        except Exception as exc:
            return TranscriptResult(self.label, "", time.perf_counter() - t0, error=repr(exc))


def print_result(label: str, text: str, score: dict[str, Any], elapsed_sec: float, error: str | None) -> None:
    print(f"\n[{label}]", flush=True)
    if error:
        print(f"ERROR: {error}", flush=True)
        return
    print(f"인식: {text}", flush=True)
    print(
        "점수: "
        f"문자 {score['char_score']:.2f}/100 "
        f"(CER {score['cer']:.4f}, edit {score['char_distance']}/{score['char_reference_len']}) | "
        f"단어 {score['word_score']:.2f}/100 "
        f"(WER {score['wer']:.4f}, edit {score['word_distance']}/{score['word_reference_len']}) | "
        f"{elapsed_sec:.2f}s",
        flush=True,
    )


def append_jsonl(path: Path, row: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(row, ensure_ascii=False) + "\n")


def print_running_summary(rows: list[dict[str, Any]]) -> None:
    buckets: dict[str, list[dict[str, Any]]] = {}
    for row in rows:
        for recognizer in row.get("recognizers", []):
            if recognizer.get("error"):
                continue
            score = recognizer.get("score") or {}
            if not score:
                continue
            buckets.setdefault(str(recognizer.get("label")), []).append(score)

    if not buckets:
        return

    print("\n[누적 평균]", flush=True)
    for label, scores in buckets.items():
        char_avg = sum(float(s["char_score"]) for s in scores) / len(scores)
        word_avg = sum(float(s["word_score"]) for s in scores) / len(scores)
        cer_avg = sum(float(s["cer"]) for s in scores) / len(scores)
        wer_avg = sum(float(s["wer"]) for s in scores) / len(scores)
        print(
            f"- {label}: 문자 {char_avg:.2f}/100 CER {cer_avg:.4f} | "
            f"단어 {word_avg:.2f}/100 WER {wer_avg:.4f} | n={len(scores)}",
            flush=True,
        )


def list_devices() -> int:
    import sounddevice as sd

    print(sd.query_devices())
    return 0


def run_score_self_test() -> int:
    score = score_transcript("안녕하세요 오늘 날씨가 좋네요", "안녕하세요 오늘 날씨가 좋내요")
    assert score["char_distance"] == 1, score
    assert score["char_reference_len"] > 0, score
    print("[SELFTEST] scoring ok")
    return 0


def main() -> int:
    args = parse_args()
    if args.score_self_test:
        return run_score_self_test()

    os.environ.setdefault("PYTHONUTF8", "1")
    if args.cuda_visible_devices:
        os.environ["CUDA_VISIBLE_DEVICES"] = str(args.cuda_visible_devices)

    if args.list_devices:
        return list_devices()

    device = normalize_device(args.device)
    out_dir = Path(args.out_dir).expanduser().resolve()
    jsonl_path = out_dir / "results.jsonl"

    current = None if args.skip_current_stt else CurrentSttRecognizer(max_new_tokens=args.max_new_tokens)
    gemma = None if args.skip_gemma else GemmaW4A16Recognizer(
        model_id=args.gemma_model_id,
        processor_id=args.gemma_processor_id,
        prompt=args.gemma_prompt,
        dtype_name=args.gemma_dtype,
        max_new_tokens=args.max_new_tokens,
        python_exe=args.gemma_python,
        runner=args.gemma_runner,
    )

    print("Korean STT Scoreboard", flush=True)
    print(f"- CUDA_VISIBLE_DEVICES={os.environ.get('CUDA_VISIBLE_DEVICES', '')}", flush=True)
    print(f"- output={out_dir}", flush=True)
    print("- Enter: 녹음 시작 / q: 종료", flush=True)
    print("- 녹음 후 같은 CMD에 정답 문장을 입력하면 바로 점수가 나온다.", flush=True)

    trial_index = 0
    completed_rows: list[dict[str, Any]] = []
    while True:
        command = input("\n준비되면 Enter, 종료는 q > ").strip().lower()
        if command in {"q", "quit", "exit"}:
            break
        trial_index += 1
        stamp = now_stamp()
        audio_path = out_dir / f"{stamp}_{trial_index:03d}.wav"
        audio, audio_meta = record_audio(args.seconds, args.sample_rate, device)
        write_wav(audio_path, audio, args.sample_rate)
        print(f"[SAVE] {audio_path}", flush=True)
        gold = input("정답 문장 > ").strip()
        if not gold:
            print("[SKIP] 정답이 비어서 점수 계산을 건너뛴다.", flush=True)
            continue

        results: list[TranscriptResult] = []
        if current is not None:
            result = current.transcribe(audio, args.sample_rate)
            results.append(result)
            if args.unload_between_models:
                current.unload()
        if gemma is not None:
            result = gemma.transcribe(audio_path)
            results.append(result)
            if args.unload_between_models:
                gemma.unload()

        row = {
            "trial": trial_index,
            "created_at": datetime.now().isoformat(timespec="seconds"),
            "audio_path": str(audio_path),
            "audio": audio_meta,
            "gold": gold,
            "recognizers": [],
        }

        print(f"\n정답: {gold}", flush=True)
        for result in results:
            score = score_transcript(gold, result.text) if not result.error else {}
            print_result(result.label, result.text, score, result.elapsed_sec, result.error)
            row["recognizers"].append(
                {
                    "label": result.label,
                    "text": result.text,
                    "elapsed_sec": round(result.elapsed_sec, 3),
                    "error": result.error,
                    "score": score,
                }
            )
        append_jsonl(jsonl_path, row)
        completed_rows.append(row)
        print_running_summary(completed_rows)
        print(f"\n[LOG] {jsonl_path}", flush=True)

    if current is not None:
        current.unload()
    if gemma is not None:
        gemma.unload()
    print("\n종료.", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
