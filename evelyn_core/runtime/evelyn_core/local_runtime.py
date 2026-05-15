from __future__ import annotations

import asyncio
from dataclasses import asdict, dataclass
from urllib.parse import urlparse, urlunparse

import aiohttp

from .config import (
    LLM_SERVER_URL,
    MODEL_NAME,
    MINECRAFT_AUTONOMY_SERVICE_HOST,
    MINECRAFT_AUTONOMY_SERVICE_PORT,
    OMNIVOICE_MODEL,
    OMNIVOICE_SERVER_URL,
    OPENAI_API_KEY,
    ROUTER_LLM_URL,
    ROUTER_MODEL_NAME,
    SUMMARY_LLM_URL,
    SUMMARY_MODEL_NAME,
    VOYAGER_CODEX_GATEWAY_URL,
    VOYAGER_CODEX_MODEL,
    VOYAGER_CRITIC_LLM_URL,
    VOYAGER_CRITIC_MODEL_NAME,
    VOYAGER_CURRICULUM_LLM_URL,
    VOYAGER_CURRICULUM_MODEL_NAME,
)


@dataclass(frozen=True)
class LocalRuntimeService:
    name: str
    role: str
    endpoint: str
    health_url: str
    model: str | None
    recommended_use: str

    def public_dict(self) -> dict[str, object]:
        return asdict(self)


def _models_url(chat_url: str) -> str:
    parsed = urlparse(chat_url)
    path = parsed.path
    if path.endswith("/v1/chat/completions"):
        path = path[: -len("/chat/completions")] + "/models"
    elif path.endswith("/chat/completions"):
        path = path[: -len("/chat/completions")] + "/models"
    elif not path.endswith("/models"):
        path = "/v1/models"
    return urlunparse((parsed.scheme, parsed.netloc, path, "", "", ""))


def configured_local_runtime_services() -> list[dict[str, object]]:
    services = [
        LocalRuntimeService(
            name="main_llm",
            role="large_response_or_skill_reasoning",
            endpoint=LLM_SERVER_URL,
            health_url=_models_url(LLM_SERVER_URL),
            model=MODEL_NAME,
            recommended_use="longer Minecraft skill/recovery reasoning; avoid per-tick calls",
        ),
        LocalRuntimeService(
            name="sub_llm",
            role="summary_memory_or_deeper_state_reasoning",
            endpoint=SUMMARY_LLM_URL,
            health_url=_models_url(SUMMARY_LLM_URL),
            model=SUMMARY_MODEL_NAME,
            recommended_use="slower deeper summaries and postmortem state analysis",
        ),
        LocalRuntimeService(
            name="router_llm",
            role="fast_state_router_inventory_assessor",
            endpoint=ROUTER_LLM_URL,
            health_url=_models_url(ROUTER_LLM_URL),
            model=ROUTER_MODEL_NAME,
            recommended_use="per-observe route/state/inventory classification JSON",
        ),
        LocalRuntimeService(
            name="curriculum_agent_llm",
            role="curriculum_next_goal_planner",
            endpoint=VOYAGER_CURRICULUM_LLM_URL,
            health_url=_models_url(VOYAGER_CURRICULUM_LLM_URL),
            model=VOYAGER_CURRICULUM_MODEL_NAME,
            recommended_use="GPT nano API when openai_api is set; picks next stage/goal",
        ),
        LocalRuntimeService(
            name="critic_fallback_llm",
            role="ambiguous_critic_fallback",
            endpoint=VOYAGER_CRITIC_LLM_URL,
            health_url=_models_url(VOYAGER_CRITIC_LLM_URL),
            model=VOYAGER_CRITIC_MODEL_NAME,
            recommended_use="rule-first critic fallback only when the rules are ambiguous",
        ),
        LocalRuntimeService(
            name="codex_gateway",
            role="code_action_generation",
            endpoint=VOYAGER_CODEX_GATEWAY_URL,
            health_url=VOYAGER_CODEX_GATEWAY_URL.rsplit("/", 2)[0] + "/health",
            model=VOYAGER_CODEX_MODEL,
            recommended_use="last-resort generated Mineflayer JS; not for normal FSM routing",
        ),
        LocalRuntimeService(
            name="omnivoice_tts",
            role="voice_output",
            endpoint=OMNIVOICE_SERVER_URL,
            health_url=OMNIVOICE_SERVER_URL.rstrip("/") + "/health",
            model=OMNIVOICE_MODEL,
            recommended_use="spoken Korean output only; never blocks Minecraft action choice",
        ),
        LocalRuntimeService(
            name="minecraft_autonomy",
            role="minecraft_observe_execute_loop",
            endpoint=f"http://{MINECRAFT_AUTONOMY_SERVICE_HOST}:{MINECRAFT_AUTONOMY_SERVICE_PORT}",
            health_url=f"http://{MINECRAFT_AUTONOMY_SERVICE_HOST}:{MINECRAFT_AUTONOMY_SERVICE_PORT}/health",
            model=None,
            recommended_use="thin Minecraft adapter for observe/status/start/stop/goal; main app does not direct-execute steps here",
        ),
    ]
    return [service.public_dict() for service in services]


async def probe_local_runtime_services(timeout_s: float = 0.8) -> list[dict[str, object]]:
    services = configured_local_runtime_services()
    timeout = aiohttp.ClientTimeout(total=max(0.2, timeout_s))

    async def probe(service: dict[str, object]) -> dict[str, object]:
        result = dict(service)
        url = str(service.get("health_url") or service.get("endpoint") or "")
        result["available"] = False
        result["status"] = "unknown"
        if not url:
            result["status"] = "missing_url"
            return result
        try:
            headers = {"Authorization": f"Bearer {OPENAI_API_KEY}"} if OPENAI_API_KEY and ("api.openai.com" in url or "openai" in url.lower()) else None
            async with aiohttp.ClientSession(timeout=timeout) as session:
                async with session.get(url, headers=headers) as resp:
                    text = await resp.text()
                    result["available"] = 200 <= resp.status < 300
                    result["status"] = resp.status
                    result["sample"] = text[:240]
        except Exception as exc:
            result["status"] = type(exc).__name__
            result["error"] = str(exc)[:240]
        return result

    return await asyncio.gather(*(probe(service) for service in services))
