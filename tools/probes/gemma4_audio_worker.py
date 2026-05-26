from __future__ import annotations

import argparse
import json
import os
import sys
import time
import traceback
from pathlib import Path
from typing import Any

from transformers import AutoProcessor

from probe_gemma4_audio import (
    DEFAULT_MODEL_ID,
    DEFAULT_PROCESSOR_ID,
    DEFAULT_PROMPT,
    build_messages,
    load_model,
    prepare_inputs,
    resolve_dtype,
)


try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace", line_buffering=True)
    sys.stderr.reconfigure(encoding="utf-8", errors="replace", line_buffering=True)
except Exception:
    pass


READY_PREFIX = "__READY__ "
RESULT_PREFIX = "__RESULT__ "
ERROR_PREFIX = "__ERROR__ "


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Persistent Gemma 4 E4B audio transcription worker."
    )
    parser.add_argument("--model-id", default=DEFAULT_MODEL_ID)
    parser.add_argument("--processor-id", default=DEFAULT_PROCESSOR_ID)
    parser.add_argument("--prompt", default=DEFAULT_PROMPT)
    parser.add_argument(
        "--dtype",
        default="auto",
        choices=["auto", "bfloat16", "float16", "float32"],
    )
    parser.add_argument("--max-new-tokens", type=int, default=256)
    return parser.parse_args()


def protocol(prefix: str, payload: dict[str, Any]) -> None:
    print(prefix + json.dumps(payload, ensure_ascii=False), flush=True)


def transcribe_one(
    *,
    processor,
    model,
    audio_path: Path,
    prompt: str,
    max_new_tokens: int,
) -> tuple[str, dict[str, float]]:
    messages = build_messages(audio_path, prompt)
    p0 = time.perf_counter()
    inputs = prepare_inputs(processor, messages, model.device)
    p1 = time.perf_counter()
    outputs = model.generate(**inputs, max_new_tokens=max_new_tokens)
    g1 = time.perf_counter()

    prompt_len = inputs["input_ids"].shape[-1]
    text = processor.decode(
        outputs[0][prompt_len:],
        skip_special_tokens=True,
    ).strip()
    return text, {"prep_s": p1 - p0, "infer_s": g1 - p1, "total_s": g1 - p0}


def main() -> int:
    args = parse_args()
    print(f"[START] pid={os.getpid()}", flush=True)
    print(f"[LOAD] processor={args.processor_id}", flush=True)
    processor = AutoProcessor.from_pretrained(args.processor_id)
    print("[LOAD] processor=done", flush=True)

    dtype = resolve_dtype(args.dtype)
    print(f"[LOAD] model={args.model_id}", flush=True)
    load_started = time.perf_counter()
    loader_name, model = load_model(args.model_id, dtype)
    load_done = time.perf_counter()
    print(f"[LOAD] loader={loader_name}", flush=True)
    print(f"[LOAD] model_device={model.device}", flush=True)
    protocol(
        READY_PREFIX,
        {
            "ok": True,
            "model_id": args.model_id,
            "processor_id": args.processor_id,
            "loader": loader_name,
            "model_device": str(model.device),
            "load_s": round(load_done - load_started, 3),
        },
    )

    for raw_line in sys.stdin:
        line = raw_line.strip()
        if not line:
            continue
        try:
            request = json.loads(line)
            if request.get("cmd") == "shutdown":
                protocol(RESULT_PREFIX, {"id": request.get("id"), "ok": True, "shutdown": True})
                return 0

            request_id = str(request.get("id") or "")
            audio = request.get("audio")
            if not audio:
                raise ValueError("missing audio")
            audio_path = Path(str(audio)).expanduser().resolve()
            if not audio_path.exists():
                raise FileNotFoundError(f"audio not found: {audio_path}")

            prompt = str(request.get("prompt") or args.prompt)
            max_new_tokens = int(request.get("max_new_tokens") or args.max_new_tokens)
            print(f"[REQUEST] id={request_id} audio={audio_path}", flush=True)
            started = time.perf_counter()
            text, timings = transcribe_one(
                processor=processor,
                model=model,
                audio_path=audio_path,
                prompt=prompt,
                max_new_tokens=max_new_tokens,
            )
            elapsed = time.perf_counter() - started
            protocol(
                RESULT_PREFIX,
                {
                    "id": request_id,
                    "ok": True,
                    "text": text,
                    "elapsed_s": round(elapsed, 3),
                    "timings": {k: round(v, 3) for k, v in timings.items()},
                },
            )
        except Exception as exc:
            payload = {
                "id": request.get("id") if "request" in locals() and isinstance(request, dict) else None,
                "ok": False,
                "error": repr(exc),
                "traceback": traceback.format_exc(),
            }
            protocol(ERROR_PREFIX, payload)

    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except KeyboardInterrupt:
        print("\n[ERROR] interrupted by user", flush=True)
        raise SystemExit(130)
    except Exception as exc:
        print(f"[ERROR] {exc}", flush=True)
        traceback.print_exc()
        raise SystemExit(1)
