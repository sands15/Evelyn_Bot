import atexit
import builtins
import contextlib
import hashlib
import html
import json
import logging
import math
import os
import re
import shutil
import subprocess
import sys
import threading
import time
import asyncio
import uuid
import wave
import zipfile
from dataclasses import dataclass, field, replace
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
from aiohttp import web
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
from evelyn_core.memory_writebehind import (
    mark_memory_writer_status,
    memory_writebehind_task_key,
    run_memory_writebehind_steps,
    should_replace_existing_memory_task,
)
from evelyn_core.minecraft_runtime_snapshot import attach_minecraft_runtime_snapshot, minecraft_runtime_status_fields
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
from evelyn_core.context_pipeline import (
    ContextBuilder,
    ContextPolicy,
    build_basic_context_packet,
    build_context_policy_for_turn,
    build_conversation_state_context,
    build_memory_writer_decision,
    build_minecraft_skill_context,
    build_runtime_state_context,
    build_skill_context_hint,
    build_vision_context_hint,
)
from evelyn_core.skills import SkillContext, SkillResult, skill_registry
from evelyn_core.skills.routing import (
    build_chat_messages,
    build_main_llm_payload,
    build_route_decision_from_state,
    decode_sse_stream_line,
    extract_main_llm_answer_from_choice,
    should_await_user_reply_for_route,
)
from evelyn_core.local_mic import (
    LocalMicCaptureService,
    resolve_local_mic_target,
    serialize_local_mic_target,
    should_route_discord_user_to_local_mic,
)
from evelyn_core.control_page_windows import (
    CONTROL_PAGE_WINDOW_SPECS,
    control_page_window_choices_text,
    resolve_control_page_window_key,
)
from evelyn_core.page_urls import resolve_public_page_url
from evelyn_core.query_intents import (
    answer_current_datetime_query,
    should_force_search_query,
)
from evelyn_core.assistant_contracts import (
    TtsSynthRequest,
    TtsSynthResult,
)
from evelyn_core.tts_playback import (
    CachedWaveAudioSource,
    ChunkWindow,
    OmniVoicePCMStream,
    PreparedPlaybackStarter,
    PreparedTtsPlaybackQueue,
    QueuedAudioSource,
    StreamingVoiceDelivery,
    SpeechChunker,
    TTSQueueSink,
    TtsPlaybackRegistry,
    TtsPlaybackTracker,
    add_omnivoice_stream_contract,
    clear_tts_playback_tracking,
    cleanup_tts_stream_tasks,
    configure_tts_playback_logging,
    drain_prepared_tts_playback,
    finish_tts_playback_tracking,
    get_tracked_tts_playback,
    is_tracked_tts_playback_active,
    mark_tts_playback_summary_state,
    mark_tts_speaking,
    play_audio_source,
    prefetch_tts_sources,
    resolve_cached_tts_audio_path,
    split_tts_sentences,
    start_tts_playback_tracking,
    stop_tracked_tts_playback,
    tracked_tts_playback_count,
    tracked_tts_playback_guild_ids,
    tts_input_suppression_reason,
    update_tts_playback_tracking,
)
from evelyn_core.turn_trace import TURN_SUMMARY_EVENTS, build_turn_summary_payload
from evelyn_core.voice_orchestration import (
    VoiceTurnOrchestrator,
    VoiceTurnOrchestratorDeps,
    VoiceTurnRequest,
    VoiceTurnRouteContext,
    VoiceTranscriptReplyContext,
    VoiceTranscriptReplyDeps,
    apply_voice_ingress_dequeue_debug_meta,
    build_rejected_voice_turn,
    build_voice_ingress_item,
    clear_room_owner as orchestration_clear_room_owner,
    enqueue_voice_ingress_item,
    evaluate_voice_ingress_dequeue,
    is_room_owner_active as orchestration_is_room_owner_active,
    process_voice_reply_from_transcript_context,
    room_state_snapshot as orchestration_room_state_snapshot,
    set_room_owner as orchestration_set_room_owner,
    set_room_reply_in_progress as orchestration_set_room_reply_in_progress,
)
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
    classify_dialogue_turn,
    route_decision_policy_dict,
)
from evelyn_voice import EvelynVoiceClient


TURN_TRACE_JSON_LOG = os.getenv("TURN_TRACE_JSON_LOG", "true").lower() == "true"
VOICE_CONSOLE_ONLY_STT_AND_REPLY = os.getenv("VOICE_CONSOLE_ONLY_STT_AND_REPLY", "true").lower() == "true"
VOICE_BOTTLENECK_LOGS = os.getenv("VOICE_BOTTLENECK_LOGS", "true").lower() == "true"
VOICE_TRACE_ALL_EVENTS = os.getenv("VOICE_TRACE_ALL_EVENTS", "true").lower() == "true"
TURN_TRACE_LOG_DIR = Path(os.getenv("TURN_TRACE_LOG_DIR", str(PROJECT_ROOT / "logs" / "turn_trace")))
VOICE_DEBUG_SAVE_AUDIO = os.getenv("VOICE_DEBUG_SAVE_AUDIO", "true").lower() == "true"
VOICE_DEBUG_AUDIO_DIR = os.getenv("VOICE_DEBUG_AUDIO_DIR", "debug_audio")
VOICE_DEBUG_MAX_FILES_PER_GUILD = int(os.getenv("VOICE_DEBUG_MAX_FILES_PER_GUILD", "200"))
WAKE_STT_TIMEOUT_SEC = float(os.getenv("WAKE_STT_TIMEOUT_SEC", "20"))
FULL_STT_TIMEOUT_SEC = float(os.getenv("FULL_STT_TIMEOUT_SEC", "30"))
VOICE_INGRESS_QUEUE_MAX = max(1, int(os.getenv("VOICE_INGRESS_QUEUE_MAX", "16")))
VOICE_INGRESS_MAX_AGE_SEC = float(os.getenv("VOICE_INGRESS_MAX_AGE_SEC", "8.0"))
VOICE_INGRESS_DROP_OLDEST_ON_FULL = os.getenv("VOICE_INGRESS_DROP_OLDEST_ON_FULL", "true").lower() in {"1", "true", "yes", "on"}
STT_FULL_RESCORING_TIMEOUT_SEC = float(os.getenv("STT_FULL_RESCORING_TIMEOUT_SEC", "12"))
STT_FULL_RESCORING_MIN_AUDIO_SEC = float(os.getenv("STT_FULL_RESCORING_MIN_AUDIO_SEC", "2.0"))
STT_FULL_RESCORING_MIN_TEXT_LEN = int(os.getenv("STT_FULL_RESCORING_MIN_TEXT_LEN", "8"))
STT_COOLDOWN_AFTER_TIMEOUT_SEC = float(os.getenv("STT_COOLDOWN_AFTER_TIMEOUT_SEC", "6.0"))
VOICE_REJOIN_ON_READY = os.getenv("VOICE_REJOIN_ON_READY", "true").lower() in {"1", "true", "yes", "on"}
VOICE_LAST_CHANNEL_STATE_FILE = os.getenv(
    "VOICE_LAST_CHANNEL_STATE_FILE",
    str(RUNTIME_ARTIFACTS_ROOT / "state" / "voice_last_channel.json"),
)
VOICE_LIVE_RECENT_SEC = float(os.getenv("VOICE_LIVE_RECENT_SEC", "90.0"))
TTS_FIRST_CHUNK_MIN_CHARS = int(os.getenv("TTS_FIRST_CHUNK_MIN_CHARS", "14"))
TTS_FIRST_CHUNK_TARGET_CHARS = int(os.getenv("TTS_FIRST_CHUNK_TARGET_CHARS", "22"))
TTS_FIRST_CHUNK_MAX_CHARS = int(os.getenv("TTS_FIRST_CHUNK_MAX_CHARS", "38"))
TTS_NEXT_CHUNK_MIN_CHARS = int(os.getenv("TTS_NEXT_CHUNK_MIN_CHARS", "18"))
TTS_NEXT_CHUNK_TARGET_CHARS = int(os.getenv("TTS_NEXT_CHUNK_TARGET_CHARS", "38"))
TTS_NEXT_CHUNK_MAX_CHARS = int(os.getenv("TTS_NEXT_CHUNK_MAX_CHARS", "78"))
TTS_INTERRUPT_DEBOUNCE_SEC = float(os.getenv("TTS_INTERRUPT_DEBOUNCE_SEC", "0.18"))
CACHED_AUDIO_ENABLED = os.getenv("EVELYN_CACHED_AUDIO_ENABLED", "true").lower() in {"1", "true", "yes", "on"}
CACHED_AUDIO_DIR = Path(os.getenv("EVELYN_CACHED_AUDIO_DIR", str(PROJECT_ROOT / "assets" / "audio_cache")))
CANNED_WAKE_REPLY_TEXT = os.getenv("EVELYN_CANNED_WAKE_REPLY_TEXT", "응, 왜 불렀어?")
CANNED_WAKE_REPLY_AUDIO = Path(
    os.getenv(
        "EVELYN_CANNED_WAKE_REPLY_AUDIO",
        str(CACHED_AUDIO_DIR / "wake_call_default.wav"),
    )
)
DEBUG_WRITE_QUEUE_MAX = int(os.getenv("DEBUG_WRITE_QUEUE_MAX", "128"))
MIN_EDIT_INTERVAL_MS = int(os.getenv("MIN_EDIT_INTERVAL_MS", "300"))
MIN_DELTA_CHARS = int(os.getenv("MIN_DELTA_CHARS", "24"))
MAX_HOLD_MS = int(os.getenv("MAX_HOLD_MS", "900"))
ROUTER_LLM_URL = globals().get("ROUTER_LLM_URL", os.getenv("ROUTER_LLM_URL", "http://127.0.0.1:9822/v1/chat/completions"))
ROUTER_MODEL_NAME = globals().get("ROUTER_MODEL_NAME", os.getenv("ROUTER_MODEL_NAME", "gemma-4-E2B-it-UD-Q6_K_XL.gguf"))
ROUTER_LLM_ENABLED = globals().get("ROUTER_LLM_ENABLED", os.getenv("ROUTER_LLM_ENABLED", "true").lower() in {"1", "true", "yes", "on"})
ROUTER_ROUTE_MAX_TOKENS = int(globals().get("ROUTER_ROUTE_MAX_TOKENS", os.getenv("ROUTER_ROUTE_MAX_TOKENS", "220")))
ROUTER_ROUTE_TIMEOUT_SEC = float(globals().get("ROUTER_ROUTE_TIMEOUT_SEC", os.getenv("ROUTER_ROUTE_TIMEOUT_SEC", "8")))
ODYSSEY_CAPABILITY_JSON_DIR = Path(os.getenv(
    "ODYSSEY_CAPABILITY_JSON_DIR",
    r"C:\Users\Admin\.openclaw\workspace\research\odyssey\MC-Comprehensive-Skill-Library\json",
))
CONTEXT_PIPELINE_BENCHMARK_LOG = Path(os.getenv(
    "CONTEXT_PIPELINE_BENCHMARK_LOG",
    str(RUNTIME_ARTIFACTS_ROOT / "benchmarks" / "context_pipeline_benchmarks.jsonl"),
))
MEMORY_WRITEBEHIND_STATUS_LOG = Path(os.getenv(
    "MEMORY_WRITEBEHIND_STATUS_LOG",
    str(RUNTIME_ARTIFACTS_ROOT / "memory" / "writebehind_status.jsonl"),
))
CONTROL_PAGE_ENABLED = os.getenv("CONTROL_PAGE_ENABLED", "true").lower() == "true"
CONTROL_PAGE_HOST = os.getenv("CONTROL_PAGE_HOST", "127.0.0.1")
CONTROL_PAGE_PORT = int(os.getenv("CONTROL_PAGE_PORT", "8799"))
CONTROL_PAGE_CHAT_LOG_LIMIT = int(os.getenv("CONTROL_PAGE_CHAT_LOG_LIMIT", "40"))
CONTROL_PAGE_DOCS_DIR = PROJECT_ROOT / "docs"
CONTROL_PAGE_ASSETS_DIR = CONTROL_PAGE_DOCS_DIR / "assets"
CONTROL_PAGE_MINECRAFT_ICON_ROUTE = "/api/control-page/minecraft-item-icon"
CONTROL_PAGE_MINECRAFT_CACHE_REFRESH_SEC = float(os.getenv("CONTROL_PAGE_MINECRAFT_CACHE_REFRESH_SEC", "1.0"))
CONTROL_PAGE_MINECRAFT_CACHE_MAX_STALE_SEC = float(os.getenv("CONTROL_PAGE_MINECRAFT_CACHE_MAX_STALE_SEC", "20.0"))
CONTROL_PAGE_MINECRAFT_SNAPSHOT_TIMEOUT_SEC = float(os.getenv("CONTROL_PAGE_MINECRAFT_SNAPSHOT_TIMEOUT_SEC", "2.5"))
CONTROL_PAGE_RUNTIME_CACHE_REFRESH_SEC = float(os.getenv("CONTROL_PAGE_RUNTIME_CACHE_REFRESH_SEC", "2.0"))
RUNTIME_STATUS_CONTEXT_ENABLED = os.getenv("RUNTIME_STATUS_CONTEXT_ENABLED", "true").lower() in {"1", "true", "yes", "on"}
RUNTIME_STATUS_CONTEXT_REFRESH_SEC = float(os.getenv("RUNTIME_STATUS_CONTEXT_REFRESH_SEC", "4.0"))
RUNTIME_STATUS_CONTEXT_CONNECT_TIMEOUT_SEC = float(os.getenv("RUNTIME_STATUS_CONTEXT_CONNECT_TIMEOUT_SEC", "0.18"))
RUNTIME_STATUS_CONTEXT_MAX_ERROR_CHARS = int(os.getenv("RUNTIME_STATUS_CONTEXT_MAX_ERROR_CHARS", "160"))
control_page_minecraft_item_icon_cache: dict[str, bytes | None] = {}
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
turn_trace_file_lock = threading.Lock()


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

SYSTEM_PROMPT = f"""
너는 Evelyn. 한국어로 친구처럼 짧게 반말한다.
비서/상담원 말투, 존댓말 대기문, 이모지는 쓰지 않는다.
음성 대화는 보통 1~3문장. 필요한 말만 한다.
불확실하면 지어내지 말고 솔직히 말한다.
필요할 때만 맨 앞에 [찾기] [질문] [대기] [답변] 중 하나를 붙인다.
최종 답변만 말하고 생각 과정은 말하지 않는다.
{OMNIVOICE_TAG_GUIDANCE}
태그를 빼도 문장이 성립해야 한다.
내부 메모나 sub handoff 문장을 사용자 말로 오해하지 않는다.
""".strip()

session_locks: dict[str, asyncio.Lock] = {}
reply_slot_locks: dict[str, asyncio.Lock] = {}
tts_lock = asyncio.Lock()
tts_playback_tracker = TtsPlaybackTracker()
active_tts_playbacks = tts_playback_tracker.registry
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
last_bot_audio_end_at = tts_playback_tracker.last_audio_end_at
bot_speaking_guilds = tts_playback_tracker.speaking_guilds
memory_locks: dict[int, asyncio.Lock] = {}
cognitive_locks: dict[int, asyncio.Lock] = {}
background_cognitive_tasks: dict[str, asyncio.Task] = {}
background_memory_tasks: dict[str, asyncio.Task] = {}
background_memory_vault_tasks: dict[int, asyncio.Task] = {}
memory_vault_last_maintenance_at: dict[int, float] = {}
background_search_tasks: dict[str, asyncio.Task] = {}
inflight_search_tasks: dict[str, asyncio.Task] = {}
voice_ingress_queue: asyncio.Queue[dict[str, Any]] = asyncio.Queue(maxsize=VOICE_INGRESS_QUEUE_MAX)
voice_worker_task: asyncio.Task | None = None
debug_write_queue: asyncio.Queue[dict[str, Any]] = asyncio.Queue(maxsize=max(8, DEBUG_WRITE_QUEUE_MAX))
debug_write_task: asyncio.Task | None = None
local_mic_service: LocalMicCaptureService | None = None
local_mic_runtime_state: dict[str, Any] = {
    "enabled": bool(LOCAL_MIC_ENABLED),
    "capture_ready": False,
    "last_error": None,
    "routed_user_ids": sorted(int(user_id) for user_id in LOCAL_MIC_DISCORD_USER_IDS),
    "segment_count": 0,
    "last_segment_at": None,
    "last_segment_duration_sec": None,
    "discord_suppression_active": False,
}
control_page_runner: web.AppRunner | None = None
control_page_site: web.TCPSite | None = None
control_page_start_lock: asyncio.Lock | None = None
control_page_chat_logs: dict[int, list[dict[str, Any]]] = {}
control_page_minecraft_snapshot_cache: dict[str, Any] = {}
control_page_minecraft_snapshot_cached_at = 0.0
control_page_minecraft_snapshot_stale = True
control_page_minecraft_snapshot_last_error = ""
control_page_minecraft_snapshot_lock: asyncio.Lock | None = None
control_page_minecraft_snapshot_refresh_task: asyncio.Task | None = None
control_page_minecraft_snapshot_poll_task: asyncio.Task | None = None
control_page_runtime_services_cache: dict[str, Any] = {}
control_page_runtime_services_cached_at = 0.0
control_page_runtime_services_lock: asyncio.Lock | None = None
control_page_ui_commands: list[dict[str, Any]] = []
control_page_ui_command_seq = 0
runtime_status_context_cache: dict[str, Any] = {"text": "", "cached_at": 0.0}
runtime_status_context_lock: asyncio.Lock | None = None
room_recent_speaker_stats: dict[str, dict[int, dict[str, float]]] = {}
session_speculative_policies: dict[str, dict[str, Any]] = {}
room_turn_scopes: dict[str, "TurnScope"] = {}
turn_stage_metrics: dict[str, dict[str, float]] = {}
turn_path_metrics: dict[str, dict[str, Any]] = {}
autonomy_engines: dict[int, AutonomyEngine] = {}
last_autonomy_ping_at: dict[int, float] = {}
autonomy_last_cognitive_refresh_at: dict[int, float] = {}
autonomy_cognitive_refresh_tasks: dict[int, asyncio.Task] = {}
search_followup_queued_count = 0
cancelled_stale_turn_count = 0
inflight_llm_requests = 0
stt_inference_lock: asyncio.Lock | None = None
stt_cooldown_until = 0.0
voice_pipeline_counters: dict[str, int] = {
    "queue_full_drop_count": 0,
    "queue_stale_drop_count": 0,
    "stt_busy_drop_count": 0,
    "stt_timeout_count": 0,
    "tts_request_failed_count": 0,
    "tts_producer_cancelled_count": 0,
    "tts_playback_failed_count": 0,
    "llm_failed_count": 0,
    "voice_delivery_failed_count": 0,
    "voice_rejoin_attempts": 0,
    "voice_rejoin_success": 0,
    "voice_rejoin_fail": 0,
}
voice_pipeline_state: dict[str, Any] = {
    "last_voice_segment_at": None,
    "last_voice_channel": None,
    "last_voice_rejoin_at": None,
    "last_voice_rejoin_error": None,
    "last_failure": None,
}
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
                if "?" in content:
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
    orchestration_clear_room_owner(
        room_session_key,
        room_owner_user_ids=room_owner_user_ids,
        room_owner_until=room_owner_until,
    )



def room_state_snapshot(room_session_key: str | None) -> dict:
    return orchestration_room_state_snapshot(
        room_session_key,
        room_owner_user_ids=room_owner_user_ids,
        room_owner_until=room_owner_until,
        room_reply_in_progress=room_reply_in_progress,
        active_speaker_user_id=pick_active_speaker(room_session_key),
        now_monotonic=time.monotonic(),
    )



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
    return orchestration_is_room_owner_active(
        room_session_key,
        user_id,
        room_owner_user_ids=room_owner_user_ids,
        room_owner_until=room_owner_until,
        room_reply_in_progress=room_reply_in_progress,
        active_speaker_user_id=pick_active_speaker(room_session_key),
        now_monotonic=time.monotonic(),
    )



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
    orchestration_set_room_owner(
        room_session_key,
        user_id,
        ttl_sec=ttl_sec,
        reason=reason,
        room_owner_user_ids=room_owner_user_ids,
        room_owner_until=room_owner_until,
        log_event=log_turn_event,
        now_monotonic=time.monotonic(),
        session_key=session_key,
        turn_id=turn_id,
        segment_id=segment_id,
    )



def set_room_reply_in_progress(room_session_key: str | None, value: bool, *, owner_user_id: int | None = None) -> None:
    orchestration_set_room_reply_in_progress(
        room_session_key,
        value,
        room_reply_in_progress=room_reply_in_progress,
        room_owner_user_ids=room_owner_user_ids,
        log_event=log_turn_event,
        owner_user_id=owner_user_id,
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


PERSONA_STATE_SNIPPETS = (
    "책 좀 보다가 왔어",
    "멍하니 쉬고 있었어",
    "노래 좀 듣고 있었어",
    "이것저것 정리하고 있었어",
    "잠깐 놀고 있었어",
    "생각 좀 정리하고 있었어",
)


def recent_assistant_reply_summary(*, session_key: str | None = None, guild_id: int | None = None, limit: int = 1) -> str:
    history = get_conversation_history(session_key=session_key, guild_id=guild_id)
    replies: list[str] = []
    for item in reversed(history):
        if not isinstance(item, dict) or item.get("role") != "assistant":
            continue
        content = clean_text(str(item.get("content") or ""))
        if not content:
            continue
        replies.append(content[:60])
        if len(replies) >= limit:
            break
    replies.reverse()
    return " / ".join(replies)


def is_casual_call_or_status_question(text: str) -> bool:
    cleaned = clean_text(text).lower()
    if not cleaned:
        return True
    stripped = re.sub(r"[\s,.!?~]+", "", cleaned)
    if stripped in {"evelyn", "이블린", "이브"}:
        return True
    return any(marker in cleaned for marker in ("뭐해", "뭐하고", "뭐 하는", "불러", "괜찮"))


def persona_state_hint_for_turn(user_text: str, *, session_key: str | None = None, guild_id: int | None = None) -> str:
    if not is_casual_call_or_status_question(user_text):
        return ""
    recent = recent_assistant_reply_summary(session_key=session_key, guild_id=guild_id, limit=4)
    used = [snippet for snippet in PERSONA_STATE_SNIPPETS if snippet in recent]
    choices = [snippet for snippet in PERSONA_STATE_SNIPPETS if snippet not in used] or list(PERSONA_STATE_SNIPPETS)
    basis = f"{runtime_session_key(session_key=session_key, guild_id=guild_id) or ''}:{clean_text(user_text)}:{len(recent)}"
    index = sum(ord(ch) for ch in basis) % len(choices)
    state = choices[index]
    return f"호출/근황 질문. 실제 행동 주장 없이 캐릭터 상태 하나만 가볍게 말해라. 상태={state}. 반복 금지."


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
    clear_tts_playback_tracking(
        tracker=tts_playback_tracker,
        guild_id=guild_id,
    )
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
    preserve_null_fields = event in TURN_SUMMARY_EVENTS
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
        if value is None and not preserve_null_fields:
            continue
        record[key] = value
    try:
        TURN_TRACE_LOG_DIR.mkdir(parents=True, exist_ok=True)
        trace_path = TURN_TRACE_LOG_DIR / f"{time.strftime('%Y%m%d')}.jsonl"
        with turn_trace_file_lock:
            with trace_path.open("a", encoding="utf-8") as f:
                f.write(json.dumps(record, ensure_ascii=False, sort_keys=True) + "\n")
    except Exception as exc:
        _ORIGINAL_PRINT(f"[TURN TRACE FILE ERROR] {exc!r}")
    try:
        print("[TURN TRACE]\n" + json.dumps(record, ensure_ascii=False, sort_keys=True, indent=2))
    except Exception as exc:
        safe_record = {"event": event, "ts": record.get("ts"), "trace_error": repr(exc)}
        for key in ("turn_id", "chunk_index", "session_key", "source_type", "stage", "error"):
            value = record.get(key)
            if value is not None:
                safe_record[key] = value
        print("[TURN TRACE]\n" + json.dumps(safe_record, ensure_ascii=False, sort_keys=True, indent=2))


configure_tts_playback_logging(log_turn_event)


def record_context_pipeline_benchmark(
    *,
    metrics: dict | None,
    user_text: str,
    answer: str,
    source: str,
    guild_id: int | None,
    session_key: str | None,
) -> None:
    meta = (metrics or {}).get("meta") if isinstance(metrics, dict) else {}
    context_meta = meta.get("context_pipeline") if isinstance(meta, dict) else None
    if not isinstance(context_meta, dict):
        return
    record = {
        "ts": round(time.time(), 3),
        "source": clean_text(source),
        "guild_id": guild_id,
        "session_key": session_key,
        "turn_id": meta.get("turn_id") if isinstance(meta, dict) else None,
        "route": context_meta.get("route") or meta.get("route") if isinstance(meta, dict) else None,
        "policy": context_meta.get("policy"),
        "sections": context_meta.get("sections"),
        "section_chars": context_meta.get("section_chars"),
        "minecraft_context": bool(context_meta.get("minecraft_context")),
        "vision_context": "vision" in set(context_meta.get("sections") or []),
        "user_text_len": len(clean_text(user_text)),
        "answer_len": len(clean_text(answer)),
        "marks": {
            key: value
            for key, value in ((metrics or {}).get("marks") or {}).items()
            if key in {"route_ready", "memory_ready", "t_context_build", "llm_done", "t_main_done", "llm_http_ms"}
        },
    }
    try:
        path = CONTEXT_PIPELINE_BENCHMARK_LOG
        if not path.is_absolute():
            path = PROJECT_ROOT / path
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("a", encoding="utf-8") as f:
            f.write(json.dumps(record, ensure_ascii=False, sort_keys=True) + "\n")
    except Exception as exc:
        print(f"[CONTEXT PIPELINE BENCHMARK] write_failed err={exc!r}")


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


def _append_bounded_metric(values: list[float], value: float | None, *, limit: int = 200) -> None:
    if value is None:
        return
    values.append(float(value))
    if len(values) > limit:
        del values[: len(values) - limit]


def record_turn_path_summary(meta: dict[str, Any], marks: dict[str, Any], total_ms: float) -> None:
    turn_type = clean_text(str(meta.get("turn_type") or "unknown")) or "unknown"
    selected_path = clean_text(str(meta.get("selected_path") or "unknown")) or "unknown"
    key = f"{turn_type}|{selected_path}"
    bucket = turn_path_metrics.setdefault(
        key,
        {
            "turn_type": turn_type,
            "selected_path": selected_path,
            "count": 0,
            "total_ms": [],
            "stt_ms": [],
            "main_first_ms": [],
            "tts_first_ms": [],
            "playback_ms": [],
        },
    )
    bucket["count"] = int(bucket.get("count", 0)) + 1
    _append_bounded_metric(bucket["total_ms"], total_ms)
    _append_bounded_metric(bucket["stt_ms"], marks.get("t_stt_done"))
    _append_bounded_metric(bucket["main_first_ms"], marks.get("t_main_first_token"))
    _append_bounded_metric(bucket["tts_first_ms"], marks.get("t_tts_first_audio"))
    _append_bounded_metric(bucket["playback_ms"], marks.get("t_playback_first_packet"))


def summarize_turn_path_metrics() -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for bucket in turn_path_metrics.values():
        rows.append(
            {
                "turnType": bucket.get("turn_type"),
                "selectedPath": bucket.get("selected_path"),
                "count": int(bucket.get("count", 0)),
                "totalMsP95": round(_p95(bucket.get("total_ms") or []), 1),
                "sttMsP95": round(_p95(bucket.get("stt_ms") or []), 1),
                "mainFirstMsP95": round(_p95(bucket.get("main_first_ms") or []), 1),
                "ttsFirstMsP95": round(_p95(bucket.get("tts_first_ms") or []), 1),
                "playbackMsP95": round(_p95(bucket.get("playback_ms") or []), 1),
            }
        )
    rows.sort(key=lambda row: (-int(row.get("count") or 0), str(row.get("turnType") or "")))
    return rows[:12]


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


def increment_voice_pipeline_counter(name: str, amount: int = 1) -> None:
    voice_pipeline_counters[name] = int(voice_pipeline_counters.get(name, 0)) + int(amount)


def get_stt_inference_lock() -> asyncio.Lock:
    global stt_inference_lock
    if stt_inference_lock is None:
        stt_inference_lock = asyncio.Lock()
    return stt_inference_lock


def voice_last_channel_state_path() -> Path:
    path = Path(VOICE_LAST_CHANNEL_STATE_FILE)
    if not path.is_absolute():
        path = PROJECT_ROOT / path
    return path


def load_last_voice_channel_state() -> dict[str, Any]:
    path = voice_last_channel_state_path()
    if not path.exists():
        return {}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return {}
    return data if isinstance(data, dict) else {}


def save_last_voice_channel_state(
    guild: discord.Guild,
    channel: discord.VoiceChannel,
    *,
    reason: str,
    manual_disconnect: bool = False,
) -> None:
    payload = {
        "guild_id": int(guild.id),
        "guild_name": clean_text(getattr(guild, "name", "") or ""),
        "channel_id": int(channel.id),
        "channel_name": clean_text(getattr(channel, "name", "") or ""),
        "updated_at": time.time(),
        "reason": reason,
        "manual_disconnect": bool(manual_disconnect),
    }
    path = voice_last_channel_state_path()
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
        voice_pipeline_state["last_voice_channel"] = dict(payload)
    except Exception as exc:
        print(f"[VOICE STATE SAVE FAIL] err={exc!r}")


def mark_voice_manual_disconnect(guild: discord.Guild | None, *, reason: str) -> None:
    if guild is None:
        return
    data = load_last_voice_channel_state()
    if not data or int(data.get("guild_id") or 0) != int(guild.id):
        return
    data["manual_disconnect"] = True
    data["reason"] = reason
    data["updated_at"] = time.time()
    path = voice_last_channel_state_path()
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
        voice_pipeline_state["last_voice_channel"] = dict(data)
    except Exception as exc:
        print(f"[VOICE STATE SAVE FAIL] err={exc!r}")


def record_voice_pipeline_failure(kind: str, err: BaseException | str, metrics: dict | None = None, **extra: Any) -> None:
    counter_map = {
        "llm_failed": "llm_failed_count",
        "tts_request_failed": "tts_request_failed_count",
        "tts_producer_cancelled": "tts_producer_cancelled_count",
        "tts_playback_failed": "tts_playback_failed_count",
        "voice_delivery_failed": "voice_delivery_failed_count",
    }
    counter = counter_map.get(kind)
    if counter:
        increment_voice_pipeline_counter(counter)
    error_text = repr(err) if isinstance(err, BaseException) else clean_text(str(err))
    voice_pipeline_state["last_failure"] = {
        "kind": kind,
        "error": error_text[:260],
        "at": time.time(),
    }
    meta = (metrics or {}).get("meta") or {}
    log_turn_event(
        kind,
        **merge_log_event_payload(
            explicit={
                "turn_id": meta.get("turn_id"),
                "segment_id": meta.get("segment_id"),
                "chunk_index": meta.get("chunk_index"),
                "session_key": meta.get("session_key"),
                "room_session_key": meta.get("room_session_key"),
                "guild_id": meta.get("guild_id"),
                "source": meta.get("source"),
                "error": error_text[:500],
            },
            extra=extra,
        ),
    )


def build_voice_pipeline_snapshot(guild: discord.Guild | None = None) -> dict[str, Any]:
    p95 = summarize_p95_metrics()
    lock = stt_inference_lock
    now_mono = time.monotonic()
    last_segment_at = voice_pipeline_state.get("last_voice_segment_at")
    last_segment_age_sec = None
    if isinstance(last_segment_at, (int, float)):
        last_segment_age_sec = round(max(0.0, time.time() - float(last_segment_at)), 3)
    cooldown_remaining = max(0.0, stt_cooldown_until - now_mono)
    state_file = load_last_voice_channel_state()
    if state_file:
        voice_pipeline_state["last_voice_channel"] = dict(state_file)
    return {
        "queueDepth": voice_ingress_queue.qsize(),
        "queueMax": VOICE_INGRESS_QUEUE_MAX,
        "liveRecent": last_segment_age_sec is not None and last_segment_age_sec <= VOICE_LIVE_RECENT_SEC,
        "lastVoiceSegmentAgeSec": last_segment_age_sec,
        "sttBusy": bool(lock and lock.locked()),
        "sttCooldownRemainingSec": round(cooldown_remaining, 3),
        "sttTimeoutCount": voice_pipeline_counters.get("stt_timeout_count", 0),
        "sttBusyDropCount": voice_pipeline_counters.get("stt_busy_drop_count", 0),
        "queueFullDropCount": voice_pipeline_counters.get("queue_full_drop_count", 0),
        "queueStaleDropCount": voice_pipeline_counters.get("queue_stale_drop_count", 0),
        "ttsRequestFailedCount": voice_pipeline_counters.get("tts_request_failed_count", 0),
        "ttsPlaybackFailedCount": voice_pipeline_counters.get("tts_playback_failed_count", 0),
        "llmFailedCount": voice_pipeline_counters.get("llm_failed_count", 0),
        "voiceDeliveryFailedCount": voice_pipeline_counters.get("voice_delivery_failed_count", 0),
        "rejoinAttempts": voice_pipeline_counters.get("voice_rejoin_attempts", 0),
        "rejoinSuccess": voice_pipeline_counters.get("voice_rejoin_success", 0),
        "rejoinFail": voice_pipeline_counters.get("voice_rejoin_fail", 0),
        "lastVoiceChannel": voice_pipeline_state.get("last_voice_channel"),
        "lastVoiceRejoinAt": voice_pipeline_state.get("last_voice_rejoin_at"),
        "lastVoiceRejoinError": voice_pipeline_state.get("last_voice_rejoin_error"),
        "lastFailure": voice_pipeline_state.get("last_failure"),
        "sttMsP95": p95.get("stt_ms_p95", 0),
        "ttsFirstAudioMsP95": p95.get("tts_first_audio_ms_p95", 0),
        "mainFirstTokenMsP95": p95.get("main_first_token_ms_p95", 0),
        "turnPathMetrics": summarize_turn_path_metrics(),
    }


async def run_blocking_stt_task(
    func: Callable[[], Any],
    *,
    stage: str,
    timeout_sec: float,
    metrics: dict | None = None,
) -> Any:
    global stt_cooldown_until
    now_mono = time.monotonic()
    if now_mono < stt_cooldown_until:
        increment_voice_pipeline_counter("stt_busy_drop_count")
        raise TimeoutError(f"stt_cooldown:{stage}:{stt_cooldown_until - now_mono:.2f}s")

    lock = get_stt_inference_lock()
    if lock.locked():
        increment_voice_pipeline_counter("stt_busy_drop_count")
        raise RuntimeError(f"stt_busy:{stage}")

    async with lock:
        try:
            return await asyncio.wait_for(asyncio.to_thread(func), timeout=max(0.5, timeout_sec))
        except asyncio.TimeoutError:
            stt_cooldown_until = time.monotonic() + max(0.0, STT_COOLDOWN_AFTER_TIMEOUT_SEC)
            increment_voice_pipeline_counter("stt_timeout_count")
            record_voice_pipeline_failure("stt_timeout", f"{stage} timed out after {timeout_sec:.1f}s", metrics, stage=stage)
            raise


def compute_runtime_mode(metrics: dict | None) -> str:
    meta = (metrics or {}).get("meta") or {}
    marks = (metrics or {}).get("marks") or {}
    tts_backlog = tracked_tts_playback_count(tts_playback_tracker)
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
    voice_segment = meta.get("voice_segment_contract")
    if voice_segment is not None and meta.get("rejected_turn_contract") is None:
        meta["rejected_turn_contract"] = build_rejected_voice_turn(
            segment=voice_segment,
            ingress_source=str(meta.get("ingress_source") or meta.get("source") or "voice"),
            drop_reason=reason,
            queue_wait_ms=float(meta.get("voice_queue_wait_ms") or 0.0),
            topic_id=meta.get("topic_id"),
            gate_mode=meta.get("reply_gate_blocked_by"),
            owner_user_id=extra.get("owner_user_id") if extra.get("owner_user_id") is not None else meta.get("owner_user_id"),
            detail_text=str(extra.get("text") or extra.get("final_text") or extra.get("wake_probe_text") or ""),
        )
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

        meta_turn_id = None
        meta_segment_id = None
        if isinstance(debug_meta, dict):
            meta_turn_id = clean_text(str(debug_meta.get("turn_id") or "")) or None
            raw_segment_id = debug_meta.get("segment_id")
            if raw_segment_id is not None:
                try:
                    meta_segment_id = int(raw_segment_id)
                except (TypeError, ValueError):
                    meta_segment_id = clean_text(str(raw_segment_id)) or None

        stamp = time.strftime("%Y%m%d-%H%M%S")
        stem_key = (guild_id, session_key or "", meta_turn_id or "", str(meta_segment_id or ""))
        stem = voice_debug_stems.get(stem_key)
        if stem is None:
            idx = voice_debug_counts.get(guild_id, 0) + 1
            voice_debug_counts[guild_id] = idx
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
            "turn_id": meta_turn_id,
            "segment_id": meta_segment_id,
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
    record_context_pipeline_benchmark(
        metrics=metrics,
        user_text=voice_reply.history_user_text,
        answer=plain_answer,
        source="voice",
        guild_id=guild_id,
        session_key=session_key,
    )
    memory_writer_decision = schedule_memory_update(
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
    metrics.setdefault("meta", {})["memory_writer_decision"] = memory_writer_decision
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
    awaiting_reply = bool(session_state_snapshot(session_key).get("awaiting_user_reply"))
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
    "그거",
    "그건",
    "그 다음",
    "이어",
    "계속",
)

FAST_PATH_DIRECTIVE_MARKERS = (
    "해줘",
    "말해줘",
    "알려줘",
    "정리해줘",
    "요약해줘",
    "설명해줘",
    "번역해줘",
    "고쳐줘",
    "수정해줘",
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
    "비교",
    "분석",
    "판단",
    "기억",
    "아까",
    "방금",
    "전에",
    "이전",
    "이어",
    "계속",
    "요약",
    "정리",
)


def needs_search_or_deep_routing(text: str) -> bool:
    cleaned = clean_text(text)
    if not cleaned:
        return False
    if should_force_search_query(cleaned):
        return True
    marker_hits = sum(1 for marker in FAST_PATH_DEEP_ROUTE_MARKERS if marker in cleaned)
    if marker_hits >= 2:
        return True
    if len(cleaned) >= 72:
        return True
    search_markers = ("검색", "찾아", "최신", "뉴스", "시세", "가격", "주가", "환율")
    return any(marker in cleaned for marker in search_markers)


def is_simple_directive(text: str) -> bool:
    cleaned = clean_text(text)
    if not cleaned:
        return False
    if needs_search_or_deep_routing(cleaned):
        return False
    if any(marker in cleaned for marker in FAST_PATH_DIRECTIVE_MARKERS):
        return True
    return len(cleaned) <= 24 and "?" not in cleaned


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
    if should_force_search_query(cleaned):
        return {"route": "search_executor", "action": "search_then_answer", "reason_brief": "search_trigger"}
    if is_simple_directive(cleaned):
        return {"route": "main_direct", "action": "answer", "reason_brief": "simple_directive"}
    if not needs_search_or_deep_routing(cleaned):
        return {"route": "main_direct", "action": "answer", "reason_brief": "light_request"}
    return None


def context_policy_for_fast_path_policy(policy: dict | None, *, source: str) -> dict[str, Any]:
    action = clean_text(str((policy or {}).get("action") or "answer"))
    route = clean_text(str((policy or {}).get("route") or "main_direct"))
    needs_search = action == "search_then_answer" or route == "search_executor"
    return {
        "intent": "question" if needs_search else "chat",
        "needs_main_llm": action == "answer",
        "needs_memory": False,
        "needs_runtime_state": False,
        "needs_minecraft_state": False,
        "needs_vision": False,
        "needs_skill_graph": False,
        "needs_long_context": False,
        "needs_search": needs_search,
        "needs_tts": True,
        "priority": "accuracy" if needs_search else "latency",
        "context_focus": [],
        "response_mode": "short" if source == "voice" else "normal",
    }


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

    tts_suppression = tts_input_suppression_reason(
        tracker=tts_playback_tracker,
        guild_id=guild_id,
        post_tts_ignore_sec=POST_TTS_IGNORE_SEC,
        now=now,
    )
    if tts_suppression is not None:
        return False, tts_suppression, tts_suppression

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
            dequeue_plan = evaluate_voice_ingress_dequeue(
                item,
                now_monotonic=time.monotonic(),
                max_age_sec=VOICE_INGRESS_MAX_AGE_SEC,
                queue_depth_at_dequeue=voice_ingress_queue.qsize(),
            )
            if dequeue_plan.should_drop_stale:
                increment_voice_pipeline_counter("queue_stale_drop_count")
                member = item.get("member")
                apply_voice_ingress_dequeue_debug_meta(item, dequeue_plan)
                print(
                    f"[VOICE QUEUE DROP] reason=stale wait_ms={dequeue_plan.queue_wait_ms:.1f} "
                    f"max_age_ms={dequeue_plan.max_age_ms:.1f} speaker={getattr(member, 'display_name', None)}"
                )
                continue
            apply_voice_ingress_dequeue_debug_meta(item, dequeue_plan)
            process_item = dict(item)
            process_item.pop("enqueued_at", None)
            await _process_member_audio_impl(**process_item)
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
    visible = normalize_friend_style_output(visible_text(text)).strip()
    if not visible:
        return False
    if visible.startswith("[질문]"):
        return False
    if session_key is not None and session_state_snapshot(session_key).get("awaiting_user_reply"):
        return True
    return False


def format_display_text(text: str, *, session_key: str | None = None) -> str:
    visible = normalize_friend_style_output(visible_text(text)).strip()
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
        hard_break = candidate.endswith((".", "!", "?", "\n"))
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
    runtime_snapshot = state.get("runtime_snapshot") if isinstance(state.get("runtime_snapshot"), dict) else {}
    if runtime_snapshot:
        freshness = clean_text(str(runtime_snapshot.get("freshness") or ""))
        age_sec = runtime_snapshot.get("age_sec")
        if freshness:
            parts.append(f"snapshot={freshness}")
        if age_sec is not None:
            parts.append(f"snapshot_age={age_sec}s")
        snapshot_error = clean_text(str(runtime_snapshot.get("last_error") or ""))
        if snapshot_error:
            parts.append(f"snapshot_error={snapshot_error[:120]}")
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


def runtime_status_port_from_url(url: str) -> tuple[str, int] | None:
    parsed = urlparse(clean_text(str(url or "")))
    if not parsed.hostname:
        return None
    if parsed.port is not None:
        return parsed.hostname, int(parsed.port)
    if parsed.scheme == "https":
        return parsed.hostname, 443
    if parsed.scheme == "http":
        return parsed.hostname, 80
    return None


async def probe_runtime_tcp_service(label: str, host: str, port: int) -> tuple[str, bool]:
    try:
        _reader, writer = await asyncio.wait_for(
            asyncio.open_connection(host, port),
            timeout=RUNTIME_STATUS_CONTEXT_CONNECT_TIMEOUT_SEC,
        )
        writer.close()
        with contextlib.suppress(Exception):
            await writer.wait_closed()
        return label, True
    except Exception:
        return label, False


def read_text_tail(path: Path, *, max_bytes: int = 4096) -> str:
    try:
        with path.open("rb") as handle:
            handle.seek(0, os.SEEK_END)
            size = handle.tell()
            handle.seek(max(0, size - max_bytes), os.SEEK_SET)
            return handle.read(max_bytes).decode("utf-8", errors="replace")
    except Exception:
        return ""


def compact_runtime_error(value: Any, *, max_chars: int | None = None) -> str:
    limit = max_chars if max_chars is not None else RUNTIME_STATUS_CONTEXT_MAX_ERROR_CHARS
    text = clean_text(str(value or ""))
    if not text:
        return ""
    text = re.sub(r"\s+", " ", text).strip()
    if len(text) > limit:
        return text[: max(0, limit - 3)].rstrip() + "..."
    return text


def runtime_file_age_label(path: Path) -> str:
    try:
        age_sec = max(0.0, time.time() - path.stat().st_mtime)
    except Exception:
        return ""
    if age_sec < 60:
        return f"{int(age_sec)}s ago"
    if age_sec < 3600:
        return f"{int(age_sec // 60)}m ago"
    if age_sec < 86400:
        return f"{int(age_sec // 3600)}h ago"
    return f"{int(age_sec // 86400)}d ago"


def load_runtime_recent_errors() -> list[str]:
    errors: list[str] = []
    codex_path = RUNTIME_ARTIFACTS_ROOT / "codex_gateway" / "last_request.json"
    try:
        codex_payload = json.loads(codex_path.read_text(encoding="utf-8"))
        codex_error = (
            codex_payload.get("error")
            or codex_payload.get("stderr_tail")
            or codex_payload.get("message")
        )
        codex_status = clean_text(str(codex_payload.get("status") or codex_payload.get("phase") or ""))
        if codex_error:
            codex_age = runtime_file_age_label(codex_path)
            codex_meta = ", ".join(part for part in (codex_status, codex_age) if part)
            prefix = f"codex({codex_meta})" if codex_meta else "codex"
            errors.append(f"{prefix}: {compact_runtime_error(codex_error)}")
    except Exception:
        pass

    voyager_status_path = RUNTIME_ARTIFACTS_ROOT / "voyager" / "upstream_bridge_status.json"
    try:
        status_payload = json.loads(voyager_status_path.read_text(encoding="utf-8"))
        voyager_error = (
            status_payload.get("last_error")
            or status_payload.get("last_critique")
            or status_payload.get("last_completion_reason")
        )
        if voyager_error:
            voyager_age = runtime_file_age_label(voyager_status_path)
            prefix = f"voyager({voyager_age})" if voyager_age else "voyager"
            errors.append(f"{prefix}: {compact_runtime_error(voyager_error)}")
    except Exception:
        pass

    for label, log_path in (
        ("voyager_service", RUNTIME_ARTIFACTS_ROOT / "logs" / "voyager_service_errors.log"),
        ("upstream_bridge", RUNTIME_ARTIFACTS_ROOT / "logs" / "upstream_bridge_errors.log"),
    ):
        if len(errors) >= 3:
            break
        tail = read_text_tail(log_path)
        lines = [compact_runtime_error(line) for line in tail.splitlines() if compact_runtime_error(line)]
        if lines:
            log_age = runtime_file_age_label(log_path)
            prefix = f"{label}({log_age})" if log_age else label
            errors.append(f"{prefix}: {lines[-1]}")

    return errors[:3]


async def build_runtime_status_context(*, force: bool = False) -> str:
    global runtime_status_context_lock
    if not RUNTIME_STATUS_CONTEXT_ENABLED:
        return ""

    cached_at = float(runtime_status_context_cache.get("cached_at") or 0.0)
    if not force and runtime_status_context_cache.get("text") and (time.time() - cached_at) <= RUNTIME_STATUS_CONTEXT_REFRESH_SEC:
        return str(runtime_status_context_cache.get("text") or "")

    if runtime_status_context_lock is None:
        runtime_status_context_lock = asyncio.Lock()

    async with runtime_status_context_lock:
        cached_at = float(runtime_status_context_cache.get("cached_at") or 0.0)
        if not force and runtime_status_context_cache.get("text") and (time.time() - cached_at) <= RUNTIME_STATUS_CONTEXT_REFRESH_SEC:
            return str(runtime_status_context_cache.get("text") or "")

        probes: list[tuple[str, str, int]] = [
            ("bot/control", CONTROL_PAGE_HOST, CONTROL_PAGE_PORT),
        ]
        for label, url in (
            ("main_llm", LLM_SERVER_URL),
            ("router_llm", ROUTER_LLM_URL),
            ("sub_llm", SUMMARY_LLM_URL),
            ("tts", OMNIVOICE_SERVER_URL),
        ):
            target = runtime_status_port_from_url(url)
            if target is not None:
                probes.append((label, target[0], target[1]))
        probes.append(("voyager_service", "127.0.0.1", MINECRAFT_AUTONOMY_SERVICE_PORT))
        if clean_text(str(VOYAGER_ACTION_BACKEND or "")).lower() == "codex-gateway":
            probes.append(("codex_gateway", "127.0.0.1", VOYAGER_CODEX_GATEWAY_PORT))

        results = await asyncio.gather(
            *(probe_runtime_tcp_service(label, host, port) for label, host, port in probes),
            return_exceptions=True,
        )
        status_parts: list[str] = []
        for result in results:
            if isinstance(result, tuple) and len(result) == 2:
                label, ok = result
                status_parts.append(f"{label}={'up' if ok else 'down'}")

        service_summary = ""
        try:
            services = await get_control_page_runtime_services()
            service_summary = compact_runtime_error(services.get("summary"), max_chars=120)
        except Exception:
            service_summary = ""
        if service_summary:
            status_parts.append(f"summary={service_summary}")

        recent_errors = load_runtime_recent_errors()
        if recent_errors:
            status_parts.append("recent_errors=" + " | ".join(recent_errors))
        else:
            status_parts.append("recent_errors=none")

        text = "; ".join(part for part in status_parts if part)
        runtime_status_context_cache["text"] = text
        runtime_status_context_cache["cached_at"] = time.time()
        return text


def build_main_response_guidance(
    cognitive_state: dict | None = None,
    *,
    source: str = "text",
    user_text: str = "",
    session_key: str | None = None,
    guild_id: int | None = None,
    minecraft_state: dict[str, Any] | None = None,
    runtime_status_context: str | None = None,
) -> str:
    state = apply_ask_gating(cognitive_state, source=source)
    threshold = ask_confidence_threshold_for_source(source)
    turn_type = classify_dialogue_turn(user_text)
    parts = [
        f"이번 입력 source={source}, turn_type={turn_type}. ask는 confidence {threshold:.2f}+일 때만.",
    ]
    persona_hint = persona_state_hint_for_turn(user_text, session_key=session_key, guild_id=guild_id)
    if persona_hint:
        parts.append(persona_hint)
    recent_assistant = recent_assistant_reply_summary(session_key=session_key, guild_id=guild_id, limit=1) if persona_hint else ""
    if recent_assistant:
        parts.append(f"최근 네 말: {recent_assistant}. 반복하지 말고 이어서 답해라.")

    action = state.get("action", "answer")
    if state.get("user_intent"):
        parts.append(f"사용자 의도 추정: {state['user_intent']}")

    if action == "ask":
        parts.append("짧게 확인 질문만 해라.")
        if state.get("question_for_user"):
            parts.append(f"되물을 말: {state['question_for_user']}")
    elif action == "wait":
        parts.append("길게 답하지 말고 더 들을 여지를 둬라.")
    else:
        parts.append("바로 답해라.")

    if state.get("main_prompt_hint"):
        parts.append(f"응답 추가 힌트: {state['main_prompt_hint']}")
    if state.get("confidence", 0.0) > 0:
        parts.append(f"내부 판단 신뢰도: {state['confidence']:.2f}")

    if runtime_status_context:
        parts.append(f"현재 Evelyn 런타임 상태 요약: {runtime_status_context}")
        parts.append("사용자가 Evelyn의 상태, 오류, 연결, 지연, 서버 상황을 물을 때만 이 런타임 상태를 근거로 답해라. 일반 대화에서는 먼저 꺼내지 마라.")

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
    if route in {"voice_context", "voice-context", "context", "memory_context"}:
        return "sub_hint"
    return "main_direct"


def should_force_voice_context_route(user_text: str) -> bool:
    text = clean_text(user_text)
    if not text:
        return False
    voice_context_markers = [
        "기억",
        "방금",
        "아까",
        "전에",
        "이전",
        "말했",
        "했던",
        "하던",
        "무슨 얘기",
        "뭐지",
        "이어",
        "계속",
        "정리",
        "요약",
        "우리",
        "우리가",
        "하기로",
        "먹기로",
        "가기로",
        "약속",
        "정했",
    ]
    marker_hits = sum(1 for marker in voice_context_markers if marker in text)
    if marker_hits >= 1:
        return True
    return bool(re.search(r"(우리|우리가).*(하기로|먹기로|가기로)", text))


def classify_llm_route_fallback(user_text: str, *, source: str = "text") -> str:
    text = clean_text(user_text)
    if source == "voice" and not should_force_voice_context_route(text):
        return "main_direct"

    short_text = len(text) <= 18 or len(text.split()) <= 4
    if short_text and source != "voice":
        return "main_direct"

    context_markers = [
        "아까",
        "방금",
        "전에",
        "이전",
        "기억",
        "문맥",
        "계속",
        "이어",
        "요약",
        "정리",
        "판단",
        "비교",
        "설명",
        "의견",
        "생각",
        "왜",
        "어떻게",
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
) -> tuple[list[dict], dict | None, str, ContextPolicy]:
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
    if local_fast_policy is not None:
        route_meta = dict(route_meta or {})
        route_meta["context_policy"] = context_policy_for_fast_path_policy(local_fast_policy, source=source)
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

    context_policy = build_context_policy_for_turn(
        user_text=user_text,
        source=source,
        route=route,
        route_meta=route_meta,
        cognitive_state=cognitive_state,
    )
    memory_context = ""
    if guild_id is not None and context_policy.needs_memory:
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
    elif metrics is not None:
        metrics.setdefault("meta", {})["memory_context_skipped_by_policy"] = True

    session_snapshot = session_state_snapshot(session_key)
    live_context_minecraft_state: dict[str, Any] | None = None
    if guild_id is not None and (context_policy.needs_minecraft_state or context_policy.needs_skill_graph):
        try:
            live_context_minecraft_state = await observe_live_minecraft_state(guild_id)
        except Exception as e:
            live_context_minecraft_state = attach_minecraft_runtime_snapshot(
                {"last_error": clean_text(repr(e))[:160]},
                source="context_error",
                now=time.time(),
                observed_at=time.time(),
                stale_after_sec=CONTROL_PAGE_MINECRAFT_CACHE_REFRESH_SEC,
                expired_after_sec=CONTROL_PAGE_MINECRAFT_CACHE_MAX_STALE_SEC,
                last_error=clean_text(repr(e))[:160],
            )
        if metrics is not None and isinstance(live_context_minecraft_state, dict):
            runtime_snapshot = live_context_minecraft_state.get("runtime_snapshot")
            if isinstance(runtime_snapshot, dict):
                metrics.setdefault("meta", {})["minecraft_snapshot_age_ms"] = (
                    None
                    if runtime_snapshot.get("age_sec") is None
                    else max(0.0, float(runtime_snapshot.get("age_sec") or 0.0) * 1000.0)
                )
                metrics.setdefault("meta", {})["minecraft_snapshot_freshness"] = runtime_snapshot.get("freshness")
    conversation_context = build_conversation_state_context(
        cognitive_state=cognitive_state,
        session_state=session_snapshot,
        route=route,
    )
    runtime_context = build_runtime_state_context(
        source=source,
        route=route,
        guild_id=guild_id,
        session_key=session_key,
        room_key=room_key,
        person_key=person_key,
        session_memory_key=session_memory_key,
        session_state=session_snapshot,
    )
    skill_context = build_minecraft_skill_context(
        context_policy,
        user_text=user_text,
        minecraft_state=live_context_minecraft_state,
        skill_library_path=PROJECT_ROOT / "third_party" / "Voyager" / "skill_library" / "skill" / "skills.json",
        capability_data_dir=ODYSSEY_CAPABILITY_JSON_DIR if ODYSSEY_CAPABILITY_JSON_DIR.exists() else None,
    )
    if not skill_context:
        skill_context = build_skill_context_hint(context_policy)
    vision_context = build_vision_context_hint(context_policy, user_text=user_text)
    context_packet = build_basic_context_packet(
        current_user_input="",
        memory_context=memory_context if context_policy.needs_memory else "",
        runtime_state=runtime_context if context_policy.needs_runtime_state else "",
        conversation_state=conversation_context,
        skill_context=skill_context,
        vision_context=vision_context,
        policy=context_policy,
    )
    if context_packet.sections():
        messages = ContextBuilder().build_messages(context_packet, messages)
    if metrics is not None:
        metrics.setdefault("marks", {})["t_context_build"] = (time.monotonic() - float(metrics.get("started_at", time.monotonic()))) * 1000.0
        metrics.setdefault("meta", {})["context_pipeline"] = {
            "phase": "policy_packet",
            "route": route,
            "policy": context_policy.to_dict(),
            "memory_context_chars": len(memory_context),
            "message_count": len(messages),
            "sections": [section.source or section.name for section in context_packet.sections()],
            "section_chars": {
                section.source or section.name: len(section.cleaned_content())
                for section in context_packet.sections()
            },
            "minecraft_context": bool(live_context_minecraft_state),
        }

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
    return messages, cognitive_state, route, context_policy


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
    session_state = session_state_snapshot(session_key)
    vault_context = build_memory_vault_context(
        guild_id,
        user_text,
        session_key=session_key,
        topic_id=clean_text(str(session_state.get("topic_id", ""))) or None,
        source="context_pipeline",
        context_focus=[
            "relevant_memory",
            clean_text(str(state.get("user_intent", ""))),
            clean_text(str(state.get("state_summary", ""))),
        ],
        max_items=5,
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
    if vault_context:
        parts.append("Structured memory vault recall:\n" + vault_context)
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
                "You are Evelyn's lightweight router and context policy planner. "
                "Return exactly one JSON object and no other text. "
                "Required shape: "
                '{"selected":"main_direct|voice_context|sub_wait","confidence":0.0,'
                '"reason_brief":"short reason","context_policy":{'
                '"intent":"chat|question|minecraft_task|vision_question|memory_update|control",'
                '"needs_main_llm":true,"needs_memory":true,"needs_runtime_state":true,'
                '"needs_minecraft_state":false,"needs_vision":false,"needs_skill_graph":false,'
                '"needs_long_context":false,"priority":"latency|accuracy|action",'
                '"context_focus":["current_goal"],"response_mode":"short|normal|detailed|action_only"}}. '
                "Use main_direct for ordinary direct replies, voice_context when recent state/memory is important, "
                "and sub_wait when search/wait/search_then_answer style reasoning is needed. "
                "Set minecraft/vision/skill flags only when the current turn needs them."
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
    raw_context_policy = result.get("context_policy")
    if isinstance(raw_context_policy, dict):
        meta["context_policy"] = ContextPolicy.from_mapping(raw_context_policy).to_dict()
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
    has_open_question = ("?" in cleaned_user) or ("?" in cleaned_answer)
    explicit_fact_markers = ("나는 ", "내가 ", "우리는", "설정", "결정", "기억", "기억해줘", "해야", "하기로")
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


def schedule_memory_vault_maintenance(guild_id: int, *, turn_scope: TurnScope | None = None) -> None:
    interval_sec = float(os.getenv("MEMORY_VAULT_MAINTENANCE_INTERVAL_SEC", "900"))
    now = time.monotonic()
    last_run = float(memory_vault_last_maintenance_at.get(guild_id, 0.0) or 0.0)
    if now - last_run < interval_sec:
        return
    existing = background_memory_vault_tasks.get(guild_id)
    if existing is not None and not existing.done():
        return

    async def _maintain_memory_vault() -> None:
        try:
            await asyncio.sleep(0.2)
            if turn_scope is not None:
                turn_scope.raise_if_cancelled()
            result = await asyncio.to_thread(run_memory_vault_maintenance_once, guild_id)
            memory_vault_last_maintenance_at[guild_id] = time.monotonic()
            if result.get("daily_consolidation"):
                print(
                    f"[MEMORY VAULT] maintenance guild={guild_id} version={result.get('memory_version')} "
                    f"consolidated={result.get('daily_consolidation')} ms={result.get('latency_ms')}"
                )
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            print(f"[MEMORY VAULT] maintenance failed guild={guild_id}: {exc!r}")
        finally:
            task = background_memory_vault_tasks.get(guild_id)
            if task is asyncio.current_task():
                background_memory_vault_tasks.pop(guild_id, None)

    background_memory_vault_tasks[guild_id] = create_turn_scoped_task(_maintain_memory_vault(), turn_scope=turn_scope)


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
) -> dict[str, Any]:
    rows = [
        {"role": "user", "speaker": user_speaker, "source": source, "text": user_text},
        {"role": "assistant", "speaker": assistant_speaker, "source": source, "text": answer},
    ]
    append_raw_transcript_rows(guild_id, rows, mirror_daily=False)
    daily_scope_labels = ["guild"]
    if room_key:
        append_raw_transcript_rows(guild_id, rows, scope_type="room", scope_key=room_key, mirror_daily=False)
        daily_scope_labels.append(f"room:{room_key}")
    if person_key:
        append_raw_transcript_rows(guild_id, rows, scope_type="person", scope_key=person_key, mirror_daily=False)
        daily_scope_labels.append(f"person:{person_key}")
    if session_memory_key:
        append_raw_transcript_rows(guild_id, rows, scope_type="session", scope_key=session_memory_key, mirror_daily=False)
        daily_scope_labels.append(f"session:{session_memory_key}")
    vault_mirrored = True
    try:
        append_turn_rows_to_memory_vault(
            guild_id,
            rows,
            scope_labels=daily_scope_labels,
        )
    except Exception as exc:
        vault_mirrored = False
        print(f"[MEMORY VAULT] daily mirror failed: {exc!r}")

    mode = runtime_mode or "normal"
    if mode != "realtime":
        schedule_memory_vault_maintenance(guild_id, turn_scope=turn_scope)
    memory_writer_decision = build_memory_writer_decision(
        user_text=user_text,
        answer=answer,
        source=source,
        should_refresh_memory=should_run_memory_update(
            guild_id=guild_id,
            user_text=user_text,
            answer=answer,
            source=source,
            session_key=session_key,
        ),
        runtime_mode=mode,
    )
    decision_payload = memory_writer_decision.to_dict()
    decision_payload["source"] = source
    decision_payload["session_key"] = session_key
    decision_payload["raw_transcript_written"] = True
    decision_payload["vault_mirrored"] = vault_mirrored
    if not memory_writer_decision.should_run_summary_llm():
        mark_memory_writer_status(
            decision_payload,
            "skipped",
            event_path=MEMORY_WRITEBEHIND_STATUS_LOG,
            log=print,
            writebehind_reason="summary_llm_not_needed",
        )
        return decision_payload

    if mode == "realtime":
        mark_memory_writer_status(
            decision_payload,
            "deferred",
            event_path=MEMORY_WRITEBEHIND_STATUS_LOG,
            log=print,
            writebehind_reason="runtime_mode_realtime",
        )
        return decision_payload

    memory_task_key = session_memory_key or room_key or session_key or runtime_session_key(guild_id=guild_id)
    if mode == "batch" and memory_task_key is not None:
        memory_task_key = memory_writebehind_task_key(memory_task_key, decision_payload)
        existing = background_memory_tasks.get(memory_task_key)
        if existing is not None and not existing.done() and should_replace_existing_memory_task(decision_payload):
            existing.cancel()
        async def _batched_memory_refresh() -> None:
            try:
                await asyncio.sleep(1.5)
                if turn_scope is not None:
                    turn_scope.raise_if_cancelled()
                await run_memory_writebehind_steps(
                    decision_payload,
                    [
                        lambda: update_long_term_memory(
                            guild_id,
                            user_text,
                            answer,
                            room_key=room_key,
                            person_key=person_key,
                            session_memory_key=session_memory_key,
                            turn_scope=turn_scope,
                        ),
                        lambda: update_cognitive_state(
                            guild_id,
                            user_text,
                            session_key=session_key,
                            room_key=room_key,
                            person_key=person_key,
                            session_memory_key=session_memory_key,
                            source=source,
                            turn_scope=turn_scope,
                        ),
                    ],
                    log=print,
                    event_path=MEMORY_WRITEBEHIND_STATUS_LOG,
                )
            finally:
                task = background_memory_tasks.get(memory_task_key)
                if task is asyncio.current_task():
                    background_memory_tasks.pop(memory_task_key, None)
        mark_memory_writer_status(
            decision_payload,
            "queued",
            event_path=MEMORY_WRITEBEHIND_STATUS_LOG,
            log=print,
            writebehind_mode="batch",
        )
        background_memory_tasks[memory_task_key] = create_turn_scoped_task(_batched_memory_refresh(), turn_scope=turn_scope)
        return decision_payload

    async def _memory_writebehind() -> None:
        await run_memory_writebehind_steps(
            decision_payload,
            [
                lambda: update_long_term_memory(
                    guild_id,
                    user_text,
                    answer,
                    room_key=room_key,
                    person_key=person_key,
                    session_memory_key=session_memory_key,
                    turn_scope=turn_scope,
                ),
                lambda: update_cognitive_state(
                    guild_id,
                    user_text,
                    session_key=session_key,
                    room_key=room_key,
                    person_key=person_key,
                    session_memory_key=session_memory_key,
                    source=source,
                    turn_scope=turn_scope,
                ),
            ],
            log=print,
            event_path=MEMORY_WRITEBEHIND_STATUS_LOG,
        )

    mark_memory_writer_status(
        decision_payload,
        "queued",
        event_path=MEMORY_WRITEBEHIND_STATUS_LOG,
        log=print,
        writebehind_mode="normal",
    )
    create_turn_scoped_task(_memory_writebehind(), turn_scope=turn_scope)
    return decision_payload


RESPONSE_ACTION_TAGS = {"찾기": "search", "질문": "ask", "대기": "wait", "응답": "answer"}


def parse_response_action_tag(text: str) -> tuple[str | None, str]:
    raw = text or ""
    match = re.match(r"^\s*\[(찾기|질문|대기|응답)\]\s*", raw)
    if not match:
        return None, clean_text(raw)
    action = RESPONSE_ACTION_TAGS.get(match.group(1))
    stripped = clean_text(raw[match.end():])
    return action, stripped


def normalize_friend_style_output(text: str) -> str:
    cleaned = clean_text(text)
    if not cleaned:
        return ""
    cleaned = cleaned.replace("부르셨나요", "불렀어?")
    cleaned = cleaned.replace("말씀하세요", "말해")
    cleaned = re.sub(r"[\U0001F300-\U0001FAFF\u2600-\u27BF]+", "", cleaned)
    return clean_text(cleaned)


def sanitize_model_output(text: str) -> str:
    text = text or ""
    text = re.sub(r"<think>.*?</think>", "", text, flags=re.DOTALL | re.IGNORECASE)
    text = re.sub(r"<think>.*$", "", text, flags=re.DOTALL | re.IGNORECASE)
    text = normalize_omnivoice_tags(text)
    _action, cleaned = parse_response_action_tag(text)
    text = normalize_friend_style_output(cleaned)
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
        "messages": build_chat_messages([
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
        ], content_format=MAIN_LLM_CHAT_CONTENT_FORMAT),
        "temperature": 0.1,
        "max_tokens": 220,
        "stream": False,
        "cache_prompt": True,
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
            await speak_answer(vc, answer, turn_id=current_turn_id(session_key), session_key=session_key, turn_scope=turn_scope)
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
        "messages": build_chat_messages(
            [{"role": "user", "content": "짧게: 준비됐으면 '응'만 답해."}],
            content_format=MAIN_LLM_CHAT_CONTENT_FORMAT,
        ),
        "temperature": 0.0,
        "max_tokens": min(8, VOICE_LLM_MAX_TOKENS),
        "stream": True,
        "cache_prompt": True,
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


def stop_local_mic_service() -> None:
    global local_mic_service
    service = local_mic_service
    if service is None:
        local_mic_runtime_state["capture_ready"] = False
        return
    try:
        service.stop()
    finally:
        local_mic_runtime_state["capture_ready"] = False
        local_mic_service = None


atexit.register(stop_local_mic_service)


def resolve_evelyn_page_url() -> str | None:
    try:
        completed = subprocess.run(
            ["git", "config", "--get", "remote.origin.url"],
            cwd=str(PROJECT_ROOT),
            capture_output=True,
            text=True,
            timeout=2,
            check=False,
        )
    except Exception:
        return None
    return resolve_public_page_url(
        configured_url=EVELYN_PAGE_URL,
        remote_origin_url=completed.stdout,
    )


def should_drop_discord_audio_for_local_mic(member_id: int | None, *, source: str | None = None) -> bool:
    if source == "local_mic":
        return False
    capture_ready = bool(local_mic_service and local_mic_service.capture_ready)
    local_mic_runtime_state["capture_ready"] = capture_ready
    local_mic_recent = False
    last_segment_at = local_mic_runtime_state.get("last_segment_at")
    if isinstance(last_segment_at, (int, float)):
        local_mic_recent = (time.time() - float(last_segment_at)) <= LOCAL_MIC_DISCORD_SUPPRESS_AFTER_SEGMENT_SEC
    should_suppress = bool(
        local_mic_recent
        and should_route_discord_user_to_local_mic(
            member_id,
            preferred_user_ids=LOCAL_MIC_DISCORD_USER_IDS,
            capture_ready=capture_ready,
        )
    )
    local_mic_runtime_state["discord_suppression_active"] = should_suppress
    return should_suppress


def serialize_local_mic_runtime_state() -> dict[str, Any]:
    capture_ready = bool(local_mic_service and local_mic_service.capture_ready)
    local_mic_runtime_state["capture_ready"] = capture_ready
    last_segment_at = local_mic_runtime_state.get("last_segment_at")
    last_segment_age_sec = None
    if isinstance(last_segment_at, (int, float)):
        last_segment_age_sec = round(max(0.0, time.time() - float(last_segment_at)), 3)
    last_input_age_sec = None
    if local_mic_service is not None and isinstance(local_mic_service.last_input_at, (int, float)):
        last_input_age_sec = round(max(0.0, time.time() - float(local_mic_service.last_input_at)), 3)
    return {
        "enabled": bool(local_mic_runtime_state.get("enabled")),
        "captureReady": capture_ready,
        "lastError": local_mic_runtime_state.get("last_error"),
        "routedUserIds": list(local_mic_runtime_state.get("routed_user_ids") or []),
        "segmentCount": int(local_mic_runtime_state.get("segment_count") or 0),
        "lastSegmentAgeSec": last_segment_age_sec,
        "lastSegmentDurationSec": local_mic_runtime_state.get("last_segment_duration_sec"),
        "inputBlockCount": int(getattr(local_mic_service, "input_block_count", 0) or 0),
        "lastInputAgeSec": last_input_age_sec,
        "lastInputLevel": round(float(getattr(local_mic_service, "last_input_level", 0.0) or 0.0), 6),
        "maxInputLevel": round(float(getattr(local_mic_service, "max_input_level", 0.0) or 0.0), 6),
        "lastInputStatus": getattr(local_mic_service, "last_input_status", None),
        "discordSuppressionActive": bool(local_mic_runtime_state.get("discord_suppression_active")),
        "discordSuppressAfterSegmentSec": LOCAL_MIC_DISCORD_SUPPRESS_AFTER_SEGMENT_SEC,
        "device": LOCAL_MIC_DEVICE or "default",
        "sampleRate": LOCAL_MIC_SAMPLE_RATE,
        "captureSampleRate": int(getattr(local_mic_service, "sample_rate", LOCAL_MIC_SAMPLE_RATE) or LOCAL_MIC_SAMPLE_RATE),
        "startThreshold": LOCAL_MIC_START_THRESHOLD,
        "continueThreshold": LOCAL_MIC_CONTINUE_THRESHOLD,
    }


def local_mic_status_line() -> str:
    state = serialize_local_mic_runtime_state()
    if not state["enabled"]:
        return "disabled"
    if state["captureReady"]:
        age = state.get("lastSegmentAgeSec")
        segment_text = "no segments" if age is None else f"last segment {age:.1f}s ago"
        suppress_text = "discord suppress on" if state.get("discordSuppressionActive") else "discord fallback on"
        return f"ready | {segment_text} | {suppress_text}"
    error = clean_text(str(state.get("lastError") or "capture not ready"))
    return f"not ready | {error}"


async def handle_local_mic_segment(pcm_bytes: bytes, debug_meta: dict[str, Any] | None = None) -> None:
    if not pcm_bytes:
        return
    local_mic_runtime_state["segment_count"] = int(local_mic_runtime_state.get("segment_count") or 0) + 1
    local_mic_runtime_state["last_segment_at"] = time.time()
    if isinstance(debug_meta, dict):
        local_mic_runtime_state["last_segment_duration_sec"] = debug_meta.get("duration_sec")
    target = resolve_local_mic_target(guilds=bot.guilds, preferred_user_ids=LOCAL_MIC_DISCORD_USER_IDS)
    if target is None:
        local_mic_runtime_state["last_error"] = "no_active_discord_target_for_local_mic"
        return
    routed_meta = dict(debug_meta or {})
    routed_meta["source"] = "local_mic"
    routed_meta["routed_discord_user_id"] = int(getattr(target.member, "id", 0) or 0)
    await process_member_audio(target.member, pcm_bytes, routed_meta)


async def ensure_local_mic_service_started() -> None:
    global local_mic_service
    if not LOCAL_MIC_ENABLED or not LOCAL_MIC_DISCORD_USER_IDS:
        local_mic_runtime_state["capture_ready"] = False
        return
    if local_mic_service is not None and local_mic_service.capture_ready:
        local_mic_runtime_state["capture_ready"] = True
        return

    loop = asyncio.get_running_loop()

    def _dispatch_local_segment(pcm_bytes: bytes, meta: dict[str, Any]) -> None:
        loop.call_soon_threadsafe(asyncio.create_task, handle_local_mic_segment(pcm_bytes, meta))

    service = LocalMicCaptureService(
        on_segment=_dispatch_local_segment,
        sample_rate=LOCAL_MIC_SAMPLE_RATE,
        block_ms=LOCAL_MIC_BLOCK_MS,
        start_threshold=LOCAL_MIC_START_THRESHOLD,
        continue_threshold=LOCAL_MIC_CONTINUE_THRESHOLD,
        start_consecutive=LOCAL_MIC_START_CONSECUTIVE,
        min_voiced_ms=LOCAL_MIC_MIN_VOICED_MS,
        max_silence_ms=LOCAL_MIC_MAX_SILENCE_MS,
        preroll_ms=LOCAL_MIC_PREROLL_MS,
        max_segment_sec=LOCAL_MIC_MAX_SEGMENT_SEC,
        device=LOCAL_MIC_DEVICE,
        queue_max=LOCAL_MIC_QUEUE_MAX,
    )
    started = service.start()
    local_mic_runtime_state["capture_ready"] = bool(started and service.capture_ready)
    local_mic_runtime_state["last_error"] = service.last_error
    if local_mic_runtime_state["capture_ready"]:
        local_mic_service = service
        print(
            f"[LOCAL MIC] ready user_ids={sorted(LOCAL_MIC_DISCORD_USER_IDS)} sample_rate={LOCAL_MIC_SAMPLE_RATE} device={LOCAL_MIC_DEVICE or 'default'}"
        )
        return
    print(f"[LOCAL MIC] unavailable err={service.last_error or 'capture_not_ready'}")


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

    meta = metrics.get("meta") or {}
    record_turn_path_summary(meta, marks, total_ms)
    p95_summary = summarize_p95_metrics()
    if VOICE_BOTTLENECK_LOGS or should_log_voice_timing(total_ms):
        lines = [
            "[VOICE BOTTLENECK]",
            f"label={label}",
            f"total_ms={total_ms:.0f}",
            f"turn_type={meta.get('turn_type') or '-'}",
            f"selected_path={meta.get('selected_path') or '-'}",
            f"reply_source={meta.get('reply_source') or '-'}",
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

    log_turn_event(
        event_name,
        **build_turn_summary_payload(
            metrics,
            label=label,
            event_name=event_name,
            total_ms=round(total_ms, 1),
            p95_summary=p95_summary,
            extra=extra or None,
        ),
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
    turn_scope: TurnScope | None = None,
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

        async def stream_with_voice(voice_name: str) -> TtsSynthResult:
            request_id = f"{turn_id or 'turnless'}:{chunk_index or 0}:{uuid.uuid4().hex[:10]}"
            tts_request = TtsSynthRequest(
                request_id=request_id,
                turn_id=turn_id or "",
                text=text,
                voice=voice_name,
                voice_profile=voice_name.split(":", 1)[1] if voice_name.startswith("clone:") else None,
                response_format="pcm",
                sample_rate_hz=OMNIVOICE_PCM_RATE,
                stream=OMNIVOICE_STREAM,
                chunk_index=int(chunk_index or 0),
                metadata={"session_key": session_key or "", "text_len": len(text)},
            )
            payload = {
                "model": OMNIVOICE_MODEL,
                "input": text,
                "voice": tts_request.voice,
                "response_format": tts_request.response_format,
                "stream": tts_request.stream,
            }
            if OMNIVOICE_LANGUAGE:
                payload["language"] = OMNIVOICE_LANGUAGE
            if turn_id:
                payload["turn_id"] = turn_id
            if session_key:
                payload["session_key"] = session_key

            nonlocal first_pcm_logged
            request_started_mono = time.monotonic()
            first_audio_ms: float | None = None

            if on_request_start is not None:
                on_request_start()
            log_turn_event(
                "tts_request_started",
                **merge_log_event_payload(
                    explicit={
                        "request_id": tts_request.request_id,
                        "voice": tts_request.voice,
                        "voice_profile": tts_request.voice_profile,
                    },
                    extra=trace,
                ),
            )
            async with session.post(
                f"{OMNIVOICE_SERVER_URL}/v1/audio/speech",
                json=payload,
                timeout=timeout,
            ) as resp:
                if on_response_headers is not None:
                    on_response_headers()
                if resp.status != 200:
                    error_text = await resp.text()
                    return TtsSynthResult(
                        request_id=tts_request.request_id,
                        turn_id=tts_request.turn_id,
                        backend="omnivoice_http",
                        ok=False,
                        response_format=tts_request.response_format,
                        sample_rate_hz=tts_request.sample_rate_hz,
                        profile_resolved=tts_request.voice,
                        status_code=resp.status,
                        latency_ms=(time.monotonic() - request_started_mono) * 1000.0,
                        first_audio_ms=first_audio_ms,
                        error_code="http_error",
                        error_text=error_text,
                        metadata=tts_request.metadata,
                    )

                async for chunk in resp.content.iter_chunked(8192):
                    if chunk:
                        if on_first_byte is not None and not first_pcm_logged:
                            on_first_byte()
                        if not first_pcm_logged:
                            first_pcm_logged = True
                            first_audio_ms = (time.monotonic() - request_started_mono) * 1000.0
                            log_turn_event(
                                "tts_first_pcm_received",
                                **merge_log_event_payload(
                                    explicit={"request_id": tts_request.request_id, "bytes": len(chunk)},
                                    extra=trace,
                                ),
                            )
                        source.feed_pcm24_mono(chunk)
                return TtsSynthResult(
                    request_id=tts_request.request_id,
                    turn_id=tts_request.turn_id,
                    backend="omnivoice_http",
                    ok=True,
                    response_format=tts_request.response_format,
                    sample_rate_hz=tts_request.sample_rate_hz,
                    profile_resolved=tts_request.voice,
                    status_code=resp.status,
                    latency_ms=(time.monotonic() - request_started_mono) * 1000.0,
                    first_audio_ms=first_audio_ms,
                    metadata=tts_request.metadata,
                )

        try:
            tts_result = await stream_with_voice(OMNIVOICE_VOICE)
            if not tts_result.ok:
                if OMNIVOICE_VOICE.startswith("clone:"):
                    error_text = tts_result.error_text or ""
                    print(f"[TTS FALLBACK] clone voice 실패 -> auto 사용 | voice={OMNIVOICE_VOICE} err={error_text[:200]}")
                    tts_result = await stream_with_voice("auto")
                if not tts_result.ok:
                    raise RuntimeError(f"OmniVoice 서버 오류: {(tts_result.error_text or '')[:300]}")
        except asyncio.CancelledError:
            record_voice_pipeline_failure("tts_producer_cancelled", "cancelled", None, **trace)
            source.cleanup()
            raise
        except Exception as e:
            record_voice_pipeline_failure("tts_request_failed", e, None, **trace)
            source.fail(e)
            return

        source.finish()

    create_turn_scoped_task(producer(), turn_scope=turn_scope)
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
        save_last_voice_channel_state(guild, target_channel, reason="ensure_listening", manual_disconnect=False)
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


async def restore_last_voice_channel(guild: discord.Guild | None = None, *, force: bool = False) -> tuple[bool, str]:
    if not VOICE_REJOIN_ON_READY and not force:
        return False, "rejoin_disabled"
    state = load_last_voice_channel_state()
    if not state:
        return False, "no_saved_voice_channel"
    if state.get("manual_disconnect") and not force:
        return False, "manual_disconnect"

    guild_id = int(state.get("guild_id") or 0)
    channel_id = int(state.get("channel_id") or 0)
    if not guild_id or not channel_id:
        return False, "invalid_saved_voice_channel"

    target_guild = guild or bot.get_guild(guild_id)
    if target_guild is None or int(target_guild.id) != guild_id:
        return False, "saved_guild_not_available"
    channel = target_guild.get_channel(channel_id)
    if not isinstance(channel, discord.VoiceChannel):
        return False, "saved_channel_not_available"

    increment_voice_pipeline_counter("voice_rejoin_attempts")
    voice_pipeline_state["last_voice_rejoin_at"] = time.time()
    voice_pipeline_state["last_voice_rejoin_error"] = None
    try:
        vc = await ensure_listening_voice_client(target_guild, channel)
    except Exception as exc:
        increment_voice_pipeline_counter("voice_rejoin_fail")
        voice_pipeline_state["last_voice_rejoin_error"] = repr(exc)
        print(f"[VOICE REJOIN FAIL] guild={guild_id} channel={channel_id} err={exc!r}")
        return False, repr(exc)
    if vc is None:
        increment_voice_pipeline_counter("voice_rejoin_fail")
        voice_pipeline_state["last_voice_rejoin_error"] = "voice_client_none"
        return False, "voice_client_none"
    increment_voice_pipeline_counter("voice_rejoin_success")
    save_last_voice_channel_state(target_guild, channel, reason="restore_last_voice_channel", manual_disconnect=False)
    print(f"[VOICE REJOIN OK] guild={guild_id} channel={getattr(channel, 'name', None)}")
    return True, getattr(channel, "name", str(channel_id))


async def stop_active_tts_playback(guild_id: int | None, *, reason: str = "interrupt") -> None:
    stopped = await stop_tracked_tts_playback(
        tracker=tts_playback_tracker,
        guild_id=guild_id,
    )
    if not stopped:
        return
    log_turn_event("tts_interrupt", guild_id=guild_id, reason=reason)


def cached_audio_path_for_answer(answer: str) -> Path | None:
    return resolve_cached_tts_audio_path(
        answer,
        enabled=CACHED_AUDIO_ENABLED,
        canned_text=CANNED_WAKE_REPLY_TEXT,
        canned_audio_path=CANNED_WAKE_REPLY_AUDIO,
        project_root=PROJECT_ROOT,
    )


async def play_cached_answer_audio(
    vc: discord.VoiceClient,
    answer: str,
    *,
    turn_id: str | None = None,
    session_key: str | None = None,
    metrics: dict | None = None,
) -> bool:
    path = cached_audio_path_for_answer(answer)
    if path is None:
        return False

    guild_id = getattr(getattr(vc, "guild", None), "id", None)
    source = CachedWaveAudioSource(
        path,
        on_first_packet_sent=lambda: log_turn_event(
            "first_packet_sent",
            turn_id=turn_id,
            chunk_index=1,
            session_key=session_key,
            source_type="CachedWaveAudioSource",
        ) or log_voice_latency(metrics, "first_packet_sent_logged", "캐시 오디오 첫 패킷 송신 시간"),
    )
    playback_task = asyncio.current_task()
    start_tts_playback_tracking(
        tracker=tts_playback_tracker,
        guild_id=guild_id,
        mark_speaking=True,
        vc=vc,
        playback_source=source,
        playback_task=playback_task,
        turn_id=turn_id,
        session_key=session_key,
        source_type=type(source).__name__,
    )

    completed = False
    try:
        log_turn_event(
            "cached_audio_playback_selected",
            turn_id=turn_id,
            session_key=session_key,
            path=str(path),
            answer=clean_text(answer),
        )
        await play_audio_source(
            vc,
            source,
            trace_payload={
                "turn_id": turn_id,
                "chunk_index": 1,
                "session_key": session_key,
                "source_type": type(source).__name__,
                "cached_audio_path": str(path),
            },
        )
        completed = True
    finally:
        source.cleanup()
        mark_tts_playback_summary_state(metrics, started=completed, completed=completed)
        finish_tts_playback_tracking(
            tracker=tts_playback_tracker,
            guild_id=guild_id,
            mark_audio_end=True,
        )
    return True


async def speak_answer(
    vc: discord.VoiceClient,
    answer: str,
    *,
    turn_id: str | None = None,
    session_key: str | None = None,
    turn_scope: TurnScope | None = None,
    metrics: dict | None = None,
) -> None:
    guild_id = getattr(getattr(vc, "guild", None), "id", None)

    if await play_cached_answer_audio(
        vc,
        answer,
        turn_id=turn_id,
        session_key=session_key,
        metrics=metrics,
    ):
        return

    async with tts_lock:
        source = await create_omnivoice_source(
            answer,
            turn_id=turn_id,
            chunk_index=1,
            session_key=session_key,
            turn_scope=turn_scope,
            trace_payload={"source_type": "OmniVoicePCMStream"},
            on_first_packet_sent=lambda: log_turn_event(
                "first_packet_sent",
                turn_id=turn_id,
                chunk_index=1,
                session_key=session_key,
            ) or log_voice_latency(metrics, "first_packet_sent_logged", "첫 패킷 송신 시간"),
        )
        completed = False
        try:
            mark_tts_speaking(tracker=tts_playback_tracker, guild_id=guild_id)
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
            completed = True
        finally:
            mark_tts_playback_summary_state(metrics, started=completed, completed=completed)
            finish_tts_playback_tracking(
                tracker=tts_playback_tracker,
                guild_id=guild_id,
                mark_audio_end=True,
                clear_registry=False,
            )


async def _prefetch_tts_sources(
    sentence_queue: "asyncio.Queue[str | None]",
    prepared_queue: "asyncio.Queue[object]",
    *,
    metrics: dict | None = None,
    turn_id: str | None = None,
    session_key: str | None = None,
    turn_scope: TurnScope | None = None,
) -> None:
    task = _attach_current_task(turn_scope)

    def check_cancelled() -> None:
        if turn_scope is not None:
            turn_scope.raise_if_cancelled()

    async def synthesize_source(sentence: str, chunk_index: int) -> OmniVoicePCMStream:
        return await create_omnivoice_source(
            sentence,
            turn_id=turn_id,
            chunk_index=chunk_index,
            session_key=session_key,
            turn_scope=turn_scope,
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

    def on_failure(exc: Exception) -> None:
        record_voice_pipeline_failure(
            "tts_playback_failed",
            exc,
            metrics,
            turn_id=turn_id,
            session_key=session_key,
            stage="prefetch",
        )

    try:
        await prefetch_tts_sources(
            sentence_queue,
            prepared_queue,
            synthesize_source=synthesize_source,
            ready_timeout_sec=OMNIVOICE_TIMEOUT_SEC,
            check_cancelled=check_cancelled,
            on_failure=on_failure,
        )
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
    playback_completed = False
    task = _attach_current_task(turn_scope)

    try:
        async with tts_lock:
            prepared_queue: asyncio.Queue[object] = asyncio.Queue(maxsize=max(1, TTS_PREFETCH_CHUNKS))
            playback_source = QueuedAudioSource(
                trace_payload={
                    "turn_id": turn_id,
                    "session_key": session_key,
                    "source_type": "QueuedAudioSource",
                }
            )
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

            def create_playback_task() -> asyncio.Task:
                return create_turn_scoped_task(
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

            def on_playback_started(task: asyncio.Task) -> None:
                nonlocal did_speak, playback_task
                did_speak = True
                playback_task = task
                mark_tts_playback_summary_state(metrics, started=True)
                update_tts_playback_tracking(
                    tracker=tts_playback_tracker,
                    guild_id=guild_id,
                    playback_task=playback_task,
                )

            def on_source_ready() -> None:
                if guild_id is not None and not did_speak:
                    mark_tts_speaking(tracker=tts_playback_tracker, guild_id=guild_id)

            def on_prepared_failure(item: Exception) -> None:
                record_voice_pipeline_failure(
                    "tts_playback_failed",
                    item,
                    metrics,
                    turn_id=turn_id,
                    session_key=session_key,
                    stage="prepared_exception",
                )

            playback_queue = PreparedTtsPlaybackQueue(
                prepared_queue,
                playback_source,
                turn_id=turn_id,
                session_key=session_key,
                lookahead_chunks=TTS_PLAYBACK_START_LOOKAHEAD_CHUNKS,
                lookahead_timeout_ms=TTS_PLAYBACK_START_LOOKAHEAD_TIMEOUT_MS,
                log=print,
                on_source_ready=on_source_ready,
                on_failure=on_prepared_failure,
            )
            playback_starter = PreparedPlaybackStarter(
                playback_queue,
                create_playback_task=create_playback_task,
                on_started=on_playback_started,
                log=print,
            )

            def check_cancelled() -> None:
                if turn_scope is not None:
                    turn_scope.raise_if_cancelled()

            start_tts_playback_tracking(
                tracker=tts_playback_tracker,
                guild_id=guild_id,
                vc=vc,
                sentence_queue=sentence_queue,
                prepared_queue=prepared_queue,
                playback_source=playback_source,
                prefetch_task=prefetch_task,
                playback_task=playback_task,
                turn_id=turn_id,
                session_key=session_key,
            )
            try:
                await drain_prepared_tts_playback(
                    prepared_queue,
                    playback_queue,
                    start_playback_once=playback_starter.start_once,
                    get_playback_task=playback_starter.get_task,
                    check_cancelled=check_cancelled,
                )
                playback_completed = did_speak
            finally:
                mark_tts_playback_summary_state(
                    metrics,
                    started=did_speak,
                    completed=playback_completed,
                )
                await cleanup_tts_stream_tasks(
                    playback_source=playback_source,
                    playback_task=playback_task,
                    prefetch_task=prefetch_task,
                )
                finish_tts_playback_tracking(
                    tracker=tts_playback_tracker,
                    guild_id=guild_id,
                    mark_audio_end=did_speak,
                )
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
    lightweight_persona_turn = is_casual_call_or_status_question(guided_user_text)
    live_minecraft_state = None if lightweight_persona_turn else await observe_live_minecraft_state(guild_id)
    runtime_status_context = "" if lightweight_persona_turn else await build_runtime_status_context()
    final_user_text = f"{guided_user_text}\n\n{build_main_response_guidance(cognitive_state, source=source, user_text=guided_user_text, session_key=session_key, guild_id=guild_id, minecraft_state=live_minecraft_state, runtime_status_context=runtime_status_context)}"

    payload = {
        "model": MODEL_NAME,
        "messages": build_chat_messages(
            messages + [{"role": "user", "content": final_user_text}],
            content_format=MAIN_LLM_CHAT_CONTENT_FORMAT,
        ),
        "temperature": 0.0,
        "max_tokens": min(40, VOICE_LLM_MAX_TOKENS),
        "stream": False,
        "cache_prompt": True,
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
    messages, cognitive_state, _route, _context_policy = await prepare_llm_messages(
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
    live_minecraft_state = None if is_casual_call_or_status_question(user_text) else await observe_live_minecraft_state(guild_id)
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
        "messages": build_chat_messages(
            messages + [{"role": "user", "content": followup_prompt}],
            content_format=MAIN_LLM_CHAT_CONTENT_FORMAT,
        ),
        "temperature": 0.0,
        "max_tokens": min(64, VOICE_LLM_MAX_TOKENS),
        "stream": False,
        "cache_prompt": True,
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
    log_voice_stage(metrics, "LLM 2단계 요청 시작", extra=f"source={source} user_text_len={len(clean_text(user_text))}")
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
        log_voice_stage(metrics, "LLM 2단계 요청 끝남", extra=f"skill_route={route_decision.route} answer_len={len(skill_route_answer)}")
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
        log_voice_stage(metrics, "LLM 2단계 요청 끝남", extra=f"policy_len={len(route_decision.user_visible_preface)}")
        return build_answer_payload_from_text(route_decision.user_visible_preface).display_text

    guided_user_text = route_decision.prompt_text or user_text
    lightweight_persona_turn = is_casual_call_or_status_question(guided_user_text)
    live_minecraft_state = None if lightweight_persona_turn else await observe_live_minecraft_state(guild_id)
    runtime_status_context = "" if lightweight_persona_turn else await build_runtime_status_context()
    final_user_text = f"{guided_user_text}\n\n{build_main_response_guidance(cognitive_state, source=source, user_text=guided_user_text, session_key=session_key, guild_id=guild_id, minecraft_state=live_minecraft_state, runtime_status_context=runtime_status_context)}"
    payload = build_main_llm_payload(
        model_name=MODEL_NAME,
        messages=messages,
        final_user_text=final_user_text,
        source=source,
        stream=False,
        content_format=MAIN_LLM_CHAT_CONTENT_FORMAT,
        max_tokens=VOICE_LLM_MAX_TOKENS,
    )
    answer, answer_source = await execute_main_llm_once(
        payload=payload,
        user_text=user_text,
    )
    if answer_source == "reasoning":
        log_voice_stage(metrics, "LLM 2단계 요청 끝남", extra=f"reasoning_len={len(answer)}")
    elif answer_source.startswith("fallback"):
        log_voice_stage(metrics, "LLM canned reply 사용", extra=f"reason={answer_source} fallback_len={len(answer)}")
    else:
        log_voice_stage(metrics, "LLM 2단계 요청 끝남", extra=f"answer_len={len(answer)}")
    return build_answer_payload_from_text(answer).display_text


def build_stream_speech_chunker(*, metrics: dict | None) -> SpeechChunker:
    speech_chunker = SpeechChunker()
    speech_chunker.config.first_window = ChunkWindow(
        max(1, TTS_FIRST_CHUNK_MIN_CHARS),
        max(TTS_FIRST_CHUNK_MIN_CHARS, TTS_FIRST_CHUNK_TARGET_CHARS),
        max(TTS_FIRST_CHUNK_TARGET_CHARS, TTS_FIRST_CHUNK_MAX_CHARS),
        True,
        False,
    )
    speech_chunker.config.next_window = ChunkWindow(
        max(1, TTS_NEXT_CHUNK_MIN_CHARS),
        max(TTS_NEXT_CHUNK_MIN_CHARS, TTS_NEXT_CHUNK_TARGET_CHARS),
        max(TTS_NEXT_CHUNK_TARGET_CHARS, TTS_NEXT_CHUNK_MAX_CHARS),
        False,
        True,
    )
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
                return attach_minecraft_runtime_snapshot(
                    merged,
                    source="live_status",
                    now=time.time(),
                    observed_at=time.time(),
                    stale_after_sec=CONTROL_PAGE_MINECRAFT_CACHE_REFRESH_SEC,
                    expired_after_sec=CONTROL_PAGE_MINECRAFT_CACHE_MAX_STALE_SEC,
                )
    try:
        observed = await client.observe(ensure_service=False)
    except Exception:
        return None
    if not isinstance(observed, dict):
        return None
    merged = _merge_voyager_status_into_state(None, observed) if (observed.get("connected") or observed.get("active") or observed.get("position")) else None
    if isinstance(merged, dict):
        return attach_minecraft_runtime_snapshot(
            merged,
            source="live_observe",
            now=time.time(),
            observed_at=time.time(),
            stale_after_sec=CONTROL_PAGE_MINECRAFT_CACHE_REFRESH_SEC,
            expired_after_sec=CONTROL_PAGE_MINECRAFT_CACHE_MAX_STALE_SEC,
        )
    return None


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


CONTROL_PAGE_COMMANDS: list[dict[str, str]] = [
    {"command": "/help", "template": "/help", "summary": "페이지에서 쓸 수 있는 명령어 목록 보기", "visibility": "always"},
    {"command": "/status", "template": "/status", "summary": "현재 Evelyn, 음성, TTS 상태 보기", "visibility": "always"},
    {"command": "/inventory", "template": "/inventory", "summary": "현재 Minecraft 인벤토리 요약 보기", "visibility": "minecraft-active"},
    {"command": "/voyager stats", "template": "/voyager stats", "summary": "Voyager 진행 상태와 평가 지표 보기", "visibility": "minecraft-active"},
    {"command": "/minecraft status", "template": "/minecraft status", "summary": "Minecraft 연결과 현재 task 상태 보기", "visibility": "minecraft-active"},
    {"command": "/minecraft connect", "template": "/minecraft connect", "summary": "Voyager Minecraft 모드 시작", "visibility": "minecraft-idle"},
    {"command": "/minecraft disconnect", "template": "/minecraft disconnect", "summary": "Voyager Minecraft 모드 중지", "visibility": "minecraft-active"},
    {"command": "/minecraft goal <goal>", "template": "/minecraft goal ", "summary": "Minecraft 목표를 새 값으로 변경", "visibility": "minecraft-active"},
    {"command": "/autonomy status", "template": "/autonomy status", "summary": "Evelyn 자율 행동 엔진 상태 보기", "visibility": "always"},
    {"command": "/shutdown", "template": "/shutdown", "summary": "Shut down the full Evelyn stack", "visibility": "always"},
    {"command": "/windows", "template": "/windows", "summary": "List background console windows and their state", "visibility": "always"},
    {"command": "/show <window>", "template": "/show ", "summary": f"Bring one background window to front ({control_page_window_choices_text()})", "visibility": "always"},
    {"command": "/ui <action> <panel>", "template": "/ui ", "summary": "Control page panels: show, hide, toggle, focus, reset", "visibility": "always"},
]

CONTROL_PAGE_COMMANDS.insert(
    2,
    {"command": "/voice status", "template": "/voice status", "summary": "Show voice pipeline queue, STT, and TTS health", "visibility": "always"},
)
CONTROL_PAGE_COMMANDS.insert(
    3,
    {"command": "/voice reconnect", "template": "/voice reconnect", "summary": "Reconnect to the last saved voice channel", "visibility": "always"},
)

CONTROL_PAGE_UI_PANELS: dict[str, str] = {
    "runtime": "Runtime",
    "diagnostics": "Diagnostics",
    "avatar": "Avatar",
    "chat": "Chat",
}

CONTROL_PAGE_UI_PANEL_ALIASES: dict[str, str] = {
    "status": "runtime",
    "state": "runtime",
    "diag": "diagnostics",
    "diagnostic": "diagnostics",
    "logs": "diagnostics",
    "model": "avatar",
    "center": "avatar",
    "main": "avatar",
    "evelyn": "avatar",
    "control": "chat",
    "commands": "chat",
    "command": "chat",
}


def normalize_control_page_ui_panel(value: str | None) -> str | None:
    key = clean_text(str(value or "")).lower().strip()
    if not key:
        return None
    if key in CONTROL_PAGE_UI_PANELS:
        return key
    return CONTROL_PAGE_UI_PANEL_ALIASES.get(key)


def enqueue_control_page_ui_command(action: str, *, panel_id: str | None = None) -> dict[str, Any]:
    global control_page_ui_command_seq
    cleaned_action = clean_text(action).lower()
    control_page_ui_command_seq += 1
    command = {
        "id": control_page_ui_command_seq,
        "action": cleaned_action,
        "panel": panel_id,
        "at": time.time(),
    }
    control_page_ui_commands.append(command)
    if len(control_page_ui_commands) > 40:
        del control_page_ui_commands[:-40]
    return dict(command)


def build_control_page_panel_state() -> dict[str, Any]:
    return {
        "revision": control_page_ui_command_seq,
        "commands": [dict(command) for command in control_page_ui_commands[-40:]],
        "panels": [
            {"id": panel_id, "label": label}
            for panel_id, label in CONTROL_PAGE_UI_PANELS.items()
        ],
    }


def is_control_page_minecraft_session_active(snapshot: dict[str, Any] | None) -> bool:
    if not isinstance(snapshot, dict):
        return False
    if snapshot.get("voyager_connected") or snapshot.get("connected") or snapshot.get("active"):
        return True
    position = snapshot.get("position")
    if isinstance(position, dict) and any(value is not None for value in position.values()):
        return True
    if snapshot.get("health") is not None or snapshot.get("hunger") is not None:
        return True
    return False


def build_control_page_commands(*, minecraft_session_active: bool) -> list[dict[str, str]]:
    commands: list[dict[str, str]] = []
    for item in CONTROL_PAGE_COMMANDS:
        visibility = item.get("visibility", "always")
        if visibility == "minecraft-active" and not minecraft_session_active:
            continue
        if visibility == "minecraft-idle" and minecraft_session_active:
            continue
        commands.append({key: value for key, value in item.items() if key != "visibility"})
    return commands


def build_control_page_all_commands() -> list[dict[str, str]]:
    return [
        {key: value for key, value in item.items() if key != "visibility"}
        for item in CONTROL_PAGE_COMMANDS
    ]


def control_page_local_url() -> str:
    return f"http://{CONTROL_PAGE_HOST}:{CONTROL_PAGE_PORT}/"


def control_page_session_key(guild_id: int) -> str:
    return f"control-page:{guild_id}"


def build_control_page_runtime_summary(
    *,
    bot_ready: bool,
    voyager_ready: bool,
    codex_required: bool,
    codex_ready: bool | None,
) -> str:
    parts = [
        "bot ready" if bot_ready else "bot down",
        "voyager ready" if voyager_ready else "minecraft idle",
    ]
    if codex_required:
        parts.append("codex ready" if codex_ready else "codex standby")
    return " | ".join(parts)


def build_control_page_ui_state(
    *,
    guild_available: bool,
    listening: bool,
    speaking: bool,
    minecraft_running: bool,
    minecraft_session_active: bool,
    minecraft_snapshot_stale: bool,
    minecraft_last_error: str | None,
) -> dict[str, Any]:
    if not guild_available:
        return {
            "mode": "default",
            "submode": "offline",
            "reason": "guild_not_available",
        }
    if minecraft_session_active:
        return {
            "mode": "minecraft",
            "submode": "minecraft-live",
            "reason": "minecraft_session_active",
        }
    if minecraft_running:
        return {
            "mode": "default",
            "submode": "voyager-warmup",
            "reason": "voyager_running_without_live_session",
        }
    if speaking:
        return {
            "mode": "default",
            "submode": "voice-speaking",
            "reason": "tts_speaking",
        }
    if listening:
        return {
            "mode": "default",
            "submode": "voice-listening",
            "reason": "voice_listening",
        }
    if minecraft_snapshot_stale:
        return {
            "mode": "default",
            "submode": "stale",
            "reason": "minecraft_snapshot_stale",
        }
    if clean_text(str(minecraft_last_error or "")):
        return {
            "mode": "default",
            "submode": "issue",
            "reason": "minecraft_last_error",
        }
    return {
        "mode": "default",
        "submode": "idle",
        "reason": "default_idle",
    }


def append_control_page_chat_log(guild_id: int, role: str, author: str, text: str) -> None:
    cleaned_text = clean_text(text)
    if not cleaned_text:
        return
    rows = control_page_chat_logs.setdefault(guild_id, [])
    rows.append(
        {
            "role": role,
            "author": clean_text(author) or ("Evelyn" if role == "assistant" else "User"),
            "text": cleaned_text,
            "at": time.time(),
        }
    )
    if len(rows) > CONTROL_PAGE_CHAT_LOG_LIMIT:
        del rows[:-CONTROL_PAGE_CHAT_LOG_LIMIT]


def get_control_page_chat_log(guild_id: int) -> list[dict[str, Any]]:
    return [dict(row) for row in control_page_chat_logs.get(guild_id, [])]


def select_control_page_guild(requested_guild_id: int | None = None) -> discord.Guild | None:
    if requested_guild_id is not None:
        return bot.get_guild(requested_guild_id)
    preferred_ids: list[int] = []
    preferred_ids.extend(tracked_tts_playback_guild_ids(tts_playback_tracker))
    for guild in bot.guilds:
        if guild.voice_client is not None:
            preferred_ids.append(guild.id)
    preferred_ids.extend(guild.id for guild in bot.guilds)
    seen: set[int] = set()
    for guild_id in preferred_ids:
        if guild_id in seen:
            continue
        seen.add(guild_id)
        guild = bot.get_guild(guild_id)
        if guild is not None:
            return guild
    return None


def resolve_guild_member_name(guild: discord.Guild | None, user_id: int | None) -> str:
    if guild is None or user_id is None:
        return "없음"
    member = guild.get_member(int(user_id))
    if member is None:
        return f"user:{int(user_id)}"
    return clean_text(member.display_name or member.name or str(member.id)) or f"user:{member.id}"


def current_tts_target_name(guild: discord.Guild | None) -> str:
    if guild is None:
        return "없음"
    playback = get_tracked_tts_playback(tts_playback_tracker, guild.id)
    if not isinstance(playback, dict):
        return "없음"
    session_key = clean_text(str(playback.get("session_key") or ""))
    if not session_key:
        return "없음"
    target_user_id = active_session_user_ids.get(session_key)
    return resolve_guild_member_name(guild, target_user_id)


def format_position_short(position: Any) -> str:
    if isinstance(position, dict):
        x = position.get("x")
        y = position.get("y")
        z = position.get("z")
        if all(isinstance(value, (int, float)) for value in (x, y, z)):
            return f"{x:.1f}, {y:.1f}, {z:.1f}"
    if isinstance(position, (list, tuple)) and len(position) >= 3 and all(isinstance(value, (int, float)) for value in position[:3]):
        return f"{float(position[0]):.1f}, {float(position[1]):.1f}, {float(position[2]):.1f}"
    cleaned = clean_text(str(position or ""))
    return cleaned or "unknown"


def normalize_inventory_top_entries(inventory: Any, *, limit: int = 8) -> list[dict[str, Any]]:
    entries: list[tuple[str, int]] = []
    if isinstance(inventory, dict):
        for name, count in inventory.items():
            cleaned_name = clean_text(str(name or ""))
            if not cleaned_name:
                continue
            try:
                count_int = int(count)
            except (TypeError, ValueError):
                continue
            if count_int > 0:
                entries.append((cleaned_name, count_int))
    elif isinstance(inventory, list):
        for row in inventory:
            if not isinstance(row, dict):
                continue
            cleaned_name = clean_text(str(row.get("name") or row.get("item") or ""))
            if not cleaned_name:
                continue
            try:
                count_int = int(row.get("count") or row.get("quantity") or 0)
            except (TypeError, ValueError):
                continue
            if count_int > 0:
                entries.append((cleaned_name, count_int))
    entries.sort(key=lambda item: (-item[1], item[0]))
    return [{"name": name, "count": count} for name, count in entries[:limit]]


def summarize_inventory_top(entries: list[dict[str, Any]]) -> str:
    if not entries:
        return "인벤토리 정보 없음"
    return ", ".join(f"{row['name']} x{row['count']}" for row in entries)


def normalize_minecraft_item_name(value: Any) -> str:
    cleaned = clean_text(str(value or "")).strip().lower().replace("minecraft:", "").replace(" ", "_")
    return re.sub(r"[^a-z0-9_]", "", cleaned)


def humanize_minecraft_item_name(value: Any) -> str:
    item_name = normalize_minecraft_item_name(value)
    if not item_name:
        return "Unknown"
    return item_name.replace("_", " ")


def build_inventory_slot_templates() -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    armor_labels = ["helmet", "chestplate", "leggings", "boots"]
    for slot_index in range(5, 9):
        rows.append(
            {
                "slot": slot_index,
                "section": "armor",
                "sectionIndex": slot_index - 5,
                "label": armor_labels[slot_index - 5],
                "selected": False,
                "item": None,
                "count": 0,
                "displayName": "",
            }
        )
    for slot_index in range(9, 36):
        rows.append(
            {
                "slot": slot_index,
                "section": "main",
                "sectionIndex": slot_index - 9,
                "label": str(slot_index - 8),
                "selected": False,
                "item": None,
                "count": 0,
                "displayName": "",
            }
        )
    for slot_index in range(36, 45):
        rows.append(
            {
                "slot": slot_index,
                "section": "hotbar",
                "sectionIndex": slot_index - 36,
                "label": str(slot_index - 35),
                "selected": False,
                "item": None,
                "count": 0,
                "displayName": "",
            }
        )
    rows.append(
        {
            "slot": 45,
            "section": "offhand",
            "sectionIndex": 0,
            "label": "offhand",
            "selected": False,
            "item": None,
            "count": 0,
            "displayName": "",
        }
    )
    return rows


def normalize_inventory_slot_entries(raw_slots: Any, *, inventory: Any = None) -> list[dict[str, Any]]:
    templates = build_inventory_slot_templates()
    slot_map = {int(row["slot"]): dict(row) for row in templates}
    filled = False
    if isinstance(raw_slots, list):
        for row in raw_slots:
            if not isinstance(row, dict):
                continue
            try:
                slot_index = int(row.get("slot"))
            except (TypeError, ValueError):
                continue
            target = slot_map.get(slot_index)
            if target is None:
                continue
            item_name = normalize_minecraft_item_name(row.get("item") or row.get("name"))
            try:
                count = int(row.get("count") or 0)
            except (TypeError, ValueError):
                count = 0
            display_name = clean_text(str(row.get("displayName") or row.get("display_name") or "")) or humanize_minecraft_item_name(item_name)
            target.update(
                {
                    "item": item_name or None,
                    "count": count if count > 0 else 0,
                    "displayName": display_name if item_name else "",
                    "selected": bool(row.get("selected")),
                }
            )
            filled = True
    if not filled:
        fallback_entries = normalize_inventory_top_entries(inventory, limit=36)
        fallback_slots = [row for row in templates if row["section"] in {"main", "hotbar"}]
        for target, source in zip(fallback_slots, fallback_entries):
            item_name = normalize_minecraft_item_name(source.get("name"))
            target.update(
                {
                    "item": item_name or None,
                    "count": int(source.get("count") or 0),
                    "displayName": humanize_minecraft_item_name(item_name) if item_name else "",
                    "selected": False,
                }
            )
    return sorted(slot_map.values(), key=lambda row: int(row["slot"]))


def normalize_inventory_used_slots(value: Any, slots: list[dict[str, Any]]) -> int:
    try:
        normalized = int(value)
        if normalized >= 0:
            return normalized
    except (TypeError, ValueError):
        pass
    return sum(1 for row in slots if row.get("section") in {"main", "hotbar"} and row.get("item"))


def discover_control_page_minecraft_version_jar() -> Path | None:
    candidates: list[Path] = []
    appdata = os.getenv("APPDATA")
    if appdata:
        candidates.append(Path(appdata) / ".minecraft")
    candidates.append(Path.home() / ".minecraft")
    candidates.append(PROJECT_ROOT / ".minecraft")
    version_jars: list[Path] = []
    seen_roots: set[str] = set()
    for root in candidates:
        root = root.expanduser()
        key = str(root).lower()
        if key in seen_roots:
            continue
        seen_roots.add(key)
        versions_dir = root / "versions"
        if not versions_dir.exists():
            continue
        for entry in versions_dir.iterdir():
            if not entry.is_dir():
                continue
            jar_path = entry / f"{entry.name}.jar"
            if jar_path.is_file():
                version_jars.append(jar_path)
    if not version_jars:
        return None
    return max(version_jars, key=lambda path: path.stat().st_mtime)


def read_minecraft_asset_bytes(archive: zipfile.ZipFile, asset_path: str) -> bytes | None:
    try:
        return archive.read(asset_path)
    except KeyError:
        return None


def read_minecraft_asset_json(archive: zipfile.ZipFile, asset_path: str) -> dict[str, Any] | list[Any] | None:
    payload = read_minecraft_asset_bytes(archive, asset_path)
    if payload is None:
        return None
    try:
        return json.loads(payload.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError):
        return None


def minecraft_texture_ref_to_asset_path(texture_ref: str) -> str | None:
    normalized = clean_text(str(texture_ref or "")).strip()
    if not normalized:
        return None
    normalized = normalized.replace("minecraft:", "")
    if normalized.startswith("textures/"):
        normalized = normalized[len("textures/"):]
    normalized = normalized.lstrip("/")
    if not normalized:
        return None
    if not normalized.endswith(".png"):
        normalized = normalized + ".png"
    return f"assets/minecraft/textures/{normalized}"


def resolve_texture_alias(texture_ref: str, textures: dict[str, Any]) -> str | None:
    current = clean_text(str(texture_ref or "")).strip()
    seen: set[str] = set()
    while current.startswith("#"):
        key = current[1:]
        if key in seen:
            return None
        seen.add(key)
        next_value = textures.get(key)
        if not isinstance(next_value, str):
            return None
        current = next_value
    return current or None


def pick_model_texture_ref(textures: dict[str, Any]) -> str | None:
    for key in ("layer0", "all", "side", "top", "front", "end", "particle", "north"):
        value = textures.get(key)
        if isinstance(value, str) and value:
            return resolve_texture_alias(value, textures)
    return None


def normalize_model_reference(model_ref: str, *, default_kind: str = "item") -> str:
    normalized = clean_text(str(model_ref or "")).strip().replace("minecraft:", "").lstrip("/")
    if normalized.startswith("models/"):
        normalized = normalized[len("models/"):]
    if normalized.endswith(".json"):
        normalized = normalized[:-5]
    if "/" not in normalized:
        normalized = f"{default_kind}/{normalized}"
    return normalized


def resolve_model_texture_path(
    archive: zipfile.ZipFile,
    model_ref: str,
    *,
    inherited_textures: dict[str, Any] | None = None,
    seen_models: set[str] | None = None,
    default_kind: str = "item",
) -> str | None:
    normalized_ref = normalize_model_reference(model_ref, default_kind=default_kind)
    if not normalized_ref:
        return None
    seen = seen_models or set()
    if normalized_ref in seen:
        return None
    seen.add(normalized_ref)
    model_path = f"assets/minecraft/models/{normalized_ref}.json"
    model_json = read_minecraft_asset_json(archive, model_path)
    fallback_name = normalized_ref.split("/", 1)[1] if "/" in normalized_ref else normalized_ref
    if not isinstance(model_json, dict):
        for direct_ref in (f"item/{fallback_name}", f"block/{fallback_name}"):
            direct_path = minecraft_texture_ref_to_asset_path(direct_ref)
            if direct_path and read_minecraft_asset_bytes(archive, direct_path) is not None:
                return direct_path
        return None
    textures = dict(inherited_textures or {})
    own_textures = model_json.get("textures") if isinstance(model_json.get("textures"), dict) else {}
    for key, value in own_textures.items():
        if isinstance(value, str):
            textures[key] = value
    texture_ref = pick_model_texture_ref(textures)
    if texture_ref:
        asset_path = minecraft_texture_ref_to_asset_path(texture_ref)
        if asset_path and read_minecraft_asset_bytes(archive, asset_path) is not None:
            return asset_path
    parent_ref = model_json.get("parent")
    if isinstance(parent_ref, str) and parent_ref:
        parent_path = resolve_model_texture_path(
            archive,
            parent_ref,
            inherited_textures=textures,
            seen_models=seen,
        )
        if parent_path:
            return parent_path
    for direct_ref in (f"item/{fallback_name}", f"block/{fallback_name}"):
        direct_path = minecraft_texture_ref_to_asset_path(direct_ref)
        if direct_path and read_minecraft_asset_bytes(archive, direct_path) is not None:
            return direct_path
    return None


def collect_item_definition_model_refs(node: Any) -> list[str]:
    refs: list[str] = []
    if isinstance(node, dict):
        node_type = clean_text(str(node.get("type") or "")).strip()
        model_value = node.get("model")
        if node_type == "minecraft:model" and isinstance(model_value, str) and model_value:
            refs.append(model_value)
        for value in node.values():
            refs.extend(collect_item_definition_model_refs(value))
    elif isinstance(node, list):
        for value in node:
            refs.extend(collect_item_definition_model_refs(value))
    deduped: list[str] = []
    seen: set[str] = set()
    for ref in refs:
        normalized = clean_text(ref)
        if not normalized or normalized in seen:
            continue
        seen.add(normalized)
        deduped.append(normalized)
    return deduped


def resolve_minecraft_item_texture_path(archive: zipfile.ZipFile, item_name: str) -> str | None:
    normalized_name = normalize_minecraft_item_name(item_name)
    if not normalized_name:
        return None
    for direct_ref in (f"item/{normalized_name}", f"block/{normalized_name}"):
        direct_path = minecraft_texture_ref_to_asset_path(direct_ref)
        if direct_path and read_minecraft_asset_bytes(archive, direct_path) is not None:
            return direct_path
    item_definition = read_minecraft_asset_json(archive, f"assets/minecraft/items/{normalized_name}.json")
    for model_ref in collect_item_definition_model_refs(item_definition):
        resolved = resolve_model_texture_path(archive, model_ref)
        if resolved:
            return resolved
    legacy_item_model = resolve_model_texture_path(archive, f"item/{normalized_name}")
    if legacy_item_model:
        return legacy_item_model
    legacy_block_model = resolve_model_texture_path(archive, f"block/{normalized_name}", default_kind="block")
    if legacy_block_model:
        return legacy_block_model
    return None


def load_control_page_minecraft_item_icon(item_name: str) -> bytes | None:
    normalized_name = normalize_minecraft_item_name(item_name)
    if not normalized_name:
        return None
    if normalized_name in control_page_minecraft_item_icon_cache:
        return control_page_minecraft_item_icon_cache[normalized_name]
    jar_path = discover_control_page_minecraft_version_jar()
    if jar_path is None:
        control_page_minecraft_item_icon_cache[normalized_name] = None
        return None
    icon_bytes: bytes | None = None
    try:
        with zipfile.ZipFile(jar_path) as archive:
            texture_path = resolve_minecraft_item_texture_path(archive, normalized_name)
            if texture_path:
                icon_bytes = read_minecraft_asset_bytes(archive, texture_path)
    except (OSError, zipfile.BadZipFile):
        icon_bytes = None
    control_page_minecraft_item_icon_cache[normalized_name] = icon_bytes
    return icon_bytes


def extract_control_page_recent_activity(status: dict[str, Any] | None) -> list[dict[str, str]]:
    if not isinstance(status, dict):
        return []
    rows: list[dict[str, str]] = []
    completed = status.get("completed_tasks") if isinstance(status.get("completed_tasks"), list) else []
    failed = status.get("failed_tasks") if isinstance(status.get("failed_tasks"), list) else []
    for task in completed[-3:]:
        label = clean_text(str(task or ""))
        if label:
            rows.append({"kind": "completed", "label": label, "detail": "완료"})
    for item in failed[-3:]:
        if not isinstance(item, dict):
            continue
        label = clean_text(str(item.get("task") or ""))
        detail = clean_text(str(item.get("reason") or item.get("evidence") or "실패"))
        if label:
            rows.append({"kind": "failed", "label": label, "detail": detail or "실패"})
    return rows[-6:]


def extract_control_page_recent_activity_live(status: dict[str, Any] | None) -> list[dict[str, str]]:
    base_rows = extract_control_page_recent_activity(status)
    if not isinstance(status, dict):
        return base_rows
    rows: list[dict[str, str]] = []
    current_task = clean_text(str(status.get("current_task") or ""))
    current_stage = clean_text(str(status.get("current_task_stage") or status.get("display_stage") or status.get("last_phase") or ""))
    last_progress_message = clean_text(str(status.get("last_progress_message") or ""))
    progress_messages = status.get("progress_messages") if isinstance(status.get("progress_messages"), list) else []
    stability = status.get("stability_signals") if isinstance(status.get("stability_signals"), dict) else {}
    phase_age_seconds = stability.get("phase_age_seconds")
    task_bookkeeping = status.get("current_task_bookkeeping") if isinstance(status.get("current_task_bookkeeping"), dict) else {}
    rollout_iteration = task_bookkeeping.get("rollout_iteration")
    max_rollout_iterations = task_bookkeeping.get("max_rollout_iterations")
    program_name = clean_text(str(task_bookkeeping.get("program_name") or ""))
    verification_state = clean_text(str(task_bookkeeping.get("verification_state") or ""))
    last_search_metrics = status.get("last_search_metrics") if isinstance(status.get("last_search_metrics"), dict) else {}
    world_effect = status.get("last_world_effect_verification") if isinstance(status.get("last_world_effect_verification"), dict) else {}
    critic_result = status.get("last_critic_result") if isinstance(status.get("last_critic_result"), dict) else {}
    if current_task:
        detail_parts: list[str] = []
        if current_stage:
            detail_parts.append(current_stage)
        if isinstance(phase_age_seconds, (int, float)):
            detail_parts.append(f"{max(0.0, float(phase_age_seconds)):.0f}s")
        rows.append({
            "kind": "live",
            "label": current_task,
            "detail": " ? ".join(part for part in detail_parts if part) or "running",
        })
    if isinstance(rollout_iteration, int) and isinstance(max_rollout_iterations, int) and max_rollout_iterations > 0:
        rollout_label = f"rollout {rollout_iteration + 1}/{max_rollout_iterations}"
        rollout_detail = verification_state or program_name or "task session"
        rows.append({"kind": "live", "label": rollout_label, "detail": rollout_detail})
    if last_progress_message:
        rows.append({"kind": "live", "label": last_progress_message, "detail": "progress"})
    else:
        for message in progress_messages[-2:]:
            clean = clean_text(str(message or ""))
            if clean:
                rows.append({"kind": "live", "label": clean, "detail": "progress"})
    if last_search_metrics:
        helper = clean_text(str(last_search_metrics.get("helper") or ""))
        goal_type = clean_text(str(last_search_metrics.get("goal_type") or ""))
        completion_reason = clean_text(str(last_search_metrics.get("completion_reason") or last_search_metrics.get("failure_reason") or ""))
        search_label = " ".join(part for part in [helper, goal_type] if part) or "search helper"
        search_detail = completion_reason or "active"
        rows.append({"kind": "live", "label": search_label, "detail": search_detail})
    if world_effect:
        summary = clean_text(str(world_effect.get("summary") or ""))
        reason_code = clean_text(str(world_effect.get("reason_code") or ""))
        outcome = clean_text(str(world_effect.get("outcome") or ""))
        if summary:
            rows.append({
                "kind": "failed" if outcome == "fail" else "live",
                "label": summary,
                "detail": reason_code or outcome or "world effect",
            })
    elif critic_result:
        critique = clean_text(str(critic_result.get("critique") or ""))
        reason_code = clean_text(str(critic_result.get("reason_code") or ""))
        if critique:
            rows.append({"kind": "failed", "label": critique, "detail": reason_code or "critic"})
    rows.extend(base_rows)
    deduped: list[dict[str, str]] = []
    seen: set[tuple[str, str, str]] = set()
    for row in rows:
        key = (str(row.get("kind") or ""), str(row.get("label") or ""), str(row.get("detail") or ""))
        if key in seen:
            continue
        seen.add(key)
        deduped.append(row)
    return deduped[:6]


def extract_control_page_recent_activity_live_v2(status: dict[str, Any] | None) -> list[dict[str, str]]:
    base_rows = extract_control_page_recent_activity(status)[-2:]
    if not isinstance(status, dict):
        return base_rows
    rows: list[dict[str, str]] = []
    current_task = clean_text(str(status.get("current_task") or ""))
    current_stage = clean_text(str(status.get("current_task_stage") or status.get("display_stage") or status.get("last_phase") or ""))
    last_progress_message = clean_text(str(status.get("last_progress_message") or ""))
    progress_messages = status.get("progress_messages") if isinstance(status.get("progress_messages"), list) else []
    stability = status.get("stability_signals") if isinstance(status.get("stability_signals"), dict) else {}
    phase_age_seconds = stability.get("phase_age_seconds")
    task_bookkeeping = status.get("current_task_bookkeeping") if isinstance(status.get("current_task_bookkeeping"), dict) else {}
    rollout_iteration = task_bookkeeping.get("rollout_iteration")
    max_rollout_iterations = task_bookkeeping.get("max_rollout_iterations")
    program_name = clean_text(str(task_bookkeeping.get("program_name") or ""))
    verification_state = clean_text(str(task_bookkeeping.get("verification_state") or ""))
    last_search_metrics = status.get("last_search_metrics") if isinstance(status.get("last_search_metrics"), dict) else {}
    world_effect = status.get("last_world_effect_verification") if isinstance(status.get("last_world_effect_verification"), dict) else {}
    critic_result = status.get("last_critic_result") if isinstance(status.get("last_critic_result"), dict) else {}
    if current_task:
        detail_parts: list[str] = []
        if current_stage:
            detail_parts.append(current_stage)
        if isinstance(phase_age_seconds, (int, float)):
            detail_parts.append(f"{max(0.0, float(phase_age_seconds)):.0f}s")
        rows.append({
            "kind": "live",
            "label": current_task,
            "detail": " / ".join(part for part in detail_parts if part) or "running",
        })
    if isinstance(rollout_iteration, int) and isinstance(max_rollout_iterations, int) and max_rollout_iterations > 0:
        rollout_label = f"rollout {rollout_iteration + 1}/{max_rollout_iterations}"
        rollout_detail = verification_state or program_name or "task session"
        rows.append({"kind": "live", "label": rollout_label, "detail": rollout_detail})
    if last_progress_message:
        rows.append({"kind": "live", "label": last_progress_message, "detail": "progress"})
    else:
        for message in progress_messages[-2:]:
            clean = clean_text(str(message or ""))
            if clean:
                rows.append({"kind": "live", "label": clean, "detail": "progress"})
    if last_search_metrics:
        helper = clean_text(str(last_search_metrics.get("helper") or ""))
        goal_type = clean_text(str(last_search_metrics.get("goal_type") or ""))
        completion_reason = clean_text(str(last_search_metrics.get("completion_reason") or last_search_metrics.get("failure_reason") or ""))
        search_label = " ".join(part for part in [helper, goal_type] if part) or "search helper"
        search_detail = completion_reason or "active"
        rows.append({"kind": "live", "label": search_label, "detail": search_detail})
    if world_effect:
        summary = clean_text(str(world_effect.get("summary") or ""))
        reason_code = clean_text(str(world_effect.get("reason_code") or ""))
        outcome = clean_text(str(world_effect.get("outcome") or ""))
        if summary:
            rows.append({
                "kind": "failed" if outcome == "fail" else "live",
                "label": summary,
                "detail": reason_code or outcome or "world effect",
            })
    elif critic_result:
        critique = clean_text(str(critic_result.get("critique") or ""))
        reason_code = clean_text(str(critic_result.get("reason_code") or ""))
        if critique:
            rows.append({"kind": "failed", "label": critique, "detail": reason_code or "critic"})
    rows.extend(base_rows)
    deduped: list[dict[str, str]] = []
    seen: set[tuple[str, str, str]] = set()
    for row in rows:
        key = (str(row.get("kind") or ""), str(row.get("label") or ""), str(row.get("detail") or ""))
        if key in seen:
            continue
        seen.add(key)
        deduped.append(row)
    return deduped[:6]


async def get_control_page_minecraft_snapshot(guild_id: int | None) -> dict[str, Any]:
    client = get_minecraft_client()
    raw_status: dict[str, Any] = {}
    last_error = ""
    try:
        maybe_status = await client.status()
        if isinstance(maybe_status, dict):
            raw_status = maybe_status
    except Exception as exc:
        last_error = repr(exc)
    observation = raw_status.get("observation") if isinstance(raw_status.get("observation"), dict) else {}
    merged = _merge_voyager_status_into_state(raw_status, observation) or {}
    if not merged:
        merged = await observe_live_minecraft_state(guild_id) or {}
    if last_error and not merged.get("last_error"):
        merged["last_error"] = last_error
    merged["inventory_top"] = normalize_inventory_top_entries(merged.get("inventory") or observation.get("inventory"))
    merged["inventory_summary"] = summarize_inventory_top(merged["inventory_top"])
    merged["inventory_slots"] = normalize_inventory_slot_entries(
        observation.get("inventory_slots") or observation.get("inventorySlots"),
        inventory=merged.get("inventory") or observation.get("inventory"),
    )
    merged["inventory_used"] = normalize_inventory_used_slots(
        observation.get("inventory_used") or observation.get("inventoryUsed"),
        merged["inventory_slots"],
    )
    merged["recent_activity"] = extract_control_page_recent_activity_live_v2(raw_status)
    merged["completed_count"] = len(raw_status.get("completed_tasks") or [])
    merged["failed_count"] = len(raw_status.get("failed_tasks") or [])
    merged["current_task"] = clean_text(str(raw_status.get("current_task") or merged.get("objective_task") or ""))
    merged["current_task_stage"] = clean_text(str(raw_status.get("current_task_stage") or merged.get("objective_task_stage") or ""))
    merged["goal"] = clean_text(str(raw_status.get("goal") or merged.get("objective_goal") or ""))
    merged["stage"] = clean_text(str(raw_status.get("stage") or merged.get("objective_stage") or ""))
    merged["progress"] = clean_text(str(raw_status.get("last_progress_message") or merged.get("objective_progress") or ""))
    merged["position_text"] = format_position_short(merged.get("position") or observation.get("position"))
    return attach_minecraft_runtime_snapshot(
        merged,
        source="control_page_live",
        now=time.time(),
        observed_at=time.time(),
        stale_after_sec=CONTROL_PAGE_MINECRAFT_CACHE_REFRESH_SEC,
        expired_after_sec=CONTROL_PAGE_MINECRAFT_CACHE_MAX_STALE_SEC,
        last_error=last_error or None,
    )


async def safe_get_control_page_minecraft_snapshot(
    guild_id: int | None,
    *,
    timeout_seconds: float = 0.75,
) -> dict[str, Any]:
    try:
        return await asyncio.wait_for(get_control_page_minecraft_snapshot(guild_id), timeout=timeout_seconds)
    except Exception as exc:
        return {
            "last_error": clean_text(str(exc)) or repr(exc),
            "inventory_top": [],
            "inventory_summary": "inventory unavailable",
            "recent_activity": [],
        }


async def _probe_control_page_runtime_services_once() -> dict[str, Any]:
    bot_ready = True
    voyager_ready = False
    voyager_error = ""
    try:
        voyager_ready = await get_minecraft_client().is_service_alive(timeout_sec=0.45)
    except Exception as exc:
        voyager_error = clean_text(str(exc)) or type(exc).__name__
    codex_required = clean_text(str(VOYAGER_ACTION_BACKEND or "")).lower() == "codex-gateway"
    codex_ready: bool | None = None
    codex_error = ""
    codex_backend = clean_text(str(VOYAGER_ACTION_BACKEND or "")) or "unknown"
    if codex_required:
        codex_ready = False
        codex_health_url = f"http://127.0.0.1:{VOYAGER_CODEX_GATEWAY_PORT}/health"
        timeout = aiohttp.ClientTimeout(total=0.45)
        try:
            async with aiohttp.ClientSession(timeout=timeout) as session:
                async with session.get(codex_health_url) as resp:
                    payload = await resp.json(content_type=None)
                    if isinstance(payload, dict):
                        codex_backend = clean_text(str(payload.get("backend") or codex_backend)) or codex_backend
                        codex_ready = resp.status == 200 and bool(payload.get("ok", True))
                        if not codex_ready:
                            codex_error = clean_text(str(payload.get("error") or payload.get("codex_login_message") or "")) or codex_error
                    else:
                        codex_ready = resp.status == 200
        except Exception as exc:
            codex_error = clean_text(str(exc)) or type(exc).__name__
    services = {
        "botReady": bot_ready,
        "voyagerReady": voyager_ready,
        "codexRequired": codex_required,
        "codexReady": codex_ready,
        "codexBackend": codex_backend,
        "summary": build_control_page_runtime_summary(
            bot_ready=bot_ready,
            voyager_ready=voyager_ready,
            codex_required=codex_required,
            codex_ready=codex_ready,
        ),
    }
    if voyager_error:
        services["voyagerError"] = voyager_error
    if codex_error:
        services["codexError"] = codex_error
    return services


async def get_control_page_runtime_services(*, force: bool = False) -> dict[str, Any]:
    global control_page_runtime_services_cache
    global control_page_runtime_services_cached_at
    global control_page_runtime_services_lock

    age_seconds = (time.time() - control_page_runtime_services_cached_at) if control_page_runtime_services_cached_at else None
    is_fresh = (
        control_page_runtime_services_cache
        and age_seconds is not None
        and age_seconds <= CONTROL_PAGE_RUNTIME_CACHE_REFRESH_SEC
    )
    if is_fresh and not force:
        return dict(control_page_runtime_services_cache)
    if control_page_runtime_services_lock is None:
        control_page_runtime_services_lock = asyncio.Lock()
    async with control_page_runtime_services_lock:
        age_seconds = (time.time() - control_page_runtime_services_cached_at) if control_page_runtime_services_cached_at else None
        is_fresh = (
            control_page_runtime_services_cache
            and age_seconds is not None
            and age_seconds <= CONTROL_PAGE_RUNTIME_CACHE_REFRESH_SEC
        )
        if is_fresh and not force:
            return dict(control_page_runtime_services_cache)
        services = await _probe_control_page_runtime_services_once()
        control_page_runtime_services_cache = dict(services)
        control_page_runtime_services_cached_at = time.time()
        return dict(control_page_runtime_services_cache)


def get_control_page_minecraft_snapshot_cache_copy() -> dict[str, Any]:
    snapshot = dict(control_page_minecraft_snapshot_cache) if isinstance(control_page_minecraft_snapshot_cache, dict) else {}
    age_seconds = (time.time() - control_page_minecraft_snapshot_cached_at) if control_page_minecraft_snapshot_cached_at else None
    if age_seconds is not None:
        snapshot["snapshot_age_sec"] = round(max(0.0, age_seconds), 3)
    else:
        snapshot["snapshot_age_sec"] = None
    snapshot["snapshot_stale"] = bool(control_page_minecraft_snapshot_stale)
    snapshot["snapshot_expired"] = bool(
        snapshot["snapshot_stale"]
        and age_seconds is not None
        and age_seconds > CONTROL_PAGE_MINECRAFT_CACHE_MAX_STALE_SEC
    )
    if control_page_minecraft_snapshot_last_error and not snapshot.get("last_error"):
        snapshot["last_error"] = control_page_minecraft_snapshot_last_error
    snapshot = attach_minecraft_runtime_snapshot(
        snapshot,
        source="control_page_cache",
        now=time.time(),
        observed_at=control_page_minecraft_snapshot_cached_at or None,
        stale_after_sec=CONTROL_PAGE_MINECRAFT_CACHE_REFRESH_SEC,
        expired_after_sec=CONTROL_PAGE_MINECRAFT_CACHE_MAX_STALE_SEC,
        last_error=snapshot.get("last_error") or None,
    )
    if snapshot.get("snapshot_expired"):
        return {
            "last_error": snapshot.get("last_error") or "minecraft_snapshot_expired",
            "inventory_top": [],
            "inventory_summary": "inventory unavailable",
            "recent_activity": [],
            "snapshot_stale": True,
            "snapshot_expired": True,
            "snapshot_age_sec": snapshot.get("snapshot_age_sec"),
            "snapshot_freshness": snapshot.get("snapshot_freshness"),
            "runtime_snapshot": snapshot.get("runtime_snapshot"),
        }
    return snapshot


async def _refresh_control_page_minecraft_snapshot_once(guild_id: int | None) -> dict[str, Any]:
    global control_page_minecraft_snapshot_cache
    global control_page_minecraft_snapshot_cached_at
    global control_page_minecraft_snapshot_stale
    global control_page_minecraft_snapshot_last_error

    try:
        snapshot = await asyncio.wait_for(
            get_control_page_minecraft_snapshot(guild_id),
            timeout=max(0.5, CONTROL_PAGE_MINECRAFT_SNAPSHOT_TIMEOUT_SEC),
        )
    except Exception as exc:
        error_text = clean_text(str(exc)) or repr(exc)
        control_page_minecraft_snapshot_last_error = error_text
        control_page_minecraft_snapshot_stale = True
        if not control_page_minecraft_snapshot_cache:
            control_page_minecraft_snapshot_cache = {
                "last_error": error_text,
                "inventory_top": [],
                "inventory_summary": "inventory unavailable",
                "recent_activity": [],
            }
        else:
            control_page_minecraft_snapshot_cache["last_error"] = error_text
        return get_control_page_minecraft_snapshot_cache_copy()

    control_page_minecraft_snapshot_cache = dict(snapshot)
    control_page_minecraft_snapshot_cached_at = time.time()
    control_page_minecraft_snapshot_stale = False
    control_page_minecraft_snapshot_last_error = clean_text(str(snapshot.get("last_error") or ""))
    return get_control_page_minecraft_snapshot_cache_copy()


async def ensure_control_page_minecraft_snapshot(
    guild_id: int | None,
    *,
    force: bool = False,
    wait: bool = False,
) -> dict[str, Any]:
    global control_page_minecraft_snapshot_lock
    global control_page_minecraft_snapshot_refresh_task

    if guild_id is None:
        return get_control_page_minecraft_snapshot_cache_copy()
    if control_page_minecraft_snapshot_lock is None:
        control_page_minecraft_snapshot_lock = asyncio.Lock()

    async with control_page_minecraft_snapshot_lock:
        age_seconds = (time.time() - control_page_minecraft_snapshot_cached_at) if control_page_minecraft_snapshot_cached_at else None
        is_fresh = (
            control_page_minecraft_snapshot_cache
            and not control_page_minecraft_snapshot_stale
            and age_seconds is not None
            and age_seconds <= CONTROL_PAGE_MINECRAFT_CACHE_REFRESH_SEC
        )
        if not force and is_fresh:
            return get_control_page_minecraft_snapshot_cache_copy()
        if control_page_minecraft_snapshot_refresh_task is None or control_page_minecraft_snapshot_refresh_task.done():
            control_page_minecraft_snapshot_refresh_task = asyncio.create_task(
                _refresh_control_page_minecraft_snapshot_once(guild_id)
            )
        task = control_page_minecraft_snapshot_refresh_task

    if wait:
        try:
            await task
        except Exception:
            pass
    return get_control_page_minecraft_snapshot_cache_copy()


async def control_page_minecraft_snapshot_poller() -> None:
    while True:
        try:
            guild = select_control_page_guild()
            if guild is not None:
                await ensure_control_page_minecraft_snapshot(guild.id, force=True, wait=True)
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            print(f"[CONTROL PAGE] minecraft_snapshot_poll_failed err={exc!r}")
        await asyncio.sleep(max(0.5, CONTROL_PAGE_MINECRAFT_CACHE_REFRESH_SEC))


async def ensure_control_page_background_tasks_started() -> None:
    global control_page_minecraft_snapshot_poll_task
    if control_page_minecraft_snapshot_poll_task is not None and not control_page_minecraft_snapshot_poll_task.done():
        return
    guild = select_control_page_guild()
    if guild is not None:
        await ensure_control_page_minecraft_snapshot(guild.id, force=True, wait=True)
    control_page_minecraft_snapshot_poll_task = asyncio.create_task(control_page_minecraft_snapshot_poller())


def stop_control_page_background_tasks() -> None:
    global control_page_minecraft_snapshot_poll_task
    global control_page_minecraft_snapshot_refresh_task
    for task in (control_page_minecraft_snapshot_poll_task, control_page_minecraft_snapshot_refresh_task):
        if task is not None and not task.done():
            task.cancel()
    control_page_minecraft_snapshot_poll_task = None
    control_page_minecraft_snapshot_refresh_task = None


def build_control_page_status_text(guild: discord.Guild, minecraft: dict[str, Any]) -> str:
    vc = guild.voice_client
    voice_channel_name = getattr(getattr(vc, "channel", None), "name", None) or "없음"
    listening = bool(vc and hasattr(vc, "is_listening") and vc.is_listening())
    speaking = is_tracked_tts_playback_active(tts_playback_tracker, guild.id)
    tts_target = current_tts_target_name(guild) if speaking else "없음"
    runtime_snapshot = minecraft.get("runtime_snapshot") if isinstance(minecraft.get("runtime_snapshot"), dict) else {}
    snapshot_freshness = clean_text(str(runtime_snapshot.get("freshness") or minecraft.get("snapshot_freshness") or "unknown"))
    snapshot_age = runtime_snapshot.get("age_sec", minecraft.get("snapshot_age_sec"))
    return "\n".join(
        [
            "Evelyn 상태",
            f"- guild: {guild.name}",
            f"- voice_channel: {voice_channel_name}",
            f"- listening: {'on' if listening else 'off'}",
            f"- tts_speaking: {'on' if speaking else 'off'}",
            f"- talking_to: {tts_target}",
            f"- main_model: {MODEL_NAME}",
            f"- router_model: {ROUTER_MODEL_NAME}",
            f"- summary_model: {SUMMARY_MODEL_NAME}",
            f"- stt_model: {STT_MODEL_NAME}",
            f"- local_mic: {local_mic_status_line()}",
            f"- voyager_running: {'on' if minecraft.get('minecraft_autonomy') else 'off'}",
            f"- voyager_connected: {'on' if minecraft.get('voyager_connected') else 'off'}",
            f"- minecraft_snapshot: {snapshot_freshness}",
            f"- minecraft_snapshot_age_sec: {snapshot_age if snapshot_age is not None else 'unknown'}",
            f"- current_task: {minecraft.get('current_task') or '없음'}",
            f"- goal: {minecraft.get('goal') or '없음'}",
        ]
    )


async def build_control_page_status_reply(guild: discord.Guild) -> str:
    minecraft = await safe_get_control_page_minecraft_snapshot(guild.id)
    return build_control_page_status_text(guild, minecraft)


def build_control_page_voice_status_reply(guild: discord.Guild | None) -> str:
    vc = guild.voice_client if guild is not None else None
    voice = build_voice_pipeline_snapshot(guild)
    channel_name = getattr(getattr(vc, "channel", None), "name", None) or "none"
    saved = voice.get("lastVoiceChannel") if isinstance(voice.get("lastVoiceChannel"), dict) else {}
    saved_channel = clean_text(str((saved or {}).get("channel_name") or "")) or "none"
    return "\n".join(
        [
            "Voice pipeline",
            f"- channel: {channel_name}",
            f"- saved_channel: {saved_channel}",
            f"- queue: {voice['queueDepth']}/{voice['queueMax']}",
            f"- live_recent: {'yes' if voice['liveRecent'] else 'no'}",
            f"- stt_busy: {'yes' if voice['sttBusy'] else 'no'}",
            f"- stt_cooldown_sec: {voice['sttCooldownRemainingSec']}",
            f"- stt_timeout_count: {voice['sttTimeoutCount']}",
            f"- queue_drops: full={voice['queueFullDropCount']} stale={voice['queueStaleDropCount']}",
            f"- failures: llm={voice['llmFailedCount']} tts_req={voice['ttsRequestFailedCount']} playback={voice['ttsPlaybackFailedCount']} delivery={voice['voiceDeliveryFailedCount']}",
            f"- p95: stt={voice['sttMsP95']}ms tts_first={voice['ttsFirstAudioMsP95']}ms main_first={voice['mainFirstTokenMsP95']}ms",
        ]
    )


async def build_control_page_inventory_reply(guild: discord.Guild) -> str:
    minecraft = await safe_get_control_page_minecraft_snapshot(guild.id)
    entries = minecraft.get("inventory_top") if isinstance(minecraft.get("inventory_top"), list) else []
    if not entries:
        return "현재 인벤토리 정보를 아직 받지 못했어."
    lines = ["Minecraft 인벤토리 요약"]
    for row in entries:
        lines.append(f"- {row['name']}: {row['count']}")
    return "\n".join(lines)


async def build_control_page_minecraft_reply(guild: discord.Guild) -> str:
    minecraft = await safe_get_control_page_minecraft_snapshot(guild.id)
    return "\n".join(
        [
            "Minecraft status",
            f"- running: {'on' if minecraft.get('minecraft_autonomy') else 'off'}",
            f"- connected: {'on' if minecraft.get('voyager_connected') else 'off'}",
            f"- goal: {minecraft.get('goal') or 'none'}",
            f"- stage: {minecraft.get('stage') or 'none'}",
            f"- task: {minecraft.get('current_task') or 'none'}",
            f"- task_stage: {minecraft.get('current_task_stage') or 'none'}",
            f"- progress: {minecraft.get('progress') or 'none'}",
            f"- tech_tree: {clean_text(str(minecraft.get('voyager_tech_tree_highest') or 'unknown'))}",
            f"- unique_items: {minecraft.get('voyager_unique_item_count') if minecraft.get('voyager_unique_item_count') is not None else 'unknown'}",
            f"- skill_library: {minecraft.get('voyager_skill_library_size') if minecraft.get('voyager_skill_library_size') is not None else 'unknown'}",
            f"- travel_distance: {minecraft.get('voyager_travel_distance_blocks') if minecraft.get('voyager_travel_distance_blocks') is not None else 'unknown'}",
            f"- health: {minecraft.get('health') if minecraft.get('health') is not None else 'unknown'}",
            f"- hunger: {minecraft.get('hunger') if minecraft.get('hunger') is not None else 'unknown'}",
            f"- position: {minecraft.get('position_text') or 'unknown'}",
        ]
    )


def build_control_page_autonomy_reply(guild: discord.Guild) -> str:
    engine = autonomy_engines.get(guild.id)
    if engine is None:
        return "자율 행동 엔진이 아직 만들어지지 않았어."
    state = engine.state
    goal = state.current_goal.summary if state.current_goal else "없음"
    plan = state.current_plan.summary if state.current_plan else "없음"
    allowed = ", ".join(state.allowed_actions[:6]) or "없음"
    if len(state.allowed_actions) > 6:
        allowed += ", ..."
    router = get_routed_autonomy_executor(guild.id)
    minecraft_enabled = bool(router and router.is_domain_enabled("minecraft"))
    return "\n".join(
        [
            "자율 행동 상태",
            f"- status: {state.status}",
            f"- safety: {state.safety_mode}",
            f"- goal: {goal}",
            f"- plan: {plan}",
            f"- failures: {state.failure_count}",
            f"- last_error: {state.last_error or '없음'}",
            f"- minecraft_autonomy: {'on' if minecraft_enabled else 'off'}",
            f"- allowed: {allowed}",
        ]
    )


def build_control_page_help_reply() -> str:
    lines = ["Page commands:"]
    for item in CONTROL_PAGE_COMMANDS:
        lines.append(f"- {item['command']}: {item['summary']}")
    return "\n".join(lines)


def run_control_page_window_tool(action: str, *, key: str | None = None) -> dict[str, Any]:
    script_path = EVELYN_CORE_RUNTIME / "launchers" / "control_console_window.ps1"
    if os.name != "nt":
        return {"ok": False, "error": "windows_only"}
    if not script_path.exists():
        return {"ok": False, "error": "script_missing", "path": str(script_path)}
    command = [
        "powershell.exe",
        "-NoLogo",
        "-NoProfile",
        "-ExecutionPolicy",
        "Bypass",
        "-File",
        str(script_path),
        "-Action",
        action,
    ]
    if key:
        command.extend(["-Key", key])
    try:
        completed = subprocess.run(
            command,
            cwd=str(PROJECT_ROOT),
            capture_output=True,
            text=True,
            timeout=10,
            check=False,
        )
    except Exception as exc:
        return {"ok": False, "error": "tool_launch_failed", "detail": repr(exc)}
    raw = (completed.stdout or completed.stderr or "").strip()
    if not raw:
        return {"ok": False, "error": "empty_response", "returncode": completed.returncode}
    try:
        parsed = json.loads(raw)
    except json.JSONDecodeError:
        return {"ok": False, "error": "invalid_json", "returncode": completed.returncode, "raw": raw[:400]}
    if not isinstance(parsed, dict):
        return {"ok": False, "error": "invalid_response", "returncode": completed.returncode}
    return parsed


def build_control_page_windows_reply() -> str:
    result = run_control_page_window_tool("list")
    if not result.get("ok"):
        return "Background console window lookup failed."
    rows = result.get("windows") if isinstance(result.get("windows"), list) else []
    if not rows:
        return "No background console windows are registered."
    lines = ["Background console windows"]
    for row in rows:
        if not isinstance(row, dict):
            continue
        status = "running" if row.get("running") else "stopped"
        visibility = "window" if row.get("windowFound") else "no-window"
        lines.append(f"- {row.get('key')}: {status}, {visibility}, port {row.get('port')}")
    lines.append(f"- show: /show {CONTROL_PAGE_WINDOW_SPECS[0].key}")
    return "\n".join(lines)


def build_control_page_show_window_reply(requested_key: str) -> str:
    resolved = resolve_control_page_window_key(requested_key)
    if not resolved:
        return f"Unknown window. Try one of: {control_page_window_choices_text()}"
    result = run_control_page_window_tool("show", key=resolved)
    if result.get("ok"):
        return f"Brought {resolved} to the front."
    if result.get("error") == "window_not_found":
        running = "running" if result.get("running") else "stopped"
        return f"{resolved} has no restorable console window right now ({running}). Run start.bat first if needed."
    return f"Failed to show {resolved}: {clean_text(str(result.get('error') or 'unknown_error')) or 'unknown_error'}"


def build_control_page_ui_reply(text: str) -> str:
    words = clean_text(text).split()
    usage = (
        "UI commands\n"
        "- /ui list\n"
        "- /ui show runtime|diagnostics|avatar|chat\n"
        "- /ui hide runtime|diagnostics|avatar|chat\n"
        "- /ui toggle runtime|diagnostics|avatar|chat\n"
        "- /ui focus runtime|diagnostics|avatar|chat\n"
        "- /ui reset"
    )
    if len(words) <= 1 or words[1].lower() in {"help", "?"}:
        return usage
    action = words[1].lower()
    if action in {"list", "panels"}:
        panels = ", ".join(CONTROL_PAGE_UI_PANELS)
        return f"Control page panels: {panels}"
    if action in {"reset", "restore", "default"}:
        enqueue_control_page_ui_command("reset")
        return "Control page panel layout reset queued."
    if action not in {"show", "open", "hide", "close", "toggle", "focus"}:
        return usage
    panel_id = normalize_control_page_ui_panel(words[2] if len(words) > 2 else None)
    if not panel_id:
        panels = ", ".join(CONTROL_PAGE_UI_PANELS)
        return f"Unknown panel. Use one of: {panels}"
    normalized_action = {
        "open": "show",
        "close": "hide",
    }.get(action, action)
    enqueue_control_page_ui_command(normalized_action, panel_id=panel_id)
    return f"Control page UI command queued: {normalized_action} {panel_id}."


async def execute_control_page_command(guild: discord.Guild, text: str) -> str:
    normalized = clean_text(text).lower()
    if normalized in {"/", "/help"}:
        return build_control_page_help_reply()
    if normalized == "/ui" or normalized.startswith("/ui "):
        return build_control_page_ui_reply(text)
    if normalized == "/status":
        return await build_control_page_status_reply(guild)
    if normalized in {"/voice status", "/voice"}:
        return build_control_page_voice_status_reply(guild)
    if normalized in {"/voice reconnect", "/voice rejoin"}:
        ok, detail = await restore_last_voice_channel(guild, force=True)
        if ok:
            return f"Voice reconnected to {detail}."
        return f"Voice reconnect failed: {detail}"
    if normalized in {"/shutdown", "/quit", "/exit"}:
        if schedule_evelyn_stack_shutdown():
            return "Full Evelyn stack shutdown started. Supervisors, bot, LLM, TTS, Voyager, and WSL will stop."
        asyncio.create_task(shutdown_bot_process())
        return "Full-stack shutdown helper failed, so only the bot process is stopping."
    if normalized == "/inventory":
        return await build_control_page_inventory_reply(guild)
    if normalized in {"/voyager stats", "/minecraft status", "/mc-status"}:
        return await build_control_page_minecraft_reply(guild)
    if normalized in {"/minecraft connect", "/mc-connect"}:
        observed = await enable_minecraft_mode(guild.id)
        return (
            "Voyager Minecraft 모드를 시작했어.\n"
            f"- goal: {clean_text(str(observed.get('objective_goal') or observed.get('goal') or '없음')) or '없음'}\n"
            f"- stage: {clean_text(str(observed.get('objective_stage') or observed.get('stage') or '없음')) or '없음'}\n"
            f"- position: {format_position_short(observed.get('position'))}"
        )
    if normalized in {"/minecraft disconnect", "/mc-disconnect"}:
        await disable_minecraft_mode(guild.id)
        return "Voyager Minecraft 모드를 중지했어."
    if normalized.startswith("/minecraft goal "):
        goal_text = clean_text(text[len("/minecraft goal "):])
        if not goal_text:
            return "목표를 같이 적어줘. 예: /minecraft goal progress_to_diamond"
        status = await get_minecraft_client().set_goal(goal_text)
        return (
            "Minecraft 목표를 바꿨어.\n"
            f"- goal: {goal_text}\n"
            f"- stage: {clean_text(str(status.get('stage') or 'unknown')) or 'unknown'}"
        )
    if normalized == "/autonomy status":
        return build_control_page_autonomy_reply(guild)
    if normalized == "/windows":
        return build_control_page_windows_reply()
    if normalized.startswith("/show "):
        return build_control_page_show_window_reply(text[len("/show "):])
    return "지원하지 않는 명령어야. /help 로 현재 페이지 명령어를 확인해줘."


async def answer_control_page_text(guild: discord.Guild, user_text: str) -> str:
    session_key = control_page_session_key(guild.id)
    state_lock = session_locks.setdefault(session_key, asyncio.Lock())
    topic_id = build_topic_id(user_text, session_topic_ids.get(session_key, ""))
    async with state_lock:
        start_new_turn(session_key)
        update_session_state(
            session_key,
            speaker="user",
            awaiting_user_reply=False,
            topic_id=topic_id,
            user_text=user_text,
        )
        get_conversation_history(session_key=session_key, guild_id=guild.id)
    answer = await ask_llm_streaming(
        user_text,
        guild_id=guild.id,
        session_key=session_key,
        source="text",
        debug_text=user_text,
    )
    plain_answer = strip_omnivoice_tags(answer) or answer
    awaiting_reply = bool(session_state_snapshot(session_key).get("awaiting_user_reply"))
    async with state_lock:
        append_history(session_key, user_text, plain_answer, guild_id=guild.id)
        mark_session_active(
            session_key,
            ttl_sec=ACTIVE_CONVERSATION_TEXT_QUESTION_SEC if awaiting_reply else ACTIVE_CONVERSATION_TEXT_SEC,
            speaker="assistant",
            awaiting_user_reply=awaiting_reply,
            topic_id=topic_id,
            answer_text=plain_answer,
            user_text=user_text,
        )
    return format_display_text(answer, session_key=session_key).strip() or fallback_answer_for(user_text)


async def handle_control_page_input(guild: discord.Guild, text: str) -> str:
    if clean_text(text).startswith("/"):
        return await execute_control_page_command(guild, text)
    return await answer_control_page_text(guild, text)


async def build_control_page_state(guild: discord.Guild | None) -> dict[str, Any]:
    runtime_services = await get_control_page_runtime_services()
    if guild is None:
        commands = build_control_page_commands(minecraft_session_active=False)
        return {
            "ok": False,
            "generatedAt": time.time(),
            "localUrl": control_page_local_url(),
            "ui": build_control_page_ui_state(
                guild_available=False,
                listening=False,
                speaking=False,
                minecraft_running=False,
                minecraft_session_active=False,
                minecraft_snapshot_stale=False,
                minecraft_last_error="",
            ),
            "commands": commands,
            "allCommands": build_control_page_all_commands(),
            "chat": {"messages": []},
            "voice": {
                "channelName": "없음",
                "listening": False,
                "speaking": False,
                "ttsTargetName": "없음",
            },
            "runtime": {
                "mainModel": MODEL_NAME,
                "routerModel": ROUTER_MODEL_NAME,
                "summaryModel": SUMMARY_MODEL_NAME,
                "sttModel": STT_MODEL_NAME,
                "inflightLlmRequests": inflight_llm_requests,
                "ttsBacklog": tracked_tts_playback_count(tts_playback_tracker),
                "localMic": serialize_local_mic_runtime_state(),
                "voicePipeline": build_voice_pipeline_snapshot(guild),
                "services": runtime_services,
                "controlPagePanels": build_control_page_panel_state(),
            },
            "minecraft": {},
            "statusText": "봇이 아직 길드에 연결되지 않았어.",
        }
    vc = guild.voice_client
    await ensure_control_page_minecraft_snapshot(guild.id, wait=not bool(control_page_minecraft_snapshot_cache))
    minecraft = get_control_page_minecraft_snapshot_cache_copy()
    speaking = is_tracked_tts_playback_active(tts_playback_tracker, guild.id)
    listening = bool(vc and hasattr(vc, "is_listening") and vc.is_listening())
    tts_target_name = current_tts_target_name(guild) if speaking else "없음"
    local_mic_target = serialize_local_mic_target(
        resolve_local_mic_target(guilds=bot.guilds, preferred_user_ids=LOCAL_MIC_DISCORD_USER_IDS)
    )
    minecraft_session_active = is_control_page_minecraft_session_active(minecraft)
    commands = build_control_page_commands(minecraft_session_active=minecraft_session_active)
    activity = minecraft.get("recent_activity") if isinstance(minecraft.get("recent_activity"), list) else []
    minecraft_status_fields = minecraft_runtime_status_fields(minecraft)
    idle_summary = (
        "Voyager는 켜져 있지만 아직 Minecraft 플레이 상태는 아니야. 접속이 잡히면 위젯이 자동으로 나타나."
        if minecraft.get("minecraft_autonomy")
        else "지금은 Minecraft 플레이 전이야. /minecraft connect 를 실행하면 플레이 상태 위젯이 자동으로 나타나."
    )
    ui_state = build_control_page_ui_state(
        guild_available=True,
        listening=listening,
        speaking=speaking,
        minecraft_running=bool(minecraft.get("minecraft_autonomy")),
        minecraft_session_active=minecraft_session_active,
        minecraft_snapshot_stale=bool(minecraft.get("snapshot_stale")),
        minecraft_last_error=minecraft.get("last_error"),
    )
    return {
        "ok": True,
        "generatedAt": time.time(),
        "localUrl": control_page_local_url(),
        "ui": ui_state,
        "guild": {"id": guild.id, "name": guild.name},
        "commands": commands,
        "allCommands": build_control_page_all_commands(),
        "chat": {"messages": get_control_page_chat_log(guild.id)},
        "voice": {
            "channelName": getattr(getattr(vc, "channel", None), "name", None) or "없음",
            "listening": listening,
            "speaking": speaking,
            "ttsTargetName": tts_target_name,
        },
        "runtime": {
            "mainModel": MODEL_NAME,
            "routerModel": ROUTER_MODEL_NAME,
            "summaryModel": SUMMARY_MODEL_NAME,
            "sttModel": STT_MODEL_NAME,
            "inflightLlmRequests": inflight_llm_requests,
            "ttsBacklog": tracked_tts_playback_count(tts_playback_tracker),
            "voiceDebugAudio": VOICE_DEBUG_SAVE_AUDIO,
            "localMicTarget": local_mic_target,
            "localMic": serialize_local_mic_runtime_state(),
            "voicePipeline": build_voice_pipeline_snapshot(guild),
            "services": runtime_services,
            "controlPagePanels": build_control_page_panel_state(),
        },
        "minecraft": {
            "running": bool(minecraft.get("minecraft_autonomy")),
            "connected": bool(minecraft.get("voyager_connected")),
            "sessionActive": minecraft_session_active,
            "goal": minecraft.get("goal") or "none",
            "stage": minecraft.get("stage") or "none",
            "task": minecraft.get("current_task") or "none",
            "taskStage": minecraft.get("current_task_stage") or "none",
            "progress": minecraft.get("progress") or "none",
            "position": minecraft.get("position_text") or "unknown",
            "health": minecraft.get("health"),
            "hunger": minecraft.get("hunger"),
            "hostiles": minecraft.get("hostiles_nearby"),
            "uniqueItemCount": minecraft.get("voyager_unique_item_count"),
            "travelDistanceBlocks": minecraft.get("voyager_travel_distance_blocks"),
            "techTreeHighest": minecraft.get("voyager_tech_tree_highest"),
            "skillLibrarySize": minecraft.get("voyager_skill_library_size"),
            "inventorySummary": minecraft.get("inventory_summary") or "No inventory data",
            "inventoryTop": minecraft.get("inventory_top") or [],
            "inventorySlots": minecraft.get("inventory_slots") or [],
            "inventoryUsedSlots": minecraft.get("inventory_used"),
            "completedCount": minecraft.get("completed_count") or 0,
            "failedCount": minecraft.get("failed_count") or 0,
            "recentActivity": activity,
            "lastError": minecraft.get("last_error") or "",
            **minecraft_status_fields,
            "idleSummary": "" if minecraft_session_active else idle_summary,
        },
        "statusText": build_control_page_status_text(guild, minecraft),
    }


def control_page_json_response(data: Any, *, status: int = 200) -> web.Response:
    return web.Response(
        status=status,
        text=json.dumps(data, ensure_ascii=False),
        content_type="application/json",
    )


@web.middleware
async def control_page_cors_middleware(request: web.Request, handler: Callable[[web.Request], Awaitable[web.StreamResponse]]) -> web.StreamResponse:
    if request.method == "OPTIONS" and request.path.startswith("/api/control-page/"):
        response: web.StreamResponse = web.Response(status=204)
    else:
        response = await handler(request)
    if request.path.startswith("/api/control-page/"):
        response.headers["Access-Control-Allow-Origin"] = "*"
        response.headers["Access-Control-Allow-Methods"] = "GET,POST,OPTIONS"
        response.headers["Access-Control-Allow-Headers"] = "Content-Type"
    return response


async def control_page_index_handler(_: web.Request) -> web.StreamResponse:
    index_path = CONTROL_PAGE_DOCS_DIR / "index.html"
    if not index_path.exists():
        raise web.HTTPNotFound(text="control page index not found")
    response = web.FileResponse(index_path)
    response.headers["Cache-Control"] = "no-store, no-cache, must-revalidate, max-age=0"
    response.headers["Pragma"] = "no-cache"
    response.headers["Expires"] = "0"
    return response


async def control_page_asset_handler(request: web.Request) -> web.StreamResponse:
    requested = Path(request.match_info.get("asset_path", ""))
    asset_path = (CONTROL_PAGE_ASSETS_DIR / requested).resolve()
    assets_root = CONTROL_PAGE_ASSETS_DIR.resolve()
    try:
        asset_path.relative_to(assets_root)
    except ValueError as exc:
        raise web.HTTPForbidden(text="invalid asset path") from exc
    if not asset_path.exists() or not asset_path.is_file():
        raise web.HTTPNotFound(text="asset not found")
    response = web.FileResponse(asset_path)
    response.headers["Cache-Control"] = "no-store, no-cache, must-revalidate, max-age=0"
    response.headers["Pragma"] = "no-cache"
    response.headers["Expires"] = "0"
    return response


async def control_page_minecraft_item_icon_handler(request: web.Request) -> web.StreamResponse:
    item_name = normalize_minecraft_item_name(request.match_info.get("item_name", ""))
    if not item_name:
        raise web.HTTPNotFound(text="item icon not found")
    icon_bytes = load_control_page_minecraft_item_icon(item_name)
    if not icon_bytes:
        raise web.HTTPNotFound(text="item icon not found")
    response = web.Response(body=icon_bytes, content_type="image/png")
    response.headers["Cache-Control"] = "no-store, no-cache, must-revalidate, max-age=0"
    response.headers["Pragma"] = "no-cache"
    response.headers["Expires"] = "0"
    return response


def parse_control_page_guild_id(value: str | None) -> int | None:
    if value is None:
        return None
    try:
        return int(str(value).strip())
    except (TypeError, ValueError):
        return None


async def control_page_state_handler(request: web.Request) -> web.StreamResponse:
    guild = select_control_page_guild(parse_control_page_guild_id(request.query.get("guildId")))
    return control_page_json_response(await build_control_page_state(guild))


async def control_page_chat_handler(request: web.Request) -> web.StreamResponse:
    try:
        payload = await request.json()
    except Exception:
        return control_page_json_response({"ok": False, "error": "invalid_json"}, status=400)
    text = clean_text(str((payload or {}).get("text") or ""))
    if not text:
        return control_page_json_response({"ok": False, "error": "empty_text"}, status=400)
    guild = select_control_page_guild(parse_control_page_guild_id((payload or {}).get("guildId")))
    if guild is None:
        return control_page_json_response({"ok": False, "error": "guild_not_available"}, status=503)
    append_control_page_chat_log(guild.id, "user", "정훈", text)
    try:
        reply_text = await handle_control_page_input(guild, text)
    except Exception as exc:
        reply_text = f"처리 중 오류가 났어: {exc}"
    append_control_page_chat_log(guild.id, "assistant", "Evelyn", reply_text)
    normalized = clean_text(text).lower()
    needs_fresh_snapshot = (
        normalized.startswith("/minecraft")
        or normalized in {"/inventory", "/voyager stats", "/minecraft status", "/mc-status"}
    )
    await ensure_control_page_minecraft_snapshot(guild.id, force=needs_fresh_snapshot, wait=needs_fresh_snapshot)
    if normalized.startswith("/minecraft"):
        await get_control_page_runtime_services(force=True)
    state = await build_control_page_state(guild)
    return control_page_json_response({"ok": True, "reply": reply_text, "state": state})


async def start_control_page_server() -> None:
    global control_page_runner, control_page_site, control_page_start_lock
    if not CONTROL_PAGE_ENABLED:
        return
    if control_page_runner is not None:
        return
    if control_page_start_lock is None:
        control_page_start_lock = asyncio.Lock()
    async with control_page_start_lock:
        if control_page_runner is not None:
            return
        if not CONTROL_PAGE_DOCS_DIR.exists():
            print(f"[CONTROL PAGE] docs_missing path={CONTROL_PAGE_DOCS_DIR}")
            return
        app = web.Application(middlewares=[control_page_cors_middleware])
        app.router.add_get("/", control_page_index_handler)
        app.router.add_get("/assets/{asset_path:.*}", control_page_asset_handler)
        app.router.add_get(CONTROL_PAGE_MINECRAFT_ICON_ROUTE + "/{item_name}", control_page_minecraft_item_icon_handler)
        app.router.add_get("/api/control-page/state", control_page_state_handler)
        app.router.add_post("/api/control-page/chat", control_page_chat_handler)
        app.router.add_options("/api/control-page/state", control_page_state_handler)
        app.router.add_options("/api/control-page/chat", control_page_chat_handler)
        runner = web.AppRunner(app, access_log=None)
        try:
            await runner.setup()
            site = web.TCPSite(runner, host=CONTROL_PAGE_HOST, port=CONTROL_PAGE_PORT)
            await site.start()
        except Exception:
            await runner.cleanup()
            raise
        control_page_runner = runner
        control_page_site = site
        print(f"[CONTROL PAGE] live url={control_page_local_url()}")


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
            "route_policy": route_decision_policy_dict(route_decision),
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
    messages, cognitive_state, _route, context_policy = await prepare_llm_messages(
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
    route_decision = replace(
        route_decision,
        needs_main_llm=bool(route_decision.needs_main_llm and context_policy.needs_main_llm),
        needs_memory=bool(context_policy.needs_memory),
        needs_runtime_state=bool(context_policy.needs_runtime_state),
        needs_minecraft_state=bool(context_policy.needs_minecraft_state),
        needs_vision=bool(context_policy.needs_vision),
        needs_skill_graph=bool(context_policy.needs_skill_graph),
        needs_long_context=bool(context_policy.needs_long_context),
        needs_search=bool(route_decision.needs_search or context_policy.needs_search),
        needs_tts=bool(context_policy.needs_tts),
        response_mode=clean_text(context_policy.response_mode) or route_decision.response_mode,
        priority=clean_text(context_policy.priority) or route_decision.priority,
    )
    if metrics is not None:
        metrics.setdefault("meta", {})["route_policy"] = route_decision_policy_dict(route_decision)
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
    delivery_on_sentence = on_sentence if route_decision.needs_tts else None
    if metrics is not None:
        metrics.setdefault("meta", {})["needs_tts"] = bool(route_decision.needs_tts and on_sentence is not None)

    datetime_answer = answer_current_datetime_query(user_text)
    if datetime_answer:
        if on_first_chunk is not None:
            on_first_chunk()
            on_first_chunk = None
        answer_payload = await emit_action_result_delivery(
            build_action_result(
                action="answer",
                answer_text=datetime_answer,
                metadata={"route": "datetime_fast_path"},
            ),
            on_sentence=delivery_on_sentence,
            session_key=session_key,
            user_text=user_text,
            awaiting_user_reply=False,
        )
        if metrics is not None:
            elapsed_ms = (time.monotonic() - float(metrics.get("started_at", time.monotonic()))) * 1000.0
            metrics.setdefault("marks", {})["policy_short_circuit"] = elapsed_ms
            metrics.setdefault("marks", {})["llm_done"] = elapsed_ms
            metrics.setdefault("marks", {})["t_main_done"] = elapsed_ms
            metrics.setdefault("meta", {})["deterministic_fast_path"] = "datetime"
        return answer_payload.display_text, on_first_chunk

    if route_decision.action == "search_then_answer":
        if on_first_chunk is not None:
            on_first_chunk()
            on_first_chunk = None
        preface_text = route_decision.user_visible_preface or "잠깐 찾아보고 바로 말해줄게."
        await emit_action_result_delivery(
            build_action_result(
                action=route_decision.action,
                answer_text=preface_text,
                metadata={"route": route_decision.route, "phase": "preface"},
            ),
            on_sentence=delivery_on_sentence,
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
            on_sentence=delivery_on_sentence,
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
            on_sentence=delivery_on_sentence,
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


async def execute_main_llm_streaming_turn(
    *,
    request: VoiceTurnRequest,
    route_context: VoiceTurnRouteContext,
    on_first_chunk: Callable[[], None] | None,
) -> str:
    global inflight_llm_requests
    user_text = request.user_text
    guild_id = request.guild_id
    session_key = request.session_key
    room_key = request.room_key
    person_key = request.person_key
    session_memory_key = request.session_memory_key
    source = request.source
    debug_text = request.debug_text
    metrics = request.metrics
    turn_scope = request.turn_scope
    messages = route_context.messages
    cognitive_state = route_context.cognitive_state
    route_decision = route_context.route_decision
    on_sentence = request.on_sentence if route_decision.needs_tts else None
    if metrics is not None:
        metrics.setdefault("meta", {})["needs_tts"] = bool(route_decision.needs_tts and request.on_sentence is not None)

    guided_user_text = route_decision.prompt_text or user_text
    lightweight_persona_turn = is_casual_call_or_status_question(guided_user_text)
    needs_live_minecraft_state = (
        not lightweight_persona_turn
        and (route_decision.needs_minecraft_state or route_decision.needs_skill_graph)
    )
    needs_runtime_status_context = not lightweight_persona_turn and route_decision.needs_runtime_state
    live_minecraft_state = await observe_live_minecraft_state(guild_id) if needs_live_minecraft_state else None
    runtime_status_context = await build_runtime_status_context() if needs_runtime_status_context else ""
    final_user_text = f"{guided_user_text}\n\n{build_main_response_guidance(cognitive_state, source=source, user_text=guided_user_text, session_key=session_key, guild_id=guild_id, minecraft_state=live_minecraft_state, runtime_status_context=runtime_status_context)}"
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
        content_format=MAIN_LLM_CHAT_CONTENT_FORMAT,
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
                    print("[LLM STREAM] json answer empty, retry non-stream")
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
            f"[LLM STREAM] stream 응답 본문이 비어 있음, non-stream 재시도 | raw_len={len(''.join(raw_parts))} reasoning_len={len(''.join(reasoning_parts))} emitted_any={emitted_any}"
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
        request = VoiceTurnRequest(
            user_text=user_text,
            guild_id=guild_id,
            session_key=session_key,
            room_key=room_key,
            person_key=person_key,
            session_memory_key=session_memory_key,
            source=source,
            debug_text=debug_text,
            metrics=metrics,
            turn_scope=turn_scope,
            on_sentence=on_sentence,
            on_first_chunk=on_first_chunk,
        )
        orchestrator = VoiceTurnOrchestrator(
            VoiceTurnOrchestratorDeps(
                prepare_route_context=prepare_route_context,
                maybe_handle_short_circuit_route=maybe_handle_short_circuit_route,
                maybe_execute_registered_route=maybe_execute_registered_route,
                run_main_llm_turn=execute_main_llm_streaming_turn,
                emit_delivery_plan_chunks=emit_delivery_plan_chunks,
                build_answer_payload_from_text=build_answer_payload_from_text,
                build_delivery_plan=build_delivery_plan,
                split_tts_sentences=split_tts_sentences,
            )
        )
        result = await orchestrator.execute(request)
        return result.answer_text

    except Exception as exc:
        record_voice_pipeline_failure("llm_failed", exc, metrics, stage="ask_llm_streaming")
        raise
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
    tts_sink = TTSQueueSink(sentence_queue, log=print)
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
        log_stage=log_voice_stage,
        prefetch_chunks=TTS_PREFETCH_CHUNKS,
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
        metrics.setdefault("meta", {})["needs_tts"] = True
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
    except asyncio.CancelledError:
        metrics.setdefault("meta", {})["playback_cancelled"] = True
        metrics.setdefault("meta", {})["error_layer"] = "voice_turn"
        metrics.setdefault("meta", {})["error"] = "cancelled"
        log_voice_bottleneck_summary(
            metrics,
            label="voice_turn",
            extra="cancelled=true mode=llm_streaming",
            event_name="voice_turn_summary",
        )
        raise
    except Exception as exc:
        metrics.setdefault("meta", {})["error_layer"] = "voice_turn"
        metrics.setdefault("meta", {})["error"] = repr(exc)
        log_voice_bottleneck_summary(
            metrics,
            label="voice_turn",
            extra="error=true mode=llm_streaming",
            event_name="voice_turn_summary",
        )
        raise
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
        metrics.setdefault("meta", {})["needs_tts"] = bool(include_voice)

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
    debug_meta_input = debug_meta if isinstance(debug_meta, dict) else {}
    source = str(debug_meta_input.get("source") or "discord_voice")
    if should_drop_discord_audio_for_local_mic(getattr(member, "id", None), source=source):
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
    item = build_voice_ingress_item(
        member=member,
        pcm_bytes=pcm_bytes,
        debug_meta=debug_meta_input,
        session_key=session_key,
        room_session_key=room_session_key,
        room_key=room_key,
        person_key=person_key,
        session_memory_key=session_memory_key,
        turn_id=turn_id,
        segment_id=segment_id,
        ingress_during_reply=bool(room_state.get("reply_in_progress")),
        owner_user_id_on_ingress=room_state.get("owner_user_id"),
        queue_depth_at_enqueue=voice_ingress_queue.qsize(),
        enqueued_at=time.monotonic(),
    )
    enqueue_result = enqueue_voice_ingress_item(
        voice_ingress_queue,
        item,
        drop_oldest_on_full=VOICE_INGRESS_DROP_OLDEST_ON_FULL,
    )
    if not enqueue_result.accepted:
        increment_voice_pipeline_counter("queue_full_drop_count")
        print(
            f"[VOICE QUEUE DROP] reason=queue_full speaker={member.display_name} "
            f"qsize={voice_ingress_queue.qsize()} qmax={VOICE_INGRESS_QUEUE_MAX}"
        )
        return
    dropped = enqueue_result.dropped_oldest_item
    if dropped is not None:
        dropped_member = dropped.get("member") if isinstance(dropped, dict) else None
        print(
            f"[VOICE QUEUE DROP] reason=queue_full_drop_oldest "
            f"speaker={getattr(dropped_member, 'display_name', None)} qmax={VOICE_INGRESS_QUEUE_MAX}"
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
    voice_pipeline_state["last_voice_segment_at"] = time.time()
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
        metrics.setdefault("meta", {})["ingress_source"] = str(debug_meta.get("source") or "discord_voice")
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
    voice_segment = build_voice_segment(
        guild_id=guild_id,
        room_session_key=room_session_key,
        session_key=session_key,
        speaker_user_id=member.id,
        speaker_name=speaker_name,
        audio16k=audio16k,
        sampling_rate=stt_sampling_rate,
        duration_sec=duration_sec,
        segment_id=segment_id,
        owner_user_id=owner_user_id,
    )
    metrics.setdefault("meta", {})["voice_segment_contract"] = voice_segment
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
            wake_result = await run_blocking_stt_task(
                lambda: detect_wake_word_sync(audio_for_wake, sampling_rate=wake_sampling_rate),
                stage="wake",
                timeout_sec=max(5.0, WAKE_STT_TIMEOUT_SEC),
                metrics=metrics,
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

    tts_suppression = tts_input_suppression_reason(
        tracker=tts_playback_tracker,
        guild_id=guild_id,
        post_tts_ignore_sec=POST_TTS_IGNORE_SEC,
    )
    if tts_suppression is not None:
        register_drop_reason(metrics, tts_suppression, session_key=session_key, room_session_key=room_session_key, owner_user_id=owner_user_id, wake_probe_text=wake_probe, wake_detected=wake_detected)
        stage_label = "봇 재생 중 입력 무시" if tts_suppression == "bot_is_speaking" else "TTS 직후 입력 무시"
        log_voice_stage(metrics, stage_label, extra=f"speaker={member.display_name} wake_detected={wake_detected}")
        log_voice_bottleneck_summary(metrics, label="voice_drop", extra=f"drop={tts_suppression}", event_name="voice_drop_summary")
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
        tts_suppression = tts_input_suppression_reason(
            tracker=tts_playback_tracker,
            guild_id=guild_id,
            post_tts_ignore_sec=POST_TTS_IGNORE_SEC,
        )
        if tts_suppression is not None:
            register_drop_reason(metrics, tts_suppression, session_key=session_key, room_session_key=room_session_key, owner_user_id=owner_user_id, wake_probe_text=wake_probe, wake_detected=wake_detected)
            stage_label = "디바운스 후 봇 재생 중 입력 무시" if tts_suppression == "bot_is_speaking" else "디바운스 후 TTS 직후 입력 무시"
            log_voice_stage(metrics, stage_label, extra=f"speaker={member.display_name} wake_detected={wake_detected}")
            log_voice_bottleneck_summary(metrics, label="voice_drop", extra=f"drop={tts_suppression}", event_name="voice_drop_summary")
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
            partial_text, committed_partial_text = await run_blocking_stt_task(
                lambda: get_partial_transcript(session_key, audio16k, sampling_rate=stt_sampling_rate),
                stage="partial",
                timeout_sec=max(3.0, min(10.0, FULL_STT_TIMEOUT_SEC * 0.5)),
                metrics=metrics,
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
        primary_text = await run_blocking_stt_task(
            lambda: transcribe_audio16k_sync(audio16k, VOICE_STT_MAX_NEW_TOKENS, sampling_rate=stt_sampling_rate, stage="full"),
            stage="full",
            timeout_sec=max(8.0, FULL_STT_TIMEOUT_SEC),
            metrics=metrics,
        )
    except Exception as e:
        print(f"[STT] {e}")
        log_voice_stage(metrics, "본문 STT 실패", extra=repr(e))
        return

    text = primary_text
    print(f"[STT RESULT][full-primary] text={primary_text!r}")
    clean_primary_text = clean_text(primary_text)
    rescore_skip_reason = ""
    if duration_sec < STT_FULL_RESCORING_MIN_AUDIO_SEC:
        rescore_skip_reason = "audio_too_short"
    elif len(clean_primary_text) < STT_FULL_RESCORING_MIN_TEXT_LEN:
        rescore_skip_reason = "text_too_short"

    if STT_FULL_RESCORING_ENABLED and not rescore_skip_reason:
        log_voice_stage(metrics, "본문 STT 2차 rescoring 시작")
        try:
            rescore_text = await run_blocking_stt_task(
                lambda: transcribe_audio16k_sync(
                    audio16k,
                    VOICE_STT_MAX_NEW_TOKENS + max(0, STT_FULL_RESCORE_EXTRA_TOKENS),
                    sampling_rate=stt_sampling_rate,
                    stage="full-rescore",
                ),
                stage="full-rescore",
                timeout_sec=max(4.0, STT_FULL_RESCORING_TIMEOUT_SEC),
                metrics=metrics,
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
        stt_meta = {
            "enabled": bool(STT_FULL_RESCORING_ENABLED),
            "selected": "primary",
            "primary_text": primary_text,
            "skipped_reason": rescore_skip_reason or "disabled",
        }
        if rescore_skip_reason:
            log_voice_stage(metrics, "STT rescore skip", extra=rescore_skip_reason)

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

    voice_reply_context = VoiceTranscriptReplyContext(
        guild_id=guild_id,
        transcript=transcript_result,
        voice_segment=voice_segment,
        session_key=session_key,
        room_session_key=room_session_key,
        owner_user_id=owner_user_id,
        source_turn_id=turn_id,
        segment_id=segment_id,
        voiced_ms=voiced_ms,
        raw_seconds=raw_seconds,
        rms=body_rms,
        wake_detected=wake_detected,
        reply_in_progress=bool(room_state_snapshot(room_session_key).get("reply_in_progress")),
        metrics=metrics,
        session_topic_seed=session_topic_ids.get(session_key, ""),
        now_monotonic=time.monotonic(),
        ingress_source=str(metrics.setdefault("meta", {}).get("ingress_source") or "discord_voice"),
        queue_wait_ms=float(metrics.setdefault("meta", {}).get("voice_queue_wait_ms") or 0.0),
        active_conversation_awaiting_reply_sec=ACTIVE_CONVERSATION_AWAITING_REPLY_SEC,
        active_conversation_voice_sec=ACTIVE_CONVERSATION_VOICE_SEC,
        member=member,
        canned_wake_reply=CANNED_WAKE_REPLY_TEXT,
        room_key=room_key,
        person_key=person_key,
        session_memory_key=session_memory_key,
    )
    voice_reply_deps = VoiceTranscriptReplyDeps(
        should_reply_to_voice=should_reply_to_voice,
        register_drop_reason=register_drop_reason,
        log_voice_stage=log_voice_stage,
        log_voice_bottleneck_summary=log_voice_bottleneck_summary,
        reset_session_bad_audio=reset_session_bad_audio,
        build_voice_reply_request=build_voice_reply_request,
        build_topic_id=build_topic_id,
        session_last_stt_text=session_last_stt_text,
        room_last_voice_reply_at=room_last_voice_reply_at,
        update_room_speaker_activity=update_room_speaker_activity,
        pick_active_speaker=pick_active_speaker,
        start_new_turn=start_new_turn,
        update_session_state=update_session_state,
        set_room_owner=set_room_owner,
        session_partial_stt_text=session_partial_stt_text,
        session_committed_stt_text=session_committed_stt_text,
        partial_stt_cache=partial_stt_cache,
        make_turn_scope=TurnScope,
        replace_room_turn_scope=replace_room_turn_scope,
        attach_current_task=_attach_current_task,
        set_room_reply_in_progress=set_room_reply_in_progress,
        session_locks=session_locks,
        visible_text=visible_text,
        print_fn=print,
        get_voice_client=lambda: guild.voice_client,
        speak_answer=speak_answer,
        ask_llm_and_speak_streaming=ask_llm_and_speak_streaming,
        record_voice_pipeline_failure=record_voice_pipeline_failure,
        finalize_voice_reply_side_effects=finalize_voice_reply_side_effects,
        strip_omnivoice_tags=strip_omnivoice_tags,
        get_room_turn_scope=get_room_turn_scope,
        detach_task=_detach_task,
        clear_room_turn_scope=clear_room_turn_scope,
    )
    await process_voice_reply_from_transcript_context(
        context=voice_reply_context,
        deps=voice_reply_deps,
    )
    return


# =========================================================
# 이벤트
# =========================================================
@bot.event
async def on_ready():
    print(f"로그인 완료: {bot.user}")
    ensure_voice_worker_started()
    try:
        await ensure_startup_components_ready()
        await ensure_local_mic_service_started()
    except Exception as e:
        print(f"[STARTUP] init_fail err={e!r}")
        raise
    try:
        await start_control_page_server()
    except Exception as e:
        print(f"[CONTROL PAGE] start_fail err={e!r}")
    try:
        await ensure_control_page_background_tasks_started()
    except Exception as e:
        print(f"[CONTROL PAGE] bg_tasks_fail err={e!r}")
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
        elif VOICE_REJOIN_ON_READY:
            ok, detail = await restore_last_voice_channel(guild)
            if ok:
                print(f"[VOICE READY REJOIN] guild={guild.id} channel={detail}")
            elif detail not in {"no_saved_voice_channel", "manual_disconnect"}:
                print(f"[VOICE READY REJOIN SKIP] guild={guild.id} reason={detail}")
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


def build_discord_attachment_context(message: discord.Message, *, limit: int = 4) -> str:
    attachments = list(getattr(message, "attachments", []) or [])[: max(0, limit)]
    rows: list[str] = []
    for attachment in attachments:
        content_type = clean_text(str(getattr(attachment, "content_type", "") or ""))
        filename = clean_text(str(getattr(attachment, "filename", "") or "attachment"))
        url = clean_text(str(getattr(attachment, "url", "") or ""))
        width = getattr(attachment, "width", None)
        height = getattr(attachment, "height", None)
        is_image = content_type.startswith("image/") or filename.lower().endswith((".png", ".jpg", ".jpeg", ".webp", ".gif"))
        label = "image" if is_image else "attachment"
        size_bits = []
        if width and height:
            size_bits.append(f"{width}x{height}")
        if content_type:
            size_bits.append(content_type)
        rows.append(f"- {label}: filename={filename}; meta={', '.join(size_bits) or 'unknown'}; url={url}")
    return "\n".join(rows)


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
        user_text = "이름만 부름. 친구처럼 짧게 반말로, 원래 하던 일을 잠깐 말하며 자연스럽게 반응해."
    attachment_context = build_discord_attachment_context(message)
    if attachment_context:
        user_text = f"{user_text}\n\n[Attached Visual Inputs]\n{attachment_context}"

    state_lock = session_locks.setdefault(session_key, asyncio.Lock())
    reply_slot_key = make_text_reply_slot_key(message.guild.id, message.channel.id, thread_id=thread_id)
    reply_lock = reply_slot_locks.setdefault(reply_slot_key, asyncio.Lock())

    if reply_lock.locked():
        await message.channel.send("\u23f3 지금 다른 응답을 처리 중이야. 잠깐만.")
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
    text_turn_summary_logged = False
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
            record_context_pipeline_benchmark(
                metrics=text_metrics,
                user_text=user_text,
                answer=plain_answer,
                source="text",
                guild_id=message.guild.id,
                session_key=session_key,
            )
            memory_writer_decision = schedule_memory_update(
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
            text_metrics.setdefault("meta", {})["memory_writer_decision"] = memory_writer_decision
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

            awaiting_reply = bool(session_state_snapshot(session_key).get("awaiting_user_reply"))
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
        text_turn_summary_logged = True

    except Exception as e:
        print("전체 오류:", repr(e))
        await message.channel.send(f"❌ 오류 발생: {e}")
    finally:
        if text_metrics and not text_turn_summary_logged:
            text_metrics.setdefault("meta", {})["error_layer"] = "text_turn"
            text_metrics.setdefault("meta", {}).setdefault("error", "text_turn_aborted_before_summary")
            log_voice_bottleneck_summary(
                text_metrics,
                label="text_turn",
                extra="error=true",
                event_name="text_turn_summary",
            )
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

    mark_voice_manual_disconnect(ctx.guild, reason="leave_command")
    await vc.disconnect()
    await ctx.send("👋 나갔어.")


async def restart_bot_process() -> None:
    await asyncio.sleep(1.0)
    stop_control_page_background_tasks()
    stop_local_mic_service()
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


def schedule_evelyn_stack_shutdown(delay_ms: int = 3000) -> bool:
    stop_script = PROJECT_ROOT / "evelyn_core" / "runtime" / "launchers" / "stop_evelyn_stack.ps1"
    if not stop_script.exists():
        logging.error("Full-stack shutdown helper is missing: %s", stop_script)
        return False
    try:
        subprocess.Popen(
            [
                "powershell.exe",
                "-NoLogo",
                "-NoProfile",
                "-ExecutionPolicy",
                "Bypass",
                "-File",
                str(stop_script),
                "-DelayMs",
                str(max(0, int(delay_ms))),
            ],
            cwd=str(PROJECT_ROOT),
            close_fds=True,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
        return True
    except Exception:
        logging.exception("Failed to schedule full-stack shutdown")
        return False


async def shutdown_bot_process() -> None:
    await asyncio.sleep(0.5)
    stop_control_page_background_tasks()
    stop_local_mic_service()
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
    if schedule_evelyn_stack_shutdown():
        await ctx.send("Full Evelyn stack shutdown started. Supervisors, bot, LLM, TTS, Voyager, and WSL will stop.")
        return
    await ctx.send("Full-stack shutdown helper failed, so only the bot process is stopping.")
    asyncio.create_task(shutdown_bot_process())
    return


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


@bot.command(name="이블린페이지", aliases=["page", "homepage", "website", "landing"])
async def evelyn_page_command(ctx):
    page_url = resolve_evelyn_page_url()
    if not page_url:
        await ctx.send("아직 공개 이블린 페이지 URL을 못 찾았어. EVELYN_PAGE_URL을 설정하거나 GitHub Pages 배포를 먼저 붙여줘.")
        return
    await ctx.send(f"이블린 페이지: {page_url}")


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
        f"- {prefix}이블린페이지",
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
