from __future__ import annotations

import asyncio
from typing import Any, Callable


async def run_blocking_stt_task_from_runtime(
    func: Callable[[], Any],
    *,
    stage: str,
    timeout_sec: float,
    metrics: dict | None = None,
    get_stt_cooldown_until: Callable[[], float],
    set_stt_cooldown_until: Callable[[float], Any],
    stt_cooldown_after_timeout_sec: float,
    monotonic: Callable[[], float],
    get_stt_inference_lock: Callable[[], Any],
    increment_voice_pipeline_counter: Callable[[str], Any],
    record_voice_pipeline_failure: Callable[..., Any],
    wait_for: Callable[..., Any] = asyncio.wait_for,
    to_thread: Callable[..., Any] = asyncio.to_thread,
) -> Any:
    now_mono = monotonic()
    stt_cooldown_until = float(get_stt_cooldown_until())
    if now_mono < stt_cooldown_until:
        increment_voice_pipeline_counter("stt_busy_drop_count")
        raise TimeoutError(f"stt_cooldown:{stage}:{stt_cooldown_until - now_mono:.2f}s")

    lock = get_stt_inference_lock()
    if lock.locked():
        increment_voice_pipeline_counter("stt_busy_drop_count")
        raise RuntimeError(f"stt_busy:{stage}")

    await lock.acquire()
    release_deferred = False
    try:
        worker = asyncio.ensure_future(to_thread(func))

        def release_after_worker(done: asyncio.Future[Any]) -> None:
            try:
                done.result()
            except BaseException:
                pass
            if lock.locked():
                lock.release()

        try:
            return await wait_for(
                asyncio.shield(worker),
                timeout=max(0.5, timeout_sec),
            )
        except asyncio.TimeoutError:
            release_deferred = True
            worker.add_done_callback(release_after_worker)
            set_stt_cooldown_until(monotonic() + max(0.0, stt_cooldown_after_timeout_sec))
            increment_voice_pipeline_counter("stt_timeout_count")
            record_voice_pipeline_failure("stt_timeout", f"{stage} timed out after {timeout_sec:.1f}s", metrics, stage=stage)
            raise
        except asyncio.CancelledError:
            release_deferred = True
            worker.add_done_callback(release_after_worker)
            raise
    finally:
        if not release_deferred and lock.locked():
            lock.release()
