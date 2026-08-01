from __future__ import annotations

import base64
import json
from typing import Any
from urllib import request

import numpy as np


def transcribe_audio16k_via_service(
    audio: np.ndarray,
    *,
    service_url: str,
    timeout_sec: float,
    sampling_rate: int,
    max_new_tokens: int,
    stage: str,
    language: str | None = None,
    validation_bound: bool = False,
) -> dict[str, Any]:
    stt_audio = np.asarray(audio, dtype=np.float32)
    payload: dict[str, Any] = {
        "audio_f32_base64": base64.b64encode(stt_audio.tobytes()).decode("ascii"),
        "sample_count": int(stt_audio.size),
        "sampling_rate": int(sampling_rate),
        "max_new_tokens": int(max_new_tokens),
        "stage": str(stage or "full"),
    }
    if language:
        payload["language"] = language
    if validation_bound:
        payload["validation_bound"] = True

    root = service_url.rstrip("/")
    req = request.Request(
        f"{root}/v1/stt/transcribe",
        data=json.dumps(payload).encode("utf-8"),
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    with request.urlopen(req, timeout=max(1.0, float(timeout_sec))) as resp:
        raw = resp.read()
    return json.loads(raw.decode("utf-8"))
