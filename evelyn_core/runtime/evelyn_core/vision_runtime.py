from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any, Callable


@dataclass(frozen=True)
class VisionRuntimeDeps:
    clean_text: Callable[[str], str]
    build_vision_quality: Callable[[dict[str, Any]], dict[str, Any]]
    vision_watch_scene_is_unreliable: Callable[[str], bool]


def build_vision_observation_prompt_from_runtime(user_text: str, *, deps: VisionRuntimeDeps) -> str:
    request = deps.clean_text(user_text)[:240]
    return (
        "Look at this local screen capture for Evelyn. "
        "Answer in concise Korean. Describe the visible scene and include clear UI/OCR text. "
        "Do not guess hidden state. User request: "
        + request
    )


def build_vision_watch_prompt_from_runtime() -> str:
    return (
        "You are Evelyn's lightweight background screen observer. "
        "Describe only clearly visible changes on the user's local screen in Korean. "
        "Be concise. Mention app/window/menu/error/text only if visible. "
        "Do not infer Minecraft bot inventory or bot state from this user screen."
    )


def format_vision_observation_from_runtime(
    *,
    image_path: Any,
    image_size: tuple[int, int],
    data: dict[str, Any],
    image_deleted: bool = False,
    deps: VisionRuntimeDeps,
) -> str:
    scene = deps.clean_text(str(data.get("scene") or ""))
    ocr = deps.clean_text(str(data.get("ocr") or ""))
    ocr_error = deps.clean_text(str(data.get("ocr_error") or ""))
    quality = deps.build_vision_quality(data)
    lines = [
        "Local screen vision observation is available.",
        "This is the user's local screen capture, not authoritative Minecraft bot inventory/state.",
        "captured_image=discarded_after_analysis" if image_deleted else f"captured_image={image_path}",
        f"image_size={image_size[0]}x{image_size[1]}",
    ]
    if quality["no_usable_evidence"]:
        lines.append("vision_quality=unreliable")
        lines.append(f"vision_confidence={quality.get('confidence', 'none')}")
        lines.append("vision_actionable=false")
        lines.append(
            "The screen capture was taken, but the vision/OCR result is too weak or garbled to identify the screen contents. "
            "Do not claim what is on screen; tell the user the capture/analysis result is unreliable and needs a better vision pass."
        )
    elif quality["weak"]:
        lines.append("vision_quality=low_confidence")
        lines.append(f"vision_confidence={quality.get('confidence', 'low')}")
        lines.append("vision_actionable=false")
        lines.append("Use only the evidence below, and explicitly hedge uncertainty.")
    else:
        lines.append(f"vision_confidence={quality.get('confidence', 'normal')}")
        lines.append(f"vision_actionable={str(bool(quality.get('actionable'))).lower()}")
    if scene and not quality["scene_unreliable"]:
        lines.append("scene: " + scene[:900])
    elif scene and quality["scene_unreliable"]:
        lines.append("scene_omitted: repeated or unreliable vision output")
    if ocr and not quality["ocr_corrupt"]:
        lines.append("ocr_text: " + ocr[:900])
    elif ocr and quality["ocr_corrupt"]:
        lines.append("ocr_text_omitted: OCR output looked corrupted or mixed with invalid characters")
    if ocr_error:
        lines.append("ocr_error: " + ocr_error[:300])
    lines.append("When answering, use this observation naturally. If the observation is weak, say only what is visible.")
    return "\n".join(lines)


def vision_watch_scene_looks_bad_from_runtime(scene: str, *, deps: VisionRuntimeDeps) -> bool:
    text = deps.clean_text(scene)
    if not text:
        return True
    if deps.vision_watch_scene_is_unreliable(text):
        return True
    digit_count = len(re.findall(r"\d", text))
    latin_count = len(re.findall(r"[A-Za-z]", text))
    hangul_count = len(re.findall(r"[\uac00-\ud7a3]", text))
    if re.search(r"\d{1,3}[./:]\d{1,3}[./:]\d{1,3}", text) and digit_count > max(20, latin_count + hangul_count):
        return True
    if digit_count >= 30 and latin_count + hangul_count < 12:
        return True
    return False


__all__ = [
    "VisionRuntimeDeps",
    "build_vision_observation_prompt_from_runtime",
    "build_vision_watch_prompt_from_runtime",
    "format_vision_observation_from_runtime",
    "vision_watch_scene_looks_bad_from_runtime",
]
