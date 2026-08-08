from __future__ import annotations

import asyncio
import time
from dataclasses import dataclass
from typing import Any, Awaitable, Callable, MutableMapping

from .voice_utterance import (
    UtteranceAssemblyConfig,
    discord_pcm_seconds,
    merge_debug_meta,
    merge_discord_pcm_segments,
)
from .voice_validation import validation_attempt_binding_is_current


@dataclass(frozen=True)
class VoiceIngressRuntimeDeps:
    voice_ingress_queue: asyncio.Queue[dict[str, Any]]
    voice_utterance_buffers: MutableMapping[str, dict[str, Any]]
    voice_utterance_flush_tasks: MutableMapping[str, asyncio.Task]
    voice_utterance_assembly_config: UtteranceAssemblyConfig
    voice_ingress_max_age_sec: float
    voice_ingress_drop_oldest_on_full: bool
    voice_ingress_queue_max: int
    evaluate_voice_ingress_dequeue: Callable[..., Any]
    apply_voice_ingress_dequeue_debug_meta: Callable[..., Any]
    enqueue_voice_ingress_item: Callable[..., Any]
    increment_voice_pipeline_counter: Callable[[str], Any]
    process_member_audio: Callable[..., Awaitable[Any]]
    create_task: Callable[[Awaitable[Any]], asyncio.Task]
    log: Callable[..., Any] = print
    monotonic: Callable[[], float] = time.monotonic


@dataclass(frozen=True)
class VoiceIngressEntrypointDeps:
    ensure_startup_components_ready: Callable[[], Awaitable[Any]]
    normalize_voice_debug_meta: Callable[[dict[str, Any] | None], dict[str, Any]]
    voice_ingress_source: Callable[[dict[str, Any]], str]
    should_drop_discord_audio_for_local_mic: Callable[..., bool]
    ensure_voice_worker_started: Callable[[], Any]
    build_voice_ingress_context: Callable[..., Any]
    next_segment_id: Callable[[str], int]
    new_turn_id: Callable[[], str]
    room_state_snapshot: Callable[[str], dict[str, Any]]
    validation_context_provider: Callable[..., dict[str, Any] | None]
    build_voice_ingress_item: Callable[..., dict[str, Any]]
    voice_ingress_queue_depth: Callable[[], int]
    schedule_voice_utterance_item: Callable[[dict[str, Any]], Awaitable[Any]]
    monotonic: Callable[[], float] = time.monotonic


def voice_utterance_buffer_key(item: dict[str, Any]) -> str:
    session_key = str(item.get("session_key") or "")
    meta = item.get("debug_meta") if isinstance(item.get("debug_meta"), dict) else {}
    validation_session_id = str(meta.get("validation_session_id") or "")
    validation_step_id = str(meta.get("validation_step_id") or "")
    validation_attempt_id = str(meta.get("validation_attempt_id") or "")
    if validation_session_id and validation_step_id and validation_attempt_id:
        return "|".join(
            (
                session_key,
                validation_session_id,
                validation_step_id,
                validation_attempt_id,
            )
        )
    return session_key


async def process_member_audio_from_runtime(
    member: Any,
    pcm_bytes: bytes,
    debug_meta: dict[str, Any] | None = None,
    *,
    deps: VoiceIngressEntrypointDeps,
) -> None:
    await deps.ensure_startup_components_ready()
    if member is None or getattr(member, "bot", False):
        return
    debug_meta_input = deps.normalize_voice_debug_meta(debug_meta)
    source = deps.voice_ingress_source(debug_meta_input)
    if deps.should_drop_discord_audio_for_local_mic(getattr(member, "id", None), source=source):
        return

    guild = getattr(member, "guild", None)
    if guild is None:
        return

    deps.ensure_voice_worker_started()

    guild_id = guild.id
    voice_channel_id = getattr(getattr(guild.voice_client, "channel", None), "id", None)
    ingress = deps.build_voice_ingress_context(
        guild_id=guild_id,
        voice_channel_id=voice_channel_id,
        user_id=member.id,
    )
    room_session_key = ingress.room_session_key
    session_key = ingress.session_key
    room_key = ingress.room_key
    person_key = ingress.person_key
    session_memory_key = ingress.session_memory_key
    segment_id = deps.next_segment_id(session_key)
    turn_id = deps.new_turn_id()
    room_state = deps.room_state_snapshot(room_session_key)
    for key in (
        "validation_session_id",
        "validation_step_id",
        "validation_attempt",
        "validation_attempt_id",
    ):
        debug_meta_input.pop(key, None)
    validation_context = deps.validation_context_provider(
        surface="discord",
        prefer_interrupt=bool(room_state.get("reply_in_progress")),
    )
    discord_target = (
        validation_context.get("discordTarget")
        if isinstance(validation_context, dict)
        and isinstance(validation_context.get("discordTarget"), dict)
        else {}
    )
    validation_target_matches = bool(
        discord_target
        and str(discord_target.get("guildId") or "") == str(guild_id)
        and str(discord_target.get("channelId") or "") == str(voice_channel_id)
    )
    if validation_context and validation_target_matches:
        debug_meta_input.update(
            {
                "validation_session_id": validation_context.get("sessionId"),
                "validation_step_id": validation_context.get("stepId"),
                "validation_attempt": validation_context.get("attempt"),
                "validation_attempt_id": validation_context.get("attemptId"),
            }
        )
    item = deps.build_voice_ingress_item(
        member=member,
        pcm_bytes=pcm_bytes,
        debug_meta=debug_meta_input,
        session_key=session_key,
        room_session_key=room_session_key,
        room_key=room_key,
        person_key=person_key,
        session_memory_key=session_memory_key,
        turn_id=turn_id,
        segment_id=segment_id,
        ingress_during_reply=bool(room_state.get("reply_in_progress")),
        owner_user_id_on_ingress=room_state.get("owner_user_id"),
        queue_depth_at_enqueue=deps.voice_ingress_queue_depth(),
        enqueued_at=deps.monotonic(),
    )
    await deps.schedule_voice_utterance_item(item)


async def voice_ingress_worker_from_runtime(*, deps: VoiceIngressRuntimeDeps) -> None:
    while True:
        item = await deps.voice_ingress_queue.get()
        try:
            dequeue_plan = deps.evaluate_voice_ingress_dequeue(
                item,
                now_monotonic=deps.monotonic(),
                max_age_sec=deps.voice_ingress_max_age_sec,
                queue_depth_at_dequeue=deps.voice_ingress_queue.qsize(),
            )
            if dequeue_plan.should_drop_stale:
                deps.increment_voice_pipeline_counter("queue_stale_drop_count")
                member = item.get("member")
                deps.apply_voice_ingress_dequeue_debug_meta(item, dequeue_plan)
                deps.log(
                    f"[VOICE QUEUE DROP] reason=stale wait_ms={dequeue_plan.queue_wait_ms:.1f} "
                    f"max_age_ms={dequeue_plan.max_age_ms:.1f} speaker={getattr(member, 'display_name', None)}"
                )
                continue
            deps.apply_voice_ingress_dequeue_debug_meta(item, dequeue_plan)
            if not validation_attempt_binding_is_current(
                item.get("debug_meta"),
                surface="discord",
                reject_unbound_when_active=True,
            ):
                deps.increment_voice_pipeline_counter(
                    "validation_attempt_stale_drop_count"
                )
                deps.log("[VOICE QUEUE DROP] reason=validation_attempt_stale")
                continue
            process_item = dict(item)
            process_item.pop("enqueued_at", None)
            await deps.process_member_audio(**process_item)
        except Exception as exc:
            deps.log(f"[VOICE WORKER] 실패: errorType={type(exc).__name__}")
        finally:
            deps.voice_ingress_queue.task_done()


async def enqueue_voice_ingress_for_processing_from_runtime(
    item: dict[str, Any],
    *,
    deps: VoiceIngressRuntimeDeps,
) -> None:
    debug_meta = item.get("debug_meta")
    if isinstance(debug_meta, dict):
        debug_meta["voice_queue_depth_at_enqueue"] = deps.voice_ingress_queue.qsize()
    item["enqueued_at"] = deps.monotonic()
    enqueue_result = deps.enqueue_voice_ingress_item(
        deps.voice_ingress_queue,
        item,
        drop_oldest_on_full=deps.voice_ingress_drop_oldest_on_full,
    )
    if not enqueue_result.accepted:
        deps.increment_voice_pipeline_counter("queue_full_drop_count")
        member = item.get("member")
        deps.log(
            f"[VOICE QUEUE DROP] reason=queue_full speaker={getattr(member, 'display_name', None)} "
            f"qsize={deps.voice_ingress_queue.qsize()} qmax={deps.voice_ingress_queue_max}"
        )
        return
    dropped = enqueue_result.dropped_oldest_item
    if dropped is not None:
        dropped_member = dropped.get("member") if isinstance(dropped, dict) else None
        deps.log(
            f"[VOICE QUEUE DROP] reason=queue_full_drop_oldest "
            f"speaker={getattr(dropped_member, 'display_name', None)} qmax={deps.voice_ingress_queue_max}"
        )


async def flush_voice_utterance_buffer_from_runtime(
    key: str,
    *,
    deps: VoiceIngressRuntimeDeps,
) -> None:
    buffer = deps.voice_utterance_buffers.pop(key, None)
    deps.voice_utterance_flush_tasks.pop(key, None)
    if not buffer:
        return
    base_item = dict(buffer["base_item"])
    segments = list(buffer.get("segments") or [])
    merged_pcm = merge_discord_pcm_segments(segments, pad_ms=deps.voice_utterance_assembly_config.pad_ms)
    if not merged_pcm:
        return
    segment_count = len(segments)
    base_item["pcm_bytes"] = merged_pcm
    base_item["ingress_during_reply"] = bool(buffer.get("ingress_during_reply"))
    base_item["owner_user_id_on_ingress"] = buffer.get("owner_user_id_on_ingress")
    base_meta = dict(base_item.get("debug_meta") or {})
    base_meta["assembled_segment_ids"] = list(buffer.get("segment_ids") or [])
    base_item["debug_meta"] = merge_debug_meta(
        base_meta,
        segment_count=segment_count,
        added_pad_ms=max(0, segment_count - 1) * deps.voice_utterance_assembly_config.pad_ms,
        total_audio_sec=discord_pcm_seconds(merged_pcm),
    )
    deps.increment_voice_pipeline_counter("utterance_assembly_flush_count")
    if segment_count > 1:
        deps.increment_voice_pipeline_counter("utterance_assembly_merge_count")
        deps.log(f"[VOICE UTTERANCE MERGE] session={key} segments={segment_count} sec={discord_pcm_seconds(merged_pcm):.2f}")
    await enqueue_voice_ingress_for_processing_from_runtime(base_item, deps=deps)


async def delayed_voice_utterance_flush_from_runtime(
    key: str,
    delay_sec: float,
    *,
    deps: VoiceIngressRuntimeDeps,
) -> None:
    try:
        await asyncio.sleep(max(0.0, delay_sec))
        await flush_voice_utterance_buffer_from_runtime(key, deps=deps)
    except asyncio.CancelledError:
        pass


async def schedule_voice_utterance_item_from_runtime(
    item: dict[str, Any],
    *,
    deps: VoiceIngressRuntimeDeps,
) -> None:
    config = deps.voice_utterance_assembly_config
    if not config.enabled or config.commit_wait_sec <= 0.0:
        await enqueue_voice_ingress_for_processing_from_runtime(item, deps=deps)
        return

    key = voice_utterance_buffer_key(item)
    if not key:
        await enqueue_voice_ingress_for_processing_from_runtime(item, deps=deps)
        return

    existing_task = deps.voice_utterance_flush_tasks.pop(key, None)
    if existing_task is not None and not existing_task.done():
        existing_task.cancel()

    pcm_bytes = bytes(item.get("pcm_bytes") or b"")
    buffer = deps.voice_utterance_buffers.get(key)
    if buffer is None:
        buffer = {
            "base_item": dict(item),
            "segments": [],
            "segment_ids": [],
            "ingress_during_reply": bool(item.get("ingress_during_reply")),
            "owner_user_id_on_ingress": item.get("owner_user_id_on_ingress"),
        }
        deps.voice_utterance_buffers[key] = buffer

    buffer["segments"].append(pcm_bytes)
    buffer["segment_ids"].append(item.get("segment_id"))
    buffer["ingress_during_reply"] = bool(buffer.get("ingress_during_reply") or item.get("ingress_during_reply"))
    if buffer.get("owner_user_id_on_ingress") is None:
        buffer["owner_user_id_on_ingress"] = item.get("owner_user_id_on_ingress")

    current_sec = sum(discord_pcm_seconds(segment) for segment in buffer.get("segments") or [])
    if current_sec >= config.max_audio_sec:
        await flush_voice_utterance_buffer_from_runtime(key, deps=deps)
        return

    deps.voice_utterance_flush_tasks[key] = deps.create_task(
        delayed_voice_utterance_flush_from_runtime(key, config.commit_wait_sec, deps=deps)
    )
