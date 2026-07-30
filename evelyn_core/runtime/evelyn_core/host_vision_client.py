from __future__ import annotations

import asyncio
import argparse
import contextlib
from dataclasses import dataclass
import json
import math
from pathlib import Path
import sys
import time
from typing import Any, Awaitable, Callable
import uuid

from .host_vision_contract import (
    HOST_VISION_MAX_REQUEST_TTL_SEC,
    HOST_VISION_MAX_RESPONSE_AGE_SEC,
    HOST_VISION_REQUEST_SCHEMA,
    HOST_VISION_RESPONSE_SCHEMA,
    HOST_VISION_RESPONSE_TTL_SEC,
)
from .paths import get_runtime_artifacts_root
from .runtime_artifact_io import atomic_json_write
from .text import clean_text
from .vision_runtime import VisionEvidence, vision_evidence_from_payload


HOST_VISION_RESPONSE_KEYS = frozenset(
    {
        "schema",
        "requestId",
        "createdAt",
        "expiresAt",
        "observation",
        "evidence",
        "errorCode",
        "latencyMs",
        "screenshotDeleted",
        "sceneChars",
        "ocrChars",
    }
)
HOST_VISION_MAX_RESPONSE_BYTES = 32768
HOST_VISION_DEFAULT_TIMEOUT_SEC = 135.0


@dataclass(frozen=True)
class HostVisionResult:
    observation: str
    evidence: VisionEvidence
    error_code: str = ""
    latency_ms: float | None = None
    screenshot_deleted: bool | None = None
    scene_chars: int = 0
    ocr_chars: int = 0


def _failed_result(error_code: str, *, state: str = "failed") -> HostVisionResult:
    code = clean_text(error_code)[:80] or "host_vision_failed"
    return HostVisionResult(
        observation=(
            "Local screen vision did not produce usable evidence. "
            "Do not claim the screen was analyzed."
        ),
        evidence=VisionEvidence(state=state, reason_code=code),
        error_code=code,
    )


def _read_response(
    path: Path,
    *,
    request_id: str,
    now: float,
) -> HostVisionResult:
    try:
        if path.stat().st_size > HOST_VISION_MAX_RESPONSE_BYTES:
            return _failed_result("response_too_large")
        with path.open("r", encoding="utf-8") as handle:
            payload = json.load(handle)
    except Exception:
        return _failed_result("invalid_response_json")
    if not isinstance(payload, dict) or set(payload) != HOST_VISION_RESPONSE_KEYS:
        return _failed_result("invalid_response")
    if payload.get("schema") != HOST_VISION_RESPONSE_SCHEMA:
        return _failed_result("invalid_response_schema")
    if payload.get("requestId") != request_id:
        return _failed_result("response_id_mismatch")
    created_at = payload.get("createdAt")
    expires_at = payload.get("expiresAt")
    if (
        isinstance(created_at, bool)
        or not isinstance(created_at, (int, float))
        or isinstance(expires_at, bool)
        or not isinstance(expires_at, (int, float))
        or not math.isfinite(float(created_at))
        or not math.isfinite(float(expires_at))
    ):
        return _failed_result("invalid_response_lifetime")
    created_at = float(created_at)
    expires_at = float(expires_at)
    if (
        created_at > now + 2.0
        or expires_at <= created_at
        or expires_at - created_at > HOST_VISION_RESPONSE_TTL_SEC + 0.001
    ):
        return _failed_result("invalid_response_lifetime")
    if now - created_at > HOST_VISION_MAX_RESPONSE_AGE_SEC:
        return _failed_result("response_stale")
    if expires_at <= now:
        return _failed_result("response_expired")
    observation = payload.get("observation")
    if not isinstance(observation, str) or len(observation) > 4000:
        return _failed_result("invalid_observation")
    raw_evidence = payload.get("evidence")
    evidence = vision_evidence_from_payload(raw_evidence, now=now)
    if (
        isinstance(raw_evidence, dict)
        and raw_evidence.get("state") == "observed"
        and evidence.state != "observed"
    ):
        return _failed_result("invalid_evidence_contract")
    if evidence.state == "observed" and (not evidence.evidence_available or not clean_text(observation)):
        return _failed_result("invalid_evidence_contract")
    if evidence.state != "observed":
        observation = (
            "Local screen vision did not produce current usable evidence. "
            "Do not infer screen contents."
        )
    error_code = clean_text(payload.get("errorCode"))[:80]
    latency = payload.get("latencyMs")
    if isinstance(latency, bool) or not isinstance(latency, (int, float)):
        latency_ms = None
    else:
        latency_ms = max(0.0, float(latency))
    scene_chars = payload.get("sceneChars")
    ocr_chars = payload.get("ocrChars")
    return HostVisionResult(
        observation=clean_text(observation),
        evidence=evidence,
        error_code=error_code,
        latency_ms=latency_ms,
        screenshot_deleted=(
            payload.get("screenshotDeleted")
            if type(payload.get("screenshotDeleted")) is bool
            else None
        ),
        scene_chars=(
            max(0, int(scene_chars))
            if evidence.state == "observed" and isinstance(scene_chars, int)
            else 0
        ),
        ocr_chars=(
            max(0, int(ocr_chars))
            if evidence.state == "observed" and isinstance(ocr_chars, int)
            else 0
        ),
    )


async def request_host_vision(
    user_text: str,
    *,
    run_ocr: bool,
    artifacts_root: Path | None = None,
    timeout_sec: float = HOST_VISION_DEFAULT_TIMEOUT_SEC,
    poll_interval_sec: float = 0.1,
    now: Callable[[], float] = time.time,
    monotonic: Callable[[], float] = time.monotonic,
    sleep: Callable[[float], Awaitable[Any]] = asyncio.sleep,
) -> HostVisionResult:
    text = clean_text(user_text)
    if not text:
        return _failed_result("invalid_user_text")
    text = text[:512]
    root = Path(artifacts_root or get_runtime_artifacts_root()) / "host_vision"
    requests_dir = root / "requests"
    responses_dir = root / "responses"
    requests_dir.mkdir(parents=True, exist_ok=True)
    responses_dir.mkdir(parents=True, exist_ok=True)
    request_id = uuid.uuid4().hex
    request_path = requests_dir / f"{request_id}.json"
    response_path = responses_dir / f"{request_id}.json"
    created_at = now()
    ttl = min(
        HOST_VISION_MAX_REQUEST_TTL_SEC,
        max(5.0, float(timeout_sec) + 10.0),
    )
    payload = {
        "schema": HOST_VISION_REQUEST_SCHEMA,
        "requestId": request_id,
        "createdAt": created_at,
        "expiresAt": created_at + ttl,
        "userText": text,
        "runOcr": bool(run_ocr),
    }
    try:
        await asyncio.to_thread(atomic_json_write, request_path, payload)
    except Exception:
        return _failed_result("request_write_failed", state="unavailable")

    deadline = monotonic() + max(0.05, float(timeout_sec))
    try:
        while monotonic() < deadline:
            if response_path.exists():
                return await asyncio.to_thread(
                    _read_response,
                    response_path,
                    request_id=request_id,
                    now=now(),
                )
            await sleep(max(0.01, float(poll_interval_sec)))
        return _failed_result("host_vision_timeout", state="unavailable")
    finally:
        for path in (request_path, response_path):
            with contextlib.suppress(FileNotFoundError, PermissionError, OSError):
                path.unlink()


__all__ = [
    "HOST_VISION_DEFAULT_TIMEOUT_SEC",
    "HostVisionResult",
    "request_host_vision",
]


def main() -> None:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    parser = argparse.ArgumentParser(
        description="Run an ephemeral Evelyn Host Vision contract probe.",
    )
    parser.add_argument(
        "--ocr",
        action="store_true",
        help="Request OCR in addition to scene description.",
    )
    parser.add_argument(
        "--timeout-sec",
        type=float,
        default=HOST_VISION_DEFAULT_TIMEOUT_SEC,
    )
    args = parser.parse_args()
    result = asyncio.run(
        request_host_vision(
            "Inspect the current screen using only visible evidence.",
            run_ocr=bool(args.ocr),
            timeout_sec=max(1.0, float(args.timeout_sec)),
        )
    )
    print(
        json.dumps(
            {
                "observation": result.observation,
                "evidence": result.evidence.to_dict(),
                "errorCode": result.error_code,
                "latencyMs": result.latency_ms,
                "screenshotDeleted": result.screenshot_deleted,
            },
            ensure_ascii=False,
        )
    )


if __name__ == "__main__":
    main()
