"""Content-free benchmark process that runs only inside the owned lab network."""

from __future__ import annotations

import json
import hmac
import math
import os
import re
import shutil
import subprocess
import sys
import time
from pathlib import Path
from typing import Any
from urllib import error, request

TOOLS_DIR = Path(__file__).resolve().parent
if str(TOOLS_DIR) not in sys.path:
    sys.path.insert(0, str(TOOLS_DIR))

import post_stt_latency_benchmark as benchmark


SCHEMA = "evelyn.main-latency-lab-batch.v1"
DIRECT_DIAGNOSTIC_SCHEMA = "evelyn.main-latency-direct-diagnostic-batch.v1"
TTS_WARMUP_PROOF_SCHEMA = "evelyn.main-latency-tts-warmup-proof.v1"
_HEX_KEY = re.compile(r"[0-9a-f]{64}\Z", re.ASCII)
_CONDITIONS = frozenset({"baseline", "candidate"})
_PHASES = frozenset({"warm", "restart_ready", "soak"})
_DIRECT_PHASES = frozenset({"cold", "capture", "resident", "afterIdle"})
CACHE_PROOF_SOAK_CADENCE = 25
_MAX_DIAGNOSTIC_MS = 30_000.0
_MAX_PROMPT_TOKENS = 1_000_000
_MAX_TOKENS_PER_SECOND = 1_000_000.0
_MAX_TTS_WARMUP_BYTES = 16 * 1024 * 1024
_TIMING_DIAGNOSTIC_FIELDS = frozenset(benchmark.LLAMA_TIMING_METRIC_NAMES)


def _required_env(name: str) -> str:
    value = os.environ.get(name, "")
    if not value:
        raise ValueError("lab_harness_environment_invalid")
    return value


def _has_external_default_route() -> bool:
    route = Path("/proc/net/route")
    try:
        lines = route.read_text(encoding="ascii").splitlines()[1:]
    except OSError:
        raise RuntimeError("lab_network_route_unavailable") from None
    for line in lines:
        fields = line.split()
        if len(fields) >= 4 and fields[1] == "00000000" and int(fields[3], 16) & 0x2:
            return True
    return False


def _gpu_snapshot(key: bytes) -> tuple[float, str]:
    executable = shutil.which("nvidia-smi")
    if not executable:
        raise RuntimeError("lab_gpu_probe_unavailable")
    memory = subprocess.run(
        (
            executable,
            "--id=0",
            "--query-gpu=memory.free",
            "--format=csv,noheader,nounits",
        ),
        stdout=subprocess.PIPE,
        stderr=subprocess.DEVNULL,
        check=False,
        shell=False,
        timeout=5,
        text=True,
    )
    values = [float(line.strip()) for line in memory.stdout.splitlines() if line.strip()]
    if memory.returncode != 0 or len(values) != 1 or values[0] < 0:
        raise RuntimeError("lab_gpu_probe_failed")
    processes = subprocess.run(
        (
            executable,
            "--id=0",
            "--query-compute-apps=pid",
            "--format=csv,noheader,nounits",
        ),
        stdout=subprocess.PIPE,
        stderr=subprocess.DEVNULL,
        check=False,
        shell=False,
        timeout=5,
        text=True,
    )
    if processes.returncode != 0:
        raise RuntimeError("lab_gpu_probe_failed")
    normalized = "\n".join(sorted(line.strip() for line in processes.stdout.splitlines() if line.strip()))
    return values[0], hmac.digest(key, normalized.encode("utf-8"), "sha256").hex()


def _duration(markers: dict[str, Any], start: str, end: str) -> float:
    before = markers.get(start)
    after = markers.get(end)
    if not isinstance(before, (int, float)) or not isinstance(after, (int, float)):
        raise RuntimeError("lab_trace_incomplete")
    value = float(after) - float(before)
    if value <= 0:
        raise RuntimeError("lab_trace_invalid")
    return round(value, 3)


def _bounded_diagnostic_number(
    value: Any,
    *,
    maximum: float,
    integral: bool = False,
) -> float | int:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise RuntimeError("lab_main_diagnostics_invalid")
    try:
        number = float(value)
    except (OverflowError, ValueError):
        raise RuntimeError("lab_main_diagnostics_invalid") from None
    if not math.isfinite(number) or not 0 <= number <= maximum:
        raise RuntimeError("lab_main_diagnostics_invalid")
    if integral:
        if not number.is_integer():
            raise RuntimeError("lab_main_diagnostics_invalid")
        return int(number)
    return number


def _private_timing_diagnostics(sample: dict[str, Any], trace: dict[str, Any]) -> dict[str, Any]:
    timings = sample.get("llmTimingMetrics")
    if (
        not isinstance(timings, dict)
        or not set(timings).issubset(_TIMING_DIAGNOSTIC_FIELDS)
    ):
        raise RuntimeError("lab_main_diagnostics_invalid")
    result = {
        "llmPromptEvalMs": _bounded_diagnostic_number(
            timings.get("promptEvalMs"), maximum=_MAX_DIAGNOSTIC_MS
        ),
        "llmPromptCacheHitRatio": _bounded_diagnostic_number(
            timings.get("promptCacheHitRatio"), maximum=1.0
        ),
        "llmPromptTokensProcessed": _bounded_diagnostic_number(
            timings.get("promptTokensProcessed"),
            maximum=_MAX_PROMPT_TOKENS,
            integral=True,
        ),
        "llmPromptTokensCached": _bounded_diagnostic_number(
            timings.get("promptTokensCached"),
            maximum=_MAX_PROMPT_TOKENS,
            integral=True,
        ),
        "llmPromptTokensTotal": _bounded_diagnostic_number(
            timings.get("promptTokensTotal"),
            maximum=_MAX_PROMPT_TOKENS,
            integral=True,
        ),
    }
    if (
        result["llmPromptTokensTotal"] < 1
        or result["llmPromptTokensProcessed"]
        + result["llmPromptTokensCached"]
        != result["llmPromptTokensTotal"]
        or not math.isclose(
            result["llmPromptCacheHitRatio"],
            result["llmPromptTokensCached"] / result["llmPromptTokensTotal"],
            rel_tol=0.0,
            abs_tol=0.000051,
        )
    ):
        raise RuntimeError("lab_main_diagnostics_invalid")
    if "queueMs" in timings:
        result["llmQueueMs"] = _bounded_diagnostic_number(
            timings["queueMs"], maximum=_MAX_DIAGNOSTIC_MS
        )
    for source_name, output_name, maximum, integral in (
        ("predictedTokens", "llmPredictedTokens", _MAX_PROMPT_TOKENS, True),
        ("predictedMs", "llmPredictedMs", _MAX_DIAGNOSTIC_MS, False),
        (
            "predictedTokensPerSec",
            "llmPredictedTokensPerSec",
            _MAX_TOKENS_PER_SECOND,
            False,
        ),
    ):
        if source_name in timings:
            result[output_name] = _bounded_diagnostic_number(
                timings[source_name], maximum=maximum, integral=integral
            )

    durations = trace["durations_ms"]
    for output_name, duration_name in (
        ("routeStageMs", "ingress_committed_to_route_done_ms"),
        ("contextStageMs", "route_done_to_context_done_ms"),
    ):
        if duration_name in durations:
            result[output_name] = _bounded_diagnostic_number(
                durations[duration_name], maximum=_MAX_DIAGNOSTIC_MS
            )
    return result


_DIRECT_MODEL_PAYLOAD = {
    "model": "google-gemma-4-12B-it-IQ4_XS.gguf",
    "messages": [
        {
            "role": "system",
            "content": (
                "You are the fixed Evelyn latency diagnostic. "
                "Return one short Korean sentence and no metadata."
            ),
        },
        {
            "role": "user",
            "content": "준비 상태를 한 문장으로만 확인해.",
        },
    ],
    "temperature": 0.0,
    "max_tokens": 4,
    "stream": True,
    "stream_options": {"include_usage": True},
    "cache_prompt": True,
    "timings_per_token": True,
}

_TTS_WARMUP_PAYLOAD = {
    "model": "omnivoice",
    "input": "안녕",
    "voice": "clone:evelyn",
    "response_format": "pcm",
    "stream": True,
    "num_step": 12,
    "language": "ko",
}


def _tts_generate_warmup(url: str) -> dict[str, Any]:
    """Issue one fixed TTS request and prove a bounded full response drain."""

    req = request.Request(
        url,
        data=json.dumps(
            _TTS_WARMUP_PAYLOAD,
            ensure_ascii=True,
            allow_nan=False,
            sort_keys=True,
            separators=(",", ":"),
        ).encode("ascii"),
        headers={
            "Accept": "application/octet-stream",
            "Content-Type": "application/json",
        },
        method="POST",
    )
    try:
        with request.urlopen(req, timeout=60.0) as response:
            if response.status != 200:
                raise RuntimeError("lab_tts_warmup_failed")
            total_bytes = 0
            read_chunk = getattr(response, "read1", None)
            if read_chunk is None:
                read_chunk = response.read
            while True:
                chunk = read_chunk(8192)
                if not chunk:
                    break
                total_bytes += len(chunk)
                if total_bytes > _MAX_TTS_WARMUP_BYTES:
                    raise RuntimeError("lab_tts_warmup_failed")
    except error.HTTPError:
        raise RuntimeError("lab_tts_warmup_failed") from None
    if total_bytes == 0:
        raise RuntimeError("lab_tts_warmup_failed")
    return {
        "schema": TTS_WARMUP_PROOF_SCHEMA,
        "externalDefaultRoute": False,
        "requestCount": 1,
        "fullDrain": True,
        "audioPresent": True,
    }


def _run_tts_warmup() -> dict[str, Any]:
    if _has_external_default_route():
        raise RuntimeError("lab_isolation_preflight_failed")
    tts_url = _required_env("LAB_TTS_URL")
    if tts_url != "http://tts_lab:8880/v1/audio/speech":
        raise ValueError("lab_harness_endpoint_invalid")
    return _tts_generate_warmup(tts_url)


def _direct_backend_sample(url: str, key: bytes) -> dict[str, Any]:
    """Measure a fixed wire payload without retaining generated content."""

    encoded = json.dumps(
        _DIRECT_MODEL_PAYLOAD,
        ensure_ascii=True,
        allow_nan=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("ascii")
    payload_proof = hmac.digest(key, encoded, "sha256").hex()
    req = request.Request(
        url,
        data=encoded,
        headers={
            "Accept": "text/event-stream",
            "Content-Type": "application/json",
            "X-Evelyn-Main-Request-Kind": "interactive",
        },
        method="POST",
    )
    started = time.perf_counter()
    first_token_ms: float | None = None
    timings: dict[str, Any] = {}
    terminal = False
    try:
        with request.urlopen(req, timeout=180.0) as response:
            if response.status != 200:
                raise RuntimeError("lab_direct_backend_failed")
            for raw_line in response:
                line = raw_line.decode("utf-8", errors="ignore").strip()
                if not line or line.startswith(":"):
                    continue
                if line.startswith("data:"):
                    line = line[5:].strip()
                if line == "[DONE]":
                    terminal = True
                    break
                try:
                    event = json.loads(line)
                except json.JSONDecodeError:
                    raise RuntimeError("lab_direct_backend_malformed") from None
                if not isinstance(event, dict):
                    raise RuntimeError("lab_direct_backend_malformed")
                timings.update(benchmark.extract_llama_timing_metrics(event))
                choices = event.get("choices")
                choice = (
                    choices[0]
                    if isinstance(choices, list)
                    and choices
                    and isinstance(choices[0], dict)
                    else {}
                )
                delta = choice.get("delta")
                content = delta.get("content") if isinstance(delta, dict) else None
                if first_token_ms is None and isinstance(content, str) and content:
                    first_token_ms = (time.perf_counter() - started) * 1000.0
                if choice.get("finish_reason") is not None:
                    terminal = True
    except error.HTTPError:
        raise RuntimeError("lab_direct_backend_failed") from None
    if first_token_ms is None or not terminal:
        raise RuntimeError("lab_direct_backend_incomplete")
    required = {
        "promptEvalMs",
        "promptCacheHitRatio",
        "promptTokensProcessed",
        "promptTokensCached",
        "promptTokensTotal",
    }
    if not required.issubset(timings):
        raise RuntimeError("lab_direct_backend_diagnostics_missing")
    processed = _bounded_diagnostic_number(
        timings["promptTokensProcessed"], maximum=_MAX_PROMPT_TOKENS, integral=True
    )
    cached = _bounded_diagnostic_number(
        timings["promptTokensCached"], maximum=_MAX_PROMPT_TOKENS, integral=True
    )
    total = _bounded_diagnostic_number(
        timings["promptTokensTotal"], maximum=_MAX_PROMPT_TOKENS, integral=True
    )
    ratio = _bounded_diagnostic_number(
        timings["promptCacheHitRatio"], maximum=1.0
    )
    if (
        total < 1
        or processed + cached != total
        or not math.isclose(
            ratio,
            cached / total,
            rel_tol=0.0,
            abs_tol=0.000051,
        )
    ):
        raise RuntimeError("lab_direct_backend_diagnostics_invalid")
    return {
        "payloadProof": payload_proof,
        "rawFirstTokenMs": _bounded_diagnostic_number(
            first_token_ms, maximum=_MAX_DIAGNOSTIC_MS
        ),
        "promptEvalMs": _bounded_diagnostic_number(
            timings["promptEvalMs"], maximum=_MAX_DIAGNOSTIC_MS
        ),
        "promptCacheHitRatio": ratio,
        "promptTokensProcessed": processed,
        "promptTokensCached": cached,
        "promptTokensTotal": total,
    }


def _run_direct_diagnostic() -> dict[str, Any]:
    condition = _required_env("LAB_CONDITION")
    phase = _required_env("LAB_PHASE")
    key_hex = _required_env("LAB_EQUIVALENCE_KEY_HEX")
    if condition not in _CONDITIONS or phase not in _DIRECT_PHASES or not _HEX_KEY.fullmatch(key_hex):
        raise ValueError("lab_harness_environment_invalid")
    try:
        count = int(_required_env("LAB_SAMPLE_COUNT"))
    except ValueError:
        raise ValueError("lab_harness_environment_invalid") from None
    if count != 1 or _has_external_default_route():
        raise RuntimeError("lab_isolation_preflight_failed")
    state_url = _required_env("LAB_STATE_URL")
    direct_url = _required_env("LAB_MAIN_DIRECT_URL")
    if (
        state_url != "http://bot_api_lab:8798/api/control-page/state"
        or direct_url
        != "http://main_llm_gateway_lab:9819/v1/chat/completions"
    ):
        raise ValueError("lab_harness_endpoint_invalid")
    benchmark.wait_until_ready(
        state_url,
        "http://tts_lab:8880/health",
        timeout_sec=180.0,
        request_timeout_sec=10.0,
        startup_epoch=None,
        state_ready=_lab_state_ready,
    )
    before_ready = _cache_proof_ready(state_url)
    sample = _direct_backend_sample(direct_url, bytes.fromhex(key_hex))
    after_ready = _cache_proof_ready(state_url)
    return {
        "schema": DIRECT_DIAGNOSTIC_SCHEMA,
        "condition": condition,
        "phase": phase,
        "sampleCount": 1,
        "externalDefaultRoute": False,
        "cacheProofChecks": 2,
        "cacheProofFailures": int(not before_ready) + int(not after_ready),
        "samples": [sample],
    }


def _cache_proof_ready(state_url: str) -> bool:
    try:
        status, state = benchmark._json_request(state_url, timeout_sec=5.0)
    except (OSError, RuntimeError, ValueError, json.JSONDecodeError):
        return False
    runtime = state.get("runtime")
    runtime = runtime if isinstance(runtime, dict) else {}
    warmup = runtime.get("mainWarmup")
    warmup = warmup if isinstance(warmup, dict) else {}
    return status == 200 and warmup.get("cacheProof") is True and _lab_state_ready(state)


def _lab_state_ready(state: dict[str, Any]) -> bool:
    runtime = state.get("runtime")
    runtime = runtime if isinstance(runtime, dict) else {}
    services = runtime.get("services")
    services = services if isinstance(services, dict) else {}
    warmup = runtime.get("mainWarmup")
    warmup = warmup if isinstance(warmup, dict) else {}
    warmup_ready = warmup.get("ready") is True and (
        warmup.get("status") == "not_managed"
        or (
            warmup.get("cacheProof") is True
            and warmup.get("promptAbiProductionMatch") is True
            and (
                warmup.get("promptAbiRequired") is not True
                or warmup.get("promptAbiExact") is True
            )
        )
    )
    return (
        services.get("mainReady") is True
        and services.get("sourceAligned") is True
        and warmup_ready
    )


def _normalize_sample(
    sample: dict[str, Any],
    before_gpu: tuple[float, str],
    after_gpu: tuple[float, str],
) -> dict[str, Any]:
    if not benchmark.sample_has_required_main_diagnostics(sample):
        raise RuntimeError("lab_main_diagnostics_missing")
    trace = benchmark.extract_voice_latency_trace(
        {"latencyTrace": sample.get("latencyTrace")}
    )
    if trace is None:
        raise RuntimeError("lab_main_diagnostics_invalid")
    markers = trace["markers_ms"]
    origin = float(markers["request_received"])
    main_write = float(markers["main_request_written"]) - origin
    prefix_commit = float(markers["speech_prefix_committed"]) - origin
    if main_write <= 0 or prefix_commit <= 0:
        raise RuntimeError("lab_trace_invalid")
    marker_order = (
        "request_received",
        "turn_accepted",
        "route_done",
        "context_done",
        "prompt_compiled",
        "main_admission_requested",
        "main_request_written",
        "main_headers_received",
        "raw_first_token",
        "safe_first_delta",
        "speech_prefix_committed",
    )
    order_violation = int(
        any(float(markers[left]) > float(markers[right]) for left, right in zip(marker_order, marker_order[1:]))
    )
    reply_matches_speech = (
        sample["replyFingerprint"] == sample["ttsInputFingerprint"]
        and int(sample["replyChars"]) == int(sample["ttsInputChars"])
    )
    unsafe_prefix = int(
        float(markers["speech_prefix_committed"]) < float(markers["safe_first_delta"])
    )
    sentence_events = int(sample["eventCounts"].get("sentence", 0))
    error_events = int(sample["eventCounts"].get("error", 0))
    return {
        "postSttMainWriteMs": round(main_write, 3),
        "rawFirstTokenMs": _duration(markers, "main_request_written", "raw_first_token"),
        "rawToSafeSpeechMs": _duration(markers, "raw_first_token", "safe_first_delta"),
        "safePrefixCommitMs": round(prefix_commit, 3),
        "ttsFirstPcmMs": float(sample["ttsFirstPcmMs"]),
        "firstSentenceCommitMs": float(sample["firstSentenceMs"]),
        "answerFirstPcmMs": float(sample["postSttFirstPcmMs"]),
        "replyFingerprint": str(sample["replyFingerprint"]),
        "ttsInputFingerprint": str(sample["ttsInputFingerprint"]),
        "replyChars": int(sample["replyChars"]),
        "ttsInputChars": int(sample["ttsInputChars"]),
        "sentenceEvents": sentence_events,
        "errorEvents": error_events,
        "staleSpeech": int(not reply_matches_speech),
        "unsafePrefix": unsafe_prefix,
        "orderViolation": order_violation,
        "externalInterference": int(before_gpu[1] != after_gpu[1]),
        "safetyFailure": unsafe_prefix,
        "qualityFailure": int(not reply_matches_speech or sentence_events != 1 or error_events != 0),
        "gpuFreeMiB": min(before_gpu[0], after_gpu[0]),
        **_private_timing_diagnostics(sample, trace),
    }


def run() -> dict[str, Any]:
    execution_mode = os.environ.get("LAB_EXECUTION_MODE", "e2e")
    if execution_mode == "tts_warmup":
        return _run_tts_warmup()
    if execution_mode == "direct_backend":
        return _run_direct_diagnostic()
    if execution_mode != "e2e":
        raise ValueError("lab_harness_environment_invalid")
    condition = _required_env("LAB_CONDITION")
    phase = _required_env("LAB_PHASE")
    key_hex = _required_env("LAB_EQUIVALENCE_KEY_HEX")
    if condition not in _CONDITIONS or phase not in _PHASES or not _HEX_KEY.fullmatch(key_hex):
        raise ValueError("lab_harness_environment_invalid")
    try:
        count = int(_required_env("LAB_SAMPLE_COUNT"))
    except ValueError:
        raise ValueError("lab_harness_environment_invalid") from None
    if not 1 <= count <= 1000 or _has_external_default_route():
        raise RuntimeError("lab_isolation_preflight_failed")

    chat_url = _required_env("LAB_CHAT_URL")
    state_url = _required_env("LAB_STATE_URL")
    tts_url = _required_env("LAB_TTS_URL")
    if (chat_url, state_url, tts_url) != (
        "http://bot_api_lab:8798/api/control-page/chat-stream",
        "http://bot_api_lab:8798/api/control-page/state",
        "http://tts_lab:8880/v1/audio/speech",
    ):
        raise ValueError("lab_harness_endpoint_invalid")

    readiness = benchmark.wait_until_ready(
        state_url,
        "http://tts_lab:8880/health",
        timeout_sec=180.0,
        request_timeout_sec=10.0,
        startup_epoch=None,
        state_ready=_lab_state_ready,
    )
    startup_to_ready_ms = readiness.get("startupToReadyMs")
    if startup_to_ready_ms is not None:
        raise RuntimeError("lab_restart_readiness_invalid")
    samples: list[dict[str, Any]] = []
    key = bytes.fromhex(key_hex)
    cache_proof_checks = 0
    cache_proof_failures = 0

    def observe_cache_proof() -> None:
        nonlocal cache_proof_checks, cache_proof_failures
        cache_proof_checks += 1
        cache_proof_failures += int(not _cache_proof_ready(state_url))

    observe_cache_proof()
    for index in range(1, count + 1):
        if phase != "soak" or index == 1 or (index - 1) % CACHE_PROOF_SOAK_CADENCE == 0:
            observe_cache_proof()
        before = _gpu_snapshot(key)
        sample = benchmark.run_once(
            phase=phase,
            index=index,
            chat_url=chat_url,
            tts_url=tts_url,
            prompt=benchmark.DEFAULT_PROMPT,
            source="direct_api",
            num_step=12,
            timeout_sec=180.0,
            fingerprint_key=key,
            first_pcm_only=True,
        )
        samples.append(_normalize_sample(sample, before, _gpu_snapshot(key)))
        if phase != "soak" or index == count or index % CACHE_PROOF_SOAK_CADENCE == 0:
            observe_cache_proof()
    return {
        "schema": SCHEMA,
        "condition": condition,
        "phase": phase,
        "sampleCount": count,
        "externalDefaultRoute": False,
        "cacheProofChecks": cache_proof_checks,
        "cacheProofFailures": cache_proof_failures,
        "startupToReadyMs": startup_to_ready_ms,
        "samples": samples,
    }


def main() -> int:
    try:
        result = run()
    except (OSError, RuntimeError, ValueError):
        return 2
    sys.stdout.write(
        json.dumps(result, ensure_ascii=True, allow_nan=False, sort_keys=True, separators=(",", ":"))
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
