from __future__ import annotations

import json
import math
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable

from .text import clean_text


def percentile_p95(values: list[float]) -> float:
    if not values:
        return 0.0
    ordered = sorted(float(value) for value in values)
    idx = max(0, min(len(ordered) - 1, math.ceil(len(ordered) * 0.95) - 1))
    return ordered[idx]


def average(values: list[float]) -> float:
    if not values:
        return 0.0
    return sum(float(value) for value in values) / max(1, len(values))


def average_or_none(values: list[float]) -> float | None:
    if not values:
        return None
    return round(average(values), 1)


def p95_or_none(values: list[float]) -> float | None:
    if not values:
        return None
    return round(percentile_p95(values), 1)


def rate_or_none(count: int, denominator: int | None) -> float | None:
    if not denominator or denominator <= 0:
        return None
    return round(count / max(1, denominator), 4)


def append_bounded_metric(values: list[float], value: float | None, *, limit: int = 200) -> None:
    if value is None:
        return
    values.append(float(value))
    if len(values) > limit:
        del values[: len(values) - limit]


def safe_metric_float(value: Any, default: float = 0.0) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def summarize_turn_path_metrics_payload(turn_path_metrics: dict[str, dict[str, Any]]) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for bucket in turn_path_metrics.values():
        rows.append(
            {
                "turnType": bucket.get("turn_type"),
                "selectedPath": bucket.get("selected_path"),
                "count": int(bucket.get("count", 0)),
                "totalMsP95": round(percentile_p95(bucket.get("total_ms") or []), 1),
                "sttMsP95": round(percentile_p95(bucket.get("stt_ms") or []), 1),
                "mainFirstMsP95": round(percentile_p95(bucket.get("main_first_ms") or []), 1),
                "ttsFirstMsP95": round(percentile_p95(bucket.get("tts_first_ms") or []), 1),
                "playbackMsP95": round(percentile_p95(bucket.get("playback_ms") or []), 1),
            }
        )
    rows.sort(key=lambda row: (-int(row.get("count") or 0), str(row.get("turnType") or "")))
    return rows[:12]


def record_turn_stage_metric(
    turn_stage_metrics: dict[str, dict[str, float]],
    turn_id: str | None,
    stage: str,
    elapsed_ms: float,
) -> None:
    if not turn_id or not stage:
        return
    stages = turn_stage_metrics.setdefault(turn_id, {})
    stages[stage] = float(elapsed_ms)


def mark_turn_stage_from_runtime(
    metrics: dict | None,
    key: str,
    *,
    monotonic: Callable[[], float],
    record_turn_stage: Callable[[str | None, str, float], Any],
    merge_log_event_payload: Callable[..., dict[str, Any]],
    log_turn_event: Callable[..., Any],
    event_name: str | None = None,
    **extra: Any,
) -> None:
    if not metrics:
        return
    started_at = metrics.get("started_at")
    if started_at is None:
        return
    elapsed_ms = (monotonic() - float(started_at)) * 1000.0
    marks = metrics.setdefault("marks", {})
    marks[key] = elapsed_ms
    meta = metrics.get("meta") or {}
    turn_id = meta.get("turn_id")
    if turn_id:
        record_turn_stage(turn_id, key, elapsed_ms)
    if event_name:
        explicit = {
            "turn_id": meta.get("turn_id"),
            "segment_id": meta.get("segment_id"),
            "chunk_index": meta.get("chunk_index"),
            "session_key": meta.get("session_key"),
            "room_session_key": meta.get("room_session_key"),
            "guild_id": meta.get("guild_id"),
            "user_id": meta.get("user_id"),
            "owner_user_id": meta.get("owner_user_id"),
            "source": meta.get("source"),
            "elapsed_ms": elapsed_ms,
        }
        log_turn_event(
            event_name,
            **merge_log_event_payload(explicit=explicit, extra=extra),
        )


def new_turn_metrics_from_runtime(
    *,
    source: str,
    monotonic: Callable[[], float],
    log_turn_event: Callable[..., Any],
    session_key: str | None = None,
    room_session_key: str | None = None,
    guild_id: int | None = None,
    user_id: int | None = None,
    owner_user_id: int | None = None,
    topic_id: str | None = None,
    turn_id: str | None = None,
    segment_id: int | None = None,
    chunk_index: int | None = None,
) -> dict:
    metrics = {
        "started_at": monotonic(),
        "marks": {"t_ingress": 0.0},
        "meta": {
            "source": source,
            "session_key": session_key,
            "guild_id": guild_id,
            "user_id": user_id,
            "owner_user_id": owner_user_id,
            "room_session_key": room_session_key,
            "topic_id": topic_id,
            "turn_id": turn_id,
            "segment_id": segment_id,
            "chunk_index": chunk_index,
        },
    }
    log_turn_event(
        "turn_ingress",
        source=source,
        session_key=session_key,
        guild_id=guild_id,
        user_id=user_id,
        owner_user_id=owner_user_id,
        room_session_key=room_session_key,
        topic_id=topic_id,
        turn_id=turn_id,
        segment_id=segment_id,
        chunk_index=chunk_index,
    )
    return metrics


def register_drop_reason_from_runtime(
    metrics: dict | None,
    reason: str,
    *,
    build_rejected_voice_turn: Callable[..., Any],
    merge_log_event_payload: Callable[..., dict[str, Any]],
    log_turn_event: Callable[..., Any],
    **extra: Any,
) -> None:
    if not metrics:
        return
    meta = metrics.setdefault("meta", {})
    meta["drop_reason"] = reason
    voice_segment = meta.get("voice_segment_contract")
    if voice_segment is not None and meta.get("rejected_turn_contract") is None:
        meta["rejected_turn_contract"] = build_rejected_voice_turn(
            segment=voice_segment,
            ingress_source=str(meta.get("ingress_source") or meta.get("source") or "voice"),
            drop_reason=reason,
            queue_wait_ms=float(meta.get("voice_queue_wait_ms") or 0.0),
            topic_id=meta.get("topic_id"),
            gate_mode=meta.get("reply_gate_blocked_by"),
            owner_user_id=extra.get("owner_user_id") if extra.get("owner_user_id") is not None else meta.get("owner_user_id"),
            detail_text=str(extra.get("text") or extra.get("final_text") or extra.get("wake_probe_text") or ""),
        )
    explicit = {
        "turn_id": meta.get("turn_id"),
        "segment_id": meta.get("segment_id"),
        "chunk_index": meta.get("chunk_index"),
        "session_key": extra.get("session_key") if extra.get("session_key") is not None else meta.get("session_key"),
        "room_session_key": extra.get("room_session_key") if extra.get("room_session_key") is not None else meta.get("room_session_key"),
        "owner_user_id": extra.get("owner_user_id") if extra.get("owner_user_id") is not None else meta.get("owner_user_id"),
        "reason": reason,
    }
    log_turn_event(
        "turn_drop",
        **merge_log_event_payload(explicit=explicit, extra=extra),
    )


def summarize_question_metrics_payload(question_metrics: dict[str, Any]) -> dict[str, Any]:
    total = int(question_metrics.get("turn_count", 0) or 0)
    added = int(question_metrics.get("added_count", 0) or 0)
    removed = int(question_metrics.get("removed_count", 0) or 0)
    cooldown = int(question_metrics.get("cooldown_hit_count", 0) or 0)
    ask_modes = dict(question_metrics.get("ask_modes") or {})
    return {
        "turnCount": total,
        "questionAddedCount": added,
        "questionAddedRate": rate_or_none(added, total),
        "questionRemovedCount": removed,
        "questionCooldownHitCount": cooldown,
        "questionCooldownHitRate": rate_or_none(cooldown, total),
        "finalQuestionCount": int(question_metrics.get("final_question_count", 0) or 0),
        "askModeDistribution": ask_modes,
    }


def record_model_call_trace_from_runtime(
    *,
    model_role: str,
    purpose: str,
    hot_path: bool,
    started_at: float,
    success: bool,
    monotonic: Callable[[], float],
    record_model_call_metric: Callable[..., Any],
    log_turn_event: Callable[..., Any],
    metrics: dict | None = None,
    first_token_ms: float | None = None,
    error: BaseException | str | None = None,
    model_name: str | None = None,
    endpoint: str | None = None,
    turn_id: str | None = None,
    session_key: str | None = None,
    source: str | None = None,
    guild_id: int | None = None,
) -> None:
    meta = (metrics or {}).get("meta") if isinstance(metrics, dict) else {}
    if not isinstance(meta, dict):
        meta = {}
    elapsed_ms = max(0.0, (monotonic() - float(started_at)) * 1000.0)
    error_text = repr(error) if isinstance(error, BaseException) else clean_text(str(error or ""))
    record_model_call_metric(
        model_role=model_role,
        purpose=purpose,
        hot_path=hot_path,
        success=success,
        latency_ms=elapsed_ms,
        first_token_ms=first_token_ms,
    )
    log_turn_event(
        "model_call",
        model_role=clean_text(model_role),
        purpose=clean_text(purpose),
        hot_path=bool(hot_path),
        success=bool(success),
        latency_ms=round(elapsed_ms, 1),
        first_token_ms=None if first_token_ms is None else round(float(first_token_ms), 1),
        model_name=clean_text(model_name or ""),
        endpoint=clean_text(endpoint or ""),
        turn_id=turn_id or meta.get("turn_id"),
        session_key=session_key or meta.get("session_key"),
        source=source or meta.get("source"),
        guild_id=guild_id if guild_id is not None else meta.get("guild_id"),
        error=clean_text(error_text)[:240] if error_text else None,
    )


def record_context_pipeline_benchmark_from_runtime(
    *,
    metrics: dict | None,
    user_text: str,
    answer: str,
    source: str,
    guild_id: int | None,
    session_key: str | None,
    now: Callable[[], float],
    benchmark_log_path: Path,
    project_root: Path,
    log: Callable[[str], Any],
) -> None:
    meta = (metrics or {}).get("meta") if isinstance(metrics, dict) else {}
    context_meta = meta.get("context_pipeline") if isinstance(meta, dict) else None
    if not isinstance(context_meta, dict):
        return
    record = {
        "ts": round(now(), 3),
        "source": clean_text(source),
        "guild_id": guild_id,
        "session_key": session_key,
        "turn_id": meta.get("turn_id") if isinstance(meta, dict) else None,
        "route": context_meta.get("route") or meta.get("route") if isinstance(meta, dict) else None,
        "policy": context_meta.get("policy"),
        "sections": context_meta.get("sections"),
        "section_chars": context_meta.get("section_chars"),
        "minecraft_context": bool(context_meta.get("minecraft_context")),
        "vision_context": "vision" in set(context_meta.get("sections") or []),
        "user_text_len": len(clean_text(user_text)),
        "answer_len": len(clean_text(answer)),
        "marks": {
            key: value
            for key, value in ((metrics or {}).get("marks") or {}).items()
            if key in {"route_ready", "memory_ready", "t_context_build", "llm_done", "t_main_done", "llm_http_ms"}
        },
    }
    try:
        path = benchmark_log_path
        if not path.is_absolute():
            path = project_root / path
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(record, ensure_ascii=False, sort_keys=True) + "\n")
    except Exception as exc:
        log(f"[CONTEXT PIPELINE BENCHMARK] write_failed err={exc!r}")


@dataclass(slots=True)
class ModelCallMetricsStore:
    model_call_metrics: dict[str, dict[str, Any]]
    turn_path_metrics: dict[str, dict[str, Any]]
    summary_events: set[str] | frozenset[str]
    trace_log_dir: Path
    print_fn: Any
    replay_done: bool = False
    replayed_turn_summary_count: int = 0

    def record_model_call(
        self,
        *,
        model_role: str,
        purpose: str,
        hot_path: bool,
        success: bool,
        latency_ms: float,
        first_token_ms: float | None = None,
    ) -> None:
        key = f"{clean_text(model_role)}|{clean_text(purpose)}|{'hot' if hot_path else 'background'}"
        bucket = self.model_call_metrics.setdefault(
            key,
            {
                "model_role": clean_text(model_role),
                "purpose": clean_text(purpose),
                "hot_path": bool(hot_path),
                "count": 0,
                "success_count": 0,
                "error_count": 0,
                "latency_ms": [],
                "first_token_ms": [],
            },
        )
        bucket["count"] = int(bucket.get("count", 0)) + 1
        if success:
            bucket["success_count"] = int(bucket.get("success_count", 0)) + 1
        else:
            bucket["error_count"] = int(bucket.get("error_count", 0)) + 1
        append_bounded_metric(bucket["latency_ms"], latency_ms)
        append_bounded_metric(bucket["first_token_ms"], first_token_ms)

    def replay_model_calls_from_turn_trace(self, *, max_files: int = 7, max_lines_per_file: int = 12000) -> dict[str, int]:
        if not self.trace_log_dir.exists():
            return {"files": 0, "model_calls": 0, "turn_summaries": 0}
        files = sorted(self.trace_log_dir.glob("*.jsonl"), key=lambda path: path.stat().st_mtime, reverse=True)[:max_files]
        rows: list[dict[str, Any]] = []
        min_model_call_ts: float | None = None
        for path in reversed(files):
            try:
                with path.open("r", encoding="utf-8") as handle:
                    for index, line in enumerate(handle):
                        if index >= max_lines_per_file:
                            break
                        try:
                            row = json.loads(line)
                        except Exception:
                            continue
                        if not isinstance(row, dict):
                            continue
                        rows.append(row)
                        if clean_text(str(row.get("event") or "")) == "model_call":
                            row_ts = safe_metric_float(row.get("ts"), default=0.0)
                            if row_ts > 0 and (min_model_call_ts is None or row_ts < min_model_call_ts):
                                min_model_call_ts = row_ts
            except Exception as exc:
                self.print_fn(f"[MODEL CALL METRIC REPLAY] skip path={path} err={exc!r}")

        model_call_count = 0
        turn_summary_count = 0
        for row in rows:
            event = clean_text(str(row.get("event") or ""))
            if event in self.summary_events:
                row_ts = safe_metric_float(row.get("ts"), default=0.0)
                if min_model_call_ts is not None and row_ts >= min_model_call_ts:
                    turn_summary_count += 1
                continue
            if event != "model_call":
                continue
            self.record_model_call(
                model_role=clean_text(str(row.get("model_role") or "unknown")) or "unknown",
                purpose=clean_text(str(row.get("purpose") or "unknown")) or "unknown",
                hot_path=bool(row.get("hot_path")),
                success=bool(row.get("success", True)),
                latency_ms=safe_metric_float(row.get("latency_ms")),
                first_token_ms=None if row.get("first_token_ms") is None else safe_metric_float(row.get("first_token_ms")),
            )
            model_call_count += 1
        self.replayed_turn_summary_count = turn_summary_count
        return {"files": len(files), "model_calls": model_call_count, "turn_summaries": turn_summary_count}

    def ensure_replayed(self) -> None:
        if self.replay_done:
            return
        self.replay_done = True
        if self.model_call_metrics or self.turn_path_metrics:
            return
        result = self.replay_model_calls_from_turn_trace()
        if result.get("files") or result.get("model_calls") or result.get("turn_summaries"):
            self.print_fn(
                "[MODEL CALL METRIC REPLAY] "
                f"files={result.get('files', 0)} model_calls={result.get('model_calls', 0)} turn_summaries={result.get('turn_summaries', 0)}"
            )

    def record_turn_path_summary(self, meta: dict[str, Any], marks: dict[str, Any], total_ms: float) -> None:
        turn_type = clean_text(str(meta.get("turn_type") or "unknown")) or "unknown"
        selected_path = clean_text(str(meta.get("selected_path") or "unknown")) or "unknown"
        key = f"{turn_type}|{selected_path}"
        bucket = self.turn_path_metrics.setdefault(
            key,
            {
                "turn_type": turn_type,
                "selected_path": selected_path,
                "count": 0,
                "total_ms": [],
                "stt_ms": [],
                "main_first_ms": [],
                "tts_first_ms": [],
                "playback_ms": [],
            },
        )
        bucket["count"] = int(bucket.get("count", 0)) + 1
        append_bounded_metric(bucket["total_ms"], total_ms)
        append_bounded_metric(bucket["stt_ms"], marks.get("t_stt_done"))
        append_bounded_metric(bucket["main_first_ms"], marks.get("t_main_first_token"))
        append_bounded_metric(bucket["tts_first_ms"], marks.get("t_tts_first_audio"))
        append_bounded_metric(bucket["playback_ms"], marks.get("t_local_first_playback") or marks.get("t_playback_first_packet"))

    def summarize_turn_paths(self) -> list[dict[str, Any]]:
        return summarize_turn_path_metrics_payload(self.turn_path_metrics)

    def summarize_model_calls(self) -> dict[str, Any]:
        self.ensure_replayed()
        total_turns = self.replayed_turn_summary_count + sum(int(bucket.get("count", 0)) for bucket in self.turn_path_metrics.values())
        rows: list[dict[str, Any]] = []
        for bucket in self.model_call_metrics.values():
            count = int(bucket.get("count", 0))
            latency_values = list(bucket.get("latency_ms") or [])
            first_token_values = list(bucket.get("first_token_ms") or [])
            rows.append(
                {
                    "modelRole": bucket.get("model_role"),
                    "purpose": bucket.get("purpose"),
                    "hotPath": bool(bucket.get("hot_path")),
                    "count": count,
                    "successCount": int(bucket.get("success_count", 0)),
                    "errorCount": int(bucket.get("error_count", 0)),
                    "avgLatencyMs": round(average(latency_values), 1),
                    "p95LatencyMs": round(percentile_p95(latency_values), 1),
                    "firstTokenSampleCount": len(first_token_values),
                    "avgFirstTokenMs": average_or_none(first_token_values),
                    "p95FirstTokenMs": p95_or_none(first_token_values),
                    "callRatePerTurn": rate_or_none(count, total_turns),
                }
            )
        rows.sort(key=lambda row: (-int(row.get("count") or 0), str(row.get("modelRole") or ""), str(row.get("purpose") or "")))

        def matching(*, model_role: str | None = None, purpose: str | None = None, hot_path: bool | None = None) -> list[dict[str, Any]]:
            matched: list[dict[str, Any]] = []
            for row in rows:
                if model_role is not None and row.get("modelRole") != model_role:
                    continue
                if purpose is not None and row.get("purpose") != purpose:
                    continue
                if hot_path is not None and bool(row.get("hotPath")) != bool(hot_path):
                    continue
                matched.append(row)
            return matched

        def sum_count(matched: list[dict[str, Any]]) -> int:
            return sum(int(row.get("count") or 0) for row in matched)

        def combined_latencies(matched: list[dict[str, Any]]) -> list[float]:
            values: list[float] = []
            for row in matched:
                key = f"{row.get('modelRole')}|{row.get('purpose')}|{'hot' if row.get('hotPath') else 'background'}"
                values.extend(float(v) for v in (self.model_call_metrics.get(key, {}).get("latency_ms") or []))
            return values

        def combined_first_tokens(matched: list[dict[str, Any]]) -> list[float]:
            values: list[float] = []
            for row in matched:
                key = f"{row.get('modelRole')}|{row.get('purpose')}|{'hot' if row.get('hotPath') else 'background'}"
                values.extend(float(v) for v in (self.model_call_metrics.get(key, {}).get("first_token_ms") or []))
            return values

        router_route = matching(model_role="router", purpose="route")
        cognitive_hot = matching(model_role="router", purpose="cognitive", hot_path=True)
        summary_hot = matching(model_role="summary", hot_path=True)
        summary_all = matching(model_role="summary")
        main_all = matching(model_role="main", purpose="main_response")
        router_latencies = combined_latencies(router_route)
        main_first_tokens = combined_first_tokens(main_all)
        summary_count = sum_count(summary_all)
        model_call_total_count = sum_count(rows)

        return {
            "turnSummaryCount": total_turns,
            "modelCallCount": model_call_total_count,
            "routerRouteCallCount": sum_count(router_route),
            "routerRouteCallRate": rate_or_none(sum_count(router_route), total_turns),
            "routerAvgLatencyMs": average_or_none(router_latencies),
            "routerP95LatencyMs": p95_or_none(router_latencies),
            "mainResponseCallCount": sum_count(main_all),
            "mainFirstTokenSampleCount": len(main_first_tokens),
            "mainFirstTokenAvgMs": average_or_none(main_first_tokens),
            "mainFirstTokenP95Ms": p95_or_none(main_first_tokens),
            "summaryCallCount": summary_count,
            "summaryHotPathCount": sum_count(summary_hot),
            "summaryHotPathRate": rate_or_none(sum_count(summary_hot), summary_count),
            "cognitiveBlockingCount": sum_count(cognitive_hot),
            "cognitiveBlockingRate": rate_or_none(sum_count(cognitive_hot), total_turns),
            "byPurpose": rows[:16],
        }


def summarize_voice_p95_metrics(
    turn_stage_metrics: dict[str, dict[str, float]],
    *,
    search_followup_queued_count: int,
    cancelled_stale_turn_count: int,
) -> dict[str, float | int]:
    all_stt_ms = [row.get("t_stt_done") for row in turn_stage_metrics.values() if row.get("t_stt_done") is not None]
    all_router_ms = [row.get("route_ready") for row in turn_stage_metrics.values() if row.get("route_ready") is not None]
    all_main_first_token_ms = [
        row.get("t_main_first_token")
        for row in turn_stage_metrics.values()
        if row.get("t_main_first_token") is not None
    ]
    all_tts_first_audio_ms = [
        row.get("t_tts_first_audio")
        for row in turn_stage_metrics.values()
        if row.get("t_tts_first_audio") is not None
    ]
    return {
        "stt_ms_p95": round(percentile_p95(all_stt_ms), 1),
        "router_ms_p95": round(percentile_p95(all_router_ms), 1),
        "main_first_token_ms_p95": round(percentile_p95(all_main_first_token_ms), 1),
        "tts_first_audio_ms_p95": round(percentile_p95(all_tts_first_audio_ms), 1),
        "search_followup_queued_count": int(search_followup_queued_count),
        "cancelled_stale_turn_count": int(cancelled_stale_turn_count),
    }


__all__ = [
    "ModelCallMetricsStore",
    "record_model_call_trace_from_runtime",
    "record_context_pipeline_benchmark_from_runtime",
    "new_turn_metrics_from_runtime",
    "register_drop_reason_from_runtime",
    "record_turn_stage_metric",
    "append_bounded_metric",
    "average",
    "average_or_none",
    "p95_or_none",
    "percentile_p95",
    "rate_or_none",
    "safe_metric_float",
    "summarize_question_metrics_payload",
    "summarize_turn_path_metrics_payload",
    "summarize_voice_p95_metrics",
]
