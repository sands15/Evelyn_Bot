from __future__ import annotations

import os
from pathlib import Path

from .config import *
from .paths import get_repo_root


PROJECT_ROOT = get_repo_root()

TURN_TRACE_JSON_LOG = os.getenv("TURN_TRACE_JSON_LOG", "true").lower() == "true"
VOICE_CONSOLE_ONLY_STT_AND_REPLY = os.getenv("VOICE_CONSOLE_ONLY_STT_AND_REPLY", "true").lower() == "true"
VOICE_BOTTLENECK_LOGS = os.getenv("VOICE_BOTTLENECK_LOGS", "true").lower() == "true"
VOICE_TRACE_ALL_EVENTS = os.getenv("VOICE_TRACE_ALL_EVENTS", "true").lower() == "true"
TURN_TRACE_LOG_DIR = Path(os.getenv("TURN_TRACE_LOG_DIR", str(PROJECT_ROOT / "logs" / "turn_trace")))
CONVERSATION_ARCHIVE_ENABLED = os.getenv(
    "EVELYN_CONVERSATION_ARCHIVE_ENABLED",
    "false",
).lower() in {"1", "true", "yes", "on"}
CONVERSATION_ARCHIVE_BOT_API_URL = os.getenv(
    "EVELYN_CONVERSATION_ARCHIVE_BOT_API_URL",
    "http://127.0.0.1:8798",
).rstrip("/")
CONVERSATION_ARCHIVE_INGEST_KEY_FILE = os.getenv(
    "EVELYN_CONVERSATION_ARCHIVE_INGEST_KEY_FILE",
    "",
).strip()
CONVERSATION_ARCHIVE_USER_VIEW_KEY_FILE = os.getenv(
    "EVELYN_CONVERSATION_ARCHIVE_USER_VIEW_KEY_FILE",
    "",
).strip()
_CONVERSATION_ARCHIVE_COMMAND_GUILD_ID_RAW = os.getenv(
    "EVELYN_CONVERSATION_ARCHIVE_COMMAND_GUILD_ID",
    "",
).strip()
if (
    _CONVERSATION_ARCHIVE_COMMAND_GUILD_ID_RAW
    and (
        not _CONVERSATION_ARCHIVE_COMMAND_GUILD_ID_RAW.isdecimal()
        or not 17 <= len(_CONVERSATION_ARCHIVE_COMMAND_GUILD_ID_RAW) <= 20
    )
):
    raise RuntimeError("conversation_archive_command_guild_id_invalid")
CONVERSATION_ARCHIVE_COMMAND_GUILD_ID = int(
    _CONVERSATION_ARCHIVE_COMMAND_GUILD_ID_RAW or "0"
)
ARCHIVE_COMMAND_OWNERSHIP_FILE = os.getenv(
    "EVELYN_CONVERSATION_ARCHIVE_COMMAND_OWNERSHIP_LEDGER", ""
).strip()
ARCHIVE_COMMAND_RUN_ID = os.getenv(
    "EVELYN_CONVERSATION_ARCHIVE_COMMAND_RUN_ID", ""
).strip()
if bool(ARCHIVE_COMMAND_OWNERSHIP_FILE) != bool(ARCHIVE_COMMAND_RUN_ID) or (
    ARCHIVE_COMMAND_OWNERSHIP_FILE
    and (
        not (
            Path(ARCHIVE_COMMAND_OWNERSHIP_FILE).is_absolute()
            or ARCHIVE_COMMAND_OWNERSHIP_FILE.startswith("/")
        )
        or len(ARCHIVE_COMMAND_RUN_ID) != 32
        or any(value not in "0123456789abcdef" for value in ARCHIVE_COMMAND_RUN_ID)
    )
):
    raise RuntimeError("conversation_archive_command_ownership_invalid")
CONVERSATION_ARCHIVE_SHARED_SESSION_TTL_SEC = max(
    60,
    int(os.getenv("EVELYN_CONVERSATION_ARCHIVE_SHARED_SESSION_TTL_SEC", "14400")),
)
CONVERSATION_ARCHIVE_EPHEMERAL_DELETE_SEC = 180
VOICE_DEBUG_SAVE_AUDIO_REQUESTED = (
    os.getenv("VOICE_DEBUG_SAVE_AUDIO", "false").lower() == "true"
)
# A private archive must never coexist with recoverable raw debug audio.
VOICE_DEBUG_SAVE_AUDIO = bool(
    VOICE_DEBUG_SAVE_AUDIO_REQUESTED and not CONVERSATION_ARCHIVE_ENABLED
)
VOICE_DEBUG_AUDIO_DIR = os.getenv("VOICE_DEBUG_AUDIO_DIR", "debug_audio")
VOICE_DEBUG_MAX_FILES_PER_GUILD = int(os.getenv("VOICE_DEBUG_MAX_FILES_PER_GUILD", "200"))
VOICE_DEBUG_MAX_AGE_DAYS = float(os.getenv("VOICE_DEBUG_MAX_AGE_DAYS", "7"))
VOICE_DEBUG_MAX_TOTAL_MB_PER_GUILD = int(os.getenv("VOICE_DEBUG_MAX_TOTAL_MB_PER_GUILD", "256"))
VOICE_DEBUG_PRESERVE_NEWEST = int(os.getenv("VOICE_DEBUG_PRESERVE_NEWEST", "10"))
WAKE_STT_TIMEOUT_SEC = float(os.getenv("WAKE_STT_TIMEOUT_SEC", "20"))
FULL_STT_TIMEOUT_SEC = float(os.getenv("FULL_STT_TIMEOUT_SEC", "30"))
VOICE_INGRESS_QUEUE_MAX = max(1, int(os.getenv("VOICE_INGRESS_QUEUE_MAX", "16")))
VOICE_INGRESS_MAX_AGE_SEC = float(os.getenv("VOICE_INGRESS_MAX_AGE_SEC", "8.0"))
VOICE_INGRESS_DROP_OLDEST_ON_FULL = os.getenv("VOICE_INGRESS_DROP_OLDEST_ON_FULL", "true").lower() in {"1", "true", "yes", "on"}
VOICE_UTTERANCE_ASSEMBLY_ENABLED = os.getenv("VOICE_UTTERANCE_ASSEMBLY_ENABLED", "true").lower() in {"1", "true", "yes", "on"}
VOICE_UTTERANCE_COMMIT_WAIT_SEC = float(os.getenv("VOICE_UTTERANCE_COMMIT_WAIT_SEC", "0.22"))
VOICE_UTTERANCE_PAD_MS = int(os.getenv("VOICE_UTTERANCE_PAD_MS", "180"))
VOICE_UTTERANCE_MAX_AUDIO_SEC = float(os.getenv("VOICE_UTTERANCE_MAX_AUDIO_SEC", "14.0"))
STT_FULL_RESCORING_TIMEOUT_SEC = float(os.getenv("STT_FULL_RESCORING_TIMEOUT_SEC", "12"))
STT_FULL_RESCORING_MIN_AUDIO_SEC = float(os.getenv("STT_FULL_RESCORING_MIN_AUDIO_SEC", "2.0"))
STT_FULL_RESCORING_MIN_TEXT_LEN = int(os.getenv("STT_FULL_RESCORING_MIN_TEXT_LEN", "8"))
STT_COOLDOWN_AFTER_TIMEOUT_SEC = float(os.getenv("STT_COOLDOWN_AFTER_TIMEOUT_SEC", "6.0"))
VOICE_REJOIN_ON_READY_REQUESTED = os.getenv(
    "VOICE_REJOIN_ON_READY",
    "true",
).lower() in {"1", "true", "yes", "on"}
# An archive-enabled shared session is bound to a fresh, explicit join and is
# intentionally not resurrected after a gateway/process restart.
VOICE_REJOIN_ON_READY = bool(
    VOICE_REJOIN_ON_READY_REQUESTED and not CONVERSATION_ARCHIVE_ENABLED
)
VOICE_LAST_CHANNEL_STATE_FILE = os.getenv("VOICE_LAST_CHANNEL_STATE_FILE", str(RUNTIME_ARTIFACTS_ROOT / "state" / "voice_last_channel.json"))
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
CANNED_WAKE_REPLY_AUDIO = Path(os.getenv("EVELYN_CANNED_WAKE_REPLY_AUDIO", str(CACHED_AUDIO_DIR / "wake_call_default.wav")))
DEBUG_WRITE_QUEUE_MAX = int(os.getenv("DEBUG_WRITE_QUEUE_MAX", "128"))
MIN_EDIT_INTERVAL_MS = int(os.getenv("MIN_EDIT_INTERVAL_MS", "300"))
MIN_DELTA_CHARS = int(os.getenv("MIN_DELTA_CHARS", "24"))
MAX_HOLD_MS = int(os.getenv("MAX_HOLD_MS", "900"))
ROUTER_LLM_URL = globals().get("ROUTER_LLM_URL", os.getenv("ROUTER_LLM_URL", "http://127.0.0.1:9822/v1/chat/completions"))
ROUTER_MODEL_NAME = globals().get("ROUTER_MODEL_NAME", os.getenv("ROUTER_MODEL_NAME", "gemma-4-E2B-it-UD-Q6_K_XL.gguf"))
ROUTER_LLM_ENABLED = globals().get("ROUTER_LLM_ENABLED", os.getenv("ROUTER_LLM_ENABLED", "true").lower() in {"1", "true", "yes", "on"})
ROUTER_ROUTE_MAX_TOKENS = int(globals().get("ROUTER_ROUTE_MAX_TOKENS", os.getenv("ROUTER_ROUTE_MAX_TOKENS", "220")))
ROUTER_ROUTE_TIMEOUT_SEC = float(globals().get("ROUTER_ROUTE_TIMEOUT_SEC", os.getenv("ROUTER_ROUTE_TIMEOUT_SEC", "8")))
DISCORD_ENABLED = os.getenv("DISCORD_ENABLED", "true").lower() in {"1", "true", "yes", "on"}
LOCAL_ONLY_MODE = os.getenv("LOCAL_ONLY", "false").lower() in {"1", "true", "yes", "on"} or not DISCORD_ENABLED
LOCAL_TTS_OUTPUT_ENABLED = os.getenv("LOCAL_TTS_OUTPUT_ENABLED", "true" if LOCAL_ONLY_MODE else "false").lower() in {"1", "true", "yes", "on"}
LOCAL_TTS_OUTPUT_DEVICE = os.getenv("LOCAL_TTS_OUTPUT_DEVICE") or os.getenv("LOCAL_AUDIO_OUTPUT_DEVICE")
LOCAL_CONTROL_GUILD_ID = int(os.getenv("LOCAL_CONTROL_GUILD_ID", "0"))
LOCAL_CONTROL_GUILD_NAME = os.getenv("LOCAL_CONTROL_GUILD_NAME", "Evelyn Local")
VISION_PORT = int(os.getenv("VISION_PORT", "8891"))
VISION_SERVICE_URL = os.getenv("VISION_SERVICE_URL", f"http://127.0.0.1:{VISION_PORT}")
VISION_AUTO_CAPTURE_ENABLED = os.getenv("VISION_AUTO_CAPTURE_ENABLED", "true").lower() in {"1", "true", "yes", "on"}
VISION_ANALYZE_TIMEOUT_SEC = float(os.getenv("VISION_ANALYZE_TIMEOUT_SEC", "120"))
VISION_SCREENSHOT_DIR = Path(os.getenv("VISION_SCREENSHOT_DIR", str(RUNTIME_ARTIFACTS_ROOT / "vision")))
VISION_CAPTURE_ALL_SCREENS = os.getenv("VISION_CAPTURE_ALL_SCREENS", "false").lower() in {"1", "true", "yes", "on"}
VISION_WATCH_ENABLED = os.getenv("VISION_WATCH_ENABLED", "true").lower() in {"1", "true", "yes", "on"}
VISION_WATCH_INTERVAL_SEC = max(5.0, float(os.getenv("VISION_WATCH_INTERVAL_SEC", "25")))
VISION_WATCH_THUMBNAIL_SIZE = max(64, int(os.getenv("VISION_WATCH_THUMBNAIL_SIZE", "384")))
VISION_WATCH_MAX_IMAGE_DIM = max(320, int(os.getenv("VISION_WATCH_MAX_IMAGE_DIM", "1280")))
VISION_WATCH_DIFF_THRESHOLD = max(0.01, float(os.getenv("VISION_WATCH_DIFF_THRESHOLD", "0.08")))
VISION_WATCH_ANALYZE_COOLDOWN_SEC = max(10.0, float(os.getenv("VISION_WATCH_ANALYZE_COOLDOWN_SEC", "75")))
VISION_WATCH_RUN_OCR = os.getenv("VISION_WATCH_RUN_OCR", "false").lower() in {"1", "true", "yes", "on"}
VISION_WATCH_OCR_INTERVAL_SEC = max(30.0, float(os.getenv("VISION_WATCH_OCR_INTERVAL_SEC", "180")))
VISION_DELETE_REQUEST_IMAGES = os.getenv("VISION_DELETE_REQUEST_IMAGES", "true").lower() in {"1", "true", "yes", "on"}
VISION_MEMORY_WRITE_ENABLED = os.getenv("VISION_MEMORY_WRITE_ENABLED", "false").lower() in {"1", "true", "yes", "on"}
QUESTION_FEATURE_ENABLED = os.getenv("QUESTION_FEATURE_ENABLED", "true").lower() in {"1", "true", "yes", "on"}
QUESTION_MIN_TURN_GAP = max(0, int(os.getenv("QUESTION_MIN_TURN_GAP", "3")))
QUESTION_MIN_SECONDS_GAP = max(0.0, float(os.getenv("QUESTION_MIN_SECONDS_GAP", "60")))
QUESTION_MAX_PER_10_TURNS = max(0, int(os.getenv("QUESTION_MAX_PER_10_TURNS", "3")))
QUESTION_DISABLE_AFTER_FRUSTRATION_SEC = max(0.0, float(os.getenv("QUESTION_DISABLE_AFTER_FRUSTRATION_SEC", "300")))
ODYSSEY_CAPABILITY_JSON_DIR = Path(os.getenv("ODYSSEY_CAPABILITY_JSON_DIR", r"C:\Users\Admin\.openclaw\workspace\research\odyssey\MC-Comprehensive-Skill-Library\json"))
CONTEXT_PIPELINE_BENCHMARK_LOG = Path(os.getenv("CONTEXT_PIPELINE_BENCHMARK_LOG", str(RUNTIME_ARTIFACTS_ROOT / "benchmarks" / "context_pipeline_benchmarks.jsonl")))
MEMORY_WRITEBEHIND_STATUS_LOG = Path(os.getenv("MEMORY_WRITEBEHIND_STATUS_LOG", str(RUNTIME_ARTIFACTS_ROOT / "memory" / "writebehind_status.jsonl")))
CONTROL_PAGE_ENABLED = os.getenv("CONTROL_PAGE_ENABLED", "true").lower() == "true"
CONTROL_PAGE_HOST = os.getenv("CONTROL_PAGE_HOST", "127.0.0.1")
CONTROL_PAGE_PORT = int(os.getenv("CONTROL_PAGE_PORT", "8799"))
CONTROL_PAGE_CHAT_LOG_LIMIT = int(os.getenv("CONTROL_PAGE_CHAT_LOG_LIMIT", "40"))
CONTROL_PAGE_WELCOME_LLM_TIMEOUT_SEC = float(os.getenv("CONTROL_PAGE_WELCOME_LLM_TIMEOUT_SEC", "18.0"))
CONTROL_PAGE_WELCOME_FALLBACK = os.getenv("CONTROL_PAGE_WELCOME_FALLBACK", "왔어? 오늘도 이상한 건 내가 정리하고, 재밌는 건 같이 키워볼게.")
CONTROL_PAGE_DOCS_DIR = PROJECT_ROOT / "docs"
CONTROL_PAGE_ASSETS_DIR = CONTROL_PAGE_DOCS_DIR / "assets"
CONTROL_PAGE_MINECRAFT_ICON_ROUTE = "/api/control-page/minecraft-item-icon"
CONTROL_PAGE_MINECRAFT_CACHE_REFRESH_SEC = float(os.getenv("CONTROL_PAGE_MINECRAFT_CACHE_REFRESH_SEC", "1.0"))
CONTROL_PAGE_MINECRAFT_CACHE_MAX_STALE_SEC = float(os.getenv("CONTROL_PAGE_MINECRAFT_CACHE_MAX_STALE_SEC", "20.0"))
CONTROL_PAGE_MINECRAFT_SNAPSHOT_TIMEOUT_SEC = float(os.getenv("CONTROL_PAGE_MINECRAFT_SNAPSHOT_TIMEOUT_SEC", "2.5"))
CONTROL_PAGE_RUNTIME_CACHE_REFRESH_SEC = float(os.getenv("CONTROL_PAGE_RUNTIME_CACHE_REFRESH_SEC", "2.0"))
CONTROL_PAGE_BOT_API_HOST = os.getenv("CONTROL_PAGE_BOT_API_HOST", "127.0.0.1")
CONTROL_PAGE_BOT_API_PORT = int(os.getenv("CONTROL_PAGE_BOT_API_PORT", "8798"))
CONTROL_PAGE_BOT_API_STATE_PATH = os.getenv("CONTROL_PAGE_BOT_API_STATE_PATH", "/api/control-page/state")
CONTROL_PAGE_BOT_API_PROBE_TIMEOUT_SEC = float(os.getenv("CONTROL_PAGE_BOT_API_PROBE_TIMEOUT_SEC", "0.75"))
CONTROL_PAGE_RUNTIME_CACHE_WARN_STALE_SEC = float(os.getenv("CONTROL_PAGE_RUNTIME_CACHE_WARN_STALE_SEC", "6.0"))
CONTROL_PAGE_RUNTIME_CACHE_MAX_STALE_SEC = float(os.getenv("CONTROL_PAGE_RUNTIME_CACHE_MAX_STALE_SEC", "25.0"))
CONTROL_PAGE_RUNTIME_CACHE_REFRESH_MIN_INTERVAL_SEC = float(os.getenv("CONTROL_PAGE_RUNTIME_CACHE_REFRESH_MIN_INTERVAL_SEC", "2.5"))
RUNTIME_STATUS_CONTEXT_ENABLED = os.getenv("RUNTIME_STATUS_CONTEXT_ENABLED", "true").lower() in {"1", "true", "yes", "on"}
RUNTIME_STATUS_CONTEXT_REFRESH_SEC = float(os.getenv("RUNTIME_STATUS_CONTEXT_REFRESH_SEC", "4.0"))

ALLOWED_CONSOLE_PREFIXES = (
    "🎤 [", "💬 [Evelyn]", "[STT RESULT][wake]", "[STT RESULT][partial]",
    "[STT RESULT][full-final]", "[MC OBS]", "[MC GOAL]", "[MC PLAN]",
    "[MC STEP]", "[MC RESULT]", "[MC DIG]", "[MC ERROR]", "[MC STDERR]",
)
BOTTLENECK_TURN_TRACE_EVENTS = {
    "tts_interrupt", "tts_first_pcm_received", "playback_queue_put", "playback_queue_get",
    "discord_playback_play_invoked", "discord_playback_finished", "discord_playback_exception",
    "first_packet_sent", "turn_ingress", "turn_drop", "policy_ready", "room_owner_update",
    "room_reply_state", "model_call", "question_trace", "text_turn_summary",
    "voice_turn_summary", "voice_drop_summary", "barge_in_continuity",
}

__all__ = tuple(name for name in globals() if name.isupper())
