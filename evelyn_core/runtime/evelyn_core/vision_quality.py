from __future__ import annotations

import re
from typing import Any

from .text import clean_text


VISION_ACTIONABLE_GUIDANCE = {
    "normal": "Vision evidence is usable as supporting context.",
    "low": "Vision evidence is weak; mention uncertainty and do not use it as the sole basis for actions.",
    "none": "Vision evidence is unusable; do not claim screen contents or base actions on it.",
}


def vision_scene_looks_unreliable(scene: str) -> bool:
    text = clean_text(scene)
    if not text:
        return False
    tokens = [token for token in text.replace(",", " ").split() if token]
    if len(tokens) >= 6:
        most_common = max(tokens.count(token) for token in set(tokens))
        if most_common / max(1, len(tokens)) >= 0.4:
            return True
    for marker in ("아이콘", "자리선택자"):
        if text.count(marker) >= 4:
            return True
    return False


def vision_text_looks_corrupt(
    text: str,
    *,
    source: str = "",
) -> bool:
    text = clean_text(text)
    if not text:
        return False
    if "\ufffd" in text:
        return True
    if source == "windows_native":
        control_count = sum(
            1
            for character in text
            if ord(character) < 32 and not character.isspace()
        )
        return control_count > 0
    allowed_punct = set(" \t\r\n.,!?;:'\"()[]{}<>/\\|-_+=*&^%$#@~`·…•→←↑↓※")
    bad_count = 0
    visible_count = 0
    for ch in text:
        if ch.isspace():
            continue
        visible_count += 1
        code = ord(ch)
        is_allowed = (
            ch.isdigit()
            or "a" <= ch.lower() <= "z"
            or "\uac00" <= ch <= "\ud7a3"
            or "\u3130" <= ch <= "\u318f"
            or ch in allowed_punct
            or code in {0x221E}
        )
        if not is_allowed:
            bad_count += 1
    if visible_count == 0:
        return False
    return bad_count >= 2 or (bad_count / visible_count) >= 0.015


def build_vision_quality(data: dict[str, Any]) -> dict[str, Any]:
    scene = clean_text(str(data.get("scene") or ""))
    ocr = clean_text(str(data.get("ocr") or ""))
    ocr_source = clean_text(str(data.get("ocr_source") or ""))
    foreground_title = clean_text(
        str(data.get("foreground_window_title") or "")
    )
    foreground_class = clean_text(
        str(data.get("foreground_window_class") or "")
    )
    foreground_available = bool(foreground_title or foreground_class)
    scene_request_echo = bool(data.get("_scene_request_echo"))
    normalized_scene = re.sub(r"[\s.]+", "", scene).casefold()
    identity_only_scene = normalized_scene in {"evelyn", "이블린"}
    scene_unreliable = bool(
        scene
        and (
            scene_request_echo
            or identity_only_scene
            or vision_scene_looks_unreliable(scene)
        )
    )
    ocr_corrupt = vision_text_looks_corrupt(
        ocr,
        source=clean_text(str(data.get("ocr_source") or "")),
    )
    ocr_empty = not bool(ocr)
    unscored_native_ocr = bool(ocr) and ocr_source == "windows_native"
    weak = (
        scene_unreliable
        or ocr_corrupt
        or unscored_native_ocr
        or (not scene and ocr_empty)
    )
    no_usable_evidence = (
        not foreground_available
        and (not scene or scene_unreliable)
        and (ocr_empty or ocr_corrupt)
    )
    confidence = "none" if no_usable_evidence else ("low" if weak else "normal")
    actionable = confidence == "normal"
    return {
        "scene_unreliable": scene_unreliable,
        "scene_request_echo": scene_request_echo,
        "scene_identity_only": identity_only_scene,
        "foreground_available": foreground_available,
        "ocr_corrupt": ocr_corrupt,
        "ocr_empty": ocr_empty,
        "ocr_unscored": unscored_native_ocr,
        "weak": weak,
        "no_usable_evidence": no_usable_evidence,
        "confidence": confidence,
        "actionable": actionable,
        "guidance": VISION_ACTIONABLE_GUIDANCE[confidence],
    }
