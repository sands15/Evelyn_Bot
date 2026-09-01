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


_VOICE_TRANSITION_GUILD_IDS: set[int] = set()


def _voice_ingress_guild_id(value: Any) -> int | None:
    if isinstance(value, bool) or not isinstance(value, int):
        return None
    return value if value >= 0 else None


def current_voice_ingress_epoch(
    epochs: MutableMapping[int, int],
    guild_id: Any,
    *,
    guild_is_open: Callable[[int], bool] | None = None,
) -> int:
    normalized_guild_id = _voice_ingress_guild_id(guild_id)
    if normalized_guild_id is None:
        return -1
    if (
        normalized_guild_id > 0
        and guild_is_open is not None
        and not guild_is_open(normalized_guild_id)
    ):
        return -1
    epoch = epochs.get(normalized_guild_id, 0)
    if isinstance(epoch, bool) or not isinstance(epoch, int) or epoch < 0:
        return -1
    return epoch


def advance_voice_ingress_epoch(
    epochs: MutableMapping[int, int],
    guild_id: Any,
) -> int:
    normalized_guild_id = _voice_ingress_guild_id(guild_id)
    if normalized_guild_id is None or normalized_guild_id == 0:
        raise ValueError("invalid_voice_ingress_guild_id")
    current_epoch = current_voice_ingress_epoch(epochs, normalized_guild_id)
    next_epoch = current_epoch + 1 if current_epoch >= 0 else 1
    epochs[normalized_guild_id] = next_epoch
    return next_epoch


def voice_ingress_epoch_is_current(
    epochs: MutableMapping[int, int],
    guild_id: Any,
    expected_epoch: Any,
    *,
    guild_is_open: Callable[[int], bool] | None = None,
) -> bool:
    return (
        not isinstance(expected_epoch, bool)
        and isinstance(expected_epoch, int)
        and expected_epoch >= 0
        and expected_epoch == current_voice_ingress_epoch(
            epochs,
            guild_id,
            guild_is_open=guild_is_open,
        )
    )


def _observe_handed_off_voice_task(
    task: asyncio.Task,
    *,
    log: Callable[..., Any],
) -> None:
    try:
        task.result()
    except asyncio.CancelledError:
        pass
    except Exception as exc:
        try:
            log(f"[VOICE WORKER] 실패: errorType={type(exc).__name__}")
        except Exception:
            pass


def _observe_registered_handed_off_voice_task(
    task: asyncio.Task,
    *,
    registry: MutableMapping[asyncio.Task, dict[str, Any]] | None,
    log: Callable[..., Any],
) -> None:
    if registry is not None:
        registry.pop(task, None)
    _observe_handed_off_voice_task(task, log=log)


def set_voice_transition_pending(guild_id: int, pending: bool) -> None:
    if pending:
        _VOICE_TRANSITION_GUILD_IDS.add(int(guild_id))
    else:
        _VOICE_TRANSITION_GUILD_IDS.discard(int(guild_id))


def voice_transition_is_pending(guild_id: Any) -> bool:
    try:
        return int(guild_id) in _VOICE_TRANSITION_GUILD_IDS
    except (TypeError, ValueError):
        return False


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
    voice_ingress_epoch_is_current: Callable[[int, Any], bool]
    process_member_audio: Callable[..., Awaitable[Any]]
    create_task: Callable[[Awaitable[Any]], asyncio.Task]
    log: Callable[..., Any] = print
    monotonic: Callable[[], float] = time.monotonic
    voice_ingress_process_tasks: MutableMapping[
        asyncio.Task, dict[str, Any]
    ] | None = None
    voice_ingress_target_is_current: Callable[[dict[str, Any]], bool] | None = None


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
    capture_voice_ingress_epoch: Callable[[int], int]
    build_voice_ingress_item: Callable[..., dict[str, Any]]
    voice_ingress_queue_depth: Callable[[], int]
    schedule_voice_utterance_item: Callable[[dict[str, Any]], Awaitable[Any]]
    monotonic: Callable[[], float] = time.monotonic
    admit_search_followup_recovery: Callable[[], Awaitable[bool]] | None = None


def voice_listener_binding_is_current(
    member: Any,
    binding: Any,
) -> bool:
    guild = getattr(member, "guild", None)
    current_client = getattr(guild, "voice_client", None)
    if voice_transition_is_pending(getattr(guild, "id", None)) or getattr(
        current_client,
        "_evelyn_voice_move_pending",
        False,
    ):
        return False
    if binding is None:
        return not hasattr(current_client, "_listener_generation")
    if not isinstance(binding, tuple) or len(binding) != 3:
        return False
    source_client, source_generation, source_channel_id = binding
    if isinstance(source_generation, bool) or not isinstance(source_generation, int):
        return False
    if current_client is not source_client:
        return False
    if getattr(current_client, "_listener_generation", None) != source_generation:
        return False
    current_channel_id = getattr(getattr(current_client, "channel", None), "id", None)
    return current_channel_id == source_channel_id


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


def _voice_ingress_item_epoch_is_current(
    item: dict[str, Any],
    *,
    deps: VoiceIngressRuntimeDeps,
) -> bool:
    guild_id = getattr(getattr(item.get("member"), "guild", None), "id", None)
    try:
        return bool(
            deps.voice_ingress_epoch_is_current(
                guild_id,
                item.get("voice_ingress_epoch"),
            )
        )
    except Exception:
        return False


def _voice_ingress_item_target_is_current(
    item: dict[str, Any],
    *,
    deps: VoiceIngressRuntimeDeps,
) -> bool:
    callback = deps.voice_ingress_target_is_current
    if callback is None:
        return True
    try:
        return callback(item) is True
    except Exception:
        return False


def _drain_matching_voice_ingress_items(
    target_predicate: Callable[[dict[str, Any]], bool],
    *,
    deps: VoiceIngressRuntimeDeps,
) -> int:
    survivors: list[dict[str, Any]] = []
    removed = 0
    while True:
        try:
            item = deps.voice_ingress_queue.get_nowait()
        except asyncio.QueueEmpty:
            break
        if target_predicate(item):
            removed += 1
        else:
            survivors.append(item)
        deps.voice_ingress_queue.task_done()
    for item in survivors:
        deps.voice_ingress_queue.put_nowait(item)
    return removed


def _drop_matching_voice_utterance_buffers(
    target_predicate: Callable[[dict[str, Any]], bool],
    *,
    deps: VoiceIngressRuntimeDeps,
) -> tuple[int, list[asyncio.Task]]:
    removed = 0
    cancelled_tasks: list[asyncio.Task] = []
    for key, buffer in tuple(deps.voice_utterance_buffers.items()):
        item = buffer.get("base_item") if isinstance(buffer, dict) else None
        if not isinstance(item, dict) or not target_predicate(item):
            continue
        deps.voice_utterance_buffers.pop(key, None)
        flush_task = deps.voice_utterance_flush_tasks.pop(key, None)
        if flush_task is not None and not flush_task.done():
            flush_task.cancel()
            cancelled_tasks.append(flush_task)
        removed += 1
    return removed, cancelled_tasks


async def cleanup_voice_ingress_targets_from_runtime(
    target_predicate: Callable[[dict[str, Any]], bool],
    *,
    deps: VoiceIngressRuntimeDeps,
    timeout_sec: float = 1.0,
) -> tuple[int, int]:
    """Bounded target cleanup; returns (removed, still_recalled)."""

    removed, flush_tasks = _drop_matching_voice_utterance_buffers(
        target_predicate,
        deps=deps,
    )
    removed += _drain_matching_voice_ingress_items(
        target_predicate,
        deps=deps,
    )
    registry = deps.voice_ingress_process_tasks
    matching_tasks = (
        [
            task
            for task, item in tuple(registry.items())
            if target_predicate(item)
        ]
        if registry is not None
        else []
    )
    tasks_to_join = [*flush_tasks, *matching_tasks]
    for task in tasks_to_join:
        if not task.done():
            task.cancel()
    pending_tasks: set[asyncio.Task] = set()
    if tasks_to_join:
        _done, pending_tasks = await asyncio.wait(
            tasks_to_join,
            timeout=max(0.0, float(timeout_sec)),
        )
    removed += sum(task.done() for task in tasks_to_join)

    # A producer may have reached its next await while tasks were draining.
    late_removed, late_flush_tasks = _drop_matching_voice_utterance_buffers(
        target_predicate,
        deps=deps,
    )
    removed += late_removed
    for task in late_flush_tasks:
        if not task.done():
            task.cancel()
    if late_flush_tasks:
        _done, late_pending = await asyncio.wait(
            late_flush_tasks,
            timeout=max(0.0, float(timeout_sec)),
        )
        pending_tasks.update(late_pending)
        removed += sum(task.done() for task in late_flush_tasks)
    removed += _drain_matching_voice_ingress_items(
        target_predicate,
        deps=deps,
    )
    remaining_tasks = set(pending_tasks)
    remaining_buffers = 0
    for buffer in deps.voice_utterance_buffers.values():
        item = buffer.get("base_item") if isinstance(buffer, dict) else None
        if isinstance(item, dict) and target_predicate(item):
            remaining_buffers += 1
    if registry is not None:
        remaining_tasks.update(
            task
            for task, item in tuple(registry.items())
            if not task.done() and target_predicate(item)
        )
    return removed, remaining_buffers + len(remaining_tasks)


async def process_member_audio_from_runtime(
    member: Any,
    pcm_bytes: bytes,
    debug_meta: dict[str, Any] | None = None,
    *,
    deps: VoiceIngressEntrypointDeps,
) -> None:
    debug_meta_input = deps.normalize_voice_debug_meta(debug_meta)
    voice_listener_binding = debug_meta_input.pop("_voice_listener_binding", None)
    if member is None or getattr(member, "bot", False):
        return
    guild = getattr(member, "guild", None)
    if guild is None:
        return
    guild_id = guild.id
    voice_ingress_epoch = deps.capture_voice_ingress_epoch(guild_id)
    if (
        isinstance(voice_ingress_epoch, bool)
        or not isinstance(voice_ingress_epoch, int)
        or voice_ingress_epoch < 0
    ):
        return
    await deps.ensure_startup_components_ready()
    current_voice_ingress_epoch = deps.capture_voice_ingress_epoch(guild_id)
    if (
        isinstance(current_voice_ingress_epoch, bool)
        or not isinstance(current_voice_ingress_epoch, int)
        or current_voice_ingress_epoch != voice_ingress_epoch
    ):
        return
    if (
        deps.admit_search_followup_recovery is not None
        and not await deps.admit_search_followup_recovery()
    ):
        return
    if not voice_listener_binding_is_current(member, voice_listener_binding):
        return
    source = deps.voice_ingress_source(debug_meta_input)
    if deps.should_drop_discord_audio_for_local_mic(getattr(member, "id", None), source=source):
        return

    deps.ensure_voice_worker_started()

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
        voice_ingress_epoch=voice_ingress_epoch,
        queue_depth_at_enqueue=deps.voice_ingress_queue_depth(),
        enqueued_at=deps.monotonic(),
    )
    if voice_listener_binding is not None:
        item["voice_listener_binding"] = voice_listener_binding
    await deps.schedule_voice_utterance_item(item)


async def voice_ingress_worker_from_runtime(*, deps: VoiceIngressRuntimeDeps) -> None:
    while True:
        item = await deps.voice_ingress_queue.get()
        try:
            if not _voice_ingress_item_epoch_is_current(
                item, deps=deps
            ) or not _voice_ingress_item_target_is_current(item, deps=deps):
                deps.increment_voice_pipeline_counter(
                    "voice_ingress_epoch_stale_drop_count"
                )
                deps.log("[VOICE QUEUE DROP] reason=guild_reset_epoch_stale")
                continue
            if not voice_listener_binding_is_current(
                item.get("member"),
                item.get("voice_listener_binding"),
            ):
                deps.increment_voice_pipeline_counter(
                    "listener_generation_stale_drop_count"
                )
                deps.log("[VOICE QUEUE DROP] reason=listener_generation_stale")
                continue
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
            handoff_event = asyncio.Event()
            process_item["release_ingress_worker"] = handoff_event.set
            process_task = deps.create_task(
                deps.process_member_audio(**process_item)
            )
            process_registry = deps.voice_ingress_process_tasks
            if process_registry is not None:
                process_registry[process_task] = dict(item)
            handoff_task = deps.create_task(handoff_event.wait())
            handed_off = False
            try:
                done, _pending = await asyncio.wait(
                    (process_task, handoff_task),
                    return_when=asyncio.FIRST_COMPLETED,
                )
                if process_task in done:
                    await process_task
                else:
                    handed_off = True
                    process_task.add_done_callback(
                        lambda done_task: _observe_registered_handed_off_voice_task(
                            done_task,
                            registry=process_registry,
                            log=deps.log,
                        )
                    )
            except asyncio.CancelledError:
                worker_task = asyncio.current_task()
                if (
                    process_task.cancelled()
                    and worker_task is not None
                    and not worker_task.cancelling()
                ):
                    continue
                if worker_task is not None and worker_task.cancelling():
                    if not handed_off and not handoff_event.is_set():
                        process_task.cancel()
                        await asyncio.gather(process_task, return_exceptions=True)
                    elif not handed_off:
                        process_task.add_done_callback(
                            lambda done_task: _observe_handed_off_voice_task(
                                done_task,
                                log=deps.log,
                            )
                        )
                    raise
            finally:
                handoff_task.cancel()
                await asyncio.gather(handoff_task, return_exceptions=True)
                if not handed_off and process_registry is not None:
                    process_registry.pop(process_task, None)
        except Exception as exc:
            deps.log(f"[VOICE WORKER] 실패: errorType={type(exc).__name__}")
        finally:
            deps.voice_ingress_queue.task_done()


async def enqueue_voice_ingress_for_processing_from_runtime(
    item: dict[str, Any],
    *,
    deps: VoiceIngressRuntimeDeps,
) -> None:
    if not _voice_ingress_item_epoch_is_current(
        item, deps=deps
    ) or not _voice_ingress_item_target_is_current(item, deps=deps):
        deps.increment_voice_pipeline_counter(
            "voice_ingress_epoch_stale_drop_count"
        )
        deps.log("[VOICE QUEUE DROP] reason=guild_reset_epoch_stale")
        return
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
    if not _voice_ingress_item_epoch_is_current(
        base_item, deps=deps
    ) or not _voice_ingress_item_target_is_current(base_item, deps=deps):
        deps.increment_voice_pipeline_counter(
            "voice_ingress_epoch_stale_drop_count"
        )
        deps.log("[VOICE UTTERANCE DROP] reason=guild_reset_epoch_stale")
        return
    if not voice_listener_binding_is_current(
        base_item.get("member"),
        base_item.get("voice_listener_binding"),
    ):
        deps.increment_voice_pipeline_counter(
            "listener_generation_stale_drop_count"
        )
        deps.log("[VOICE UTTERANCE DROP] reason=listener_generation_stale")
        return
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
    if not _voice_ingress_item_epoch_is_current(
        item, deps=deps
    ) or not _voice_ingress_item_target_is_current(item, deps=deps):
        deps.increment_voice_pipeline_counter(
            "voice_ingress_epoch_stale_drop_count"
        )
        deps.log("[VOICE UTTERANCE DROP] reason=guild_reset_epoch_stale")
        return
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
    if (
        buffer is not None
        and (
            buffer.get("base_item", {}).get("voice_listener_binding")
            != item.get("voice_listener_binding")
            or buffer.get("base_item", {}).get("voice_ingress_epoch")
            != item.get("voice_ingress_epoch")
        )
    ):
        await flush_voice_utterance_buffer_from_runtime(key, deps=deps)
        buffer = None
    if not _voice_ingress_item_epoch_is_current(
        item, deps=deps
    ) or not _voice_ingress_item_target_is_current(item, deps=deps):
        deps.increment_voice_pipeline_counter(
            "voice_ingress_epoch_stale_drop_count"
        )
        deps.log("[VOICE UTTERANCE DROP] reason=guild_reset_epoch_stale")
        return
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
