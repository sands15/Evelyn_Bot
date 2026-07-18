from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Callable


@dataclass(frozen=True)
class VoiceTimingRuntimeDeps:
    monotonic: Callable[[], float]
    voice_timing_log_threshold_ms: float
    voice_bottleneck_logs: bool
    record_turn_stage: Callable[[str | None, str, float], Any]
    record_turn_path_summary: Callable[[dict[str, Any], dict[str, Any], float], Any]
    summarize_p95_metrics: Callable[[], dict[str, Any]]
    build_turn_summary_payload: Callable[..., dict[str, Any]]
    log_turn_event: Callable[..., Any]
    log: Callable[[str], Any] = print


def build_voice_timing_runtime_deps(
    *,
    monotonic: Callable[[], float],
    voice_timing_log_threshold_ms: float,
    voice_bottleneck_logs: bool,
    record_turn_stage: Callable[[str | None, str, float], Any],
    record_turn_path_summary: Callable[[dict[str, Any], dict[str, Any], float], Any],
    summarize_p95_metrics: Callable[[], dict[str, Any]],
    build_turn_summary_payload: Callable[..., dict[str, Any]],
    log_turn_event: Callable[..., Any],
    log: Callable[[str], Any] = print,
) -> VoiceTimingRuntimeDeps:
    return VoiceTimingRuntimeDeps(
        monotonic=monotonic,
        voice_timing_log_threshold_ms=voice_timing_log_threshold_ms,
        voice_bottleneck_logs=voice_bottleneck_logs,
        record_turn_stage=record_turn_stage,
        record_turn_path_summary=record_turn_path_summary,
        summarize_p95_metrics=summarize_p95_metrics,
        build_turn_summary_payload=build_turn_summary_payload,
        log_turn_event=log_turn_event,
        log=log,
    )


def should_log_voice_timing_from_runtime(elapsed_ms: float, *, deps: VoiceTimingRuntimeDeps) -> bool:
    return elapsed_ms >= deps.voice_timing_log_threshold_ms


def log_voice_latency_from_runtime(
    metrics: dict | None,
    key: str,
    label: str,
    *,
    deps: VoiceTimingRuntimeDeps,
) -> None:
    if not metrics or metrics.get(key):
        return

    started_at = metrics.get("started_at")
    if started_at is None:
        return

    elapsed_ms = (deps.monotonic() - float(started_at)) * 1000.0
    metrics[key] = True
    metrics.setdefault("marks", {})[key] = elapsed_ms
    turn_id = (metrics.get("meta") or {}).get("turn_id")
    deps.record_turn_stage(turn_id, key, elapsed_ms)
    alias_map = {
        "llm_first_chunk_logged": ["t_main_first_token"],
        "tts_first_byte_logged": ["t_tts_first_byte", "t_tts_first_audio"],
        "tts_first_frame_logged": ["t_tts_first_frame"],
        "first_packet_sent_logged": ["t_playback_first_packet"],
        "local_first_playback_logged": ["t_local_first_playback"],
    }
    for alias in alias_map.get(key, []):
        metrics.setdefault("marks", {})[alias] = elapsed_ms
        deps.record_turn_stage(turn_id, alias, elapsed_ms)
    if deps.voice_bottleneck_logs or should_log_voice_timing_from_runtime(elapsed_ms, deps=deps):
        deps.log(
            "[VOICE LATENCY]\n"
            f"label={label}\n"
            f"elapsed_ms={elapsed_ms:.0f}\n"
            f"metric_key={key}"
        )


def log_voice_stage_from_runtime(
    metrics: dict | None,
    label: str,
    *,
    deps: VoiceTimingRuntimeDeps,
    extra: str = "",
    key: str | None = None,
) -> None:
    if not metrics:
        return
    started_at = metrics.get("started_at")
    if started_at is None:
        return
    elapsed_ms = (deps.monotonic() - float(started_at)) * 1000.0
    turn_id = (metrics.get("meta") or {}).get("turn_id")
    if key:
        metrics.setdefault("marks", {})[key] = elapsed_ms
        deps.record_turn_stage(turn_id, key, elapsed_ms)
    stage_alias = {
        "route_ready": "t_policy",
        "memory_ready": "t_context_build",
        "stt_done": "t_stt_done",
        "llm_done": "t_main_done",
    }
    if key and key in stage_alias:
        metrics.setdefault("marks", {})[stage_alias[key]] = elapsed_ms
        deps.record_turn_stage(turn_id, stage_alias[key], elapsed_ms)
    if not (deps.voice_bottleneck_logs or should_log_voice_timing_from_runtime(elapsed_ms, deps=deps)):
        return
    lines = [
        "[VOICE STAGE]",
        f"label={label}",
        f"elapsed_ms={elapsed_ms:.0f}",
    ]
    if key:
        lines.append(f"metric_key={key}")
    if extra:
        lines.append(f"extra={extra}")
    deps.log("\n".join(lines))


def log_voice_bottleneck_summary_from_runtime(
    metrics: dict | None,
    *,
    deps: VoiceTimingRuntimeDeps,
    label: str,
    extra: str = "",
    event_name: str = "turn_summary",
) -> None:
    if not metrics:
        return
    started_at = metrics.get("started_at")
    if started_at is None:
        return

    total_ms = (deps.monotonic() - float(started_at)) * 1000.0
    marks = metrics.get("marks") or {}

    def _fmt(name: str) -> str:
        value = marks.get(name)
        return f"{float(value):.0f}ms" if value is not None else "-"

    meta = metrics.get("meta") or {}
    deps.record_turn_path_summary(meta, marks, total_ms)
    p95_summary = deps.summarize_p95_metrics()
    if deps.voice_bottleneck_logs or should_log_voice_timing_from_runtime(total_ms, deps=deps):
        lines = [
            "[VOICE BOTTLENECK]",
            f"label={label}",
            f"total_ms={total_ms:.0f}",
            f"turn_type={meta.get('turn_type') or '-'}",
            f"selected_path={meta.get('selected_path') or '-'}",
            f"reply_source={meta.get('reply_source') or '-'}",
            f"route={_fmt('route_ready')}",
            f"cognitive={_fmt('cognitive_hotpath_ms')}",
            f"memory={_fmt('memory_ready')}",
            f"wake_probe_ms={_fmt('wake_done')}",
            f"stt={_fmt('stt_done')}",
            f"llm_first={_fmt('llm_first_chunk_logged')}",
            f"llm_done={_fmt('llm_done')}",
            f"tts_req={_fmt('tts_request_logged')}",
            f"tts_headers={_fmt('tts_response_headers_logged')}",
            f"tts_first={_fmt('tts_first_byte_logged')}",
            f"tts_frame={_fmt('tts_first_frame_logged')}",
            f"playback={_fmt('first_packet_sent_logged')}",
            f"local_playback={_fmt('local_first_playback_logged')}",
            f"p95_stt={p95_summary['stt_ms_p95']:.0f}ms",
            f"p95_router={p95_summary['router_ms_p95']:.0f}ms",
            f"p95_main_first={p95_summary['main_first_token_ms_p95']:.0f}ms",
            f"p95_tts_first={p95_summary['tts_first_audio_ms_p95']:.0f}ms",
            f"search_q={p95_summary['search_followup_queued_count']}",
            f"cancelled_turns={p95_summary['cancelled_stale_turn_count']}",
        ]
        if extra:
            lines.append(f"extra={extra}")
        deps.log("\n".join(lines))

    deps.log_turn_event(
        event_name,
        **deps.build_turn_summary_payload(
            metrics,
            label=label,
            event_name=event_name,
            total_ms=round(total_ms, 1),
            p95_summary=p95_summary,
            extra=extra or None,
        ),
    )
