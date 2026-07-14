from __future__ import annotations

import json
import os
import re
import time
from typing import Any, AsyncIterator

from aiohttp import ClientSession, ClientTimeout, web

from .control_page_http import reject_browser_origin_middleware
from .assistant_prompt_contract import (
    FAST_MAIN_LLM_USER_PREFIX,
    build_evelyn_system_prompt,
    build_fast_main_llm_user_text,
)
from .control_page_contracts import (
    build_control_page_panel_state_payload,
    build_fast_control_default_commands,
    detect_memory_panel_action,
    local_restart_requested_reply,
    local_shutdown_requested_reply,
    memory_panel_reply,
)
from .fast_context_contract import build_fast_main_llm_messages
from .paths import get_runtime_artifacts_root
from .runtime_health import collect_runtime_health, default_probe_runner
from .runtime_services import HealthProbeSpec, ServiceSpec, load_service_manifest
from .text import visible_text as shared_visible_text


HOST = os.getenv("CONTROL_PAGE_HOST", "0.0.0.0")
PORT = int(os.getenv("CONTROL_PAGE_PORT", os.getenv("CONTROL_PAGE_BOT_API_PORT", "8798")))
PUBLIC_CONTROL_PORT = int(os.getenv("CONTROL_PAGE_PUBLIC_PORT", "8799"))
LLM_SERVER_URL = os.getenv("LLM_SERVER_URL", "http://127.0.0.1:9820/v1/chat/completions")
MODEL_NAME = os.getenv("LLM_MODEL_NAME", "google-gemma-4-12B-it-IQ4_XS.gguf")
MAIN_LLM_STOP_TOKENS = tuple(
    token.strip()
    for token in os.getenv("MAIN_LLM_STOP_TOKENS", "<|eot_id|>,<|end_of_text|>").split(",")
    if token.strip()
)
CHAT_LOG_LIMIT = max(4, int(os.getenv("FAST_CONTROL_CHAT_LOG_LIMIT", "40")))
LOCAL_BRIDGE_STALE_AFTER_SEC = max(3.0, float(os.getenv("LOCAL_BRIDGE_STALE_AFTER_SEC", "8.0")))
FAST_MAIN_LLM_SYSTEM_PROMPT = build_evelyn_system_prompt()


BOOT_STEPS = (
    ("control_page", "Control-Page"),
    ("bot_api", "Bot API"),
    ("main_llm", "Main LLM"),
    ("router_llm", "Router LLM"),
    ("sub_llm", "Sub LLM"),
    ("tts", "TTS"),
    ("stt", "STT"),
)

CHAT_MESSAGES: list[dict[str, Any]] = []
CONTROL_PAGE_UI_COMMANDS: list[dict[str, Any]] = []
CONTROL_PAGE_UI_COMMAND_SEQ = 0
LOCAL_BRIDGE_STATUS: dict[str, Any] = {
    "enabled": False,
    "ready": False,
    "mode": "windows_io_bridge",
}
LOCAL_BRIDGE_SPEAK_QUEUE: list[dict[str, Any]] = []
LOCAL_BRIDGE_SPEAK_SEQ = 0
LOCAL_AUDIO_DEVICE_STATE_PATH = get_runtime_artifacts_root() / "state" / "local_audio_devices.json"
SHUTDOWN_REQUEST: dict[str, Any] = {
    "requested": False,
    "requestedAt": None,
    "source": "",
    "reason": "",
}
RESTART_REQUEST: dict[str, Any] = {
    "requested": False,
    "requestedAt": None,
    "source": "",
    "reason": "",
}


def load_local_audio_device_state() -> dict[str, Any]:
    try:
        with LOCAL_AUDIO_DEVICE_STATE_PATH.open("r", encoding="utf-8") as handle:
            data = json.load(handle)
    except Exception:
        return {"outputDevice": "", "requestedAt": None, "source": "", "revision": 0}
    if not isinstance(data, dict):
        return {"outputDevice": "", "requestedAt": None, "source": "", "revision": 0}
    try:
        revision = int(data.get("revision") or 0)
    except Exception:
        revision = 0
    return {
        "outputDevice": re.sub(r"\s+", " ", str(data.get("outputDevice") or "").strip()),
        "requestedAt": data.get("requestedAt") if isinstance(data.get("requestedAt"), (int, float)) else None,
        "source": re.sub(r"\s+", " ", str(data.get("source") or "").strip()),
        "revision": max(0, revision),
    }


def save_local_audio_device_state(state: dict[str, Any]) -> None:
    LOCAL_AUDIO_DEVICE_STATE_PATH.parent.mkdir(parents=True, exist_ok=True)
    with LOCAL_AUDIO_DEVICE_STATE_PATH.open("w", encoding="utf-8") as handle:
        json.dump(state, handle, ensure_ascii=False, indent=2)


LOCAL_BRIDGE_OUTPUT_DEVICE_REQUEST: dict[str, Any] = load_local_audio_device_state()


def json_response(payload: dict[str, Any], *, status: int = 200) -> web.Response:
    return web.json_response(payload, status=status)


def clean_text(value: Any) -> str:
    return re.sub(r"\s+", " ", str(value or "").strip())


def visible_text(value: Any) -> str:
    text = str(value or "")
    text = re.sub(
        r"<\|channel\>\s*(?:thought|analysis|reasoning)\b.*?<channel\|>\s*",
        " ",
        text,
        flags=re.IGNORECASE | re.DOTALL,
    )
    text = re.sub(r"<\|channel\>\s*(?:final|model|answer|content)\s*<channel\|>", " ", text, flags=re.IGNORECASE)
    text = re.sub(r"<\|channel\>|<channel\|>|</?think>", " ", text, flags=re.IGNORECASE)
    text = re.sub(r"^\s*\[(?:찾기|질문|대기|응답)\]\s*", "", text, flags=re.IGNORECASE)
    return clean_text(shared_visible_text(text))


def append_chat_message(role: str, author: str, text: str, *, source: str | None = None) -> None:
    message = {
        "role": role,
        "author": author,
        "text": text,
        "at": time.time(),
    }
    if source:
        message["source"] = source
    CHAT_MESSAGES.append(message)
    if len(CHAT_MESSAGES) > CHAT_LOG_LIMIT:
        del CHAT_MESSAGES[:-CHAT_LOG_LIMIT]


def local_bridge_status_snapshot(*, now: float | None = None) -> dict[str, Any]:
    snapshot = dict(LOCAL_BRIDGE_STATUS)
    if LOCAL_BRIDGE_OUTPUT_DEVICE_REQUEST.get("outputDevice"):
        snapshot["outputDeviceSelection"] = dict(LOCAL_BRIDGE_OUTPUT_DEVICE_REQUEST)
    updated_at = snapshot.get("updatedAt")
    if not snapshot.get("enabled") or not isinstance(updated_at, (int, float)):
        return snapshot
    age_sec = float((time.time() if now is None else now) - updated_at)
    snapshot["ageSec"] = round(max(0.0, age_sec), 1)
    if age_sec <= LOCAL_BRIDGE_STALE_AFTER_SEC:
        snapshot["stale"] = False
        return snapshot
    snapshot["ready"] = False
    snapshot["stale"] = True
    snapshot["lastError"] = clean_text(snapshot.get("lastError")) or f"local_bridge_stale age={snapshot['ageSec']}s"
    return snapshot


def queue_local_bridge_speech(text: str, *, source: str = "control_page") -> dict[str, Any] | None:
    global LOCAL_BRIDGE_SPEAK_SEQ
    speech_text = clean_text(text)
    if not speech_text:
        return None
    bridge = local_bridge_status_snapshot()
    if not bridge.get("ready") or bridge.get("stale"):
        return None
    LOCAL_BRIDGE_SPEAK_SEQ += 1
    request = {
        "id": f"page-tts-{LOCAL_BRIDGE_SPEAK_SEQ}",
        "text": speech_text,
        "source": source,
        "createdAt": time.time(),
    }
    LOCAL_BRIDGE_SPEAK_QUEUE.append(request)
    del LOCAL_BRIDGE_SPEAK_QUEUE[:-8]
    return request


def drain_local_bridge_speak_requests() -> list[dict[str, Any]]:
    requests = list(LOCAL_BRIDGE_SPEAK_QUEUE)
    LOCAL_BRIDGE_SPEAK_QUEUE.clear()
    return requests


def set_local_bridge_output_device(output_device: str, *, source: str = "control_page") -> dict[str, Any]:
    current_revision = int(LOCAL_BRIDGE_OUTPUT_DEVICE_REQUEST.get("revision") or 0)
    requested_device = clean_text(output_device) or "default"
    LOCAL_BRIDGE_OUTPUT_DEVICE_REQUEST.update(
        {
            "outputDevice": requested_device,
            "requestedAt": time.time(),
            "source": clean_text(source) or "control_page",
            "revision": current_revision + 1,
        }
    )
    save_local_audio_device_state(LOCAL_BRIDGE_OUTPUT_DEVICE_REQUEST)
    return dict(LOCAL_BRIDGE_OUTPUT_DEVICE_REQUEST)


def should_queue_local_bridge_speech(source: str) -> bool:
    return clean_text(source) not in {"local_bridge", "local_mic", "voice"}


def enqueue_control_page_ui_command(action: str, *, panel_id: str) -> dict[str, Any]:
    global CONTROL_PAGE_UI_COMMAND_SEQ
    cleaned_action = clean_text(action).lower()
    if cleaned_action not in {"open", "close", "toggle"}:
        cleaned_action = "toggle"
    CONTROL_PAGE_UI_COMMAND_SEQ += 1
    command = {
        "id": CONTROL_PAGE_UI_COMMAND_SEQ,
        "action": cleaned_action,
        "panel": panel_id,
        "at": time.time(),
    }
    CONTROL_PAGE_UI_COMMANDS.append(command)
    if len(CONTROL_PAGE_UI_COMMANDS) > 40:
        del CONTROL_PAGE_UI_COMMANDS[:-40]
    return dict(command)


def build_control_page_panel_state() -> dict[str, Any]:
    return build_control_page_panel_state_payload(
        CONTROL_PAGE_UI_COMMANDS,
        revision=CONTROL_PAGE_UI_COMMAND_SEQ,
    )


def execute_memory_panel_action(action: str) -> str:
    cleaned_action = action if action in {"open", "close", "toggle"} else "toggle"
    enqueue_control_page_ui_command(cleaned_action, panel_id="memory")
    return memory_panel_reply(cleaned_action)


def request_local_shutdown(*, source: str, reason: str = "") -> dict[str, Any]:
    SHUTDOWN_REQUEST.update(
        {
            "requested": True,
            "requestedAt": time.time(),
            "source": clean_text(source) or "control_page",
            "reason": clean_text(reason) or "operator_request",
        }
    )
    return {
        "ok": True,
        "message": "Local Evelyn shutdown requested. Windows local I/O bridge will run the stop script.",
        "shutdown": dict(SHUTDOWN_REQUEST),
    }


def request_local_restart(*, source: str, reason: str = "") -> dict[str, Any]:
    RESTART_REQUEST.update(
        {
            "requested": True,
            "requestedAt": time.time(),
            "source": clean_text(source) or "control_page",
            "reason": clean_text(reason) or "operator_request",
        }
    )
    return {
        "ok": True,
        "message": "Local Evelyn restart requested. Windows local I/O bridge will restart the local runtime.",
        "restart": dict(RESTART_REQUEST),
    }


def build_control_plane_state(*, bot_ready: bool) -> dict[str, Any]:
    return {
        "controlPage": {
            "ready": True,
            "host": "127.0.0.1",
            "port": PUBLIC_CONTROL_PORT,
            "role": "Control-Page",
        },
        "botApi": {
            "ready": bool(bot_ready),
            "portOpen": bool(bot_ready),
            "host": HOST,
            "port": PORT,
            "role": "Bot API",
            "state": "ready" if bot_ready else "down",
        },
        "lastProxyFailure": {},
        "healthCache": {"ageSec": 0.0, "stale": False},
        "statusText": (
            "Control-Page and Bot API are both responding."
            if bot_ready
            else f"Control-Page is live on {PUBLIC_CONTROL_PORT}; Bot API is not ready on {PORT}."
        ),
    }


def default_chat_messages() -> list[dict[str, Any]]:
    if CHAT_MESSAGES:
        return list(CHAT_MESSAGES)
    return [
        {
            "role": "assistant",
            "author": "Control",
            "text": "Docker core is ready. Windows local I/O bridge can attach microphone and speaker output.",
            "at": time.time(),
        }
    ]


def parse_stream_line(raw_line: bytes) -> dict[str, Any] | None:
    line = raw_line.decode("utf-8", errors="ignore").strip()
    if not line or line.startswith(":"):
        return None
    if line.startswith("data:"):
        line = line[5:].strip()
    if not line:
        return None
    if line == "[DONE]":
        return {"done": True, "delta": ""}
    try:
        data = json.loads(line)
    except json.JSONDecodeError:
        return None
    choices = data.get("choices") or []
    if not choices:
        return {"done": False, "delta": ""}
    choice = choices[0] or {}
    delta = choice.get("delta") or {}
    content = delta.get("content")
    if isinstance(content, list):
        content = "".join(str(item.get("text") or "") for item in content if isinstance(item, dict))
    if content is None:
        content = (choice.get("message") or {}).get("content") or choice.get("text") or ""
    return {"done": False, "delta": str(content or "")}


def pop_speakable_chunks(buffer: str, *, force: bool = False, max_chars: int = 110) -> tuple[list[str], str]:
    text = buffer or ""
    chunks: list[str] = []
    while text:
        match = re.search(r"(.+?[.!?\u3002\uff01\uff1f]+)(?:\s+|$)", text, flags=re.DOTALL)
        if match:
            chunk = clean_text(match.group(1))
            if chunk:
                chunks.append(chunk)
            text = text[match.end() :]
            continue
        if force:
            chunk = clean_text(text)
            if chunk:
                chunks.append(chunk)
            return chunks, ""
        if len(text) >= max_chars:
            split_at = max(text.rfind(" ", 0, max_chars), text.rfind(",", 0, max_chars), text.rfind("，", 0, max_chars))
            if split_at < max_chars // 2:
                split_at = max_chars
            chunk = clean_text(text[:split_at])
            if chunk:
                chunks.append(chunk)
            text = text[split_at:]
            continue
        break
    return chunks, text


async def build_main_llm_payload(text: str, *, source: str) -> dict[str, Any]:
    recent_messages = [
        {"role": message.get("role"), "content": clean_text(message.get("text"))}
        for message in CHAT_MESSAGES[-8:]
        if message.get("role") in {"user", "assistant"} and clean_text(message.get("text"))
    ]
    if recent_messages and recent_messages[-1].get("content") == clean_text(text):
        recent_messages = recent_messages[:-1]
    final_user_text = build_fast_main_llm_user_text(text)
    messages = await build_fast_main_llm_messages(
        base_system_prompt=FAST_MAIN_LLM_SYSTEM_PROMPT,
        recent_messages=recent_messages,
        user_text=text,
        final_user_text=final_user_text,
        source=source,
    )
    payload = {
        "model": MODEL_NAME,
        "messages": messages,
        "temperature": 0.3 if source in {"voice", "local_bridge", "local_mic"} else 0.2,
        "max_tokens": 700,
        "stream": True,
        "cache_prompt": True,
    }
    if MAIN_LLM_STOP_TOKENS:
        payload["stop"] = list(MAIN_LLM_STOP_TOKENS)
    return payload


async def iter_main_llm_deltas(text: str, *, source: str) -> AsyncIterator[str]:
    payload = await build_main_llm_payload(text, source=source)
    timeout = ClientTimeout(total=120)
    async with ClientSession(timeout=timeout) as session:
        async with session.post(LLM_SERVER_URL, json=payload) as resp:
            if resp.status != 200:
                detail = await resp.text()
                raise RuntimeError(f"main_llm_error {resp.status}: {detail[:300]}")
            content_type = resp.headers.get("Content-Type", "")
            if "application/json" in content_type.lower():
                data = await resp.json()
                choices = data.get("choices") or []
                if choices:
                    yield str((choices[0].get("message") or {}).get("content") or "")
                return
            async for raw_line in resp.content:
                event = parse_stream_line(raw_line)
                if not event:
                    continue
                if event.get("done"):
                    break
                delta = str(event.get("delta") or "")
                if delta:
                    yield delta


async def ask_main_llm(text: str, *, source: str) -> str:
    parts = [delta async for delta in iter_main_llm_deltas(text, source=source)]
    return visible_text("".join(parts))


async def ask_main_llm_and_queue_speech(text: str, *, source: str) -> tuple[str, int]:
    raw_parts: list[str] = []
    clean_seen_len = 0
    sentence_buffer = ""
    queued_count = 0
    async for delta in iter_main_llm_deltas(text, source=source):
        raw_parts.append(delta)
        cleaned = visible_text("".join(raw_parts))
        new_text = cleaned[clean_seen_len:]
        clean_seen_len = len(cleaned)
        if not new_text:
            continue
        sentence_buffer += new_text
        chunks, sentence_buffer = pop_speakable_chunks(sentence_buffer)
        for chunk in chunks:
            if queue_local_bridge_speech(chunk, source=source):
                queued_count += 1
    tail_chunks, sentence_buffer = pop_speakable_chunks(sentence_buffer, force=True)
    for chunk in tail_chunks:
        if queue_local_bridge_speech(chunk, source=source):
            queued_count += 1
    return visible_text("".join(raw_parts)), queued_count


async def resolve_pre_llm_reply(text: str, *, source: str) -> str | None:
    normalized = text.lower()
    queued_speech_count = 0
    if normalized in {"/help", "help"}:
        return "?ъ슜 媛?? /status, /memory, /voice status, ?쇰컲 ??? 濡쒖뺄 ?뚯꽦? Windows local I/O bridge媛 ?대떦??"
    if normalized in {"/status", "status"}:
        manifest = load_service_manifest()
        health = await collect_runtime_health(manifest=manifest, probe_runner=fast_control_probe_runner)
        return str(health.get("summary") or health.get("overallState") or "runtime status unavailable")
    if (memory_action := detect_memory_panel_action(text)) is not None:
        return execute_memory_panel_action(memory_action)
    if normalized in {"/voice", "/voice status", "voice status"}:
        bridge_status = local_bridge_status_snapshot()
        ready = bool(bridge_status.get("ready"))
        error = clean_text(bridge_status.get("lastError"))
        reply = f"Windows local I/O bridge: {'ready' if ready else 'not ready'}"
        if bridge_status.get("stale"):
            reply += f" | stale {bridge_status.get('ageSec')}s"
        if error:
            reply += f" | {error}"
        return reply
    if normalized in {"/restart", "restart"}:
        request_local_restart(source=source, reason="chat_command")
        return local_restart_requested_reply()
    if normalized in {"/shutdown", "shutdown"}:
        request_local_shutdown(source=source, reason="chat_command")
        return local_shutdown_requested_reply()
    return None


def _service_by_id(health: dict[str, Any]) -> dict[str, dict[str, Any]]:
    return {str(service.get("id") or ""): dict(service) for service in health.get("services") or [] if isinstance(service, dict)}


def build_boot_progress(health: dict[str, Any]) -> dict[str, Any]:
    services = _service_by_id(health)
    steps: list[dict[str, Any]] = []
    for service_id, label in BOOT_STEPS:
        service = services.get(service_id) or {}
        ready = bool(service.get("ready") or service.get("state") == "up")
        steps.append(
            {
                "key": service_id,
                "label": label,
                "done": ready,
                "status": "done" if ready else str(service.get("state") or "pending"),
                "detail": str(service.get("reason") or ""),
            }
        )
    done_count = sum(1 for step in steps if step["done"])
    percent = round((done_count / max(1, len(steps))) * 100)
    current = next((step for step in steps if not step["done"]), steps[-1])
    return {
        "percent": percent,
        "phase": "all services ready" if percent >= 100 else f"waiting for {current['label']}",
        "ready": percent >= 100,
        "componentsReady": percent >= 100,
        "done": done_count,
        "total": len(steps),
        "source": "fast_control_api",
        "steps": steps,
    }


def build_default_commands() -> list[dict[str, str]]:
    return build_fast_control_default_commands()


def build_control_state(health: dict[str, Any]) -> dict[str, Any]:
    legacy = dict(health.get("legacyServices") or {})
    services_by_id = _service_by_id(health)
    boot_progress = build_boot_progress(health)
    control_ready = (services_by_id.get("control_page") or {}).get("state") == "up"
    bot_ready = bool(legacy.get("botReady"))
    chat_ready = bool(legacy.get("mainReady") and legacy.get("routerReady"))
    voice_ready = bool(legacy.get("ttsReady") and legacy.get("sttReady"))
    commands = build_default_commands()
    summary = str(health.get("summary") or health.get("overallState") or "unknown")
    bridge_status = local_bridge_status_snapshot()
    bridge_mic = dict(bridge_status.get("mic") or {})
    bridge_speaking = bool(bridge_status.get("speaking"))
    bridge_listening = bool(bridge_mic.get("captureActive"))
    control_plane = build_control_plane_state(bot_ready=bot_ready)
    return {
        "ok": True,
        "generatedAt": time.time(),
        "mode": "docker_fast_control",
        "localUrl": f"http://127.0.0.1:{PUBLIC_CONTROL_PORT}/",
        "bootProgress": boot_progress,
        "ui": {
            "mode": "default",
            "submode": (
                "voice-speaking"
                if bridge_speaking
                else "voice-listening"
                if bridge_listening
                else "idle"
                if bot_ready
                else "booting"
            ),
            "reason": "docker_fast_control",
        },
        "commands": commands,
        "allCommands": commands,
        "controlPagePanels": build_control_page_panel_state(),
        "chat": {
            "messages": default_chat_messages(),
            "inputEnabled": chat_ready,
        },
        "voice": {
            "outputMode": "windows_local_bridge" if bridge_status.get("enabled") else "docker_service",
            "channelName": "로컬 마이크" if bridge_listening else "없음",
            "listening": bridge_listening,
            "speaking": bridge_speaking,
            "ttsTargetName": "로컬 스피커" if bridge_speaking else "없음",
            "localBridge": bridge_status,
        },
        "restart": dict(RESTART_REQUEST),
        "shutdown": dict(SHUTDOWN_REQUEST),
        "runtime": {
            "summary": summary,
            "services": {
                "controlReady": control_ready,
                "botReady": bot_ready,
                "mainReady": bool(legacy.get("mainReady")),
                "routerReady": bool(legacy.get("routerReady")),
                "subReady": bool(legacy.get("subReady")),
                "ttsReady": bool(legacy.get("ttsReady")),
                "sttReady": bool(legacy.get("sttReady")),
                "visionReady": bool(legacy.get("visionReady")),
                "chatReady": chat_ready,
                "voiceReady": voice_ready,
            },
            "controlPlane": control_plane,
            "bootProgress": boot_progress,
            "serviceHealth": health,
        },
        "statusText": control_plane["statusText"],
    }


async def fast_control_probe_runner(service: ServiceSpec, check: HealthProbeSpec) -> dict[str, Any]:
    if service.id == "bot_api":
        target = f"{check.host}:{check.port}{check.path}"
        return {
            "kind": check.kind,
            "ok": True,
            "reason": "fast_control_self",
            "target": target,
            "status": 200 if check.kind == "http" else None,
            "elapsedMs": 0.0,
        }
    return await default_probe_runner(service, check)


async def health_handler(_: web.Request) -> web.StreamResponse:
    return json_response({"ok": True, "role": "fast-control-bot-api", "port": PORT})


async def state_handler(_: web.Request) -> web.StreamResponse:
    manifest = load_service_manifest()
    health = await collect_runtime_health(manifest=manifest, probe_runner=fast_control_probe_runner)
    return json_response(build_control_state(health))


async def chat_handler(request: web.Request) -> web.StreamResponse:
    try:
        payload = await request.json()
    except Exception:
        return json_response({"ok": False, "error": "invalid_json"}, status=400)
    text = clean_text((payload or {}).get("text"))
    if not text:
        return json_response({"ok": False, "error": "empty_text"}, status=400)
    source = clean_text((payload or {}).get("source")) or "control_page"
    append_chat_message("user", "정훈", text, source=source)
    normalized = text.lower()
    try:
        if normalized in {"/help", "help"}:
            reply = "사용 가능: /status, /memory, /voice status, 일반 대화. 로컬 음성은 Windows local I/O bridge가 담당해."
        elif normalized in {"/status", "status"}:
            manifest = load_service_manifest()
            health = await collect_runtime_health(manifest=manifest, probe_runner=fast_control_probe_runner)
            reply = str(health.get("summary") or health.get("overallState") or "runtime status unavailable")
        elif (memory_action := detect_memory_panel_action(text)) is not None:
            reply = execute_memory_panel_action(memory_action)
        elif normalized in {"/voice", "/voice status", "voice status"}:
            bridge_status = local_bridge_status_snapshot()
            ready = bool(bridge_status.get("ready"))
            error = clean_text(bridge_status.get("lastError"))
            reply = f"Windows local I/O bridge: {'ready' if ready else 'not ready'}"
            if bridge_status.get("stale"):
                reply += f" | stale {bridge_status.get('ageSec')}s"
            if error:
                reply += f" | {error}"
        elif normalized in {"/restart", "restart"}:
            request_local_restart(source=source, reason="chat_command")
            reply = local_restart_requested_reply()
        elif normalized in {"/shutdown", "shutdown"}:
            request_local_shutdown(source=source, reason="chat_command")
            reply = local_shutdown_requested_reply()
        else:
            if should_queue_local_bridge_speech(source):
                reply, queued_speech_count = await ask_main_llm_and_queue_speech(text, source=source)
            else:
                reply = await ask_main_llm(text, source=source)
            if not reply:
                reply = "응답이 비어 있었어. 다시 한 번 말해줘."
    except Exception as exc:
        reply = f"처리 중 오류가 났어: {exc}"
    append_chat_message("assistant", "Evelyn", reply, source="fast_control_api")
    if should_queue_local_bridge_speech(source) and queued_speech_count <= 0:
        queue_local_bridge_speech(reply, source=source)
    manifest = load_service_manifest()
    health = await collect_runtime_health(manifest=manifest, probe_runner=fast_control_probe_runner)
    return json_response({"ok": True, "reply": reply, "state": build_control_state(health)})


async def write_stream_event(response: web.StreamResponse, payload: dict[str, Any]) -> None:
    await response.write((json.dumps(payload, ensure_ascii=False) + "\n").encode("utf-8"))


async def chat_stream_handler(request: web.Request) -> web.StreamResponse:
    try:
        payload = await request.json()
    except Exception:
        return json_response({"ok": False, "error": "invalid_json"}, status=400)
    text = clean_text((payload or {}).get("text"))
    if not text:
        return json_response({"ok": False, "error": "empty_text"}, status=400)
    source = clean_text((payload or {}).get("source")) or "local_bridge"
    append_chat_message("user", "?뺥썕", text, source=source)

    response = web.StreamResponse(
        status=200,
        headers={
            "Content-Type": "application/x-ndjson; charset=utf-8",
            "Cache-Control": "no-cache",
            "X-Accel-Buffering": "no",
        },
    )
    await response.prepare(request)

    started_at = time.perf_counter()
    first_sentence_ms: float | None = None
    raw_parts: list[str] = []
    clean_seen_len = 0
    sentence_buffer = ""
    reply = ""
    try:
        pre_llm_reply = await resolve_pre_llm_reply(text, source=source)
        if pre_llm_reply is not None:
            reply = pre_llm_reply
            await write_stream_event(response, {"type": "sentence", "text": reply, "elapsedMs": 0.0})
            first_sentence_ms = 0.0
        else:
            async for delta in iter_main_llm_deltas(text, source=source):
                raw_parts.append(delta)
                cleaned = visible_text("".join(raw_parts))
                new_text = cleaned[clean_seen_len:]
                clean_seen_len = len(cleaned)
                if not new_text:
                    continue
                sentence_buffer += new_text
                chunks, sentence_buffer = pop_speakable_chunks(sentence_buffer)
                for chunk in chunks:
                    elapsed_ms = (time.perf_counter() - started_at) * 1000.0
                    if first_sentence_ms is None:
                        first_sentence_ms = elapsed_ms
                    await write_stream_event(
                        response,
                        {"type": "sentence", "text": chunk, "elapsedMs": round(elapsed_ms, 1)},
                    )
            tail_chunks, sentence_buffer = pop_speakable_chunks(sentence_buffer, force=True)
            for chunk in tail_chunks:
                elapsed_ms = (time.perf_counter() - started_at) * 1000.0
                if first_sentence_ms is None:
                    first_sentence_ms = elapsed_ms
                await write_stream_event(
                    response,
                    {"type": "sentence", "text": chunk, "elapsedMs": round(elapsed_ms, 1)},
                )
            reply = visible_text("".join(raw_parts))
            if not reply:
                reply = "답변이 비어 있었어. 다시 한 번 말해줘."
        append_chat_message("assistant", "Evelyn", reply, source="fast_control_api_stream")
        await write_stream_event(
            response,
            {
                "type": "done",
                "ok": True,
                "reply": reply,
                "firstSentenceMs": round(first_sentence_ms, 1) if first_sentence_ms is not None else None,
                "elapsedMs": round((time.perf_counter() - started_at) * 1000.0, 1),
            },
        )
    except Exception as exc:
        await write_stream_event(response, {"type": "error", "ok": False, "error": repr(exc)})
    await response.write_eof()
    return response


async def local_bridge_status_handler(request: web.Request) -> web.StreamResponse:
    speak_requests: list[dict[str, Any]] = []
    if request.method == "POST":
        try:
            payload = await request.json()
        except Exception:
            payload = {}
        if isinstance(payload, dict):
            LOCAL_BRIDGE_STATUS.update(payload)
        LOCAL_BRIDGE_STATUS["enabled"] = True
        LOCAL_BRIDGE_STATUS["updatedAt"] = time.time()
        speak_requests = drain_local_bridge_speak_requests()
    return json_response(
        {
            "ok": True,
            "localBridge": local_bridge_status_snapshot(),
            "speakRequests": speak_requests,
            "outputDeviceRequest": dict(LOCAL_BRIDGE_OUTPUT_DEVICE_REQUEST)
            if LOCAL_BRIDGE_OUTPUT_DEVICE_REQUEST.get("outputDevice")
            else {},
            "restart": dict(RESTART_REQUEST),
            "shutdown": dict(SHUTDOWN_REQUEST),
        }
    )


async def local_bridge_output_device_handler(request: web.Request) -> web.StreamResponse:
    if request.method == "GET":
        return json_response(
            {
                "ok": True,
                "selection": dict(LOCAL_BRIDGE_OUTPUT_DEVICE_REQUEST),
                "localBridge": local_bridge_status_snapshot(),
            }
        )
    try:
        payload = await request.json()
    except Exception:
        return json_response({"ok": False, "error": "invalid_json"}, status=400)
    output_device = clean_text((payload or {}).get("outputDevice"))
    if not output_device:
        return json_response({"ok": False, "error": "missing_output_device"}, status=400)
    selection = set_local_bridge_output_device(
        output_device,
        source=clean_text((payload or {}).get("source")) or "control_page",
    )
    return json_response(
        {
            "ok": True,
            "selection": selection,
            "localBridge": local_bridge_status_snapshot(),
        }
    )


async def local_bridge_test_tts_handler(request: web.Request) -> web.StreamResponse:
    try:
        payload = await request.json()
    except Exception:
        payload = {}
    text = clean_text((payload or {}).get("text")) or "이블린 오디오 테스트입니다."
    queued = queue_local_bridge_speech(text, source="control_page_audio_test")
    if queued is None:
        return json_response({"ok": False, "error": "local_bridge_not_ready"}, status=409)
    return json_response({"ok": True, "request": queued, "localBridge": local_bridge_status_snapshot()})


async def shutdown_handler(request: web.Request) -> web.StreamResponse:
    try:
        payload = await request.json()
    except Exception:
        payload = {}
    source = clean_text((payload or {}).get("source")) or "control_page"
    reason = clean_text((payload or {}).get("reason")) or "shutdown_endpoint"
    return json_response(request_local_shutdown(source=source, reason=reason))


def create_app() -> web.Application:
    app = web.Application(middlewares=[reject_browser_origin_middleware])
    app.router.add_get("/health", health_handler)
    app.router.add_get("/api/control-page/state", state_handler)
    app.router.add_post("/api/control-page/chat", chat_handler)
    app.router.add_post("/api/control-page/chat-stream", chat_stream_handler)
    app.router.add_post("/api/control-page/shutdown", shutdown_handler)
    app.router.add_get("/api/local-bridge/status", local_bridge_status_handler)
    app.router.add_post("/api/local-bridge/status", local_bridge_status_handler)
    app.router.add_get("/api/local-bridge/output-device", local_bridge_output_device_handler)
    app.router.add_post("/api/local-bridge/output-device", local_bridge_output_device_handler)
    app.router.add_post("/api/local-bridge/test-tts", local_bridge_test_tts_handler)
    return app


def main() -> None:
    web.run_app(create_app(), host=HOST, port=PORT)


if __name__ == "__main__":
    main()
