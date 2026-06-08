from __future__ import annotations

import json
import hashlib
import os
import time
import uuid
from pathlib import Path
from typing import Any

from PIL import Image, ImageChops, ImageGrab, ImageStat

from .paths import get_runtime_artifacts_root
from .text import clean_text


VISION_WATCH_DIR = get_runtime_artifacts_root() / "vision_watch"
VISION_WATCH_STATE_PATH = VISION_WATCH_DIR / "vision_watch_state.json"
VISION_WATCH_KEEP_FILES = max(2, int(os.getenv("VISION_WATCH_KEEP_FILES", "48")))
VISION_WATCH_MAX_FILE_AGE_SEC = max(0.0, float(os.getenv("VISION_WATCH_MAX_FILE_AGE_SEC", "1800")))
VISION_CONTEXT_SCENE_TTL_SEC = max(0.0, float(os.getenv("VISION_CONTEXT_SCENE_TTL_SEC", "600")))
VISION_CONTEXT_OCR_TTL_SEC = max(0.0, float(os.getenv("VISION_CONTEXT_OCR_TTL_SEC", "180")))


def read_vision_watch_state(path: Path | None = None) -> dict[str, Any]:
    target = path or VISION_WATCH_STATE_PATH
    try:
        data = json.loads(target.read_text(encoding="utf-8"))
        return data if isinstance(data, dict) else {}
    except Exception:
        return {}


def write_vision_watch_state(state: dict[str, Any], path: Path | None = None) -> dict[str, Any]:
    target = path or VISION_WATCH_STATE_PATH
    target.parent.mkdir(parents=True, exist_ok=True)
    payload = dict(state)
    payload["updated_at"] = time.time()
    target.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    return payload


def _resize_to_max(image: Image.Image, max_dim: int) -> Image.Image:
    if max_dim <= 0:
        return image.copy()
    width, height = image.size
    largest = max(width, height)
    if largest <= max_dim:
        return image.copy()
    scale = max_dim / float(largest)
    new_size = (max(1, int(width * scale)), max(1, int(height * scale)))
    return image.resize(new_size, Image.Resampling.LANCZOS)


def _make_thumbnail(image: Image.Image, size: int) -> Image.Image:
    thumb = image.copy()
    thumb.thumbnail((max(32, size), max(32, size)), Image.Resampling.BILINEAR)
    return thumb.convert("RGB")


def _image_fingerprint(image: Image.Image) -> str:
    try:
        normalized = image.resize((32, 32), Image.Resampling.BILINEAR).convert("RGB")
        return hashlib.sha1(normalized.tobytes()).hexdigest()[:16]
    except Exception:
        return ""


def _text_fingerprint(text: str) -> str:
    cleaned = clean_text(text)
    if not cleaned:
        return ""
    return hashlib.sha1(cleaned.encode("utf-8", errors="ignore")).hexdigest()[:16]


def _diff_score(current_thumb: Image.Image, previous_path: str | None) -> float:
    if not previous_path:
        return 1.0
    path = Path(previous_path)
    if not path.exists():
        return 1.0
    try:
        previous = Image.open(path).convert("RGB")
        if previous.size != current_thumb.size:
            previous = previous.resize(current_thumb.size, Image.Resampling.BILINEAR)
        diff = ImageChops.difference(current_thumb, previous)
        stat = ImageStat.Stat(diff)
        means = stat.mean or [0.0]
        return max(0.0, min(1.0, sum(float(value) for value in means) / (len(means) * 255.0)))
    except Exception:
        return 1.0


def trim_vision_watch_dir(
    output_dir: Path,
    *,
    keep_files: int = VISION_WATCH_KEEP_FILES,
    max_age_sec: float = VISION_WATCH_MAX_FILE_AGE_SEC,
) -> dict[str, Any]:
    deleted = 0
    newest_mtime = 0.0
    try:
        files = sorted(
            [path for path in output_dir.glob("watch_*") if path.is_file()],
            key=lambda path: path.stat().st_mtime,
            reverse=True,
        )
    except Exception:
        return {"deleted": 0, "retained": 0, "newestMtime": 0.0}
    now = time.time()
    for path in files:
        try:
            newest_mtime = max(newest_mtime, path.stat().st_mtime)
        except Exception:
            pass
    keep_limit = max(0, keep_files)
    for index, path in enumerate(files):
        delete_for_count = index >= keep_limit
        delete_for_age = False
        if max_age_sec > 0:
            try:
                delete_for_age = (now - path.stat().st_mtime) > max_age_sec
            except Exception:
                delete_for_age = False
        if not (delete_for_count or delete_for_age):
            continue
        try:
            path.unlink()
            deleted += 1
        except Exception:
            pass
    retained = 0
    try:
        retained = len([path for path in output_dir.glob("watch_*") if path.is_file()])
    except Exception:
        retained = max(0, len(files) - deleted)
    return {"deleted": deleted, "retained": retained, "newestMtime": newest_mtime}


def capture_vision_watch_frame(
    *,
    thumbnail_size: int = 384,
    max_image_dim: int = 1280,
    diff_threshold: float = 0.08,
    all_screens: bool = False,
    output_dir: Path | None = None,
    state_path: Path | None = None,
) -> dict[str, Any]:
    output = output_dir or VISION_WATCH_DIR
    output.mkdir(parents=True, exist_ok=True)
    previous_state = read_vision_watch_state(state_path)

    image = ImageGrab.grab(all_screens=all_screens).convert("RGB")
    now = time.time()
    stem = f"watch_{int(now * 1000)}_{uuid.uuid4().hex[:8]}"
    extrema = image.getextrema()
    capture_black = bool(extrema and all(int(high) <= 2 for _low, high in extrema))

    thumb = _make_thumbnail(image, thumbnail_size)
    image_fingerprint = _image_fingerprint(thumb)
    diff = _diff_score(thumb, clean_text(str(previous_state.get("thumbnail_path") or "")))
    changed = False if capture_black else diff >= max(0.0, float(diff_threshold))

    thumbnail_path = output / f"{stem}_thumb.jpg"
    thumb.save(thumbnail_path, quality=82, optimize=True)

    resized = _resize_to_max(image, max_image_dim)
    image_path = output / f"{stem}_{resized.size[0]}x{resized.size[1]}.jpg"
    resized.save(image_path, quality=88, optimize=True)
    cleanup = trim_vision_watch_dir(output)

    state = {
        **previous_state,
        "captured_at": now,
        "original_width": image.size[0],
        "original_height": image.size[1],
        "analysis_width": resized.size[0],
        "analysis_height": resized.size[1],
        "image_path": str(image_path),
        "thumbnail_path": str(thumbnail_path),
        "diff_score": round(diff, 5),
        "image_fingerprint": image_fingerprint,
        "changed": bool(changed),
        "diff_threshold": float(diff_threshold),
        "capture_black": bool(capture_black),
        "cleanup": {
            "lastCleanupAt": now,
            "deletedFiles": int(cleanup.get("deleted", 0) or 0),
            "retainedFiles": int(cleanup.get("retained", 0) or 0),
            "keepFiles": VISION_WATCH_KEEP_FILES,
            "maxFileAgeSec": VISION_WATCH_MAX_FILE_AGE_SEC,
        },
    }
    if capture_black:
        state["analysis_error"] = "screen capture returned a black frame"
        state["scene"] = ""
        state["scene_unreliable"] = True
        state["ocr"] = ""
        state["ocr_error"] = ""
    elif changed:
        state["scene"] = ""
        state["scene_unreliable"] = False
        state["ocr"] = ""
        state["ocr_error"] = ""
    return write_vision_watch_state(state, state_path)


def update_vision_watch_analysis(
    *,
    data: dict[str, Any] | None = None,
    error: str = "",
    run_ocr: bool = False,
    state_path: Path | None = None,
) -> dict[str, Any]:
    state = read_vision_watch_state(state_path)
    now = time.time()
    data = data if isinstance(data, dict) else {}
    if error:
        state["analysis_error"] = clean_text(error)[:300]
    else:
        state["analysis_error"] = ""
        state["capture_black"] = False
        scene = clean_text(str(data.get("scene") or ""))[:1200]
        if vision_watch_scene_is_unreliable(scene):
            state["scene"] = ""
            state["scene_unreliable"] = True
        else:
            state["scene"] = scene
            state["scene_unreliable"] = False
            state["scene_expires_at"] = now + VISION_CONTEXT_SCENE_TTL_SEC if VISION_CONTEXT_SCENE_TTL_SEC > 0 else None
            state["scene_fingerprint"] = _text_fingerprint(scene)
        if run_ocr:
            state["ocr"] = clean_text(str(data.get("ocr") or ""))[:1200]
            state["ocr_error"] = clean_text(str(data.get("ocr_error") or ""))[:300]
            state["last_ocr_at"] = now
            state["ocr_expires_at"] = now + VISION_CONTEXT_OCR_TTL_SEC if VISION_CONTEXT_OCR_TTL_SEC > 0 else None
    state["analyzed_at"] = now
    state["run_ocr"] = bool(run_ocr)
    return write_vision_watch_state(state, state_path)


def vision_watch_scene_is_unreliable(scene: str) -> bool:
    text = clean_text(scene)
    if not text:
        return False
    digit_count = sum(ch.isdigit() for ch in text)
    letter_count = sum(ch.isalpha() for ch in text)
    if digit_count >= 30 and letter_count < 12:
        return True
    tokens = [token for token in text.replace(",", " ").split() if token]
    if len(tokens) >= 8:
        most_common = max(tokens.count(token) for token in set(tokens))
        if most_common / max(1, len(tokens)) >= 0.45:
            return True
    compact = "".join(tokens) or text.replace(" ", "")
    if len(compact) >= 24:
        for width in range(2, 9):
            chunks = [compact[i : i + width] for i in range(0, min(len(compact), 80), width)]
            if len(chunks) >= 6:
                most_common = max(chunks.count(chunk) for chunk in set(chunks) if chunk)
                if most_common >= 5:
                    return True
    return False


def render_vision_watch_context(*, max_age_sec: float = 600.0) -> str:
    state = read_vision_watch_state()
    if not state:
        return ""
    now = time.time()
    captured_at = float(state.get("captured_at", 0.0) or 0.0)
    age = 999999.0 if captured_at <= 0 else max(0.0, now - captured_at)
    if age > max_age_sec:
        return ""
    lines = [
        "Background local screen observation:",
        "This is the user's local screen, not authoritative Minecraft bot inventory/state.",
        f"- age_sec={age:.1f}; changed={bool(state.get('changed'))}; diff_score={float(state.get('diff_score', 0.0) or 0.0):.3f}.",
        f"- original_size={int(state.get('original_width', 0) or 0)}x{int(state.get('original_height', 0) or 0)}; analysis_size={int(state.get('analysis_width', 0) or 0)}x{int(state.get('analysis_height', 0) or 0)}.",
    ]
    scene = clean_text(str(state.get("scene") or ""))
    ocr = clean_text(str(state.get("ocr") or ""))
    error = clean_text(str(state.get("analysis_error") or ""))
    scene_expires_at = float(state.get("scene_expires_at", 0.0) or 0.0)
    ocr_expires_at = float(state.get("ocr_expires_at", 0.0) or 0.0)
    scene_expired = bool(scene_expires_at > 0 and now > scene_expires_at)
    ocr_expired = bool(ocr_expires_at > 0 and now > ocr_expires_at)
    if scene and not scene_expired and not vision_watch_scene_is_unreliable(scene):
        lines.append("scene: " + scene[:700])
    elif state.get("scene_unreliable"):
        lines.append("scene: omitted because the lightweight background observer marked it unreliable.")
    if ocr and not ocr_expired:
        lines.append("ocr_text: " + ocr[:500])
    if error:
        lines.append("analysis_error: " + error[:220])
    lines.append("Use this only as soft context. If uncertain, ask before claiming what is on screen.")
    return "\n".join(lines)
