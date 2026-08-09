from __future__ import annotations

import asyncio
from dataclasses import dataclass
from typing import Any, Awaitable, Callable


@dataclass(frozen=True)
class VisionWatchCompositionDeps:
    enabled: bool
    interval_sec: float
    thumbnail_size: int
    max_image_dim: int
    diff_threshold: float
    capture_all_screens: bool
    analyze_cooldown_sec: float
    run_ocr: bool
    ocr_interval_sec: float
    analyze_timeout_sec: float
    vision_service_url: str
    capture_frame: Callable[..., Any]
    scene_looks_bad: Callable[[str], bool]
    build_prompt: Callable[[], str]
    get_http_session: Callable[[], Awaitable[Any]]
    client_timeout_factory: Callable[..., Any]
    update_analysis: Callable[..., dict[str, Any]]
    mark_startup_component: Callable[[str, str, str], Any]
    to_thread: Callable[..., Awaitable[Any]]
    sleep: Callable[[float], Awaitable[Any]]
    create_task: Callable[[Awaitable[Any]], Any]
    now: Callable[[], float]
    log: Callable[..., Any]


class VisionWatchComposition:
    """Owns background vision observation task lifecycle and analysis orchestration."""

    def __init__(self, deps: VisionWatchCompositionDeps) -> None:
        self.deps = deps
        self.task: Any = None

    async def run_vision_watch_once(self) -> dict[str, Any]:
        deps = self.deps
        frame = await deps.to_thread(
            deps.capture_frame,
            thumbnail_size=deps.thumbnail_size,
            max_image_dim=deps.max_image_dim,
            diff_threshold=deps.diff_threshold,
            all_screens=deps.capture_all_screens,
        )
        if frame.get("capture_black"):
            return frame
        now = deps.now()
        changed = bool(frame.get("changed"))
        last_analyzed_at = float(frame.get("analyzed_at", 0.0) or 0.0)
        scene_bad = deps.scene_looks_bad(str(frame.get("scene") or ""))
        analysis_stale = last_analyzed_at <= 0 or (now - last_analyzed_at) >= max(
            300.0, deps.analyze_cooldown_sec * 4
        )
        if not changed and not scene_bad and not analysis_stale:
            return frame
        if (now - last_analyzed_at) < deps.analyze_cooldown_sec and not analysis_stale:
            return frame

        last_ocr_at = float(frame.get("last_ocr_at", 0.0) or 0.0)
        run_ocr = bool(
            deps.run_ocr
            and (
                scene_bad
                or analysis_stale
                or (now - last_ocr_at) >= deps.ocr_interval_sec
            )
        )
        payload = {
            "image_path": str(frame.get("image_path") or ""),
            "prompt": deps.build_prompt(),
            "run_ocr": run_ocr,
            "ocr_category": "plain",
            "max_new_tokens": 96,
        }
        try:
            timeout = deps.client_timeout_factory(total=deps.analyze_timeout_sec)
            session = await deps.get_http_session()
            async with session.post(
                f"{deps.vision_service_url.rstrip('/')}/v1/vision/analyze",
                json=payload,
                timeout=timeout,
            ) as response:
                if response.status != 200:
                    return deps.update_analysis(
                        error="vision_analysis_failed:RuntimeError",
                        run_ocr=run_ocr,
                    )
                data = await response.json()
            return deps.update_analysis(data=data, run_ocr=run_ocr)
        except Exception as exc:
            return deps.update_analysis(
                error=f"vision_analysis_failed:{type(exc).__name__}",
                run_ocr=run_ocr,
            )

    async def vision_watch_loop(self) -> None:
        deps = self.deps
        deps.mark_startup_component(
            "vision_watch", "running", "background screen observer"
        )
        deps.log(
            "[VISION WATCH] enabled "
            f"interval={deps.interval_sec}s thumb={deps.thumbnail_size}px "
            f"analysis_max={deps.max_image_dim}px threshold={deps.diff_threshold} "
            f"ocr={'on' if deps.run_ocr else 'off'}"
        )
        while True:
            try:
                state = await self.run_vision_watch_once()
                deps.mark_startup_component(
                    "vision_watch",
                    "done",
                    f"changed={bool(state.get('changed'))} "
                    f"diff={float(state.get('diff_score', 0.0) or 0.0):.3f}",
                )
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                deps.mark_startup_component(
                    "vision_watch",
                    "failed",
                    f"vision_watch_failed:{type(exc).__name__}",
                )
                deps.log(
                    "[VISION WATCH] errorCode=vision_watch_failed "
                    f"errorType={type(exc).__name__}"
                )
            await deps.sleep(deps.interval_sec)

    def ensure_vision_watch_started(self) -> None:
        if not self.deps.enabled:
            return
        if self.task is not None and not self.task.done():
            return
        self.task = self.deps.create_task(self.vision_watch_loop())

    def stop_vision_watch_task(self) -> None:
        task = self.task
        self.task = None
        if task is not None and not task.done():
            task.cancel()
