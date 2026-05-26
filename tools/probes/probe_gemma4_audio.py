from __future__ import annotations

import argparse
import os
import time
import traceback
from pathlib import Path

import torch
from transformers import AutoModelForImageTextToText, AutoProcessor

try:
    from transformers import AutoModelForMultimodalLM
except ImportError:  # pragma: no cover - version-dependent availability
    AutoModelForMultimodalLM = None


DEFAULT_MODEL_ID = "ciocan/gemma-4-E4B-it-W4A16"
DEFAULT_PROCESSOR_ID = "google/gemma-4-E4B-it"
DEFAULT_PROMPT = (
    "Transcribe the following speech segment in Korean into Korean text. "
    "Only output the transcription, with no extra explanation."
)


class ProbeLogger:
    def __init__(self, log_file: Path | None):
        self.log_file = log_file
        self.handle = None
        if log_file is not None:
            log_file.parent.mkdir(parents=True, exist_ok=True)
            self.handle = log_file.open("a", encoding="utf-8")

    def emit(self, message: str = "") -> None:
        print(message, flush=True)
        if self.handle is not None:
            self.handle.write(message + "\n")
            self.handle.flush()

    def exception(self, exc: BaseException) -> None:
        self.emit(f"[ERROR] {exc}")
        for line in traceback.format_exc().splitlines():
            self.emit(line)

    def close(self) -> None:
        if self.handle is not None:
            self.handle.close()
            self.handle = None


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Minimal Gemma 4 E4B audio-in -> text-out probe."
    )
    parser.add_argument("--audio", required=True, help="Path to a local audio file")
    parser.add_argument("--model-id", default=DEFAULT_MODEL_ID, help="Model ID to load")
    parser.add_argument(
        "--processor-id",
        default=DEFAULT_PROCESSOR_ID,
        help="Processor ID to load",
    )
    parser.add_argument("--prompt", default=DEFAULT_PROMPT, help="Prompt sent with the audio")
    parser.add_argument(
        "--max-new-tokens",
        type=int,
        default=256,
        help="Maximum generated tokens",
    )
    parser.add_argument(
        "--dtype",
        default="auto",
        choices=["auto", "bfloat16", "float16", "float32"],
        help="Torch dtype used while loading the model",
    )
    parser.add_argument(
        "--log-file",
        help="Optional persistent log file path",
    )
    return parser.parse_args()


def resolve_dtype(name: str):
    if name == "bfloat16":
        return torch.bfloat16
    if name == "float16":
        return torch.float16
    if name == "float32":
        return torch.float32
    return "auto"


def load_model(model_id: str, dtype):
    last_error: Exception | None = None

    loaders = []
    if AutoModelForMultimodalLM is not None:
        loaders.append(("AutoModelForMultimodalLM", AutoModelForMultimodalLM))
    loaders.append(("AutoModelForImageTextToText", AutoModelForImageTextToText))

    for loader_name, loader in loaders:
        try:
            model = loader.from_pretrained(
                model_id,
                torch_dtype=dtype,
                device_map="auto",
            )
            return loader_name, model
        except Exception as exc:  # pragma: no cover - runtime fallback path
            last_error = exc

    assert last_error is not None
    raise last_error


def build_messages(audio_path: Path, prompt: str) -> list[dict]:
    return [
        {
            "role": "user",
            "content": [
                {"type": "audio", "audio": str(audio_path)},
                {"type": "text", "text": prompt},
            ],
        }
    ]


def prepare_inputs(processor, messages: list[dict], model_device):
    inputs = processor.apply_chat_template(
        messages,
        add_generation_prompt=True,
        tokenize=True,
        return_dict=True,
        return_tensors="pt",
    )

    # BatchFeature usually supports .to(), but keep a dict fallback in case
    # some processor return value is slightly different across versions.
    if hasattr(inputs, "to"):
        return inputs.to(model_device)

    moved = {}
    for key, value in inputs.items():
        moved[key] = value.to(model_device) if hasattr(value, "to") else value
    return moved


def main() -> int:
    args = parse_args()
    log_path = Path(args.log_file).expanduser().resolve() if args.log_file else None
    logger = ProbeLogger(log_path)
    audio_path = Path(args.audio).expanduser().resolve()
    try:
        logger.emit(f"[START] pid={os.getpid()}")
        if log_path is not None:
            logger.emit(f"[LOG] file={log_path}")

        if not audio_path.exists():
            logger.emit(f"[ERROR] audio not found: {audio_path}")
            return 1

        model_dtype = resolve_dtype(args.dtype)
        logger.emit(f"[INPUT] audio={audio_path}")
        logger.emit(f"[INPUT] prompt={args.prompt}")
        logger.emit(f"[INPUT] dtype={args.dtype}")

        t0 = time.perf_counter()
        logger.emit(f"[LOAD] processor={args.processor_id}")
        processor = AutoProcessor.from_pretrained(args.processor_id)
        logger.emit("[LOAD] processor=done")

        logger.emit(f"[LOAD] model={args.model_id}")
        loader_name, model = load_model(args.model_id, model_dtype)
        t1 = time.perf_counter()

        logger.emit(f"[LOAD] loader={loader_name}")
        logger.emit(f"[LOAD] model_device={model.device}")

        messages = build_messages(audio_path, args.prompt)
        logger.emit("[PREP] messages=done")
        inputs = prepare_inputs(processor, messages, model.device)
        logger.emit("[PREP] inputs=done")

        g0 = time.perf_counter()
        logger.emit("[GENERATE] start")
        outputs = model.generate(**inputs, max_new_tokens=args.max_new_tokens)
        g1 = time.perf_counter()
        logger.emit("[GENERATE] done")

        prompt_len = inputs["input_ids"].shape[-1]
        answer = processor.decode(
            outputs[0][prompt_len:],
            skip_special_tokens=True,
        ).strip()

        logger.emit(
            f"[TIME] load_s={t1 - t0:.2f} infer_s={g1 - g0:.2f} total_s={g1 - t0:.2f}"
        )
        logger.emit("[TEXT]")
        logger.emit(answer)
        logger.emit("[END] ok")
        return 0
    finally:
        logger.close()


if __name__ == "__main__":
    exit_code = 1
    try:
        exit_code = main()
    except KeyboardInterrupt:
        print("\n[ERROR] interrupted by user", flush=True)
        exit_code = 130
    except Exception as exc:  # pragma: no cover - diagnostic entrypoint
        print(f"[ERROR] {exc}", flush=True)
        traceback.print_exc()
        exit_code = 1
    raise SystemExit(exit_code)
