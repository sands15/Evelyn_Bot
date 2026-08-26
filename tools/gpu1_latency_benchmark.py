from __future__ import annotations

import argparse
import base64
import hashlib
import json
import math
import os
import socket
import subprocess
import sys
import threading
import time
import wave
from array import array
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from urllib import error, request


REPO_ROOT = next(
    path for path in Path(__file__).resolve().parents if (path / "main.py").exists()
)
TOOLS_ROOT = Path(__file__).resolve().parent
RUNTIME_ROOT = REPO_ROOT / "evelyn_core" / "runtime"
if str(TOOLS_ROOT) not in sys.path:
    sys.path.insert(0, str(TOOLS_ROOT))
if str(RUNTIME_ROOT) not in sys.path:
    sys.path.insert(0, str(RUNTIME_ROOT))

from post_stt_latency_benchmark import extract_llama_timing_metrics  # noqa: E402
from evelyn_core.assistant_prompt_contract import (  # noqa: E402
    FAST_MAIN_LLM_USER_PREFIX,
    build_evelyn_system_prompt,
)

_main_timing_metrics = extract_llama_timing_metrics


DEFAULT_AUDIO = REPO_ROOT / "tools" / "probes" / "sample_input.wav"
DEFAULT_OUTPUT = (
    REPO_ROOT
    / "runtime_artifacts"
    / "benchmarks"
    / "gpu1_concurrency_latest.json"
)
MAIN_PROMPT_CHARS = 938
GPU1_BENCHMARK_AUDIO_SHA256 = (
    "6aa48d50a8a5efed11fcb5b30896c52565c70abed14ed067bf47ca09a3e98d3f"
)
GPU1_CONCURRENCY_REPORT_SCHEMA = "evelyn.gpu1-latency-budget.v1"


def budget_violations(
    report: dict[str, Any],
    *,
    minimum_samples: int = 5,
) -> tuple[str, ...]:
    budgets = report.get("budgets") or {}
    summary = report.get("summary") or {}
    violations: list[str] = []
    if summary.get("sampleCount", 0) < minimum_samples:
        violations.append("insufficient_samples")
    if summary.get("gpuSampleCount", 0) < 1:
        violations.append("gpu_metrics_missing")
    for reason, observed_key, budget_key, direction in (
        ("fast_main_ttft", "fastMainTtftP95Ms", "fastMainTtftP95Ms", "max"),
        ("qwen_latency", "qwenLatencyP95Ms", "qwenTimeoutMs", "max"),
        ("stt_final_latency", "sttFinalLatencyP95Ms", "sttFinalLatencyP95Ms", "max"),
        ("gpu_free_memory", "gpuMinFreeMb", "gpuMinFreeMb", "min"),
    ):
        observed = summary.get(observed_key)
        limit = budgets.get(budget_key)
        if not isinstance(observed, (int, float)) or not isinstance(limit, (int, float)):
            violations.append(f"{reason}_missing")
        elif (direction == "max" and observed > limit) or (
            direction == "min" and observed < limit
        ):
            violations.append(f"{reason}_budget_exceeded")
    for key, reason in (
        ("mainErrorCount", "fast_main_error"),
        ("qwenErrorCount", "qwen_error"),
        ("sttErrorCount", "stt_error"),
        ("gpuErrorCount", "gpu_metrics_error"),
    ):
        if summary.get(key, 1) > 0:
            violations.append(reason)
    if summary.get("qwenTimeoutCount", 1) > budgets.get("qwenTimeoutCountMax", 0):
        violations.append("qwen_timeout_budget_exceeded")
    return tuple(dict.fromkeys(violations))


def percentile_p95(values: list[float]) -> float | None:
    if not values:
        return None
    ordered = sorted(float(value) for value in values)
    return round(ordered[max(0, math.ceil(len(ordered) * 0.95) - 1)], 1)


def _fixed_main_prompt() -> str:
    prompt = "\n".join((build_evelyn_system_prompt(), FAST_MAIN_LLM_USER_PREFIX))
    if len(prompt) != MAIN_PROMPT_CHARS:
        raise RuntimeError("fast_main_prompt_contract_changed")
    return prompt


def _post_json(
    url: str,
    payload: dict[str, Any],
    *,
    timeout_sec: float,
) -> dict[str, Any]:
    req = request.Request(
        url,
        data=json.dumps(payload, separators=(",", ":")).encode("utf-8"),
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    with request.urlopen(req, timeout=max(0.1, timeout_sec)) as response:
        body = json.loads(response.read().decode("utf-8"))
    if not isinstance(body, dict):
        raise RuntimeError("response_not_json_object")
    return body


def _main_ttft(
    start: threading.Event,
    *,
    url: str,
    model: str,
    timeout_sec: float,
) -> dict[str, Any]:
    payload = {
        "model": model,
        "messages": [
            {"role": "system", "content": _fixed_main_prompt()},
            {"role": "user", "content": "한 문장으로 준비됐다고 답해줘."},
        ],
        "temperature": 0,
        "max_tokens": 32,
        "stream": True,
        "cache_prompt": True,
        "timings_per_token": True,
    }
    start.wait()
    started = time.perf_counter()
    try:
        req = request.Request(
            url,
            data=json.dumps(payload, separators=(",", ":")).encode("utf-8"),
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        first_token_ms: float | None = None
        timing_metrics: dict[str, Any] = {}
        with request.urlopen(req, timeout=max(0.1, timeout_sec)) as response:
            for raw_line in response:
                line = raw_line.decode("utf-8", errors="replace").strip()
                if not line.startswith("data:"):
                    continue
                data = line[5:].strip()
                if data == "[DONE]":
                    break
                event_payload = json.loads(data)
                timing_metrics.update(extract_llama_timing_metrics(event_payload))
                choices = event_payload.get("choices") or []
                delta = choices[0].get("delta") if choices else None
                content = delta.get("content") if isinstance(delta, dict) else None
                if content and first_token_ms is None:
                    first_token_ms = (time.perf_counter() - started) * 1000.0
        if first_token_ms is None:
            raise RuntimeError("main_first_token_missing")
        return {
            "ok": True,
            "ttftMs": round(first_token_ms, 1),
            "totalMs": round((time.perf_counter() - started) * 1000.0, 1),
            **timing_metrics,
        }
    except Exception as exc:  # noqa: BLE001 - report stores type only.
        return {"ok": False, "errorType": type(exc).__name__}


def _qwen_specialist(
    start: threading.Event,
    *,
    url: str,
    model: str,
    timeout_sec: float,
) -> dict[str, Any]:
    payload = {
        "model": model,
        "messages": [
            {
                "role": "system",
                "content": (
                    "You are Evelyn's deep-analysis specialist. Return compact evidence, "
                    "not a user-facing answer."
                ),
            },
            {
                "role": "user",
                "content": "Compare two safe implementation options and list three checks.",
            },
        ],
        "temperature": 0,
        "top_p": 0.8,
        "max_tokens": 256,
        "stream": False,
        "chat_template_kwargs": {"enable_thinking": False},
    }
    start.wait()
    started = time.perf_counter()
    try:
        body = _post_json(url, payload, timeout_sec=timeout_sec)
        choices = body.get("choices") or []
        message = choices[0].get("message") if choices else None
        if not isinstance(message, dict) or not str(message.get("content") or "").strip():
            raise RuntimeError("qwen_response_invalid")
        return {
            "ok": True,
            "timedOut": False,
            "latencyMs": round((time.perf_counter() - started) * 1000.0, 1),
        }
    except Exception as exc:  # noqa: BLE001 - report stores type only.
        timed_out = isinstance(exc, (TimeoutError, socket.timeout)) or (
            isinstance(exc, error.URLError)
            and isinstance(getattr(exc, "reason", None), (TimeoutError, socket.timeout))
        )
        return {
            "ok": False,
            "timedOut": timed_out,
            "latencyMs": round((time.perf_counter() - started) * 1000.0, 1),
            "errorType": type(exc).__name__,
        }


def _load_audio(path: Path) -> tuple[dict[str, Any], dict[str, Any]]:
    with wave.open(str(path), "rb") as wav:
        if (
            wav.getnchannels() != 1
            or wav.getsampwidth() != 2
            or wav.getframerate() != 16_000
            or wav.getcomptype() != "NONE"
        ):
            raise ValueError("audio_must_be_pcm16_mono_16khz")
        frames = wav.readframes(wav.getnframes())
        frame_count = wav.getnframes()
    samples = array("h")
    samples.frombytes(frames)
    if sys.byteorder != "little":
        samples.byteswap()
    audio_f32 = array("f", (sample / 32768.0 for sample in samples))
    raw_f32 = audio_f32.tobytes()
    payload = {
        "audio_f32_base64": base64.b64encode(raw_f32).decode("ascii"),
        "sample_count": len(audio_f32),
        "sampling_rate": 16_000,
        "max_new_tokens": 256,
        "stage": "gpu1-concurrency-benchmark",
        "language": "Korean",
        "validation_bound": True,
    }
    metadata = {
        "sha256": hashlib.sha256(frames).hexdigest(),
        "durationMs": round(frame_count / 16_000.0 * 1000.0, 1),
        "sampleCount": frame_count,
    }
    return payload, metadata


def _stt_final(
    start: threading.Event,
    *,
    url: str,
    payload: dict[str, Any],
    timeout_sec: float,
) -> dict[str, Any]:
    start.wait()
    started = time.perf_counter()
    try:
        body = _post_json(url, payload, timeout_sec=timeout_sec)
        if not isinstance(body.get("text"), str):
            raise RuntimeError("stt_response_invalid")
        return {
            "ok": True,
            "latencyMs": round((time.perf_counter() - started) * 1000.0, 1),
            "serviceDurationMs": body.get("durationMs"),
        }
    except Exception as exc:  # noqa: BLE001 - report stores type only.
        return {"ok": False, "errorType": type(exc).__name__}


def _gpu_samples(
    start: threading.Event,
    done: threading.Event,
    *,
    gpu_index: int,
    interval_sec: float,
) -> dict[str, Any]:
    start.wait()
    samples: list[dict[str, float]] = []
    errors = 0
    while True:
        try:
            completed = subprocess.run(
                [
                    "nvidia-smi",
                    "-i",
                    str(gpu_index),
                    "--query-gpu=memory.used,memory.free,memory.total,utilization.gpu",
                    "--format=csv,noheader,nounits",
                ],
                check=True,
                capture_output=True,
                text=True,
                timeout=2.0,
            )
            values = [float(item.strip()) for item in completed.stdout.splitlines()[0].split(",")]
            samples.append(
                {
                    "usedMb": values[0],
                    "freeMb": values[1],
                    "totalMb": values[2],
                    "utilizationPct": values[3],
                }
            )
        except Exception:  # noqa: BLE001 - counter is sufficient and content-free.
            errors += 1
        if done.wait(max(0.01, interval_sec)):
            break
    return {"samples": samples, "errorCount": errors}


def run_iteration(args: argparse.Namespace, stt_payload: dict[str, Any]) -> dict[str, Any]:
    start = threading.Event()
    done = threading.Event()
    with ThreadPoolExecutor(max_workers=4) as pool:
        main_future = pool.submit(
            _main_ttft,
            start,
            url=args.main_url,
            model=args.main_model,
            timeout_sec=args.main_timeout_sec,
        )
        qwen_future = pool.submit(
            _qwen_specialist,
            start,
            url=args.qwen_url,
            model=args.qwen_model,
            timeout_sec=args.qwen_timeout_ms / 1000.0,
        )
        stt_future = pool.submit(
            _stt_final,
            start,
            url=args.stt_url,
            payload=stt_payload,
            timeout_sec=args.stt_timeout_sec,
        )
        gpu_future = pool.submit(
            _gpu_samples,
            start,
            done,
            gpu_index=args.gpu_index,
            interval_sec=args.gpu_sample_interval_ms / 1000.0,
        )
        start.set()
        main_result = main_future.result()
        qwen_result = qwen_future.result()
        stt_result = stt_future.result()
        done.set()
        gpu_result = gpu_future.result()
    return {
        "main": main_result,
        "qwen": qwen_result,
        "stt": stt_result,
        "gpu": gpu_result,
    }


def summarize(iterations: list[dict[str, Any]]) -> dict[str, Any]:
    main_ttft = [row["main"]["ttftMs"] for row in iterations if row["main"].get("ok")]
    main_prompt_eval = [
        row["main"]["promptEvalMs"]
        for row in iterations
        if row["main"].get("ok")
        and isinstance(row["main"].get("promptEvalMs"), (int, float))
    ]
    main_prompt_rate = [
        row["main"]["promptTokensPerSec"]
        for row in iterations
        if row["main"].get("ok")
        and isinstance(row["main"].get("promptTokensPerSec"), (int, float))
    ]
    main_cache_ratio = [
        row["main"]["promptCacheHitRatio"]
        for row in iterations
        if row["main"].get("ok")
        and isinstance(row["main"].get("promptCacheHitRatio"), (int, float))
    ]
    qwen_latency = [row["qwen"]["latencyMs"] for row in iterations if row["qwen"].get("ok")]
    stt_latency = [row["stt"]["latencyMs"] for row in iterations if row["stt"].get("ok")]
    gpu_samples = [sample for row in iterations for sample in row["gpu"]["samples"]]
    return {
        "sampleCount": len(iterations),
        "fastMainTtftP95Ms": percentile_p95(main_ttft),
        "mainPromptEvalP95Ms": percentile_p95(main_prompt_eval),
        "mainPromptTokensPerSecAvg": (
            round(sum(main_prompt_rate) / len(main_prompt_rate), 1)
            if main_prompt_rate
            else None
        ),
        "mainPromptCacheHitRatioMin": min(main_cache_ratio, default=None),
        "mainPromptCacheHitRatioAvg": (
            round(sum(main_cache_ratio) / len(main_cache_ratio), 4)
            if main_cache_ratio
            else None
        ),
        "qwenLatencyP95Ms": percentile_p95(qwen_latency),
        "qwenTimeoutCount": sum(bool(row["qwen"].get("timedOut")) for row in iterations),
        "sttFinalLatencyP95Ms": percentile_p95(stt_latency),
        "gpuPeakUsedMb": max((sample["usedMb"] for sample in gpu_samples), default=None),
        "gpuMinFreeMb": min((sample["freeMb"] for sample in gpu_samples), default=None),
        "gpuPeakUtilizationPct": max((sample["utilizationPct"] for sample in gpu_samples), default=None),
        "gpuSampleCount": len(gpu_samples),
        "mainErrorCount": sum(not row["main"].get("ok") for row in iterations),
        "qwenErrorCount": sum(
            not row["qwen"].get("ok") and not row["qwen"].get("timedOut")
            for row in iterations
        ),
        "sttErrorCount": sum(not row["stt"].get("ok") for row in iterations),
        "gpuErrorCount": sum(row["gpu"]["errorCount"] for row in iterations),
    }


def build_report(
    args: argparse.Namespace,
    *,
    iterations: list[dict[str, Any]],
    audio_metadata: dict[str, Any],
    generated_at: float | None = None,
) -> dict[str, Any]:
    created = time.time() if generated_at is None else float(generated_at)
    budgets = {
        "fastMainTtftP95Ms": float(args.main_ttft_budget_ms),
        "qwenTimeoutMs": float(args.qwen_timeout_ms),
        "qwenTimeoutCountMax": 0,
        "sttFinalLatencyP95Ms": float(args.stt_final_budget_ms),
        "gpuMinFreeMb": float(args.gpu_min_free_mb),
    }
    summary = summarize(iterations)
    candidate = {"budgets": budgets, "summary": summary}
    violations = list(budget_violations(candidate, minimum_samples=5))
    if audio_metadata.get("sha256") != GPU1_BENCHMARK_AUDIO_SHA256:
        violations.append("audio_fixture_mismatch")
    return {
        "schema": GPU1_CONCURRENCY_REPORT_SCHEMA,
        "generatedAt": datetime.fromtimestamp(created, timezone.utc).isoformat(),
        "generatedAtEpochSec": created,
        "status": "pass" if not violations else "fail",
        "scenario": {
            "name": "fast-main-qwen-specialist-stt-overlap",
            "gpuIndex": int(args.gpu_index),
            "warmupIterations": int(args.warmup_iterations),
            "iterations": int(args.iterations),
            "parallelStart": True,
            "mainPromptChars": MAIN_PROMPT_CHARS,
            "mainPromptSha256": hashlib.sha256(
                _fixed_main_prompt().encode("utf-8")
            ).hexdigest(),
            "audio": audio_metadata,
        },
        "budgets": budgets,
        "summary": summary,
        "violations": violations,
        "samples": iterations,
    }


def _write_report(path: Path, report: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.{os.getpid()}.tmp")
    temporary.write_text(
        json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    os.replace(temporary, path)


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Measure concurrent Fast Main, Qwen specialist, STT, and GPU1 budgets."
    )
    parser.add_argument("--main-url", default="http://127.0.0.1:9820/v1/chat/completions")
    parser.add_argument("--main-model", default="gemma-4-12B-it-IQ4_XS-text-only")
    parser.add_argument("--qwen-url", default="http://127.0.0.1:9823/v1/chat/completions")
    parser.add_argument("--qwen-model", default="Qwen3-14B-Q4_K_M.gguf")
    parser.add_argument("--stt-url", default="http://127.0.0.1:8892/v1/stt/transcribe")
    parser.add_argument("--audio", type=Path, default=DEFAULT_AUDIO)
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    parser.add_argument("--gpu-index", type=int, default=1)
    parser.add_argument("--warmup-iterations", type=int, default=1)
    parser.add_argument("--iterations", type=int, default=5)
    parser.add_argument("--gpu-sample-interval-ms", type=float, default=50.0)
    parser.add_argument("--main-timeout-sec", type=float, default=15.0)
    parser.add_argument("--stt-timeout-sec", type=float, default=15.0)
    parser.add_argument("--main-ttft-budget-ms", type=float, default=1_000.0)
    parser.add_argument("--qwen-timeout-ms", type=float, default=6_000.0)
    parser.add_argument("--stt-final-budget-ms", type=float, default=1_200.0)
    parser.add_argument("--gpu-min-free-mb", type=float, default=2_048.0)
    args = parser.parse_args(argv)
    if args.iterations < 1 or args.warmup_iterations < 0:
        parser.error("iterations must be positive and warmup-iterations nonnegative")
    if args.gpu_index != 1 or args.gpu_sample_interval_ms <= 0:
        parser.error("gpu-index must be 1 and sample interval positive")
    return args


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    stt_payload, audio_metadata = _load_audio(args.audio)
    in_progress = build_report(
        args,
        iterations=[],
        audio_metadata=audio_metadata,
    )
    in_progress["status"] = "running"
    in_progress["violations"] = ["benchmark_in_progress"]
    _write_report(args.output, in_progress)
    for _ in range(args.warmup_iterations):
        run_iteration(args, stt_payload)
    iterations = [run_iteration(args, stt_payload) for _ in range(args.iterations)]
    report = build_report(
        args,
        iterations=iterations,
        audio_metadata=audio_metadata,
    )
    _write_report(args.output, report)
    print(
        json.dumps(
            {
                "status": report["status"],
                "summary": report["summary"],
                "violations": report["violations"],
                "output": str(args.output),
            },
            ensure_ascii=False,
        )
    )
    return 0 if report["status"] == "pass" else 2


if __name__ == "__main__":
    raise SystemExit(main())
