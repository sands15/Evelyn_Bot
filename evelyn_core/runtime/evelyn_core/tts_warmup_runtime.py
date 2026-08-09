from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Awaitable, Callable


@dataclass(frozen=True)
class TtsWarmupRuntimeDeps:
    get_http_session: Callable[[], Awaitable[Any]]
    client_timeout: Callable[..., Any]
    mark_startup_component: Callable[[str, str, str], Any]
    startup_component_done: Callable[[str], bool]
    omnivoice_server_url: str
    omnivoice_model: str
    omnivoice_voice: str
    omnivoice_language: str
    getenv: Callable[[str, str], str]
    log: Callable[..., Any] = print


async def warmup_tts_server_from_runtime(*, deps: TtsWarmupRuntimeDeps) -> None:
    deps.mark_startup_component("tts_warmup", "running", "OmniVoice health check")

    session = await deps.get_http_session()
    async with session.get(f"{deps.omnivoice_server_url}/health", timeout=deps.client_timeout(total=10)) as resp:
        if resp.status != 200:
            deps.mark_startup_component("tts_warmup", "failed", "tts_warmup_failed")
            raise RuntimeError("OmniVoice health check failed")
        deps.log("OmniVoice 서버 준비 확인 완료")

    if deps.getenv("TTS_WARMUP_GENERATE_ENABLED", "false").lower() not in {"1", "true", "yes", "on"}:
        deps.mark_startup_component("tts_warmup", "done", "health check only")
        return

    payload = {
        "model": deps.omnivoice_model,
        "input": "안녕",
        "voice": deps.omnivoice_voice if deps.omnivoice_voice else "auto",
        "response_format": "pcm",
        "stream": True,
    }
    if deps.omnivoice_language:
        payload["language"] = deps.omnivoice_language

    async with session.post(
        f"{deps.omnivoice_server_url}/v1/audio/speech",
        json=payload,
        timeout=deps.client_timeout(total=20),
    ) as resp:
        if resp.status != 200:
            deps.mark_startup_component("tts_warmup", "failed", "tts_warmup_failed")
            raise RuntimeError("OmniVoice warmup failed")
        async for chunk in resp.content.iter_chunked(4096):
            if chunk:
                deps.mark_startup_component("tts_warmup", "done", "")
                deps.log("OmniVoice TTS 워밍업 완료")
                break
        if not deps.startup_component_done("tts_warmup"):
            deps.mark_startup_component("tts_warmup", "done", "no audio chunk returned")
