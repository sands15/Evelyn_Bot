from __future__ import annotations

import time
import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Awaitable, Callable

from .vision_runtime import (
    LiveVisionContextRuntimeDeps,
    VisionRuntimeDeps,
    build_live_vision_context_from_runtime,
    build_vision_observation_prompt_from_runtime,
    build_vision_watch_prompt_from_runtime,
    format_vision_observation_from_runtime,
    vision_watch_scene_looks_bad_from_runtime,
)


@dataclass(frozen=True)
class VisionRequestCompositionDeps:
    screenshot_dir: Path
    capture_all_screens: bool
    delete_request_images: bool
    auto_capture_enabled: bool
    analyze_timeout_sec: float
    service_url: str
    build_vision_quality: Callable[..., dict[str, Any]]
    vision_watch_scene_is_unreliable: Callable[[str], bool]
    get_http_session: Callable[[], Awaitable[Any]]
    client_timeout_factory: Callable[..., Any]
    clean_text: Callable[[str], str]
    to_thread: Callable[..., Awaitable[Any]]
    monotonic: Callable[[], float]
    local_ocr_provider: Callable[[Any], Awaitable[Any]] | None = None
    local_window_provider: Callable[[], Awaitable[dict[str, Any]]] | None = None


class VisionRequestComposition:
    """Owns on-demand screen capture, cleanup, formatting, and live analysis wiring."""

    def __init__(self, deps: VisionRequestCompositionDeps) -> None:
        self.deps = deps

    def build_vision_watch_runtime_deps(self) -> VisionRuntimeDeps:
        return VisionRuntimeDeps(
            clean_text=self.deps.clean_text,
            build_vision_quality=self.deps.build_vision_quality,
            vision_watch_scene_is_unreliable=self.deps.vision_watch_scene_is_unreliable,
        )

    def build_vision_observation_prompt(self, user_text: str) -> str:
        return build_vision_observation_prompt_from_runtime(
            user_text,
            deps=self.build_vision_watch_runtime_deps(),
        )

    def capture_local_screen_sync(self) -> tuple[Path, tuple[int, int]]:
        from PIL import ImageGrab

        screenshot_dir = self.deps.screenshot_dir
        screenshot_dir.mkdir(parents=True, exist_ok=True)
        image = ImageGrab.grab(all_screens=self.deps.capture_all_screens).convert("RGB")
        path = screenshot_dir / f"screen_{int(time.time() * 1000)}_{uuid.uuid4().hex[:8]}.png"
        image.save(path)
        extrema = image.getextrema()
        if extrema and all(int(high) <= 2 for _low, high in extrema):
            self.delete_file_quietly(path)
            raise RuntimeError("screen capture returned a black frame")
        return path, image.size

    async def capture_local_screen(self) -> tuple[Path, tuple[int, int]]:
        return await self.deps.to_thread(self.capture_local_screen_sync)

    def delete_file_quietly(self, path: Path | None) -> bool:
        if path is None:
            return False
        try:
            resolved = path.resolve()
            screenshot_root = self.deps.screenshot_dir.resolve()
            if screenshot_root not in (resolved, *resolved.parents):
                return False
            if not resolved.exists() or not resolved.is_file():
                return False
            resolved.unlink()
            return True
        except Exception:
            return False

    def delete_request_vision_image(self, path: Path | None) -> bool:
        if not self.deps.delete_request_images:
            return False
        return self.delete_file_quietly(path)

    def format_vision_observation(
        self,
        *,
        image_path: Path,
        image_size: tuple[int, int],
        data: dict[str, Any],
        image_deleted: bool = False,
    ) -> str:
        return format_vision_observation_from_runtime(
            image_path=image_path,
            image_size=image_size,
            data=data,
            image_deleted=image_deleted,
            deps=self.build_vision_watch_runtime_deps(),
        )

    def build_live_vision_context_runtime_deps(self) -> LiveVisionContextRuntimeDeps:
        deps = self.deps
        return LiveVisionContextRuntimeDeps(
            auto_capture_enabled=deps.auto_capture_enabled,
            analyze_timeout_sec=deps.analyze_timeout_sec,
            service_url=deps.service_url,
            capture_local_screen=self.capture_local_screen,
            build_observation_prompt=self.build_vision_observation_prompt,
            get_http_session=deps.get_http_session,
            client_timeout_factory=deps.client_timeout_factory,
            delete_request_image=self.delete_request_vision_image,
            format_observation=self.format_vision_observation,
            build_vision_quality=deps.build_vision_quality,
            clean_text=deps.clean_text,
            monotonic=deps.monotonic,
            local_ocr_provider=deps.local_ocr_provider,
            local_window_provider=deps.local_window_provider,
        )

    async def build_live_vision_context(
        self,
        user_text: str,
        *,
        metrics: dict | None = None,
        run_ocr: bool = True,
    ) -> str:
        return await build_live_vision_context_from_runtime(
            user_text,
            deps=self.build_live_vision_context_runtime_deps(),
            metrics=metrics,
            run_ocr=run_ocr,
        )

    def build_vision_watch_prompt(self) -> str:
        return build_vision_watch_prompt_from_runtime()

    def vision_watch_scene_looks_bad(self, scene: str) -> bool:
        return vision_watch_scene_looks_bad_from_runtime(
            scene,
            deps=self.build_vision_watch_runtime_deps(),
        )


__all__ = ["VisionRequestComposition", "VisionRequestCompositionDeps"]
