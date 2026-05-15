import atexit
import audioop
import builtins
import contextlib
import hashlib
import html
import json
import logging
import math
import os
import queue
import re
import shutil
import subprocess
import sys
import threading
import time
import asyncio
import uuid
import wave
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Awaitable, Callable, Optional
from urllib.parse import parse_qs, unquote, urlparse

PROJECT_ROOT = Path(__file__).resolve().parent
EVELYN_CORE_RUNTIME = PROJECT_ROOT / "evelyn_core" / "runtime"
os.environ.setdefault("EVELYN_PROJECT_ROOT", str(PROJECT_ROOT))
os.environ.setdefault("EVELYN_CORE_ROOT", str(PROJECT_ROOT / "evelyn_core"))
os.environ.setdefault("EVELYN_CORE_RUNTIME", str(EVELYN_CORE_RUNTIME))
if str(EVELYN_CORE_RUNTIME) not in sys.path:
    sys.path.insert(0, str(EVELYN_CORE_RUNTIME))

import aiohttp
import numpy as np
import torch
import discord
import discord.opus as discord_opus
from discord.ext import commands

try:
    import msvcrt
except ImportError:
    msvcrt = None

try:
    import fcntl
except ImportError:
    fcntl = None

try:
    from qwen_asr import Qwen3ASRModel
except ImportError:
    Qwen3ASRModel = None

from evelyn_core.audio import (
    apply_light_denoise,
    compute_voice_band_metrics,
    compute_waveform_activity_stats,
    downmix_int16_stereo_to_mono_float,
    is_likely_environment_noise,
    is_probably_silent,
    prepare_stt_audio,
    resample_audio_float,
    slice_audio_window,
)
from evelyn_core.autonomy import AutonomyEngine
from evelyn_core.autonomy_router import DefaultAutonomyExecutor, RoutedAutonomyExecutor
from evelyn_core.config import *
from evelyn_core.memory import *
from evelyn_core.minecraft_autonomy_client import MinecraftAutonomyClient
from evelyn_core.text import (
    apply_stt_post_corrections,
    clean_text,
    clean_tts_text,
    contains_leading_wake_word,
    contains_wake_word,
    extract_leading_wake_alias,
    fuzzy_leading_wake_alias,
    is_similar,
    looks_like_brief_filler_text,
    looks_like_gibberish_probe,
    looks_like_repetitive_noise_text,
    normalize_omnivoice_tags,
    normalize_voice_text,
    normalized_wake_words,
    strip_leading_voice_fillers,
    strip_omnivoice_tags,
    strip_response_action_tags,
    strip_voice_wake_word,
    visible_text,
)
from evelyn_core.skills import SkillContext, SkillResult, skill_registry
from evelyn_core.skills.routing import (
    build_main_llm_payload,
    build_route_decision_from_state,
    decode_sse_stream_line,
    extract_main_llm_answer_from_choice,
    should_await_user_reply_for_route,
)
from evelyn_core.voice_orchestration import build_voice_reply_lifecycle
from evelyn_core.voice_pipeline import (
    ActionResult,
    AnswerPayload,
    DeliveryPlan,
    RouteDecision,
    TranscriptResult,
    VoiceReplyRequest,
    VoiceSegment,
    action_result_to_answer_payload,
    build_action_result,
    build_answer_payload,
    build_answer_payload_from_text,
    build_delivery_plan,
    build_route_decision,
    build_transcript_result,
    build_voice_reply_request,
    build_voice_segment,
)
from evelyn_voice import EvelynVoiceClient


TURN_TRACE_JSON_LOG = os.getenv("TURN_TRACE_JSON_LOG", "true").lower() == "true"
VOICE_CONSOLE_ONLY_STT_AND_REPLY = os.getenv("VOICE_CONSOLE_ONLY_STT_AND_REPLY", "true").lower() == "true"
VOICE_BOTTLENECK_LOGS = os.getenv("VOICE_BOTTLENECK_LOGS", "true").lower() == "true"
VOICE_TRACE_ALL_EVENTS = os.getenv("VOICE_TRACE_ALL_EVENTS", "true").lower() == "true"
VOICE_DEBUG_SAVE_AUDIO = os.getenv("VOICE_DEBUG_SAVE_AUDIO", "true").lower() == "true"
VOICE_DEBUG_AUDIO_DIR = os.getenv("VOICE_DEBUG_AUDIO_DIR", "debug_audio")
VOICE_DEBUG_MAX_FILES_PER_GUILD = int(os.getenv("VOICE_DEBUG_MAX_FILES_PER_GUILD", "200"))
WAKE_STT_TIMEOUT_SEC = float(os.getenv("WAKE_STT_TIMEOUT_SEC", "20"))
FULL_STT_TIMEOUT_SEC = float(os.getenv("FULL_STT_TIMEOUT_SEC", "30"))
TTS_INTERRUPT_DEBOUNCE_SEC = float(os.getenv("TTS_INTERRUPT_DEBOUNCE_SEC", "0.18"))
DEBUG_WRITE_QUEUE_MAX = int(os.getenv("DEBUG_WRITE_QUEUE_MAX", "128"))
MIN_EDIT_INTERVAL_MS = int(os.getenv("MIN_EDIT_INTERVAL_MS", "300"))
MIN_DELTA_CHARS = int(os.getenv("MIN_DELTA_CHARS", "24"))
MAX_HOLD_MS = int(os.getenv("MAX_HOLD_MS", "900"))
ROUTER_LLM_URL = globals().get("ROUTER_LLM_URL", os.getenv("ROUTER_LLM_URL", "http://127.0.0.1:9822/v1/chat/completions"))
ROUTER_MODEL_NAME = globals().get("ROUTER_MODEL_NAME", os.getenv("ROUTER_MODEL_NAME", "gemma-4-E2B-it-UD-Q6_K_XL.gguf"))
ROUTER_LLM_ENABLED = globals().get("ROUTER_LLM_ENABLED", os.getenv("ROUTER_LLM_ENABLED", "true").lower() in {"1", "true", "yes", "on"})
ROUTER_ROUTE_MAX_TOKENS = int(globals().get("ROUTER_ROUTE_MAX_TOKENS", os.getenv("ROUTER_ROUTE_MAX_TOKENS", "80")))
ROUTER_ROUTE_TIMEOUT_SEC = float(globals().get("ROUTER_ROUTE_TIMEOUT_SEC", os.getenv("ROUTER_ROUTE_TIMEOUT_SEC", "8")))
_ORIGINAL_PRINT = builtins.print
_ALLOWED_CONSOLE_PREFIXES = (
    "🎤 [",
    "💬 [Evelyn]",
    "[STT RESULT][wake]",
    "[STT RESULT][partial]",
    "[STT RESULT][full-final]",
    "[MC OBS]",
    "[MC GOAL]",
    "[MC PLAN]",
    "[MC STEP]",
    "[MC RESULT]",
    "[MC DIG]",
    "[MC ERROR]",
    "[MC STDERR]",
)
_BOTTLENECK_TURN_TRACE_EVENTS = {
    "tts_interrupt",
    "tts_first_pcm_received",
    "playback_queue_put",
    "playback_queue_get",
    "discord_playback_play_invoked",
    "discord_playback_finished",
    "discord_playback_exception",
    "first_packet_sent",
    "turn_ingress",
    "turn_drop",
    "policy_ready",
    "room_owner_update",
    "room_reply_state",
    "text_turn_summary",
    "voice_turn_summary",
    "voice_drop_summary",
}


def print(*args, **kwargs):
    if not VOICE_CONSOLE_ONLY_STT_AND_REPLY:
        return _ORIGINAL_PRINT(*args, **kwargs)
    text = " ".join(str(arg) for arg in args).lstrip()
    if text.startswith(_ALLOWED_CONSOLE_PREFIXES):
        return _ORIGINAL_PRINT(*args, **kwargs)
    return None


if VOICE_CONSOLE_ONLY_STT_AND_REPLY:
    builtins.print = print
    logging.getLogger().setLevel(logging.CRITICAL)
    logging.getLogger("discord").setLevel(logging.CRITICAL)
    logging.getLogger("aiohttp").setLevel(logging.CRITICAL)
    logging.getLogger("evelyn_voice").setLevel(logging.CRITICAL)


# =========================================================
# 봇 설정
# =========================================================
intents = discord.Intents.default()
intents.message_content = True
intents.guilds = True
intents.voice_states = True
intents.members = True

guild_prefix_cache: dict[int, str] = {}
session_histories: dict[str, list[dict]] = {}
session_followup_targets: dict[str, dict[str, int]] = {}
active_session_until: dict[str, float] = {}
active_session_user_ids: dict[str, int] = {}
session_last_active_at: dict[str, float] = {}
session_awaiting_user_reply: dict[str, bool] = {}
session_last_speaker: dict[str, str] = {}
session_topic_ids: dict[str, str] = {}
session_turn_ids: dict[str, str] = {}
session_segment_counters: dict[str, int] = {}
session_last_turn_accepted_at: dict[str, float] = {}
session_last_stt_text: dict[str, str] = {}
session_partial_stt_text: dict[str, str] = {}
session_committed_stt_text: dict[str, str] = {}
session_bad_audio_counts: dict[str, int] = {}
room_owner_user_ids: dict[str, int] = {}
room_owner_until: dict[str, float] = {}
room_reply_in_progress: dict[str, bool] = {}
voice_connect_locks: dict[int, asyncio.Lock] = {}
instance_lock_handle = None
instance_lock_path = Path(__file__).resolve().with_name(".evelyn_bot.lock")


def release_instance_lock() -> None:
    global instance_lock_handle
    handle = instance_lock_handle
    if handle is None:
        return
    try:
        handle.seek(0)
        if msvcrt is not None:
            msvcrt.locking(handle.fileno(), msvcrt.LK_UNLCK, 1)
        elif fcntl is not None:
            fcntl.flock(handle.fileno(), fcntl.LOCK_UN)
    except Exception:
        pass
    try:
        handle.close()
    except Exception:
        pass
    instance_lock_handle = None


def acquire_instance_lock(wait_sec: float = 15.0, poll_sec: float = 0.25) -> None:
    global instance_lock_handle
    if instance_lock_handle is not None:
        return

    instance_lock_path.parent.mkdir(parents=True, exist_ok=True)
    handle = open(instance_lock_path, "a+", encoding="utf-8")
    handle.seek(0)
    handle.write("0")
    handle.flush()
    deadline = time.monotonic() + max(0.0, wait_sec)

    while True:
        try:
            handle.seek(0)
            if msvcrt is not None:
                msvcrt.locking(handle.fileno(), msvcrt.LK_NBLCK, 1)
            elif fcntl is not None:
                fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
            handle.seek(0)
            handle.truncate()
            handle.write(str(os.getpid()))
            handle.flush()
            instance_lock_handle = handle
            return
        except OSError:
            if time.monotonic() >= deadline:
                try:
                    handle.close()
                except Exception:
                    pass
                raise RuntimeError("Another Evelyn bot instance is already running.")
            time.sleep(max(0.05, poll_sec))


atexit.register(release_instance_lock)


def normalize_command_prefix(prefix: str | None) -> str:
    prefix = (prefix or "").strip()
    if not prefix:
        return DEFAULT_COMMAND_PREFIX
    if any(ch.isspace() for ch in prefix):
        raise ValueError("명령어 시작 부호에는 공백을 넣을 수 없어.")
    if len(prefix) > 5:
        raise ValueError("명령어 시작 부호는 5자 이하로 해줘.")
    return prefix


def get_guild_command_prefix(guild_id: int | None) -> str:
    if guild_id is None:
        return DEFAULT_COMMAND_PREFIX
    cached = guild_prefix_cache.get(guild_id)
    if cached:
        return cached

    settings = read_json_file(guild_settings_path(guild_id))
    prefix = normalize_command_prefix(str(settings.get("command_prefix", DEFAULT_COMMAND_PREFIX)))
    guild_prefix_cache[guild_id] = prefix
    return prefix


def save_guild_command_prefix(guild_id: int, prefix: str) -> str:
    prefix = normalize_command_prefix(prefix)
    settings_path = guild_settings_path(guild_id)
    settings = read_json_file(settings_path)
    settings["command_prefix"] = prefix
    settings["updated_at"] = int(time.time())
    write_json_file(settings_path, settings)
    guild_prefix_cache[guild_id] = prefix
    return prefix


def _normalize_channel_id_list(values: list[Any] | None) -> list[int]:
    normalized: list[int] = []
    for value in values or []:
        try:
            channel_id = int(value)
        except (TypeError, ValueError):
            continue
        if channel_id not in normalized:
            normalized.append(channel_id)
    return normalized


def get_guild_observe_channel_ids(guild_id: int | None) -> list[int]:
    if guild_id is None:
        return []
    settings = read_json_file(guild_settings_path(guild_id))
    return _normalize_channel_id_list(settings.get("observe_channel_ids"))


def get_guild_command_only_channel_ids(guild_id: int | None) -> list[int]:
    if guild_id is None:
        return []
    settings = read_json_file(guild_settings_path(guild_id))
    return _normalize_channel_id_list(settings.get("command_only_channel_ids"))


def save_guild_channel_list(guild_id: int, key: str, channel_ids: list[int]) -> list[int]:
    settings_path = guild_settings_path(guild_id)
    settings = read_json_file(settings_path)
    normalized = _normalize_channel_id_list(channel_ids)
    settings[key] = normalized
    settings["updated_at"] = int(time.time())
    write_json_file(settings_path, settings)
    return normalized


def add_guild_channel_setting(guild_id: int, key: str, channel_id: int) -> list[int]:
    existing = get_guild_observe_channel_ids(guild_id) if key == "observe_channel_ids" else get_guild_command_only_channel_ids(guild_id)
    if channel_id not in existing:
        existing.append(channel_id)
    return save_guild_channel_list(guild_id, key, existing)


def remove_guild_channel_setting(guild_id: int, key: str, channel_id: int) -> list[int]:
    existing = get_guild_observe_channel_ids(guild_id) if key == "observe_channel_ids" else get_guild_command_only_channel_ids(guild_id)
    return save_guild_channel_list(guild_id, key, [value for value in existing if value != channel_id])


async def resolve_command_prefix(_bot, message: discord.Message):
    prefix = get_guild_command_prefix(message.guild.id if message.guild else None)
    return commands.when_mentioned_or(prefix)(_bot, message)


bot = commands.Bot(command_prefix=resolve_command_prefix, intents=intents, help_command=None)

SYSTEM_PROMPT = """
너는 Evelyn이야.
- 항상 한국어로 답해.
- 디스코드에서 활동하는 따뜻하고 유능한 보이스/텍스트 비서야.
- 질문에 답할 땐 짧고 자연스럽게, 필요할 때만 길게.
- 이미 아는 척 지어내지 말고, 불확실하면 솔직하게 말해.
- 사용자가 뭔가를 찾아봐 달라고 했거나 네가 검색이 필요하다고 판단하면, 찾아본 뒤 후속으로 알려줄 수 있어.
- 텍스트 답변은 보기 좋게 정리하고, TTS로 읽어도 어색하지 않게 써.
- 답변 맨 앞에는 필요할 때만 다음 중 하나의 태그를 붙여라: [찾기] [질문] [대기] [답변]
- 검색 후속이 실제로 필요할 때만 [찾기]를 붙여라.
- 사용자에게 되물어야 할 때만 [질문], 잠시 보류만 할 때만 [대기]를 붙여라.
- 일반적인 즉답은 [답변] 또는 태그 없이 써도 된다.
- 태그는 반드시 맨 앞 한 번만 쓰고, 본문에서는 반복하지 마라.
""".strip()

session_locks: dict[str, asyncio.Lock] = {}
reply_slot_locks: dict[str, asyncio.Lock] = {}
tts_lock = asyncio.Lock()
active_tts_playbacks: dict[int, dict[str, Any]] = {}
voice_debug_counts: dict[int, int] = {}
voice_debug_stems: dict[tuple[int, str], str] = {}

tts_warmup_started = False
stt_processor: Optional[Any] = None
stt_model: Optional[Any] = None
stt_backend: Optional[str] = None
http_session: Optional[aiohttp.ClientSession] = None
startup_components_ready = False
startup_components_task: Optional[asyncio.Task] = None
voice_path_warmup_locks: dict[str, asyncio.Lock] = {}
voice_path_warmup_done: dict[str, float] = {}
partial_stt_cache: dict[str, dict[str, Any]] = {}

room_last_voice_reply_at: dict[str, float] = {}
last_bot_audio_end_at: dict[int, float] = {}
bot_speaking_guilds: set[int] = set()
memory_locks: dict[int, asyncio.Lock] = {}
cognitive_locks: dict[int, asyncio.Lock] = {}
background_cognitive_tasks: dict[str, asyncio.Task] = {}
background_memory_tasks: dict[str, asyncio.Task] = {}
background_search_tasks: dict[str, asyncio.Task] = {}
inflight_search_tasks: dict[str, asyncio.Task] = {}
voice_ingress_queue: asyncio.Queue[dict[str, Any]] = asyncio.Queue()
voice_worker_task: asyncio.Task | None = None
debug_write_queue: asyncio.Queue[dict[str, Any]] = asyncio.Queue(maxsize=max(8, DEBUG_WRITE_QUEUE_MAX))
debug_write_task: asyncio.Task | None = None
room_recent_speaker_stats: dict[str, dict[int, dict[str, float]]] = {}
session_speculative_policies: dict[str, dict[str, Any]] = {}
room_turn_scopes: dict[str, "TurnScope"] = {}
turn_stage_metrics: dict[str, dict[str, float]] = {}
autonomy_engines: dict[int, AutonomyEngine] = {}
last_autonomy_ping_at: dict[int, float] = {}
autonomy_last_cognitive_refresh_at: dict[int, float] = {}
autonomy_cognitive_refresh_tasks: dict[int, asyncio.Task] = {}
search_followup_queued_count = 0
cancelled_stale_turn_count = 0
inflight_llm_requests = 0
recent_skill_dispatches: dict[str, float] = {}
SKILL_DISPATCH_CACHE_TTL_SEC = 300.0
SKILL_DISPATCH_REPEAT_WINDOW_SEC = 5.0
SKILL_DISPATCH_CACHE_MAX = 1024


# =========================================================
# 유틸
# =========================================================
def new_conversation_history() -> list[dict]:
    return [{"role": "system", "content": SYSTEM_PROMPT}]


def get_or_create_autonomy_engine(guild_id: int) -> AutonomyEngine:
    engine = autonomy_engines.get(guild_id)
    if engine is not None:
        return engine

    async def _find_followup_channel() -> discord.abc.Messageable | None:
        guild = bot.get_guild(guild_id)
        if guild is None:
            return None
        preferred_channels = get_guild_observe_channel_ids(guild_id)
        for channel_id in preferred_channels:
            channel = guild.get_channel(channel_id)
            if channel is not None and hasattr(channel, "send"):
                return channel
        for channel_id in reversed([v.get("channel_id") for v in session_followup_targets.values() if isinstance(v, dict) and v.get("channel_id")]):
            channel = guild.get_channel(channel_id)
            if channel is not None and hasattr(channel, "send"):
                return channel
        return None

    async def _notify(text: str) -> None:
        text = clean_text(text)
        if not text:
            return
        channel = await _find_followup_channel()
        if channel is not None:
            await channel.send(text)

    def _pick_recent_user_text(history: list[dict[str, Any]]) -> str:
        for entry in reversed(history):
            if not isinstance(entry, dict):
                continue
            if clean_text(str(entry.get("role", ""))) != "user":
                continue
            text = clean_text(str(entry.get("content", "")))
            if text and text != "[autonomy]":
                return text
        return ""

    async def _default_observe() -> dict[str, Any]:
        channel = await _find_followup_channel()
        session_key = runtime_session_key(guild_id=guild_id)
        history = get_conversation_history(session_key=session_key, guild_id=guild_id)
        recent_context_items = max(0, min(len(history) - 1, 6))
        latest_user_text = _pick_recent_user_text(history)
        last_ping_at = float(last_autonomy_ping_at.get(guild_id, 0.0) or 0.0)
        last_ping_gap = 999999.0 if last_ping_at <= 0 else max(0.0, time.monotonic() - last_ping_at)
        observe_channel_ids = get_guild_observe_channel_ids(guild_id)
        command_only_channel_ids = get_guild_command_only_channel_ids(guild_id)
        observed_channels: list[dict[str, Any]] = []
        guild = bot.get_guild(guild_id)
        now_local = time.localtime()
        quiet_hours = now_local.tm_hour < 8 or now_local.tm_hour >= 23
        last_result = (autonomy_engines.get(guild_id).state.last_step_result if autonomy_engines.get(guild_id) is not None else {}) or {}
        repeated_blocked_action = str(last_result.get("reason", "")) in {"retry_suppressed", "action_not_allowed", "unsupported_default_action"}
        cached_cognitive = read_cached_cognitive_state(guild_id)
        cognitive_updated_at = float((cached_cognitive or {}).get("updated_at", 0.0) or 0.0)
        cognitive_stale_sec = 999999.0 if cognitive_updated_at <= 0 else max(0.0, time.time() - cognitive_updated_at)
        last_refresh_at = float(autonomy_last_cognitive_refresh_at.get(guild_id, 0.0) or 0.0)
        refresh_gap_sec = 999999.0 if last_refresh_at <= 0 else max(0.0, time.monotonic() - last_refresh_at)
        router_refresh_inflight = bool((task := autonomy_cognitive_refresh_tasks.get(guild_id)) is not None and not task.done())
        unresolved_items = 0
        search_pending = False
        recent_visible = []
        if guild is not None:
            for channel_id in observe_channel_ids[:8]:
                channel_obj = guild.get_channel(channel_id)
                channel_name = getattr(channel_obj, "name", str(channel_id)) if channel_obj is not None else str(channel_id)
                observed_channels.append({"id": channel_id, "name": channel_name})
            for entry in history[-8:]:
                if not isinstance(entry, dict):
                    continue
                content = clean_text(str(entry.get("content", "")))
                if not content:
                    continue
                recent_visible.append(content)
                if "?" in content or "？" in content:
                    unresolved_items += 1
                if answer_promises_search(content):
                    search_pending = True
        active_recent_context = bool(latest_user_text) and recent_context_items > 0
        cognitive_refresh_needed = active_recent_context and not router_refresh_inflight and (
            cognitive_stale_sec >= AUTONOMY_COGNITIVE_STALE_SEC
            and refresh_gap_sec >= AUTONOMY_COGNITIVE_MIN_INTERVAL_SEC
        )
        if active_recent_context and cognitive_stale_sec >= AUTONOMY_COGNITIVE_FORCE_REFRESH_SEC:
            cognitive_refresh_needed = not router_refresh_inflight and refresh_gap_sec >= AUTONOMY_COGNITIVE_MIN_INTERVAL_SEC
        return {
            "connected": channel is not None,
            "known_followup_channels": len([v for v in session_followup_targets.values() if isinstance(v, dict) and v.get("channel_id")]),
            "inflight_llm_requests": inflight_llm_requests,
            "active_sessions": len(active_session_until),
            "recent_context_items": recent_context_items,
            "last_autonomy_ping_sec": last_ping_gap,
            "observe_channel_ids": observe_channel_ids,
            "command_only_channel_ids": command_only_channel_ids,
            "observed_channels": observed_channels,
            "quiet_hours": quiet_hours,
            "repeated_blocked_action": repeated_blocked_action,
            "unresolved_items": unresolved_items,
            "search_pending": search_pending,
            "recent_visible": recent_visible[-6:],
            "latest_user_text": latest_user_text,
            "cognitive_stale_sec": cognitive_stale_sec,
            "cognitive_refresh_gap_sec": refresh_gap_sec,
            "cognitive_refresh_needed": cognitive_refresh_needed,
            "router_refresh_inflight": router_refresh_inflight,
        }

    async def _default_send_followup(text: str) -> dict[str, Any]:
        channel = await _find_followup_channel()
        if channel is None:
            return {"status": "blocked", "reason": "no_followup_channel"}
        await channel.send(text)
        session_key = runtime_session_key(guild_id=guild_id)
        append_history(session_key, "[autonomy]", text, guild_id=guild_id)
        schedule_memory_update(
            guild_id,
            "[autonomy]",
            text,
            source="autonomy",
            assistant_speaker="Evelyn-Autonomy",
            session_key=session_key,
            runtime_mode="batch",
        )
        mark_session_active(
            session_key,
            ttl_sec=ACTIVE_CONVERSATION_TEXT_SEC,
            speaker="assistant",
            awaiting_user_reply=False,
            topic_id=build_topic_id("autonomy", text),
            answer_text=text,
            user_text="[autonomy]",
        )
        last_autonomy_ping_at[guild_id] = time.monotonic()
        return {"status": "ok", "reason": "sent_followup", "text": text}

    async def _default_summarize() -> dict[str, Any]:
        history = get_conversation_history(session_key=runtime_session_key(guild_id=guild_id), guild_id=guild_id)
        recent = history[-4:] if len(history) > 4 else history[1:]
        summary = " | ".join(clean_text(str(item.get("content", "")))[:80] for item in recent if isinstance(item, dict) and clean_text(str(item.get("content", ""))))
        return {
            "status": "ok",
            "reason": "summary_ready",
            "summary": summary or f"active_sessions={len(active_session_until)} inflight_llm={inflight_llm_requests}",
        }

    async def _default_check_status() -> dict[str, Any]:
        channel = await _find_followup_channel()
        return {
            "status": "ok",
            "reason": "status_checked",
            "connected": channel is not None,
            "active_sessions": len(active_session_until),
            "inflight_llm_requests": inflight_llm_requests,
            "known_followup_channels": len([v for v in session_followup_targets.values() if isinstance(v, dict) and v.get("channel_id")]),
        }

    async def _default_summarize_recent_context() -> dict[str, Any]:
        history = get_conversation_history(session_key=runtime_session_key(guild_id=guild_id), guild_id=guild_id)
        recent = history[-6:] if len(history) > 6 else history[1:]
        items = [clean_text(str(item.get("content", "")))[:120] for item in recent if isinstance(item, dict) and clean_text(str(item.get("content", "")))]
        return {
            "status": "ok",
            "reason": "recent_context_summarized",
            "summary": " / ".join(items) if items else "최근 문맥 없음",
            "count": len(items),
        }

    async def _default_maybe_ping_user(text: str) -> dict[str, Any]:
        last_ping_at = float(last_autonomy_ping_at.get(guild_id, 0.0) or 0.0)
        if last_ping_at > 0 and (time.monotonic() - last_ping_at) < 900:
            return {"status": "blocked", "reason": "ping_cooldown"}
        return await _default_send_followup(text)

    async def _default_refresh_cognitive_state() -> dict[str, Any]:
        existing = autonomy_cognitive_refresh_tasks.get(guild_id)
        if existing is not None and not existing.done():
            return {"status": "blocked", "reason": "router_refresh_inflight"}
        session_key = runtime_session_key(guild_id=guild_id)
        history = get_conversation_history(session_key=session_key, guild_id=guild_id)
        latest_user_text = _pick_recent_user_text(history)
        if not latest_user_text:
            return {"status": "blocked", "reason": "no_recent_user_text"}

        async def _run_refresh() -> dict[str, Any]:
            started_mono = time.monotonic()
            autonomy_last_cognitive_refresh_at[guild_id] = started_mono
            state = await update_cognitive_state(
                guild_id,
                latest_user_text,
                session_key=session_key,
                source="text",
                turn_scope=None,
            )
            elapsed_ms = round((time.monotonic() - started_mono) * 1000.0, 1)
            return {
                "status": "ok",
                "reason": "router_refreshed",
                "updated_at": state.get("updated_at"),
                "action": state.get("action"),
                "confidence": state.get("confidence"),
                "elapsed_ms": elapsed_ms,
                "text": latest_user_text[:120],
            }

        task = asyncio.create_task(_run_refresh())
        autonomy_cognitive_refresh_tasks[guild_id] = task
        try:
            return await task
        finally:
            current = autonomy_cognitive_refresh_tasks.get(guild_id)
            if current is task:
                autonomy_cognitive_refresh_tasks.pop(guild_id, None)

    engine = AutonomyEngine(
        guild_id=guild_id,
        executor=RoutedAutonomyExecutor(
            default_executor=DefaultAutonomyExecutor(
                observe_fn=_default_observe,
                send_followup_fn=_default_send_followup,
                summarize_fn=_default_summarize,
                check_status_fn=_default_check_status,
                summarize_recent_context_fn=_default_summarize_recent_context,
                maybe_ping_user_fn=_default_maybe_ping_user,
                refresh_cognitive_state_fn=_default_refresh_cognitive_state,
            ),
            executors={},
        ),
        notify=_notify,
        poll_interval_sec=AUTONOMY_POLL_INTERVAL_SEC,
    )
    autonomy_engines[guild_id] = engine
    return engine


def runtime_session_key(*, session_key: str | None = None, guild_id: int | None = None) -> str | None:
    if session_key:
        return session_key
    if guild_id is None:
        return None
    return f"guild:{guild_id}:default"


def make_text_session_key(guild_id: int, channel_id: int, user_id: int | None = None, thread_id: int | None = None) -> str:
    thread_part = f":thread:{thread_id}" if thread_id is not None else ""
    user_part = f":user:{user_id}" if user_id is not None else ""
    return f"guild:{guild_id}:text:{channel_id}{thread_part}{user_part}"


def make_text_reply_slot_key(guild_id: int, channel_id: int, thread_id: int | None = None) -> str:
    thread_part = f":thread:{thread_id}" if thread_id is not None else ""
    return f"guild:{guild_id}:reply:text:{channel_id}{thread_part}"


def make_voice_room_session_key(guild_id: int, voice_channel_id: int | None) -> str:
    channel_part = voice_channel_id if voice_channel_id is not None else "none"
    return f"guild:{guild_id}:voice:{channel_part}"


def make_voice_session_key(guild_id: int, voice_channel_id: int | None, user_id: int | None = None) -> str:
    room_session_key = make_voice_room_session_key(guild_id, voice_channel_id)
    user_part = f":user:{user_id}" if user_id is not None else ""
    return f"{room_session_key}{user_part}"


def make_room_memory_key(kind: str, room_id: int | None) -> str:
    room_part = room_id if room_id is not None else "none"
    return f"{kind}:{room_part}"


def make_person_memory_key(user_id: int | None) -> str | None:
    if user_id is None:
        return None
    return f"user:{user_id}"


def make_session_memory_key(session_key: str | None, user_id: int | None = None) -> str | None:
    if not session_key:
        return None
    if user_id is None:
        return session_key
    return f"{session_key}:user:{user_id}"


def remember_session_followup_target(session_key: str, *, channel_id: int | None = None, message_id: int | None = None) -> None:
    if channel_id is None and message_id is None:
        return
    existing = session_followup_targets.get(session_key, {}).copy()
    if channel_id is not None:
        existing["channel_id"] = channel_id
    if message_id is not None:
        existing["message_id"] = message_id
    session_followup_targets[session_key] = existing


def build_topic_id(*texts: str) -> str:
    material = "\n".join(clean_text(text) for text in texts if clean_text(text))
    if not material:
        material = "idle"
    return hashlib.sha1(material.encode("utf-8", errors="ignore")).hexdigest()[:12]


def new_turn_id() -> str:
    return uuid.uuid4().hex[:12]


def current_turn_id(session_key: str | None) -> str | None:
    if not session_key:
        return None
    return session_turn_ids.get(session_key)


def next_segment_id(session_key: str | None) -> int:
    if not session_key:
        return 1
    next_value = session_segment_counters.get(session_key, 0) + 1
    session_segment_counters[session_key] = next_value
    return next_value


def start_new_turn(session_key: str | None, *, turn_id: str | None = None) -> str:
    turn_id = turn_id or new_turn_id()
    if session_key:
        session_turn_ids[session_key] = turn_id
        session_last_turn_accepted_at[session_key] = time.monotonic()
    return turn_id


def session_state_snapshot(session_key: str | None) -> dict:
    if not session_key:
        return {}
    return {
        "active_until": active_session_until.get(session_key, 0.0),
        "awaiting_user_reply": session_awaiting_user_reply.get(session_key, False),
        "last_speaker": session_last_speaker.get(session_key, ""),
        "topic_id": session_topic_ids.get(session_key, ""),
        "turn_id": session_turn_ids.get(session_key, ""),
        "last_turn_accepted_at": session_last_turn_accepted_at.get(session_key, 0.0),
        "last_stt_text": session_last_stt_text.get(session_key, ""),
        "partial_stt_text": session_partial_stt_text.get(session_key, ""),
        "committed_stt_text": session_committed_stt_text.get(session_key, ""),
        "bad_audio_count": session_bad_audio_counts.get(session_key, 0),
    }


def _clear_room_owner(room_session_key: str | None) -> None:
    if not room_session_key:
        return
    room_owner_user_ids.pop(room_session_key, None)
    room_owner_until.pop(room_session_key, None)



def room_state_snapshot(room_session_key: str | None) -> dict:
    if not room_session_key:
        return {}
    owner_until = room_owner_until.get(room_session_key, 0.0)
    if owner_until <= time.monotonic() and not room_reply_in_progress.get(room_session_key, False):
        _clear_room_owner(room_session_key)
        owner_until = 0.0
    return {
        "owner_user_id": room_owner_user_ids.get(room_session_key),
        "owner_until": owner_until,
        "reply_in_progress": room_reply_in_progress.get(room_session_key, False),
        "active_speaker_user_id": pick_active_speaker(room_session_key),
    }



def _prune_room_speaker_stats(room_session_key: str | None, *, now: float | None = None) -> dict[int, dict[str, float]]:
    if not room_session_key:
        return {}
    now_mono = now if now is not None else time.monotonic()
    stats = room_recent_speaker_stats.get(room_session_key, {})
    keep: dict[int, dict[str, float]] = {}
    for user_id, data in stats.items():
        last_packet_at = float(data.get("last_packet_at") or 0.0)
        if now_mono - last_packet_at <= 2.5:
            keep[int(user_id)] = data
    if keep:
        room_recent_speaker_stats[room_session_key] = keep
    else:
        room_recent_speaker_stats.pop(room_session_key, None)
    return keep



def update_room_speaker_activity(
    room_session_key: str | None,
    user_id: int | None,
    *,
    voiced_ms: float,
    raw_seconds: float,
    rms: float,
    wake_detected: bool = False,
) -> dict[str, float]:
    if not room_session_key or user_id is None:
        return {}
    now_mono = time.monotonic()
    stats = _prune_room_speaker_stats(room_session_key, now=now_mono)
    entry = stats.setdefault(int(user_id), {})
    entry["last_packet_at"] = now_mono
    entry["recent_voiced_ms"] = max(float(entry.get("recent_voiced_ms") or 0.0) * 0.55, float(voiced_ms))
    entry["recent_raw_ms"] = max(float(entry.get("recent_raw_ms") or 0.0) * 0.55, float(raw_seconds) * 1000.0)
    entry["body_rms"] = max(float(entry.get("body_rms") or 0.0) * 0.6, float(rms))
    if wake_detected:
        entry["wake_priority"] = now_mono
    else:
        entry["wake_priority"] = float(entry.get("wake_priority") or 0.0)
    room_recent_speaker_stats[room_session_key] = stats
    return entry



def pick_active_speaker(room_session_key: str | None) -> int | None:
    if not room_session_key:
        return None
    now_mono = time.monotonic()
    stats = _prune_room_speaker_stats(room_session_key, now=now_mono)
    if not stats:
        return None

    owner_user_id = room_owner_user_ids.get(room_session_key)
    owner_until = float(room_owner_until.get(room_session_key, 0.0) or 0.0)
    owner_active = owner_user_id is not None and owner_until > now_mono
    if owner_active:
        owner_stats = stats.get(int(owner_user_id))
        if owner_stats and now_mono - float(owner_stats.get("last_packet_at") or 0.0) <= 0.5:
            return int(owner_user_id)

    scored: list[tuple[tuple[float, float, float, float], int]] = []
    for user_id, data in stats.items():
        scored.append((
            (
                float(data.get("wake_priority") or 0.0),
                float(data.get("recent_voiced_ms") or 0.0),
                float(data.get("body_rms") or 0.0),
                float(data.get("last_packet_at") or 0.0),
            ),
            int(user_id),
        ))
    scored.sort(reverse=True)
    return scored[0][1] if scored else None



def is_room_owner_active(room_session_key: str | None, user_id: int | None) -> bool:
    if not room_session_key or user_id is None:
        return False
    state = room_state_snapshot(room_session_key)
    return state.get("owner_user_id") == user_id and float(state.get("owner_until") or 0.0) > time.monotonic()



def set_room_owner(
    room_session_key: str | None,
    user_id: int | None,
    *,
    ttl_sec: float,
    reason: str,
    session_key: str | None = None,
    turn_id: str | None = None,
    segment_id: int | None = None,
) -> None:
    if not room_session_key or user_id is None:
        return
    previous_owner = room_owner_user_ids.get(room_session_key)
    room_owner_user_ids[room_session_key] = user_id
    room_owner_until[room_session_key] = time.monotonic() + max(0.0, ttl_sec)
    log_turn_event(
        "room_owner_update",
        room_session_key=room_session_key,
        previous_owner_user_id=previous_owner,
        owner_user_id=user_id,
        owner_until=round(room_owner_until[room_session_key], 3),
        reason=reason,
        session_key=session_key,
        turn_id=turn_id,
        segment_id=segment_id,
    )



def set_room_reply_in_progress(room_session_key: str | None, value: bool, *, owner_user_id: int | None = None) -> None:
    if not room_session_key:
        return
    room_reply_in_progress[room_session_key] = value
    log_turn_event(
        "room_reply_state",
        room_session_key=room_session_key,
        reply_in_progress=value,
        owner_user_id=owner_user_id if owner_user_id is not None else room_owner_user_ids.get(room_session_key),
    )



def increment_session_bad_audio(session_key: str | None) -> int:
    if not session_key:
        return 0
    count = session_bad_audio_counts.get(session_key, 0) + 1
    session_bad_audio_counts[session_key] = count
    return count



def reset_session_bad_audio(session_key: str | None) -> None:
    if not session_key:
        return
    session_bad_audio_counts[session_key] = 0


def update_session_state(
    session_key: str | None,
    *,
    user_id: int | None = None,
    speaker: str | None = None,
    ttl_sec: float | None = None,
    awaiting_user_reply: bool | None = None,
    topic_id: str | None = None,
    answer_text: str | None = None,
    user_text: str | None = None,
) -> None:
    if not session_key:
        return
    if ttl_sec is not None:
        active_session_until[session_key] = time.monotonic() + ttl_sec
    session_last_active_at[session_key] = time.monotonic()
    if user_id is not None:
        active_session_user_ids[session_key] = user_id
    if speaker is not None:
        session_last_speaker[session_key] = speaker
    if awaiting_user_reply is not None:
        session_awaiting_user_reply[session_key] = awaiting_user_reply
        if awaiting_user_reply and ttl_sec is None:
            active_session_until[session_key] = time.monotonic() + ACTIVE_CONVERSATION_AWAITING_REPLY_SEC
    if topic_id:
        session_topic_ids[session_key] = topic_id
    elif user_text or answer_text:
        session_topic_ids[session_key] = build_topic_id(user_text or "", answer_text or "")
    if session_key and session_key not in session_turn_ids:
        session_turn_ids[session_key] = new_turn_id()


def mark_session_active(
    session_key: str,
    *,
    user_id: int | None = None,
    ttl_sec: float = 90.0,
    speaker: str = "assistant",
    awaiting_user_reply: bool | None = None,
    topic_id: str | None = None,
    answer_text: str | None = None,
    user_text: str | None = None,
) -> None:
    update_session_state(
        session_key,
        user_id=user_id,
        speaker=speaker,
        ttl_sec=ttl_sec,
        awaiting_user_reply=awaiting_user_reply,
        topic_id=topic_id,
        answer_text=answer_text,
        user_text=user_text,
    )


def is_session_active_for_user(session_key: str, user_id: int | None = None) -> bool:
    expires_at = active_session_until.get(session_key, 0.0)
    awaiting_user_reply = session_awaiting_user_reply.get(session_key, False)
    if expires_at <= time.monotonic() and not awaiting_user_reply:
        return False
    remembered_user = active_session_user_ids.get(session_key)
    if remembered_user is not None and user_id is not None and remembered_user != user_id:
        return False
    return True


def get_conversation_history(*, session_key: str | None = None, guild_id: int | None = None) -> list[dict]:
    resolved = runtime_session_key(session_key=session_key, guild_id=guild_id)
    if resolved is None:
        return new_conversation_history()
    return session_histories.setdefault(resolved, new_conversation_history())


def trim_history(*, session_key: str | None = None, guild_id: int | None = None) -> None:
    history = get_conversation_history(session_key=session_key, guild_id=guild_id)
    if len(history) > 1 + MAX_HISTORY_ITEMS:
        del history[1:-MAX_HISTORY_ITEMS]


def append_history(session_key: str | None, user_text: str, answer: str, *, guild_id: int | None = None) -> None:
    history = get_conversation_history(session_key=session_key, guild_id=guild_id)
    history.append({"role": "user", "content": clean_text(user_text)})
    history.append({"role": "assistant", "content": clean_text(answer)})
    trim_history(session_key=session_key, guild_id=guild_id)


def reset_guild_runtime_state(guild_id: int) -> None:
    prefix = f"guild:{guild_id}:"
    for key in [key for key in session_histories if key.startswith(prefix)]:
        session_histories.pop(key, None)
    for key in [key for key in session_followup_targets if key.startswith(prefix)]:
        session_followup_targets.pop(key, None)
    for key in [key for key in active_session_until if key.startswith(prefix)]:
        active_session_until.pop(key, None)
        active_session_user_ids.pop(key, None)
        session_last_active_at.pop(key, None)
        session_awaiting_user_reply.pop(key, None)
        session_last_speaker.pop(key, None)
        session_topic_ids.pop(key, None)
        session_turn_ids.pop(key, None)
        session_segment_counters.pop(key, None)
        session_last_turn_accepted_at.pop(key, None)
        session_last_stt_text.pop(key, None)
        session_partial_stt_text.pop(key, None)
        session_committed_stt_text.pop(key, None)
        session_bad_audio_counts.pop(key, None)
    for key in [key for key in room_owner_user_ids if key.startswith(prefix)]:
        room_owner_user_ids.pop(key, None)
        room_owner_until.pop(key, None)
        room_reply_in_progress.pop(key, None)
        room_last_voice_reply_at.pop(key, None)
    for key, scope in list(room_turn_scopes.items()):
        if key.startswith(prefix):
            if scope is not None:
                scope.cancel()
            room_turn_scopes.pop(key, None)
    for key in [key for key in session_locks if key.startswith(prefix)]:
        session_locks.pop(key, None)
    for key, task in list(background_search_tasks.items()):
        if key.startswith(prefix):
            if task is not None and not task.done():
                task.cancel()
            background_search_tasks.pop(key, None)
    last_bot_audio_end_at.pop(guild_id, None)
    bot_speaking_guilds.discard(guild_id)
    memory_locks.pop(guild_id, None)
    cognitive_locks.pop(guild_id, None)
    for key, task in list(background_cognitive_tasks.items()):
        if key.startswith(prefix):
            if task is not None and not task.done():
                task.cancel()
            background_cognitive_tasks.pop(key, None)
    autonomy_last_cognitive_refresh_at.pop(guild_id, None)
    refresh_task = autonomy_cognitive_refresh_tasks.pop(guild_id, None)
    if refresh_task is not None and not refresh_task.done():
        refresh_task.cancel()


def _sanitize_debug_label(value: str | None, *, fallback: str = "unknown") -> str:
    text = (value or "").strip()
    text = re.sub(r"[^0-9A-Za-z가-힣._-]+", "_", text)
    text = text.strip("._-")
    return text or fallback


def _trim_voice_debug_dir(guild_dir: Path) -> None:
    if VOICE_DEBUG_MAX_FILES_PER_GUILD <= 0 or not guild_dir.exists():
        return
    wavs = sorted(guild_dir.glob("*.wav"), key=lambda p: p.stat().st_mtime)
    overflow = len(wavs) - VOICE_DEBUG_MAX_FILES_PER_GUILD
    if overflow <= 0:
        return
    for path in wavs[:overflow]:
        try:
            path.unlink()
        except Exception:
            pass


def log_turn_event(event: str, **payload) -> None:
    if not TURN_TRACE_JSON_LOG:
        return
    is_bottleneck_event = event in _BOTTLENECK_TURN_TRACE_EVENTS
    if VOICE_CONSOLE_ONLY_STT_AND_REPLY:
        if not is_bottleneck_event:
            return
    elif (
        VOICE_BOTTLENECK_LOGS
        and not VOICE_TRACE_ALL_EVENTS
        and not is_bottleneck_event
    ):
        return
    record = {"event": event, "ts": round(time.time(), 3)}
    for key, value in dict(payload).items():
        if value is None:
            continue
        record[key] = value
    try:
        print("[TURN TRACE]\n" + json.dumps(record, ensure_ascii=False, sort_keys=True, indent=2))
    except Exception as exc:
        safe_record = {"event": event, "ts": record.get("ts"), "trace_error": repr(exc)}
        for key in ("turn_id", "chunk_index", "session_key", "source_type", "stage", "error"):
            value = record.get(key)
            if value is not None:
                safe_record[key] = value
        print("[TURN TRACE]\n" + json.dumps(safe_record, ensure_ascii=False, sort_keys=True, indent=2))


def merge_log_event_payload(*, explicit: dict[str, Any], extra: dict[str, Any] | None = None) -> dict[str, Any]:
    merged = dict(extra or {})
    for key in explicit.keys():
        merged.pop(key, None)
    merged.update(explicit)
    return merged


@dataclass
class TurnScope:
    turn_id: str
    cancelled: bool = False
    tasks: set[asyncio.Task] = field(default_factory=set)

    def cancel(self):
        self.cancelled = True
        for task in list(self.tasks):
            if task is not None and not task.done():
                task.cancel()

    def raise_if_cancelled(self):
        if self.cancelled:
            raise asyncio.CancelledError()

    def register_task(self, task: asyncio.Task | None = None) -> asyncio.Task | None:
        task = task or asyncio.current_task()
        if task is not None:
            self.tasks.add(task)
        return task

    def unregister_task(self, task: asyncio.Task | None = None) -> None:
        task = task or asyncio.current_task()
        if task is not None:
            self.tasks.discard(task)


def replace_room_turn_scope(room_id: str, new_scope: TurnScope, *, cancel_old: bool = True) -> TurnScope | None:
    global cancelled_stale_turn_count
    old = room_turn_scopes.get(room_id)
    room_turn_scopes[room_id] = new_scope
    if cancel_old and old is not None and old is not new_scope:
        old.cancel()
        cancelled_stale_turn_count += 1
    return old


def get_room_turn_scope(room_id: str | None) -> TurnScope | None:
    if not room_id:
        return None
    return room_turn_scopes.get(room_id)


def _attach_current_task(turn_scope: TurnScope | None) -> asyncio.Task | None:
    if turn_scope is None:
        return None
    return turn_scope.register_task(asyncio.current_task())


def _detach_task(turn_scope: TurnScope | None, task: asyncio.Task | None) -> None:
    if turn_scope is None:
        return
    turn_scope.unregister_task(task)


def create_turn_scoped_task(coro: Awaitable[Any], turn_scope: TurnScope | None = None) -> asyncio.Task:
    task = asyncio.create_task(coro)
    if turn_scope is not None:
        turn_scope.register_task(task)
        task.add_done_callback(lambda done, scope=turn_scope: scope.unregister_task(done))
    return task


def clear_room_turn_scope(room_id: str | None, turn_scope: TurnScope | None = None) -> None:
    if not room_id:
        return
    current = room_turn_scopes.get(room_id)
    if current is None:
        return
    if turn_scope is not None and current is not turn_scope:
        return
    room_turn_scopes.pop(room_id, None)


def record_turn_stage(turn_id: str | None, stage: str, elapsed_ms: float) -> None:
    if not turn_id or not stage:
        return
    stages = turn_stage_metrics.setdefault(turn_id, {})
    stages[stage] = float(elapsed_ms)


def _p95(values: list[float]) -> float:
    if not values:
        return 0.0
    ordered = sorted(float(v) for v in values)
    idx = max(0, min(len(ordered) - 1, math.ceil(len(ordered) * 0.95) - 1))
    return ordered[idx]


def summarize_p95_metrics() -> dict[str, float | int]:
    all_stt_ms = [row.get("t_stt_done") for row in turn_stage_metrics.values() if row.get("t_stt_done") is not None]
    all_router_ms = [row.get("route_ready") for row in turn_stage_metrics.values() if row.get("route_ready") is not None]
    all_main_first_token_ms = [row.get("t_main_first_token") for row in turn_stage_metrics.values() if row.get("t_main_first_token") is not None]
    all_tts_first_audio_ms = [row.get("t_tts_first_audio") for row in turn_stage_metrics.values() if row.get("t_tts_first_audio") is not None]
    return {
        "stt_ms_p95": round(_p95(all_stt_ms), 1),
        "router_ms_p95": round(_p95(all_router_ms), 1),
        "main_first_token_ms_p95": round(_p95(all_main_first_token_ms), 1),
        "tts_first_audio_ms_p95": round(_p95(all_tts_first_audio_ms), 1),
        "search_followup_queued_count": search_followup_queued_count,
        "cancelled_stale_turn_count": cancelled_stale_turn_count,
    }


def compute_runtime_mode(metrics: dict | None) -> str:
    meta = (metrics or {}).get("meta") or {}
    marks = (metrics or {}).get("marks") or {}
    tts_backlog = len(active_tts_playbacks)
    voice_queue_wait_ms = float(meta.get("voice_queue_wait_ms") or marks.get("voice_queue_wait_ms") or 0.0)
    if tts_backlog >= 2:
        return "realtime"
    if voice_queue_wait_ms >= 250.0:
        return "realtime"
    if inflight_llm_requests >= 2:
        return "congested"
    return "normal"


def apply_runtime_mode(mode: str, opts: dict[str, Any] | None = None) -> dict[str, Any]:
    merged = dict(opts or {})
    merged.setdefault("skip_router", False)
    merged.setdefault("skip_search_followup", False)
    merged.setdefault("memory_update_mode", "normal")
    merged.setdefault("tts_chunk_min_chars", 12)
    if mode == "realtime":
        merged["skip_router"] = True
        merged["skip_search_followup"] = True
        merged["memory_update_mode"] = "defer"
        merged["tts_chunk_min_chars"] = 18
    elif mode == "congested":
        merged["skip_router"] = False
        merged["memory_update_mode"] = "batch"
    return merged


def new_turn_metrics(
    *,
    source: str,
    session_key: str | None = None,
    room_session_key: str | None = None,
    guild_id: int | None = None,
    user_id: int | None = None,
    owner_user_id: int | None = None,
    topic_id: str | None = None,
    turn_id: str | None = None,
    segment_id: int | None = None,
    chunk_index: int | None = None,
) -> dict:
    metrics = {
        "started_at": time.monotonic(),
        "marks": {"t_ingress": 0.0},
        "meta": {
            "source": source,
            "session_key": session_key,
            "guild_id": guild_id,
            "user_id": user_id,
            "owner_user_id": owner_user_id,
            "room_session_key": room_session_key,
            "topic_id": topic_id,
            "turn_id": turn_id,
            "segment_id": segment_id,
            "chunk_index": chunk_index,
        },
    }
    log_turn_event(
        "turn_ingress",
        source=source,
        session_key=session_key,
        guild_id=guild_id,
        user_id=user_id,
        owner_user_id=owner_user_id,
        room_session_key=room_session_key,
        topic_id=topic_id,
        turn_id=turn_id,
        segment_id=segment_id,
        chunk_index=chunk_index,
    )
    return metrics


def mark_turn_stage(metrics: dict | None, key: str, *, event_name: str | None = None, **extra) -> None:
    if not metrics:
        return
    started_at = metrics.get("started_at")
    if started_at is None:
        return
    elapsed_ms = (time.monotonic() - float(started_at)) * 1000.0
    marks = metrics.setdefault("marks", {})
    marks[key] = elapsed_ms
    meta = metrics.get("meta") or {}
    turn_id = meta.get("turn_id")
    if turn_id:
        record_turn_stage(turn_id, key, elapsed_ms)
    if event_name:
        explicit = {
            "turn_id": meta.get("turn_id"),
            "segment_id": meta.get("segment_id"),
            "chunk_index": meta.get("chunk_index"),
            "session_key": meta.get("session_key"),
            "room_session_key": meta.get("room_session_key"),
            "guild_id": meta.get("guild_id"),
            "user_id": meta.get("user_id"),
            "owner_user_id": meta.get("owner_user_id"),
            "source": meta.get("source"),
            "elapsed_ms": elapsed_ms,
        }
        log_turn_event(
            event_name,
            **merge_log_event_payload(explicit=explicit, extra=extra),
        )


def register_drop_reason(metrics: dict | None, reason: str, **extra) -> None:
    if not metrics:
        return
    meta = metrics.setdefault("meta", {})
    meta["drop_reason"] = reason
    explicit = {
        "turn_id": meta.get("turn_id"),
        "segment_id": meta.get("segment_id"),
        "chunk_index": meta.get("chunk_index"),
        "session_key": extra.get("session_key") if extra.get("session_key") is not None else meta.get("session_key"),
        "room_session_key": extra.get("room_session_key") if extra.get("room_session_key") is not None else meta.get("room_session_key"),
        "owner_user_id": extra.get("owner_user_id") if extra.get("owner_user_id") is not None else meta.get("owner_user_id"),
        "reason": reason,
    }
    log_turn_event("turn_drop", **merge_log_event_payload(explicit=explicit, extra=extra))


def _save_voice_debug_audio_now(
    guild_id: int,
    speaker: str,
    pcm_bytes: bytes,
    audio16k: np.ndarray,
    *,
    wake_probe: str | None = None,
    final_text: str | None = None,
    debug_meta: dict | None = None,
    save_stt_audio: bool = True,
    stt_meta: dict | None = None,
    session_key: str | None = None,
    stage_label: str | None = None,
) -> None:
    try:
        base_dir = Path(VOICE_DEBUG_AUDIO_DIR)
        if not base_dir.is_absolute():
            base_dir = Path(__file__).resolve().parent / base_dir
        guild_dir = base_dir / str(guild_id)
        guild_dir.mkdir(parents=True, exist_ok=True)

        stem_key = (guild_id, session_key or "")
        stem = voice_debug_stems.get(stem_key)
        if stem is None:
            idx = voice_debug_counts.get(guild_id, 0) + 1
            voice_debug_counts[guild_id] = idx
            stamp = time.strftime("%Y%m%d-%H%M%S")
            speaker_label = _sanitize_debug_label(speaker)
            stem = f"{stamp}_{idx:04d}_{speaker_label}"
            voice_debug_stems[stem_key] = stem

        raw_path = guild_dir / f"{stem}_raw48k.wav"
        stt_path = guild_dir / f"{stem}_stt16k.wav"
        meta_path = guild_dir / f"{stem}.json"

        with wave.open(str(raw_path), "wb") as wf:
            wf.setnchannels(CHANNELS)
            wf.setsampwidth(2)
            wf.setframerate(RATE)
            wf.writeframes(pcm_bytes)

        if save_stt_audio:
            audio16k_int16 = np.clip(audio16k, -1.0, 1.0)
            audio16k_int16 = (audio16k_int16 * 32767.0).astype(np.int16)
            with wave.open(str(stt_path), "wb") as wf:
                wf.setnchannels(1)
                wf.setsampwidth(2)
                wf.setframerate(TARGET_RATE)
                wf.writeframes(audio16k_int16.tobytes())

        stage = clean_text(stage_label or "ingress") or "ingress"
        meta = {
            "saved_at": stamp,
            "guild_id": guild_id,
            "speaker": speaker,
            "raw_path": str(raw_path),
            "stt_path": str(stt_path) if save_stt_audio else None,
            "stt_saved": bool(save_stt_audio),
            "raw_bytes": len(pcm_bytes),
            "raw_seconds": round(len(pcm_bytes) / float(RATE * CHANNELS * 2), 3),
            "stt_samples": int(audio16k.size),
            "stt_seconds": round(audio16k.size / float(TARGET_RATE), 3),
            "wake_probe": wake_probe,
            "final_text": final_text,
            "session_key": session_key,
            "stage_label": stage,
        }
        if debug_meta is not None:
            meta["voice_receive"] = debug_meta
        if stt_meta is not None:
            meta["stt"] = stt_meta
        meta_path.write_text(json.dumps(meta, ensure_ascii=False, indent=2), encoding="utf-8")
        _trim_voice_debug_dir(guild_dir)
        stt_log = str(stt_path) if save_stt_audio else "[SKIPPED]"
        print(f"[VOICE DEBUG SAVE] speaker={speaker} stage={stage} raw={raw_path} stt={stt_log}")
    except Exception as e:
        print(f"[VOICE DEBUG SAVE FAIL] speaker={speaker} err={e!r}")


async def debug_write_worker() -> None:
    while True:
        item = await debug_write_queue.get()
        try:
            await asyncio.to_thread(_save_voice_debug_audio_now, **item)
        except Exception as e:
            print(f"[VOICE DEBUG WORKER FAIL] err={e!r}")
        finally:
            debug_write_queue.task_done()


def ensure_debug_write_worker_started() -> None:
    global debug_write_task
    if debug_write_task is not None and not debug_write_task.done():
        return
    debug_write_task = asyncio.create_task(debug_write_worker())


def save_voice_debug_audio(
    guild_id: int,
    speaker: str,
    pcm_bytes: bytes,
    audio16k: np.ndarray,
    *,
    wake_probe: str | None = None,
    final_text: str | None = None,
    debug_meta: dict | None = None,
    save_stt_audio: bool = True,
    stt_meta: dict | None = None,
    session_key: str | None = None,
    stage_label: str | None = None,
) -> None:
    if not VOICE_DEBUG_SAVE_AUDIO:
        return
    ensure_debug_write_worker_started()
    item = {
        "guild_id": guild_id,
        "speaker": speaker,
        "pcm_bytes": pcm_bytes,
        "audio16k": np.array(audio16k, copy=True),
        "wake_probe": wake_probe,
        "final_text": final_text,
        "debug_meta": dict(debug_meta) if isinstance(debug_meta, dict) else debug_meta,
        "save_stt_audio": save_stt_audio,
        "stt_meta": dict(stt_meta) if isinstance(stt_meta, dict) else stt_meta,
        "session_key": session_key,
        "stage_label": stage_label,
    }
    try:
        debug_write_queue.put_nowait(item)
    except asyncio.QueueFull:
        print(f"[VOICE DEBUG DROP] speaker={speaker} stage={clean_text(stage_label or 'ingress') or 'ingress'} reason=queue_full")


@dataclass(frozen=True)
class TtsInterruptMeta:
    active_speaker_match: bool = False
    wake_detected: bool = False
    vad_prob: float = 0.0
    audio_sec: float = 0.0
    rms_ok: bool = False
    voice_like: bool = False


def estimate_voice_like_probability(*, voiced_ms: float, audio_sec: float, body_rms: float) -> float:
    audio_ms = max(audio_sec * 1000.0, 1.0)
    voiced_ratio = max(0.0, min(1.0, voiced_ms / audio_ms))
    rms_ratio = 0.0
    if VOICE_WAVEFORM_BODY_RMS_MIN > 0:
        rms_ratio = max(0.0, min(1.0, body_rms / VOICE_WAVEFORM_BODY_RMS_MIN))
    return max(voiced_ratio, rms_ratio)


def should_interrupt_tts(meta: TtsInterruptMeta) -> bool:
    if meta.wake_detected and meta.audio_sec >= 0.18:
        return True
    if meta.active_speaker_match and meta.voice_like and meta.audio_sec >= 0.35 and meta.vad_prob >= 0.55:
        return True
    return meta.vad_prob >= 0.6 and meta.audio_sec >= 0.35 and meta.rms_ok


def finalize_voice_reply_side_effects(
    *,
    guild_id: int,
    member: discord.Member,
    session_key: str,
    room_session_key: str,
    room_key: str | None,
    person_key: str | None,
    session_memory_key: str | None,
    voice_reply: VoiceReplyRequest,
    plain_answer: str,
    metrics: dict,
    turn_scope: TurnScope,
    accepted_turn_id: str,
    segment_id: int,
) -> None:
    session_speculative_policies.pop(session_key, None)
    append_history(session_key, voice_reply.history_user_text, plain_answer, guild_id=guild_id)
    runtime_mode = ((metrics.get("meta") or {}).get("runtime_mode")) or compute_runtime_mode(metrics)
    schedule_memory_update(
        guild_id,
        voice_reply.history_user_text,
        plain_answer,
        room_key=room_key,
        person_key=person_key,
        session_memory_key=session_memory_key,
        source="voice",
        user_speaker=member.display_name,
        assistant_speaker="Evelyn",
        session_key=session_key,
        turn_scope=turn_scope,
        runtime_mode=runtime_mode,
    )
    search_requested = bool(
        apply_ask_gating(
            read_cached_cognitive_state(
                guild_id,
                room_key=room_key,
                person_key=person_key,
                session_memory_key=session_memory_key,
            ),
            source="voice",
        ).get("action") == "search_then_answer"
    )
    schedule_search_followup(
        guild_id,
        session_key,
        voice_reply.history_user_text,
        plain_answer,
        room_key=room_key,
        person_key=person_key,
        session_memory_key=session_memory_key,
        channel_id=None,
        source="search-followup-voice",
        force=search_requested,
        turn_scope=None,
        runtime_mode=runtime_mode,
    )
    awaiting_reply = bool("?" in plain_answer or "？" in plain_answer)
    followup_ttl = ACTIVE_CONVERSATION_VOICE_QUESTION_SEC if awaiting_reply else ACTIVE_CONVERSATION_VOICE_SEC
    mark_session_active(
        session_key,
        user_id=member.id,
        ttl_sec=followup_ttl,
        speaker="assistant",
        awaiting_user_reply=awaiting_reply,
        topic_id=voice_reply.topic_id,
        answer_text=plain_answer,
        user_text=voice_reply.history_user_text,
    )
    set_room_owner(
        room_session_key,
        member.id,
        ttl_sec=ACTIVE_CONVERSATION_AWAITING_REPLY_SEC if awaiting_reply else followup_ttl,
        reason="assistant_reply",
        session_key=session_key,
        turn_id=accepted_turn_id,
        segment_id=segment_id,
    )


FAST_PATH_CONTINUE_MARKERS = (
    "그리고",
    "근데",
    "아니",
    "아니야",
    "잠깐",
    "음",
    "어",
    "그거",
    "그건",
    "그 다음",
    "이어서",
    "계속",
)

FAST_PATH_DIRECTIVE_MARKERS = (
    "해줘",
    "해 줘",
    "말해줘",
    "말해 줘",
    "알려줘",
    "알려 줘",
    "정리해줘",
    "정리해 줘",
    "요약해줘",
    "요약해 줘",
    "설명해줘",
    "설명해 줘",
    "번역해줘",
    "번역해 줘",
    "고쳐줘",
    "고쳐 줘",
    "수정해줘",
    "수정해 줘",
)

FAST_PATH_DEEP_ROUTE_MARKERS = (
    "검색",
    "찾아봐",
    "찾아 봐",
    "최신",
    "뉴스",
    "시세",
    "가격",
    "환율",
    "주가",
    "날씨",
    "비교",
    "분석",
    "판단",
    "기억",
    "아까",
    "방금",
    "전에",
    "이전",
    "이어서",
    "계속",
    "요약",
    "정리",
)


def needs_search_or_deep_routing(text: str) -> bool:
    cleaned = clean_text(text)
    if not cleaned:
        return False
    marker_hits = sum(1 for marker in FAST_PATH_DEEP_ROUTE_MARKERS if marker in cleaned)
    if marker_hits >= 2:
        return True
    if len(cleaned) >= 72:
        return True
    search_markers = ("검색", "찾아", "최신", "뉴스", "날씨", "가격", "주가", "환율")
    return any(marker in cleaned for marker in search_markers)


def is_simple_directive(text: str) -> bool:
    cleaned = clean_text(text)
    if not cleaned:
        return False
    if needs_search_or_deep_routing(cleaned):
        return False
    if any(marker in cleaned for marker in FAST_PATH_DIRECTIVE_MARKERS):
        return True
    return len(cleaned) <= 24 and "?" not in cleaned and "？" not in cleaned


def is_obvious_continue(text: str, source: str, room_state: dict | None = None) -> bool:
    cleaned = normalize_voice_text(text) if source == "voice" else clean_text(text)
    if not cleaned:
        return True
    state = room_state or {}
    if not (state.get("reply_in_progress") or state.get("awaiting_user_reply") or state.get("owner_user_id")):
        return False
    if len(cleaned) > 12 and len(cleaned.split()) > 3:
        return False
    return any(cleaned == marker or cleaned.startswith(marker) for marker in FAST_PATH_CONTINUE_MARKERS)


def fast_path_policy(text: str, source: str, room_state: dict | None = None) -> dict | None:
    cleaned = clean_text(text)
    if not cleaned:
        return {"route": "main_direct", "action": "wait", "reason_brief": "empty_input"}
    if is_obvious_continue(cleaned, source, room_state):
        return {"route": "main_direct", "action": "wait", "reason_brief": "obvious_continue"}
    if is_simple_directive(cleaned):
        return {"route": "main_direct", "action": "answer", "reason_brief": "simple_directive"}
    if not needs_search_or_deep_routing(cleaned):
        return {"route": "main_direct", "action": "answer", "reason_brief": "light_request"}
    return None


def build_fast_cognitive_state(
    user_text: str,
    *,
    action: str,
    current_state: dict | None = None,
    reason_brief: str = "fast_path",
) -> dict:
    base = normalize_cognitive_state(current_state or {})
    cleaned = clean_text(user_text)
    hint = "짧고 자연스럽게 답해라."
    if action == "wait":
        hint = "지금은 더 듣는 쪽이 자연스럽다. 아주 짧게 반응해라."
    state = {
        "action": action if action in {"answer", "ask", "wait", "search_then_answer"} else "answer",
        "confidence": 0.92 if action == "answer" else 0.82,
        "user_intent": cleaned,
        "state_summary": base.get("state_summary") or cleaned,
        "question_for_user": "",
        "main_prompt_hint": base.get("main_prompt_hint") or hint,
        "reason_brief": reason_brief,
        "retrieved_context_ids": base.get("retrieved_context_ids") or [],
        "updated_at": int(time.time()),
    }
    return normalize_cognitive_state(state)


def should_ignore_short_transcription(
    text: str,
    pcm_bytes: bytes,
    *,
    wake_detected: bool = False,
) -> bool:
    text_n = normalize_voice_text(text)
    if not text_n:
        return True

    if text_n in normalized_wake_words():
        return False

    if wake_detected and len(text_n) >= WAKE_SHORT_TEXT_KEEP_LEN:
        return False

    audio_sec = len(pcm_bytes) / (RATE * CHANNELS * 2)
    if audio_sec < MIN_AUDIO_SEC and len(text_n) < MIN_TRANSCRIBED_LEN:
        return True

    return False


def is_short_followup_candidate(
    text: str,
    pcm_bytes: bytes,
    *,
    wake_detected: bool = False,
    owner_followup_active: bool = False,
) -> bool:
    text_n = normalize_voice_text(text)
    if not owner_followup_active:
        return False
    if wake_detected:
        return False
    if not text_n:
        return False
    audio_sec = len(pcm_bytes) / (RATE * CHANNELS * 2)
    return audio_sec < max(MIN_AUDIO_SEC * 1.5, 1.2) and len(text_n) < max(MIN_TRANSCRIBED_LEN + 2, 8)


def should_reply_to_voice(
    guild_id: int,
    text: str,
    *,
    wake_detected: bool = False,
    wake_match_mode: str = "",
    session_key: str | None = None,
    room_session_key: str | None = None,
    user_id: int | None = None,
    active_speaker_user_id: int | None = None,
) -> tuple[bool, str, str]:
    now = time.monotonic()
    text_n = normalize_voice_text(text)
    session_state = session_state_snapshot(session_key)
    room_state = room_state_snapshot(room_session_key)
    owner_user_id = room_state.get("owner_user_id")
    owner_active = is_room_owner_active(room_session_key, user_id)
    active_session = session_key is not None and is_session_active_for_user(session_key, user_id)
    if active_speaker_user_id is None:
        active_speaker_user_id = room_state.get("active_speaker_user_id")
    awaiting_followup = bool(session_state.get("awaiting_user_reply")) and owner_active
    followup_allowed = owner_active and (active_session or awaiting_followup)

    if guild_id in bot_speaking_guilds:
        return False, "bot_is_speaking", "bot_is_speaking"

    if now - last_bot_audio_end_at.get(guild_id, 0.0) < POST_TTS_IGNORE_SEC:
        return False, "post_tts_ignore", "post_tts_ignore"

    if not text_n:
        return False, "empty", "empty"

    if looks_like_brief_filler_text(text_n):
        return False, "reply_gate_brief_filler", "reply_gate_brief_filler"

    if looks_like_repetitive_noise_text(text_n):
        return False, "reply_gate_noise_text", "reply_gate_noise_text"

    last_stt_text = normalize_voice_text(session_state.get("last_stt_text", ""))
    if last_stt_text and is_similar(text_n, last_stt_text):
        return False, "duplicate", "duplicate"

    if followup_allowed:
        return True, "ok", "owner_followup"

    if active_speaker_user_id is not None and user_id is not None and active_speaker_user_id != user_id and not wake_detected:
        return False, "not_active_speaker", "not_active_speaker"

    if owner_user_id is not None and user_id is not None and owner_user_id != user_id:
        if not wake_detected:
            return False, "owner_mismatch_needs_wake", "owner_mismatch_needs_wake"
        if wake_match_mode != "exact":
            return False, "owner_takeover_requires_exact_wake", "owner_takeover_requires_exact_wake"

    if not wake_detected and not contains_wake_word(text_n):
        return False, "no_wake_word", "no_wake_word"

    if len(text_n) < MIN_TEXT_LEN and not wake_detected:
        return False, "too_short", "too_short"

    if room_session_key and (now - room_last_voice_reply_at.get(room_session_key, 0.0) < REPLY_COOLDOWN_SEC) and not wake_detected:
        return False, "cooldown", "cooldown"

    if wake_detected and owner_user_id is not None and owner_user_id != user_id:
        return True, "ok", "owner_takeover"

    return True, "ok", "wake_entry"


def ask_confidence_threshold_for_source(source: str) -> float:
    return ASK_CONFIDENCE_THRESHOLD_VOICE if source == "voice" else ASK_CONFIDENCE_THRESHOLD_TEXT


def should_skip_full_stt_after_wake_probe(*, wake_detected: bool, wake_probe: str, duration_sec: float) -> bool:
    if wake_detected:
        return False

    probe = clean_text(wake_probe)
    if not probe and duration_sec <= VOICE_NO_WAKE_MAX_CONTINUE_SEC:
        return True
    if looks_like_brief_filler_text(probe) and duration_sec <= VOICE_NO_WAKE_MAX_CONTINUE_SEC:
        return True
    if looks_like_repetitive_noise_text(probe):
        return True
    return False


def should_require_confirm_exact_for_wake(debug_meta: dict | None) -> bool:
    if not debug_meta:
        return False
    reasons = [str(reason) for reason in (debug_meta.get("reasons") or [])]
    if any(
        marker in reason
        for reason in reasons
        for marker in ("opus_fail", "plc", "fec", "front_burst_detected", "heavy_trim_ms", "burst_trim_ms")
    ):
        return True
    if debug_meta.get("front_burst_detected"):
        return True
    if int(debug_meta.get("opus_fail") or 0) > 0:
        return True
    if int(debug_meta.get("plc_packets") or 0) > 0:
        return True
    if int(debug_meta.get("fec_packets") or 0) > 0:
        return True
    if float(debug_meta.get("trim_ms") or 0.0) >= 220.0:
        return True
    if float(debug_meta.get("burst_trim_ms") or 0.0) >= 140.0:
        return True
    return False


def is_transport_corrupted_audio(debug_meta: dict | None) -> bool:
    if not debug_meta:
        return False
    reasons = [str(reason) for reason in (debug_meta.get("reasons") or [])]
    required_markers = ("opus_fail", "plc", "fec", "front_burst_detected", "heavy_trim_ms", "burst_trim_ms")
    reason_hits = {marker: any(marker in reason for reason in reasons) for marker in required_markers}
    return (
        (reason_hits["opus_fail"] or int(debug_meta.get("opus_fail") or 0) >= 4)
        and (reason_hits["plc"] or int(debug_meta.get("plc_packets") or 0) >= 2)
        and (reason_hits["fec"] or int(debug_meta.get("fec_packets") or 0) >= 2)
        and (reason_hits["front_burst_detected"] or bool(debug_meta.get("front_burst_detected")))
        and (reason_hits["heavy_trim_ms"] or float(debug_meta.get("trim_ms") or 0.0) >= 220.0)
        and (reason_hits["burst_trim_ms"] or float(debug_meta.get("burst_trim_ms") or 0.0) >= 140.0)
    )


def is_tail_fragment_candidate(
    *,
    session_key: str | None,
    raw_seconds: float,
    voiced_ms: float,
    longest_voiced_ms: float,
    unstable: bool,
) -> bool:
    if not session_key:
        return False
    accepted_at = session_last_turn_accepted_at.get(session_key, 0.0)
    if accepted_at <= 0.0:
        return False
    if (time.monotonic() - accepted_at) > TAIL_FRAGMENT_WINDOW_SEC:
        return False
    if raw_seconds > TAIL_FRAGMENT_MAX_RAW_SEC:
        return False
    if voiced_ms > TAIL_FRAGMENT_MAX_VOICED_MS:
        return False
    if longest_voiced_ms > TAIL_FRAGMENT_MAX_LONGEST_MS:
        return False
    return unstable or raw_seconds <= (TAIL_FRAGMENT_MAX_RAW_SEC * 0.6)


def apply_ask_gating(cognitive_state: dict | None = None, *, source: str = "text") -> dict:
    state = normalize_cognitive_state(cognitive_state or {})
    threshold = ask_confidence_threshold_for_source(source)

    if state.get("action") == "ask":
        question_for_user = clean_text(str(state.get("question_for_user", "")))
        confidence = float(state.get("confidence", 0.0) or 0.0)
        if not question_for_user or confidence < threshold:
            gated = dict(state)
            gated["action"] = "wait" if source == "voice" else "answer"
            reason = clean_text(str(gated.get("reason_brief", "")))
            gate_note = f"ask_gated_{source}_{confidence:.2f}_lt_{threshold:.2f}"
            gated["reason_brief"] = clean_text(f"{reason} {gate_note}") if reason else gate_note
            return gated

    return state


def policy_response_for_state(cognitive_state: dict | None = None, *, source: str = "text") -> str | None:
    state = apply_ask_gating(cognitive_state, source=source)
    action = state.get("action", "answer")

    if action == "ask":
        question = clean_text(str(state.get("question_for_user", "")))
        if question:
            return question
        return None

    if action == "wait":
        return "응, 계속 말해줘." if source == "voice" else "잠깐, 이어서 말해줘."

    if action == "search_then_answer":
        return "금방 찾아보고 바로 알려줄게."

    return None


async def voice_ingress_worker() -> None:
    while True:
        item = await voice_ingress_queue.get()
        try:
            await _process_member_audio_impl(**item)
        except Exception as e:
            print(f"[VOICE WORKER] 실패: {e!r}")
        finally:
            voice_ingress_queue.task_done()


def ensure_voice_worker_started() -> None:
    global voice_worker_task
    ensure_debug_write_worker_started()
    if voice_worker_task is not None and not voice_worker_task.done():
        return
    voice_worker_task = asyncio.create_task(voice_ingress_worker())


def should_label_question_response(text: str, *, session_key: str | None = None) -> bool:
    visible = visible_text(text).strip()
    if not visible:
        return False
    if visible.startswith("[질문]"):
        return False
    if session_key is not None and session_state_snapshot(session_key).get("awaiting_user_reply"):
        return True
    return "?" in visible or "？" in visible


def format_display_text(text: str, *, session_key: str | None = None) -> str:
    visible = visible_text(text).strip()
    if not visible:
        return visible
    if should_label_question_response(visible, session_key=session_key):
        return f"[질문] {visible}"
    return visible


def speculate_from_committed_stt(committed_text: str, room_state: dict | None) -> dict | None:
    cleaned = clean_text(committed_text)
    state = room_state or {}
    if len(cleaned) < 8:
        return None
    if not (state.get("active_speaker_user_id") or state.get("owner_user_id")):
        return None
    policy = fast_path_policy(cleaned, "voice", state)
    if policy is None:
        return None
    return {
        "text": cleaned,
        "policy": policy,
        "prepared_at": time.monotonic(),
    }


def remember_speculative_policy(session_key: str | None, speculative: dict | None) -> None:
    if not session_key or not speculative:
        return
    session_speculative_policies[session_key] = speculative


def get_matching_speculative_policy(session_key: str | None, user_text: str) -> dict | None:
    if not session_key:
        return None
    speculative = session_speculative_policies.get(session_key)
    if not speculative:
        return None
    if (time.monotonic() - float(speculative.get("prepared_at") or 0.0)) > 20.0:
        session_speculative_policies.pop(session_key, None)
        return None
    speculative_text = clean_text(str(speculative.get("text") or ""))
    current_text = clean_text(user_text)
    if not speculative_text or not current_text:
        return None
    if current_text.startswith(speculative_text) or speculative_text.startswith(current_text) or is_similar(current_text, speculative_text):
        return speculative
    return None


class BufferedEditStreamer:
    def __init__(self, message: discord.Message, *, session_key: str | None = None):
        self.message = message
        self.session_key = session_key
        self.rendered_text = clean_text(message.content or "")
        self.pending_text = self.rendered_text
        self.last_flush_at = 0.0
        self.first_pending_at = 0.0

    async def push(self, full_text: str, *, force: bool = False) -> None:
        candidate = format_display_text(full_text, session_key=self.session_key).strip()
        if not candidate or candidate == self.rendered_text:
            return
        now = time.monotonic()
        if self.pending_text != candidate:
            self.pending_text = candidate
            if self.first_pending_at <= 0.0:
                self.first_pending_at = now
        delta_chars = max(0, len(candidate) - len(self.rendered_text))
        elapsed_ms = (now - self.last_flush_at) * 1000.0 if self.last_flush_at > 0 else 10000.0
        held_ms = (now - self.first_pending_at) * 1000.0 if self.first_pending_at > 0 else elapsed_ms
        hard_break = candidate.endswith((".", "!", "?", "\n", "。", "！", "？"))
        should_flush = force or hard_break or held_ms >= MAX_HOLD_MS or (delta_chars >= MIN_DELTA_CHARS and elapsed_ms >= MIN_EDIT_INTERVAL_MS)
        if not should_flush:
            return
        await self.message.edit(content=candidate)
        self.rendered_text = candidate
        self.pending_text = candidate
        self.last_flush_at = now
        self.first_pending_at = 0.0

    async def close(self, final_text: str) -> None:
        await self.push(final_text, force=True)


class DiscordEditSink:
    def __init__(self, streamer: BufferedEditStreamer):
        self.streamer = streamer
        self.parts: list[str] = []

    async def on_chunk(self, text: str) -> None:
        if not text:
            return
        self.parts.append(text)
        await self.streamer.push("".join(self.parts))

    async def close(self, final_text: str) -> None:
        await self.streamer.close(final_text)


class TTSQueueSink:
    def __init__(self, sentence_queue: "asyncio.Queue[str | None]"):
        self.sentence_queue = sentence_queue
        self.queued_sentence_count = 0

    async def on_chunk(self, text: str) -> None:
        cleaned = clean_tts_text(text)
        print(f"[TTS QUEUE] on_chunk raw={text!r} cleaned={cleaned!r}")
        if not cleaned:
            print("[TTS QUEUE] drop_empty_chunk")
            return
        self.queued_sentence_count += 1
        await self.sentence_queue.put(cleaned)
        print(f"[TTS QUEUE] queued count={self.queued_sentence_count} qsize={self.sentence_queue.qsize()}")

    async def close(self, _final_text: str) -> None:
        print(f"[TTS QUEUE] close qsize_before={self.sentence_queue.qsize()}")
        await self.sentence_queue.put(None)


class ReplyStreamFanout:
    def __init__(self, sinks: list[Any]):
        self.sinks = [sink for sink in sinks if sink is not None]

    async def on_chunk(self, text: str) -> None:
        for sink in self.sinks:
            await sink.on_chunk(text)

    async def close(self, final_text: str) -> None:
        for sink in self.sinks:
            close = getattr(sink, "close", None)
            if close is not None:
                await close(final_text)


class StreamingVoiceDelivery:
    def __init__(
        self,
        sentence_queue: "asyncio.Queue[str | None]",
        tts_sink: TTSQueueSink,
        playback_task: asyncio.Task,
        *,
        metrics: dict,
    ):
        self.sentence_queue = sentence_queue
        self.tts_sink = tts_sink
        self.playback_task = playback_task
        self.metrics = metrics

    async def on_chunk(self, text: str) -> None:
        await self.tts_sink.on_chunk(text)

    async def close(self, final_text: str) -> None:
        await self.tts_sink.close(final_text)

    async def finalize(self) -> int:
        log_voice_stage(self.metrics, "문장별 TTS 예약 완료", extra=f"sentence_count={self.tts_sink.queued_sentence_count} prefetch={TTS_PREFETCH_CHUNKS}")
        await self.playback_task
        return self.tts_sink.queued_sentence_count

    async def abort(self) -> None:
        if not self.playback_task.done():
            await self.sentence_queue.put(None)
            self.playback_task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await self.playback_task


def read_cached_cognitive_state(
    guild_id: int | None,
    *,
    room_key: str | None = None,
    person_key: str | None = None,
    session_memory_key: str | None = None,
) -> dict | None:
    if guild_id is None:
        return None
    return read_layered_cognitive_state(
        guild_id,
        room_key=room_key,
        person_key=person_key,
        session_memory_key=session_memory_key,
    )


def should_force_search_followup(
    guild_id: int | None,
    *,
    room_key: str | None = None,
    person_key: str | None = None,
    session_memory_key: str | None = None,
    source: str,
) -> bool:
    state = read_cached_cognitive_state(
        guild_id,
        room_key=room_key,
        person_key=person_key,
        session_memory_key=session_memory_key,
    )
    if not state:
        return False
    gated = apply_ask_gating(state, source=source)
    return clean_text(str(gated.get("action") or "")) == "search_then_answer"


async def refresh_cognitive_state_in_background(
    guild_id: int,
    user_text: str,
    *,
    reason: str,
    session_key: str | None = None,
    room_key: str | None = None,
    person_key: str | None = None,
    session_memory_key: str | None = None,
    source: str = "text",
    turn_scope: TurnScope | None = None,
) -> None:
    task_key = session_memory_key or runtime_session_key(guild_id=guild_id)
    started_at = time.monotonic()
    try:
        await update_cognitive_state(
            guild_id,
            user_text,
            session_key=session_key,
            room_key=room_key,
            person_key=person_key,
            session_memory_key=session_memory_key,
            source=source,
            turn_scope=turn_scope,
        )
        log_turn_event(
            "cognitive_background_done",
            session_key=session_key,
            turn_id=current_turn_id(session_key),
            cognitive_background_ms=round((time.monotonic() - started_at) * 1000.0, 1),
            reason=reason,
        )
    except asyncio.CancelledError:
        raise
    except Exception as e:
        print(f"[COGNITIVE] background refresh 실패 guild={guild_id} session={task_key!r} reason={reason} err={e!r}")
    finally:
        task = background_cognitive_tasks.get(task_key)
        if task is asyncio.current_task():
            background_cognitive_tasks.pop(task_key, None)


def schedule_cognitive_refresh(
    guild_id: int | None,
    user_text: str,
    *,
    reason: str,
    session_key: str | None = None,
    room_key: str | None = None,
    person_key: str | None = None,
    session_memory_key: str | None = None,
    source: str = "text",
    turn_scope: TurnScope | None = None,
) -> None:
    if guild_id is None:
        return
    task_key = session_memory_key or runtime_session_key(guild_id=guild_id)
    if task_key is None:
        return
    existing = background_cognitive_tasks.get(task_key)
    if existing is not None and not existing.done():
        existing.cancel()
    background_cognitive_tasks[task_key] = create_turn_scoped_task(
        refresh_cognitive_state_in_background(
            guild_id,
            user_text,
            reason=reason,
            session_key=session_key,
            room_key=room_key,
            person_key=person_key,
            session_memory_key=session_memory_key,
            source=source,
            turn_scope=turn_scope,
        ),
        turn_scope=turn_scope,
    )


def format_minecraft_state_summary(state: dict[str, Any] | None) -> str:
    if not isinstance(state, dict) or not state:
        return ""
    parts: list[str] = []
    voyager_running = state.get("minecraft_autonomy")
    voyager_connected = state.get("voyager_connected")
    if voyager_running is not None:
        parts.append(f"Voyager={'on' if bool(voyager_running) else 'off'}")
    if voyager_connected is not None:
        parts.append(f"연결={'on' if bool(voyager_connected) else 'off'}")
    objective_goal = clean_text(str(state.get("objective_goal") or state.get("goal") or ""))
    objective_stage = clean_text(str(state.get("objective_stage") or state.get("stage") or ""))
    objective_task = clean_text(str(state.get("objective_task") or state.get("current_task") or ""))
    if objective_goal:
        parts.append(f"Voyager목표={objective_goal}")
    if objective_stage:
        parts.append(f"Voyager단계={objective_stage}")
    if objective_task:
        parts.append(f"Voyager작업={objective_task}")
    voyager_evaluation = state.get("voyager_evaluation") if isinstance(state.get("voyager_evaluation"), dict) else {}
    unique_item_count = voyager_evaluation.get("unique_item_count") if voyager_evaluation else state.get("voyager_unique_item_count")
    tech_tree_highest = clean_text(str((voyager_evaluation.get("tech_tree") or {}).get("highest_unlocked") if voyager_evaluation else state.get("voyager_tech_tree_highest") or ""))
    travel_distance_blocks = voyager_evaluation.get("travel_distance_blocks") if voyager_evaluation else state.get("voyager_travel_distance_blocks")
    skill_library_size = ((voyager_evaluation.get("skill_library") or {}).get("size") if voyager_evaluation else state.get("voyager_skill_library_size"))
    if unique_item_count is not None:
        parts.append(f"유니크아이템={unique_item_count}")
    if tech_tree_highest:
        parts.append(f"테크={tech_tree_highest}")
    if travel_distance_blocks is not None:
        parts.append(f"이동거리={travel_distance_blocks}b")
    if skill_library_size is not None:
        parts.append(f"스킬라이브러리={skill_library_size}")
    position = state.get("position") or state.get("position_block")
    if isinstance(position, dict):
        x = position.get("x")
        y = position.get("y")
        z = position.get("z")
        if x is not None and y is not None and z is not None:
            parts.append(f"위치=({x},{y},{z})")
    health = state.get("health")
    hunger = state.get("hunger")
    if health is not None:
        parts.append(f"체력={health}")
    if hunger is not None:
        parts.append(f"허기={hunger}")
    hostiles = state.get("hostiles_nearby")
    if hostiles is not None:
        parts.append(f"근처 적대몹={hostiles}")
    inventory = state.get("inventory") or {}
    if isinstance(inventory, dict) and inventory:
        top_items = []
        for name, count in sorted(inventory.items(), key=lambda item: (-int(item[1] or 0), str(item[0])))[:6]:
            top_items.append(f"{name}x{count}")
        if top_items:
            parts.append("인벤토리=" + ", ".join(top_items))
    nearby_blocks = state.get("nearby_blocks") or []
    if nearby_blocks:
        parts.append("주변블록=" + ", ".join(str(value) for value in list(nearby_blocks)[:6]))
    nearby_entities = state.get("nearby_entities") or []
    if nearby_entities:
        parts.append("주변엔티티=" + ", ".join(str(value) for value in list(nearby_entities)[:6]))
    active_environment = clean_text(str(state.get("active_environment") or ""))
    if active_environment:
        parts.append(f"활성환경={active_environment}")
    return clean_text(" / ".join(parts))


def build_main_response_guidance(cognitive_state: dict | None = None, *, source: str = "text", minecraft_state: dict[str, Any] | None = None) -> str:
    state = apply_ask_gating(cognitive_state, source=source)
    threshold = ask_confidence_threshold_for_source(source)
    parts = [
        "주의: 생각 과정 말하지 말고, 최종 답변만 한국어로 한두 문장으로 짧게 말해.",
        OMNIVOICE_TAG_GUIDANCE,
        "텍스트만 봤을 때도 자연스럽게 읽혀야 하고, 태그를 빼도 문장이 성립해야 한다.",
        "sub handoff의 question_for_user는 사용자가 한 말이 아니다. 내부 메모이므로, 그 문장을 사용자의 질문으로 오해해서 답하지 마라.",
        f"ask 행동은 {source} 입력에서 confidence {threshold:.2f} 이상일 때만 허용한다.",
    ]

    action = state.get("action", "answer")
    if state.get("user_intent"):
        parts.append(f"사용자 의도 추정: {state['user_intent']}")

    if action == "ask":
        parts.append("지금은 바로 단정하기보다 사용자의 원래 발화에 이어서 짧은 확인 질문을 먼저 하는 편이 자연스럽다.")
        parts.append("질문형 태그가 필요하면 [question-en], [question-ah], [question-oh], [question-ei], [question-yi] 중 하나만 골라라.")
        parts.append("ask 모드에서는 question_for_user의 의미를 바꾸지 말고 거의 그대로 사용해라. 새 정보 추가, 해석 확장, 답변형 전환을 하지 마라.")
        if state.get("question_for_user"):
            parts.append(f"사용자에게 그대로 되물을 문장: {state['question_for_user']}")
    elif action == "wait":
        parts.append("지금은 길게 답하지 말고, 더 들을 여지를 두는 짧은 반응이 자연스럽다.")
        parts.append("wait 상황에서는 감정 태그를 거의 쓰지 말고, 정말 필요할 때만 [sigh] 같은 약한 태그 하나만 써라.")
    else:
        parts.append("지금은 답변을 주는 편이 자연스럽다.")
        parts.append("확인이나 수긍에는 [confirmation-en], 가벼운 웃음에는 [laughter], 놀람에는 [surprise-oh]나 [surprise-wa]를 필요할 때만 써라.")

    if state.get("main_prompt_hint"):
        parts.append(f"응답 추가 힌트: {state['main_prompt_hint']}")
    if state.get("confidence", 0.0) > 0:
        parts.append(f"내부 판단 신뢰도: {state['confidence']:.2f}")

    minecraft_summary = format_minecraft_state_summary(minecraft_state)
    if minecraft_summary:
        parts.append(f"현재 마인크래프트 실시간 상태: {minecraft_summary}")
        parts.append("마인크래프트 관련 질문이나 계획을 답할 때는 이 실시간 상태를 기준으로 말해라. 모르면 추측하지 말고 현재 상태 기준으로 짧게 설명해라.")

    return " ".join(clean_text(part) for part in parts if clean_text(part))


def normalize_route_name(value: str) -> str:
    route = clean_text(value).lower()
    if route in {"subwait", "sub_wait", "wait", "fresh_sub", "fresh-sub"}:
        return "sub_wait"
    if route in {"subhint", "sub_hint", "hint", "cached_sub", "cached-sub"}:
        return "sub_hint"
    return "main_direct"


def should_force_voice_context_route(user_text: str) -> bool:
    text = clean_text(user_text)
    if not text:
        return False
    voice_context_markers = [
        "기억", "방금", "아까", "전에", "이전", "대화", "말했던", "했었던", "했던", "했었어",
        "무슨 얘기", "뭐였", "기억나", "기억해", "이어", "계속", "정리", "요약",
        "우리", "우리가", "하기로 했", "먹기로 했", "가기로 했", "약속", "정했",
    ]
    marker_hits = sum(1 for marker in voice_context_markers if marker in text)
    if marker_hits >= 1:
        return True
    return bool(re.search(r"(우리|우리가).*(했었|하기로 했|먹기로 했|가기로 했)", text))


def classify_llm_route_fallback(user_text: str, *, source: str = "text") -> str:
    text = clean_text(user_text)
    if source == "voice" and not should_force_voice_context_route(text):
        return "main_direct"

    short_text = len(text) <= 18 or len(text.split()) <= 4
    if short_text and source != "voice":
        return "main_direct"

    context_markers = [
        "아까", "방금", "전에", "이전", "기억", "문맥", "계속", "이어서",
        "요약", "정리", "판단", "비교", "설명", "의견", "생각", "왜", "어떻게",
    ]
    marker_hits = sum(1 for marker in context_markers if marker in text)

    if len(text) >= 60 or marker_hits >= 2:
        return "sub_wait"
    if len(text) >= 24 or marker_hits >= 1:
        return "sub_hint"
    return "main_direct"


async def prepare_llm_messages(
    user_text: str,
    *,
    guild_id: int | None = None,
    session_key: str | None = None,
    room_key: str | None = None,
    person_key: str | None = None,
    session_memory_key: str | None = None,
    source: str = "text",
    debug_text: str | None = None,
    metrics: dict | None = None,
    turn_scope: TurnScope | None = None,
) -> tuple[list[dict], dict | None, str]:
    if turn_scope is not None:
        turn_scope.raise_if_cancelled()
    runtime_mode = compute_runtime_mode(metrics)
    runtime_opts = apply_runtime_mode(runtime_mode)
    route_started_at = time.monotonic()
    if runtime_opts.get("skip_router"):
        route = classify_llm_route_fallback(user_text, source=source)
        route_meta = {"selected": route, "source": "runtime_mode", "mode": runtime_mode}
    else:
        route, route_meta = await classify_llm_route_async(user_text, guild_id=guild_id, source=source, session_key=session_key)
    if metrics is not None:
        metrics.setdefault("marks", {})["route_ready"] = (time.monotonic() - route_started_at) * 1000.0
        metrics.setdefault("meta", {}).update(
            {
                "source": source,
                "session_key": session_key,
                "guild_id": guild_id,
                "topic_id": session_topic_ids.get(session_key or "", "") if session_key else None,
                "runtime_mode": runtime_mode,
                "runtime_opts": dict(runtime_opts),
            }
        )
    messages = list(get_conversation_history(session_key=session_key, guild_id=guild_id))
    cognitive_state: dict | None = None

    if turn_scope is not None:
        turn_scope.raise_if_cancelled()
    cognitive_started_at = time.monotonic()
    cached_cognitive_state = read_cached_cognitive_state(
        guild_id,
        room_key=room_key,
        person_key=person_key,
        session_memory_key=session_memory_key,
    )
    speculative = get_matching_speculative_policy(session_key, user_text) if source == "voice" else None
    local_fast_policy = (speculative or {}).get("policy") or fast_path_policy(user_text, source, session_state_snapshot(session_key))
    should_block_on_cognitive = guild_id is not None and (cached_cognitive_state is None or route == "sub_wait")
    if local_fast_policy is not None:
        cognitive_state = build_fast_cognitive_state(
            user_text,
            action=str(local_fast_policy.get("action", "answer")),
            current_state=cached_cognitive_state,
            reason_brief=str(local_fast_policy.get("reason_brief", "fast_path")),
        )
        if metrics is not None:
            metrics.setdefault("meta", {})["cognitive_mode"] = "fast_path"
    elif should_block_on_cognitive and guild_id is not None:
        cognitive_state = await update_cognitive_state(
            guild_id,
            user_text,
            session_key=session_key,
            room_key=room_key,
            person_key=person_key,
            session_memory_key=session_memory_key,
            source=source,
            turn_scope=turn_scope,
        )
        if metrics is not None:
            metrics.setdefault("meta", {})["cognitive_mode"] = "blocking"
    else:
        cognitive_state = cached_cognitive_state
        if guild_id is not None and runtime_opts.get("memory_update_mode") != "defer":
            schedule_cognitive_refresh(
                guild_id,
                user_text,
                reason=f"{source}:{route}",
                session_key=session_key,
                room_key=room_key,
                person_key=person_key,
                session_memory_key=session_memory_key,
                source=source,
                turn_scope=turn_scope,
            )
        if metrics is not None:
            metrics.setdefault("meta", {})["cognitive_mode"] = "background"
    if metrics is not None and should_block_on_cognitive:
        metrics.setdefault("marks", {})["cognitive_hotpath_ms"] = (time.monotonic() - cognitive_started_at) * 1000.0

    if turn_scope is not None:
        turn_scope.raise_if_cancelled()

    if guild_id is not None:
        memory_started_at = time.monotonic()
        memory_context = build_memory_context(
            guild_id,
            user_text,
            cognitive_state=cognitive_state,
            session_key=session_key,
            room_key=room_key,
            person_key=person_key,
            session_memory_key=session_memory_key,
        )
        if metrics is not None:
            memory_elapsed = (time.monotonic() - memory_started_at) * 1000.0
            metrics.setdefault("marks", {})["memory_ready"] = memory_elapsed
            metrics.setdefault("marks", {})["t_context_build"] = (time.monotonic() - float(metrics.get("started_at", time.monotonic()))) * 1000.0
        if memory_context:
            base_system = messages[0]["content"] if messages and messages[0].get("role") == "system" else ""
            merged_system = clean_text(base_system + "\n\n" + memory_context)

            if messages and messages[0].get("role") == "system":
                messages[0] = {"role": "system", "content": merged_system}
            else:
                messages.insert(0, {"role": "system", "content": merged_system})

    if cognitive_state is not None:
        gated_state = apply_ask_gating(cognitive_state, source=source)
        if gated_state.get("action") != cognitive_state.get("action"):
            print(
                f"[ASK GATE] source={source} action={cognitive_state.get('action')} -> {gated_state.get('action')} confidence={float(cognitive_state.get('confidence', 0.0) or 0.0):.2f} threshold={ask_confidence_threshold_for_source(source):.2f}"
            )
            cognitive_state = gated_state

    if metrics is not None:
        metrics.setdefault("marks", {})["t_policy"] = (time.monotonic() - float(metrics.get("started_at", time.monotonic()))) * 1000.0
    route_text = debug_text if debug_text is not None else user_text
    meta = metrics.get("meta") if metrics is not None else {}
    log_turn_event(
        "policy_ready",
        turn_id=(meta or {}).get("turn_id"),
        segment_id=(meta or {}).get("segment_id"),
        chunk_index=(meta or {}).get("chunk_index"),
        session_key=session_key,
        source=source,
        route=route,
        cognitive_action=(cognitive_state or {}).get("action") if cognitive_state else None,
        topic_id=session_topic_ids.get(session_key or "", "") if session_key else None,
    )
    if route_meta and route_meta.get("source") == "router":
        print(
            f"[LLM ROUTE] source={source} route={route} via=router confidence={float(route_meta.get('confidence', 0.0) or 0.0):.2f} reason={route_meta.get('reason_brief', '')!r} text={visible_text(route_text)!r}"
        )
    else:
        print(f"[LLM ROUTE] source={source} route={route} via=fallback text={visible_text(route_text)!r}")
    return messages, cognitive_state, route


def collect_memory_layers(
    guild_id: int,
    *,
    room_key: str | None = None,
    person_key: str | None = None,
    session_memory_key: str | None = None,
) -> dict[str, dict]:
    layers: dict[str, dict] = {
        "guild": {
            "label": "공용 방 기억",
            "scope_type": "guild",
            "scope_key": None,
            "summary": compact_working_summary(read_text_file(memory_summary_path(guild_id))),
            "raw": read_jsonl(memory_raw_path(guild_id)),
            "vault_raw": read_vault_raw_rows(guild_id),
            "facts": read_fact_rows(guild_id),
            "questions": read_question_rows(guild_id),
        }
    }

    if room_key:
        layers["room"] = {
            "label": "방 기억",
            "scope_type": "room",
            "scope_key": room_key,
            "summary": compact_working_summary(read_text_file(memory_summary_path(guild_id, scope_type="room", scope_key=room_key))),
            "raw": read_jsonl(memory_raw_path(guild_id, scope_type="room", scope_key=room_key)),
            "vault_raw": read_vault_raw_rows(guild_id, scope_type="room", scope_key=room_key),
            "facts": read_fact_rows(guild_id, scope_type="room", scope_key=room_key),
            "questions": read_question_rows(guild_id, scope_type="room", scope_key=room_key),
        }

    if person_key:
        layers["person"] = {
            "label": "이 사람 기억",
            "scope_type": "person",
            "scope_key": person_key,
            "summary": compact_working_summary(read_text_file(memory_summary_path(guild_id, scope_type="person", scope_key=person_key))),
            "raw": read_jsonl(memory_raw_path(guild_id, scope_type="person", scope_key=person_key)),
            "vault_raw": read_vault_raw_rows(guild_id, scope_type="person", scope_key=person_key),
            "facts": read_fact_rows(guild_id, scope_type="person", scope_key=person_key),
            "questions": read_question_rows(guild_id, scope_type="person", scope_key=person_key),
        }

    if session_memory_key:
        layers["session"] = {
            "label": "현재 세션 기억",
            "scope_type": "session",
            "scope_key": session_memory_key,
            "summary": compact_working_summary(read_text_file(memory_summary_path(guild_id, scope_type="session", scope_key=session_memory_key))),
            "raw": read_jsonl(memory_raw_path(guild_id, scope_type="session", scope_key=session_memory_key)),
            "vault_raw": read_vault_raw_rows(guild_id, scope_type="session", scope_key=session_memory_key),
            "facts": read_fact_rows(guild_id, scope_type="session", scope_key=session_memory_key),
            "questions": read_question_rows(guild_id, scope_type="session", scope_key=session_memory_key),
        }

    return layers


def merge_recent_memory_rows(*row_groups: list[dict], limit: int) -> list[dict]:
    merged = merge_memory_rows(*row_groups)
    merged.sort(key=lambda row: int(row.get("saved_at", 0) or 0))
    return merged[-limit:]


def read_layered_cognitive_state(
    guild_id: int,
    *,
    room_key: str | None = None,
    person_key: str | None = None,
    session_memory_key: str | None = None,
) -> dict | None:
    if session_memory_key:
        session_state = read_json_file(cognitive_state_path(guild_id, scope_type="session", scope_key=session_memory_key))
        if session_state:
            return normalize_cognitive_state(session_state)
    if person_key:
        person_state = read_json_file(cognitive_state_path(guild_id, scope_type="person", scope_key=person_key))
        if person_state:
            return normalize_cognitive_state(person_state)
    if room_key:
        room_state = read_json_file(cognitive_state_path(guild_id, scope_type="room", scope_key=room_key))
        if room_state:
            return normalize_cognitive_state(room_state)
    guild_state = read_json_file(cognitive_state_path(guild_id))
    return normalize_cognitive_state(guild_state) if guild_state else None


def format_memory_row_lines(rows: list[dict]) -> str:
    return "\n".join(
        f"- {clean_text(str(row.get('speaker', row.get('role', 'unknown')))) or 'unknown'}"
        f" ({clean_text(str(row.get('source', 'unknown'))) or 'unknown'}): {clean_text(str(row.get('text', '')))}"
        for row in rows
        if clean_text(str(row.get('text', '')))
    )


def build_memory_context(
    guild_id: int,
    user_text: str,
    cognitive_state: dict | None = None,
    *,
    session_key: str | None = None,
    room_key: str | None = None,
    person_key: str | None = None,
    session_memory_key: str | None = None,
) -> str:
    layers = collect_memory_layers(
        guild_id,
        room_key=room_key,
        person_key=person_key,
        session_memory_key=session_memory_key,
    )
    facts = select_relevant_memory_rows(
        user_text,
        merge_memory_rows(*(layer["facts"] for layer in layers.values())),
        MEMORY_RETRIEVE_LIMIT,
    )
    questions = select_relevant_memory_rows(
        user_text,
        merge_memory_rows(*(layer["questions"] for layer in layers.values())),
        4,
    )
    vault_raw_rows = select_relevant_memory_rows(
        user_text,
        merge_memory_rows(*(layer["vault_raw"] for layer in layers.values())),
        MEMORY_VAULT_RAW_RETRIEVE_LIMIT,
    )
    state = normalize_cognitive_state(
        cognitive_state or read_layered_cognitive_state(
            guild_id,
            room_key=room_key,
            person_key=person_key,
            session_memory_key=session_memory_key,
        ) or {}
    )

    parts: list[str] = []
    summary_lines = [
        f"- {layer['label']}: {layer['summary']}"
        for layer in (layers.get("session"), layers.get("person"), layers.get("room"), layers.get("guild"))
        if layer and layer.get("summary")
    ]
    if summary_lines:
        parts.append("현재 작업 요약:\n" + "\n".join(summary_lines))

    session_rows = merge_recent_memory_rows(*(layer["raw"] for layer in (layers.get("session"),) if layer), limit=4)
    if session_rows:
        parts.append("현재 세션 최근 대화:\n" + format_memory_row_lines(session_rows))

    person_rows = merge_recent_memory_rows(*(layer["raw"] for layer in (layers.get("person"),) if layer), limit=4)
    if person_rows:
        parts.append("이 사람과의 최근 대화:\n" + format_memory_row_lines(person_rows))

    room_rows = merge_recent_memory_rows(
        *(layer["raw"] for layer in (layers.get("room"), layers.get("guild")) if layer),
        limit=MEMORY_RAW_CONTEXT_LIMIT,
    )
    if room_rows:
        parts.append("방 최근 대화:\n" + format_memory_row_lines(room_rows))

    if vault_raw_rows:
        parts.append("문서 보관함에서 꺼낸 관련 대화:\n" + format_memory_row_lines(vault_raw_rows))
    session_state = session_state_snapshot(session_key)
    if state.get("state_summary") or state.get("question_for_user") or state.get("main_prompt_hint") or session_state:
        action_label = {
            "answer": "답하기",
            "ask": "질문하기",
            "wait": "더 듣기",
        }.get(state.get("action", "answer"), "답하기")
        state_lines = [f"- 권장 행동: {action_label}"]
        if state.get("user_intent"):
            state_lines.append(f"- 사용자 의도: {state['user_intent']}")
        if state.get("state_summary"):
            state_lines.append(f"- 현재 판단: {state['state_summary']}")
        if state.get("question_for_user"):
            state_lines.append(f"- 사용자에게 되물을 내부 질문 초안: {state['question_for_user']}")
        if state.get("main_prompt_hint"):
            state_lines.append(f"- 응답 힌트: {state['main_prompt_hint']}")
        if state.get("retrieved_context_ids"):
            state_lines.append(f"- 참고 문맥 ID: {', '.join(state['retrieved_context_ids'][:4])}")
        if session_state.get("last_speaker"):
            state_lines.append(f"- 마지막 화자: {session_state['last_speaker']}")
        if session_state.get("awaiting_user_reply"):
            state_lines.append("- 사용자 후속 응답 대기 중")
        if session_state.get("topic_id"):
            state_lines.append(f"- 현재 topic_id: {session_state['topic_id']}")
        parts.append("현재 내부 상태(사용자 발화 아님):\n" + "\n".join(state_lines))
    if facts:
        parts.append(
            "장기 기억 후보:\n" + "\n".join(f"- {clean_text(str(row.get('text', '')))}" for row in facts)
        )
    if questions:
        parts.append(
            "열린 질문/가설:\n" + "\n".join(f"- {clean_text(str(row.get('text', '')))}" for row in questions)
        )

    if not parts:
        return ""

    return (
        "다음은 이전 대화에서 정리한 참고 메모다. 사실처럼 단정하지 말고, 현재 질문과 맞는 경우에만 자연스럽게 반영해라.\n\n"
        + "\n\n".join(parts)
    )


def extract_json_object(text: str) -> dict:
    text = text.strip()
    try:
        data = json.loads(text)
        if isinstance(data, dict):
            return data
    except Exception:
        pass

    start = text.find("{")
    end = text.rfind("}")
    if start != -1 and end != -1 and end > start:
        try:
            data = json.loads(text[start : end + 1])
            if isinstance(data, dict):
                return data
        except Exception:
            pass

    return {}


async def ask_summary_llm(
    messages: list[dict],
    *,
    max_tokens: int = 500,
    timeout_seconds: float = 90,
) -> dict:
    session = await get_http_session()
    payload = {
        "model": SUMMARY_MODEL_NAME,
        "messages": messages,
        "temperature": 0.1,
        "max_tokens": max_tokens,
        "stream": False,
    }
    timeout = aiohttp.ClientTimeout(total=timeout_seconds)

    async with session.post(SUMMARY_LLM_URL, json=payload, timeout=timeout) as resp:
        if resp.status != 200:
            error_text = await resp.text()
            raise RuntimeError(f"요약 LLM 서버 오류: {resp.status} / {error_text[:300]}")

        data = await resp.json()
        choices = data.get("choices", [])
        if not choices:
            return {}

        msg = choices[0].get("message", {})
        text = clean_text(msg.get("content", "") or msg.get("reasoning_content", ""))
        return extract_json_object(text)


async def ask_router_llm(
    messages: list[dict],
    *,
    max_tokens: int,
    timeout_seconds: float,
) -> dict:
    session = await get_http_session()
    payload = {
        "model": ROUTER_MODEL_NAME,
        "messages": messages,
        "temperature": 0.1,
        "max_tokens": max_tokens,
        "stream": False,
    }
    timeout = aiohttp.ClientTimeout(total=timeout_seconds)

    async with session.post(ROUTER_LLM_URL, json=payload, timeout=timeout) as resp:
        if resp.status != 200:
            error_text = await resp.text()
            raise RuntimeError(f"router LLM 서버 오류: {resp.status} / {error_text[:300]}")

        data = await resp.json()
        choices = data.get("choices", [])
        if not choices:
            return {}

        msg = choices[0].get("message", {})
        text = clean_text(msg.get("content", "") or msg.get("reasoning_content", ""))
        return extract_json_object(text)


async def classify_llm_route_async(user_text: str, *, guild_id: int | None = None, source: str = "text", session_key: str | None = None) -> tuple[str, dict | None]:
    fallback_route = classify_llm_route_fallback(user_text, source=source)
    fast_policy = fast_path_policy(user_text, source, session_state_snapshot(session_key))
    if fast_policy is not None:
        fast_route = normalize_route_name(str(fast_policy.get("route", fallback_route)))
        return fast_route, {
            "selected": fast_route,
            "source": "fast_path",
            "confidence": 0.92,
            "reason_brief": clean_text(str(fast_policy.get("reason_brief", "fast_path"))),
            "fallback": fallback_route,
        }
    force_voice_context = source == "voice" and should_force_voice_context_route(user_text)
    if (source == "voice" and not force_voice_context) or not ROUTER_LLM_ENABLED:
        return fallback_route, {"selected": fallback_route, "source": "fallback"}

    summary = compact_working_summary(read_text_file(memory_summary_path(guild_id))) if guild_id is not None else ""
    state = normalize_cognitive_state(read_json_file(cognitive_state_path(guild_id))) if guild_id is not None else normalize_cognitive_state({})
    recent_raw = read_jsonl(memory_raw_path(guild_id))[-3:] if guild_id is not None else []
    recent_facts = read_fact_rows(guild_id)[-3:] if guild_id is not None else []

    messages = [
        {
            "role": "system",
            "content": (
                '너는 경량 라우터다. 반드시 JSON 객체 하나만 출력한다. ',
                '형식은 {"selected": "main_direct|voice_context|sub_wait", "confidence": number, "reason_brief": string}. ',
                'main_direct는 메인 LLM만으로 바로 답하는 경우, voice_context는 최근 대화/상태를 강하게 이어받아야 하는 경우, sub_wait는 먼저 search/wait/search_then_answer 성격 판단이 필요한 경우다. ',
                'JSON 외 다른 텍스트는 절대 출력하지 마라.'
            ),
        },
        {
            "role": "user",
            "content": (
                f"최근 요약:\n{summary or '(없음)'}\n\n"
                f"현재 cognitive_state:\n{json.dumps(state, ensure_ascii=False)}\n\n"
                f"최근 raw_transcript:\n{format_memory_rows_for_llm(recent_raw, max_items=3)}\n\n"
                f"최근 durable_facts:\n{format_memory_rows_for_llm(recent_facts, max_items=3)}\n\n"
                f"현재 사용자 입력:\n{compact_memory_text(user_text, max_chars=160)}\n\n"
                f"fallback_route={fallback_route}\nsource={source}"
            ),
        },
    ]

    try:
        result = await ask_router_llm(messages, max_tokens=ROUTER_ROUTE_MAX_TOKENS, timeout_seconds=ROUTER_ROUTE_TIMEOUT_SEC)
    except Exception as e:
        print(f"[ROUTER] route 실패 fallback 사용: {e!r}")
        return fallback_route, {"selected": fallback_route, "source": "fallback", "error": clean_text(repr(e))[:120]}

    if not isinstance(result, dict):
        return fallback_route, {"selected": fallback_route, "source": "fallback", "reason_brief": "invalid_router_json"}

    selected = normalize_route_name(str(result.get("selected", fallback_route)))
    meta = {
        "selected": selected,
        "source": "router",
        "confidence": float(result.get("confidence", 0.0) or 0.0),
        "reason_brief": clean_text(str(result.get("reason_brief", ""))),
        "fallback": fallback_route,
    }
    return selected, meta
async def update_cognitive_state(
    guild_id: int,
    user_text: str,
    *,
    session_key: str | None = None,
    room_key: str | None = None,
    person_key: str | None = None,
    session_memory_key: str | None = None,
    source: str = "text",
    turn_scope: TurnScope | None = None,
) -> dict:
    started_at = time.monotonic()
    task = _attach_current_task(turn_scope)
    lock = cognitive_locks.setdefault(guild_id, asyncio.Lock())
    scope_type = "session" if session_memory_key else "person" if person_key else "room" if room_key else "guild"
    scope_key = session_memory_key if session_memory_key else person_key if person_key else room_key
    try:
        async with lock:
            if turn_scope is not None:
                turn_scope.raise_if_cancelled()
            layers = collect_memory_layers(
                guild_id,
                room_key=room_key,
                person_key=person_key,
                session_memory_key=session_memory_key,
            )
            summary_lines = [
                f"- {layer['label']}: {layer['summary']}"
                for layer in (layers.get("session"), layers.get("person"), layers.get("room"), layers.get("guild"))
                if layer and layer.get("summary")
            ]
            current_summary = "\n".join(summary_lines)
            current_state = normalize_cognitive_state(
                read_layered_cognitive_state(
                    guild_id,
                    room_key=room_key,
                    person_key=person_key,
                    session_memory_key=session_memory_key,
                ) or {}
            )
            speculative = get_matching_speculative_policy(session_key, user_text) if source == "voice" else None
            fast_policy = (speculative or {}).get("policy") or fast_path_policy(user_text, source, session_state_snapshot(session_key))
            if fast_policy is not None:
                state = build_fast_cognitive_state(
                    user_text,
                    action=str(fast_policy.get("action", "answer")),
                    current_state=current_state,
                    reason_brief=str(fast_policy.get("reason_brief", "fast_path")),
                )
                write_json_file(cognitive_state_path(guild_id, scope_type=scope_type, scope_key=scope_key), state)
                return state
            recent_raw = merge_recent_memory_rows(
                *(layer["raw"] for layer in layers.values()),
                limit=MEMORY_COGNITIVE_RAW_LIMIT,
            )
            recent_facts = merge_recent_memory_rows(
                *(layer["facts"] for layer in layers.values()),
                limit=4,
            )
            recent_questions = merge_recent_memory_rows(
                *(layer["questions"] for layer in layers.values()),
                limit=4,
            )

            messages = [
                {
                    "role": "system",
                    "content": (
                        '너는 실시간 대화 조율자다. 반드시 JSON 객체 하나만 출력한다. '
                        '형식은 {"action": "answer|ask|wait|search_then_answer", "confidence": number, "user_intent": string, "state_summary": string, "question_for_user": string, "main_prompt_hint": string, "reason_brief": string, "retrieved_context_ids": string[]}. '
                        'answer는 지금 답하면 되는 경우다. ask는 사용자의 원래 발화에 이어서 짧게 되묻거나 확인 질문을 하는 편이 자연스러운 경우다. wait는 아직 단정하지 말고 더 듣거나 짧게 여지를 두는 편이 자연스러운 경우다. search_then_answer는 최신 정보나 외부 확인이 필요해서 먼저 짧게 알리고 뒤이어 검색 결과를 전해야 하는 경우다. '
                        'question_for_user는 사용자가 한 말이 아니라, 메인 LLM이 사용자에게 되물을 내부 질문 초안이다. 절대로 사용자의 질문을 베껴 쓰거나 사용자가 이미 한 말처럼 적지 마라. '
                        'user_intent에는 사용자가 진짜로 하려는 말을 아주 짧게 적어라. state_summary에는 현재 상황을 한두 문장으로 적어라. main_prompt_hint에는 메인 LLM이 말할 때 지켜야 할 한 줄 힌트를 적어라. confidence는 0~1, reason_brief는 아주 짧게 써라. JSON 외 다른 텍스트는 절대 출력하지 마라.'
                    ),
                },
                {
                    "role": "user",
                    "content": (
                        f"이전 cognitive_state:\n{json.dumps(current_state, ensure_ascii=False)}\n\n"
                        f"현재 layered_summary:\n{current_summary or '(없음)'}\n\n"
                        f"최근 raw_transcript:\n{format_memory_rows_for_llm(recent_raw, max_items=MEMORY_COGNITIVE_RAW_LIMIT)}\n\n"
                        f"최근 durable_facts:\n{format_memory_rows_for_llm(recent_facts, max_items=4)}\n\n"
                        f"최근 open_questions:\n{format_memory_rows_for_llm(recent_questions, max_items=4)}\n\n"
                        f"현재 사용자 입력:\n{compact_memory_text(user_text, max_chars=160)}"
                    ),
                },
            ]

            try:
                if turn_scope is not None:
                    turn_scope.raise_if_cancelled()
                result = await ask_router_llm(
                    messages,
                    max_tokens=COGNITIVE_MAX_TOKENS,
                    timeout_seconds=COGNITIVE_TIMEOUT_SEC,
                )
            except Exception as e:
                if is_context_size_error(e):
                    compact_messages = [
                        messages[0],
                        {
                            "role": "user",
                            "content": (
                                f"현재 layered_summary:\n{current_summary or '(없음)'}\n\n"
                                f"현재 사용자 입력:\n{compact_memory_text(user_text, max_chars=120)}"
                            ),
                        },
                    ]
                    try:
                        if turn_scope is not None:
                            turn_scope.raise_if_cancelled()
                        result = await ask_router_llm(
                            compact_messages,
                            max_tokens=COGNITIVE_MAX_TOKENS,
                            timeout_seconds=max(3.0, COGNITIVE_TIMEOUT_SEC - 2.0),
                        )
                    except Exception as e2:
                        e = e2
                        print(f"[COGNITIVE] compact retry 실패: {e2}")
                    else:
                        print("[COGNITIVE] compact retry 성공")
                if "result" not in locals() or not isinstance(result, dict):
                    print(f"[COGNITIVE] 상태 업데이트 실패 또는 timeout: {e}")
                    elapsed_ms = (time.monotonic() - started_at) * 1000.0
                    if should_log_voice_timing(elapsed_ms):
                        print(f"[COGNITIVE LATENCY] guild={guild_id} scope={scope_type}:{scope_key or 'default'} failed_after_ms={elapsed_ms:.0f}")
                    fallback = current_state or {
                        "action": "answer",
                        "confidence": 0.5,
                        "user_intent": clean_text(user_text),
                        "state_summary": clean_text(user_text),
                        "question_for_user": "",
                        "main_prompt_hint": "짧고 자연스럽게 답해라.",
                        "reason_brief": "fallback",
                        "retrieved_context_ids": [],
                        "updated_at": int(time.time()),
                    }
                    write_json_file(cognitive_state_path(guild_id, scope_type=scope_type, scope_key=scope_key), fallback)
                    return fallback

            if turn_scope is not None:
                turn_scope.raise_if_cancelled()
            state = normalize_cognitive_state(result)
            if not state.get("state_summary"):
                state["state_summary"] = current_state.get("state_summary", "") or clean_text(user_text)
            if not state.get("main_prompt_hint"):
                state["main_prompt_hint"] = "짧고 자연스럽게 답해라."
            state["updated_at"] = int(time.time())
            write_json_file(cognitive_state_path(guild_id, scope_type=scope_type, scope_key=scope_key), state)
            elapsed_ms = (time.monotonic() - started_at) * 1000.0
            if should_log_voice_timing(elapsed_ms):
                print(f"[COGNITIVE LATENCY] guild={guild_id} scope={scope_type}:{scope_key or 'default'} action={state.get('action')} ms={elapsed_ms:.0f}")

            if state.get("action") == "ask" and state.get("question_for_user"):
                print(
                    f"[COGNITIVE ASK] guild={guild_id} scope={scope_type}:{scope_key or 'default'} question={state['question_for_user']!r} reason={state.get('reason_brief', '')!r} confidence={state.get('confidence', 0.0):.2f}"
                )
            elif state.get("action") == "search_then_answer":
                print(
                    f"[COGNITIVE SEARCH] guild={guild_id} scope={scope_type}:{scope_key or 'default'} intent={state.get('user_intent', '')!r} reason={state.get('reason_brief', '')!r}"
                )

            return state
    finally:
        _detach_task(turn_scope, task)
async def update_long_term_memory(
    guild_id: int,
    user_text: str,
    answer: str,
    *,
    room_key: str | None = None,
    person_key: str | None = None,
    session_memory_key: str | None = None,
    turn_scope: TurnScope | None = None,
) -> None:
    started_at = time.monotonic()
    task = _attach_current_task(turn_scope)
    lock = memory_locks.setdefault(guild_id, asyncio.Lock())
    scope_note = session_memory_key or room_key or "guild"
    try:
        async with lock:
            if turn_scope is not None:
                turn_scope.raise_if_cancelled()
            layers = collect_memory_layers(
                guild_id,
                room_key=room_key,
                person_key=person_key,
                session_memory_key=session_memory_key,
            )
            summary_lines = [
                f"- {layer['label']}: {layer['summary']}"
                for layer in (layers.get("session"), layers.get("person"), layers.get("room"), layers.get("guild"))
                if layer and layer.get("summary")
            ]
            current_summary = "\n".join(summary_lines)
            recent_raw = merge_recent_memory_rows(
                *(layer["raw"] for layer in layers.values()),
                limit=MEMORY_LONGTERM_RAW_LIMIT,
            )
            recent_facts = merge_recent_memory_rows(
                *(layer["facts"] for layer in layers.values()),
                limit=6,
            )
            recent_questions = merge_recent_memory_rows(
                *(layer["questions"] for layer in layers.values()),
                limit=4,
            )

            messages = [
                {
                    "role": "system",
                    "content": (
                        '너는 대화 장기기억 관리자이자 상황 정리자다. 반드시 JSON 객체 하나만 출력한다. '
                        '형식은 {"summary_update": string, "durable_facts": [{"type": string, "text": string}], "open_questions": [{"type": string, "text": string}]}. '
                        'summary_update는 지금 상황을 짧고 자연스러운 한국어로 압축한 누적 요약이다. '
                        'durable_facts에는 오래 기억할 만한 선호, 설정, 프로젝트 결정, 반복되는 사실만 넣어라. '
                        'open_questions에는 아직 확정되지 않은 추정, 확인이 필요한 질문, 다음에 물어볼 만한 포인트만 넣어라. '
                        '잡담, 일회성 노이즈, 이미 해결된 내용은 넣지 마라. JSON 외 다른 텍스트는 절대 출력하지 마라.'
                    ),
                },
                {
                    "role": "user",
                    "content": (
                        f"현재 layered_summary:\n{current_summary or '(없음)'}\n\n"
                        f"최근 raw_transcript:\n{format_memory_rows_for_llm(recent_raw, max_items=MEMORY_LONGTERM_RAW_LIMIT)}\n\n"
                        f"최근 durable_facts:\n{format_memory_rows_for_llm(recent_facts, max_items=6)}\n\n"
                        f"최근 open_questions:\n{format_memory_rows_for_llm(recent_questions, max_items=4)}\n\n"
                        f"새 대화:\n- user: {compact_memory_text(user_text, max_chars=120)}\n- assistant: {compact_memory_text(answer, max_chars=120)}"
                    ),
                },
            ]

            try:
                if turn_scope is not None:
                    turn_scope.raise_if_cancelled()
                result = await ask_summary_llm(messages)
            except Exception as e:
                if is_context_size_error(e):
                    compact_messages = [
                        messages[0],
                        {
                            "role": "user",
                            "content": (
                                f"현재 layered_summary:\n{current_summary or '(없음)'}\n\n"
                                f"새 대화:\n- user: {compact_memory_text(user_text, max_chars=100)}\n- assistant: {compact_memory_text(answer, max_chars=100)}"
                            ),
                        },
                    ]
                    try:
                        if turn_scope is not None:
                            turn_scope.raise_if_cancelled()
                        result = await ask_summary_llm(compact_messages, max_tokens=220, timeout_seconds=20)
                    except Exception as e2:
                        e = e2
                        print(f"[MEMORY] compact retry 실패: {e2}")
                    else:
                        print("[MEMORY] compact retry 성공")
                if "result" not in locals() or not isinstance(result, dict):
                    print(f"[MEMORY] 요약 업데이트 실패: {e}")
                    elapsed_ms = (time.monotonic() - started_at) * 1000.0
                    if should_log_voice_timing(elapsed_ms):
                        print(f"[MEMORY LATENCY] guild={guild_id} scope={scope_note} failed_after_ms={elapsed_ms:.0f}")
                    return

            if turn_scope is not None:
                turn_scope.raise_if_cancelled()
            scope_targets: list[tuple[str, str | None]] = [("guild", None)]
            if room_key:
                scope_targets.append(("room", room_key))
            if person_key:
                scope_targets.append(("person", person_key))
            if session_memory_key:
                scope_targets.append(("session", session_memory_key))

            summary_update = compact_working_summary(str(result.get("summary_update", "")))
            if summary_update:
                for scope_type, scope_key in scope_targets:
                    write_text_file(memory_summary_path(guild_id, scope_type=scope_type, scope_key=scope_key), summary_update)

            durable_facts = result.get("durable_facts", [])
            if isinstance(durable_facts, list):
                rows = [row for row in durable_facts if isinstance(row, dict)]
                for scope_type, scope_key in scope_targets:
                    append_unique_memory_rows(
                        memory_facts_path(guild_id, scope_type=scope_type, scope_key=scope_key),
                        rows,
                        MEMORY_FACT_LIMIT,
                        mirror_path=vault_facts_path(guild_id, scope_type=scope_type, scope_key=scope_key),
                    )

            open_questions = result.get("open_questions", [])
            if isinstance(open_questions, list):
                rows = [row for row in open_questions if isinstance(row, dict)]
                for scope_type, scope_key in scope_targets:
                    append_unique_memory_rows(
                        memory_questions_path(guild_id, scope_type=scope_type, scope_key=scope_key),
                        rows,
                        MEMORY_LOOP_LIMIT,
                        mirror_path=vault_questions_path(guild_id, scope_type=scope_type, scope_key=scope_key),
                    )

            elapsed_ms = (time.monotonic() - started_at) * 1000.0
            if should_log_voice_timing(elapsed_ms):
                print(f"[MEMORY LATENCY] guild={guild_id} scope={scope_note} ms={elapsed_ms:.0f}")
    finally:
        _detach_task(turn_scope, task)
def should_run_memory_update(
    *,
    guild_id: int,
    user_text: str,
    answer: str,
    source: str,
    session_key: str | None = None,
) -> bool:
    cleaned_user = clean_text(user_text)
    cleaned_answer = clean_text(answer)
    merged = clean_text(f"{cleaned_user} {cleaned_answer}")
    text_len = len(cleaned_user)
    has_open_question = ("?" in cleaned_user) or ("？" in cleaned_user) or ("?" in cleaned_answer) or ("？" in cleaned_answer)
    explicit_fact_markers = ("내 ", "나는 ", "제가 ", "우리는 ", "설정", "결정", "기억해", "기억해줘", "해야", "하기로")
    has_explicit_fact = any(marker in merged for marker in explicit_fact_markers)
    is_smalltalk = (not needs_search_or_deep_routing(cleaned_user)) and len(cleaned_user) <= 14 and len(cleaned_answer) <= 32
    turn_index = 1
    idle_gap_sec = 0.0
    if session_key:
        history_len = len(get_conversation_history(session_key=session_key, guild_id=guild_id))
        turn_index = max(1, (history_len + 1) // 2)
        idle_gap_sec = max(0.0, time.monotonic() - float(session_last_active_at.get(session_key, 0.0) or 0.0))

    if has_explicit_fact:
        return True
    if has_open_question:
        return True
    if turn_index % 4 == 0:
        return True
    if source == "voice" and text_len < 12:
        return False
    if is_smalltalk:
        return False
    return idle_gap_sec >= 20.0


def schedule_memory_update(
    guild_id: int,
    user_text: str,
    answer: str,
    *,
    room_key: str | None = None,
    person_key: str | None = None,
    session_memory_key: str | None = None,
    source: str = "chat",
    user_speaker: str = "user",
    assistant_speaker: str = "Evelyn",
    session_key: str | None = None,
    turn_scope: TurnScope | None = None,
    runtime_mode: str | None = None,
) -> None:
    rows = [
        {"role": "user", "speaker": user_speaker, "source": source, "text": user_text},
        {"role": "assistant", "speaker": assistant_speaker, "source": source, "text": answer},
    ]
    append_raw_transcript_rows(guild_id, rows)
    if room_key:
        append_raw_transcript_rows(guild_id, rows, scope_type="room", scope_key=room_key)
    if person_key:
        append_raw_transcript_rows(guild_id, rows, scope_type="person", scope_key=person_key)
    if session_memory_key:
        append_raw_transcript_rows(guild_id, rows, scope_type="session", scope_key=session_memory_key)

    if not should_run_memory_update(
        guild_id=guild_id,
        user_text=user_text,
        answer=answer,
        source=source,
        session_key=session_key,
    ):
        return

    mode = runtime_mode or "normal"
    if mode == "realtime":
        return

    memory_task_key = session_memory_key or room_key or session_key or runtime_session_key(guild_id=guild_id)
    if mode == "batch" and memory_task_key is not None:
        existing = background_memory_tasks.get(memory_task_key)
        if existing is not None and not existing.done():
            existing.cancel()
        async def _batched_memory_refresh() -> None:
            try:
                await asyncio.sleep(1.5)
                if turn_scope is not None:
                    turn_scope.raise_if_cancelled()
                await update_long_term_memory(
                    guild_id,
                    user_text,
                    answer,
                    room_key=room_key,
                    person_key=person_key,
                    session_memory_key=session_memory_key,
                    turn_scope=turn_scope,
                )
                await update_cognitive_state(
                    guild_id,
                    user_text,
                    session_key=session_key,
                    room_key=room_key,
                    person_key=person_key,
                    session_memory_key=session_memory_key,
                    source=source,
                    turn_scope=turn_scope,
                )
            finally:
                task = background_memory_tasks.get(memory_task_key)
                if task is asyncio.current_task():
                    background_memory_tasks.pop(memory_task_key, None)
        background_memory_tasks[memory_task_key] = create_turn_scoped_task(_batched_memory_refresh(), turn_scope=turn_scope)
        return

    create_turn_scoped_task(
        update_long_term_memory(
            guild_id,
            user_text,
            answer,
            room_key=room_key,
            person_key=person_key,
            session_memory_key=session_memory_key,
            turn_scope=turn_scope,
        ),
        turn_scope=turn_scope,
    )
    create_turn_scoped_task(
        update_cognitive_state(
            guild_id,
            user_text,
            session_key=session_key,
            room_key=room_key,
            person_key=person_key,
            session_memory_key=session_memory_key,
            source=source,
            turn_scope=turn_scope,
        ),
        turn_scope=turn_scope,
    )


BAD_TAIL_WORDS = (
    "그리고",
    "근데",
    "하지만",
    "다만",
    "또",
    "그래서",
)

BAD_TAIL_SUFFIXES = (
    "은", "는", "이", "가", "을", "를", "에", "와", "과",
    "도", "로", "며", "고", "서", "면", "한", "할",
)

GOOD_END_SUFFIXES = (
    "다", "요", "지", "네", "까", "어", "아", "음",
)


@dataclass(frozen=True)
class ChunkWindow:
    min_chars: int
    target_chars: int
    max_chars: int
    allow_soft_breaks: bool = True
    soft_break_overflow_only: bool = False


@dataclass
class ChunkerConfig:
    hard_breaks: tuple[str, ...] = (".", "!", "?", "\n", "。", "！", "？")
    soft_breaks: tuple[str, ...] = (",", "，", "…", ";", "；", ":", "：")
    hard_break_grace_chars: int = 10
    candidate_unstable_penalty: int = 80
    natural_end_bonus: int = 12
    first_window: ChunkWindow = field(default_factory=lambda: ChunkWindow(18, 24, 40, True, False))
    next_window: ChunkWindow = field(default_factory=lambda: ChunkWindow(12, 36, 72, False, True))
    structured_first_window: ChunkWindow = field(default_factory=lambda: ChunkWindow(22, 30, 48, False, False))
    structured_next_window: ChunkWindow = field(default_factory=lambda: ChunkWindow(18, 40, 84, False, True))


def _normalized_tail_probe(text: str) -> str:
    s = clean_tts_text(text).strip()
    if not s:
        return ""
    while s and s[-1] in " \t\r\n,，;；:：….!?。！？)]}>'\"”’」』】":
        s = s[:-1].rstrip()
    return s


def has_unbalanced_pairs(text: str) -> bool:
    s = text or ""
    pairs = (("(", ")"), ("[", "]"), ("{", "}"))
    for left, right in pairs:
        if s.count(left) != s.count(right):
            return True
    if s.count('"') % 2 == 1:
        return True
    if s.count("'") % 2 == 1:
        return True
    if s.count("```") % 2 == 1:
        return True
    return False


def is_unstable_tail(chunk: str) -> bool:
    s = clean_tts_text(chunk).strip()
    if not s:
        return True
    if has_unbalanced_pairs(s):
        return True

    tail_probe = _normalized_tail_probe(s)
    if not tail_probe:
        return True

    for word in BAD_TAIL_WORDS:
        if tail_probe.endswith(word):
            return True

    for suffix in BAD_TAIL_SUFFIXES:
        if tail_probe.endswith(suffix):
            return True

    return False


def has_natural_end(chunk: str) -> bool:
    tail_probe = _normalized_tail_probe(chunk)
    if not tail_probe:
        return False
    return tail_probe.endswith(GOOD_END_SUFFIXES)


def detect_output_shape(text: str) -> str:
    s = text or ""
    stripped = s.lstrip()
    if not stripped:
        return "chat"
    if stripped.startswith(("```", "`")):
        return "structured"
    if re.search(r"(?m)^\s*(?:[-*•]|\d+[.)])\s+\S", stripped):
        return "structured"
    if re.search(r"(?m)^\s*\|.+\|\s*$", stripped):
        return "structured"
    return "chat"


@dataclass
class SpeechChunker:
    config: ChunkerConfig = field(default_factory=ChunkerConfig)
    buf: str = ""
    sent_first: bool = False
    mode: str = "chat"

    def push(self, delta: str, *, max_chunks: int | None = 1) -> list[str]:
        if delta:
            self.buf += delta
        self.mode = detect_output_shape(self.buf)

        out: list[str] = []
        while True:
            cut = self._find_dispatch_point(self.buf)
            if cut is None:
                break

            chunk = self._consume(cut)
            if not chunk:
                continue

            out.append(chunk)
            self.sent_first = True
            if max_chunks is not None and len(out) >= max_chunks:
                break

        return out

    def flush(self) -> list[str]:
        tail = clean_tts_text(self.buf)
        self.buf = ""
        return [tail] if tail else []

    def _window(self) -> ChunkWindow:
        if self.mode == "structured":
            return self.config.structured_next_window if self.sent_first else self.config.structured_first_window
        return self.config.next_window if self.sent_first else self.config.first_window

    def _consume(self, cut: int) -> str:
        raw = self.buf[:cut]
        self.buf = self.buf[cut:].lstrip()
        return clean_tts_text(raw)

    def _find_dispatch_point(self, text: str) -> int | None:
        if not text.strip():
            return None

        window = self._window()
        best_idx: int | None = None
        best_score = -(10 ** 9)
        best_kind: str | None = None
        best_visible_len = 0
        clean_len = len(clean_text(text))

        for i, ch in enumerate(text):
            raw_idx = i + 1
            chunk = clean_tts_text(text[:raw_idx])
            visible_len = len(clean_text(chunk))
            if visible_len < window.min_chars:
                continue

            is_hard = ch in self.config.hard_breaks
            is_soft = ch in self.config.soft_breaks
            if not is_hard:
                if not is_soft:
                    continue
                if not window.allow_soft_breaks:
                    if not window.soft_break_overflow_only or visible_len < window.max_chars:
                        continue

            score = 100 if is_hard else 55
            score -= abs(visible_len - window.target_chars)
            if visible_len > window.max_chars:
                score -= 20
            if is_unstable_tail(chunk):
                score -= self.config.candidate_unstable_penalty
            if has_natural_end(chunk):
                score += self.config.natural_end_bonus

            if score > best_score:
                best_score = score
                best_idx = raw_idx
                best_kind = "hard" if is_hard else "soft"
                best_visible_len = visible_len

        if best_idx is not None and best_kind == "soft":
            for i, ch in enumerate(text):
                raw_idx = i + 1
                if raw_idx <= best_idx or ch not in self.config.hard_breaks:
                    continue
                candidate = clean_tts_text(text[:raw_idx])
                visible_len = len(clean_text(candidate))
                if visible_len > window.max_chars:
                    continue
                if visible_len - best_visible_len > self.config.hard_break_grace_chars:
                    continue
                if is_unstable_tail(candidate):
                    continue
                return raw_idx

        if best_idx is None and clean_len >= window.max_chars:
            forced_idx = self._find_forced_cut(text, window.max_chars)
            forced_chunk = clean_tts_text(text[:forced_idx])
            if forced_chunk and not is_unstable_tail(forced_chunk):
                return forced_idx

        return best_idx

    def _find_forced_cut(self, text: str, max_chars: int) -> int:
        visible_count = 0
        target_raw_idx = len(text)

        for i, ch in enumerate(text):
            if not ch.isspace() or visible_count > 0:
                visible_count += 1
            if visible_count >= max_chars:
                target_raw_idx = i + 1
                break

        search_start = max(1, target_raw_idx - 14)
        window = text[search_start - 1:target_raw_idx]
        for j in range(len(window) - 1, -1, -1):
            ch = window[j]
            raw_idx = (search_start - 1) + j + 1
            if ch.isspace() or ch in self.config.soft_breaks or ch in self.config.hard_breaks:
                return raw_idx
        return target_raw_idx


def split_tts_sentences(
    buffer: str,
    *,
    force: bool = False,
    emitted_chunks: int = 0,
) -> tuple[list[str], str]:
    chunker = SpeechChunker(sent_first=emitted_chunks > 0)
    chunks = chunker.push(buffer or "", max_chunks=None)
    if not force:
        return chunks, chunker.buf
    chunks.extend(chunker.flush())
    return chunks, ""


RESPONSE_ACTION_TAGS = {"찾기": "search", "질문": "ask", "대기": "wait", "답변": "answer"}


def parse_response_action_tag(text: str) -> tuple[str | None, str]:
    raw = text or ""
    match = re.match(r"^\s*\[(찾기|질문|대기|답변)\]\s*", raw)
    if not match:
        return None, clean_text(raw)
    action = RESPONSE_ACTION_TAGS.get(match.group(1))
    stripped = clean_text(raw[match.end():])
    return action, stripped


def sanitize_model_output(text: str) -> str:
    text = text or ""
    text = re.sub(r"<think>.*?</think>", "", text, flags=re.DOTALL | re.IGNORECASE)
    text = re.sub(r"<think>.*$", "", text, flags=re.DOTALL | re.IGNORECASE)
    text = normalize_omnivoice_tags(text)
    _action, cleaned = parse_response_action_tag(text)
    text = clean_text(cleaned)
    return text


def strip_markdown_noise(text: str) -> str:
    text = re.sub(r"[*_`#]+", "", text)
    text = re.sub(r"^[\-\*\d\.\)\s]+", "", text)
    return clean_text(text)


def looks_like_meta_line(text: str) -> bool:
    t = text.strip().lower()
    blocked_prefixes = (
        "thinking process",
        "analyze the request",
        "determine the",
        "draft",
        "option",
        "selecting",
        "refining",
        "final polish",
        "final output generation",
        "role:",
        "language:",
        "format:",
        "length:",
        "tone:",
        "content:",
        "input:",
        "wait",
        "let's",
        "let’s",
        "or:",
    )
    if any(t.startswith(p) for p in blocked_prefixes):
        return True
    if "**" in text:
        return True
    if len(re.findall(r"[A-Za-z]", text)) > len(re.findall(r"[가-힣]", text)) * 2:
        return True
    return False


def extract_answer_from_reasoning(reasoning: str, user_text: str) -> str:
    text = sanitize_model_output(reasoning)
    if not text:
        return ""

    text = text.replace("\r", "\n")
    candidates: list[str] = []

    quoted = re.findall(r"[\"“”'‘’]([^\"“”'‘’]{4,120})[\"“”'‘’]", text)
    for q in quoted:
        q = strip_markdown_noise(q)
        if not q or not re.search(r"[가-힣]", q):
            continue
        if looks_like_meta_line(q):
            continue
        if clean_text(q) == clean_text(user_text):
            continue
        candidates.append(q)

    explicit_patterns = [
        r"(?:최종\s*답변|답변|response|assistant)\s*[:：]\s*([^\n]{4,120})",
        r"(?:최종\s*출력|final\s*answer)\s*[:：]\s*([^\n]{4,120})",
    ]
    for pattern in explicit_patterns:
        for match in re.findall(pattern, text, flags=re.IGNORECASE):
            candidate = strip_markdown_noise(match)
            if not candidate or not re.search(r"[가-힣]", candidate):
                continue
            if looks_like_meta_line(candidate):
                continue
            if clean_text(candidate) == clean_text(user_text):
                continue
            candidates.append(candidate)

    seen = set()
    filtered: list[str] = []
    for c in candidates:
        c = clean_text(c).strip("\"'“”‘’")
        if not c or c in seen:
            continue
        seen.add(c)
        if len(c) < 6 or len(c) > 120:
            continue
        filtered.append(c)

    return filtered[-1] if filtered else ""


async def get_http_session() -> aiohttp.ClientSession:
    global http_session
    if http_session is None or http_session.closed:
        timeout = aiohttp.ClientTimeout(total=None, connect=10, sock_connect=10)
        http_session = aiohttp.ClientSession(timeout=timeout)
    return http_session


def answer_promises_search(answer_text: str) -> bool:
    action, stripped = parse_response_action_tag(answer_text)
    if action == "search":
        return True
    text = clean_text(strip_omnivoice_tags(stripped)).lower()
    if not text:
        return False

    completed_markers = [
        "찾아봤",
        "검색해봤",
        "확인해봤",
        "알아봤",
        "찾아보니",
        "검색해보니",
        "결과는",
    ]
    if any(marker in text for marker in completed_markers):
        return False

    promise_markers = [
        "찾아볼게",
        "찾아볼께",
        "찾아보고",
        "찾아보고 올게",
        "찾아서 알려줄게",
        "찾아서 말해줄게",
        "검색해볼게",
        "검색해볼께",
        "검색해서 알려줄게",
        "검색해서 말해줄게",
        "확인해볼게",
        "확인해줄게",
        "확인해서 알려줄게",
        "확인해서 말해줄게",
        "알아볼게",
        "알아봐서 알려줄게",
        "알아봐서 말해줄게",
        "조사해볼게",
        "조사해서 알려줄게",
        "찾는 중",
        "찾아보고 있어",
        "자료 찾아볼게",
    ]
    return any(marker in text for marker in promise_markers)


def build_search_query(guild_id: int, user_text: str) -> str:
    text = clean_text(strip_omnivoice_tags(user_text))
    if len(text) >= 8:
        return text

    summary = compact_working_summary(read_text_file(memory_summary_path(guild_id)))
    if summary:
        return clean_text(f"{text} {summary}")
    return text


def decode_duckduckgo_url(url: str) -> str:
    parsed = urlparse(url)
    if "duckduckgo.com" in parsed.netloc and parsed.path.startswith("/l/"):
        target = parse_qs(parsed.query).get("uddg", [""])[0]
        if target:
            return unquote(target)
    return html.unescape(url)


def strip_html_tags(text: str) -> str:
    return clean_text(html.unescape(re.sub(r"<[^>]+>", " ", text or "")))


async def search_duckduckgo(query: str, *, limit: int = 5) -> list[dict]:
    session = await get_http_session()
    headers = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/124.0 Safari/537.36"}
    results: list[dict] = []
    seen_urls: set[str] = set()

    try:
        async with session.get(
            "https://api.duckduckgo.com/",
            params={
                "q": query,
                "format": "json",
                "no_html": "1",
                "skip_disambig": "0",
            },
            headers=headers,
            timeout=aiohttp.ClientTimeout(total=12),
        ) as resp:
            if resp.status == 200:
                data = await resp.json()
                abstract = clean_text(str(data.get("AbstractText", "")))
                abstract_url = clean_text(str(data.get("AbstractURL", "")))
                abstract_source = clean_text(str(data.get("AbstractSource", "DuckDuckGo"))) or "DuckDuckGo"
                if abstract and abstract_url:
                    results.append({
                        "title": abstract_source,
                        "snippet": abstract,
                        "url": abstract_url,
                    })
                    seen_urls.add(abstract_url)

                def collect_topics(items):
                    for item in items or []:
                        if not isinstance(item, dict):
                            continue
                        if "Topics" in item:
                            collect_topics(item.get("Topics"))
                            continue
                        snippet = clean_text(str(item.get("Text", "")))
                        url = clean_text(str(item.get("FirstURL", "")))
                        if not snippet or not url or url in seen_urls:
                            continue
                        results.append({
                            "title": snippet.split(" - ", 1)[0][:80],
                            "snippet": snippet,
                            "url": url,
                        })
                        seen_urls.add(url)
                        if len(results) >= limit:
                            return

                collect_topics(data.get("RelatedTopics", []))
    except Exception as e:
        print(f"[SEARCH] instant API 실패: {e!r}")

    if len(results) >= limit:
        return results[:limit]

    try:
        async with session.post(
            "https://html.duckduckgo.com/html/",
            data={"q": query, "kl": "kr-ko"},
            headers=headers,
            timeout=aiohttp.ClientTimeout(total=15),
        ) as resp:
            if resp.status != 200:
                return results[:limit]
            page = await resp.text()
    except Exception as e:
        print(f"[SEARCH] html search 실패: {e!r}")
        return results[:limit]

    pattern = re.compile(
        r'<a[^>]+class="result__a"[^>]+href="(?P<url>[^"]+)"[^>]*>(?P<title>.*?)</a>(?P<rest>.*?)</div>',
        re.S,
    )
    for match in pattern.finditer(page):
        url = decode_duckduckgo_url(match.group("url"))
        if not url or url in seen_urls:
            continue
        title = strip_html_tags(match.group("title"))
        snippet_match = re.search(r'class="result__snippet"[^>]*>(.*?)</a>|class="result__snippet"[^>]*>(.*?)</div>', match.group("rest"), re.S)
        snippet = strip_html_tags((snippet_match.group(1) or snippet_match.group(2)) if snippet_match else "")
        if not title and not snippet:
            continue
        results.append({"title": title or url, "snippet": snippet, "url": url})
        seen_urls.add(url)
        if len(results) >= limit:
            break

    return results[:limit]


async def answer_from_search_results(query: str, results: list[dict]) -> str:
    if not results:
        return "찾아봤는데 지금 바로 쓸 만한 결과를 못 찾았어. 검색어를 조금 더 구체적으로 말해주면 다시 찾아볼게."

    session = await get_http_session()
    payload = {
        "model": MODEL_NAME,
        "messages": [
            {
                "role": "system",
                "content": (
                    "너는 검색 결과를 짧게 정리하는 비서다. 검색은 이미 끝났다. "
                    "'찾아볼게', '찾는 중', '확인해볼게' 같은 표현은 절대 쓰지 마라. "
                    "찾은 내용만 한국어로 바로 말하고, 한두 문장 뒤에 필요하면 출처 1~2개를 괄호로 덧붙여라."
                ),
            },
            {
                "role": "user",
                "content": (
                    f"사용자 질문:\n{query}\n\n"
                    + "검색 결과:\n"
                    + "\n".join(
                        f"- {clean_text(row.get('title', ''))} | {clean_text(row.get('snippet', ''))} | {clean_text(row.get('url', ''))}"
                        for row in results[:5]
                    )
                ),
            },
        ],
        "temperature": 0.1,
        "max_tokens": 220,
        "stream": False,
    }

    async with session.post(LLM_SERVER_URL, json=payload, timeout=aiohttp.ClientTimeout(total=45)) as resp:
        if resp.status != 200:
            error_text = await resp.text()
            raise RuntimeError(f"검색 정리 LLM 오류: {resp.status} / {error_text[:300]}")
        data = await resp.json()

    choices = data.get("choices", [])
    if not choices:
        first = results[0]
        return clean_text(f"찾아보니까 {first.get('snippet', '')} ({first.get('url', '')})")

    message = choices[0].get("message", {})
    answer = sanitize_model_output(message.get("content", ""))
    if answer:
        return answer

    first = results[0]
    return clean_text(f"찾아보니까 {first.get('snippet', '')} ({first.get('url', '')})")


async def deliver_proactive_followup(
    guild_id: int,
    query: str,
    answer: str,
    *,
    session_key: str | None,
    room_key: str | None,
    person_key: str | None,
    session_memory_key: str | None,
    channel_id: int | None,
    reply_to_message_id: int | None = None,
    source: str,
    turn_scope: TurnScope | None = None,
    runtime_mode: str | None = None,
) -> None:
    if turn_scope is not None:
        turn_scope.raise_if_cancelled()
    plain_answer = strip_omnivoice_tags(answer) or answer
    guild = bot.get_guild(guild_id)
    target_channel_id = channel_id
    stored_target = session_followup_targets.get(session_key, {}) if session_key is not None else {}
    if target_channel_id is None and session_key is not None:
        target_channel_id = stored_target.get("channel_id")
    reply_target_id = reply_to_message_id if reply_to_message_id is not None else stored_target.get("message_id")

    if target_channel_id is not None:
        channel = bot.get_channel(target_channel_id)
        if channel is None:
            try:
                channel = await bot.fetch_channel(target_channel_id)
            except Exception:
                channel = None
        if channel is not None and hasattr(channel, "send"):
            if turn_scope is not None:
                turn_scope.raise_if_cancelled()
            try:
                if reply_target_id is not None:
                    await channel.send(format_display_text(answer, session_key=session_key), reference=discord.Object(id=int(reply_target_id)))
                else:
                    await channel.send(format_display_text(answer, session_key=session_key))
            except Exception:
                await channel.send(format_display_text(answer, session_key=session_key))

    vc = guild.voice_client if guild else None
    if vc is not None and vc.is_connected():
        try:
            if turn_scope is not None:
                turn_scope.raise_if_cancelled()
            await speak_answer(vc, answer, turn_id=current_turn_id(session_key), session_key=session_key)
        except Exception as e:
            print(f"[SEARCH] proactive TTS 실패: {e!r}")

    if turn_scope is not None:
        turn_scope.raise_if_cancelled()
    append_history(session_key, query, plain_answer, guild_id=guild_id)
    schedule_memory_update(
        guild_id,
        query,
        plain_answer,
        room_key=room_key,
        person_key=person_key,
        session_memory_key=session_memory_key,
        source=source,
        user_speaker="search_task",
        assistant_speaker="Evelyn",
        turn_scope=turn_scope,
        runtime_mode=runtime_mode,
    )


def normalize_search_key(session_key: str, query: str) -> str:
    return f"{session_key}:{clean_text(query).lower()}"


def schedule_search_followup_singleflight(
    guild_id: int,
    query: str,
    *,
    session_key: str,
    room_key: str | None,
    person_key: str | None,
    session_memory_key: str | None,
    channel_id: int | None,
    reply_to_message_id: int | None,
    source: str,
    turn_scope: TurnScope | None = None,
    runtime_mode: str | None = None,
) -> asyncio.Task:
    search_key = normalize_search_key(session_key, query)
    existing = inflight_search_tasks.get(search_key)
    if existing is not None and not existing.done():
        return existing
    task = create_turn_scoped_task(
        run_search_followup(
            guild_id,
            query,
            session_key=session_key,
            room_key=room_key,
            person_key=person_key,
            session_memory_key=session_memory_key,
            channel_id=channel_id,
            reply_to_message_id=reply_to_message_id,
            source=source,
            turn_scope=turn_scope,
            runtime_mode=runtime_mode,
            search_key=search_key,
        ),
        turn_scope=turn_scope,
    )
    inflight_search_tasks[search_key] = task
    return task


async def run_search_followup(
    guild_id: int,
    query: str,
    *,
    session_key: str | None,
    room_key: str | None,
    person_key: str | None,
    session_memory_key: str | None,
    channel_id: int | None,
    reply_to_message_id: int | None = None,
    source: str,
    turn_scope: TurnScope | None = None,
    runtime_mode: str | None = None,
    search_key: str | None = None,
) -> None:
    task = _attach_current_task(turn_scope)
    try:
        if turn_scope is not None:
            turn_scope.raise_if_cancelled()
        results = await search_duckduckgo(query)
        if turn_scope is not None:
            turn_scope.raise_if_cancelled()
        answer = await answer_from_search_results(query, results)
        removed = resolve_open_question_rows(guild_id, query, answer)
        if room_key:
            removed += resolve_open_question_rows(guild_id, query, answer, scope_type="room", scope_key=room_key)
        if person_key:
            removed += resolve_open_question_rows(guild_id, query, answer, scope_type="person", scope_key=person_key)
        if session_memory_key:
            removed += resolve_open_question_rows(guild_id, query, answer, scope_type="session", scope_key=session_memory_key)
        if removed:
            print(f"[SEARCH] resolved_open_questions guild={guild_id} removed={removed}")
        completed_state = {
            "action": "answer",
            "confidence": 1.0,
            "user_intent": clean_text(query),
            "state_summary": "검색을 마쳤고 결과를 사용자에게 전달했다.",
            "question_for_user": "",
            "main_prompt_hint": "찾은 내용을 바로 전달해라.",
            "reason_brief": "search_completed",
            "retrieved_context_ids": [],
            "updated_at": int(time.time()),
        }
        write_json_file(cognitive_state_path(guild_id), completed_state)
        if room_key:
            write_json_file(cognitive_state_path(guild_id, scope_type="room", scope_key=room_key), completed_state)
        if person_key:
            write_json_file(cognitive_state_path(guild_id, scope_type="person", scope_key=person_key), completed_state)
        if session_memory_key:
            write_json_file(cognitive_state_path(guild_id, scope_type="session", scope_key=session_memory_key), completed_state)
        if turn_scope is not None:
            turn_scope.raise_if_cancelled()
        await deliver_proactive_followup(
            guild_id,
            query,
            answer,
            session_key=session_key,
            room_key=room_key,
            person_key=person_key,
            session_memory_key=session_memory_key,
            channel_id=channel_id,
            reply_to_message_id=reply_to_message_id,
            source=source,
            turn_scope=turn_scope,
            runtime_mode=runtime_mode,
        )
    except asyncio.CancelledError:
        raise
    except Exception as e:
        print(f"[SEARCH] follow-up 실패 guild={guild_id} query={query!r} err={e!r}")
    finally:
        task_key = runtime_session_key(session_key=session_key, guild_id=guild_id)
        task_ref = background_search_tasks.get(task_key) if task_key is not None else None
        if task_ref is asyncio.current_task() and task_key is not None:
            background_search_tasks.pop(task_key, None)
        if search_key:
            inflight = inflight_search_tasks.get(search_key)
            if inflight is asyncio.current_task():
                inflight_search_tasks.pop(search_key, None)
        _detach_task(turn_scope, task)


def schedule_search_followup(
    guild_id: int,
    session_key: str | None,
    user_text: str,
    answer: str,
    *,
    room_key: str | None = None,
    person_key: str | None = None,
    session_memory_key: str | None = None,
    channel_id: int | None,
    reply_to_message_id: int | None = None,
    source: str,
    force: bool = False,
    turn_scope: TurnScope | None = None,
    runtime_mode: str | None = None,
) -> None:
    global search_followup_queued_count
    if not guild_id:
        return
    opts = apply_runtime_mode(runtime_mode or "normal")
    if opts.get("skip_search_followup") and not force:
        return
    tagged_action, stripped_answer = parse_response_action_tag(answer)
    wants_search_by_tag = tagged_action == "search"
    wants_search_by_fallback = answer_promises_search(stripped_answer)
    if wants_search_by_tag:
        wants_search_by_fallback = False
    if not force and not wants_search_by_tag and not wants_search_by_fallback:
        return
    query = build_search_query(guild_id, user_text)
    if len(query) < 2:
        return
    task_key = runtime_session_key(session_key=session_key, guild_id=guild_id)
    if task_key is None:
        return
    if channel_id is not None or reply_to_message_id is not None:
        remember_session_followup_target(task_key, channel_id=channel_id, message_id=reply_to_message_id)
    search_key = normalize_search_key(task_key, query)
    for existing_key, existing_task in list(inflight_search_tasks.items()):
        if not existing_key.startswith(f"{task_key}:"):
            continue
        prior_query = existing_key.split(":", 1)[1]
        if existing_key == search_key:
            if existing_task is not None and not existing_task.done():
                return
            continue
        if is_similar(prior_query, clean_text(query).lower()) and existing_task is not None and not existing_task.done():
            existing_task.cancel()
            inflight_search_tasks.pop(existing_key, None)
    existing = background_search_tasks.get(task_key)
    if existing is not None and not existing.done():
        existing.cancel()
    print(f"[SEARCH] scheduled guild={guild_id} session={task_key!r} query={query!r} source={source}")
    search_followup_queued_count += 1
    task = schedule_search_followup_singleflight(
        guild_id,
        query,
        session_key=task_key,
        room_key=room_key,
        person_key=person_key,
        session_memory_key=session_memory_key,
        channel_id=channel_id,
        reply_to_message_id=reply_to_message_id,
        source=source,
        turn_scope=turn_scope,
        runtime_mode=runtime_mode,
    )
    background_search_tasks[task_key] = task
class OmniVoicePCMStream(discord.AudioSource):
    def __init__(
        self,
        *,
        on_first_frame: Callable[[], None] | None = None,
        on_first_packet_sent: Callable[[], None] | None = None,
        trace_payload: dict[str, Any] | None = None,
    ):
        self._queue: queue.Queue[bytes | None] = queue.Queue()
        self._buffer = bytearray()
        self._done = False
        self._closed = False
        self._rate_state = None
        self._input_remainder = b""
        self._first_frame_sent = False
        self._on_first_frame = on_first_frame
        self._on_first_packet_sent = on_first_packet_sent
        self._ready_event = threading.Event()
        self._trace_payload = dict(trace_payload or {})
        self.error: Exception | None = None

    def feed_pcm24_mono(self, chunk: bytes) -> None:
        if self._closed or not chunk:
            return

        pcm = self._input_remainder + chunk
        if len(pcm) % 2 == 1:
            self._input_remainder = pcm[-1:]
            pcm = pcm[:-1]
        else:
            self._input_remainder = b""

        if not pcm:
            return

        upsampled, self._rate_state = audioop.ratecv(
            pcm,
            2,
            OMNIVOICE_PCM_CHANNELS,
            OMNIVOICE_PCM_RATE,
            DISCORD_PCM_RATE,
            self._rate_state,
        )
        stereo = audioop.tostereo(upsampled, 2, 1, 1)
        if stereo:
            self._ready_event.set()
            self._queue.put(stereo)
            log_turn_event(
                "playback_queue_put",
                **merge_log_event_payload(explicit={"bytes": len(stereo)}, extra=self._trace_payload),
            )

    def finish(self) -> None:
        self._done = True
        self._ready_event.set()
        self._queue.put(None)

    def fail(self, err: Exception) -> None:
        self.error = err
        self.finish()

    def has_audio_ready(self) -> bool:
        return bool(self._buffer) or not self._queue.empty() or self._ready_event.is_set()

    def is_exhausted(self) -> bool:
        return self._done and not self._buffer and self._queue.empty()

    async def wait_until_ready(self, timeout: float = 1.0) -> bool:
        return await asyncio.to_thread(self._ready_event.wait, timeout)

    def read(self) -> bytes:
        while len(self._buffer) < DISCORD_FRAME_BYTES:
            try:
                item = self._queue.get(timeout=0.02)
            except queue.Empty:
                if self._done:
                    break
                continue

            if item is None:
                self._done = True
                break

            log_turn_event(
                "playback_queue_get",
                **merge_log_event_payload(explicit={"bytes": len(item)}, extra=self._trace_payload),
            )
            self._buffer.extend(item)

        if len(self._buffer) >= DISCORD_FRAME_BYTES:
            chunk = bytes(self._buffer[:DISCORD_FRAME_BYTES])
            del self._buffer[:DISCORD_FRAME_BYTES]
            if not self._first_frame_sent and any(chunk):
                self._first_frame_sent = True
                if self._on_first_frame is not None:
                    self._on_first_frame()
                if self._on_first_packet_sent is not None:
                    self._on_first_packet_sent()
            return chunk

        if self._done and self._buffer:
            chunk = bytes(self._buffer)
            self._buffer.clear()
            padded = chunk + (b"\x00" * (DISCORD_FRAME_BYTES - len(chunk)))
            if not self._first_frame_sent and any(padded):
                self._first_frame_sent = True
                if self._on_first_frame is not None:
                    self._on_first_frame()
                if self._on_first_packet_sent is not None:
                    self._on_first_packet_sent()
            return padded

        return b""

    def cleanup(self) -> None:
        self._closed = True
        self._done = True
        self._ready_event.set()
        try:
            self._queue.put_nowait(None)
        except Exception:
            pass


class QueuedAudioSource(discord.AudioSource):
    def __init__(self) -> None:
        self._sources: queue.Queue[OmniVoicePCMStream | None] = queue.Queue()
        self._current: OmniVoicePCMStream | None = None
        self._closed = False
        self._done = False
        self.error: Exception | None = None

    def add_source(self, source: OmniVoicePCMStream) -> None:
        if self._closed:
            source.cleanup()
            return
        self._sources.put(source)

    def finish(self) -> None:
        self._done = True
        self._sources.put(None)

    def read(self) -> bytes:
        while True:
            if self._current is None:
                try:
                    next_source = self._sources.get(timeout=0.02)
                except queue.Empty:
                    if self._done:
                        return b""
                    return b"\x00" * DISCORD_FRAME_BYTES
                if next_source is None:
                    self._done = True
                    return b""
                self._current = next_source

            chunk = self._current.read()
            if chunk:
                return chunk

            if self._current.error is not None and self.error is None:
                self.error = self._current.error
            if self._current.is_exhausted():
                self._current.cleanup()
                self._current = None
                continue

            return b"\x00" * DISCORD_FRAME_BYTES

    def cleanup(self) -> None:
        self._closed = True
        if self._current is not None:
            self._current.cleanup()
            self._current = None
        while True:
            try:
                item = self._sources.get_nowait()
            except queue.Empty:
                break
            if item is not None:
                item.cleanup()


async def set_tts_presence(is_warming_up: bool) -> None:
    if bot.user is None:
        return
    try:
        if is_warming_up:
            await bot.change_presence(activity=discord.Game(name="봇 준비중..."))
        else:
            await bot.change_presence(activity=None)
    except Exception as e:
        print("Presence 변경 실패:", repr(e))


def ensure_opus_loaded() -> None:
    if discord_opus.is_loaded():
        print("[OPUS LOAD] already_loaded")
        return
    try:
        discord_opus._load_default()
    except Exception as e:
        raise RuntimeError(f"Opus library load failed: {e!r}") from e
    if not discord_opus.is_loaded():
        raise RuntimeError("Opus library did not report loaded after default load")
    print("[OPUS LOAD] done")


def warmup_stt_sync() -> None:
    print("[STARTUP] stt_warmup_begin")
    silence = np.zeros(TARGET_RATE, dtype=np.float32)
    try:
        _ = transcribe_audio16k_sync(
            silence,
            max_new_tokens=min(32, max(8, WAKE_MAX_TOKENS)),
            sampling_rate=TARGET_RATE,
            stage="warmup",
        )
    except Exception as e:
        raise RuntimeError(f"STT warmup failed: {e!r}") from e
    print("[STARTUP] stt_warmup_done")


async def warmup_llm() -> None:
    session = await get_http_session()
    payload = {
        "model": MODEL_NAME,
        "messages": [{"role": "user", "content": "짧게: 준비됐으면 '응'만 답해."}],
        "temperature": 0.0,
        "max_tokens": min(8, VOICE_LLM_MAX_TOKENS),
        "stream": True,
    }
    print("[STARTUP] llm_warmup_begin")
    async with session.post(LLM_SERVER_URL, json=payload, timeout=aiohttp.ClientTimeout(total=20)) as resp:
        if resp.status != 200:
            error_text = await resp.text()
            raise RuntimeError(f"LLM warmup failed: {resp.status} / {error_text[:300]}")
        async for raw_line in resp.content:
            event = decode_sse_stream_line(raw_line)
            if not event or event.get("done"):
                continue
            if event.get("delta_text"):
                print("[STARTUP] llm_warmup_done")
                return
    print("[STARTUP] llm_warmup_done_no_chunk")


async def warmup_voice_path(*, reason: str, key: str | None = None, include_stt: bool = True, include_llm: bool = True, include_tts: bool = True) -> None:
    lock_key = key or reason
    lock = voice_path_warmup_locks.setdefault(lock_key, asyncio.Lock())
    async with lock:
        if key is not None and voice_path_warmup_done.get(key):
            return
        print(f"[STARTUP] voice_path_warmup_begin reason={reason} key={lock_key}")
        if include_stt:
            await asyncio.to_thread(get_stt_model)
            await asyncio.to_thread(warmup_stt_sync)
        if include_llm:
            await warmup_llm()
        if include_tts:
            await warmup_tts_server()
        voice_path_warmup_done[lock_key] = time.monotonic()
        print(f"[STARTUP] voice_path_warmup_done reason={reason} key={lock_key}")


async def initialize_startup_components() -> None:
    print("[STARTUP] init_begin")
    await set_tts_presence(True)
    try:
        await asyncio.to_thread(ensure_opus_loaded)
        await warmup_voice_path(reason="startup", key="startup")
        print("[STARTUP] init_done")
    finally:
        await set_tts_presence(False)


async def ensure_startup_components_ready() -> None:
    global startup_components_ready, startup_components_task
    if startup_components_ready:
        return
    current = startup_components_task
    if current is None or current.done():
        startup_components_task = asyncio.create_task(initialize_startup_components())
        current = startup_components_task
    await current
    startup_components_ready = True


async def warmup_tts_server() -> None:
    global tts_warmup_started

    tts_warmup_started = True

    session = await get_http_session()
    timeout = aiohttp.ClientTimeout(total=10)
    async with session.get(f"{OMNIVOICE_SERVER_URL}/health", timeout=timeout) as resp:
        if resp.status != 200:
            text = await resp.text()
            raise RuntimeError(f"OmniVoice health check 실패: {resp.status} / {text[:200]}")
        print("OmniVoice 서버 준비 확인 완료")

    payload = {
        "model": OMNIVOICE_MODEL,
        "input": "안녕",
        "voice": OMNIVOICE_VOICE if OMNIVOICE_VOICE else "auto",
        "response_format": "pcm",
        "stream": True,
    }
    if OMNIVOICE_LANGUAGE:
        payload["language"] = OMNIVOICE_LANGUAGE

    async with session.post(
        f"{OMNIVOICE_SERVER_URL}/v1/audio/speech",
        json=payload,
        timeout=aiohttp.ClientTimeout(total=20),
    ) as resp:
        if resp.status != 200:
            text = await resp.text()
            raise RuntimeError(f"OmniVoice warmup 실패: {resp.status} / {text[:200]}")
        async for chunk in resp.content.iter_chunked(4096):
            if chunk:
                print("OmniVoice TTS 워밍업 완료")
                break


def should_log_voice_timing(elapsed_ms: float) -> bool:
    return elapsed_ms >= VOICE_TIMING_LOG_THRESHOLD_MS


def log_voice_latency(metrics: dict | None, key: str, label: str) -> None:
    if not metrics or metrics.get(key):
        return

    started_at = metrics.get("started_at")
    if started_at is None:
        return

    elapsed_ms = (time.monotonic() - float(started_at)) * 1000.0
    metrics[key] = True
    metrics.setdefault("marks", {})[key] = elapsed_ms
    record_turn_stage((metrics.get("meta") or {}).get("turn_id"), key, elapsed_ms)
    alias_map = {
        "llm_first_chunk_logged": ["t_main_first_token"],
        "tts_first_byte_logged": ["t_tts_first_byte", "t_tts_first_audio"],
        "tts_first_frame_logged": ["t_tts_first_frame"],
        "first_packet_sent_logged": ["t_playback_first_packet"],
    }
    aliases = alias_map.get(key, [])
    for alias in aliases:
        metrics.setdefault("marks", {})[alias] = elapsed_ms
        record_turn_stage((metrics.get("meta") or {}).get("turn_id"), alias, elapsed_ms)
    if VOICE_BOTTLENECK_LOGS or should_log_voice_timing(elapsed_ms):
        print(
            "[VOICE LATENCY]\n"
            f"label={label}\n"
            f"elapsed_ms={elapsed_ms:.0f}\n"
            f"metric_key={key}"
        )


def log_voice_stage(metrics: dict | None, label: str, *, extra: str = "", key: str | None = None) -> None:
    if not metrics:
        return
    started_at = metrics.get("started_at")
    if started_at is None:
        return
    elapsed_ms = (time.monotonic() - float(started_at)) * 1000.0
    if key:
        metrics.setdefault("marks", {})[key] = elapsed_ms
        record_turn_stage((metrics.get("meta") or {}).get("turn_id"), key, elapsed_ms)
    stage_alias = {
        "route_ready": "t_policy",
        "memory_ready": "t_context_build",
        "stt_done": "t_stt_done",
        "llm_done": "t_main_done",
    }
    if key and key in stage_alias:
        metrics.setdefault("marks", {})[stage_alias[key]] = elapsed_ms
        record_turn_stage((metrics.get("meta") or {}).get("turn_id"), stage_alias[key], elapsed_ms)
    if not (VOICE_BOTTLENECK_LOGS or should_log_voice_timing(elapsed_ms)):
        return
    lines = [
        "[VOICE STAGE]",
        f"label={label}",
        f"elapsed_ms={elapsed_ms:.0f}",
    ]
    if key:
        lines.append(f"metric_key={key}")
    if extra:
        lines.append(f"extra={extra}")
    print("\n".join(lines))


def log_voice_bottleneck_summary(
    metrics: dict | None,
    *,
    label: str,
    extra: str = "",
    event_name: str = "turn_summary",
) -> None:
    if not metrics:
        return
    started_at = metrics.get("started_at")
    if started_at is None:
        return

    total_ms = (time.monotonic() - float(started_at)) * 1000.0
    marks = metrics.get("marks") or {}

    def _fmt(name: str) -> str:
        value = marks.get(name)
        return f"{float(value):.0f}ms" if value is not None else "-"

    p95_summary = summarize_p95_metrics()
    if VOICE_BOTTLENECK_LOGS or should_log_voice_timing(total_ms):
        lines = [
            "[VOICE BOTTLENECK]",
            f"label={label}",
            f"total_ms={total_ms:.0f}",
            f"route={_fmt('route_ready')}",
            f"cognitive={_fmt('cognitive_hotpath_ms')}",
            f"memory={_fmt('memory_ready')}",
            f"wake_probe_ms={_fmt('wake_done')}",
            f"stt={_fmt('stt_done')}",
            f"llm_first={_fmt('llm_first_chunk_logged')}",
            f"llm_done={_fmt('llm_done')}",
            f"tts_req={_fmt('tts_request_logged')}",
            f"tts_headers={_fmt('tts_response_headers_logged')}",
            f"tts_first={_fmt('tts_first_byte_logged')}",
            f"tts_frame={_fmt('tts_first_frame_logged')}",
            f"playback={_fmt('first_packet_sent_logged')}",
            f"p95_stt={p95_summary['stt_ms_p95']:.0f}ms",
            f"p95_router={p95_summary['router_ms_p95']:.0f}ms",
            f"p95_main_first={p95_summary['main_first_token_ms_p95']:.0f}ms",
            f"p95_tts_first={p95_summary['tts_first_audio_ms_p95']:.0f}ms",
            f"search_q={p95_summary['search_followup_queued_count']}",
            f"cancelled_turns={p95_summary['cancelled_stale_turn_count']}",
        ]
        if extra:
            lines.append(f"extra={extra}")
        print("\n".join(lines))

    meta = metrics.get("meta") or {}
    log_turn_event(
        event_name,
        label=label,
        turn_id=meta.get("turn_id"),
        segment_id=meta.get("segment_id"),
        chunk_index=meta.get("chunk_index"),
        source=meta.get("source"),
        session_key=meta.get("session_key"),
        room_session_key=meta.get("room_session_key"),
        owner_user_id=meta.get("owner_user_id"),
        reply_gate_passed_by=meta.get("reply_gate_passed_by"),
        reply_gate_blocked_by=meta.get("reply_gate_blocked_by"),
        topic_id=meta.get("topic_id"),
        drop_reason=meta.get("drop_reason"),
        t_ingress=marks.get("t_ingress"),
        t_policy=marks.get("t_policy"),
        t_context_build=marks.get("t_context_build"),
        cognitive_hotpath_ms=marks.get("cognitive_hotpath_ms"),
        t_main_first_token=marks.get("t_main_first_token"),
        t_main_done=marks.get("t_main_done"),
        t_tts_first_audio=marks.get("t_tts_first_audio"),
        t_playback_first_packet=marks.get("t_playback_first_packet"),
        t_stt_done=marks.get("t_stt_done"),
        total_ms=round(total_ms, 1),
        stt_ms_p95=p95_summary.get("stt_ms_p95"),
        router_ms_p95=p95_summary.get("router_ms_p95"),
        main_first_token_ms_p95=p95_summary.get("main_first_token_ms_p95"),
        tts_first_audio_ms_p95=p95_summary.get("tts_first_audio_ms_p95"),
        search_followup_queued_count=p95_summary.get("search_followup_queued_count"),
        cancelled_stale_turn_count=p95_summary.get("cancelled_stale_turn_count"),
        extra=extra or None,
    )


async def create_omnivoice_source(
    text: str,
    *,
    on_task_started: Callable[[], None] | None = None,
    on_request_start: Callable[[], None] | None = None,
    on_response_headers: Callable[[], None] | None = None,
    on_first_byte: Callable[[], None] | None = None,
    on_first_frame: Callable[[], None] | None = None,
    on_first_packet_sent: Callable[[], None] | None = None,
    turn_id: str | None = None,
    chunk_index: int | None = None,
    session_key: str | None = None,
    trace_payload: dict[str, Any] | None = None,
) -> OmniVoicePCMStream:
    text = clean_tts_text(text)
    if not text:
        raise ValueError("TTS 텍스트가 비어 있습니다.")

    trace = merge_log_event_payload(
        explicit={
            "turn_id": turn_id,
            "chunk_index": chunk_index,
            "session_key": session_key,
        },
        extra=trace_payload,
    )

    source = OmniVoicePCMStream(
        on_first_frame=on_first_frame,
        on_first_packet_sent=on_first_packet_sent,
        trace_payload=trace,
    )

    async def producer() -> None:
        session = await get_http_session()
        timeout = aiohttp.ClientTimeout(total=OMNIVOICE_TIMEOUT_SEC)
        first_pcm_logged = False

        if on_task_started is not None:
            on_task_started()
        log_turn_event("playback_task_started", **trace)

        async def stream_with_voice(voice_name: str) -> tuple[bool, str]:
            payload = {
                "model": OMNIVOICE_MODEL,
                "input": text,
                "voice": voice_name,
                "response_format": "pcm",
                "stream": OMNIVOICE_STREAM,
            }
            if OMNIVOICE_LANGUAGE:
                payload["language"] = OMNIVOICE_LANGUAGE
            if turn_id:
                payload["turn_id"] = turn_id
            if session_key:
                payload["session_key"] = session_key

            nonlocal first_pcm_logged

            if on_request_start is not None:
                on_request_start()
            log_turn_event(
                "tts_request_started",
                **merge_log_event_payload(explicit={"voice": voice_name}, extra=trace),
            )
            async with session.post(
                f"{OMNIVOICE_SERVER_URL}/v1/audio/speech",
                json=payload,
                timeout=timeout,
            ) as resp:
                if on_response_headers is not None:
                    on_response_headers()
                if resp.status != 200:
                    return False, await resp.text()

                async for chunk in resp.content.iter_chunked(8192):
                    if chunk:
                        if on_first_byte is not None and not first_pcm_logged:
                            on_first_byte()
                        if not first_pcm_logged:
                            first_pcm_logged = True
                            log_turn_event(
                                "tts_first_pcm_received",
                                **merge_log_event_payload(explicit={"bytes": len(chunk)}, extra=trace),
                            )
                        source.feed_pcm24_mono(chunk)
                return True, ""

        try:
            ok, error_text = await stream_with_voice(OMNIVOICE_VOICE)
            if not ok:
                if OMNIVOICE_VOICE.startswith("clone:"):
                    print(f"[TTS FALLBACK] clone voice 실패 -> auto 사용 | voice={OMNIVOICE_VOICE} err={error_text[:200]}")
                    ok, error_text = await stream_with_voice("auto")
                if not ok:
                    raise RuntimeError(f"OmniVoice 서버 오류: {error_text[:300]}")
        except Exception as e:
            source.fail(e)
            return

        source.finish()

    asyncio.create_task(producer())
    return source


# =========================================================
# STT
# =========================================================
def resolve_stt_torch_dtype() -> torch.dtype:
    value = str(STT_COMPUTE_TYPE).strip().lower()
    mapping = {
        "float16": torch.float16,
        "fp16": torch.float16,
        "half": torch.float16,
        "bfloat16": torch.bfloat16,
        "bf16": torch.bfloat16,
        "float32": torch.float32,
        "fp32": torch.float32,
        "float": torch.float32,
    }
    return mapping.get(value, torch.float32)


def normalize_stt_language(language: str | None = None) -> str | None:
    value = str(language if language is not None else STT_LANGUAGE).strip()
    if not value:
        return None

    lowered = value.lower()
    aliases = {
        "korean": "Korean",
        "kor": "Korean",
        "kr": "Korean",
        "ko": "Korean",
        "ko-kr": "Korean",
        "ko_kr": "Korean",
        "english": "English",
        "en": "English",
        "chinese": "Chinese",
        "zh": "Chinese",
        "japanese": "Japanese",
        "ja": "Japanese",
    }
    return aliases.get(lowered, value)


def get_stt_model() -> tuple[str, Any, Any]:
    global stt_processor, stt_model, stt_backend

    if stt_backend == "qwen_asr" and stt_model is not None:
        return stt_backend, stt_processor, stt_model

    if Qwen3ASRModel is None:
        raise RuntimeError("qwen-asr 패키지가 설치되지 않았습니다. `pip install -r requirements.txt` 후 다시 실행하세요.")

    device = "cuda:0" if torch.cuda.is_available() else "cpu"
    token = os.getenv("HF_TOKEN")
    torch_dtype = resolve_stt_torch_dtype()

    print(f"[STT LOAD] start model={STT_MODEL_NAME} device={device} dtype={torch_dtype}")

    stt_backend = "qwen_asr"
    stt_processor = None
    load_kwargs: dict[str, Any] = {
        "dtype": torch_dtype,
        "device_map": device,
        "max_inference_batch_size": 1,
        "max_new_tokens": max(VOICE_STT_MAX_NEW_TOKENS, 256),
    }
    if token:
        load_kwargs["token"] = token

    stt_model = Qwen3ASRModel.from_pretrained(
        STT_MODEL_NAME,
        **load_kwargs,
    )
    print("[STT LOAD] done backend=Qwen3-ASR")
    return stt_backend, stt_processor, stt_model


def transcribe_audio16k_sync(audio16k: np.ndarray, max_new_tokens: int = 256, *, sampling_rate: int = TARGET_RATE, stage: str = "full") -> str:
    if audio16k.size == 0:
        return ""

    effective_rate = max(1, int(sampling_rate))
    print(f"[STT INPUT][{stage}] sampling_rate={effective_rate} samples={audio16k.size} sec={audio16k.size / float(effective_rate):.2f}")
    backend, _processor, model = get_stt_model()
    stt_audio = np.asarray(audio16k, dtype=np.float32)

    if effective_rate != TARGET_RATE:
        stt_audio = resample_audio_float(stt_audio, effective_rate, TARGET_RATE)
        effective_rate = TARGET_RATE
        print(f"[STT RESAMPLE][{stage}] {sampling_rate} -> {TARGET_RATE} samples={stt_audio.size}")

    language = normalize_stt_language() if STT_FORCE_LANGUAGE else None
    results = model.transcribe(
        audio=(stt_audio, effective_rate),
        language=language,
        return_time_stamps=False,
    )
    if not results:
        print(f"[STT DONE][{stage}] empty_result")
        return ""

    text = clean_text(getattr(results[0], "text", "") or "")
    print(f"[STT DONE][{stage}] text={text!r}")
    return text


def build_partial_stt_window(audio16k: np.ndarray, *, sampling_rate: int = TARGET_RATE) -> np.ndarray:
    if audio16k.size == 0:
        return audio16k
    rate = max(1, int(sampling_rate))
    max_samples = int(rate * 1.2)
    overlap_samples = int(rate * 0.3)
    if audio16k.size <= max_samples:
        return np.asarray(audio16k, dtype=np.float32)
    start = max(0, audio16k.size - max_samples)
    if start > overlap_samples:
        start -= overlap_samples
    return np.asarray(audio16k[start:], dtype=np.float32)


def longest_common_prefix_text(a: str, b: str) -> str:
    left = clean_text(a)
    right = clean_text(b)
    limit = min(len(left), len(right))
    idx = 0
    while idx < limit and left[idx] == right[idx]:
        idx += 1
    return left[:idx]


def commit_stable_transcript(session_key: str | None, *, new_partial_text: str) -> str:
    if not session_key:
        return clean_text(new_partial_text)
    prev_partial = clean_text(session_partial_stt_text.get(session_key, ""))
    committed = clean_text(session_committed_stt_text.get(session_key, ""))
    current_partial = clean_text(new_partial_text)
    session_partial_stt_text[session_key] = current_partial
    if not current_partial:
        return committed
    stable = longest_common_prefix_text(prev_partial, current_partial) if prev_partial else current_partial
    safe = stable[:-3].strip() if len(stable) > 3 else ""
    if not safe and current_partial == prev_partial:
        safe = current_partial
    if safe and len(safe) > len(committed):
        committed = clean_text(safe)
        session_committed_stt_text[session_key] = committed
    elif not committed:
        session_committed_stt_text[session_key] = committed
    return committed


def get_partial_transcript(session_key: str | None, audio16k: np.ndarray, *, sampling_rate: int = TARGET_RATE) -> tuple[str, str]:
    partial_audio = build_partial_stt_window(audio16k, sampling_rate=sampling_rate)
    partial_samples = int(partial_audio.size)
    min_partial_samples = max(1, int(float(sampling_rate) * 0.85))
    if partial_samples < min_partial_samples:
        committed_text = clean_text(session_committed_stt_text.get(session_key or "", ""))
        return "", committed_text

    audio_hash = hashlib.sha1(np.asarray(partial_audio, dtype=np.float32).tobytes()).hexdigest()
    cache_key = session_key or "__global__"
    cached = partial_stt_cache.get(cache_key)
    if cached and cached.get("hash") == audio_hash:
        partial_text = clean_text(cached.get("partial_text", ""))
        committed_text = commit_stable_transcript(session_key, new_partial_text=partial_text)
        return partial_text, committed_text

    partial_text = transcribe_audio16k_sync(
        partial_audio,
        max_new_tokens=max(64, min(VOICE_STT_MAX_NEW_TOKENS, 128)),
        sampling_rate=sampling_rate,
        stage="partial",
    )
    partial_stt_cache[cache_key] = {
        "hash": audio_hash,
        "partial_text": partial_text,
        "samples": partial_samples,
        "updated_at": time.monotonic(),
    }
    committed_text = commit_stable_transcript(session_key, new_partial_text=partial_text)
    return partial_text, committed_text


def score_stt_candidate(text: str, *, wake_probe: str = "") -> float:
    text = clean_text(text)
    if not text:
        return -100.0

    normalized = normalize_voice_text(text)
    if not normalized:
        return -80.0

    compact = normalized.replace(" ", "")
    token_count = len([t for t in normalized.split() if t])
    hangul_alnum_count = len(re.findall(r"[가-힣A-Za-z0-9]", text))
    unique_chars = len(set(compact))
    unique_ratio = unique_chars / max(1, len(compact))

    score = 0.0
    score += min(24.0, len(compact) * 0.75)
    score += min(6.0, token_count * 0.6)
    score += min(8.0, hangul_alnum_count * 0.15)
    score += unique_ratio * 2.0

    if contains_wake_word(text):
        score += 10.0
    if wake_probe:
        wake_probe_n = normalize_voice_text(wake_probe)
        if wake_probe_n and wake_probe_n in normalized:
            score += 2.0

    if looks_like_brief_filler_text(text):
        score -= 14.0
    if looks_like_repetitive_noise_text(text):
        score -= 16.0
    if re.search(r"(.)\1{3,}", compact):
        score -= 6.0
    if len(compact) <= 2:
        score -= 4.0

    return score


def choose_full_stt_candidate(primary_text: str, rescore_text: str, *, wake_probe: str = "") -> tuple[str, dict]:
    primary = clean_text(primary_text)
    rescore = clean_text(rescore_text)
    primary_score = score_stt_candidate(primary, wake_probe=wake_probe)
    rescore_score = score_stt_candidate(rescore, wake_probe=wake_probe)

    choice = "primary"
    chosen_text = primary

    if not primary and rescore:
        choice = "rescore"
        chosen_text = rescore
    elif rescore and not is_similar(primary, rescore):
        if rescore_score >= primary_score + 1.5:
            choice = "rescore"
            chosen_text = rescore
        elif contains_wake_word(rescore) and not contains_wake_word(primary) and rescore_score >= primary_score:
            choice = "rescore"
            chosen_text = rescore
        elif len(normalize_voice_text(rescore).replace(" ", "")) >= len(normalize_voice_text(primary).replace(" ", "")) + 3 and rescore_score > primary_score:
            choice = "rescore"
            chosen_text = rescore

    return chosen_text, {
        "enabled": True,
        "primary_text": primary,
        "primary_score": round(primary_score, 3),
        "rescore_text": rescore,
        "rescore_score": round(rescore_score, 3),
        "selected": choice,
    }


def detect_wake_word_sync(audio: np.ndarray, *, sampling_rate: int = TARGET_RATE) -> dict[str, str | bool | None]:
    wake_audio = slice_audio_window(audio, WAKE_AUDIO_SEC, sampling_rate=sampling_rate)
    wake_raw_text = transcribe_audio16k_sync(
        wake_audio,
        max_new_tokens=WAKE_MAX_TOKENS,
        sampling_rate=sampling_rate,
        stage="wake",
    )
    wake_text = apply_stt_post_corrections(wake_raw_text, wake_detected=False)

    probe_text = strip_leading_voice_fillers(wake_text)
    probe_alias = extract_leading_wake_alias(probe_text)
    probe_fuzzy_alias = fuzzy_leading_wake_alias(probe_text) if probe_alias is None else None
    confirm_text = ""

    if probe_alias is None and looks_like_gibberish_probe(probe_text):
        return {
            "wake_detected": False,
            "wake_probe_text": wake_text,
            "wake_confirm_text": "",
            "wake_match_mode": "rejected",
            "wake_alias": None,
            "wake_reject_reason": "gibberish_probe",
        }

    if probe_alias is None and probe_fuzzy_alias is None:
        return {
            "wake_detected": False,
            "wake_probe_text": wake_text,
            "wake_confirm_text": "",
            "wake_match_mode": "rejected",
            "wake_alias": None,
            "wake_reject_reason": "probe_miss",
        }

    confirm_audio = slice_audio_window(audio, WAKE_CONFIRM_AUDIO_SEC, sampling_rate=sampling_rate)
    confirm_raw_text = transcribe_audio16k_sync(
        confirm_audio,
        max_new_tokens=WAKE_CONFIRM_MAX_TOKENS,
        sampling_rate=sampling_rate,
        stage="wake-confirm",
    )
    confirm_text = apply_stt_post_corrections(confirm_raw_text, wake_detected=False)
    confirm_probe = strip_leading_voice_fillers(confirm_text)
    confirm_alias = extract_leading_wake_alias(confirm_probe)

    if probe_alias is not None and confirm_alias == probe_alias:
        return {
            "wake_detected": True,
            "wake_probe_text": wake_text,
            "wake_confirm_text": confirm_text,
            "wake_match_mode": "exact",
            "wake_alias": probe_alias,
            "wake_reject_reason": None,
        }

    confirm_fuzzy_alias = fuzzy_leading_wake_alias(confirm_probe) if confirm_alias is None else None
    if probe_alias is None and probe_fuzzy_alias is not None and confirm_fuzzy_alias == probe_fuzzy_alias:
        return {
            "wake_detected": True,
            "wake_probe_text": wake_text,
            "wake_confirm_text": confirm_text,
            "wake_match_mode": "fuzzy",
            "wake_alias": probe_fuzzy_alias,
            "wake_reject_reason": None,
        }

    return {
        "wake_detected": False,
        "wake_probe_text": wake_text,
        "wake_confirm_text": confirm_text,
        "wake_match_mode": "rejected",
        "wake_alias": probe_alias or probe_fuzzy_alias,
        "wake_reject_reason": "confirm_miss",
    }


# =========================================================
# 디스코드 음성
# =========================================================
async def _wait_for_internal_voice_reconnect(target_channel: discord.VoiceChannel) -> EvelynVoiceClient | None:
    existing_vc = target_channel.guild.voice_client
    if not isinstance(existing_vc, EvelynVoiceClient):
        return None
    if not existing_vc.is_internal_voice_reconnect_active():
        return None

    resumed = await existing_vc.wait_for_internal_voice_reconnect(timeout=max(VOICE_CONNECT_TIMEOUT, 5.0))
    if resumed and existing_vc.channel == target_channel:
        return existing_vc
    refreshed_vc = target_channel.guild.voice_client
    if isinstance(refreshed_vc, EvelynVoiceClient) and refreshed_vc.is_connected() and refreshed_vc.channel == target_channel:
        return refreshed_vc
    return None


async def connect_evelyn_voice_client(target_channel: discord.VoiceChannel) -> EvelynVoiceClient:
    guild_id = target_channel.guild.id
    lock = voice_connect_locks.setdefault(guild_id, asyncio.Lock())

    async with lock:
        reused_vc = await _wait_for_internal_voice_reconnect(target_channel)
        if reused_vc is not None:
            return reused_vc

        last_error: Exception | None = None

        for attempt in range(1, VOICE_CONNECT_RETRIES + 1):
            try:
                print(
                    f"[VOICE CONNECT] attempt={attempt}/{VOICE_CONNECT_RETRIES} channel={target_channel.name} timeout={VOICE_CONNECT_TIMEOUT}"
                )
                vc = await target_channel.connect(
                    cls=EvelynVoiceClient,
                    timeout=VOICE_CONNECT_TIMEOUT,
                    reconnect=False,
                )
                if not isinstance(vc, EvelynVoiceClient):
                    raise RuntimeError(f"unexpected voice client type: {type(vc)!r}")
                vc.on_user_audio = process_member_audio
                if not vc.is_listener_healthy():
                    vc.listen()
                    print(f"[VOICE CONNECT ARM] guild={guild_id} channel={target_channel.name}")
                return vc
            except Exception as e:
                last_error = e
                print(
                    f"[VOICE CONNECT FAIL] attempt={attempt}/{VOICE_CONNECT_RETRIES} channel={target_channel.name} err={e!r}"
                )

                reused_vc = await _wait_for_internal_voice_reconnect(target_channel)
                if reused_vc is not None:
                    return reused_vc

                stale_vc = target_channel.guild.voice_client
                if stale_vc is not None:
                    try:
                        await stale_vc.disconnect(force=True)
                    except Exception:
                        pass

                try:
                    await target_channel.guild.change_voice_state(channel=None, self_deaf=False, self_mute=False)
                except Exception:
                    pass

                if attempt < VOICE_CONNECT_RETRIES:
                    await asyncio.sleep(VOICE_CONNECT_RETRY_DELAY_SEC)

        assert last_error is not None
        raise last_error


async def ensure_listening_voice_client(guild: discord.Guild, target_channel: discord.VoiceChannel) -> Optional[EvelynVoiceClient]:
    await ensure_startup_components_ready()
    vc = guild.voice_client

    if vc is not None and not isinstance(vc, EvelynVoiceClient):
        await vc.disconnect(force=True)
        vc = None

    if vc is None:
        vc = await connect_evelyn_voice_client(target_channel)
    elif isinstance(vc, EvelynVoiceClient) and vc.is_internal_voice_reconnect_active():
        waited_vc = await _wait_for_internal_voice_reconnect(target_channel)
        if waited_vc is not None:
            vc = waited_vc
    elif vc.channel != target_channel:
        await vc.move_to(target_channel)

    if isinstance(vc, EvelynVoiceClient):
        vc.on_user_audio = process_member_audio
        if not vc.is_listener_healthy():
            try:
                vc.stop_listening()
            except Exception:
                pass
            vc.listen()
            print(f"[VOICE LISTEN REARM] guild={guild.id} channel={target_channel.name}")
        warmup_key = f"voice:{guild.id}:{getattr(target_channel, 'id', 'unknown')}"
        try:
            await warmup_voice_path(reason="voice_connect", key=warmup_key)
        except Exception as e:
            print(f"[VOICE PATH WARMUP FAIL] guild={guild.id} channel={getattr(target_channel, 'name', None)} err={e!r}")
        return vc

    return None


async def ensure_voice_client(message: discord.Message) -> Optional[EvelynVoiceClient]:
    if not message.guild:
        return None

    voice_state = getattr(message.author, "voice", None)
    if not voice_state or not voice_state.channel:
        return None

    vc = await ensure_listening_voice_client(message.guild, voice_state.channel)
    return vc


async def stop_active_tts_playback(guild_id: int | None, *, reason: str = "interrupt") -> None:
    if guild_id is None:
        return
    state = active_tts_playbacks.get(guild_id)
    if not state:
        return

    vc = state.get("vc")
    playback_task = state.get("playback_task")
    prefetch_task = state.get("prefetch_task")
    sentence_queue = state.get("sentence_queue")
    prepared_queue = state.get("prepared_queue")
    playback_source = state.get("playback_source")

    if sentence_queue is not None:
        with contextlib.suppress(Exception):
            await sentence_queue.put(None)
    if prepared_queue is not None:
        with contextlib.suppress(Exception):
            await prepared_queue.put(None)
    if playback_source is not None:
        with contextlib.suppress(Exception):
            playback_source.finish()
    if vc is not None and (vc.is_playing() or vc.is_paused()):
        with contextlib.suppress(Exception):
            vc.stop()
    if playback_task is not None and not playback_task.done():
        playback_task.cancel()
        with contextlib.suppress(asyncio.CancelledError, Exception):
            await playback_task
    if prefetch_task is not None and not prefetch_task.done():
        prefetch_task.cancel()
        with contextlib.suppress(asyncio.CancelledError, Exception):
            await prefetch_task
    active_tts_playbacks.pop(guild_id, None)
    bot_speaking_guilds.discard(guild_id)
    last_bot_audio_end_at[guild_id] = time.monotonic()
    log_turn_event("tts_interrupt", guild_id=guild_id, reason=reason)


async def wait_until_not_playing(vc: discord.VoiceClient) -> None:
    while vc.is_playing() or vc.is_paused():
        await asyncio.sleep(0.05)


async def play_audio_source(
    vc: discord.VoiceClient,
    source: discord.AudioSource,
    *,
    on_play_start: Callable[[], None] | None = None,
    trace_payload: dict[str, Any] | None = None,
) -> None:
    await wait_until_not_playing(vc)

    payload = dict(trace_payload or {})
    payload.setdefault("source_type", type(source).__name__)
    done = asyncio.Event()
    playback_error: list[Exception | None] = [None]

    def after_play(err):
        if err:
            playback_error[0] = err
        bot.loop.call_soon_threadsafe(done.set)

    try:
        if on_play_start is not None:
            on_play_start()
        log_turn_event("discord_playback_play_invoked", **payload)
        vc.play(source, after=after_play)
        await done.wait()
    except Exception as exc:
        log_turn_event(
            "discord_playback_exception",
            **merge_log_event_payload(explicit={"stage": "vc_play", "error": repr(exc)}, extra=payload),
        )
        raise

    if playback_error[0] is not None:
        log_turn_event(
            "discord_playback_exception",
            **merge_log_event_payload(explicit={"stage": "after_play", "error": repr(playback_error[0])}, extra=payload),
        )
        raise playback_error[0]

    if isinstance(source, (OmniVoicePCMStream, QueuedAudioSource)) and source.error is not None:
        log_turn_event(
            "discord_playback_exception",
            **merge_log_event_payload(explicit={"stage": "source_error", "error": repr(source.error)}, extra=payload),
        )
        raise source.error

    log_turn_event("discord_playback_finished", **payload)


async def speak_answer(
    vc: discord.VoiceClient,
    answer: str,
    *,
    turn_id: str | None = None,
    session_key: str | None = None,
) -> None:
    guild_id = getattr(getattr(vc, "guild", None), "id", None)

    async with tts_lock:
        source = await create_omnivoice_source(
            answer,
            turn_id=turn_id,
            chunk_index=1,
            session_key=session_key,
            trace_payload={"source_type": "OmniVoicePCMStream"},
            on_first_packet_sent=lambda: log_turn_event(
                "first_packet_sent",
                turn_id=turn_id,
                chunk_index=1,
                session_key=session_key,
            ),
        )
        try:
            if guild_id is not None:
                bot_speaking_guilds.add(guild_id)
            await play_audio_source(
                vc,
                source,
                trace_payload={
                    "turn_id": turn_id,
                    "chunk_index": 1,
                    "session_key": session_key,
                    "source_type": type(source).__name__,
                },
            )
        finally:
            if guild_id is not None:
                bot_speaking_guilds.discard(guild_id)
                last_bot_audio_end_at[guild_id] = time.monotonic()


async def _prefetch_tts_sources(
    sentence_queue: "asyncio.Queue[str | None]",
    prepared_queue: "asyncio.Queue[object]",
    *,
    metrics: dict | None = None,
    turn_id: str | None = None,
    session_key: str | None = None,
    turn_scope: TurnScope | None = None,
) -> None:
    chunk_index = 0
    task = _attach_current_task(turn_scope)

    try:
        while True:
            if turn_scope is not None:
                turn_scope.raise_if_cancelled()
            sentence = await sentence_queue.get()
            if sentence is None:
                await prepared_queue.put(None)
                return

            sentence = clean_tts_text(sentence)
            if not sentence:
                continue

            if turn_scope is not None:
                turn_scope.raise_if_cancelled()
            chunk_index += 1
            source = await create_omnivoice_source(
                sentence,
                turn_id=turn_id,
                chunk_index=chunk_index,
                session_key=session_key,
                trace_payload={"source_type": "OmniVoicePCMStream"},
                on_request_start=lambda: (
                    mark_turn_stage(metrics, "tts_request_start", event_name="tts_request_start", chunk_index=chunk_index),
                    log_voice_latency(metrics, "tts_request_logged", "TTS 요청 시작 시간")
                ),
                on_response_headers=lambda: log_voice_latency(metrics, "tts_response_headers_logged", "TTS 응답 헤더 도착 시간"),
                on_first_byte=lambda: (
                    mark_turn_stage(metrics, "tts_first_byte", event_name="tts_first_byte", chunk_index=chunk_index),
                    log_voice_latency(metrics, "tts_first_byte_logged", "TTS 첫 바이트 도착 시간")
                ),
                on_first_frame=lambda: log_voice_latency(metrics, "tts_first_frame_logged", "TTS 첫 프레임 공급 시간"),
                on_first_packet_sent=lambda ci=chunk_index: (
                    log_voice_latency(metrics, "first_packet_sent_logged", "첫 패킷 송신 시간"),
                    log_turn_event(
                        "first_packet_sent",
                        turn_id=turn_id,
                        chunk_index=ci,
                        session_key=session_key,
                    )
                ),
            )
            await source.wait_until_ready(timeout=max(0.2, OMNIVOICE_TIMEOUT_SEC))
            await prepared_queue.put((chunk_index, source))
    except Exception as exc:
        await prepared_queue.put(exc)
    finally:
        _detach_task(turn_scope, task)


async def stream_tts_sentences(
    vc: discord.VoiceClient,
    sentence_queue: "asyncio.Queue[str | None]",
    *,
    metrics: dict | None = None,
    turn_id: str | None = None,
    session_key: str | None = None,
    turn_scope: TurnScope | None = None,
) -> None:
    guild_id = getattr(getattr(vc, "guild", None), "id", None)
    did_speak = False
    task = _attach_current_task(turn_scope)

    try:
        async with tts_lock:
            prepared_queue: asyncio.Queue[object] = asyncio.Queue(maxsize=max(1, TTS_PREFETCH_CHUNKS))
            playback_source = QueuedAudioSource()
            prefetch_task = create_turn_scoped_task(
                _prefetch_tts_sources(
                    sentence_queue,
                    prepared_queue,
                    metrics=metrics,
                    turn_id=turn_id,
                    session_key=session_key,
                    turn_scope=turn_scope,
                ),
                turn_scope=turn_scope,
            )
            playback_task: asyncio.Task | None = None
            if guild_id is not None:
                active_tts_playbacks[guild_id] = {
                    "vc": vc,
                    "sentence_queue": sentence_queue,
                    "prepared_queue": prepared_queue,
                    "playback_source": playback_source,
                    "prefetch_task": prefetch_task,
                    "playback_task": playback_task,
                    "turn_id": turn_id,
                    "session_key": session_key,
                }
            try:
                while True:
                    if turn_scope is not None:
                        turn_scope.raise_if_cancelled()
                    item = await prepared_queue.get()
                    print(f"[TTS PLAYBACK] prepared_item type={type(item).__name__} guild_id={guild_id}")
                    if item is None:
                        print("[TTS PLAYBACK] received_sentinel")
                        playback_source.finish()
                        break
                    if isinstance(item, Exception):
                        print(f"[TTS PLAYBACK] prepared_exception err={item!r}")
                        playback_source.finish()
                        raise item

                    _, source = item
                    playback_source.add_source(source)
                    print(f"[TTS PLAYBACK] source_added playback_started={playback_task is not None}")

                    if guild_id is not None and not did_speak:
                        bot_speaking_guilds.add(guild_id)

                    if playback_task is None:
                        did_speak = True
                        print("[TTS PLAYBACK] starting_discord_playback")
                        playback_task = create_turn_scoped_task(
                            play_audio_source(
                                vc,
                                playback_source,
                                trace_payload={
                                    "turn_id": turn_id,
                                    "session_key": session_key,
                                    "source_type": type(playback_source).__name__,
                                },
                            ),
                            turn_scope=turn_scope,
                        )
                        if guild_id is not None and guild_id in active_tts_playbacks:
                            active_tts_playbacks[guild_id]["playback_task"] = playback_task

                if playback_task is not None:
                    await playback_task
            finally:
                playback_source.finish()
                if playback_task is not None and not playback_task.done():
                    playback_task.cancel()
                    try:
                        await playback_task
                    except asyncio.CancelledError:
                        pass
                if not prefetch_task.done():
                    prefetch_task.cancel()
                    try:
                        await prefetch_task
                    except asyncio.CancelledError:
                        pass
                if guild_id is not None:
                    active_tts_playbacks.pop(guild_id, None)
                    bot_speaking_guilds.discard(guild_id)
                    if did_speak:
                        last_bot_audio_end_at[guild_id] = time.monotonic()
    finally:
        _detach_task(turn_scope, task)


# =========================================================
# LLM
# =========================================================
def fallback_answer_for(user_text: str) -> str:
    user_text = clean_text(user_text)
    if not user_text:
        return "응, 듣고 있어."
    return "응, 잠깐만."


def split_first_response_and_followup(answer: str) -> tuple[str, str]:
    cleaned = clean_text(answer)
    if not cleaned:
        return "", ""
    sentences, _tail = split_tts_sentences(cleaned, force=True)
    sentences = [clean_tts_text(sentence) for sentence in sentences if clean_tts_text(sentence)]
    if not sentences:
        return cleaned, ""
    first = sentences[0]
    followup = clean_text(" ".join(sentences[1:])) if len(sentences) > 1 else ""
    return first, followup


def normalize_compare_text(text: str) -> str:
    cleaned = clean_text(text).lower()
    return "".join(ch for ch in cleaned if ch.isalnum() or ch.isspace()).strip()


def is_duplicate_followup(first_response: str, followup_text: str) -> bool:
    first_norm = normalize_compare_text(first_response)
    follow_norm = normalize_compare_text(followup_text)
    if not first_norm or not follow_norm:
        return False
    if follow_norm == first_norm:
        return True
    if follow_norm.startswith(first_norm):
        remainder = follow_norm[len(first_norm):].strip()
        return len(remainder) <= 8
    return False


async def build_first_response(
    user_text: str,
    *,
    guild_id: int | None = None,
    session_key: str | None = None,
    room_key: str | None = None,
    person_key: str | None = None,
    session_memory_key: str | None = None,
    source: str = "text",
    debug_text: str | None = None,
    metrics: dict | None = None,
) -> tuple[AnswerPayload, str, dict | None]:
    log_voice_stage(metrics, "1단계 first response 생성 시작", extra=f"source={source} user_text_len={len(clean_text(user_text))}")
    messages, cognitive_state, route_decision, gated_state, _awaiting_user_reply = await prepare_route_context(
        user_text,
        guild_id=guild_id,
        session_key=session_key,
        room_key=room_key,
        person_key=person_key,
        session_memory_key=session_memory_key,
        source=source,
        debug_text=debug_text,
        metrics=metrics,
    )
    if route_decision.user_visible_preface:
        return build_answer_payload_from_text(route_decision.user_visible_preface), "", gated_state

    guided_user_text = user_text
    if gated_state and gated_state.get("action") == "ask" and gated_state.get("question_for_user"):
        guided_user_text = clean_text(str(gated_state.get("question_for_user", "")))
    live_minecraft_state = await observe_live_minecraft_state(guild_id)
    final_user_text = f"{guided_user_text}\n\n{build_main_response_guidance(cognitive_state, source=source, minecraft_state=live_minecraft_state)}"

    payload = {
        "model": MODEL_NAME,
        "messages": messages + [{"role": "user", "content": final_user_text}],
        "temperature": 0.0,
        "max_tokens": min(40, VOICE_LLM_MAX_TOKENS),
        "stream": False,
    }

    timeout = aiohttp.ClientTimeout(total=120)
    session = await get_http_session()

    async with session.post(LLM_SERVER_URL, json=payload, timeout=timeout) as resp:
        if resp.status != 200:
            error_text = await resp.text()
            raise RuntimeError(f"LLM 서버 오류: {resp.status} / {error_text[:300]}")

        data = await resp.json()
        choices = data.get("choices", [])
        if not choices:
            answer = fallback_answer_for(user_text)
            return build_answer_payload_from_text(answer), "", gated_state

        choice = choices[0]
        msg = choice.get("message", {})
        raw_answer = msg.get("content", "")
        _response_action, answer = parse_response_action_tag(sanitize_model_output(raw_answer))
        reasoning = msg.get("reasoning_content", "")
        finish_reason = choice.get("finish_reason", "")

        if not answer:
            answer = extract_answer_from_reasoning(reasoning, user_text)
        if not answer:
            print(f"LLM 1단계 응답 본문이 비어 있어서 fallback 사용, finish_reason={finish_reason}")
            answer = fallback_answer_for(user_text)

        first_response, followup_seed = split_first_response_and_followup(answer)
        return build_answer_payload_from_text(first_response or answer), followup_seed, gated_state


async def build_followup_response(
    user_text: str,
    first_response: str,
    *,
    guild_id: int | None = None,
    session_key: str | None = None,
    room_key: str | None = None,
    person_key: str | None = None,
    session_memory_key: str | None = None,
    source: str = "text",
    debug_text: str | None = None,
    metrics: dict | None = None,
) -> AnswerPayload:
    log_voice_stage(metrics, "2단계 followup 생성 시작", extra=f"source={source} first_len={len(clean_text(first_response))}")
    messages, cognitive_state, _route = await prepare_llm_messages(
        user_text,
        guild_id=guild_id,
        session_key=session_key,
        room_key=room_key,
        person_key=person_key,
        session_memory_key=session_memory_key,
        source=source,
        debug_text=debug_text,
        metrics=metrics,
    )
    live_minecraft_state = await observe_live_minecraft_state(guild_id)
    minecraft_summary = format_minecraft_state_summary(live_minecraft_state)
    followup_prompt = (
        f"사용자가 방금 한 말: {clean_text(user_text)}\n"
        f"이미 먼저 말한 첫 응답: {clean_text(first_response)}\n"
        + (f"현재 마인크래프트 상태: {minecraft_summary}\n" if minecraft_summary else "")
        + "\n"
        + "할 일: 첫 응답과 겹치지 않는 보충 설명만 1~2문장으로 이어서 말해. "
        + "첫 문장을 반복하거나 비슷하게 다시 시작하지 마. 새 정보가 없으면 빈 응답을 반환해."
    )
    payload = {
        "model": MODEL_NAME,
        "messages": messages + [{"role": "user", "content": followup_prompt}],
        "temperature": 0.0,
        "max_tokens": min(64, VOICE_LLM_MAX_TOKENS),
        "stream": False,
    }
    timeout = aiohttp.ClientTimeout(total=120)
    session = await get_http_session()
    async with session.post(LLM_SERVER_URL, json=payload, timeout=timeout) as resp:
        if resp.status != 200:
            error_text = await resp.text()
            raise RuntimeError(f"LLM 서버 오류: {resp.status} / {error_text[:300]}")
        data = await resp.json()
        choices = data.get("choices", [])
        if not choices:
            return build_answer_payload_from_text("")
        msg = choices[0].get("message", {})
        raw_answer = msg.get("content", "")
        _response_action, answer = parse_response_action_tag(sanitize_model_output(raw_answer))
    first, followup = split_first_response_and_followup(answer)
    if clean_text(first) == clean_text(first_response):
        return build_answer_payload_from_text(followup)
    cleaned_answer = clean_text(answer)
    if is_duplicate_followup(first_response, cleaned_answer):
        return build_answer_payload_from_text("")
    return build_answer_payload_from_text(cleaned_answer)


async def execute_main_llm_once(
    *,
    payload: dict[str, Any],
    user_text: str,
) -> tuple[str, str]:
    timeout = aiohttp.ClientTimeout(total=120)
    session = await get_http_session()
    async with session.post(LLM_SERVER_URL, json=payload, timeout=timeout) as resp:
        if resp.status != 200:
            error_text = await resp.text()
            raise RuntimeError(f"LLM 서버 오류: {resp.status} / {error_text[:300]}")
        data = await resp.json()
    choices = data.get("choices", [])
    if not choices:
        return fallback_answer_for(user_text), "fallback_empty_choices"
    answer, answer_source, finish_reason = extract_main_llm_answer_from_choice(
        choices[0],
        user_text,
        sanitize_output=sanitize_model_output,
        parse_response_action_tag=parse_response_action_tag,
        extract_answer_from_reasoning=extract_answer_from_reasoning,
    )
    if answer:
        return answer, answer_source
    print(f"LLM 응답 본문이 비어 있어서 fallback 사용, finish_reason={finish_reason}")
    return fallback_answer_for(user_text), "fallback_empty_body"


async def ask_llm_once(
    user_text: str,
    guild_id: int | None = None,
    *,
    session_key: str | None = None,
    room_key: str | None = None,
    person_key: str | None = None,
    session_memory_key: str | None = None,
    source: str = "text",
    debug_text: str | None = None,
    metrics: dict | None = None,
) -> str:
    log_voice_stage(metrics, "LLM 단발 요청 시작", extra=f"source={source} user_text_len={len(clean_text(user_text))}")
    messages, cognitive_state, route_decision, gated_state, awaiting_user_reply = await prepare_route_context(
        user_text,
        guild_id=guild_id,
        session_key=session_key,
        room_key=room_key,
        person_key=person_key,
        session_memory_key=session_memory_key,
        source=source,
        debug_text=debug_text,
        metrics=metrics,
    )
    skill_route_answer = await maybe_execute_registered_route(
        route_decision=route_decision,
        user_text=user_text,
        source=source,
        guild_id=guild_id,
        session_key=session_key,
        room_key=room_key,
        person_key=person_key,
        session_memory_key=session_memory_key,
        debug_text=debug_text,
        metrics=metrics,
        cognitive_state=cognitive_state,
        messages=messages,
        allow_internal_routes={"main_direct", "policy_short_circuit", "search_executor"},
    )
    if skill_route_answer:
        if session_key is not None:
            update_session_state(
                session_key,
                speaker="assistant",
                awaiting_user_reply=awaiting_user_reply,
                answer_text=skill_route_answer,
                user_text=user_text,
            )
        log_voice_stage(metrics, "LLM 단발 요청 성공", extra=f"skill_route={route_decision.route} answer_len={len(skill_route_answer)}")
        return build_answer_payload_from_text(skill_route_answer).display_text

    if route_decision.user_visible_preface:
        if session_key is not None:
            update_session_state(
                session_key,
                speaker="assistant",
                awaiting_user_reply=awaiting_user_reply,
                answer_text=route_decision.user_visible_preface,
                user_text=user_text,
            )
        log_voice_stage(metrics, "LLM 단발 요청 성공", extra=f"policy_len={len(route_decision.user_visible_preface)}")
        return build_answer_payload_from_text(route_decision.user_visible_preface).display_text

    guided_user_text = route_decision.prompt_text or user_text
    live_minecraft_state = await observe_live_minecraft_state(guild_id)
    final_user_text = f"{guided_user_text}\n\n{build_main_response_guidance(cognitive_state, source=source, minecraft_state=live_minecraft_state)}"
    payload = build_main_llm_payload(
        model_name=MODEL_NAME,
        messages=messages,
        final_user_text=final_user_text,
        source=source,
        stream=False,
        max_tokens=VOICE_LLM_MAX_TOKENS,
    )
    answer, answer_source = await execute_main_llm_once(
        payload=payload,
        user_text=user_text,
    )
    if answer_source == "reasoning":
        log_voice_stage(metrics, "LLM 단발 요청 성공", extra=f"reasoning_len={len(answer)}")
    elif answer_source.startswith("fallback"):
        log_voice_stage(metrics, "LLM canned reply 사용", extra=f"reason={answer_source} fallback_len={len(answer)}")
    else:
        log_voice_stage(metrics, "LLM 단발 요청 성공", extra=f"answer_len={len(answer)}")
    return build_answer_payload_from_text(answer).display_text


def build_stream_speech_chunker(*, metrics: dict | None) -> SpeechChunker:
    speech_chunker = SpeechChunker()
    runtime_opts = ((metrics or {}).get("meta") or {}).get("runtime_opts") or {}
    if runtime_opts.get("tts_chunk_min_chars"):
        speech_chunker.config.next_window = ChunkWindow(
            int(runtime_opts.get("tts_chunk_min_chars") or speech_chunker.config.next_window.min_chars),
            speech_chunker.config.next_window.target_chars,
            speech_chunker.config.next_window.max_chars,
            speech_chunker.config.next_window.allow_soft_breaks,
            speech_chunker.config.next_window.soft_break_overflow_only,
        )
    return speech_chunker


async def emit_stream_delta_chunks(
    delta_text: str,
    *,
    speech_chunker: SpeechChunker,
    on_sentence: Callable[[str], Awaitable[None]] | None,
) -> bool:
    emitted_any = False
    if on_sentence is not None:
        for chunk in speech_chunker.push(delta_text, max_chunks=1):
            if not chunk:
                continue
            emitted_any = True
            await on_sentence(chunk)
    return emitted_any


async def flush_streamed_answer_chunks(
    answer: str,
    *,
    speech_chunker: SpeechChunker,
    on_sentence: Callable[[str], Awaitable[None]] | None,
    emitted_any: bool,
) -> None:
    if on_sentence is None:
        return
    ready_chunks = speech_chunker.flush()
    if not ready_chunks and answer and not emitted_any:
        ready_chunks = [clean_tts_text(answer)]
    for chunk in ready_chunks:
        if not chunk:
            continue
        await on_sentence(chunk)


async def emit_delivery_plan_chunks(
    delivery_plan: DeliveryPlan,
    *,
    on_sentence: Callable[[str], Awaitable[None]] | None,
) -> None:
    if on_sentence is None:
        return
    for chunk in delivery_plan.tts_chunks:
        if not chunk:
            continue
        await on_sentence(chunk)


DEFAULT_INTERNAL_ROUTES = {"main_direct", "policy_short_circuit", "search_executor", "routing", "delivery"}
DISABLED_MAIN_APP_SKILL_ROUTES = {"minecraft"}


def resolve_route_executor(*, guild_id: int | None, route_name: str) -> Any:
    if guild_id is None:
        return None
    engine = autonomy_engines.get(guild_id)
    if engine is None:
        if route_name != "minecraft":
            return None
        engine = get_or_create_autonomy_engine(guild_id)
    return getattr(engine.executor, "executors", {}).get(route_name)


def get_minecraft_client() -> MinecraftAutonomyClient:
    client = getattr(get_minecraft_client, "_client", None)
    if isinstance(client, MinecraftAutonomyClient):
        return client
    client = MinecraftAutonomyClient()
    setattr(get_minecraft_client, "_client", client)
    return client


def _merge_voyager_status_into_state(status: dict[str, Any] | None, observed: dict[str, Any] | None) -> dict[str, Any] | None:
    if not isinstance(status, dict) and not isinstance(observed, dict):
        return None
    merged: dict[str, Any] = dict(observed or {})
    if isinstance(status, dict):
        merged["minecraft_autonomy"] = bool(status.get("running"))
        merged["voyager_connected"] = bool(status.get("connected"))
        goal = clean_text(str(status.get("goal") or ""))
        stage = clean_text(str(status.get("stage") or ""))
        current_task = clean_text(str(status.get("current_task") or ""))
        current_task_stage = clean_text(str(status.get("current_task_stage") or ""))
        last_action = clean_text(str(status.get("last_action") or ""))
        last_progress_message = clean_text(str(status.get("last_progress_message") or ""))
        last_error = clean_text(str(status.get("last_error") or ""))
        if goal:
            merged["objective_goal"] = goal
        if stage:
            merged["objective_stage"] = stage
        if current_task:
            merged["objective_task"] = current_task
        if current_task_stage:
            merged["objective_task_stage"] = current_task_stage
        if last_action:
            merged["objective_last_action"] = last_action
        if last_progress_message:
            merged["objective_progress"] = last_progress_message
        if last_error and not merged.get("last_error"):
            merged["last_error"] = last_error
        current_execution = status.get("autonomy_current_execution")
        if isinstance(current_execution, dict):
            execution_desc = clean_text(str(current_execution.get("description") or current_execution.get("action") or ""))
            execution_stage = clean_text(str(current_execution.get("stage") or ""))
            if execution_desc and not merged.get("objective_task"):
                merged["objective_task"] = execution_desc
            if execution_stage and not merged.get("objective_task_stage"):
                merged["objective_task_stage"] = execution_stage
        voyager_evaluation = status.get("voyager_evaluation") if isinstance(status.get("voyager_evaluation"), dict) else None
        if isinstance(voyager_evaluation, dict):
            merged["voyager_evaluation"] = voyager_evaluation
            unique_item_count = voyager_evaluation.get("unique_item_count")
            if unique_item_count is not None:
                merged["voyager_unique_item_count"] = unique_item_count
            travel_distance_blocks = voyager_evaluation.get("travel_distance_blocks")
            if travel_distance_blocks is not None:
                merged["voyager_travel_distance_blocks"] = travel_distance_blocks
            tech_tree = voyager_evaluation.get("tech_tree") if isinstance(voyager_evaluation.get("tech_tree"), dict) else {}
            tech_tree_highest = clean_text(str(tech_tree.get("highest_unlocked") or ""))
            if tech_tree_highest:
                merged["voyager_tech_tree_highest"] = tech_tree_highest
            skill_library = voyager_evaluation.get("skill_library") if isinstance(voyager_evaluation.get("skill_library"), dict) else {}
            skill_library_size = skill_library.get("size")
            if skill_library_size is not None:
                merged["voyager_skill_library_size"] = skill_library_size
    return merged if merged else None


def get_routed_autonomy_executor(guild_id: int | None) -> RoutedAutonomyExecutor | None:
    if guild_id is None:
        return None
    engine = autonomy_engines.get(guild_id)
    if engine is None:
        return None
    executor = getattr(engine, "executor", None)
    return executor if isinstance(executor, RoutedAutonomyExecutor) else None


async def observe_live_minecraft_state(guild_id: int | None) -> dict[str, Any] | None:
    _ = guild_id
    client = get_minecraft_client()
    try:
        status = await client.status()
    except Exception:
        status = None
    if isinstance(status, dict):
        observed = status.get("observation") if isinstance(status.get("observation"), dict) else None
        merged = _merge_voyager_status_into_state(status, observed)
        if isinstance(merged, dict):
            has_context = bool(
                status.get("running")
                or status.get("connected")
                or clean_text(str(status.get("goal") or ""))
                or clean_text(str(status.get("stage") or ""))
                or clean_text(str(status.get("current_task") or ""))
                or (isinstance(observed, dict) and (observed.get("connected") or observed.get("active") or observed.get("position")))
            )
            if has_context:
                return merged
    try:
        observed = await client.observe(ensure_service=False)
    except Exception:
        return None
    if not isinstance(observed, dict):
        return None
    return _merge_voyager_status_into_state(None, observed) if (observed.get("connected") or observed.get("active") or observed.get("position")) else None


async def wait_for_minecraft_ready(guild_id: int, *, timeout_sec: float = 12.0, poll_sec: float = 1.0) -> dict[str, Any]:
    _ = guild_id
    deadline = time.monotonic() + max(0.5, timeout_sec)
    last_observed: dict[str, Any] = {}
    client = get_minecraft_client()
    while time.monotonic() < deadline:
        status = await client.status()
        observed = status.get("observation") if isinstance(status.get("observation"), dict) else status
        if isinstance(observed, dict):
            last_observed = _merge_voyager_status_into_state(status, observed) or dict(observed)
            if status.get("connected") or observed.get("connected") or observed.get("active") or observed.get("position"):
                return last_observed
            last_error = clean_text(str(status.get("last_error") or observed.get("last_error") or ""))
            if last_error:
                last_observed["wait_last_error"] = last_error
        await asyncio.sleep(max(0.1, poll_sec))
    if last_observed:
        return last_observed
    return {"connected": False, "active": False, "last_error": "timeout_waiting_for_voyager_service"}


async def enable_minecraft_mode(guild_id: int, goal: str | None = None) -> dict[str, Any]:
    _ = guild_id
    client = get_minecraft_client()
    started = await client.start(goal=goal)
    observed = await wait_for_minecraft_ready(guild_id)
    merged = _merge_voyager_status_into_state(started if isinstance(started, dict) else None, observed if isinstance(observed, dict) else None) or {}
    merged["voyager_repo_present"] = started.get("voyager_repo_present") if isinstance(started, dict) else None
    return merged


async def disable_minecraft_mode(guild_id: int) -> None:
    _ = guild_id
    client = get_minecraft_client()
    await client.stop()


def build_skill_context(
    *,
    user_text: str,
    source: str,
    guild_id: int | None,
    session_key: str | None,
    room_key: str | None,
    person_key: str | None,
    session_memory_key: str | None,
    debug_text: str | None,
    metrics: dict | None,
    route_decision: RouteDecision,
    cognitive_state: dict | None,
    messages: list[dict[str, Any]] | None = None,
    minecraft_state: dict[str, Any] | None = None,
) -> SkillContext:
    return SkillContext(
        source=source,
        guild_id=guild_id,
        session_key=session_key,
        room_key=room_key,
        person_key=person_key,
        session_memory_key=session_memory_key,
        debug_text=debug_text,
        metrics=metrics,
        extras={
            "user_text": user_text,
            "route": route_decision.route,
            "action": route_decision.action,
            "prompt_text": route_decision.prompt_text,
            "user_visible_preface": route_decision.user_visible_preface,
            "needs_search": route_decision.needs_search,
            "should_interrupt_delivery": route_decision.should_interrupt_delivery,
            "cognitive_state": cognitive_state,
            "messages": list(messages or []),
            "minecraft_state": dict(minecraft_state or {}),
            "model_name": MODEL_NAME,
            "voice_llm_max_tokens": VOICE_LLM_MAX_TOKENS,
            "build_main_response_guidance_fn": build_main_response_guidance,
            "build_main_llm_payload_fn": build_main_llm_payload,
            "execute_main_llm_once_fn": execute_main_llm_once,
            "execute_search_then_answer_action_fn": execute_search_then_answer_action,
            "build_answer_payload_from_text_fn": build_answer_payload_from_text,
            "build_delivery_plan_fn": build_delivery_plan,
            "split_tts_sentences_fn": split_tts_sentences,
            "executor": resolve_route_executor(guild_id=guild_id, route_name=str(route_decision.route or "")),
        },
    )


def skill_result_to_text(result: Any) -> str:
    if result is None:
        return ""
    if isinstance(result, SkillResult):
        return clean_text(result.display_text or result.answer_text or "")
    if isinstance(result, str):
        return clean_text(result)
    if isinstance(result, dict):
        for key in ("display_text", "answer_text", "message", "summary", "rationale"):
            value = clean_text(str(result.get(key) or ""))
            if value:
                return value
        goal = result.get("goal") or {}
        goal_name = clean_text(str(goal.get("name") or ""))
        goal_desc = clean_text(str(goal.get("description") or ""))
        steps = [clean_text(str(step)) for step in (result.get("proposed_steps") or []) if clean_text(str(step))]
        pieces = []
        if goal_name or goal_desc:
            pieces.append(f"[Minecraft] {goal_name}: {goal_desc}".strip())
        if steps:
            pieces.append(" / ".join(steps[:3]))
        return clean_text(" ".join(piece for piece in pieces if piece))
    return clean_text(str(result))


def make_skill_dispatch_key(*, route_name: str, source: str, session_key: str | None, user_text: str) -> str:
    base = clean_text(user_text).lower()
    return f"{route_name}|{source}|{session_key or '-'}|{base}"


def cleanup_recent_skill_dispatches(*, now: float | None = None) -> None:
    current = time.monotonic() if now is None else now
    stale_before = current - SKILL_DISPATCH_CACHE_TTL_SEC
    stale_keys = [key for key, ts in recent_skill_dispatches.items() if float(ts or 0.0) < stale_before]
    for key in stale_keys:
        recent_skill_dispatches.pop(key, None)
    if len(recent_skill_dispatches) <= SKILL_DISPATCH_CACHE_MAX:
        return
    overflow = len(recent_skill_dispatches) - SKILL_DISPATCH_CACHE_MAX
    for key, _ts in sorted(recent_skill_dispatches.items(), key=lambda item: item[1])[:overflow]:
        recent_skill_dispatches.pop(key, None)


async def maybe_execute_registered_route(
    *,
    route_decision: RouteDecision,
    user_text: str,
    source: str,
    guild_id: int | None,
    session_key: str | None,
    room_key: str | None,
    person_key: str | None,
    session_memory_key: str | None,
    debug_text: str | None,
    metrics: dict | None,
    cognitive_state: dict | None,
    messages: list[dict[str, Any]] | None = None,
    allow_internal_routes: set[str] | None = None,
) -> str | None:
    route_name = clean_text(route_decision.route)
    if not route_name:
        return None
    if route_name in DEFAULT_INTERNAL_ROUTES and route_name not in (allow_internal_routes or set()):
        return None
    if route_name in DISABLED_MAIN_APP_SKILL_ROUTES:
        return None
    dispatch_key = make_skill_dispatch_key(
        route_name=route_name,
        source=source,
        session_key=session_key,
        user_text=user_text,
    )
    now = time.monotonic()
    cleanup_recent_skill_dispatches(now=now)
    last_dispatch = float(recent_skill_dispatches.get(dispatch_key, 0.0) or 0.0)
    if last_dispatch > 0 and (now - last_dispatch) < SKILL_DISPATCH_REPEAT_WINDOW_SEC:
        return None
    skills = skill_registry.find_by_route(route_name, source=source)
    if not skills:
        return None
    live_minecraft_state = await observe_live_minecraft_state(guild_id)
    context = build_skill_context(
        user_text=user_text,
        source=source,
        guild_id=guild_id,
        session_key=session_key,
        room_key=room_key,
        person_key=person_key,
        session_memory_key=session_memory_key,
        debug_text=debug_text,
        metrics=metrics,
        route_decision=route_decision,
        cognitive_state=cognitive_state,
        messages=messages,
        minecraft_state=live_minecraft_state,
    )
    recent_skill_dispatches[dispatch_key] = now
    result = await skill_registry.execute(skills[0].name, context)
    if isinstance(result, SkillResult):
        if not result.handled or not result.should_emit:
            return None
        if result.dedupe_key:
            recent_skill_dispatches[result.dedupe_key] = now
        if result.followup_route:
            if result.followup_delay_ms and int(result.followup_delay_ms) > 0:
                recent_skill_dispatches[dispatch_key] = now + (int(result.followup_delay_ms) / 1000.0)
                return skill_result_to_text(result)
            followup_context = SkillContext(
                source=source,
                guild_id=guild_id,
                session_key=session_key,
                room_key=room_key,
                person_key=person_key,
                session_memory_key=session_memory_key,
                debug_text=debug_text,
                metrics=metrics,
                extras={
                    "user_text": user_text,
                    "answer_text": result.answer_text or result.display_text,
                    **dict(result.followup_payload or {}),
                    "build_answer_payload_from_text_fn": build_answer_payload_from_text,
                    "build_delivery_plan_fn": build_delivery_plan,
                    "split_tts_sentences_fn": split_tts_sentences,
                },
            )
            followup_skills = skill_registry.find_by_route(result.followup_route, source=source)
            if followup_skills:
                followup_result = await skill_registry.execute(followup_skills[0].name, followup_context)
                return skill_result_to_text(followup_result)
    return skill_result_to_text(result)


async def emit_action_result_delivery(
    action_result: ActionResult,
    *,
    on_sentence: Callable[[str], Awaitable[None]] | None,
    session_key: str | None,
    user_text: str,
    awaiting_user_reply: bool,
) -> AnswerPayload:
    answer_payload = action_result_to_answer_payload(action_result)
    if session_key is not None:
        update_session_state(
            session_key,
            speaker="assistant",
            awaiting_user_reply=awaiting_user_reply,
            answer_text=answer_payload.display_text,
            user_text=user_text,
        )
    await emit_delivery_plan_chunks(
        build_delivery_plan(answer_payload, include_voice=on_sentence is not None, split_chunks=split_tts_sentences),
        on_sentence=on_sentence,
    )
    return answer_payload


async def execute_search_then_answer_action(
    *,
    guild_id: int | None,
    user_text: str,
) -> ActionResult:
    search_query = build_search_query(guild_id, user_text) if guild_id is not None else clean_text(user_text)
    try:
        results = await search_duckduckgo(search_query)
        answer = await answer_from_search_results(search_query, results)
        return build_action_result(
            action="search_then_answer",
            answer_text=clean_text(answer) or "지금 검색 결과를 정리하지 못했어. 잠깐 뒤에 다시 시도해줘.",
            metadata={"query": search_query, "result_count": len(results)},
        )
    except Exception:
        return build_action_result(
            action="search_then_answer",
            answer_text="지금 검색 결과를 바로 가져오지 못했어. 잠깐 뒤에 다시 시도해줘.",
            metadata={"query": search_query, "error": "search_failed"},
        )


async def prepare_route_context(
    user_text: str,
    guild_id: int | None = None,
    *,
    session_key: str | None = None,
    room_key: str | None = None,
    person_key: str | None = None,
    session_memory_key: str | None = None,
    source: str = "text",
    debug_text: str | None = None,
    metrics: dict | None = None,
    turn_scope: TurnScope | None = None,
) -> tuple[list[dict[str, Any]], dict | None, RouteDecision, dict | None, bool]:
    messages, cognitive_state, _route = await prepare_llm_messages(
        user_text,
        guild_id=guild_id,
        session_key=session_key,
        room_key=room_key,
        person_key=person_key,
        session_memory_key=session_memory_key,
        source=source,
        debug_text=debug_text,
        metrics=metrics,
        turn_scope=turn_scope,
    )
    policy_response = policy_response_for_state(cognitive_state, source=source)
    route_decision = build_route_decision_from_state(
        cognitive_state=cognitive_state,
        source=source,
        user_text=user_text,
        policy_response=policy_response,
        apply_ask_gating=apply_ask_gating,
        build_route_decision=build_route_decision,
    )
    gated_state = apply_ask_gating(cognitive_state, source=source) if cognitive_state is not None else None
    awaiting_user_reply = should_await_user_reply_for_route(
        gated_state=gated_state,
        route_action=route_decision.action,
    )
    return messages, cognitive_state, route_decision, gated_state, awaiting_user_reply


async def maybe_handle_short_circuit_route(
    *,
    route_decision: RouteDecision,
    source: str,
    guild_id: int | None,
    user_text: str,
    session_key: str | None,
    on_sentence: Callable[[str], Awaitable[None]] | None,
    on_first_chunk: Callable[[], None] | None,
    awaiting_user_reply: bool,
    metrics: dict | None,
) -> tuple[str | None, Callable[[], None] | None]:
    if route_decision.action == "search_then_answer" and source == "voice":
        if on_first_chunk is not None:
            on_first_chunk()
            on_first_chunk = None
        preface_text = route_decision.user_visible_preface or "금방 찾아보고 바로 알려줄게."
        await emit_action_result_delivery(
            build_action_result(
                action=route_decision.action,
                answer_text=preface_text,
                metadata={"route": route_decision.route, "phase": "preface"},
            ),
            on_sentence=on_sentence,
            session_key=session_key,
            user_text=user_text,
            awaiting_user_reply=awaiting_user_reply,
        )
        action_result = await execute_search_then_answer_action(
            guild_id=guild_id,
            user_text=user_text,
        )
        answer_payload = await emit_action_result_delivery(
            action_result,
            on_sentence=on_sentence,
            session_key=session_key,
            user_text=user_text,
            awaiting_user_reply=False,
        )
        if metrics is not None:
            elapsed_ms = (time.monotonic() - float(metrics.get("started_at", time.monotonic()))) * 1000.0
            metrics.setdefault("marks", {})["policy_short_circuit"] = elapsed_ms
            metrics.setdefault("marks", {})["llm_done"] = elapsed_ms
            metrics.setdefault("marks", {})["t_main_done"] = elapsed_ms
        return answer_payload.display_text, on_first_chunk

    if route_decision.user_visible_preface:
        if on_first_chunk is not None:
            on_first_chunk()
            on_first_chunk = None
        policy_payload = await emit_action_result_delivery(
            build_action_result(
                action=route_decision.action,
                answer_text=route_decision.user_visible_preface,
                metadata={"route": route_decision.route},
            ),
            on_sentence=on_sentence,
            session_key=session_key,
            user_text=user_text,
            awaiting_user_reply=awaiting_user_reply,
        )
        if metrics is not None:
            metrics.setdefault("marks", {})["policy_short_circuit"] = (time.monotonic() - float(metrics.get("started_at", time.monotonic()))) * 1000.0
            metrics.setdefault("marks", {})["llm_done"] = (time.monotonic() - float(metrics.get("started_at", time.monotonic()))) * 1000.0
            metrics.setdefault("marks", {})["t_main_done"] = metrics.setdefault("marks", {}).get("llm_done")
        return policy_payload.display_text, on_first_chunk

    return None, on_first_chunk


async def ask_llm_streaming(
    user_text: str,
    guild_id: int | None = None,
    on_sentence: Callable[[str], Awaitable[None]] | None = None,
    on_first_chunk: Callable[[], None] | None = None,
    *,
    session_key: str | None = None,
    room_key: str | None = None,
    person_key: str | None = None,
    session_memory_key: str | None = None,
    source: str = "text",
    debug_text: str | None = None,
    metrics: dict | None = None,
    turn_scope: TurnScope | None = None,
) -> str:
    global inflight_llm_requests
    task = _attach_current_task(turn_scope)
    try:
        if turn_scope is not None:
            turn_scope.raise_if_cancelled()
        messages, cognitive_state, route_decision, gated_state, awaiting_user_reply = await prepare_route_context(
            user_text,
            guild_id=guild_id,
            session_key=session_key,
            room_key=room_key,
            person_key=person_key,
            session_memory_key=session_memory_key,
            source=source,
            debug_text=debug_text,
            metrics=metrics,
            turn_scope=turn_scope,
        )
        short_circuit_answer, on_first_chunk = await maybe_handle_short_circuit_route(
            route_decision=route_decision,
            source=source,
            guild_id=guild_id,
            user_text=user_text,
            session_key=session_key,
            on_sentence=on_sentence,
            on_first_chunk=on_first_chunk,
            awaiting_user_reply=awaiting_user_reply,
            metrics=metrics,
        )
        if short_circuit_answer is not None:
            return short_circuit_answer

        skill_route_answer = await maybe_execute_registered_route(
            route_decision=route_decision,
            user_text=user_text,
            source=source,
            guild_id=guild_id,
            session_key=session_key,
            room_key=room_key,
            person_key=person_key,
            session_memory_key=session_memory_key,
            debug_text=debug_text,
            metrics=metrics,
            cognitive_state=cognitive_state,
            messages=messages,
        )
        if skill_route_answer:
            if on_first_chunk is not None:
                on_first_chunk()
                on_first_chunk = None
            await emit_delivery_plan_chunks(
                build_delivery_plan(build_answer_payload_from_text(skill_route_answer), include_voice=on_sentence is not None, split_chunks=split_tts_sentences),
                on_sentence=on_sentence,
            )
            return skill_route_answer

        guided_user_text = route_decision.prompt_text or user_text
        live_minecraft_state = await observe_live_minecraft_state(guild_id)
        final_user_text = f"{guided_user_text}\n\n{build_main_response_guidance(cognitive_state, source=source, minecraft_state=live_minecraft_state)}"
        mark_turn_stage(
            metrics,
            "prompt_built",
            event_name="prompt_built",
            prompt_chars=len(final_user_text),
            source_mode=source,
        )

        payload = build_main_llm_payload(
            model_name=MODEL_NAME,
            messages=messages,
            final_user_text=final_user_text,
            source=source,
            stream=True,
            max_tokens=VOICE_LLM_MAX_TOKENS,
        )

        timeout = aiohttp.ClientTimeout(total=120)
        session = await get_http_session()
        raw_parts: list[str] = []
        reasoning_parts: list[str] = []
        speech_chunker = build_stream_speech_chunker(metrics=metrics)
        emitted_any = False
        llm_started_at = time.monotonic()

        inflight_llm_requests += 1
        try:
            mark_turn_stage(
                metrics,
                "llm_request_start",
                event_name="llm_request_start",
                source_mode=source,
                prompt_chars=len(final_user_text),
            )
            async with session.post(LLM_SERVER_URL, json=payload, timeout=timeout) as resp:
                if resp.status != 200:
                    error_text = await resp.text()
                    raise RuntimeError(f"LLM 서버 오류: {resp.status} / {error_text[:300]}")

                content_type = resp.headers.get("Content-Type", "")
                if "application/json" in content_type.lower():
                    if turn_scope is not None:
                        turn_scope.raise_if_cancelled()
                    data = await resp.json()
                    choices = data.get("choices", [])
                    answer = ""
                    if choices:
                        answer, _answer_source, _finish_reason = extract_main_llm_answer_from_choice(
                            choices[0],
                            user_text,
                            sanitize_output=sanitize_model_output,
                            parse_response_action_tag=parse_response_action_tag,
                            extract_answer_from_reasoning=extract_answer_from_reasoning,
                        )
                    if not answer:
                        print("[LLM STREAM] json 응답 본문 비어 있음, non-stream 재시도")
                        answer = await ask_llm_once(
                            user_text,
                            guild_id=guild_id,
                            session_key=session_key,
                            room_key=room_key,
                            person_key=person_key,
                            session_memory_key=session_memory_key,
                            source=source,
                            debug_text=debug_text,
                        )
                    if on_first_chunk is not None:
                        mark_turn_stage(
                            metrics,
                            "llm_first_chunk",
                            event_name="llm_first_chunk",
                            source_mode=source,
                            since_request_ms=max(0.0, (time.monotonic() - llm_started_at) * 1000.0),
                        )
                        on_first_chunk()
                        on_first_chunk = None
                    await emit_delivery_plan_chunks(
                        build_delivery_plan(build_answer_payload_from_text(answer), include_voice=on_sentence is not None, split_chunks=split_tts_sentences),
                        on_sentence=on_sentence,
                    )
                    if metrics is not None:
                        metrics.setdefault("marks", {})["llm_done"] = (time.monotonic() - float(metrics.get("started_at", time.monotonic()))) * 1000.0
                    return answer

                async for raw_line in resp.content:
                    if turn_scope is not None:
                        turn_scope.raise_if_cancelled()
                    stream_event = decode_sse_stream_line(raw_line)
                    if not stream_event:
                        continue
                    if stream_event.get("done"):
                        break

                    reasoning_text = str(stream_event.get("reasoning_text") or "")
                    if reasoning_text:
                        reasoning_parts.append(reasoning_text)

                    delta_text = str(stream_event.get("delta_text") or "")
                    if not delta_text:
                        continue

                    if on_first_chunk is not None:
                        mark_turn_stage(
                            metrics,
                            "llm_first_chunk",
                            event_name="llm_first_chunk",
                            source_mode=source,
                            since_request_ms=max(0.0, (time.monotonic() - llm_started_at) * 1000.0),
                        )
                        on_first_chunk()
                        on_first_chunk = None

                    raw_parts.append(delta_text)
                    emitted_any = (await emit_stream_delta_chunks(
                        delta_text,
                        speech_chunker=speech_chunker,
                        on_sentence=on_sentence,
                    )) or emitted_any
        finally:
            inflight_llm_requests = max(0, inflight_llm_requests - 1)

        if turn_scope is not None:
            turn_scope.raise_if_cancelled()
        answer = sanitize_model_output("".join(raw_parts))
        if not answer:
            print(
                f"[LLM STREAM] stream 본문 비어 있음, non-stream 재시도 | raw_len={len(''.join(raw_parts))} reasoning_len={len(''.join(reasoning_parts))} emitted_any={emitted_any}"
            )
            answer = await ask_llm_once(
                user_text,
                guild_id=guild_id,
                session_key=session_key,
                room_key=room_key,
                person_key=person_key,
                session_memory_key=session_memory_key,
                source=source,
                debug_text=debug_text,
            )

        if turn_scope is not None:
            turn_scope.raise_if_cancelled()
        await flush_streamed_answer_chunks(
            answer,
            speech_chunker=speech_chunker,
            on_sentence=on_sentence,
            emitted_any=emitted_any,
        )

        if metrics is not None:
            metrics.setdefault("marks", {})["llm_http_ms"] = (time.monotonic() - llm_started_at) * 1000.0
            metrics.setdefault("marks", {})["llm_done"] = (time.monotonic() - float(metrics.get("started_at", time.monotonic()))) * 1000.0

        return answer
    finally:
        _detach_task(turn_scope, task)


def start_streaming_voice_delivery(
    vc: discord.VoiceClient,
    *,
    metrics: dict,
    turn_id: str | None,
    session_key: str | None,
    turn_scope: TurnScope | None,
) -> StreamingVoiceDelivery:
    sentence_queue: asyncio.Queue[str | None] = asyncio.Queue()
    tts_sink = TTSQueueSink(sentence_queue)
    playback_task = create_turn_scoped_task(
        stream_tts_sentences(
            vc,
            sentence_queue,
            metrics=metrics,
            turn_id=turn_id,
            session_key=session_key,
            turn_scope=turn_scope,
        ),
        turn_scope=turn_scope,
    )
    return StreamingVoiceDelivery(
        sentence_queue,
        tts_sink,
        playback_task,
        metrics=metrics,
    )


async def execute_voice_delivery_plan(
    vc: discord.VoiceClient,
    delivery_plan: DeliveryPlan,
    *,
    metrics: dict,
    turn_id: str | None,
    session_key: str | None,
    turn_scope: TurnScope | None,
) -> int:
    if not delivery_plan.should_play_voice or not delivery_plan.tts_chunks:
        return 0

    delivery = start_streaming_voice_delivery(
        vc,
        metrics=metrics,
        turn_id=turn_id,
        session_key=session_key,
        turn_scope=turn_scope,
    )
    try:
        for chunk in delivery_plan.tts_chunks:
            await delivery.on_chunk(chunk)
        await delivery.close(delivery_plan.text_message or "")
        return await delivery.finalize()
    finally:
        await delivery.abort()


async def finalize_voice_answer(
    answer: str,
    *,
    on_final_answer: Callable[[str], Awaitable[None]] | None,
    delivery: StreamingVoiceDelivery,
    metrics: dict,
) -> tuple[str, int]:
    cleaned_answer = clean_text(answer)
    log_voice_stage(metrics, "LLM 완료", extra=f"chars={len(cleaned_answer)}", key="llm_done")
    if cleaned_answer and on_final_answer is not None:
        await on_final_answer(cleaned_answer)
    await delivery.close(cleaned_answer)
    queued_sentence_count = await delivery.finalize()
    return cleaned_answer, queued_sentence_count


async def ask_llm_and_speak_streaming(
    vc: discord.VoiceClient,
    user_text: str,
    guild_id: int | None = None,
    on_final_answer: Callable[[str], Awaitable[None]] | None = None,
    *,
    session_key: str | None = None,
    room_key: str | None = None,
    person_key: str | None = None,
    session_memory_key: str | None = None,
    source: str = "voice",
    debug_text: str | None = None,
    metrics: dict | None = None,
    turn_scope: TurnScope | None = None,
) -> str:
    task = _attach_current_task(turn_scope)
    try:
        if metrics is None:
            metrics = new_turn_metrics(
                source=source,
                session_key=session_key,
                guild_id=guild_id,
                topic_id=session_topic_ids.get(session_key),
                turn_id=current_turn_id(session_key),
                segment_id=0,
            )
        else:
            metrics.setdefault("started_at", time.monotonic())
            metrics.setdefault("marks", {})
            metrics.setdefault("meta", {})
        metrics.setdefault("tts_request_logged", False)
        metrics.setdefault("tts_response_headers_logged", False)
        metrics.setdefault("tts_first_byte_logged", False)
        metrics.setdefault("tts_first_frame_logged", False)
        metrics.setdefault("first_packet_sent_logged", False)
        log_voice_stage(metrics, "LLM/TTS 파이프라인 시작", extra=f"source={source} mode=llm_streaming")

        delivery = start_streaming_voice_delivery(
            vc,
            metrics=metrics,
            turn_id=metrics.get("meta", {}).get("turn_id") or current_turn_id(session_key),
            session_key=session_key,
            turn_scope=turn_scope,
        )
        fanout = ReplyStreamFanout([delivery])

        answer = ""
        queued_sentence_count = 0
        try:
            answer = await ask_llm_streaming(
                user_text,
                guild_id=guild_id,
                session_key=session_key,
                room_key=room_key,
                person_key=person_key,
                session_memory_key=session_memory_key,
                on_sentence=fanout.on_chunk,
                on_first_chunk=lambda: log_voice_latency(metrics, "llm_first_chunk_logged", "LLM 첫 chunk 시간"),
                source=source,
                debug_text=debug_text,
                metrics=metrics,
                turn_scope=turn_scope,
            )
            answer, queued_sentence_count = await finalize_voice_answer(
                answer,
                on_final_answer=on_final_answer,
                delivery=delivery,
                metrics=metrics,
            )
        finally:
            await delivery.abort()

        log_voice_bottleneck_summary(
            metrics,
            label="voice_turn",
            extra=f"source={source} chars={len(answer)} mode=llm_streaming sentences={queued_sentence_count}",
            event_name="voice_turn_summary",
        )
        return answer
    finally:
        _detach_task(turn_scope, task)


async def stream_text_reply(
    channel: discord.abc.Messageable,
    user_text: str,
    *,
    guild_id: int,
    session_key: str,
    turn_id: str | None = None,
    room_key: str | None = None,
    person_key: str | None = None,
    session_memory_key: str | None = None,
    source: str = "text",
    debug_text: str | None = None,
    include_voice: bool = False,
    turn_scope: TurnScope | None = None,
) -> tuple[str, discord.Message | None, dict, DeliveryPlan]:
    task = _attach_current_task(turn_scope)
    try:
        metrics = new_turn_metrics(
            source=source,
            session_key=session_key,
            guild_id=guild_id,
            topic_id=session_topic_ids.get(session_key),
            turn_id=turn_id,
            segment_id=0,
        )

        answer = ""
        sent_message: discord.Message | None = None
        answer = await ask_llm_streaming(
            user_text,
            guild_id=guild_id,
            session_key=session_key,
            room_key=room_key,
            person_key=person_key,
            session_memory_key=session_memory_key,
            on_first_chunk=lambda: log_voice_latency(metrics, "llm_first_chunk_logged", "LLM 첫 chunk 시간"),
            source=source,
            debug_text=debug_text,
            metrics=metrics,
            turn_scope=turn_scope,
        )
        answer_payload = build_answer_payload_from_text(answer)
        final_text = format_display_text(answer_payload.display_text, session_key=session_key).strip() or fallback_answer_for(user_text)
        delivery_plan = build_delivery_plan(
            answer_payload,
            include_voice=include_voice,
            text_message=final_text,
            split_chunks=split_tts_sentences,
        )
        sent_message = await channel.send(final_text)
        return answer, sent_message, metrics, delivery_plan
    finally:
        _detach_task(turn_scope, task)


# =========================================================
# 음성 입력 처리
# =========================================================
async def process_member_audio(member: discord.Member | None, pcm_bytes: bytes, debug_meta: dict | None = None) -> None:
    await ensure_startup_components_ready()
    if member is None or member.bot:
        return

    guild = getattr(member, "guild", None)
    if guild is None:
        return

    ensure_voice_worker_started()

    guild_id = guild.id
    voice_channel_id = getattr(getattr(guild.voice_client, "channel", None), "id", None)
    room_session_key = make_voice_room_session_key(guild_id, voice_channel_id)
    session_key = make_voice_session_key(guild_id, voice_channel_id, member.id)
    room_key = make_room_memory_key("voice", voice_channel_id)
    person_key = make_person_memory_key(member.id)
    session_memory_key = make_session_memory_key(session_key, member.id)
    segment_id = next_segment_id(session_key)
    turn_id = new_turn_id()
    room_state = room_state_snapshot(room_session_key)
    voice_ingress_queue.put_nowait(
        {
            "member": member,
            "pcm_bytes": pcm_bytes,
            "debug_meta": debug_meta,
            "session_key": session_key,
            "room_session_key": room_session_key,
            "room_key": room_key,
            "person_key": person_key,
            "session_memory_key": session_memory_key,
            "turn_id": turn_id,
            "segment_id": segment_id,
            "ingress_during_reply": bool(room_state.get("reply_in_progress")),
            "owner_user_id_on_ingress": room_state.get("owner_user_id"),
        }
    )


async def _process_member_audio_impl(
    member: discord.Member | None,
    pcm_bytes: bytes,
    debug_meta: dict | None = None,
    *,
    session_key: str,
    room_session_key: str,
    room_key: str | None,
    person_key: str | None,
    session_memory_key: str | None,
    turn_id: str,
    segment_id: int,
    ingress_during_reply: bool = False,
    owner_user_id_on_ingress: int | None = None,
) -> None:
    if member is None:
        return

    if member.bot:
        return

    guild = getattr(member, "guild", None)
    if guild is None:
        return

    guild_id = guild.id
    speaker_name = member.display_name or str(member.id)
    audio16k_ingress = prepare_stt_audio(pcm_bytes)
    save_voice_debug_audio(
        guild_id,
        speaker_name,
        pcm_bytes,
        audio16k_ingress,
        final_text="[INGRESS RAW]",
        debug_meta=debug_meta,
        save_stt_audio=True,
        session_key=session_key,
        stage_label="ingress",
    )
    room_state = room_state_snapshot(room_session_key)
    owner_user_id = room_state.get("owner_user_id")
    topic_id = session_topic_ids.get(session_key) or build_topic_id(member.display_name or str(member.id))
    metrics = new_turn_metrics(
        source="voice",
        session_key=session_key,
        room_session_key=room_session_key,
        guild_id=guild_id,
        user_id=member.id,
        owner_user_id=owner_user_id,
        topic_id=topic_id,
        turn_id=turn_id,
        segment_id=segment_id,
    )
    if isinstance(debug_meta, dict):
        queue_wait_ms = debug_meta.get("queue_wait_ms")
        if queue_wait_ms is not None:
            try:
                queue_wait_ms = float(queue_wait_ms)
            except (TypeError, ValueError):
                queue_wait_ms = None
            else:
                metrics.setdefault("meta", {})["voice_queue_wait_ms"] = queue_wait_ms
                metrics.setdefault("marks", {})["voice_queue_wait_ms"] = queue_wait_ms
    log_voice_stage(metrics, "voice_worker_turn 시작", extra=f"speaker={member.display_name} pcm_bytes={len(pcm_bytes)} owner={owner_user_id}")

    if ingress_during_reply and owner_user_id_on_ingress is not None and owner_user_id_on_ingress != member.id:
        register_drop_reason(
            metrics,
            "other_speaker_during_reply",
            session_key=session_key,
            room_session_key=room_session_key,
            owner_user_id=owner_user_id_on_ingress,
        )
        log_voice_stage(metrics, "다른 화자 중복 진입 차단", extra=f"owner_user_id={owner_user_id_on_ingress}")
        log_voice_bottleneck_summary(metrics, label="voice_drop", extra="drop=other_speaker_during_reply", event_name="voice_drop_summary")
        return

    if STT_USE_RAW_48K:
        audio16k = downmix_int16_stereo_to_mono_float(pcm_bytes)
        audio_for_wake = apply_light_denoise(audio16k, sampling_rate=RATE)
        stt_sampling_rate = RATE
        wake_sampling_rate = RATE
    else:
        audio16k = prepare_stt_audio(pcm_bytes)
        audio_for_wake = audio16k
        stt_sampling_rate = TARGET_RATE
        wake_sampling_rate = TARGET_RATE
    if audio16k.size == 0:
        register_drop_reason(metrics, "empty_audio", session_key=session_key)
        log_voice_stage(metrics, "오디오 비어있음")
        log_voice_bottleneck_summary(metrics, label="voice_drop", extra="drop=empty_audio", event_name="voice_drop_summary")
        return

    raw_seconds = len(pcm_bytes) / float(RATE * CHANNELS * 2)
    if raw_seconds <= VOICE_MIN_TOTAL_SEC:
        print(f"[FULL STT SKIP] reason=too_short_total speaker={member.display_name} raw_seconds={raw_seconds:.3f}")
        print(f"[SHORT AUDIO IGNORE] speaker={member.display_name} raw_seconds={raw_seconds:.3f}")
        save_voice_debug_audio(
            guild_id,
            speaker_name,
            pcm_bytes,
            audio16k,
            final_text="[SHORT AUDIO IGNORE]",
            debug_meta=debug_meta,
            save_stt_audio=False,
            session_key=session_key,
            stage_label="drop",
        )
        register_drop_reason(metrics, "too_short_total", session_key=session_key, raw_seconds=round(raw_seconds, 3))
        log_voice_stage(metrics, "전체 길이 너무 짧아서 제외", extra=f"raw_seconds={raw_seconds:.3f}")
        log_voice_bottleneck_summary(metrics, label="voice_drop", extra="drop=too_short_total", event_name="voice_drop_summary")
        return

    unstable_audio = bool(debug_meta and debug_meta.get("unstable"))
    transport_corrupted = is_transport_corrupted_audio(debug_meta)
    if unstable_audio:
        reasons = ",".join(str(r) for r in debug_meta.get("reasons", []))
        print(f"[UNSTABLE AUDIO] speaker={member.display_name} reasons={reasons}")
        log_voice_stage(metrics, "불안정 음성 감지", extra=f"reasons={reasons}")

    duration_sec = len(audio16k) / float(max(1, stt_sampling_rate))
    waveform_stats = compute_waveform_activity_stats(audio16k, sampling_rate=stt_sampling_rate)
    voiced_ms = float(waveform_stats.get("voiced_ms") or 0.0)
    longest_voiced_ms = float(waveform_stats.get("longest_voiced_ms") or 0.0)
    body_rms = float(waveform_stats.get("body_rms") or 0.0)
    voice_like_prob = estimate_voice_like_probability(voiced_ms=voiced_ms, audio_sec=duration_sec, body_rms=body_rms)
    update_room_speaker_activity(
        room_session_key,
        member.id,
        voiced_ms=voiced_ms,
        raw_seconds=raw_seconds,
        rms=body_rms,
        wake_detected=False,
    )
    if transport_corrupted and raw_seconds <= max(1.4, TAIL_FRAGMENT_MAX_RAW_SEC + 0.5):
        bad_audio_count = increment_session_bad_audio(session_key)
        register_drop_reason(
            metrics,
            "transport_corrupted",
            session_key=session_key,
            room_session_key=room_session_key,
            owner_user_id=owner_user_id,
            raw_seconds=round(raw_seconds, 3),
            voiced_ms=round(voiced_ms, 1),
            longest_voiced_ms=round(longest_voiced_ms, 1),
            bad_audio_count=bad_audio_count,
        )
        log_voice_stage(
            metrics,
            "transport corrupted 조기 종료",
            extra=f"raw_seconds={raw_seconds:.3f} voiced_ms={voiced_ms:.0f} longest_ms={longest_voiced_ms:.0f}",
        )
        log_voice_bottleneck_summary(metrics, label="voice_drop", extra="drop=transport_corrupted", event_name="voice_drop_summary")
        return
    if is_tail_fragment_candidate(
        session_key=session_key,
        raw_seconds=raw_seconds,
        voiced_ms=voiced_ms,
        longest_voiced_ms=longest_voiced_ms,
        unstable=unstable_audio,
    ):
        bad_audio_count = increment_session_bad_audio(session_key)
        register_drop_reason(
            metrics,
            "tail_fragment_drop",
            session_key=session_key,
            room_session_key=room_session_key,
            owner_user_id=owner_user_id,
            raw_seconds=round(raw_seconds, 3),
            voiced_ms=round(voiced_ms, 1),
            longest_voiced_ms=round(longest_voiced_ms, 1),
            bad_audio_count=bad_audio_count,
        )
        log_voice_stage(
            metrics,
            "tail fragment 조기 종료",
            extra=f"raw_seconds={raw_seconds:.3f} voiced_ms={voiced_ms:.0f} longest_ms={longest_voiced_ms:.0f}",
        )
        log_voice_bottleneck_summary(metrics, label="voice_drop", extra="drop=tail_fragment_drop", event_name="voice_drop_summary")
        return
    if VAD_ENABLED and is_probably_silent(audio16k, sampling_rate=stt_sampling_rate):
        duration_sec = len(audio16k) / float(max(1, stt_sampling_rate))
        peak = float(np.max(np.abs(audio16k))) if audio16k.size else 0.0
        rms = float(np.sqrt(np.mean(np.square(audio16k)))) if audio16k.size else 0.0
        voiced_ms = float(waveform_stats.get("voiced_ms") or 0.0)
        longest_voiced_ms = float(waveform_stats.get("longest_voiced_ms") or 0.0)
        body_rms = float(waveform_stats.get("body_rms") or 0.0)
        body_peak = float(waveform_stats.get("body_peak") or 0.0)
        waveform_override = (not transport_corrupted) and voiced_ms >= VOICE_WAVEFORM_MIN_VOICED_MS and (
            longest_voiced_ms >= VOICE_WAVEFORM_MIN_RUN_MS
            or body_rms >= VOICE_WAVEFORM_BODY_RMS_MIN
            or body_peak >= VOICE_WAVEFORM_BODY_PEAK_MIN
        )
        if waveform_override:
            print(
                f"[VAD OVERRIDE] speaker={member.display_name} sec={duration_sec:.2f} voiced_ms={voiced_ms:.0f} longest_ms={longest_voiced_ms:.0f} body_rms={body_rms:.4f} body_peak={body_peak:.4f}"
            )
            log_voice_stage(
                metrics,
                "VAD override",
                extra=f"voiced_ms={voiced_ms:.0f} longest_ms={longest_voiced_ms:.0f} body_rms={body_rms:.4f} body_peak={body_peak:.4f}",
            )
        else:
            print(f"[FULL STT SKIP] reason=vad_ignore speaker={member.display_name} sampling_rate={stt_sampling_rate} sec={duration_sec:.2f} peak={peak:.4f} rms={rms:.4f} voiced_ms={voiced_ms:.0f} longest_ms={longest_voiced_ms:.0f} body_rms={body_rms:.4f}")
            print(f"[VAD IGNORE] speaker={member.display_name} sampling_rate={stt_sampling_rate} sec={duration_sec:.2f} peak={peak:.4f} rms={rms:.4f}")
            save_voice_debug_audio(guild_id, speaker_name, pcm_bytes, audio16k, final_text="[VAD IGNORE]", debug_meta=debug_meta, session_key=session_key, stage_label="drop")
            bad_audio_count = increment_session_bad_audio(session_key)
            register_drop_reason(metrics, "vad_ignore", session_key=session_key, room_session_key=room_session_key, owner_user_id=owner_user_id, voiced_ms=round(voiced_ms, 1), bad_audio_count=bad_audio_count)
            log_voice_stage(metrics, "VAD 무시 처리", extra=f"sampling_rate={stt_sampling_rate} sec={duration_sec:.2f} peak={peak:.4f} rms={rms:.4f} voiced_ms={voiced_ms:.0f} longest_ms={longest_voiced_ms:.0f} body_rms={body_rms:.4f}")
            log_voice_bottleneck_summary(metrics, label="voice_drop", extra="drop=vad_ignore", event_name="voice_drop_summary")
            return

    owner_followup_active = is_room_owner_active(room_session_key, member.id) and is_session_active_for_user(session_key, member.id)
    active_speaker_user_id = pick_active_speaker(room_session_key)
    wake_probe = ""
    wake_confirm = ""
    wake_detected = False
    wake_match_mode = "owner_followup_active" if owner_followup_active else "rejected"
    wake_alias = None
    wake_reject_reason = None

    if owner_followup_active:
        log_voice_stage(
            metrics,
            "active owner follow-up, wake probe 생략",
            extra=f"owner_user_id={member.id}",
            key="wake_done",
        )
    else:
        log_voice_stage(metrics, "웨이크 프로브 시작", extra=f"samples={audio_for_wake.size} sampling_rate={wake_sampling_rate}")
        try:
            wake_result = await asyncio.wait_for(
                asyncio.to_thread(detect_wake_word_sync, audio_for_wake, sampling_rate=wake_sampling_rate),
                timeout=max(5.0, WAKE_STT_TIMEOUT_SEC),
            )
        except Exception as e:
            print(f"[WAKE STT] {e}")
            register_drop_reason(metrics, "wake_probe_error", session_key=session_key, room_session_key=room_session_key, owner_user_id=owner_user_id, error=repr(e))
            log_voice_stage(metrics, "웨이크 프로브 실패", extra=repr(e))
            log_voice_bottleneck_summary(metrics, label="voice_drop", extra="drop=wake_probe_error", event_name="voice_drop_summary")
            return

        wake_probe = apply_stt_post_corrections(str(wake_result.get("wake_probe_text") or ""), wake_detected=False)
        wake_confirm = apply_stt_post_corrections(str(wake_result.get("wake_confirm_text") or ""), wake_detected=False)
        wake_detected = bool(wake_result.get("wake_detected"))
        wake_match_mode = str(wake_result.get("wake_match_mode") or ("exact" if wake_detected else "rejected"))
        wake_alias = clean_text(str(wake_result.get("wake_alias") or "")) or None
        wake_reject_reason = clean_text(str(wake_result.get("wake_reject_reason") or "")) or None
        print(
            f"[STT RESULT][wake] probe={wake_probe!r} confirm={wake_confirm!r} detected={wake_detected} mode={wake_match_mode} alias={wake_alias!r} reject={wake_reject_reason!r}"
        )

        strict_confirm_required = should_require_confirm_exact_for_wake(debug_meta)
        if strict_confirm_required and wake_match_mode != "exact":
            wake_detected = False
            wake_match_mode = "rejected"
            wake_reject_reason = "unstable_audio"

        log_voice_stage(
            metrics,
            "웨이크 프로브 완료",
            extra=(
                f"wake_detected={wake_detected} wake_match_mode={wake_match_mode} wake_alias={wake_alias!r} "
                f"wake_probe_text={wake_probe!r} wake_confirm_text={wake_confirm!r} wake_reject_reason={wake_reject_reason!r}"
            ),
            key="wake_done",
        )

        hard_drop_reasons = {"unstable_audio", "gibberish_probe", "probe_miss", "confirm_miss", "wake_probe_low_signal", "full_text_veto", "transport_corrupted"}
        fuzzy_probe_alias = fuzzy_leading_wake_alias(wake_probe)
        fuzzy_confirm_alias = fuzzy_leading_wake_alias(wake_confirm)
        near_miss_wake = bool((not wake_detected) and (fuzzy_probe_alias or fuzzy_confirm_alias))
        if near_miss_wake:
            wake_detected = True
            wake_match_mode = "fuzzy"
            wake_alias = fuzzy_probe_alias or fuzzy_confirm_alias
            wake_reject_reason = None
            log_voice_stage(metrics, "웨이크 근접오타 완화", extra=f"probe={wake_probe!r} confirm={wake_confirm!r} alias={wake_alias!r}")
        if not wake_detected:
            reject_reason = wake_reject_reason or "confirm_miss"
            if reject_reason in hard_drop_reasons:
                register_drop_reason(
                    metrics,
                    reject_reason,
                    session_key=session_key,
                    room_session_key=room_session_key,
                    owner_user_id=owner_user_id,
                    wake_probe_text=wake_probe,
                    wake_confirm_text=wake_confirm,
                    wake_match_mode=wake_match_mode,
                    wake_alias=wake_alias,
                )
                log_voice_stage(metrics, "웨이크 거부", extra=f"wake_reject_reason={reject_reason} wake_match_mode={wake_match_mode}")
                log_voice_bottleneck_summary(metrics, label="voice_drop", extra=f"drop={reject_reason}", event_name="voice_drop_summary")
                return
        env_noise_candidate = is_likely_environment_noise(audio_for_wake, sampling_rate=wake_sampling_rate)
        filler_candidate = looks_like_brief_filler_text(wake_probe)
        repetitive_noise_candidate = looks_like_repetitive_noise_text(wake_probe)

        if env_noise_candidate:
            band_ratio, flatness, rms = compute_voice_band_metrics(audio_for_wake, sampling_rate=wake_sampling_rate)
            if not wake_detected and raw_seconds <= VOICE_NO_WAKE_MAX_CONTINUE_SEC:
                print(f"[FULL STT SKIP] reason=env_ignore speaker={member.display_name} probe={wake_probe!r}")
                print(
                    f"[ENV IGNORE] speaker={member.display_name} band_ratio={band_ratio:.3f} flatness={flatness:.3f} rms={rms:.4f} probe={wake_probe!r}"
                )
                save_voice_debug_audio(guild_id, speaker_name, pcm_bytes, audio16k, wake_probe=wake_probe, final_text="[ENV IGNORE]", debug_meta=debug_meta, session_key=session_key, stage_label="drop")
                bad_audio_count = increment_session_bad_audio(session_key)
                register_drop_reason(metrics, "env_ignore", session_key=session_key, room_session_key=room_session_key, owner_user_id=owner_user_id, wake_probe_text=wake_probe, bad_audio_count=bad_audio_count)
                log_voice_stage(metrics, "환경음 후보 조기 종료", extra=f"wake_probe_text={wake_probe!r}")
                log_voice_bottleneck_summary(metrics, label="voice_drop", extra="drop=env_ignore", event_name="voice_drop_summary")
                return
            print(f"[FULL STT CONTINUE] reason=env_ignore speaker={member.display_name} probe={wake_probe!r}")
            print(
                f"[ENV IGNORE] speaker={member.display_name} band_ratio={band_ratio:.3f} flatness={flatness:.3f} rms={rms:.4f} probe={wake_probe!r}"
            )
            log_voice_stage(metrics, "환경음 후보지만 본문 STT 진행", extra=f"wake_probe_text={wake_probe!r}")

        if filler_candidate:
            if not wake_detected and raw_seconds <= VOICE_NO_WAKE_MAX_CONTINUE_SEC:
                print(f"[FULL STT SKIP] reason=filler_ignore speaker={member.display_name} probe={wake_probe!r}")
                print(f"[FILLER IGNORE] speaker={member.display_name} probe={wake_probe!r}")
                save_voice_debug_audio(guild_id, speaker_name, pcm_bytes, audio16k, wake_probe=wake_probe, final_text="[FILLER IGNORE]", debug_meta=debug_meta, session_key=session_key, stage_label="drop")
                register_drop_reason(metrics, "filler_ignore", session_key=session_key, room_session_key=room_session_key, owner_user_id=owner_user_id, wake_probe_text=wake_probe)
                log_voice_stage(metrics, "짧은 필러 후보 조기 종료", extra=f"wake_probe_text={wake_probe!r}")
                log_voice_bottleneck_summary(metrics, label="voice_drop", extra="drop=filler_ignore", event_name="voice_drop_summary")
                return
            print(f"[FULL STT CONTINUE] reason=filler_ignore speaker={member.display_name} probe={wake_probe!r}")
            print(f"[FILLER IGNORE] speaker={member.display_name} probe={wake_probe!r}")
            log_voice_stage(metrics, "짧은 필러 후보지만 본문 STT 진행", extra=f"wake_probe_text={wake_probe!r}")

        if repetitive_noise_candidate:
            if not wake_detected:
                print(f"[FULL STT SKIP] reason=noise_text_ignore speaker={member.display_name} probe={wake_probe!r}")
                print(f"[NOISE TEXT IGNORE] speaker={member.display_name} probe={wake_probe!r}")
                save_voice_debug_audio(guild_id, speaker_name, pcm_bytes, audio16k, wake_probe=wake_probe, final_text="[NOISE TEXT IGNORE]", debug_meta=debug_meta, session_key=session_key, stage_label="drop")
                register_drop_reason(metrics, "noise_text_ignore", session_key=session_key, room_session_key=room_session_key, owner_user_id=owner_user_id, wake_probe_text=wake_probe)
                log_voice_stage(metrics, "반복 소음 후보 조기 종료", extra=f"wake_probe_text={wake_probe!r}")
                log_voice_bottleneck_summary(metrics, label="voice_drop", extra="drop=noise_text_ignore", event_name="voice_drop_summary")
                return
            print(f"[FULL STT CONTINUE] reason=noise_text_ignore speaker={member.display_name} probe={wake_probe!r}")
            print(f"[NOISE TEXT IGNORE] speaker={member.display_name} probe={wake_probe!r}")
            log_voice_stage(metrics, "반복 소음 후보지만 본문 STT 진행", extra=f"wake_probe_text={wake_probe!r}")

        if not wake_detected:
            print(f"[FULL STT CONTINUE] reason=wake_ignore speaker={member.display_name} probe={wake_probe!r}")
            if wake_probe:
                print(f"[WAKE IGNORE] {member.display_name}: {wake_probe!r}")
            if should_skip_full_stt_after_wake_probe(wake_detected=wake_detected, wake_probe=wake_probe, duration_sec=duration_sec):
                print(f"[FULL STT SKIP] reason=wake_probe_low_signal speaker={member.display_name} probe={wake_probe!r} sec={duration_sec:.2f}")
                save_voice_debug_audio(guild_id, speaker_name, pcm_bytes, audio16k, wake_probe=wake_probe, final_text="[WAKE PROBE SKIP]", debug_meta=debug_meta, session_key=session_key, stage_label="drop")
                register_drop_reason(metrics, "wake_probe_low_signal", session_key=session_key, room_session_key=room_session_key, owner_user_id=owner_user_id, wake_probe_text=wake_probe, wake_detected=wake_detected)
                log_voice_stage(metrics, "웨이크 프로브 기반 조기 종료", extra=f"wake_probe_text={wake_probe!r} sec={duration_sec:.2f}")
                return
            log_voice_stage(metrics, "웨이크 미검출이지만 본문 STT 진행", extra=f"wake_probe_text={wake_probe!r}")

    now_mono = time.monotonic()
    if guild_id in bot_speaking_guilds:
        register_drop_reason(metrics, "bot_is_speaking", session_key=session_key, room_session_key=room_session_key, owner_user_id=owner_user_id, wake_probe_text=wake_probe, wake_detected=wake_detected)
        log_voice_stage(metrics, "봇 재생 중 입력 무시", extra=f"speaker={member.display_name} wake_detected={wake_detected}")
        log_voice_bottleneck_summary(metrics, label="voice_drop", extra="drop=bot_is_speaking", event_name="voice_drop_summary")
        return
    if now_mono - last_bot_audio_end_at.get(guild_id, 0.0) < POST_TTS_IGNORE_SEC:
        register_drop_reason(metrics, "post_tts_ignore", session_key=session_key, room_session_key=room_session_key, owner_user_id=owner_user_id, wake_probe_text=wake_probe, wake_detected=wake_detected)
        log_voice_stage(metrics, "TTS 직후 입력 무시", extra=f"speaker={member.display_name} wake_detected={wake_detected}")
        log_voice_bottleneck_summary(metrics, label="voice_drop", extra="drop=post_tts_ignore", event_name="voice_drop_summary")
        return

    interrupt_meta = TtsInterruptMeta(
        active_speaker_match=active_speaker_user_id == member.id,
        wake_detected=wake_detected,
        vad_prob=voice_like_prob,
        audio_sec=duration_sec,
        rms_ok=body_rms >= VOICE_WAVEFORM_BODY_RMS_MIN,
        voice_like=voice_like_prob >= 0.45,
    )
    if should_interrupt_tts(interrupt_meta):
        await asyncio.sleep(TTS_INTERRUPT_DEBOUNCE_SEC)
        if guild_id in bot_speaking_guilds:
            register_drop_reason(metrics, "bot_is_speaking", session_key=session_key, room_session_key=room_session_key, owner_user_id=owner_user_id, wake_probe_text=wake_probe, wake_detected=wake_detected)
            log_voice_stage(metrics, "디바운스 후 봇 재생 중 입력 무시", extra=f"speaker={member.display_name} wake_detected={wake_detected}")
            log_voice_bottleneck_summary(metrics, label="voice_drop", extra="drop=bot_is_speaking", event_name="voice_drop_summary")
            return
        if time.monotonic() - last_bot_audio_end_at.get(guild_id, 0.0) < POST_TTS_IGNORE_SEC:
            register_drop_reason(metrics, "post_tts_ignore", session_key=session_key, room_session_key=room_session_key, owner_user_id=owner_user_id, wake_probe_text=wake_probe, wake_detected=wake_detected)
            log_voice_stage(metrics, "디바운스 후 TTS 직후 입력 무시", extra=f"speaker={member.display_name} wake_detected={wake_detected}")
            log_voice_bottleneck_summary(metrics, label="voice_drop", extra="drop=post_tts_ignore", event_name="voice_drop_summary")
            return
        await stop_active_tts_playback(guild_id, reason="qualified_user_audio")

    print(f"[FULL STT ENTER] speaker={member.display_name} sampling_rate={stt_sampling_rate} samples={audio16k.size} wake_detected={wake_detected}")
    log_voice_stage(metrics, "본문 STT 시작", extra=f"samples={audio16k.size}")
    stt_meta: dict | None = None
    partial_text = ""
    committed_partial_text = ""
    partial_audio = build_partial_stt_window(audio16k, sampling_rate=stt_sampling_rate)
    partial_min_samples = max(1, int(float(stt_sampling_rate) * 0.85))
    partial_should_run = partial_audio.size >= partial_min_samples
    if not partial_should_run:
        metrics.setdefault("meta", {})["partial_stt_skip_reason"] = "insufficient_audio"
    try:
        if partial_should_run:
            partial_text, committed_partial_text = await asyncio.to_thread(
                get_partial_transcript,
                session_key,
                audio16k,
                sampling_rate=stt_sampling_rate,
            )
        else:
            committed_partial_text = clean_text(session_committed_stt_text.get(session_key, ""))
        metrics.setdefault("meta", {}).update({
            "partial_stt_text": partial_text,
            "committed_stt_text": committed_partial_text,
        })
        if partial_text:
            print(f"[STT RESULT][partial] text={partial_text!r} committed={committed_partial_text!r}")
        speculative = speculate_from_committed_stt(committed_partial_text or partial_text, room_state_snapshot(room_session_key))
        if speculative is not None:
            remember_speculative_policy(session_key, speculative)
            metrics.setdefault("meta", {})["speculative_policy"] = dict(speculative.get("policy") or {})
    except Exception as e:
        print(f"[STT PARTIAL] {e}")

    try:
        primary_text = await asyncio.wait_for(
            asyncio.to_thread(transcribe_audio16k_sync, audio16k, VOICE_STT_MAX_NEW_TOKENS, sampling_rate=stt_sampling_rate, stage="full"),
            timeout=max(8.0, FULL_STT_TIMEOUT_SEC),
        )
    except Exception as e:
        print(f"[STT] {e}")
        log_voice_stage(metrics, "본문 STT 실패", extra=repr(e))
        return

    text = primary_text
    print(f"[STT RESULT][full-primary] text={primary_text!r}")
    if STT_FULL_RESCORING_ENABLED:
        log_voice_stage(metrics, "본문 STT 2차 rescoring 시작")
        try:
            rescore_text = await asyncio.to_thread(
                transcribe_audio16k_sync,
                audio16k,
                VOICE_STT_MAX_NEW_TOKENS + max(0, STT_FULL_RESCORE_EXTRA_TOKENS),
                sampling_rate=stt_sampling_rate,
                stage="full-rescore",
            )
            text, stt_meta = choose_full_stt_candidate(primary_text, rescore_text, wake_probe=wake_probe)
            print(
                f"[STT RESCORE] speaker={member.display_name} selected={stt_meta['selected']} primary_score={stt_meta['primary_score']:.3f} rescore_score={stt_meta['rescore_score']:.3f}"
            )
            print(f"[STT RESULT][full-rescore] text={rescore_text!r}")
            if stt_meta["selected"] == "rescore":
                print(f"[STT RESCORE PICK] primary={primary_text!r} -> rescore={rescore_text!r}")
            log_voice_stage(metrics, "본문 STT 2차 rescoring 완료", extra=f"selected={stt_meta['selected']}")
        except Exception as e:
            stt_meta = {"enabled": True, "selected": "primary", "rescore_error": repr(e), "primary_text": primary_text}
            print(f"⚠️ [STT RESCORE FAIL] {e}")
            log_voice_stage(metrics, "본문 STT 2차 rescoring 실패", extra=repr(e))
    else:
        stt_meta = {"enabled": False, "selected": "primary", "primary_text": primary_text}

    mark_turn_stage(metrics, "stt_full_done", event_name="stt_full_done", text_len=len(text))
    log_voice_stage(metrics, "본문 STT 완료", extra=f"text_len={len(text)}", key="stt_done")

    if not text:
        save_voice_debug_audio(guild_id, speaker_name, pcm_bytes, audio16k, wake_probe=wake_probe, final_text="[EMPTY STT]", debug_meta=debug_meta, stt_meta=stt_meta, session_key=session_key, stage_label="drop")
        log_voice_stage(metrics, "본문 STT 빈 결과")
        return

    corrected_text = apply_stt_post_corrections(text, wake_detected=wake_detected)
    if corrected_text != text:
        print(f"[STT CORRECT] raw={text!r} -> corrected={corrected_text!r}")
    text = corrected_text
    session_partial_stt_text[session_key] = clean_text(partial_text)
    committed_text = commit_stable_transcript(session_key, new_partial_text=text)
    transcript_result = build_transcript_result(
        wake_detected=wake_detected,
        wake_match_mode=wake_match_mode,
        wake_alias=wake_alias,
        probe_text=wake_probe,
        confirm_text=wake_confirm,
        reject_reason=wake_reject_reason,
        partial_text=clean_text(partial_text),
        committed_text=committed_text,
        final_text=clean_text(text),
        speaker_user_id=member.id,
        duration_sec=duration_sec,
    )
    speculative = speculate_from_committed_stt(committed_text, room_state_snapshot(room_session_key))
    if speculative is not None:
        remember_speculative_policy(session_key, speculative)
    if committed_text and len(clean_text(text)) >= len(committed_text):
        text = clean_text(text)
    print(f"[STT RESULT][full-final] text={transcript_result.final_text!r} committed={transcript_result.committed_text!r} wake_detected={transcript_result.wake_detected}")

    short_followup_candidate = is_short_followup_candidate(
        transcript_result.final_text,
        pcm_bytes,
        wake_detected=transcript_result.wake_detected,
        owner_followup_active=owner_followup_active,
    )
    if should_ignore_short_transcription(transcript_result.final_text, pcm_bytes, wake_detected=transcript_result.wake_detected):
        if short_followup_candidate:
            print(f"[SHORT FOLLOWUP CANDIDATE] text={transcript_result.final_text!r}")
            metrics.setdefault("meta", {})["short_followup_candidate"] = True
            save_voice_debug_audio(guild_id, speaker_name, pcm_bytes, audio16k, wake_probe=wake_probe, final_text=f"[SHORT FOLLOWUP CANDIDATE] {text}", debug_meta=debug_meta, stt_meta=stt_meta, session_key=session_key, stage_label="drop")
        else:
            print(f"[STT IGNORE] short_noise: {transcript_result.final_text!r}")
            save_voice_debug_audio(guild_id, speaker_name, pcm_bytes, audio16k, wake_probe=wake_probe, final_text=transcript_result.final_text, debug_meta=debug_meta, stt_meta=stt_meta, session_key=session_key, stage_label="drop")
            log_voice_stage(metrics, "짧은 STT 무시", extra=f"text={transcript_result.final_text!r}")
            return

    if not owner_followup_active:
        final_wake_alias = extract_leading_wake_alias(transcript_result.final_text)
        if final_wake_alias is None:
            wake_detected = False
            wake_match_mode = "rejected"
            wake_reject_reason = "full_text_veto"
            register_drop_reason(
                metrics,
                "full_text_veto",
                session_key=session_key,
                room_session_key=room_session_key,
                owner_user_id=owner_user_id,
                wake_probe_text=wake_probe,
                wake_confirm_text=wake_confirm,
                final_text=transcript_result.final_text,
            )
            save_voice_debug_audio(guild_id, speaker_name, pcm_bytes, audio16k, wake_probe=wake_probe, final_text=transcript_result.final_text, debug_meta=debug_meta, stt_meta=stt_meta, session_key=session_key, stage_label="drop")
            log_voice_stage(metrics, "최종 텍스트 veto", extra=f"wake_reject_reason={wake_reject_reason} text={transcript_result.final_text!r}")
            log_voice_bottleneck_summary(metrics, label="voice_drop", extra="drop=full_text_veto", event_name="voice_drop_summary")
            return
        wake_alias = final_wake_alias

    save_voice_debug_audio(guild_id, speaker_name, pcm_bytes, audio16k, wake_probe=wake_probe, final_text=transcript_result.final_text, debug_meta=debug_meta, stt_meta=stt_meta, session_key=session_key, stage_label="final")
    print(
        f"🎤 [{member.display_name}] wake_detected={transcript_result.wake_detected} wake_match_mode={transcript_result.wake_match_mode} wake_alias={transcript_result.wake_alias!r} "
        f"wake_probe_text={transcript_result.probe_text!r} wake_confirm_text={transcript_result.confirm_text!r} wake_reject_reason={transcript_result.reject_reason!r} text={transcript_result.final_text}"
    )

    update_room_speaker_activity(
        room_session_key,
        member.id,
        voiced_ms=voiced_ms,
        raw_seconds=raw_seconds,
        rms=body_rms,
        wake_detected=wake_detected,
    )
    active_speaker_user_id = pick_active_speaker(room_session_key)
    metrics.setdefault("meta", {}).update({"active_speaker_user_id": active_speaker_user_id})

    voice_segment = build_voice_segment(
        guild_id=guild_id,
        room_session_key=room_session_key,
        session_key=session_key,
        speaker_user_id=member.id,
        speaker_name=member.display_name,
        audio16k=audio16k,
        sampling_rate=stt_sampling_rate,
        duration_sec=duration_sec,
        segment_id=segment_id,
        owner_user_id=owner_user_id,
    )

    ok, reason, gate_mode = should_reply_to_voice(
        guild_id,
        transcript_result.final_text,
        wake_detected=transcript_result.wake_detected,
        wake_match_mode=transcript_result.wake_match_mode,
        session_key=voice_segment.session_key,
        room_session_key=voice_segment.room_session_key,
        user_id=voice_segment.speaker_user_id,
        active_speaker_user_id=active_speaker_user_id,
    )
    metrics.setdefault("meta", {}).update({
        "owner_user_id": owner_user_id,
        "reply_gate_passed_by": gate_mode if ok else None,
        "reply_gate_blocked_by": None if ok else gate_mode,
    })
    if not ok:
        print(f"[STT IGNORE] {reason}: {transcript_result.final_text!r}")
        register_drop_reason(metrics, reason, session_key=session_key, room_session_key=room_session_key, owner_user_id=owner_user_id, text=transcript_result.final_text)
        log_voice_stage(metrics, "응답 차단", extra=f"reason={reason} gate={gate_mode}")
        log_voice_bottleneck_summary(metrics, label="voice_drop", extra=f"drop={reason}", event_name="voice_drop_summary")
        return

    reset_session_bad_audio(session_key)
    session_last_stt_text[session_key] = transcript_result.final_text
    room_last_voice_reply_at[room_session_key] = time.monotonic()

    voice_reply = build_voice_reply_request(
        transcript=transcript_result,
        segment=voice_segment,
        gate_mode=gate_mode,
        session_topic_seed=session_topic_ids.get(session_key, ""),
        build_topic_id=build_topic_id,
    )
    log_voice_stage(metrics, "응답 게이트 통과", extra=f"gate={gate_mode} user_text={voice_reply.raw_user_text!r}")

    canned_wake_reply = "응, 왜 불렀어?"
    accepted_turn_id = start_new_turn(session_key, turn_id=turn_id)
    lifecycle = build_voice_reply_lifecycle(
        accepted_turn_id=accepted_turn_id,
        gate_mode=gate_mode,
        reply_in_progress=bool(room_state_snapshot(room_session_key).get("reply_in_progress")),
        active_conversation_awaiting_reply_sec=ACTIVE_CONVERSATION_AWAITING_REPLY_SEC,
        active_conversation_voice_sec=ACTIVE_CONVERSATION_VOICE_SEC,
        topic_id=voice_reply.topic_id,
        history_user_text=voice_reply.history_user_text,
    )
    session_partial_stt_text[session_key] = ""
    session_committed_stt_text[session_key] = ""
    partial_stt_cache.pop(session_key, None)
    set_room_owner(
        room_session_key,
        member.id,
        ttl_sec=lifecycle.owner_ttl_sec,
        reason=gate_mode,
        session_key=session_key,
        turn_id=accepted_turn_id,
        segment_id=segment_id,
    )
    update_session_state(
        session_key,
        user_id=member.id,
        speaker="user",
        ttl_sec=lifecycle.owner_ttl_sec,
        awaiting_user_reply=False,
        topic_id=lifecycle.topic_id,
        user_text=lifecycle.history_user_text,
    )
    metrics.setdefault("meta", {}).update({"topic_id": lifecycle.topic_id, "turn_id": accepted_turn_id, "owner_user_id": member.id})
    turn_scope = TurnScope(accepted_turn_id)
    replace_room_turn_scope(
        room_session_key,
        turn_scope,
        cancel_old=lifecycle.should_cancel_old_scope,
    )
    turn_task = _attach_current_task(turn_scope)
    vc = guild.voice_client
    lock = session_locks.setdefault(room_session_key, asyncio.Lock())

    if lock.locked():
        print(f"[VOICE WAIT] room={room_session_key} speaker={member.display_name} text={voice_reply.history_user_text!r}")
        log_voice_stage(metrics, "방 락 대기", extra=f"room={room_session_key}")

    set_room_reply_in_progress(room_session_key, True, owner_user_id=member.id)
    try:
        async with lock:
            log_voice_stage(metrics, "방 락 획득", extra=f"room={room_session_key}")
            vc = guild.voice_client
            if vc is None:
                return

            async def on_final_answer(answer_text: str) -> None:
                print(f"💬 [Evelyn] {visible_text(answer_text)}")

            try:
                if voice_reply.wake_only_turn:
                    answer = canned_wake_reply
                    log_voice_stage(metrics, "웨이크 전용 턴 canned reply", extra=f"answer={answer!r}")
                    if on_final_answer is not None:
                        await on_final_answer(answer)
                    await speak_answer(
                        vc,
                        answer,
                        turn_id=accepted_turn_id,
                        session_key=session_key,
                    )
                else:
                    answer = await ask_llm_and_speak_streaming(
                        vc,
                        voice_reply.prompt_user_text,
                        guild_id=guild_id,
                        on_final_answer=on_final_answer,
                        session_key=session_key,
                        room_key=room_key,
                        person_key=person_key,
                        session_memory_key=session_memory_key,
                        source="voice",
                        debug_text=voice_reply.history_user_text,
                        metrics=metrics,
                        turn_scope=turn_scope,
                    )
                log_voice_stage(metrics, "LLM/TTS 완료", extra=f"answer_len={len(answer)}")
            except Exception as e:
                print(f"❌ [LLM/TTS] {e}")
                return

            answer = clean_text(answer)
            if not answer:
                log_voice_stage(metrics, "최종 답변 비어있음")
                return

            plain_answer = strip_omnivoice_tags(answer)
            if not plain_answer:
                plain_answer = answer

            finalize_voice_reply_side_effects(
                guild_id=guild_id,
                member=member,
                session_key=session_key,
                room_session_key=room_session_key,
                room_key=room_key,
                person_key=person_key,
                session_memory_key=session_memory_key,
                voice_reply=voice_reply,
                plain_answer=plain_answer,
                metrics=metrics,
                turn_scope=turn_scope,
                accepted_turn_id=accepted_turn_id,
                segment_id=segment_id,
            )
            log_voice_stage(metrics, "voice_worker_turn 완료", extra=f"speaker={member.display_name} gate={gate_mode}")
    finally:
        current_scope = get_room_turn_scope(room_session_key)
        if current_scope is turn_scope or current_scope is None:
            set_room_reply_in_progress(room_session_key, False, owner_user_id=member.id)
        _detach_task(turn_scope, turn_task)
        clear_room_turn_scope(room_session_key, turn_scope)


# =========================================================
# 이벤트
# =========================================================
@bot.event
async def on_ready():
    print(f"로그인 완료: {bot.user}")
    ensure_voice_worker_started()
    try:
        await ensure_startup_components_ready()
    except Exception as e:
        print(f"[STARTUP] init_fail err={e!r}")
        raise
    for guild in bot.guilds:
        vc = guild.voice_client
        if isinstance(vc, EvelynVoiceClient):
            print(f"[VOICE READY] guild={guild.id} channel={getattr(getattr(vc, 'channel', None), 'name', None)} listening={vc.is_listening()}")
            try:
                if vc.channel is not None:
                    await ensure_listening_voice_client(guild, vc.channel)
            except Exception as e:
                print(f"[VOICE READY REARM FAIL] guild={guild.id} err={e!r}")
        elif vc is not None:
            print(f"[VOICE READY] guild={guild.id} unexpected_voice_client={type(vc)!r}")
        if AUTONOMY_ENABLED:
            try:
                await get_or_create_autonomy_engine(guild.id).start()
                print(f"[AUTONOMY] guild={guild.id} started")
            except Exception as e:
                print(f"[AUTONOMY] guild={guild.id} start_fail err={e!r}")


@bot.event
async def on_voice_state_update(member, before, after):
    if bot.user is None or member.id != bot.user.id:
        return
    guild = getattr(member, 'guild', None)
    if guild is None:
        return
    vc = guild.voice_client
    if not isinstance(vc, EvelynVoiceClient):
        return
    target_channel = after.channel or vc.channel
    if target_channel is None:
        return
    try:
        await ensure_listening_voice_client(guild, target_channel)
        print(f"[VOICE STATE REARM] guild={guild.id} channel={getattr(target_channel, 'name', None)} listening={vc.is_listening()}")
    except Exception as e:
        print(f"[VOICE STATE REARM FAIL] guild={guild.id} err={e!r}")


@bot.event
async def on_message(message: discord.Message):
    if message.author.bot:
        return

    if not message.guild:
        await bot.process_commands(message)
        return

    thread_id = getattr(message.channel, "id", None) if isinstance(getattr(message.channel, "parent", None), discord.TextChannel) else None
    session_key = make_text_session_key(message.guild.id, message.channel.id, message.author.id, thread_id=thread_id)
    room_key = make_room_memory_key("text", message.channel.id)
    person_key = make_person_memory_key(message.author.id)
    session_memory_key = make_session_memory_key(session_key, message.author.id)
    remember_session_followup_target(session_key, channel_id=message.channel.id, message_id=message.id)

    prefix = get_guild_command_prefix(message.guild.id)
    content_stripped = (message.content or "").lstrip()
    command_only_channel_ids = set(get_guild_command_only_channel_ids(message.guild.id))
    if content_stripped.startswith(prefix):
        await bot.process_commands(message)
        return
    if message.channel.id in command_only_channel_ids:
        return

    is_wake_word = contains_wake_word(message.content)
    is_reply = False
    is_active_session = is_session_active_for_user(session_key, message.author.id)

    if message.reference:
        try:
            replied_msg = await message.channel.fetch_message(message.reference.message_id)
            if replied_msg.author == bot.user:
                is_reply = True
        except Exception as e:
            print("답장 확인 오류:", repr(e))

    if not (is_wake_word or is_reply or is_active_session):
        log_turn_event(
            "turn_drop",
            turn_id=current_turn_id(session_key),
            segment_id=0,
            source="text",
            session_key=session_key,
            reason="text_gate_not_open",
            user_id=message.author.id,
        )
        await bot.process_commands(message)
        return

    user_text = strip_voice_wake_word(message.content) if is_wake_word else message.content.strip()
    if not user_text:
        user_text = "부르셨나요?"

    state_lock = session_locks.setdefault(session_key, asyncio.Lock())
    reply_slot_key = make_text_reply_slot_key(message.guild.id, message.channel.id, thread_id=thread_id)
    reply_lock = reply_slot_locks.setdefault(reply_slot_key, asyncio.Lock())

    if reply_lock.locked():
        await message.channel.send("⏳ 지금 다른 응답을 처리 중이야. 잠깐만.")
        await bot.process_commands(message)
        return

    async with state_lock:
        topic_id = build_topic_id(user_text, session_topic_ids.get(session_key, ""))
        turn_id = start_new_turn(session_key)
        update_session_state(
            session_key,
            user_id=message.author.id,
            speaker="user",
            awaiting_user_reply=False,
            topic_id=topic_id,
            user_text=user_text,
        )
        get_conversation_history(session_key=session_key, guild_id=message.guild.id)

    turn_scope = TurnScope(turn_id)
    replace_room_turn_scope(session_key, turn_scope)
    turn_task = _attach_current_task(turn_scope)
    vc = None
    answer = ""
    plain_answer = ""
    text_metrics: dict[str, Any] = {}
    text_delivery_plan: DeliveryPlan | None = None
    try:
        async with reply_lock:
            async with message.channel.typing():
                if AUTO_JOIN_VOICE:
                    vc = await ensure_voice_client(message)

                answer, _sent_message, text_metrics, text_delivery_plan = await stream_text_reply(
                    message.channel,
                    user_text,
                    guild_id=message.guild.id,
                    session_key=session_key,
                    turn_id=turn_id,
                    room_key=room_key,
                    person_key=person_key,
                    session_memory_key=session_memory_key,
                    source="text",
                    debug_text=user_text,
                    include_voice=vc is not None,
                    turn_scope=turn_scope,
                )
                plain_answer = strip_omnivoice_tags(answer)
                if not plain_answer:
                    plain_answer = answer

            if vc is not None and text_delivery_plan is not None and text_delivery_plan.should_play_voice:
                await execute_voice_delivery_plan(
                    vc,
                    text_delivery_plan,
                    metrics=text_metrics,
                    turn_id=turn_id or current_turn_id(session_key),
                    session_key=session_key,
                    turn_scope=turn_scope,
                )

        async with state_lock:
            session_speculative_policies.pop(session_key, None)
            append_history(session_key, user_text, plain_answer, guild_id=message.guild.id)
            runtime_mode = ((text_metrics.get("meta") or {}).get("runtime_mode")) or compute_runtime_mode(text_metrics)
            schedule_memory_update(
                message.guild.id,
                user_text,
                plain_answer,
                room_key=room_key,
                person_key=person_key,
                session_memory_key=session_memory_key,
                source="text",
                user_speaker=message.author.display_name,
                assistant_speaker="Evelyn",
                session_key=session_key,
                turn_scope=turn_scope,
                runtime_mode=runtime_mode,
            )
            search_requested = should_force_search_followup(
                message.guild.id,
                room_key=room_key,
                person_key=person_key,
                session_memory_key=session_memory_key,
                source="text",
            )
            schedule_search_followup(
                message.guild.id,
                session_key,
                user_text,
                plain_answer,
                room_key=room_key,
                person_key=person_key,
                session_memory_key=session_memory_key,
                channel_id=message.channel.id,
                reply_to_message_id=message.id,
                source="search-followup-text",
                force=search_requested,
                turn_scope=None,
                runtime_mode=runtime_mode,
            )

            awaiting_reply = bool("?" in plain_answer or "？" in plain_answer)
            mark_session_active(
                session_key,
                user_id=message.author.id,
                ttl_sec=ACTIVE_CONVERSATION_TEXT_QUESTION_SEC if awaiting_reply else ACTIVE_CONVERSATION_TEXT_SEC,
                speaker="assistant",
                awaiting_user_reply=awaiting_reply,
                topic_id=topic_id,
                answer_text=plain_answer,
                user_text=user_text,
            )

        log_voice_bottleneck_summary(
            text_metrics,
            label="text_turn",
            extra=f"chars={len(format_display_text(answer, session_key=session_key).strip())} voice_read={str(vc is not None).lower()}",
            event_name="text_turn_summary",
        )

    except Exception as e:
        print("전체 오류:", repr(e))
        await message.channel.send(f"❌ 오류 발생: {e}")
    finally:
        _detach_task(turn_scope, turn_task)
        clear_room_turn_scope(session_key, turn_scope)

    await bot.process_commands(message)


# =========================================================
# 명령어
# =========================================================
@bot.command(name="들어와", aliases=["join"])
async def join_voice(ctx):
    voice_state = getattr(ctx.author, "voice", None)
    if not voice_state or not voice_state.channel:
        await ctx.send("먼저 음성 채널에 들어가줘.")
        return

    try:
        vc = await ensure_listening_voice_client(ctx.guild, voice_state.channel)
        if vc is None:
            await ctx.send("❌ 음성 연결에 실패했어.")
            return
        await ctx.send(f"🔊 {voice_state.channel.name}에 들어왔어. 이제 듣고 말할게.")
    except Exception as e:
        print("음성 연결 오류:", repr(e))
        await ctx.send(f"❌ 음성 연결 실패: {e}")


@bot.command(name="다시들어와", aliases=["rejoin"])
async def rejoin_voice(ctx):
    channel = ctx.author.voice.channel if ctx.author.voice else None
    if channel is None:
        await ctx.send("먼저 음성 채널에 들어가줘.")
        return

    vc = ctx.guild.voice_client
    if vc is not None:
        try:
            if hasattr(vc, "stop_listening"):
                vc.stop_listening()
        except Exception:
            pass
        await vc.disconnect(force=True)

    try:
        new_vc = await ensure_listening_voice_client(ctx.guild, channel)
        if new_vc is None:
            await ctx.send("❌ 재연결 실패")
            return
        await ctx.send("🔄 다시 붙었어. 이제 계속 들을게.")
    except Exception as e:
        print("재연결 오류:", repr(e))
        await ctx.send(f"❌ 재연결 실패: {e}")


@bot.command(name="나가", aliases=["leave"])
async def leave_voice(ctx):
    vc = ctx.guild.voice_client
    if vc is None:
        await ctx.send("이미 나와 있어.")
        return

    try:
        if hasattr(vc, "stop_listening"):
            vc.stop_listening()
    except Exception:
        pass

    await vc.disconnect()
    await ctx.send("👋 나갔어.")


async def restart_bot_process() -> None:
    await asyncio.sleep(1.0)
    script_path = Path(__file__).resolve()
    project_dir = script_path.parent
    start_bot_bat = project_dir / "evelyn_core" / "start_bot.bat"
    start_bat = project_dir / "evelyn_core" / "start.bat"

    env = os.environ.copy()
    env.setdefault("STT_USE_RAW_48K", "false")

    if start_bot_bat.exists():
        subprocess.Popen(
            ["cmd.exe", "/c", str(start_bot_bat)],
            cwd=str(project_dir),
            env=env,
            close_fds=True,
            creationflags=getattr(subprocess, "CREATE_NEW_CONSOLE", 0),
        )
    elif start_bat.exists():
        subprocess.Popen(
            ["cmd.exe", "/c", str(start_bat)],
            cwd=str(project_dir),
            env=env,
            close_fds=True,
            creationflags=getattr(subprocess, "CREATE_NEW_CONSOLE", 0),
        )
    else:
        subprocess.Popen(
            [sys.executable, str(script_path)],
            cwd=str(project_dir),
            env=env,
            close_fds=True,
        )
    os._exit(0)


async def shutdown_bot_process() -> None:
    await asyncio.sleep(0.5)
    try:
        for guild in list(bot.guilds):
            vc = guild.voice_client
            if vc is None:
                continue
            try:
                if hasattr(vc, "stop_listening"):
                    vc.stop_listening()
            except Exception:
                pass
            try:
                await vc.disconnect(force=True)
            except Exception:
                pass
    finally:
        os._exit(0)


def is_control_command_authorized(ctx) -> bool:
    if getattr(ctx.author, "id", None) in ALLOWED_RESTART_USER_IDS:
        return True
    perms = getattr(ctx.author, "guild_permissions", None)
    return bool(perms and getattr(perms, "administrator", False))


async def handle_control_command_error(ctx, error):
    if isinstance(error, commands.CheckFailure):
        await ctx.send("이 명령은 허용된 Discord ID이거나 서버 관리자 권한이 있어야 쓸 수 있어.")
        return
    raise error


@bot.command(name="재시작", aliases=["restart"])
@commands.check(is_control_command_authorized)
async def restart_bot_command(ctx):
    await ctx.send("🔄 봇을 재시작할게. 잠깐만 기다려줘.")
    asyncio.create_task(restart_bot_process())


@restart_bot_command.error
async def restart_bot_command_error(ctx, error):
    await handle_control_command_error(ctx, error)


@bot.command(name="종료", aliases=["shutdown", "quit", "exit"])
@commands.check(is_control_command_authorized)
async def shutdown_bot_command(ctx):
    await ctx.send("⏹️ 봇을 종료할게.")
    asyncio.create_task(shutdown_bot_process())


@shutdown_bot_command.error
async def shutdown_bot_command_error(ctx, error):
    await handle_control_command_error(ctx, error)


@bot.command(name="상태", aliases=["status"])
async def status_command(ctx):
    guild = ctx.guild
    vc = guild.voice_client if guild else None
    voice_channel_name = getattr(getattr(vc, "channel", None), "name", None) or "없음"
    listening = bool(vc and hasattr(vc, "is_listening") and vc.is_listening())
    debug_audio_state = "on" if VOICE_DEBUG_SAVE_AUDIO else "off"
    opus_env_state = os.getenv("OPUS_ERROR_TO_SILENCE")
    try:
        from evelyn_voice.client import OPUS_ERROR_TO_SILENCE as OPUS_RUNTIME_VALUE
    except Exception:
        OPUS_RUNTIME_VALUE = None

    await ctx.send(
        "\n".join([
            f"모델: {MODEL_NAME}",
            f"라우터모델: {ROUTER_MODEL_NAME}",
            f"서브모델: {SUMMARY_MODEL_NAME}",
            f"STT: {STT_MODEL_NAME}",
            f"음성채널: {voice_channel_name}",
            f"리스닝: {'on' if listening else 'off'}",
            f"디버그 오디오 저장: {debug_audio_state}",
            f"OPUS_ERROR_TO_SILENCE(env): {opus_env_state if opus_env_state is not None else 'unset'}",
            f"OPUS_ERROR_TO_SILENCE(runtime): {OPUS_RUNTIME_VALUE}",
            f"VAD: {'on' if VAD_ENABLED else 'off'} ({VAD_PROVIDER})",
        ])
    )


@bot.command(name="접두사", aliases=["prefix"])
@commands.check(is_control_command_authorized)
async def set_guild_prefix(ctx, new_prefix: str | None = None):
    if ctx.guild is None:
        await ctx.send("이 명령은 길드에서만 쓸 수 있어.")
        return

    guild_id = ctx.guild.id
    current_prefix = get_guild_command_prefix(guild_id)

    if not new_prefix:
        await ctx.send(
            f"현재 이 길드 명령어 시작 부호는 `{current_prefix}` 야. 바꾸려면 `{current_prefix}접두사 ?` 처럼 써줘. 기본값으로 돌리려면 `{current_prefix}접두사 기본`"
        )
        return

    if new_prefix.lower() in {"기본", "default", "reset"}:
        saved_prefix = save_guild_command_prefix(guild_id, DEFAULT_COMMAND_PREFIX)
        await ctx.send(f"✅ 명령어 시작 부호를 기본값 `{saved_prefix}` 로 되돌렸어.")
        return

    try:
        saved_prefix = save_guild_command_prefix(guild_id, new_prefix)
    except ValueError as e:
        await ctx.send(f"❌ {e}")
        return

    await ctx.send(f"✅ 이 길드 명령어 시작 부호를 `{saved_prefix}` 로 저장했어. 이제 `{saved_prefix}초기화`, `{saved_prefix}들어와` 처럼 쓰면 돼.")


@set_guild_prefix.error
async def set_guild_prefix_error(ctx, error):
    await handle_control_command_error(ctx, error)


@bot.command(name="자율시작", aliases=["autonomy-on"])
async def autonomy_start_command(ctx):
    if ctx.guild is None:
        await ctx.send("이 명령은 길드에서만 쓸 수 있어.")
        return
    try:
        await get_or_create_autonomy_engine(ctx.guild.id).start()
        await ctx.send("🤖 자율 행동 루프를 시작했어.")
    except Exception as e:
        await ctx.send(f"❌ 자율 행동 시작 실패: {e}")


@bot.command(name="자율정지", aliases=["autonomy-off"])
async def autonomy_stop_command(ctx):
    if ctx.guild is None:
        await ctx.send("이 명령은 길드에서만 쓸 수 있어.")
        return
    engine = autonomy_engines.get(ctx.guild.id)
    if engine is None:
        await ctx.send("이미 자율 행동이 꺼져 있어.")
        return
    try:
        await engine.stop()
        await ctx.send("🛑 자율 행동 루프를 멈췄어.")
    except Exception as e:
        await ctx.send(f"❌ 자율 행동 정지 실패: {e}")


@bot.command(name="자율상태", aliases=["autonomy-status"])
async def autonomy_status_command(ctx):
    if ctx.guild is None:
        await ctx.send("이 명령은 길드에서만 쓸 수 있어.")
        return
    engine = autonomy_engines.get(ctx.guild.id)
    if engine is None:
        await ctx.send("자율 행동 엔진이 아직 만들어지지 않았어.")
        return
    state = engine.state
    goal = state.current_goal.summary if state.current_goal else "없음"
    plan = state.current_plan.summary if state.current_plan else "없음"
    allowed = ", ".join(state.allowed_actions[:6])
    if len(state.allowed_actions) > 6:
        allowed += ", ..."
    router = get_routed_autonomy_executor(ctx.guild.id)
    minecraft_enabled = bool(router and router.is_domain_enabled("minecraft"))
    await ctx.send(
        f"🤖 자율상태\n- status: {state.status}\n- safety: {state.safety_mode}\n- goal: {goal}\n- plan: {plan}\n- failures: {state.failure_count}\n- last_error: {state.last_error or '없음'}\n- minecraft_autonomy: {'on' if minecraft_enabled else 'off'}\n- allowed: {allowed or '없음'}"
    )


def _mark_text_session_from_command(ctx, user_text: str, answer_text: str, *, awaiting_user_reply: bool = False) -> None:
    if ctx.guild is None:
        return
    thread_id = getattr(ctx.channel, "id", None) if isinstance(getattr(ctx.channel, "parent", None), discord.TextChannel) else None
    session_key = make_text_session_key(ctx.guild.id, ctx.channel.id, ctx.author.id, thread_id=thread_id)
    remember_session_followup_target(session_key, channel_id=ctx.channel.id, message_id=getattr(ctx.message, "id", None))
    append_history(session_key, user_text, answer_text, guild_id=ctx.guild.id)
    mark_session_active(
        session_key,
        user_id=ctx.author.id,
        ttl_sec=ACTIVE_CONVERSATION_TEXT_QUESTION_SEC if awaiting_user_reply else ACTIVE_CONVERSATION_TEXT_SEC,
        speaker="assistant",
        awaiting_user_reply=awaiting_user_reply,
        topic_id=build_topic_id(user_text, answer_text),
        answer_text=answer_text,
        user_text=user_text,
    )


@bot.command(name="마크접속", aliases=["mc-connect", "minecraft-connect"])
async def minecraft_connect_command(ctx):
    if ctx.guild is None:
        await ctx.send("이 명령은 길드에서만 쓸 수 있어.")
        return
    try:
        observed = await enable_minecraft_mode(ctx.guild.id)
        connected = bool(observed.get("connected") or observed.get("active") or observed.get("position"))
        target = f"{observed.get('position')}" if observed.get("position") else "위치 미확인"
        stage = clean_text(str(observed.get("objective_stage") or "")) or "unknown"
        goal = clean_text(str(observed.get("objective_goal") or "")) or "progress_to_diamond"
        last_error = clean_text(str(observed.get("last_error") or observed.get("wait_last_error") or ""))
        if connected:
            reply_text = "✅ Voyager 기반 마인크래프트 자율 모드 시작 완료." + f"\n- goal: {goal}\n- stage: {stage}\n- position: {target}"
        else:
            detail = f" last_error={last_error}" if last_error else ""
            reply_text = "❌ 마인크래프트 접속 실패: Voyager 서비스는 올라왔지만 게임 연결 확인에 실패했어." + detail
        await ctx.send(reply_text)
        _mark_text_session_from_command(ctx, ctx.message.content or "마크접속", reply_text)
    except Exception as e:
        reply_text = f"❌ 마인크래프트 접속 실패: {e}"
        await ctx.send(reply_text)
        _mark_text_session_from_command(ctx, ctx.message.content or "마크접속", reply_text)


@bot.command(name="마크종료", aliases=["mc-disconnect", "minecraft-disconnect"])
async def minecraft_disconnect_command(ctx):
    if ctx.guild is None:
        await ctx.send("이 명령은 길드에서만 쓸 수 있어.")
        return
    try:
        await disable_minecraft_mode(ctx.guild.id)
        reply_text = "🛑 Voyager 기반 마인크래프트 자율 모드를 중지했어."
        await ctx.send(reply_text)
        _mark_text_session_from_command(ctx, ctx.message.content or "마크종료", reply_text)
    except Exception as e:
        reply_text = f"❌ 마인크래프트 연결 종료 실패: {e}"
        await ctx.send(reply_text)
        _mark_text_session_from_command(ctx, ctx.message.content or "마크종료", reply_text)


@bot.command(name="마크상태", aliases=["mc-status", "minecraft-status"])
async def minecraft_status_command(ctx):
    if ctx.guild is None:
        await ctx.send("이 명령은 길드에서만 쓸 수 있어.")
        return
    client = get_minecraft_client()
    try:
        status = await client.status()
        observed = status.get("observation") if isinstance(status.get("observation"), dict) else {}
        evaluation = status.get("voyager_evaluation") if isinstance(status.get("voyager_evaluation"), dict) else {}
        position = observed.get("position") if isinstance(observed, dict) else None
        hunger = observed.get("hunger") if isinstance(observed, dict) else None
        health = observed.get("health") if isinstance(observed, dict) else None
        hostiles = observed.get("hostiles_nearby") if isinstance(observed, dict) else None
        tech_tree = evaluation.get("tech_tree") if isinstance(evaluation.get("tech_tree"), dict) else {}
        skill_library = evaluation.get("skill_library") if isinstance(evaluation.get("skill_library"), dict) else {}
        reply_text = (
            "⛏️ 마인크래프트 상태\n"
            f"- service: voyager\n"
            f"- running: {'on' if status.get('running') else 'off'}\n"
            f"- connected: {'on' if status.get('connected') else 'off'}\n"
            f"- goal: {status.get('goal') or 'none'}\n"
            f"- stage: {status.get('stage') or 'unknown'}\n"
            f"- task: {status.get('current_task') or 'none'}\n"
            f"- task_stage: {status.get('current_task_stage') or 'unknown'}\n"
            f"- progress: {status.get('last_progress_message') or 'none'}\n"
            f"- eval_goal: {evaluation.get('goal') or status.get('goal') or 'none'}\n"
            f"- unique_items: {evaluation.get('unique_item_count') if evaluation.get('unique_item_count') is not None else 'unknown'}\n"
            f"- tech_tree: {tech_tree.get('highest_unlocked') or 'unknown'}\n"
            f"- travel_distance: {evaluation.get('travel_distance_blocks') if evaluation.get('travel_distance_blocks') is not None else 'unknown'}\n"
            f"- skill_library: {skill_library.get('size') if skill_library.get('size') is not None else 'unknown'}\n"
            f"- health: {health if health is not None else 'unknown'}\n"
            f"- hunger: {hunger if hunger is not None else 'unknown'}\n"
            f"- hostiles: {hostiles if hostiles is not None else 'unknown'}\n"
            f"- position: {position if position is not None else 'unknown'}"
        )
        await ctx.send(reply_text)
        _mark_text_session_from_command(ctx, ctx.message.content or "마크상태", reply_text)
    except Exception as e:
        reply_text = f"❌ 마인크래프트 상태 확인 실패: {e}"
        await ctx.send(reply_text)
        _mark_text_session_from_command(ctx, ctx.message.content or "마크상태", reply_text)


@bot.command(name="마크목표", aliases=["mc-goal", "minecraft-goal"])
async def minecraft_goal_command(ctx, *, goal: str | None = None):
    if ctx.guild is None:
        await ctx.send("이 명령은 길드에서만 쓸 수 있어.")
        return
    goal_text = clean_text(str(goal or ""))
    if not goal_text:
        reply_text = "목표를 같이 적어줘. 예: 마크목표 diamond 또는 마크목표 iron_pickaxe"
        await ctx.send(reply_text)
        _mark_text_session_from_command(ctx, ctx.message.content or "마크목표", reply_text)
        return
    client = get_minecraft_client()
    try:
        status = await client.set_goal(goal_text)
        reply_text = f"🎯 마인크래프트 목표를 바꿨어.\n- goal: {goal_text}\n- stage: {status.get('stage') or 'unknown'}"
        await ctx.send(reply_text)
        _mark_text_session_from_command(ctx, ctx.message.content or "마크목표", reply_text)
    except Exception as e:
        reply_text = f"❌ 마인크래프트 목표 변경 실패: {e}"
        await ctx.send(reply_text)
        _mark_text_session_from_command(ctx, ctx.message.content or "마크목표", reply_text)



@bot.command(name="관찰채널", aliases=["observe-channel"])
@commands.check(is_control_command_authorized)
async def observe_channel_command(ctx, action: str | None = None, channel: discord.TextChannel | None = None):
    if ctx.guild is None:
        await ctx.send("이 명령은 길드에서만 쓸 수 있어.")
        return
    action = clean_text(str(action or "목록")).lower()
    current = get_guild_observe_channel_ids(ctx.guild.id)
    if action in {"목록", "list"}:
        names = []
        for channel_id in current:
            target = ctx.guild.get_channel(channel_id)
            names.append(target.mention if target is not None else f"#{channel_id}")
        await ctx.send("👀 관찰채널: " + (", ".join(names) if names else "없음"))
        return
    if channel is None:
        await ctx.send("채널을 같이 지정해줘. 예: `!관찰채널 추가 #general`")
        return
    if action in {"추가", "add"}:
        updated = add_guild_channel_setting(ctx.guild.id, "observe_channel_ids", channel.id)
        await ctx.send(f"✅ 관찰채널에 {channel.mention} 추가했어. (총 {len(updated)}개)")
        return
    if action in {"제거", "remove", "삭제"}:
        updated = remove_guild_channel_setting(ctx.guild.id, "observe_channel_ids", channel.id)
        await ctx.send(f"🗑️ 관찰채널에서 {channel.mention} 뺐어. (총 {len(updated)}개)")
        return
    await ctx.send("사용법: `!관찰채널 목록` / `!관찰채널 추가 #채널` / `!관찰채널 제거 #채널`")


@observe_channel_command.error
async def observe_channel_command_error(ctx, error):
    await handle_control_command_error(ctx, error)


@bot.command(name="명령채널", aliases=["command-channel"])
@commands.check(is_control_command_authorized)
async def command_channel_command(ctx, action: str | None = None, channel: discord.TextChannel | None = None):
    if ctx.guild is None:
        await ctx.send("이 명령은 길드에서만 쓸 수 있어.")
        return
    action = clean_text(str(action or "목록")).lower()
    current = get_guild_command_only_channel_ids(ctx.guild.id)
    if action in {"목록", "list"}:
        names = []
        for channel_id in current:
            target = ctx.guild.get_channel(channel_id)
            names.append(target.mention if target is not None else f"#{channel_id}")
        await ctx.send("🧭 명령채널: " + (", ".join(names) if names else "없음"))
        return
    if channel is None:
        await ctx.send("채널을 같이 지정해줘. 예: `!명령채널 추가 #bot-control`")
        return
    if action in {"추가", "add"}:
        updated = add_guild_channel_setting(ctx.guild.id, "command_only_channel_ids", channel.id)
        await ctx.send(f"✅ 명령채널에 {channel.mention} 추가했어. 이제 여기선 명령어만 읽어.")
        return
    if action in {"제거", "remove", "삭제"}:
        updated = remove_guild_channel_setting(ctx.guild.id, "command_only_channel_ids", channel.id)
        await ctx.send(f"🗑️ 명령채널에서 {channel.mention} 뺐어. (총 {len(updated)}개)")
        return
    await ctx.send("사용법: `!명령채널 목록` / `!명령채널 추가 #채널` / `!명령채널 제거 #채널`")


@command_channel_command.error
async def command_channel_command_error(ctx, error):
    await handle_control_command_error(ctx, error)


@bot.command(name="도움말", aliases=["help"])
async def help_command(ctx):
    prefix = get_guild_command_prefix(ctx.guild.id if ctx.guild else None)
    lines = [
        "📘 Evelyn 명령어",
        f"- {prefix}들어와 / {prefix}다시들어와 / {prefix}나가",
        f"- {prefix}상태 / {prefix}접두사",
        f"- {prefix}자율시작 / {prefix}자율정지 / {prefix}자율상태",
        f"- {prefix}관찰채널 목록|추가 #채널|제거 #채널",
        f"- {prefix}명령채널 목록|추가 #채널|제거 #채널",
        f"- {prefix}초기화",
    ]
    if is_control_command_authorized(ctx):
        lines.append(f"- {prefix}재시작 / {prefix}종료")
    await ctx.send("\n".join(lines))


@bot.command(name="초기화", aliases=["reset"])
@commands.check(is_control_command_authorized)
async def reset_guild_memory(ctx):
    if ctx.guild is None:
        await ctx.send("이 명령은 길드에서만 쓸 수 있어.")
        return

    guild_id = ctx.guild.id
    memory_dir = MEMORY_ROOT / f"guild_{guild_id}"
    current_prefix = get_guild_command_prefix(guild_id)

    reset_guild_runtime_state(guild_id)
    if memory_dir.exists():
        shutil.rmtree(memory_dir)

    await ctx.send(f"🧹 {ctx.guild.name} 메모리와 대화 히스토리를 이 길드만 초기화했어. 명령어 시작 부호 `{current_prefix}` 설정은 유지했어.")


@reset_guild_memory.error
async def reset_guild_memory_error(ctx, error):
    await handle_control_command_error(ctx, error)


# =========================================================
# 실행
# =========================================================
if not DISCORD_BOT_TOKEN:
    raise RuntimeError("DISCORD_BOT_TOKEN 환경변수가 설정되지 않았습니다.")

acquire_instance_lock()
bot.run(DISCORD_BOT_TOKEN)
