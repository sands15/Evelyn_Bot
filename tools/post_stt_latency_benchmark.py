from __future__ import annotations

import argparse
import hmac
import json
import math
import secrets
import statistics
import sys
import threading
import time
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable
from urllib import error, request
from urllib.parse import urlsplit, urlunsplit


SCHEMA = "evelyn.post-stt-latency.v3"
DEFAULT_PROMPT = "다른 설명 없이 정확히 한 문장으로만 답해: 오늘도 곁에 있을게."
DEFAULT_CHAT_URL = "http://127.0.0.1:8798/api/control-page/chat-stream"
DEFAULT_TTS_URL = "http://127.0.0.1:8880/v1/audio/speech"
LLAMA_TIMING_METRIC_NAMES = (
    "promptTokensProcessed",
    "promptTokensCached",
    "promptTokensTotal",
    "promptCacheHitRatio",
    "promptEvalMs",
    "promptPerTokenMs",
    "promptTokensPerSec",
    "predictedTokens",
    "predictedMs",
    "predictedPerTokenMs",
    "predictedTokensPerSec",
    "queueMs",
)
VOICE_LATENCY_TRACE_SCHEMA = "evelyn.voice-latency-trace.v1"
VOICE_LATENCY_STAGE_NAMES = frozenset(
    {
        "request_received",
        "turn_accepted",
        "ingress_committed",
        "route_done",
        "context_done",
        "prompt_compiled",
        "main_admission_requested",
        "main_slot_acquired",
        "main_request_written",
        "main_headers_received",
        "raw_first_token",
        "safe_first_delta",
        "speech_prefix_committed",
        "tts_requested",
        "tts_started",
        "tts_first_pcm",
        "playback_first_write",
        "turn_completed",
    }
)
_REPORT_FINGERPRINT_KEY = secrets.token_bytes(32)
_ALLOWED_TTS_MEDIA_TYPES = frozenset(
    {"application/octet-stream", "audio/l16", "audio/pcm", "audio/wav"}
)
_REQUIRED_TRACE_MARKERS = frozenset(
    {
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
    }
)
_REQUIRED_TIMING_METRICS = frozenset(
    {
        "promptTokensProcessed",
        "promptTokensCached",
        "promptTokensTotal",
        "promptCacheHitRatio",
        "promptEvalMs",
    }
)


def content_fingerprint(text: str, *, key: bytes | None = None) -> str:
    return hmac.digest(
        key or _REPORT_FINGERPRINT_KEY,
        text.encode("utf-8"),
        "sha256",
    ).hex()


def public_endpoint(url: str) -> str:
    parts = urlsplit(url)
    host = parts.hostname or ""
    if not host:
        raise ValueError("endpoint_host_missing")
    rendered_host = f"[{host}]" if ":" in host else host
    netloc = f"{rendered_host}:{parts.port}" if parts.port is not None else rendered_host
    return urlunsplit((parts.scheme, netloc, parts.path, "", ""))


def public_tts_media_type(value: Any) -> str:
    if not isinstance(value, str):
        return "other"
    media_type = value.split(";", 1)[0].strip().lower()
    return media_type if media_type in _ALLOWED_TTS_MEDIA_TYPES else "other"


def load_equivalence_key(path: Path | None) -> bytes | None:
    if path is None:
        return None
    raw_path = str(path)
    if raw_path.startswith(("\\\\", "//")) or path.is_symlink():
        raise ValueError("equivalence_key_path_invalid")
    try:
        key = path.read_bytes()
    except OSError:
        raise ValueError("equivalence_key_unavailable") from None
    if not 32 <= len(key) <= 64:
        raise ValueError("equivalence_key_invalid")
    return key


def _nonnegative_number(value: Any) -> float | None:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    try:
        number = float(value)
    except (OverflowError, ValueError):
        return None
    return number if math.isfinite(number) and number >= 0 else None


def _nonnegative_count(value: Any) -> int | None:
    number = _nonnegative_number(value)
    if number is None or not number.is_integer():
        return None
    return int(number)


def extract_llama_timing_metrics(event_payload: dict[str, Any]) -> dict[str, Any]:
    """Return only allowlisted numeric llama.cpp timing diagnostics."""

    normalized = event_payload.get("mainTiming")
    if isinstance(normalized, dict):
        metrics: dict[str, Any] = {}
        count_names = {
            "promptTokensProcessed",
            "promptTokensCached",
            "promptTokensTotal",
            "predictedTokens",
        }
        for name in LLAMA_TIMING_METRIC_NAMES:
            value = (
                _nonnegative_count(normalized.get(name))
                if name in count_names
                else _nonnegative_number(normalized.get(name))
            )
            if value is None or (name == "promptCacheHitRatio" and value > 1):
                continue
            metrics[name] = (
                value
                if name in count_names
                else round(value, 4 if name == "promptCacheHitRatio" else 1)
            )
        return metrics

    timings = event_payload.get("timings")
    timings = timings if isinstance(timings, dict) else {}
    usage = event_payload.get("usage")
    usage = usage if isinstance(usage, dict) else {}
    prompt_details = usage.get("prompt_tokens_details")
    prompt_details = prompt_details if isinstance(prompt_details, dict) else {}

    processed = _nonnegative_count(timings.get("prompt_n"))
    cached = _nonnegative_count(timings.get("cache_n"))
    if cached is None:
        cached = _nonnegative_count(prompt_details.get("cached_tokens"))
    total = _nonnegative_count(usage.get("prompt_tokens"))
    if processed is not None and cached is not None:
        total = processed + cached
    elif total is not None and cached is not None and cached <= total:
        processed = total - cached
    elif total is not None and processed is not None and processed <= total:
        cached = total - processed
    predicted = _nonnegative_count(timings.get("predicted_n"))
    if predicted is None:
        predicted = _nonnegative_count(usage.get("completion_tokens"))

    metrics: dict[str, Any] = {}
    for key, value in (
        ("promptTokensProcessed", processed),
        ("promptTokensCached", cached),
        ("promptTokensTotal", total),
        ("predictedTokens", predicted),
    ):
        if value is not None:
            metrics[key] = value
    if total and cached is not None and cached <= total:
        metrics["promptCacheHitRatio"] = round(cached / total, 4)

    for source_key, result_key in (
        ("prompt_ms", "promptEvalMs"),
        ("prompt_per_token_ms", "promptPerTokenMs"),
        ("prompt_per_second", "promptTokensPerSec"),
        ("predicted_ms", "predictedMs"),
        ("predicted_per_token_ms", "predictedPerTokenMs"),
        ("predicted_per_second", "predictedTokensPerSec"),
        ("queue_ms", "queueMs"),
    ):
        value = _nonnegative_number(timings.get(source_key))
        if value is not None:
            metrics[result_key] = round(value, 1)
    return metrics


def extract_voice_latency_trace(event_payload: dict[str, Any]) -> dict[str, Any] | None:
    value = event_payload.get("latencyTrace")
    if not isinstance(value, dict) or value.get("schema") != VOICE_LATENCY_TRACE_SCHEMA:
        return None
    raw_markers = value.get("markers_ms")
    raw_durations = value.get("durations_ms")
    raw_markers = raw_markers if isinstance(raw_markers, dict) else {}
    raw_durations = raw_durations if isinstance(raw_durations, dict) else {}
    markers = {
        stage: round(number, 3)
        for stage in VOICE_LATENCY_STAGE_NAMES
        if (number := _nonnegative_number(raw_markers.get(stage))) is not None
    }
    durations: dict[str, float] = {}
    for key, raw_value in raw_durations.items():
        if not isinstance(key, str) or not key.endswith("_ms") or "_to_" not in key:
            continue
        start, end = key[:-3].split("_to_", 1)
        number = _nonnegative_number(raw_value)
        if start in VOICE_LATENCY_STAGE_NAMES and end in VOICE_LATENCY_STAGE_NAMES and number is not None:
            durations[key] = round(number, 3)
    return {
        "schema": VOICE_LATENCY_TRACE_SCHEMA,
        "markers_ms": markers,
        "durations_ms": durations,
    }


def parse_ndjson_line(raw: bytes | str) -> dict[str, Any] | None:
    text = raw.decode("utf-8") if isinstance(raw, bytes) else raw
    text = text.strip()
    if not text:
        return None
    payload = json.loads(text)
    if not isinstance(payload, dict):
        raise ValueError("ndjson_event_not_object")
    return payload


def nearest_rank(values: list[float], percentile: float) -> float:
    if not values or not 0 < percentile <= 1:
        raise ValueError("invalid_percentile")
    ordered = sorted(values)
    rank = math.ceil(len(ordered) * percentile)
    return ordered[rank - 1]


def stats(values: list[float], *, digits: int = 1) -> dict[str, float]:
    if not values:
        raise ValueError("empty_metric")
    return {
        "min": round(min(values), digits),
        "p50": round(statistics.median(values), digits),
        "p95": round(nearest_rank(values, 0.95), digits),
        "max": round(max(values), digits),
        "mean": round(statistics.fmean(values), digits),
    }


def _origin_url(url: str, path: str) -> str:
    parts = urlsplit(url)
    return urlunsplit((parts.scheme, parts.netloc, path, "", ""))


def _json_request(
    url: str,
    *,
    timeout_sec: float,
    payload: dict[str, Any] | None = None,
) -> tuple[int, dict[str, Any]]:
    req = request.Request(
        url,
        data=(
            json.dumps(payload, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
            if payload is not None
            else None
        ),
        headers={"Content-Type": "application/json"},
        method="POST" if payload is not None else "GET",
    )
    try:
        response = request.urlopen(req, timeout=timeout_sec)
    except error.HTTPError as exc:
        response = exc
    with response:
        body = response.read(1_048_577)
        if len(body) > 1_048_576:
            raise RuntimeError("json_response_too_large")
        parsed = json.loads(body.decode("utf-8"))
        if not isinstance(parsed, dict):
            raise RuntimeError("json_response_not_object")
        return int(response.status), parsed


def _state_ready(state: dict[str, Any]) -> bool:
    runtime = state.get("runtime")
    runtime = runtime if isinstance(runtime, dict) else {}
    services = runtime.get("services")
    services = services if isinstance(services, dict) else {}
    warmup = runtime.get("mainWarmup")
    warmup = warmup if isinstance(warmup, dict) else {}
    return (
        services.get("chatReady") is True
        and warmup.get("cacheProof") is True
        and warmup.get("promptAbiProductionMatch") is True
        and (
            services.get("mainWarmupReady") is True
            or warmup.get("ready") is True
        )
    )


def wait_until_ready(
    state_url: str,
    tts_health_url: str,
    *,
    timeout_sec: float,
    request_timeout_sec: float,
    startup_epoch: float | None,
    state_ready: Callable[[dict[str, Any]], bool] | None = None,
) -> dict[str, Any]:
    started = time.perf_counter()
    deadline = started + timeout_sec
    while True:
        state_ok = tts_ok = False
        try:
            status, state = _json_request(
                state_url,
                timeout_sec=min(request_timeout_sec, 5.0),
            )
            state_ok = status == 200 and (state_ready or _state_ready)(state)
        except (OSError, ValueError, RuntimeError, json.JSONDecodeError):
            pass
        try:
            status, health = _json_request(
                tts_health_url,
                timeout_sec=min(request_timeout_sec, 5.0),
            )
            tts_ok = (
                status == 200
                and health.get("ready") is True
                and health.get("model_loaded") is True
            )
        except (OSError, ValueError, RuntimeError, json.JSONDecodeError):
            pass
        if state_ok and tts_ok:
            ready_epoch = time.time()
            result: dict[str, Any] = {
                "observedWaitMs": round((time.perf_counter() - started) * 1000.0, 1),
                "readyAtUtc": datetime.fromtimestamp(
                    ready_epoch, timezone.utc
                ).isoformat(),
            }
            if startup_epoch is not None:
                if startup_epoch <= 0 or startup_epoch > ready_epoch:
                    raise ValueError("invalid_startup_epoch")
                result["startupToReadyMs"] = round(
                    (ready_epoch - startup_epoch) * 1000.0, 1
                )
            return result
        if time.perf_counter() >= deadline:
            raise RuntimeError("runtime_readiness_timeout")
        time.sleep(min(1.0, max(0.0, deadline - time.perf_counter())))


def _tts_request(
    text: str,
    *,
    url: str,
    num_step: int,
    timeout_sec: float,
    chat_started: float,
    result: dict[str, Any],
    first_pcm_only: bool = False,
) -> None:
    started = time.perf_counter()
    result["requestStartMs"] = round((started - chat_started) * 1000.0, 1)
    payload = {
        "model": "omnivoice",
        "input": text,
        "voice": "clone:evelyn",
        "response_format": "pcm",
        "stream": True,
        "num_step": num_step,
        "language": "ko",
    }
    req = request.Request(
        url,
        data=json.dumps(payload, ensure_ascii=False, separators=(",", ":")).encode(
            "utf-8"
        ),
        headers={
            "Accept": "application/octet-stream",
            "Content-Type": "application/json",
        },
        method="POST",
    )
    try:
        with request.urlopen(req, timeout=timeout_sec) as response:
            if response.status != 200:
                raise RuntimeError(f"tts_http_{response.status}")
            total_bytes = 0
            first_at: float | None = None
            read_chunk = getattr(response, "read1", response.read)
            while True:
                chunk = read_chunk(8192)
                if not chunk:
                    break
                if first_at is None:
                    first_at = time.perf_counter()
                total_bytes += len(chunk)
                if first_pcm_only:
                    break
            if first_at is None or total_bytes == 0:
                raise RuntimeError("tts_audio_missing")
            result.update(
                {
                    "firstPcmMs": round((first_at - started) * 1000.0, 1),
                    "postSttFirstPcmMs": round(
                        (first_at - chat_started) * 1000.0, 1
                    ),
                    "totalMs": round((time.perf_counter() - started) * 1000.0, 1),
                    "bytes": total_bytes,
                    "contentType": public_tts_media_type(
                        response.headers.get("Content-Type", "")
                    ),
                }
            )
    except error.HTTPError as exc:
        result["error"] = f"tts_http_{exc.code}"
    except Exception as exc:  # Error messages may contain response content; keep type only.
        result["error"] = type(exc).__name__


def run_once(
    *,
    phase: str,
    index: int,
    chat_url: str,
    tts_url: str,
    prompt: str,
    source: str,
    num_step: int,
    timeout_sec: float,
    fingerprint_key: bytes | None = None,
    first_pcm_only: bool = False,
) -> dict[str, Any]:
    chat_started = time.perf_counter()
    chat_payload = {
        "text": prompt,
        "source": source,
        "requestId": f"post-stt-benchmark-{uuid.uuid4().hex}",
    }
    req = request.Request(
        chat_url,
        data=json.dumps(
            chat_payload, ensure_ascii=False, separators=(",", ":")
        ).encode("utf-8"),
        headers={
            "Accept": "application/x-ndjson",
            "Content-Type": "application/json",
        },
        method="POST",
    )
    first_delta_ms: float | None = None
    first_sentence_ms: float | None = None
    chat_done_ms: float | None = None
    chat_eof_ms: float | None = None
    reply: str | None = None
    tts_input: str | None = None
    tts_result: dict[str, Any] = {}
    tts_thread: threading.Thread | None = None
    event_counts: dict[str, int] = {}
    llm_timing_metrics: dict[str, Any] = {}
    latency_trace: dict[str, Any] | None = None
    done_seen = False

    try:
        with request.urlopen(req, timeout=timeout_sec) as response:
            if response.status != 200:
                raise RuntimeError(f"chat_http_{response.status}")
            for raw_line in response:
                event = parse_ndjson_line(raw_line)
                if event is None:
                    continue
                event_type = event.get("type")
                if not isinstance(event_type, str) or not event_type:
                    raise RuntimeError("stream_event_type_missing")
                if done_seen:
                    raise RuntimeError("stream_event_after_done")
                event_counts[event_type] = event_counts.get(event_type, 0) + 1
                llm_timing_metrics.update(extract_llama_timing_metrics(event))
                trace_candidate = extract_voice_latency_trace(event)
                if trace_candidate is not None:
                    latency_trace = trace_candidate
                if event_type == "error":
                    raise RuntimeError("chat_stream_error_event")
                if event_type == "delta" and first_delta_ms is None:
                    fragment = event.get("text")
                    if isinstance(fragment, str) and fragment:
                        first_delta_ms = round(
                            (time.perf_counter() - chat_started) * 1000.0, 1
                        )
                elif event_type == "sentence" and first_sentence_ms is None:
                    sentence = event.get("text")
                    if isinstance(sentence, str) and sentence.strip():
                        tts_input = sentence.strip()
                        first_sentence_ms = round(
                            (time.perf_counter() - chat_started) * 1000.0, 1
                        )
                        tts_thread = threading.Thread(
                            target=_tts_request,
                            kwargs={
                                "text": tts_input,
                                "url": tts_url,
                                "num_step": num_step,
                                "timeout_sec": timeout_sec,
                                "chat_started": chat_started,
                                "result": tts_result,
                                "first_pcm_only": first_pcm_only,
                            },
                            daemon=True,
                        )
                        tts_thread.start()
                elif event_type == "done":
                    if event.get("ok") is False or event.get("error"):
                        raise RuntimeError("chat_done_error")
                    reply_value = event.get("reply")
                    if not isinstance(reply_value, str) or not reply_value:
                        raise RuntimeError("chat_reply_missing")
                    reply = reply_value
                    done_seen = True
                    chat_done_ms = round(
                        (time.perf_counter() - chat_started) * 1000.0, 1
                    )
            chat_eof_ms = round(
                (time.perf_counter() - chat_started) * 1000.0, 1
            )
    except error.HTTPError as exc:
        raise RuntimeError(f"chat_http_{exc.code}") from None
    finally:
        if tts_thread is not None:
            tts_thread.join(timeout_sec + 5.0)

    if not event_counts:
        raise RuntimeError("chat_events_missing")
    if first_delta_ms is None:
        raise RuntimeError("chat_delta_missing")
    if first_sentence_ms is None or tts_input is None or tts_thread is None:
        raise RuntimeError("chat_sentence_missing")
    if not done_seen or chat_done_ms is None or chat_eof_ms is None or reply is None:
        raise RuntimeError("chat_done_missing")
    if tts_thread.is_alive():
        raise RuntimeError("tts_thread_timeout")
    if tts_result.get("error"):
        raise RuntimeError(str(tts_result["error"]))
    for key in ("firstPcmMs", "postSttFirstPcmMs", "totalMs", "bytes"):
        if key not in tts_result:
            raise RuntimeError("tts_result_incomplete")

    return {
        "phase": phase,
        "index": index,
        "firstDeltaMs": first_delta_ms,
        "firstSentenceMs": first_sentence_ms,
        "chatDoneMs": chat_done_ms,
        "chatEofMs": chat_eof_ms,
        "ttsRequestStartMs": tts_result["requestStartMs"],
        "ttsFirstPcmMs": tts_result["firstPcmMs"],
        "postSttFirstPcmMs": tts_result["postSttFirstPcmMs"],
        "postSttAllReadyMs": round(
            max(
                chat_eof_ms,
                float(tts_result["requestStartMs"]) + float(tts_result["totalMs"]),
            ),
            1,
        ),
        "ttsTotalMs": tts_result["totalMs"],
        "audioBytes": tts_result["bytes"],
        "ttsContentType": tts_result["contentType"],
        "replyChars": len(reply),
        "replyFingerprint": content_fingerprint(reply, key=fingerprint_key),
        "ttsInputChars": len(tts_input),
        "ttsInputFingerprint": content_fingerprint(
            tts_input,
            key=fingerprint_key,
        ),
        "eventCounts": event_counts,
        "llmTimingMetrics": llm_timing_metrics,
        "latencyTrace": latency_trace,
    }


def _llm_timing_summary(samples: list[dict[str, Any]]) -> dict[str, Any]:
    timing_rows: list[dict[str, Any]] = []
    for sample in samples:
        source = sample.get("llmTimingMetrics")
        if not isinstance(source, dict):
            continue
        row = {
            key: source[key]
            for key in LLAMA_TIMING_METRIC_NAMES
            if key in source and _nonnegative_number(source[key]) is not None
        }
        if row:
            timing_rows.append(row)
    metrics: dict[str, Any] = {}
    for key in LLAMA_TIMING_METRIC_NAMES:
        values = [
            float(row[key])
            for row in timing_rows
            if _nonnegative_number(row.get(key)) is not None
        ]
        if values:
            metrics[key] = {
                "sampleCount": len(values),
                **stats(
                    values,
                    digits=4 if key == "promptCacheHitRatio" else 1,
                ),
            }
    return {
        "availableSampleCount": len(timing_rows),
        "metrics": metrics,
    }


def _voice_latency_trace_summary(samples: list[dict[str, Any]]) -> dict[str, Any]:
    trace_rows: list[dict[str, float]] = []
    for sample in samples:
        trace = extract_voice_latency_trace({"latencyTrace": sample.get("latencyTrace")})
        if trace is None:
            continue
        durations = trace["durations_ms"]
        if durations:
            trace_rows.append(durations)
    metric_names = sorted({key for row in trace_rows for key in row})
    return {
        "availableSampleCount": len(trace_rows),
        "durations": {
            key: {
                "sampleCount": len(values),
                **stats(values, digits=3),
            }
            for key in metric_names
            if (
                values := [
                    float(row[key])
                    for row in trace_rows
                    if _nonnegative_number(row.get(key)) is not None
                ]
            )
        },
    }


def sample_has_required_main_diagnostics(sample: dict[str, Any]) -> bool:
    timings = sample.get("llmTimingMetrics")
    if not isinstance(timings, dict) or not _REQUIRED_TIMING_METRICS.issubset(timings):
        return False
    trace = extract_voice_latency_trace({"latencyTrace": sample.get("latencyTrace")})
    if trace is None or not _REQUIRED_TRACE_MARKERS.issubset(trace["markers_ms"]):
        return False
    return {
        "main_request_written_to_raw_first_token_ms",
        "raw_first_token_to_speech_prefix_committed_ms",
    }.issubset(trace["durations_ms"])


def _summary(samples: list[dict[str, Any]]) -> dict[str, Any]:
    return {
        "sampleCount": len(samples),
        "metrics": {
            key: stats([float(sample[key]) for sample in samples])
            for key in (
                "firstDeltaMs",
                "firstSentenceMs",
                "chatDoneMs",
                "chatEofMs",
                "ttsFirstPcmMs",
                "postSttFirstPcmMs",
                "postSttAllReadyMs",
                "ttsTotalMs",
                "audioBytes",
            )
        },
        "replyFingerprints": sorted(
            {str(sample["replyFingerprint"]) for sample in samples}
        ),
        "replyCharLengths": sorted({int(sample["replyChars"]) for sample in samples}),
        "ttsInputFingerprints": sorted(
            {str(sample["ttsInputFingerprint"]) for sample in samples}
        ),
        "ttsInputCharLengths": sorted(
            {int(sample["ttsInputChars"]) for sample in samples}
        ),
        "llmTimings": _llm_timing_summary(samples),
        "voiceLatencyTrace": _voice_latency_trace_summary(samples),
    }


def _health_metadata(
    chat_url: str,
    state_url: str,
    tts_health_url: str,
    *,
    timeout_sec: float,
) -> dict[str, Any]:
    bot_status, bot = _json_request(
        _origin_url(chat_url, "/health"), timeout_sec=timeout_sec
    )
    state_status, state = _json_request(state_url, timeout_sec=timeout_sec)
    tts_status, tts = _json_request(tts_health_url, timeout_sec=timeout_sec)
    if bot_status != 200 or state_status != 200 or tts_status != 200:
        raise RuntimeError("health_http_non_200")
    runtime = state.get("runtime")
    runtime = runtime if isinstance(runtime, dict) else {}
    allowed_tts = {
        "status",
        "ready",
        "model_loaded",
        "uptime_s",
        "model_id",
        "model_revision",
        "runtime_revision",
        "flashinfer_revision",
        "inference_backend",
        "memory_rss_mb",
        "torch_version",
        "torch_cuda_version",
        "flashinfer_python_version",
        "flashinfer_jit_cache_version",
        "flashinfer_jit_disabled",
        "flashinfer_cuda_graph_buckets",
        "max_concurrent",
        "num_step",
    }
    return {
        "bot": {
            key: bot[key]
            for key in ("ok", "role", "port", "sourceIdentity")
            if key in bot
        },
        "controlState": {
            "ok": state.get("ok"),
            "mode": state.get("mode"),
            "services": runtime.get("services", {}),
            "mainWarmup": runtime.get("mainWarmup", {}),
            "sourceIdentity": runtime.get("sourceIdentity", {}),
        },
        "tts": {key: tts[key] for key in allowed_tts if key in tts},
    }


def run(args: argparse.Namespace) -> dict[str, Any]:
    if (
        args.cold < 0
        or args.warmups < 0
        or args.measured < 1
        or args.num_step < 1
        or args.timeout <= 0
        or args.ready_timeout < 0
    ):
        raise ValueError("invalid_arguments")
    state_url = args.state_url or _origin_url(
        args.chat_url, "/api/control-page/state"
    )
    tts_health_url = _origin_url(args.tts_url, "/health")
    fingerprint_key = load_equivalence_key(args.equivalence_key_file)
    readiness = wait_until_ready(
        state_url,
        tts_health_url,
        timeout_sec=args.ready_timeout,
        request_timeout_sec=args.timeout,
        startup_epoch=args.startup_epoch,
    )
    samples: list[dict[str, Any]] = []
    for phase, count in (
        ("first_admitted_after_warmup", args.cold),
        ("warmup", args.warmups),
        ("measured", args.measured),
    ):
        for index in range(1, count + 1):
            print(f"{phase} {index}/{count}", file=sys.stderr, flush=True)
            samples.append(
                run_once(
                    phase=phase,
                    index=index,
                    chat_url=args.chat_url,
                    tts_url=args.tts_url,
                    prompt=args.prompt,
                    source=args.source,
                    num_step=args.num_step,
                    timeout_sec=args.timeout,
                    fingerprint_key=fingerprint_key,
                )
            )
    first_admitted = [
        sample
        for sample in samples
        if sample["phase"] == "first_admitted_after_warmup"
    ]
    measured = [sample for sample in samples if sample["phase"] == "measured"]
    measured_summary = _summary(measured)
    if (
        len(measured_summary["replyFingerprints"]) != 1
        or len(measured_summary["replyCharLengths"]) != 1
        or len(measured_summary["ttsInputFingerprints"]) != 1
        or any(sample["eventCounts"].get("sentence") != 1 for sample in measured)
        or any(
            sample["replyFingerprint"] != sample["ttsInputFingerprint"]
            for sample in measured
        )
    ):
        raise RuntimeError("measured_response_contract_changed")
    if any(not sample_has_required_main_diagnostics(sample) for sample in measured):
        raise RuntimeError("measured_main_diagnostics_missing")
    return {
        "schema": SCHEMA,
        "generatedAtUtc": datetime.now(timezone.utc).isoformat(),
        "scope": {
            "start": "Bot API NDJSON request",
            "end": "first nonempty OmniVoice PCM response bytes",
            "excluded": [
                "STT",
                "microphone",
                "speaker playback",
                "Discord",
                "Control Page proxy",
                "Vision",
                "Minecraft",
            ],
        },
        "config": {
            "chatUrl": public_endpoint(args.chat_url),
            "stateUrl": public_endpoint(state_url),
            "ttsUrl": public_endpoint(args.tts_url),
            "source": args.source,
            "firstAdmittedAfterWarmup": args.cold,
            "firstAdmittedMeaning": (
                "first admitted request after production startup warmup; not process-cold"
            ),
            "equivalenceFingerprintScope": (
                "shared_secret" if fingerprint_key is not None else "process_ephemeral"
            ),
            "discardedWarmups": args.warmups,
            "measured": args.measured,
            "numStep": args.num_step,
            "ttsModel": "omnivoice",
            "voice": "clone:evelyn",
            "language": "ko",
            "responseFormat": "pcm",
            "stream": True,
            "p50Method": "median",
            "p95Method": "nearest-rank",
        },
        "prompt": {
            "chars": len(args.prompt),
            "fingerprint": content_fingerprint(
                args.prompt,
                key=fingerprint_key,
            ),
        },
        "readiness": readiness,
        "health": _health_metadata(
            args.chat_url,
            state_url,
            tts_health_url,
            timeout_sec=args.timeout,
        ),
        "summary": {
            "firstAdmittedAfterWarmup": (
                _summary(first_admitted) if first_admitted else None
            ),
            "measured": measured_summary,
        },
        "samples": samples,
    }


def self_test() -> None:
    assert nearest_rank([4.0, 1.0, 3.0, 2.0], 0.95) == 4.0
    assert stats([1.0, 2.0, 3.0, 4.0])["p50"] == 2.5
    assert parse_ndjson_line(b' {"type":"delta","text":"x"}\n') == {
        "type": "delta",
        "text": "x",
    }
    assert parse_ndjson_line(" \n") is None
    try:
        parse_ndjson_line("[]")
    except ValueError as exc:
        assert str(exc) == "ndjson_event_not_object"
    else:
        raise AssertionError("non-object NDJSON was accepted")


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Measure Bot API to OmniVoice first-PCM latency (STT excluded)."
    )
    parser.add_argument("--chat-url", default=DEFAULT_CHAT_URL)
    parser.add_argument("--state-url", default="")
    parser.add_argument("--tts-url", default=DEFAULT_TTS_URL)
    parser.add_argument("--prompt", default=DEFAULT_PROMPT)
    parser.add_argument("--source", default="direct_api")
    parser.add_argument(
        "--cold",
        type=int,
        default=1,
        help="Legacy count name: first admitted samples after readiness warmup.",
    )
    parser.add_argument("--warmups", type=int, default=3)
    parser.add_argument("--measured", type=int, default=10)
    parser.add_argument("--num-step", type=int, default=12)
    parser.add_argument("--timeout", type=float, default=180.0)
    parser.add_argument("--ready-timeout", type=float, default=600.0)
    parser.add_argument("--startup-epoch", type=float)
    parser.add_argument(
        "--equivalence-key-file",
        type=Path,
        help=(
            "Optional local 32-64 byte HMAC key shared by baseline/candidate runs; "
            "the key and path are never written to the report."
        ),
    )
    parser.add_argument("--report", type=Path)
    parser.add_argument("--self-test", action="store_true")
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    if args.self_test:
        self_test()
        print("self-test: ok")
        return 0
    try:
        report = run(args)
        rendered = json.dumps(report, ensure_ascii=False, indent=2) + "\n"
        if args.report:
            args.report.parent.mkdir(parents=True, exist_ok=True)
            args.report.write_text(rendered, encoding="utf-8")
            measured = report["summary"]["measured"]["metrics"]
            print(
                json.dumps(
                    {
                        "report": str(args.report),
                        "postSttFirstPcmMs": measured["postSttFirstPcmMs"],
                    },
                    ensure_ascii=False,
                )
            )
        else:
            print(rendered, end="")
        return 0
    except Exception as exc:
        print(f"benchmark failed: {type(exc).__name__}: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
