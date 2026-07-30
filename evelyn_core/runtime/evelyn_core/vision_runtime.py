from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any, Awaitable, Callable


VISION_EVIDENCE_SCHEMA = "vision.evidence.v1"
VISION_EVIDENCE_STATES = frozenset({"observed", "unreliable", "unavailable", "failed", "unknown"})


@dataclass(frozen=True)
class VisionEvidence:
    state: str = "unknown"
    reason_code: str = "missing_evidence_contract"
    evidence_available: bool = False
    scene_available: bool = False
    ocr_available: bool = False
    confidence: str = "none"
    actionable: bool = False
    freshness: str = "unknown"
    schema: str = VISION_EVIDENCE_SCHEMA

    def to_dict(self) -> dict[str, Any]:
        state = self.state if self.state in VISION_EVIDENCE_STATES else "unknown"
        return {
            "schema": VISION_EVIDENCE_SCHEMA,
            "state": state,
            "reason_code": str(self.reason_code or "unknown"),
            "evidence_available": bool(self.evidence_available),
            "scene_available": bool(self.scene_available),
            "ocr_available": bool(self.ocr_available),
            "confidence": str(self.confidence or "none"),
            "actionable": bool(self.actionable),
            "freshness": str(self.freshness or "unknown"),
        }

    def satisfies_tool(self, tool_name: str) -> bool:
        if self.state != "observed" or not self.evidence_available:
            return False
        if tool_name == "vision_ocr":
            return self.ocr_available and self.actionable
        return self.scene_available or (
            self.ocr_available and self.actionable
        )

    def provenance_summary(self, *, tool_name: str = "") -> str:
        satisfied = self.satisfies_tool(tool_name) if tool_name else self.evidence_available
        return (
            f"schema={VISION_EVIDENCE_SCHEMA}; state={self.state}; reason={self.reason_code}; "
            f"tool_satisfied={str(bool(satisfied)).lower()}; "
            f"scene_available={str(self.scene_available).lower()}; "
            f"ocr_available={str(self.ocr_available).lower()}; "
            f"confidence={self.confidence}; actionable={str(self.actionable).lower()}; "
            f"freshness={self.freshness}"
        )


def vision_scene_echoes_request(
    scene: str,
    user_text: str,
    *,
    cleaner: Callable[[str], str],
) -> bool:
    normalized_scene = re.sub(r"\s+", "", cleaner(scene)).lower()
    normalized_request = re.sub(r"\s+", "", cleaner(user_text)).lower()
    if len(normalized_request) < 8 or normalized_request not in normalized_scene:
        return False
    return len(normalized_scene) <= max(48, int(len(normalized_request) * 3.5))


def record_vision_evidence(metrics: dict | None, evidence: VisionEvidence) -> None:
    if metrics is None:
        return
    metrics.setdefault("meta", {})["vision_evidence"] = evidence.to_dict()


def vision_evidence_from_metrics(metrics: dict | None) -> VisionEvidence:
    meta = metrics.get("meta") if isinstance(metrics, dict) else None
    payload = meta.get("vision_evidence") if isinstance(meta, dict) else None
    return vision_evidence_from_payload(payload)


def vision_evidence_from_payload(payload: Any) -> VisionEvidence:
    if not isinstance(payload, dict) or payload.get("schema") != VISION_EVIDENCE_SCHEMA:
        return VisionEvidence()
    state = str(payload.get("state") or "unknown")
    if state not in VISION_EVIDENCE_STATES:
        state = "unknown"
    scene_available = bool(payload.get("scene_available"))
    ocr_available = bool(payload.get("ocr_available"))
    evidence_available = bool(payload.get("evidence_available"))
    if state == "observed" and (not evidence_available or not (scene_available or ocr_available)):
        return VisionEvidence(state="unknown", reason_code="invalid_evidence_contract")
    if state != "observed":
        evidence_available = False
        scene_available = False
        ocr_available = False
    return VisionEvidence(
        state=state,
        reason_code=str(payload.get("reason_code") or "unknown"),
        evidence_available=evidence_available,
        scene_available=scene_available,
        ocr_available=ocr_available,
        confidence=str(payload.get("confidence") or "none"),
        actionable=bool(payload.get("actionable")) and evidence_available,
        freshness=str(payload.get("freshness") or "unknown"),
    )


@dataclass(frozen=True)
class VisionRuntimeDeps:
    clean_text: Callable[[str], str]
    build_vision_quality: Callable[[dict[str, Any]], dict[str, Any]]
    vision_watch_scene_is_unreliable: Callable[[str], bool]


@dataclass(frozen=True)
class LiveVisionContextRuntimeDeps:
    auto_capture_enabled: bool
    analyze_timeout_sec: float
    service_url: str
    capture_local_screen: Callable[[], Awaitable[tuple[Any, tuple[int, int]]]]
    build_observation_prompt: Callable[[str], str]
    get_http_session: Callable[[], Awaitable[Any]]
    client_timeout_factory: Callable[..., Any]
    delete_request_image: Callable[[Any], bool]
    format_observation: Callable[..., str]
    build_vision_quality: Callable[[dict[str, Any]], dict[str, Any]]
    clean_text: Callable[[str], str]
    monotonic: Callable[[], float]
    local_ocr_provider: Callable[[Any], Awaitable[Any]] | None = None
    local_window_provider: Callable[[], Awaitable[dict[str, Any]]] | None = None


async def build_live_vision_context_from_runtime(
    user_text: str,
    *,
    deps: LiveVisionContextRuntimeDeps,
    metrics: dict | None = None,
    run_ocr: bool = True,
) -> str:
    if not deps.auto_capture_enabled:
        record_vision_evidence(
            metrics,
            VisionEvidence(state="unavailable", reason_code="auto_capture_disabled"),
        )
        return "Local screen vision was requested, but automatic capture is disabled."
    started_at = deps.monotonic()
    try:
        image_path, image_size = await deps.capture_local_screen()
    except Exception as exc:
        error = deps.clean_text(repr(exc))[:240]
        if metrics is not None:
            metrics.setdefault("meta", {})["vision_capture_error"] = error
        if "black frame" in error.lower():
            record_vision_evidence(
                metrics,
                VisionEvidence(state="failed", reason_code="black_frame"),
            )
            return (
                "Local screen vision was requested, but the Windows screen capture returned a black frame. "
                "Do not claim the screen was analyzed. Tell the user the capture itself is black and needs capture-session fixing."
            )
        record_vision_evidence(
            metrics,
            VisionEvidence(state="failed", reason_code="capture_failed"),
        )
        return (
            "Local screen vision was requested, but screen capture failed. "
            "Do not claim the screen was analyzed."
        )

    local_ocr = ""
    local_ocr_attempted = False
    local_ocr_failed = False
    foreground_window: dict[str, Any] = {}
    if deps.local_window_provider is not None:
        try:
            candidate_window = await deps.local_window_provider()
            if isinstance(candidate_window, dict) and candidate_window.get(
                "available"
            ):
                foreground_window = {
                    "title": deps.clean_text(
                        str(candidate_window.get("title") or "")
                    )[:240],
                    "className": deps.clean_text(
                        str(candidate_window.get("className") or "")
                    )[:80],
                }
        except Exception:
            foreground_window = {}
    if run_ocr and deps.local_ocr_provider is not None:
        try:
            local_ocr_result = await deps.local_ocr_provider(image_path)
            if isinstance(local_ocr_result, dict):
                local_ocr_attempted = bool(
                    local_ocr_result.get("attempted")
                )
                local_ocr = deps.clean_text(
                    str(local_ocr_result.get("text") or "")
                )
            else:
                local_ocr_attempted = True
                local_ocr = deps.clean_text(local_ocr_result)
        except Exception:
            local_ocr_failed = True
    payload = {
        "image_path": str(image_path),
        "prompt": deps.build_observation_prompt(user_text),
        "run_ocr": bool(run_ocr and not local_ocr_attempted),
        "ocr_category": "plain",
        "max_new_tokens": 128,
    }
    try:
        timeout = deps.client_timeout_factory(total=deps.analyze_timeout_sec)
        session = await deps.get_http_session()
        async with session.post(
            f"{deps.service_url.rstrip('/')}/v1/vision/analyze",
            json=payload,
            timeout=timeout,
        ) as resp:
            if resp.status != 200:
                error_text = await resp.text()
                raise RuntimeError(f"vision service {resp.status}: {error_text[:240]}")
            data = await resp.json()
    except Exception as exc:
        error = deps.clean_text(repr(exc))[:240]
        deleted = deps.delete_request_image(image_path)
        if metrics is not None:
            metrics.setdefault("meta", {})["vision_analyze_error"] = error
            metrics.setdefault("meta", {})["vision_capture_path"] = "" if deleted else str(image_path)
            metrics.setdefault("meta", {})["vision_capture_deleted"] = deleted
        record_vision_evidence(
            metrics,
            VisionEvidence(state="failed", reason_code="analysis_failed"),
        )
        if deleted:
            return (
                "Local screen capture was discarded after vision analysis failed. "
                "Do not claim the screen was analyzed."
            )
        return (
            "Local screen capture cleanup also failed after vision analysis failed. "
            "Do not claim the screen was analyzed."
        )

    data = dict(data)
    if foreground_window:
        data["foreground_window_title"] = foreground_window["title"]
        data["foreground_window_class"] = foreground_window["className"]
    if local_ocr_attempted:
        data["ocr"] = local_ocr
        data["ocr_source"] = "windows_native"
    elif local_ocr_failed:
        data["local_ocr_error"] = "windows_native_ocr_failed"
    data["_scene_request_echo"] = vision_scene_echoes_request(
        str(data.get("scene") or ""),
        user_text,
        cleaner=deps.clean_text,
    )
    deleted = deps.delete_request_image(image_path)
    observation = deps.format_observation(
        image_path=image_path,
        image_size=image_size,
        data=data,
        image_deleted=deleted,
    )
    quality = deps.build_vision_quality(data)
    if data["_scene_request_echo"]:
        ocr = deps.clean_text(str(data.get("ocr") or ""))
        ocr_available_after_echo = bool(ocr) and not bool(quality.get("ocr_corrupt"))
        quality = {
            **quality,
            "scene_unreliable": True,
            "scene_request_echo": True,
            "weak": True,
            "no_usable_evidence": not ocr_available_after_echo,
            "confidence": "low" if ocr_available_after_echo else "none",
            "actionable": False,
        }
    scene = deps.clean_text(str(data.get("scene") or ""))
    ocr = deps.clean_text(str(data.get("ocr") or ""))
    foreground_available = bool(
        deps.clean_text(str(data.get("foreground_window_title") or ""))
        or deps.clean_text(str(data.get("foreground_window_class") or ""))
    )
    scene_available = foreground_available or (
        bool(scene) and not bool(quality.get("scene_unreliable"))
    )
    ocr_available = bool(ocr) and not bool(quality.get("ocr_corrupt"))
    evidence_available = scene_available or ocr_available
    no_usable_evidence = bool(quality.get("no_usable_evidence", not evidence_available))
    if no_usable_evidence:
        evidence_available = False
        scene_available = False
        ocr_available = False
    confidence = str(quality.get("confidence") or ("normal" if evidence_available else "none"))
    evidence = VisionEvidence(
        state="observed" if evidence_available else "unreliable",
        reason_code="live_observation" if evidence_available else "no_usable_visual_evidence",
        evidence_available=evidence_available,
        scene_available=scene_available,
        ocr_available=ocr_available,
        confidence=confidence,
        actionable=bool(quality.get("actionable")) and evidence_available,
        freshness="live",
    )
    record_vision_evidence(metrics, evidence)
    if metrics is not None:
        metrics.setdefault("marks", {})["vision_ready"] = (deps.monotonic() - started_at) * 1000.0
        metrics.setdefault("meta", {})["vision_capture_path"] = "" if deleted else str(image_path)
        metrics.setdefault("meta", {})["vision_capture_deleted"] = deleted
        metrics.setdefault("meta", {})["vision_ocr_chars"] = len(ocr)
        metrics.setdefault("meta", {})["vision_ocr_source"] = deps.clean_text(
            str(data.get("ocr_source") or "vision_service")
        )
        metrics.setdefault("meta", {})["vision_scene_chars"] = len(scene)
        metrics.setdefault("meta", {})["vision_foreground_available"] = (
            foreground_available
        )
        metrics.setdefault("meta", {})["vision_quality"] = dict(quality)
    return observation


def build_vision_observation_prompt_from_runtime(user_text: str, *, deps: VisionRuntimeDeps) -> str:
    return (
        "Look at this local screen capture for Evelyn. "
        "Return a concise factual visual inventory in Korean: app/window, prominent heading, "
        "buttons or menus, and clearly visible UI text. Do not answer a user question, repeat "
        "instruction text, or guess hidden state. Distinguish chat content from application chrome."
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
    foreground_title = deps.clean_text(
        str(data.get("foreground_window_title") or "")
    )
    foreground_class = deps.clean_text(
        str(data.get("foreground_window_class") or "")
    )
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
    if foreground_title or foreground_class:
        lines.append(
            "foreground_window: "
            f"title={foreground_title[:240] or '<empty>'}; "
            f"class={foreground_class[:80] or '<empty>'}"
        )
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
    "LiveVisionContextRuntimeDeps",
    "VISION_EVIDENCE_SCHEMA",
    "VisionEvidence",
    "VisionRuntimeDeps",
    "build_live_vision_context_from_runtime",
    "build_vision_observation_prompt_from_runtime",
    "build_vision_watch_prompt_from_runtime",
    "format_vision_observation_from_runtime",
    "record_vision_evidence",
    "vision_evidence_from_payload",
    "vision_evidence_from_metrics",
    "vision_scene_echoes_request",
    "vision_watch_scene_looks_bad_from_runtime",
]
