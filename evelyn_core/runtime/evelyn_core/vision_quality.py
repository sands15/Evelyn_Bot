from __future__ import annotations

from typing import Any

from .text import clean_text
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


def vision_text_looks_corrupt(text: str) -> bool:
    text = clean_text(text)
    if not text:
        return False
    if "\ufffd" in text:
        return True
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


def build_vision_quality(data: dict[str, Any]) -> dict[str, bool]:
    scene = clean_text(str(data.get("scene") or ""))
    ocr = clean_text(str(data.get("ocr") or ""))
    scene_unreliable = bool(scene and vision_scene_looks_unreliable(scene))
    ocr_corrupt = vision_text_looks_corrupt(ocr)
    ocr_empty = not bool(ocr)
    weak = scene_unreliable or ocr_corrupt or (not scene and ocr_empty)
    no_usable_evidence = (not scene or scene_unreliable) and (ocr_empty or ocr_corrupt)
    return {
        "scene_unreliable": scene_unreliable,
        "ocr_corrupt": ocr_corrupt,
        "ocr_empty": ocr_empty,
        "weak": weak,
        "no_usable_evidence": no_usable_evidence,
    }
